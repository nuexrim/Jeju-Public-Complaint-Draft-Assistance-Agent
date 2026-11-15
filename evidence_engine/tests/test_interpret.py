"""주장과 지표의 뜻 맞추기: 측면(aspect) 판별, 간접 지표 처리, 해석 선택지(기타 포함)."""
import copy
import json
from pathlib import Path

import pytest

import run_engine
from indicators.library import claim_aspect, load_registry, proxy_gap, indicator_by_id
from package.builder import ContractError, validate_interpretation

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jeju.duckdb"
FIX = ROOT / "tests" / "fixtures"
REG = load_registry()


def q(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def test_registry_meaning_fields_are_consistent():
    aspects = {a["id"] for a in REG["aspects"]}
    topics = {t["id"] for t in REG["topics"]}
    for ind in REG["indicators"]:
        if ind.get("role") == "context":
            continue
        assert ind["topic"] in topics and ind["measures"] in aspects and ind["ask"], ind["id"]
    for g in REG["known_gaps"]:
        assert g["id"] and g["topic"] in topics and g["aspect"] in aspects and g["ask"]


@pytest.mark.parametrize("text,aspect", [
    ("버스가 줄었다", "service"),
    ("버스 배차 간격이 길어졌다", "service"),
    ("버스 이용객이 줄었다", "usage"),
    ("노인이 버스를 이용하기 어렵다", "access"),
    ("응급의료기관이 멀다", "access"),
    ("버스 정류소가 부족하다", "supply"),
    ("야간에 이용할 약국이 없다", "supply"),
    ("교통사고가 자주 발생한다", "risk"),
    ("버스 이야기", None),
])
def test_claim_aspect(text, aspect):
    assert claim_aspect(text) == aspect


def test_proxy_gap_only_when_no_indicator_measures_that_aspect():
    assert proxy_gap(indicator_by_id("bus_usage_change_pct"), "service")["id"] == "bus_service"
    # 의료에는 거리(access)를 재는 응급의료 지표가 있으므로 '야간 진료' 빈칸을 만들지 않는다
    assert proxy_gap(indicator_by_id("clinic_per_1k_elderly"), "access") is None
    assert proxy_gap(indicator_by_id("bus_stop_per_1k_pop"), "supply") is None


def test_options_for_bus_decrease_put_service_meaning_first_and_end_with_other():
    out = run_engine.interpret(q("q01"), mock=True)
    validate_interpretation(out)
    c1 = out["claims"][0]
    ids = [o["indicator_id"] for o in c1["options"]]
    assert c1["aspect"] == "운행 횟수·배차" and ids[0] == "gap:bus_service" and c1["recommended"] == "c1-o1"
    assert "bus_usage_change_pct" in ids
    last = c1["options"][-1]
    assert last["kind"] == "other" and last["indicator_id"] is None and "기타" in last["text"]
    assert all("구좌읍" in o["text"] for o in c1["options"] if o["indicator_id"] == "bus_usage_change_pct")


def test_elderly_options_need_elderly_words():
    query = q("q05")
    query["claims"] = [{"id": "c1", "text": "의원이 부족하다"}]
    query["targets"], query["normalized_text"] = ["의원"], "의원이 부족하다"
    ids = [o["indicator_id"] for o in run_engine.interpret(query, mock=True)["claims"][0]["options"]]
    assert "clinic_per_1k_pop" == ids[0] and "clinic_per_1k_elderly" not in ids


def test_claim_without_known_topic_still_offers_other():
    query = q("q01")
    query["claims"] = [{"id": "c1", "text": "동네 분위기가 안 좋다"}]
    query["targets"] = []
    opts = run_engine.interpret(query, mock=True)["claims"][0]["options"]
    assert [o["kind"] for o in opts] == ["other"]


def test_options_do_not_copy_personal_data():
    query = q("q01")
    query["claims"][0]["text"] = "김철수 씨 집(한림로 650) 앞 버스가 줄었다"
    dumped = json.dumps(run_engine.interpret(query, mock=True), ensure_ascii=False)
    assert "김철수" not in dumped and "650" not in dumped


def test_unknown_indicator_id_is_rejected():
    query = q("q01")
    query["claims"][0]["indicator_id"] = "no_such_indicator"
    with pytest.raises(ContractError):
        run_engine.run(query, mock=True)


def test_cli_interpret_mock(capsys):
    assert run_engine.main(["--query", str(FIX / "q01.json"), "--interpret", "--mock"]) == 0
    assert json.loads(capsys.readouterr().out)["claims"][0]["options"]


# ---- 실제 엔진 ----
needs_db = pytest.mark.skipif(not DB.exists(), reason="data/jeju.duckdb 없음")


@pytest.fixture(scope="module")
def engine():
    if not DB.exists():
        pytest.skip("data/jeju.duckdb 없음")
    from package.assemble import Engine
    e = Engine(run_engine.load_config())
    yield e
    e.close()


@needs_db
def test_bus_decrease_with_ridership_is_downgraded_and_records_missing_data(engine):
    p = run_engine.run(q("q01"), engine=engine)
    c1 = p["evidence"][0]
    assert c1["verdict"] == "partial"
    assert any(c.startswith("간접 지표: 주장은 '운행 횟수·배차'") for c in c1["caveats"])
    concepts = [g["concept"] for g in p["gaps"]]
    assert "버스 운행 횟수·배차 간격" in concepts and "야간·휴일 진료 병원" not in concepts


@needs_db
def test_elderly_bus_difficulty_is_proxy(engine):
    c2 = run_engine.run(q("q04"), engine=engine)["evidence"][1]
    assert c2["verdict"] == "partial" and any(c.startswith("간접 지표") for c in c2["caveats"])


@needs_db
def test_direct_meaning_is_not_flagged(engine):
    for name in ("q03", "q05", "q06", "q08", "q10"):
        for e in run_engine.run(q(name), engine=engine)["evidence"]:
            assert not any(c.startswith("간접 지표") for c in e["caveats"]), (name, e["claim_id"])


@needs_db
def test_chosen_indicator_is_used_without_proxy_downgrade(engine):
    query = q("q01")
    query["claims"] = [{"id": "c1", "text": "구좌읍 정류소에서 버스를 타는 사람이 1년 전보다 줄었다",
                        "indicator_id": "bus_usage_change_pct"}]
    e = run_engine.run(query, engine=engine)["evidence"][0]
    assert e["indicator"].startswith("버스 정류소 이용 인원 증감률") and e["verdict"] == "supported"
    assert "사용자가 해석 선택지에서 고른 뜻으로 계산" in e["caveats"]


@needs_db
def test_chosen_no_data_meaning_is_unverifiable_with_gap(engine):
    query = q("q01")
    query["claims"] = [{"id": "c1", "text": "버스 운행 횟수나 배차가 줄었다", "indicator_id": "gap:bus_service"}]
    p = run_engine.run(query, engine=engine)
    assert p["evidence"][0]["verdict"] == "unverifiable"
    assert p["gaps"][0]["concept"] == "버스 운행 횟수·배차 간격"


@needs_db
def test_engine_interpret_matches_schema(engine):
    for name in ("q01", "q04", "q09"):
        validate_interpretation(run_engine.interpret(q(name), engine=engine))


# ---- 2026-11-15: 적재 자료 지표 · 맥락 지표(교차 확인) · 약한 근거 ----
def _one(claim: str, emd: str = "구좌읍") -> dict:
    query = q("q01")
    query["claims"] = [{"id": "c1", "text": claim}]
    query["normalized_text"], query["location"] = claim, {"emd_name": emd}
    query.pop("targets", None)
    return query


def test_context_indicators_are_never_selected_or_offered():
    from indicators.library import select_indicator
    tables = {t for i in REG["indicators"] for t in i["tables"]}
    ind, others = select_indicator("인구가 줄었다", "", tables)
    assert (ind is None or ind.get("role") != "context") and "pop_change_pct" not in others
    opts = run_engine.interpret(_one("어르신이 많은데 경로당이 부족하다"), mock=True)["claims"][0]["options"]
    assert all(o["indicator_id"] not in {"pop_change_pct", "elderly_share_pct", "pop_density"} for o in opts)


def test_context_indicator_cannot_be_chosen_by_user():
    query = _one("버스가 줄었다")
    query["claims"][0]["indicator_id"] = "pop_change_pct"
    with pytest.raises(ContractError):
        run_engine.run(query, mock=True)


def test_streetlight_claim_offers_no_data_meaning_first():
    opts = run_engine.interpret(_one("밤에 가로등이 없어 길이 어둡다"), mock=True)["claims"][0]["options"]
    assert opts[0]["indicator_id"] == "gap:streetlight"


@needs_db
def test_weak_evidence_indicator_is_capped_at_partial(engine):
    e = run_engine.run(_one("CCTV가 부족하다"), engine=engine)["evidence"][0]
    assert e["indicator"].startswith("인구 1만 명당 공공 CCTV") and e["verdict"] in ("partial", "unverifiable")
    assert e["verdict"] == "unverifiable" or any(c.startswith("약한 근거") for c in e["caveats"]) \
        or "중간 수준" in " ".join(e["caveats"])


@needs_db
def test_related_context_is_attached_as_cross_check(engine):
    e = run_engine.run(q("q01"), engine=engine)["evidence"][0]
    checks = [c for c in e["caveats"] if c.startswith("교차 확인 · 1년 인구 증감률")]
    assert len(checks) == 1 and "구좌읍" in checks[0] and "확인 이유" in checks[0]
    assert e["verdict"] == "partial"          # 교차 확인은 판정을 바꾸지 않는다


@needs_db
def test_new_indicators_compute_for_all_43_emd(engine):
    from indicators.library import compute
    for iid in ("senior_center_per_1k_elderly", "otc_store_per_10k_pop", "pedestrian_accident_per_10k_pop",
                "cctv_cameras_per_10k_pop", "pop_change_pct", "elderly_share_pct", "pop_density"):
        r = compute(indicator_by_id(iid), "50110256", engine.runner)
        assert len(r.values) == 43 and r.value is not None, iid
