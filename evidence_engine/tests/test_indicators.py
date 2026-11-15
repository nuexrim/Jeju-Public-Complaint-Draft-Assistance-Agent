"""지표 선택·판정 규칙·읽기 전용 SQL (DB 없이 도는 단위 테스트 위주)."""
import json
from pathlib import Path

import duckdb
import pytest

from compute.sql_runner import SQLRejected, SQLRunner, check_sql
from indicators.library import _shift, known_gap, load_registry, select_indicator
from verdict.rules import judge, worse_share

ROOT = Path(__file__).resolve().parent.parent
ALL_TABLES = {t for ind in load_registry()["indicators"] for t in ind["tables"]}

# fixture claim → 기대 지표 (None = 지표 없음, 빈칸으로 보고)
EXPECTED = {
    ("r-0001", "c1"): "bus_usage_change_pct",
    ("r-0001", "c2"): "clinic_per_1k_elderly",
    ("r-0002", "c1"): "evening_bus_share_jeju",
    ("r-0003", "c1"): "accident_hotspot_occurrences",
    ("r-0004", "c1"): "bus_stop_per_1k_pop",
    ("r-0004", "c2"): "elderly_bus_use_per_capita",
    ("r-0005", "c1"): "clinic_per_1k_pop",
    ("r-0005", "c2"): "clinic_per_1k_elderly",
    ("r-0006", "c1"): "night_pharmacy_count",
    ("r-0006", "c2"): "emergency_distance_km",
    ("r-0007", "c1"): "clean_house_pop_per_site",
    ("r-0008", "c1"): "recycling_center_per_10k_pop",
    ("r-0009", "c1"): None,
    ("r-0009", "c2"): None,
    ("r-0010", "c1"): "refugee_facility_per_10k_pop",
}


def _claims():
    for path in sorted((ROOT / "tests" / "fixtures").glob("q*.json")):
        q = json.loads(path.read_text(encoding="utf-8"))
        context = " ".join([q["normalized_text"], *q.get("targets", [])])
        for c in q["claims"]:
            yield q["request_id"], c, context


@pytest.mark.parametrize("rid,claim,context", list(_claims()), ids=lambda x: x if isinstance(x, str) and x.startswith("r-") else "")
def test_fixture_claim_selects_expected_indicator(rid, claim, context):
    ind, _ = select_indicator(claim["text"], context, ALL_TABLES)
    assert (ind["id"] if ind else None) == EXPECTED[(rid, claim["id"])]


def test_heat_shelter_is_known_gap_not_refugee_facility():
    ind, _ = select_indicator("노인 인구에 비해 수용 인원이 적다", "여름 폭염 때 대피시설이 부족 폭염", ALL_TABLES)
    assert ind is None
    assert known_gap("노인 인구에 비해 수용 인원이 적다", "여름 폭염 때")["concept"].startswith("무더위쉼터")


def test_indicator_needs_its_tables_loaded():
    ind, _ = select_indicator("의원이 부족하다", "", ALL_TABLES - {"hospital"})
    assert ind is None


def test_every_indicator_is_well_formed():
    reg = load_registry()
    ids = {i["id"] for i in reg["indicators"]}
    for ind in reg["indicators"]:
        if ind.get("role") == "context":    # 맥락 지표: 고르는 낱말 대신 '확인 이유'가 있다
            assert {"id", "name", "unit", "tables", "scope", "rule", "sql", "why"} <= ind.keys(), ind["id"]
        else:
            assert {"id", "name", "unit", "tables", "keywords", "scope", "rule", "sql"} <= ind.keys(), ind["id"]
        for rid in ind.get("related", []):  # 교차 확인은 맥락 지표만 가리킨다
            assert rid in ids and next(i for i in reg["indicators"] if i["id"] == rid).get("role") == "context", rid
        assert ind["rule"] in {"rank", "trend", "zero", "context"}
        if ind["rule"] in {"rank", "zero", "trend"}:
            assert ind.get("worse") in {"high", "low"}, ind["id"]
        if ind.get("window"):
            assert ind.get("window_source"), ind["id"]
        check_sql(ind["sql"])          # 읽기 전용 규칙 통과


@pytest.mark.parametrize("value,peers,worse,expected", [
    (1.0, [2, 3, 4, 5, 6], "low", "supported"),        # 모두 이곳보다 많음 → 부족 주장 지지
    (9.0, [2, 3, 4, 5, 6], "low", "contradicted"),
    (4.0, [2, 3, 5, 6], "low", "partial"),
    (90.0, [10, 20, 30], "high", "supported"),         # 사고 건수가 가장 많음
])
def test_rank_rule(value, peers, worse, expected):
    assert judge("rank", value, peers=peers, worse=worse).verdict == expected


def test_other_rules():
    assert judge("trend", -11.0).verdict == "supported"
    assert judge("trend", -1.0).verdict == "partial"
    assert judge("trend", 3.0).verdict == "contradicted"
    assert judge("zero", 0).verdict == "supported"
    assert judge("zero", 2).verdict == "contradicted"
    assert judge("context", 15.5).verdict == "unverifiable"
    assert judge("rank", None, peers=[1]).verdict == "unverifiable"


def test_worse_share_counts_ties_half():
    assert worse_share(3, [3, 3], "low") == 0.5


def test_month_shift():
    assert _shift("2025-01", -1) == "2024-12"
    assert _shift("2025-09", -24) == "2023-09"
    assert _shift("2024-12", 1) == "2025-01"


@pytest.mark.parametrize("sql", [
    "DROP TABLE x", "SELECT 1; DELETE FROM x", "INSERT INTO x VALUES (1)",
    "SELECT * FROM read_csv('/etc/passwd')", "PRAGMA database_list", "ATTACH 'a.db'", "COPY x TO 'y.csv'",
])
def test_sql_runner_rejects_non_readonly(sql):
    with pytest.raises(SQLRejected):
        check_sql(sql)


def test_sql_runner_allows_keywords_inside_strings_and_limits_rows(tmp_path):
    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t AS SELECT range AS n, 'drop table' AS note FROM range(50)")
    con.close()
    runner = SQLRunner(db, max_rows=10)
    try:
        res = runner.run("SELECT n, note FROM t WHERE note = 'drop table' ORDER BY n")
        assert len(res.rows) == 10 and res.truncated
        assert runner.run("WITH a AS (SELECT count(*) c FROM t) SELECT c FROM a").rows == [(50,)]
        with pytest.raises(duckdb.Error):     # read_only 연결이라 DB 단에서도 막힘
            runner.con.execute("CREATE TABLE z (a INT)")
    finally:
        runner.close()
