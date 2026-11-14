"""지표 계산에 쓰는 파생 테이블을 DuckDB에 만든다.

    python data/derived.py        (build_db.py 끝에서도 자동 실행)

pop_emd  : 행정동별 주민등록 총인구·65세 이상 인구
           원자료 = 행정안전부「지역별(법정동) 성별 연령별 주민등록 인구수」(data.go.kr 15099158)
           법정동 → 행정동은 국가데이터처「법정동 연계정보」로 묶는다.
           한 법정동이 여러 행정동에 걸치면 인구를 나눌 근거가 없으므로 그 행정동들은 NULL로 둔다(status=ambiguous).
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


def population_by_emd(pop_csv: Path, link_csv: Path, emd_csv: Path = EMD_CSV) -> tuple[list[tuple], str]:
    """[(emd_code, total, pop65, status)], 기준연월"""
    with emd_csv.open(encoding="utf-8", newline="") as f:
        valid = [r["emd_code"] for r in csv.DictReader(f)]
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
        if code in ambiguous:
            out.append((code, None, None, "ambiguous"))
        elif total[code]:
            out.append((code, total[code], old[code], "ok"))
        else:
            out.append((code, None, None, "missing"))
    month = max(months) if months else ""
    return out, (f"{month[:4]}-{month[4:6]}" if len(month) == 6 else month)


def build(con) -> dict:
    stats = {}
    pop_csv, link_csv = _find("인구수"), _find("법정동 연계정보")
    if pop_csv and link_csv:
        rows, month = population_by_emd(pop_csv, link_csv)
        con.execute("CREATE OR REPLACE TABLE pop_emd (emd_code VARCHAR, total_pop INTEGER, pop_65plus INTEGER, "
                    "status VARCHAR, period VARCHAR)")
        con.executemany("INSERT INTO pop_emd VALUES (?, ?, ?, ?, ?)", [(*r, month) for r in rows])
        ok = sum(r[3] == "ok" for r in rows)
        con.execute("DELETE FROM dataset WHERE table_name = 'pop_emd'")
        fetched = datetime.fromtimestamp(pop_csv.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        con.execute(
            "INSERT INTO dataset VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'aggregate', ?, ?, NULL, 'external_file', NULL)",
            ["pop_emd", POP_DATASET, "공공데이터포털(행정안전부)", POP_URL, unicodedata.normalize("NFC", pop_csv.name),
             month, month, fetched, len(rows), month, month, ok, ok / len(rows)],
        )
        stats["pop_emd"] = f"{ok}/{len(rows)} 행정동 인구 확정 ({month})"
    else:
        stats["pop_emd"] = "건너뜀: raw/emd_link 에 인구수 파일 또는 연계정보 파일 없음"

    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if {"bus_stop", "bus_stop_usage_by_user_type"} <= tables:
        # 이용 실적은 정류소 '이름'만 있다. 같은 이름의 정류소가 모두 한 행정동에 있을 때만 연결한다.
        con.execute("""
            CREATE OR REPLACE TABLE bus_station_emd AS
            SELECT trim(stationName) AS station_name, any_value(emd_code) AS emd_code, count(*) AS stops
            FROM bus_stop WHERE emd_code IS NOT NULL
            GROUP BY 1 HAVING count(DISTINCT emd_code) = 1
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
        stats["bus_usage_monthly"] = f"이용 인원의 {matched / total:.1%}를 행정동에 연결"

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
