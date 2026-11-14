"""실제 계산 경로(A, use_ontology=false). 적재된 DB와 카탈로그 카드가 있어야 돈다(없으면 건너뜀)."""
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

import run_engine
from package.builder import validate_package

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("q*.json"))
DB = ROOT / "data" / "jeju.duckdb"
CARDS = ROOT / "catalog" / "cards.parquet"

pytestmark = pytest.mark.skipif(not (DB.exists() and CARDS.exists()),
                                reason="data/jeju.duckdb 또는 catalog/cards.parquet 없음 (README 데이터 준비 참고)")


@pytest.fixture(scope="module")
def engine():
    from package.assemble import Engine
    e = Engine(run_engine.load_config())
    yield e
    e.close()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_runs_end_to_end_without_ontology(path, engine):
    query = load(path)
    package = run_engine.run(query, use_ontology=False, engine=engine)
    validate_package(package)
    assert package["engine"] == "catalog"
    assert package["meta"]["use_ontology"] is False
    assert [e["claim_id"] for e in package["evidence"]] == [c["id"] for c in query["claims"]]
    assert not [g for g in package["gaps"] if g["concept"].startswith("읍면동 코드")]
    for ev in package["evidence"]:
        assert not any("MOCK" in c for c in ev["caveats"])
        if ev["verdict"] != "unverifiable":
            assert isinstance(ev["value"], (int, float)) and ev["sources"], ev
        if ev["value"] is None:
            assert ev["verdict"] == "unverifiable"
        for s in ev["sources"]:
            assert s["dataset"] and s["as_of"]


def test_heat_shelter_reported_as_gap(engine):
    package = run_engine.run(load(ROOT / "tests" / "fixtures" / "q09.json"), use_ontology=False, engine=engine)
    assert all(e["verdict"] == "unverifiable" for e in package["evidence"])
    assert [g["concept"] for g in package["gaps"]] == ["무더위쉼터(폭염 대피시설)"]


def test_legal_dong_name_resolves(engine):
    query = load(FIXTURES[0])
    query["location"] = {"emd_name": "도남동"}       # 법정동 → 행정동 이도2동
    package = run_engine.run(query, use_ontology=False, engine=engine)
    assert "이도2동" in package["evidence"][1]["indicator"]


def test_catalog_search_top5_contains_expected_dataset(engine):
    """11/13 완료 기준: 질의별 상위 5개 안에 정답 데이터셋 포함."""
    from retrieval.catalog_search import claim_query, search
    from retrieval.merge import rank

    expected = yaml.safe_load((ROOT / "tests" / "fixtures" / "expected_datasets.yaml").read_text(encoding="utf-8"))
    th = engine.config["thresholds"]
    misses = []
    for path in FIXTURES:
        q = load(path)
        for c in q["claims"]:
            want = expected[q["request_id"]][c["id"]]
            if not want:
                continue
            top = rank(search(claim_query(q, c), {}, top_k=30), None, engine.datasets, engine.config["ranking"],
                       today=date.today(), max_missing=th["max_missing_ratio"], max_years=th["max_staleness_years"])
            if not any(t.card_id in want for t in top):
                misses.append((q["request_id"], c["id"], [t.card_id for t in top]))
    assert not misses
