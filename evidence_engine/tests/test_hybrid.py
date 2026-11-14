"""하이브리드(B 온톨로지 + A 카탈로그). 경로 탐색 단위 테스트는 작은 ttl로, 통합 테스트는 jeju.ttl·DB가 있을 때만."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TTL = ROOT / "ontology" / "jeju.ttl"
DB = ROOT / "data" / "jeju.duckdb"
FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("q*.json"))

MINI = """
@prefix : <http://example.org/ontology#> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> . @prefix skos: <http://www.w3.org/2004/02/skos/core#> .
:concernsDomain a owl:ObjectProperty ; rdfs:label "관련 분야"@ko ; rdfs:domain owl:Thing ; rdfs:range :ComplaintDomain .
:ComplaintDomain a owl:Class ; rdfs:label "민원 분야"@ko .
:HealthcareAccess a owl:Class ; rdfs:subClassOf :ComplaintDomain ; rdfs:label "의료 접근"@ko ; skos:altLabel "진료"@ko .
:Transportation a owl:Class ; rdfs:subClassOf :ComplaintDomain ; rdfs:label "교통"@ko .
:Safety a owl:Class ; rdfs:subClassOf :ComplaintDomain ; rdfs:label "안전"@ko .
:Facility a owl:Class ; rdfs:label "시설"@ko .
:HealthcareFacility a owl:Class ; rdfs:subClassOf :Facility ,
   [ a owl:Restriction ; owl:onProperty :concernsDomain ; owl:someValuesFrom :HealthcareAccess ] ; rdfs:label "의료 시설"@ko .
:Clinic a owl:Class ; rdfs:subClassOf :HealthcareFacility ; rdfs:label "병·의원"@ko ; skos:altLabel "병원"@ko .
:Shelter a owl:Class ; rdfs:subClassOf :Facility ; rdfs:label "대피 시설"@ko .
:BusStopFacility a owl:Class ; rdfs:subClassOf :Facility ; rdfs:label "정류소"@ko .
:HeatShelter a owl:Class ; rdfs:subClassOf :Shelter ; rdfs:label "무더위쉼터"@ko ; skos:altLabel "폭염"@ko .
:Hospital a owl:Class ; rdfs:subClassOf :Clinic ; rdfs:label "병·의원 정보"@ko ; :sourceTable "hospital" .
:BusStop a owl:Class ; rdfs:subClassOf :BusStopFacility ; rdfs:label "버스 정류소"@ko ; :sourceTable "bus_stop" .
:DimEmd a owl:Class ; rdfs:label "행정동"@ko ; :sourceTable "dim_emd" .
:Hospital_hasEmdCode a owl:ObjectProperty ; rdfs:domain :Hospital ; rdfs:range :DimEmd .
:BusStop_hasEmdCode a owl:ObjectProperty ; rdfs:domain :BusStop ; rdfs:range :DimEmd .
"""


@pytest.fixture(scope="module")
def mini(tmp_path_factory):
    from ontology.paths import OntologyGraph
    p = tmp_path_factory.mktemp("onto") / "mini.ttl"
    p.write_text(MINI, encoding="utf-8")
    return OntologyGraph(p)


def test_domain_concept_reaches_table_through_restriction(mini):
    paths = mini.paths_to_tables(["http://example.org/ontology#HealthcareAccess"], 3)
    assert paths["hospital"][0] == 3
    assert paths["hospital"][1] == "의료 접근 —관련 분야(역)→ 의료 시설 —하위개념→ 병·의원 —하위개념→ 병·의원 정보"


def test_hubs_are_not_passed_through(mini):
    # 병원 → (행정동 허브) → 버스 정류소, (시설 허브) → 정류소 는 막혀야 한다
    paths = mini.paths_to_tables(["http://example.org/ontology#Clinic"], 3)
    assert "bus_stop" not in paths and "dim_emd" not in paths


def test_concept_without_data_is_gap_but_domain_concept_is_not(mini):
    from retrieval.ontology_expand import expand
    e = expand("폭염 때 갈 곳이 없다", "", mini, {})
    assert [g["concept"] for g in e.gaps] == ["무더위쉼터"]
    assert expand("진료 받기 어렵다", "", mini, {}).gaps == []      # 의료 접근: 관계로 데이터가 뒷받침


def test_longer_match_suppresses_generic_fragment(mini):
    from ontology.concept_index import match_concepts
    labels = [m.label for m in match_concepts("대피 시설이 부족하다", mini)]
    assert "대피 시설" in labels and "시설" not in labels


# ---- 통합 (jeju.ttl + DB) ----
needs_all = pytest.mark.skipif(not (TTL.exists() and DB.exists()), reason="ontology/jeju.ttl 또는 data/jeju.duckdb 없음")


@pytest.fixture(scope="module")
def engine():
    import run_engine
    from package.assemble import Engine
    e = Engine(run_engine.load_config())
    yield e
    e.close()


@needs_all
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_runs_hybrid(path, engine):
    import run_engine
    from package.builder import validate_package
    q = json.loads(path.read_text(encoding="utf-8"))
    p = run_engine.run(q, use_ontology=True, engine=engine)
    validate_package(p)
    assert p["engine"] == "hybrid" and p["meta"]["use_ontology"] is True
    for ev in p["evidence"]:
        if ev["found_by"] in ("ontology", "both") and ev["verdict"] != "unverifiable" or ev["relation_path"]:
            assert ev["relation_path"] and " —" in ev["relation_path"]


@needs_all
def test_heat_shelter_gap_detected_by_ontology(engine):
    import run_engine
    q = json.loads((ROOT / "tests" / "fixtures" / "q09.json").read_text(encoding="utf-8"))
    p = run_engine.run(q, use_ontology=True, engine=engine)
    assert {"concept": "무더위쉼터", "detected_by": "ontology"}.items() <= p["gaps"][0].items()


@needs_all
def test_switch_off_gives_catalog_engine(engine):
    import run_engine
    q = json.loads(FIXTURES[0].read_text(encoding="utf-8"))
    p = run_engine.run(q, use_ontology=False, engine=engine)
    assert p["engine"] == "catalog" and all(e["relation_path"] is None for e in p["evidence"])


@needs_all
def test_competency_questions_meet_adoption_bar():
    """11/16 B 채택 기준: 15개 중 10개 이상."""
    from ontology.evaluate_cq import PASS_MARK, evaluate
    results = evaluate()
    assert len(results) == 15
    assert sum(r["ok"] for r in results) >= PASS_MARK
