"""지표 계산에 쓰는 파생 테이블을 DuckDB에 만든다.

    python data/derived.py        (build_db.py 끝에서도 자동 실행)

pop_emd  : 행정동별 주민등록 총인구·65세 이상 인구
           원자료 = 행정안전부「지역별(법정동) 성별 연령별 주민등록 인구수」(data.go.kr 15099158)
           법정동 → 행정동은 국가데이터처「법정동 연계정보」로 묶는다.
           한 법정동이 여러 행정동에 걸치면 법정동 인구로는 나눌 수 없다. 이 행정동들은
           행정안전부 통·반 단위 인구(data/ingest/portal_api.py, data.go.kr 15108071)를 행정동코드로 더해 채운다
           (status=tongban, 총인구만 있고 65세 이상은 NULL). 통·반 파일이 없거나 기준월이 다르면 NULL(status=ambiguous).
pop_tongban : 위 통·반 원자료 (출처 표기용)
bus_usage_monthly : 정류소 이용 실적(이름만 있음)을 정류소 이름으로 행정동에 연결한 월별 집계.
           이름은 공백·쉼표를 지우고 맞추고, 안 맞으면 괄호 안 설명까지 지우고 다시 맞춘다.
           같은 이름의 정류소가 여러 행정동에 있으면 어느 동의 승차인지 알 수 없어 연결하지 않는다.
emd_geo  : 행정동 경계 면적(km²)과 중심점 (raw/boundary 의 admdongkor 경계)
"""
from __future__ import annotations

import csv
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))

from data.spatial.boundary import emd_geometry  # noqa: E402

DB = ENGINE_ROOT / "data" / "jeju.duckdb"
LINK_DIR = ENGINE_ROOT / "data" / "ingest" / "raw" / "emd_link"
EMD_CSV = ENGINE_ROOT / "data" / "spatial" / "emd_codes.csv"
POP_URL = "https://www.data.go.kr/data/15099158/fileData.do"
POP_DATASET = "행정안전부_지역별(법정동) 성별 연령별 주민등록 인구수"


def _find(keyword: str) -> Path | None:
    hits = sorted(p for p in LINK_DIR.glob("*.csv") if keyword in unicodedata.normalize("NFC", p.name))
    return hits[-1] if hits else None


def _read_csv(path: Path):
    for enc in ("utf-8-sig", "cp949"):
        try:
            with path.open(encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError(f"인코딩을 알 수 없음: {path.name}")


TONGBAN_DATASET = "행정안전부_법정동별(행정동 통반단위) 주민등록 인구 및 세대현황"
TONGBAN_URL = "https://www.data.go.kr/data/15108071/openapi.do"


def load_tongban(month: str) -> dict | None:
    """raw/emd_link/행정동_통반인구_<YYYYMM>.json (portal_api.py 결과). 기준월이 같은 파일만 쓴다."""
    path = LINK_DIR / f"행정동_통반인구_{month[:7].replace('-', '')}.json"   # month: YYYY-MM 또는 YYYY-MM-DD
    if not path.exists():
        return None
    import json
    return json.loads(path.read_text(encoding="utf-8"))


def population_by_emd(pop_csv: Path, link_csv: Path, emd_csv: Path = EMD_CSV,
                      tongban: dict[str, int] | None = None) -> tuple[list[tuple], str]:
    """[(emd_code, total, pop65, status)], 기준연월

    tongban: {행정동 8자리 코드: 통·반 인구 합} — 법정동이 여러 행정동에 걸쳐 비는 행정동의 총인구를 채운다.
    """
    with emd_csv.open(encoding="utf-8", newline="") as f:
        emd_rows = list(csv.DictReader(f))
    valid = [r["emd_code"] for r in emd_rows]
    # 연계표에 없는 법정리(예: 성산읍 신양리 5013025901)는 읍·면 이름으로 찾는다. 리는 그 읍·면 행정동에 속한다.
    eupmyeon = {(r["sigungu_name"], r["emd_name"]): r["emd_code"] for r in emd_rows
                if r["emd_name"].endswith(("읍", "면"))}
    legal_to_adm: dict[str, set[str]] = defaultdict(set)
    for r in _read_csv(link_csv):
        if r["시도명"].startswith("제주") and r["행정동코드"][:8] in valid:
            legal_to_adm[r["법정동코드"]].add(r["행정동코드"][:8])

    total, old = Counter(), Counter()
    ambiguous: set[str] = set()
    months = set()
    rows = _read_csv(pop_csv)
    age_cols = [c for c in rows[0] if c.endswith(("남자", "여자")) and c[:-2].rstrip("세 이상").isdigit()]
    for r in rows:
        if not r["시도명"].startswith("제주"):
            continue
        adm = legal_to_adm.get(r["법정동코드"])
        if not adm and r.get("리명"):
            code = eupmyeon.get((r.get("시군구명", ""), r.get("읍면동명", "")))
            adm = {code} if code else None
        if not adm:
            continue
        months.add(r["기준연월"])
        if len(adm) > 1:
            ambiguous |= adm
            continue
        code = next(iter(adm))
        total[code] += int(r["계"])
        old[code] += sum(int(r[c] or 0) for c in age_cols if int(c[:-2].rstrip("세 이상")) >= 65)

    out = []
    for code in valid:
        if code in ambiguous and tongban and tongban.get(code):
            out.append((code, tongban[code], None, "tongban"))
        elif code in ambiguous:
            out.append((code, None, None, "ambiguous"))
        elif total[code]:
            out.append((code, total[code], old[code], "ok"))
        else:
            out.append((code, None, None, "missing"))
    month = max(months) if months else ""
    return out, (f"{month[:4]}-{month[4:6]}" if len(month) == 6 else month)


def build_bus_usage(con) -> str:
    """bus_station_emd(이용 실적 정류소 이름 → 행정동), bus_usage_monthly 를 만들고 연결 비율 문장을 돌려준다."""
    # 이용 실적은 정류소 '이름'만 있다. 같은 이름의 정류소가 모두 한 행정동에 있을 때만 연결한다.
    # 이용 실적과 정류소 정보는 띄어쓰기·쉼표가 달라(예: '제주도청신제주로터리' / '제주도청 신제주로터리')
    # 1) 공백·쉼표를 지운 이름, 2) 그래도 없으면 괄호 안 설명까지 지운 이름으로 맞춘다.
    #    1)에서 여러 행정동이 나오면 2)로 넘어가지 않고 연결하지 않는다.
    con.execute(r"""
        CREATE OR REPLACE TABLE bus_station_emd AS
        WITH st AS (SELECT regexp_replace(trim(stationName), '[\s,]', '', 'g') AS k1, emd_code
                    FROM bus_stop WHERE emd_code IS NOT NULL),
             st1 AS (SELECT k1, any_value(emd_code) AS emd, count(DISTINCT emd_code) AS n FROM st GROUP BY 1),
             st2 AS (SELECT regexp_replace(k1, '\([^)]*\)', '', 'g') AS k2, any_value(emd_code) AS emd,
                            count(DISTINCT emd_code) AS n FROM st GROUP BY 1),
             names AS (SELECT DISTINCT trim(station_name) AS station_name,
                              regexp_replace(trim(station_name), '[\s,]', '', 'g') AS k1
                       FROM bus_stop_usage_by_user_type)
        SELECT * FROM (
            SELECT names.station_name,
                   CASE WHEN st1.k1 IS NOT NULL THEN CASE WHEN st1.n = 1 THEN st1.emd END
                        WHEN st2.n = 1 THEN st2.emd END AS emd_code,
                   CASE WHEN st1.k1 IS NOT NULL THEN 'name' ELSE 'name_without_parentheses' END AS matched_by
            FROM names
            LEFT JOIN st1 ON st1.k1 = names.k1
            LEFT JOIN st2 ON st2.k2 = regexp_replace(names.k1, '\([^)]*\)', '', 'g')
        ) WHERE emd_code IS NOT NULL
    """)
    con.execute("""
        CREATE OR REPLACE TABLE bus_usage_monthly AS
        SELECT s.emd_code, substr(u.period, 1, 7) AS month, u.user_type, sum(u.user_count) AS users
        FROM bus_stop_usage_by_user_type u JOIN bus_station_emd s ON trim(u.station_name) = s.station_name
        GROUP BY ALL
    """)
    matched, total = con.execute("""
        SELECT sum(CASE WHEN s.station_name IS NOT NULL THEN user_count END), sum(user_count)
        FROM bus_stop_usage_by_user_type u LEFT JOIN bus_station_emd s ON trim(u.station_name) = s.station_name
    """).fetchone()
    return f"이용 인원의 {matched / total:.1%}를 행정동에 연결"


def build(con) -> dict:
    stats = {}
    pop_csv, link_csv = _find("인구수"), _find("법정동 연계정보")
    if pop_csv and link_csv:
        _, month = population_by_emd(pop_csv, link_csv)
        tb = load_tongban(month)
        tb_sum = Counter()
        for r in (tb or {}).get("rows", []):
            if r.get("admmCd") and r.get("totNmprCnt"):
                tb_sum[r["admmCd"][:8]] += int(r["totNmprCnt"])
        rows, month = population_by_emd(pop_csv, link_csv, tongban=tb_sum)
        con.execute("CREATE OR REPLACE TABLE pop_emd (emd_code VARCHAR, total_pop INTEGER, pop_65plus INTEGER, "
                    "status VARCHAR, period VARCHAR)")
        con.executemany("INSERT INTO pop_emd VALUES (?, ?, ?, ?, ?)", [(*r, month) for r in rows])
        ok = sum(r[3] in ("ok", "tongban") for r in rows)
        filled = sum(r[3] == "tongban" for r in rows)
        con.execute("DELETE FROM dataset WHERE table_name = 'pop_tongban'")
        con.execute("DROP TABLE IF EXISTS pop_tongban")
        if tb and filled:
            con.execute("CREATE TABLE pop_tongban (emd_code VARCHAR, stdg_code VARCHAR, tong VARCHAR, ban VARCHAR, "
                        "total_pop INTEGER, period VARCHAR)")
            con.executemany("INSERT INTO pop_tongban VALUES (?, ?, ?, ?, ?, ?)",
                            [(r["admmCd"][:8], r["stdgCd"], r["tong"], r["ban"], int(r["totNmprCnt"]), tb["month"])
                             for r in tb["rows"] if r.get("admmCd") and r.get("totNmprCnt")])
            n = con.execute("SELECT count(*) FROM pop_tongban").fetchone()[0]
            con.execute(
                "INSERT INTO dataset VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'aggregate', ?, 1.0, NULL, 'external_api', NULL)",
                ["pop_tongban", TONGBAN_DATASET, "공공데이터포털(행정안전부)", TONGBAN_URL,
                 f"행정동_통반인구_{tb['month'].replace('-', '')}.json", tb["month"], tb["month"], tb["fetched_at"],
                 n, tb["month"], tb["month"], n],
            )
        con.execute("DELETE FROM dataset WHERE table_name = 'pop_emd'")
        fetched = datetime.fromtimestamp(pop_csv.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        con.execute(
            "INSERT INTO dataset VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'aggregate', ?, ?, NULL, 'external_file', NULL)",
            ["pop_emd", POP_DATASET, "공공데이터포털(행정안전부)", POP_URL, unicodedata.normalize("NFC", pop_csv.name),
             month, month, fetched, len(rows), month, month, ok, ok / len(rows)],
        )
        stats["pop_emd"] = (f"{ok}/{len(rows)} 행정동 인구 확정 ({month}"
                            + (f", 그중 {filled}곳은 통·반 인구로 채움)" if filled else ")"))
    else:
        stats["pop_emd"] = "건너뜀: raw/emd_link 에 인구수 파일 또는 연계정보 파일 없음"

    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if {"bus_stop", "bus_stop_usage_by_user_type"} <= tables:
        stats["bus_usage_monthly"] = build_bus_usage(con)

    geo = emd_geometry()
    if geo:
        con.execute("CREATE OR REPLACE TABLE emd_geo (emd_code VARCHAR, area_km2 DOUBLE, lat DOUBLE, lon DOUBLE)")
        con.executemany("INSERT INTO emd_geo VALUES (?, ?, ?, ?)", [(c, *v) for c, v in sorted(geo.items())])
        stats["emd_geo"] = f"{len(geo)} 행정동"
    else:
        stats["emd_geo"] = "건너뜀: raw/boundary 에 경계 파일 없음"
    return stats


def main() -> None:
    con = duckdb.connect(str(DB))
    for k, v in build(con).items():
        print(f"{k}: {v}")
    con.close()


if __name__ == "__main__":
    main()
