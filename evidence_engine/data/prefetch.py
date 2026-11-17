"""미리 받은 데이터허브 표(10MB 이하 csv·xlsx)를 DuckDB에 적재하고, 읍면동·기간 키를 자동으로 찾아 기록한다.

    python data/ingest/hub_files.py --small 10     # 먼저 받기 (raw/jejudatahub/files/<id>/, manifest 에 prefetch: true)
    python data/prefetch.py                        # 적재 + 진단표(prefetch_profile)

왜 필요한가
  등록 지표(registry.yaml)는 민원 주제 몇 가지에 맞춰 고른 데이터 48종만 쓴다. 그 밖의 주장(예: 공영주차장이 부족하다)은
  '해당 지표 없음'이 된다. 데이터허브의 작은 표를 미리 받아 두면, 범용 틀(시설 목록 → 인구 대비 개수)로 근거를 낼 수 있는지
  실행 중 내려받기 없이(센터 PC 인터넷과 상관없이, 같은 숫자로 재현되게) 확인할 수 있다.

만드는 것 (data/jeju.duckdb)
  pf_<허브id>_<n>   원본 컬럼(문자열 그대로) + emd_code, emd_method, period
  prefetch_profile  표마다 행 수, 읍면동을 찾은 컬럼·방식·비율, 기간 컬럼, 한 행이 시설 하나인지(place_list)
  출처(데이터명·허브 주소·받은 시각)는 prefetch_profile 에 둔다. dataset 표에는 넣지 않는다:
  엔진의 카탈로그 검색(retrieval/datasets.py)이 dataset 을 읽어 '적재됨'으로 다루므로, 범용 틀을 연결하기 전까지
  기존 판정·빈칸 문구가 바뀌지 않게 따로 둔다.

자동으로 찾는 규칙 (컬럼 이름 기준. 값으로 확인한 비율을 함께 남긴다)
  읍면동 이름  : 이름에 읍면동·행정동·법정동·동명 이 든 컬럼 → match_emd
  주소         : 주소·소재지·도로명·지번 이 든 컬럼 → match_emd (주소 속 읍면동·리 이름)
  좌표         : 위도/경도(lat/lon) 짝 → 행정동 경계로 찾기
  여러 후보 중 가장 많은 행을 찾은 것 하나를 쓴다. 50% 미만이면 쓰지 않는다(emd_code 비움).
  기간         : 이름에 연도·년도·연월·년월·일자·날짜·기준일 이 든 첫 컬럼 → 'YYYY' 또는 'YYYY-MM'
  place_list   : 주소·좌표로 찾았고(이름 컬럼이 아님) 기간 컬럼이 없거나 값이 하나뿐인 표 = 한 행이 시설·지점 하나인 목록.
                 단 한 데이터셋이 파일 여러 개로 나뉘어 있으면 빼다(월별 렌터카 체류 위치처럼 기간별로 나눈 이동 기록이었음)
원본 값을 바꾸지 않는다. 숫자 판정은 이 표로 하지 않고, 범용 틀이 판정 상한(partial)을 두고 쓴다.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import tempfile
from pathlib import Path

import duckdb

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))

from data.build_db import DB, EMD_CSV, FILES, as_utf8, q  # noqa: E402
from data.spatial import boundary  # noqa: E402
from data.spatial.emd_match import match_emd  # noqa: E402

PREFIX = "pf_"
NAME_COLS = ("읍면동", "행정동", "법정동", "동명", "읍면")
ADDR_COLS = ("주소", "소재지", "도로명", "지번")
LAT_COLS = ("위도", "lat", "latitude", "y좌표")
LON_COLS = ("경도", "lon", "lng", "longitude", "x좌표")
PERIOD_COLS = ("연도", "년도", "연월", "년월", "일자", "날짜", "기준일", "기준 연", "기준연")
MIN_RATIO = 0.5                 # 이보다 적게 찾으면 읍면동 키를 쓰지 않는다
MAX_POINTS = 50_000             # 좌표 찾기는 표 하나에 이 개수까지 (경계 계산이 파이썬이라 느림)


def table_name(hub_id: int, n: int) -> str:
    return f"{PREFIX}{hub_id}_{n}"


def _xlsx_to_csv(path: Path, workdir: Path) -> Path:
    """첫 시트를 CSV로. 맨 위 빈 줄은 건너뛰고, 처음 나오는 값 있는 줄을 머리글로 본다."""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    out = workdir / (path.stem + ".xlsx.csv")
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        started = False
        for row in ws.iter_rows(values_only=True):
            if not started and not any(v not in (None, "") for v in row):
                continue
            started = True
            w.writerow(["" if v is None else v for v in row])
    wb.close()
    return out


def _read(con, path: Path, workdir: Path) -> None:
    src = _xlsx_to_csv(path, workdir) if path.suffix.lower() == ".xlsx" else as_utf8(path, workdir)
    sql = (f"CREATE OR REPLACE TEMP TABLE _raw AS SELECT * FROM read_csv('{src.as_posix().replace(chr(39), chr(39) * 2)}', "
           "header=true, all_varchar=true, null_padding=true, ignore_errors=true, sample_size=-1)")
    con.execute(sql)


def _cols(columns: list[str], keys: tuple[str, ...]) -> list[str]:
    return [c for c in columns if any(k in c.lower() for k in keys)]


def _map_text(con, cols: list[str]) -> tuple[dict[str, tuple[str, str]], str]:
    expr = " || ' ' || ".join(f"coalesce({q(c)}, '')" for c in cols)
    found = {}
    for (t,) in con.execute(f"SELECT DISTINCT {expr} FROM _raw").fetchall():
        code, method = match_emd(t, EMD_CSV)
        if code:
            found[t] = (code, method)
    return found, expr


def detect_emd(con, columns: list[str], rows: int) -> dict | None:
    """가장 많은 행을 찾은 읍면동 키 하나. {mode, columns, expr, mapping, ratio}"""
    if not rows:
        return None
    best = None
    for mode, cols in (("name", _cols(columns, NAME_COLS)), ("address", _cols(columns, ADDR_COLS))):
        for c in cols:                                   # 컬럼마다 따로 본다(주소 두 개면 더 잘 찾는 쪽)
            mapping, expr = _map_text(con, [c])
            if not mapping:
                continue
            con.execute("CREATE OR REPLACE TEMP TABLE _m (_pf_t VARCHAR, _pf_code VARCHAR, _pf_method VARCHAR)")
            con.executemany("INSERT INTO _m VALUES (?, ?, ?)", [(t, *v) for t, v in mapping.items()])
            hit = con.execute(f"SELECT count(*) FROM _raw r JOIN _m ON _m._pf_t = {expr}").fetchone()[0]
            if not best or hit > best["hit"]:
                best = {"mode": mode, "columns": [c], "expr": expr, "mapping": mapping, "hit": hit}
    lat, lon = _cols(columns, LAT_COLS), _cols(columns, LON_COLS)
    if lat and lon and boundary.default_boundaries():
        pts = con.execute(f"SELECT DISTINCT {q(lat[0])}, {q(lon[0])} FROM _raw LIMIT {MAX_POINTS + 1}").fetchall()
        if len(pts) <= MAX_POINTS:
            mapping = {}
            for a, b in pts:
                try:
                    code = boundary.point_to_emd(a, b)
                except (TypeError, ValueError):
                    code = None
                if code:
                    mapping[f"{a}|{b}"] = (code, "point")
            expr = f"coalesce({q(lat[0])}, '') || '|' || coalesce({q(lon[0])}, '')"
            if mapping:
                con.execute("CREATE OR REPLACE TEMP TABLE _m (_pf_t VARCHAR, _pf_code VARCHAR, _pf_method VARCHAR)")
                con.executemany("INSERT INTO _m VALUES (?, ?, ?)", [(t, *v) for t, v in mapping.items()])
                hit = con.execute(f"SELECT count(*) FROM _raw r JOIN _m ON _m._pf_t = {expr}").fetchone()[0]
                if not best or hit > best["hit"]:
                    best = {"mode": "point", "columns": [lat[0], lon[0]], "expr": expr, "mapping": mapping, "hit": hit}
    if not best:
        return None
    best["ratio"] = best["hit"] / rows
    return best


_PERIOD_SQL = ("CASE WHEN regexp_matches({c}, '(19|20)\\d\\d[-./년 ]*(0[1-9]|1[0-2])(\\D|$)') "
               "THEN regexp_extract({c}, '((19|20)\\d\\d)', 1) || '-' || "
               "regexp_extract({c}, '(19|20)\\d\\d[-./년 ]*(0[1-9]|1[0-2])', 2) "
               "WHEN regexp_matches({c}, '(19|20)\\d\\d') THEN regexp_extract({c}, '((19|20)\\d\\d)', 1) END")


def detect_period(columns: list[str]) -> str | None:
    cols = _cols(columns, PERIOD_COLS)
    return cols[0] if cols else None


def load_one(con, hub_id: int, n: int, path: Path, workdir: Path) -> dict:
    table = table_name(hub_id, n)
    _read(con, path, workdir)
    columns = [r[0] for r in con.execute("DESCRIBE _raw").fetchall()]
    rows = con.execute("SELECT count(*) FROM _raw").fetchone()[0]
    emd = detect_emd(con, columns, rows)
    if emd and emd["ratio"] < MIN_RATIO:
        emd = None
    pcol = detect_period(columns)
    period = _PERIOD_SQL.format(c=f"r.{q(pcol)}") if pcol else "NULL::VARCHAR"
    if emd:
        con.execute("CREATE OR REPLACE TEMP TABLE _m (_pf_t VARCHAR, _pf_code VARCHAR, _pf_method VARCHAR)")
        con.executemany("INSERT INTO _m VALUES (?, ?, ?)", [(t, *v) for t, v in emd["mapping"].items()])
        con.execute(f"CREATE OR REPLACE TABLE {q(table)} AS SELECT r.*, m._pf_code AS emd_code, m._pf_method AS emd_method, "
                    f"{period} AS period FROM _raw r LEFT JOIN _m m ON m._pf_t = {emd['expr']}")
    else:
        con.execute(f"CREATE OR REPLACE TABLE {q(table)} AS SELECT r.*, NULL::VARCHAR AS emd_code, "
                    f"NULL::VARCHAR AS emd_method, {period} AS period FROM _raw r")
    n_periods = con.execute(f"SELECT count(DISTINCT period) FROM {q(table)}").fetchone()[0] if pcol else 0
    p_min, p_max = con.execute(f"SELECT min(period), max(period) FROM {q(table)}").fetchone()
    return {
        "table_name": table, "hub_id": hub_id, "source_file": path.name, "rows": rows, "columns": len(columns),
        "emd_mode": emd["mode"] if emd else None, "emd_columns": json.dumps(emd["columns"], ensure_ascii=False) if emd else None,
        "emd_ratio": round(emd["ratio"], 4) if emd else None,
        "period_column": pcol, "period_min": p_min, "period_max": p_max, "n_periods": n_periods,
        "place_list": bool(emd and emd["mode"] in ("address", "point") and n_periods <= 1),
        "error": None,
    }


PROFILE_COLS = ("table_name", "hub_id", "source_file", "title", "category", "url", "fetched_at", "updated_at", "rows", "columns", "emd_mode", "emd_columns",
                "emd_ratio", "period_column", "period_min", "period_max", "n_periods", "place_list", "error")


def main() -> None:
    manifest = json.loads((FILES / "manifest.json").read_text(encoding="utf-8"))
    targets = {int(k): v for k, v in manifest.items() if v.get("prefetch")}
    con = duckdb.connect(str(DB))
    for (t,) in con.execute(f"SELECT table_name FROM information_schema.tables WHERE table_name LIKE '{PREFIX}%'").fetchall():
        con.execute(f"DROP TABLE {q(t)}")
    profile = []
    with tempfile.TemporaryDirectory() as tmp:
        for hub_id, meta in sorted(targets.items()):
            for n, f in enumerate(sorted(meta.get("files", []), key=lambda x: x["name"]), 1):
                path = FILES / str(hub_id) / f["name"]
                row = {"title": meta["dataset"], "category": meta.get("category"), "url": meta["url"],
                       "fetched_at": meta.get("fetched_at"), "updated_at": meta.get("updated_at")}
                try:
                    stats = load_one(con, hub_id, n, path, Path(tmp))
                except Exception as exc:                 # 표 하나가 깨져도 나머지는 적재한다(사유를 진단표에 남김)
                    stats = {k: None for k in PROFILE_COLS} | {
                        "table_name": table_name(hub_id, n), "hub_id": hub_id, "source_file": f["name"],
                        "place_list": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
                profile.append({**stats, **row})
    per_hub: dict[int, int] = {}
    for prof in profile:
        per_hub[prof["hub_id"]] = per_hub.get(prof["hub_id"], 0) + 1
    for prof in profile:
        if per_hub[prof["hub_id"]] > 1:
            prof["place_list"] = False
    con.execute("CREATE OR REPLACE TABLE prefetch_profile (" + ", ".join(
        f"{c} {'BIGINT' if c in ('hub_id', 'rows', 'columns', 'n_periods') else 'DOUBLE' if c == 'emd_ratio' else 'BOOLEAN' if c == 'place_list' else 'VARCHAR'}"
        for c in PROFILE_COLS) + ")")
    con.executemany(f"INSERT INTO prefetch_profile VALUES ({', '.join('?' * len(PROFILE_COLS))})",
                    [[p.get(c) for c in PROFILE_COLS] for p in profile])
    ok, emd, places, err = con.execute(
        "SELECT count(*) FILTER (WHERE error IS NULL), count(emd_mode), count(*) FILTER (WHERE place_list), "
        "count(*) FILTER (WHERE error IS NOT NULL) FROM prefetch_profile").fetchone()
    con.close()
    print(f"미리 받은 표 {len(profile)}개: 적재 {ok}, 읍면동 키 {emd}, 시설 목록(place_list) {places}, 실패 {err}")


if __name__ == "__main__":
    main()
