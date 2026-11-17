"""두 모듈 연결: 이해 → 되묻기 → 뜻 선택 → 확인 → 근거. 네트워크·유료 호출 없음(LLM 응답은 가짜)."""
import json
import os
from pathlib import Path

import pytest

import complaint_agent.pipeline as P
from complaint_agent.conversation import run_conversation

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "evidence_engine" / "tests" / "fixtures"
HAS_DB = (ROOT / "evidence_engine" / "data" / "jeju.duckdb").exists()

STRUCT_OUT = {
    "complaint_type": "교통", "location_mention": "우리 동네", "emd_name": "구좌읍",
    "claims": [{"text": "버스가 줄었다"}, {"text": "노인의 병원 접근이 어렵다"}],
    "targets": ["버스", "병원", "노인"], "user_request": "", "period_from": "", "period_to": "",
}


def norm_out(text, ask=()):
    return {"input": text, "standard": text.replace("줄어부난", "줄어서"), "changes": [], "ask_user": list(ask),
            "warnings": [], "ignored_changes": [], "dict_hits": [], "examples": [], "mode": "rag", "model": "fake"}


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.delenv("ALLOW_PAID_CALLS", raising=False)
    a = P.Agent(mock_engine=not HAS_DB)
    yield a
    a.close()


@pytest.fixture
def fake_llm(monkeypatch):
    """structure 의 Claude 호출과 normalize 를 가짜로. 보낸 프롬프트를 기록한다."""
    import normalize
    sent = []

    def call_json(system, user, schema, purpose="", **kw):
        sent.append(user)
        return json.loads(json.dumps(STRUCT_OUT))

    monkeypatch.setattr(P.S.llm, "call_json", call_json)
    state = {"ask": []}
    monkeypatch.setattr(normalize, "normalize", lambda t, use_rag=True: norm_out(t, state["ask"]))
    return sent, state


def test_ready_query_goes_through_meaning_choice_to_package(agent, fake_llm):
    r = agent.understand("구좌읍 버스가 줄어부난 병원 가기 힘들어요")
    assert r["structured_by"] == "llm" and r["query"]["location"]["emd_name"] == "구좌읍"
    r = agent.answer(r, user_request="")                  # 요구사항 없이 진행
    assert r["status"] == "ready" and not r["questions"]
    interp = agent.meaning_options(r["query"])
    c1 = interp["claims"][0]
    q, again = agent.choose(r["query"], interp, {"c1": c1["recommended"]})
    assert not again and q["claims"][0]["indicator_id"] == c1["options"][0]["indicator_id"]
    pkg = agent.evidence(q)
    hand = agent.handoff(r, q, pkg)
    assert hand["package"]["request_id"] == q["request_id"] and "raw_text" not in hand["query"]


def test_pii_is_masked_before_llm_and_in_answers(agent, fake_llm):
    sent, _ = fake_llm
    r = agent.understand("구좌읍 김철수 씨 집 앞 버스가 줄었어요 010-1234-5678")
    assert "김철수" not in sent[0] and "010-1234-5678" not in sent[0]
    assert set(r["pii_masked"]) == {"이름", "전화번호"}
    r = agent.answer(r, user_request="버스 증차 710-2584 로 연락")
    assert "710-2584" not in json.dumps(r["query"], ensure_ascii=False)


def test_dialect_question_accept_or_correct(agent, fake_llm):
    _, state = fake_llm
    state["ask"] = ["'줄어부난'을(를) '줄어서'(으)로 이해했는데 맞나요?"]
    r = agent.understand("구좌읍 버스가 줄어부난 힘들어요")
    assert r["status"] == "need_input" and r["questions"][0]["field"] == "dialect"
    ok = agent.answer(r, accept_sentence=True)
    assert all(q["field"] != "dialect" for q in ok["questions"])
    state["ask"] = []
    fixed = agent.answer(r, corrected_sentence="버스가 줄어서 힘들어요")   # 읍면동이 빠진 문장으로 고쳐도
    assert fixed["query"]["location"]["emd_name"] == "구좌읍"            # 앞서 정해진 읍면동은 유지
    assert fixed["query"]["normalized_text"] == "버스가 줄어서 힘들어요"


def test_missing_dialect_resources_fall_back_to_plain_structuring(agent, monkeypatch):
    import normalize
    monkeypatch.setattr(normalize, "_load", lambda use_rag: (_ for _ in ()).throw(normalize.ResourceMissing("x")))
    monkeypatch.setattr(P.S.llm, "call_json", lambda *a, **k: json.loads(json.dumps(STRUCT_OUT)))
    r = agent.understand("구좌읍 버스가 줄었어요")
    assert r["structured_by"] == "llm" and r["normalize"] is None
    assert any("표준어 변환을 하지 못해" in n for n in r["notices"])


def test_normalize_library_raises_instead_of_exiting(monkeypatch, tmp_path):
    import normalize
    monkeypatch.setattr(normalize, "DICT_PATH", tmp_path / "none.json")
    monkeypatch.setattr(normalize, "_res", {})
    with pytest.raises(normalize.ResourceMissing):
        normalize.normalize("고냉이")


def test_blocked_llm_uses_rules_and_types_match_fixtures(agent, monkeypatch):
    def blocked(*a, **k):
        raise P.llm.PaidCallNotAllowed("blocked")
    monkeypatch.setattr(P.S.llm, "call_json", blocked)
    ok = 0
    for fp in sorted(FIX.glob("q*.json")):
        gold = json.loads(fp.read_text(encoding="utf-8"))
        r = agent.understand(f"{gold['location']['emd_name']} {gold['normalized_text']}", use_normalize=False)
        assert r["structured_by"] == "rules" and r["query"]["location"]["emd_name"] == gold["location"]["emd_name"]
        ok += r["query"]["complaint_type"] == gold["complaint_type"]
    assert ok == 10


def test_rules_resolve_unique_village_but_not_ambiguous(agent):
    assert agent._resolve_place("종달리 버스가 줄었다") == ("구좌읍", "")
    assert agent._resolve_place("세화리 버스") == ("", "")          # 구좌읍·표선면 두 곳에 있음 → 묻는다
    assert agent._resolve_place("버스가 연동되어") == ("", "")        # 낱말 일부를 지명으로 보지 않음


def test_rules_short_place_name_asks_to_confirm(agent, monkeypatch):
    """'노형'처럼 줄여 말하면 노형동으로 잡되, 글자만 보고 고른 것이라 맞는지 묻는다."""
    def blocked(*a, **k):
        raise P.llm.PaidCallNotAllowed("blocked")
    monkeypatch.setattr(P.S.llm, "call_json", blocked)
    r = agent.understand("노형에 사는데 병원이 너무 멉니다", use_normalize=False)
    assert r["query"]["location"]["emd_name"] == "노형동"
    confirm = r["questions"][0]                         # 요구사항보다 먼저 묻는다
    assert confirm["field"] == "emd_name" and confirm.get("confirm") and "'노형'" in confirm["question"]
    later = agent.answer(r, user_request="")              # 다른 질문에 먼저 답해도 확인 질문은 남는다
    assert any(q.get("confirm") for q in later["questions"])
    assert agent.answer(r, confirm_emd=True)["query"]["location"]["emd_name"] == "노형동"
    no = agent.answer(r, confirm_emd=False)
    assert no["query"]["location"]["emd_name"] == "" and no["status"] == "need_input"
    assert agent._resolve_place("도로 중앙에 쓰레기가 많아요") == ("", "")   # 흔한 낱말은 지명으로 보지 않음


def test_rules_aspect_reads_conjugated_predicates(agent, monkeypatch):
    """규칙 구조화도 '멉니다·힘듭니다·없습니다' 같은 활용형으로 측면을 읽는다."""
    def blocked(*a, **k):
        raise P.llm.PaidCallNotAllowed("blocked")
    monkeypatch.setattr(P.S.llm, "call_json", blocked)
    r = agent.answer(agent.understand("구좌읍 정류소까지 너무 멉니다", use_normalize=False), user_request="")
    interp = agent.meaning_options(r["query"])
    assert interp["claims"][0]["aspect"] == "거리·이용 어려움"


def test_choose_several_meanings_makes_one_claim_each(agent, fake_llm):
    """규칙 구조화는 민원 전체가 주장 하나 → 뜻을 여러 개 고르면 고른 뜻마다 주장이 생긴다."""
    r = agent.answer(agent.understand("구좌읍 버스가 줄어부난 병원 가기 힘들어요"), user_request="")
    query = {**r["query"], "claims": [{"id": "c1", "text": "버스가 줄어서 할머니들이 병원 가기 힘들다"}]}
    interp = agent.meaning_options(query)
    c1 = interp["claims"][0]
    assert c1["max_picks"] == 3
    ind = [o for o in c1["options"] if o["kind"] == "indicator"]
    q, again = agent.choose(query, interp, {"c1": [ind[0]["option_id"], ind[1]["option_id"], ind[0]["option_id"]]})
    assert [c["id"] for c in q["claims"]] == ["c1", "c1-2"] and not again        # 같은 뜻 두 번은 하나로
    assert [c["indicator_id"] for c in q["claims"]] == [ind[0]["indicator_id"], ind[1]["indicator_id"]]
    pkg = agent.evidence(q)
    assert [e["claim_id"] for e in pkg["evidence"]] == ["c1", "c1-2"]
    q2, again2 = agent.choose(query, interp, {"c1": [ind[0]["option_id"], {"other": "막차가 빨라졌다"}]})
    assert again2 == ["c1-2"] and "indicator_id" not in q2["claims"][1]
    with pytest.raises(ValueError):
        agent.choose(query, interp, {"c1": [o["option_id"] for o in ind[:4]]})   # max_picks 초과
    with pytest.raises(ValueError):
        agent.choose(query, interp, {"c1": []})


def test_parse_picks():
    from complaint_agent.conversation import parse_picks
    assert parse_picks("", 6, 3) == [] and parse_picks("2", 6, 3) == [1]
    assert parse_picks("1,3", 6, 3) == [0, 2] and parse_picks("1 3 3", 6, 3) == [0, 2]
    assert parse_picks("7", 6, 3) is None and parse_picks("1,2,3,4", 6, 3) is None and parse_picks("가", 6, 3) is None


def test_emd_answers(agent, fake_llm):
    r = agent.understand("버스가 줄었어요")
    r = agent.answer(r, confirm_emd=False)
    assert r["status"] == "need_input" and any(q["field"] == "emd_name" for q in r["questions"])
    bad = agent.answer(r, emd_name="없는마을")
    assert bad["status"] == "need_input" and any("찾지 못했어요" in n for n in bad["notices"])
    good = agent.answer(r, emd_name="종달리")
    assert good["query"]["location"]["emd_name"] == "구좌읍"


def test_choose_other_and_invalid(agent, fake_llm):
    r = agent.answer(agent.understand("구좌읍 버스가 줄었어요"), user_request="")
    interp = agent.meaning_options(r["query"])
    q, again = agent.choose(r["query"], interp, {"c1": {"other": "막차 시간이 당겨졌다 010-1111-2222"}})
    assert again == ["c1"] and "indicator_id" not in q["claims"][0] and "010-1111-2222" not in q["claims"][0]["text"]
    with pytest.raises(ValueError):
        agent.choose(r["query"], interp, {"c1": "c1-o99"})


def test_summary_uses_only_query_fields():
    q = {"complaint_type": "교통", "location": {"emd_name": "구좌읍"}, "claims": [{"id": "c1", "text": "버스가 줄었다"}],
         "period": {"from": "2025-01", "to": "2025-12"}, "user_request": "버스 증차"}
    assert P.Agent.summary(q) == "[교통 · 구좌읍, 2025-01~2025-12] 버스가 줄었다. 바라는 점: 버스 증차. 이렇게 이해했어요. 맞나요?"


def test_env_loader_never_turns_on_paid_calls(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOW_PAID_CALLS", raising=False)
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    env = tmp_path / ".env"
    env.write_text("ALLOW_PAID_CALLS=1\nCLAUDE_MODEL=claude-haiku-5-5\n", encoding="utf-8")
    assert P.load_env([env]) == ["CLAUDE_MODEL"] and "ALLOW_PAID_CALLS" not in os.environ


def test_scripted_conversation(agent, fake_llm):
    answers = iter(["", "", "y"])          # 요구사항 없음, 추천 뜻, 확인
    out = run_conversation("구좌읍 버스가 줄어부난 병원 가기 힘들어요", ask=lambda p: next(answers, "y"),
                           say=lambda s: None, agent=agent)
    assert out and len(out["package"]["evidence"]) == 2
    assert all(c.get("indicator_id") for c in out["query"]["claims"])


def test_scripted_conversation_with_several_meanings(agent, monkeypatch):
    """Claude 없이(규칙) 한 문장에 불편 두 개 → 뜻 두 개를 골라 근거 두 개."""
    def blocked(*a, **k):
        raise P.llm.PaidCallNotAllowed("blocked")
    monkeypatch.setattr(P.S.llm, "call_json", blocked)
    answers = iter(["", "1,2", "y"])        # 요구사항 없음, 뜻 1·2번, 확인
    out = run_conversation("구좌읍 버스가 줄어서 할머니들이 병원 가기가 힘듭니다", ask=lambda p: next(answers, "y"),
                           say=lambda s: None, agent=agent)
    assert out and out["structured_by"] == "rules" and len(out["package"]["evidence"]) == 2


def test_paid_on_but_no_key_falls_back_without_network(agent, monkeypatch):
    import normalize
    monkeypatch.setenv("ALLOW_PAID_CALLS", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_MOCK", raising=False)
    monkeypatch.setattr(normalize, "_load", lambda use_rag: (None, None))
    monkeypatch.setattr(P.S.llm, "_cache", {})
    r = agent.understand("구좌읍 버스가 줄었어요", use_normalize=False)
    assert r["structured_by"] == "rules" and any("LLMError" in n for n in r["notices"])


def test_no_evidence_question_follows_engine_topics(agent, monkeypatch):
    """'근거 없이 작성할까요?'는 유형 이름이 아니라 엔진에 자료가 있는 주제로 정한다."""
    def blocked(*a, **k):
        raise P.llm.PaidCallNotAllowed("blocked")
    monkeypatch.setattr(P.S.llm, "call_json", blocked)
    price = agent.understand("구좌읍 물가가 너무 올랐어요", use_normalize=False)
    q = [x for x in price["questions"] if x["field"] == "complaint_type"]
    assert q and "경로당" in q[0]["question"] and "생활물가" not in q[0]["question"]
    hall = agent.understand("구좌읍 경로당이 부족해요", use_normalize=False)
    assert hall["query"]["complaint_type"] == "기타"                 # 유형 보기에 복지가 없어 '기타'
    assert not [x for x in hall["questions"] if x["field"] == "complaint_type"]   # 그래도 자료는 있음
    apt = agent.understand("노형동 아파트 쓰레기가 넘쳐요", use_normalize=False)
    assert apt["query"]["complaint_type"] == "환경·청소"


def test_llm_path_short_place_name_when_claude_leaves_it_empty(agent, monkeypatch):
    """Claude 가 읍면동을 비워 둬도 문장 속 줄인 이름(노형)을 찾아 확인 질문과 함께 채운다."""
    out = {**STRUCT_OUT, "emd_name": "", "location_mention": ""}
    monkeypatch.setattr(P.S.llm, "call_json", lambda *a, **k: json.loads(json.dumps(out)))
    r = agent.understand("노형 사는데 버스가 줄었어요", use_normalize=False)
    assert r["structured_by"] == "llm" and r["query"]["location"]["emd_name"] == "노형동"
    assert any(q["field"] == "emd_name" and q.get("confirm") for q in r["questions"])


def test_conversation_skips_meaning_question_without_options(agent, monkeypatch):
    """엔진 주제 밖 주장(선택지가 '기타'뿐)은 뜻을 묻지 않고 문장 그대로 근거를 찾는다."""
    def blocked(*a, **k):
        raise P.llm.PaidCallNotAllowed("blocked")
    monkeypatch.setattr(P.S.llm, "call_json", blocked)
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return "y" if "맞나요" in prompt else ""
    out = run_conversation("구좌읍 공영주차장이 부족해요", ask=ask, say=lambda s: None, agent=agent)
    assert out and not any("어떤 뜻인가요" in p for p in asked) and not any("직접 써" in p for p in asked)


def _rules(monkeypatch):
    def blocked(*a, **k):
        raise P.llm.PaidCallNotAllowed("blocked")
    monkeypatch.setattr(P.S.llm, "call_json", blocked)


def test_place_question_offers_choices_and_takes_several(agent, monkeypatch):
    """읍면동을 못 알아들으면 목록(시·읍면/동 묶음 + 제주 전체)을 주고, 여러 곳을 고르면 장소마다 근거를 낸다."""
    _rules(monkeypatch)
    r = agent.understand("병원이 너무 부족해요", use_normalize=False)
    q = next(x for x in r["questions"] if x["field"] == "emd_name")
    ch = q["choices"]
    assert ch["all"] == "제주 전체" and ch["max_picks"] == 3
    assert sum(len(g["names"]) for g in ch["groups"]) == 43
    r = agent.answer(r, emd_names=["구좌읍", "성산읍"], user_request="")
    assert r["status"] == "ready" and r["places"] == ["구좌읍", "성산읍"]
    assert r["query"]["location"]["emd_name"] == "구좌읍"
    assert "구좌읍, 성산읍" in agent.summary(r["query"], r["places"])
    pkg = agent.evidence(r["query"], r["places"])
    labels = [e["indicator"] for e in pkg["evidence"]]
    assert any("구좌읍" in x for x in labels) and any("성산읍" in x for x in labels)
    assert agent.handoff(r, r["query"], pkg)["places"] == ["구좌읍", "성산읍"]
    with pytest.raises(ValueError):
        agent.answer(r, emd_names=["구좌읍", "성산읍", "표선면", "남원읍"])


def test_whole_jeju_choice(agent, monkeypatch):
    _rules(monkeypatch)
    r = agent.answer(agent.understand("버스가 줄었어요", use_normalize=False), emd_names=["제주 전체"], user_request="")
    assert r["status"] == "ready" and r["query"]["location"]["emd_name"] == "제주 전체"
    pkg = agent.evidence(r["query"], r["places"])
    assert pkg["evidence"]
    assert any("제주 전체" in n for n in agent.handoff(r, r["query"], pkg)["notices"])


def test_terminal_place_answer_with_commas(agent, monkeypatch):
    _rules(monkeypatch)
    r = agent.understand("병원이 부족해요", use_normalize=False)
    assert agent.answer(r, emd_name="구좌, 성산읍")["places"] == ["구좌읍", "성산읍"]
    assert agent.answer(r, emd_name="전체")["places"] == ["제주 전체"]
