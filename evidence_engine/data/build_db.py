"""raw 파일 → DuckDB 적재, 공통 키(emd_code, period) 부여.

    python data/build_db.py

만드는 것 (data/jeju.duckdb)
- dim_emd            : 행정동 43개 (emd_code, sigungu_name, emd_name)
- <table>            : datasets.yaml의 테이블. 원본 컬럼 + period, emd_code, emd_method
- dataset            : 테이블별 출처·기준일·행 수·키 부여 방식·매핑률

상태: 데이터허브 FILE만 적재한다. 좌표 → emd_code 공간조인은 행정동 경계 확보 후 추가한다
(지금은 좌표 컬럼 이름만 dataset.latlon_columns 에 기록).
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import yaml

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))

from data.spatial import boundary  # noqa: E402
from data.spatial.emd_match import match_emd  # noqa: E402

INGEST = ENGINE_ROOT / "data" / "ingest"
FILES = INGEST / "raw" / "jejudatahub" / "files"
DB = ENGINE_ROOT / "data" / "jeju.duckdb"
EMD_CSV = ENGINE_ROOT / "data" / "spatial" / "emd_codes.csv"
PORTAL = INGEST / "raw" / "datagokr"


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def as_utf8(path: Path, workdir: Path) -> Path:
    """UTF-8(BOM 포함)이면 그대로, 아니면 CP949로 읽어 UTF-8 사본을 만든다."""
    with path.open("rb") as f:
        head = f.read(1 << 20)
    for cut in range(4):
        try:
            head[: len(head) - cut].decode("utf-8")
            return path
        except UnicodeDecodeError:
            continue
    out = workdir / (path.stem + ".utf8.csv")
    out.write_text(path.read_text(encoding="cp949"), encoding="utf-8")
    return out


def period_sql(spec: dict | None) -> str:
    if not spec:
        return "NULL::VARCHAR"
    fmt = spec["format"]
    if fmt == "year_month":
        year, month = (f"CAST({q(c)} AS VARCHAR)" for c in spec["columns"])
        return f"{year} || '-' || lpad({month}, 2, '0')"
    col = f"CAST({q(spec['column'])} AS VARCHAR)"
    return {
        "yyyy": f"substr({col}, 1, 4)",
        "yyyymm": f"substr({col}, 1, 4) || '-' || substr({col}, 5, 2)",
        "yyyymmdd": f"substr({col}, 1, 4) || '-' || substr({col}, 5, 2) || '-' || substr({col}, 7, 2)",
        "yyyy-mm-dd": f"substr({col}, 1, 10)",
    }[fmt]


def ragged_sql(csv_path: Path, ragged: dict) -> str:
    """따옴표 없이 쉼표가 들어간 컬럼이 하나 있는 CSV를 읽는 SQL.

    예: 20190101,제주국제공항(대정,화순,일주서로),일반,92 → 가운데 남는 조각을 그 컬럼으로 합친다.
    """
    with csv_path.open(encoding="utf-8-sig") as f:
        header = f.readline().rstrip("\r\n").split(",")
    k, n = header.index(ragged["column"]), len(header)
    selects = []
    for i, name in enumerate(header):
        if i < k:
            expr = f"p[{i + 1}]"
        elif i == k:
            expr = f"array_to_string(p[{k + 1}:len(p) - {n - 1 - k}], ',')"
        else:
            expr = f"p[len(p) - {n - 1 - i}]"
        if name in ragged.get("int_columns", []):
            expr = f"CAST({expr} AS BIGINT)"
        selects.append(f"{expr} AS {q(name)}")
    return (
        f"SELECT {', '.join(selects)} FROM (SELECT string_split(line, ',') AS p FROM "
        f"read_csv('{csv_path.as_posix()}', header=false, skip=1, delim='\x01', quote='', escape='', "
        "columns={'line': 'VARCHAR'}))"
    )


def load_table(con, spec: dict, source: Path, workdir: Path) -> dict:
    table = spec["table"]
    csv_path = as_utf8(source, workdir)
    if spec.get("ragged"):
        con.execute(f"CREATE OR REPLACE TEMP TABLE _raw AS {ragged_sql(csv_path, spec['ragged'])}")
    else:
        base = f"CREATE OR REPLACE TEMP TABLE _raw AS SELECT * FROM read_csv('{csv_path.as_posix()}', header=true, delim=','"
        try:
            con.execute(base + ", sample_size=-1)")
        except duckdb.InvalidInputException:
            # 형식 자동 인식 실패(빈 칸이 많은 넓은 표 등): 형식을 고정하고 전부 문자열로 읽는다.
            con.execute(base + ", quote='\"', escape='\"', all_varchar=true, null_padding=true)")
    columns = [r[0] for r in con.execute("DESCRIBE _raw").fetchall()]
    needed = (spec.get("period") or {}).get("columns", []) + [(spec.get("period") or {}).get("column")]
    needed += (spec.get("emd") or {}).get("columns", []) + spec.get("latlon", [])
    missing = [c for c in needed if c and c not in columns]
    if missing:
        raise KeyError(f"{table}: 파일에 없는 컬럼 {missing} (실제: {columns})")

    emd = spec.get("emd")
    if emd:
        text = " || ' ' || ".join(f"coalesce(CAST({q(c)} AS VARCHAR), '')" for c in emd["columns"])
        distinct = [r[0] for r in con.execute(f"SELECT DISTINCT {text} FROM _raw").fetchall()]
        mapping = [(t, *match_emd(t, EMD_CSV)) for t in distinct]
        con.execute("CREATE OR REPLACE TEMP TABLE _emd (emd_text VARCHAR, emd_code VARCHAR, emd_method VARCHAR)")
        con.executemany("INSERT INTO _emd VALUES (?, ?, ?)", mapping)
        con.execute(
            f"CREATE OR REPLACE TABLE {q(table)} AS SELECT r.*, {period_sql(spec.get('period'))} AS period, "
            f"m.emd_code, m.emd_method FROM _raw r LEFT JOIN _emd m ON m.emd_text = {text}"
        )
    else:
        con.execute(
            f"CREATE OR REPLACE TABLE {q(table)} AS SELECT *, {period_sql(spec.get('period'))} AS period, "
            "NULL::VARCHAR AS emd_code, NULL::VARCHAR AS emd_method FROM _raw"
        )

    latlon = spec.get("latlon")
    point_mapped = 0
    if latlon and boundary.default_boundaries():
        # 이름·주소로 못 찾은 행만 좌표로 찾는다(좌표만 있는 표는 전부).
        lat, lon = (f"CAST({q(c)} AS VARCHAR)" for c in latlon)
        points = con.execute(f"SELECT DISTINCT {lat}, {lon} FROM {q(table)} WHERE emd_code IS NULL").fetchall()
        found = [(a, b, code) for a, b in points if (code := boundary.point_to_emd(a, b))]
        if found:
            con.execute("CREATE OR REPLACE TEMP TABLE _pt (lat VARCHAR, lon VARCHAR, code VARCHAR)")
            con.executemany("INSERT INTO _pt VALUES (?, ?, ?)", found)
            point_mapped = con.execute(
                f"UPDATE {q(table)} t SET emd_code = p.code, emd_method = 'point' FROM _pt p "
                f"WHERE t.emd_code IS NULL AND {lat.replace(q(latlon[0]), 't.' + q(latlon[0]))} = p.lat "
                f"AND {lon.replace(q(latlon[1]), 't.' + q(latlon[1]))} = p.lon"
            ).fetchone()[0]

    rows, mapped, p_min, p_max = con.execute(
        f"SELECT count(*), count(emd_code), min(period), max(period) FROM {q(table)}"
    ).fetchone()
    modes = [m for m in ((emd or {}).get("mode"), "point" if latlon and boundary.default_boundaries() else None) if m]
    has_emd = bool(modes)
    return {
        "rows": rows,
        "emd_mode": "+".join(modes) if has_emd else None,
        "emd_mapped": mapped if has_emd else None,
        "emd_ratio": round(mapped / rows, 4) if has_emd and rows else None,
        "emd_point_mapped": point_mapped,
        "period_min": p_min,
        "period_max": p_max,
        "latlon_columns": json.dumps(spec["latlon"], ensure_ascii=False) if spec.get("latlon") else None,
    }


def register(con, spec: dict, meta: dict, kind: str, stats: dict) -> None:
    con.execute(
        "INSERT INTO dataset VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [spec["table"], meta["dataset"], meta["publisher"], meta["hub_id"], spec.get("complaint"), meta["url"],
         meta["source_file"], meta["period_end"], meta["updated_at"], meta["fetched_at"], stats["rows"],
         stats["period_min"], stats["period_max"], stats["emd_mode"], stats["emd_mapped"], stats["emd_ratio"],
         stats["latlon_columns"], kind, meta.get("origin_url")],
    )


def main() -> None:
    cfg = yaml.safe_load((INGEST / "datasets.yaml").read_text(encoding="utf-8"))
    manifest = json.loads((FILES / "manifest.json").read_text(encoding="utf-8"))
    catalog = {r["id"]: r for r in json.loads(sorted((INGEST / "raw" / "jejudatahub").glob("catalog_*.json"))[-1].read_text(encoding="utf-8"))}
    con = duckdb.connect(str(DB))
    con.execute(f"CREATE OR REPLACE TABLE dim_emd AS SELECT * FROM read_csv('{EMD_CSV.as_posix()}', header=true, all_varchar=true)")
    con.execute(
        "CREATE OR REPLACE TABLE dataset (table_name VARCHAR, dataset VARCHAR, publisher VARCHAR, hub_id INTEGER, "
        "complaint VARCHAR, url VARCHAR, source_file VARCHAR, period_end VARCHAR, updated_at VARCHAR, fetched_at VARCHAR, "
        "rows BIGINT, period_min VARCHAR, period_max VARCHAR, emd_mode VARCHAR, emd_mapped BIGINT, emd_ratio DOUBLE, "
        "latlon_columns VARCHAR, source_kind VARCHAR, origin_url VARCHAR)"
    )
    with tempfile.TemporaryDirectory() as tmp:
        for spec in cfg["hub_files"]:
            meta = manifest.get(str(spec["id"]))
            names = [f["name"] for f in (meta or {}).get("files", []) if spec.get("file", "") in f["name"]]
            if len(names) != 1 or not (FILES / str(spec["id"]) / names[0]).exists():
                print(f"건너뜀 {spec['table']}: 받은 파일 없음 (id {spec['id']})")
                continue
            stats = load_table(con, spec, FILES / str(spec["id"]) / names[0], Path(tmp))
            register(con, spec, {**meta, "hub_id": spec["id"], "source_file": names[0], "origin_url": None}, "hub_file", stats)

        api_manifest_path = INGEST / "raw" / "jejudatahub" / "api" / "manifest.json"
        api_manifest = json.loads(api_manifest_path.read_text(encoding="utf-8")) if api_manifest_path.exists() else {}
        for spec in cfg.get("hub_apis", []):
            meta = api_manifest.get(str(spec["id"]))
            path = INGEST / "raw" / "jejudatahub" / "api" / f"{spec['id']}.csv"
            if not meta or not path.exists() or not meta.get("rows"):
                print(f"건너뜀 {spec['table']}: API 수집본 없음 (id {spec['id']})")
                continue
            stats = load_table(con, spec, path, Path(tmp))
            register(con, spec, {**meta, "hub_id": spec["id"], "source_file": path.name, "origin_url": None}, "hub_api", stats)

        for spec in cfg.get("portal_files", []):
            path = PORTAL / spec["file"]
            row = catalog.get(spec["hub_id"])
            if not path.exists() or not row:
                print(f"건너뜀 {spec['table']}: 파일 또는 카탈로그 항목 없음 ({spec['file']})")
                continue
            stats = load_table(con, spec, path, Path(tmp))
            fetched = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            meta = {
                "dataset": row["title"].strip(),                       # 허브에 등록된 데이터명
                "publisher": "공공데이터포털(" + (row.get("owner") or "제주특별자치도") + ")",
                "hub_id": spec["hub_id"],
                "url": f"https://www.data.go.kr/data/{spec['portal_id']}/fileData.do",
                "origin_url": f"https://jejudatahub.net/data/view/data/{spec['hub_id']}",
                "source_file": spec["file"],
                "period_end": stats["period_max"],                     # 파일에 적힌 데이터 기준일
                "updated_at": stats["period_max"],
                "fetched_at": fetched,
            }
            register(con, spec, meta, "datagokr_file", stats)

    from data import derived
    for name, note in derived.build(con).items():
        print(f"파생 {name}: {note}")

    print(f"{'테이블':30} {'출처':6} {'행':>9}  {'기간':23} emd")
    for name, kind, rows, p0, p1, mode, ratio, latlon in con.execute(
        "SELECT table_name, source_kind, rows, period_min, period_max, emd_mode, emd_ratio, latlon_columns "
        "FROM dataset ORDER BY source_kind DESC, hub_id, table_name"
    ).fetchall():
        emd = f"{mode} {ratio:.1%}" if mode else ("좌표만(경계 필요)" if latlon else "-")
        print(f"{name:30} { {'datagokr_file': '포털', 'hub_api': '허브API'}.get(kind, '허브'):6} {rows:>9}  {(p0 or '-') + '~' + (p1 or '-'):23} {emd}")
    total, hubs = con.execute("SELECT count(*), count(DISTINCT hub_id) FROM dataset").fetchone()
    print(f"적재: 테이블 {total}개 (데이터허브 등록 데이터명 기준 {hubs}종) → {DB}")
    con.close()


if __name__ == "__main__":
    main()
