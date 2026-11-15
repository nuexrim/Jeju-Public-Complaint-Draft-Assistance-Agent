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
    assert agent._resolve_place("종달리 버스가 줄었다") == "구좌읍"
    assert agent._resolve_place("세화리 버스") == ""          # 구좌읍·표선면 두 곳에 있음 → 묻는다
    assert agent._resolve_place("버스가 연동되어") == ""        # 낱말 일부를 지명으로 보지 않음


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


def test_paid_on_but_no_key_falls_back_without_network(agent, monkeypatch):
    import normalize
    monkeypatch.setenv("ALLOW_PAID_CALLS", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_MOCK", raising=False)
    monkeypatch.setattr(normalize, "_load", lambda use_rag: (None, None))
    monkeypatch.setattr(P.S.llm, "_cache", {})
    r = agent.understand("구좌읍 버스가 줄었어요", use_normalize=False)
    assert r["structured_by"] == "rules" and any("LLMError" in n for n in r["notices"])
