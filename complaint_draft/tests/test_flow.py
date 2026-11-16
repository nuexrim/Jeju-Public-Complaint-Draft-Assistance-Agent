"""화면용 대화 흐름(flow.py): 가짜 Agent 로 단계 전환을 보고, 실제 Agent(규칙·mock 엔진)로 끝까지 한 번 돈다."""
import copy

import pytest

from complaint_draft import flow
from complaint_draft.draft import validate_draft


class FakeAgent:
    """Agent 와 같은 메서드 이름·모양. 네트워크·DB 없음."""
    mock_engine = True

    def __init__(self, questions=None, status="ready", claims=None):
        self.questions = questions or []
        self.status = status
        self.claims = claims or [{"id": "c1", "text": "버스가 줄었다"}]
        self.calls = []

    def understand(self, text):
        self.calls.append(("understand", text))
        return {"status": self.status, "questions": copy.deepcopy(self.questions), "schema_errors": [],
                "pii_masked": [], "notices": [], "structured_by": "rules", "source_text": text,
                "query": {"request_id": "t-1", "normalized_text": text, "complaint_type": "교통",
                          "claims": copy.deepcopy(self.claims), "location": {"emd_name": "구좌읍", "emd_code": ""}}}

    def answer(self, r, **kw):
        self.calls.append(("answer", kw))
        r = copy.deepcopy(r)
        field = {"emd_name": "emd_name", "confirm_emd": "emd_name", "user_request": "user_request",
                 "detail": "claims", "accept_sentence": "dialect", "corrected_sentence": "dialect"}[next(iter(kw))]
        if kw.get("emd_name") == "":
            return r                                        # 빈 답: 질문이 그대로 남는다
        if kw.get("user_request"):
            r["query"]["user_request"] = kw["user_request"]
        r["questions"] = [q for q in r["questions"] if q["field"] != field]
        r["status"] = "ready" if not [q for q in r["questions"] if not q.get("optional")] else "need_input"
        return r

    def meaning_options(self, query):
        return {"request_id": "t-1", "claims": [
            {"claim_id": c["id"], "aspect": None, "recommended": f"{c['id']}-o1", "options": [
                {"option_id": f"{c['id']}-o1", "text": "이용 인원이 줄었다", "kind": "indicator", "indicator_id": "ind_a", "measures": None, "note": None},
                {"option_id": f"{c['id']}-o2", "text": "운행 횟수가 줄었다", "kind": "no_data", "indicator_id": "gap:x", "measures": None, "note": "자료 없음"},
                {"option_id": f"{c['id']}-o3", "text": "기타 (직접 쓰기)", "kind": "other", "indicator_id": None, "measures": None, "note": None},
            ]} for c in query["claims"]]}

    @staticmethod
    def choose(query, interp, picks):
        q = copy.deepcopy(query)
        again = []
        for c in q["claims"]:
            p = picks.get(c["id"])
            if isinstance(p, dict):
                c["text"] = p["other"]
                c.pop("indicator_id", None)
                again.append(c["id"])
            elif p:
                opt = next(o for ic in interp["claims"] if ic["claim_id"] == c["id"] for o in ic["options"] if o["option_id"] == p)
                c["text"], c["indicator_id"] = opt["text"], opt["indicator_id"]
        return q, again

    @staticmethod
    def summary(query):
        return f"[{query['complaint_type']} · {query['location']['emd_name']}] " + " / ".join(c["text"] for c in query["claims"]) + ". 이렇게 이해했어요. 맞나요?"

    def evidence(self, query):
        return {"request_id": query["request_id"], "engine": "mock", "gaps": [],
                "meta": {"use_ontology": False, "snapshot": None, "elapsed_ms": 0},
                "evidence": [{"claim_id": c["id"], "verdict": "supported", "indicator": "(MOCK) 지표", "value": -0.1, "unit": "비율",
                              "region_compare": None, "sources": [], "found_by": "catalog", "relation_path": None,
                              "chart": None, "caveats": []} for c in query["claims"]]}

    def handoff(self, result, query, package):
        return {"query": query, "package": package, "structured_by": result["structured_by"],
                "notices": result["notices"], "pii_masked": result["pii_masked"]}


Q_EMD = {"field": "emd_name", "question": "어느 읍·면·동인가요?"}
Q_REQ = {"field": "user_request", "question": "어떻게 바뀌었으면 하시나요?", "optional": True}


def test_empty_text_stays_on_input():
    assert flow.start(FakeAgent(), "   ")["stage"] == "input"


def test_no_questions_goes_straight_to_meanings():
    s = flow.start(FakeAgent(), "구좌읍 버스가 줄었어요")
    assert s["stage"] == "meanings" and s["pending"] == ["c1"]


def test_questions_one_at_a_time_then_meanings():
    a = FakeAgent(questions=[Q_EMD, Q_REQ], status="need_input")
    s = flow.start(a, "버스가 줄었어요")
    assert s["stage"] == "questions" and flow.question_view(s)["kind"] == "emd"
    s = flow.answer_question(a, s, text="구좌읍")
    assert s["stage"] == "questions" and flow.question_view(s)["kind"] == "request"
    s = flow.answer_question(a, s, text="버스 증차")
    assert s["stage"] == "meanings" and s["query"]["user_request"] == "버스 증차"


def test_answer_does_not_mutate_given_state():
    a = FakeAgent(questions=[Q_EMD], status="need_input")
    s = flow.start(a, "버스가 줄었어요")
    before = copy.deepcopy(s)
    flow.answer_question(a, s, text="구좌읍")
    assert s == before


def test_unanswered_required_question_stops_after_rounds():
    a = FakeAgent(questions=[Q_EMD], status="need_input")
    s = flow.start(a, "버스가 줄었어요")
    for _ in range(flow.MAX_QUESTION_ROUNDS):
        assert s["stage"] == "questions"
        s = flow.answer_question(a, s, text="")
    assert s["stage"] == "stopped" and "모자라" in s["stop_reason"]


def test_suggested_request_yes_uses_suggestion():
    q = {"field": "user_request", "question": "'버스 증차'을(를) 요청하시는 게 맞나요?", "optional": True, "suggestion": "버스 증차"}
    a = FakeAgent(questions=[q])
    s = flow.start(a, "구좌읍 버스가 줄었어요")
    assert flow.question_view(s)["kind"] == "request_suggest"
    s = flow.answer_question(a, s, yes=True)
    assert s["query"]["user_request"] == "버스 증차"


def test_complaint_type_notice_is_message_not_question():
    q = {"field": "complaint_type", "question": "현재 근거 확인은 교통만 가능해요. 근거 없이 민원서만 작성할까요?", "optional": True}
    s = flow.start(FakeAgent(questions=[q]), "구좌읍 소음이 심해요")
    assert s["stage"] == "meanings" and any("민원서만 작성해요" in m for m in s["messages"])


def test_pick_recommended_then_confirm_then_draft():
    a = FakeAgent()
    s = flow.start(a, "구좌읍 버스가 확 줄어부난")
    s = flow.submit_meanings(a, s, {})                       # 안 고르면 추천
    assert s["stage"] == "confirm" and s["query"]["claims"][0]["indicator_id"] == "ind_a"
    assert "구좌읍" in s["summary"]
    s = flow.confirm(a, s, True)
    assert s["stage"] == "draft" and validate_draft(s["draft"]) == []
    content = next(x for x in s["draft"]["sections"] if x["id"] == "content")
    assert content["dialect"] == "구좌읍 버스가 확 줄어부난" and content["dialect_source"] == "original"
    assert s["draft"]["copy_allowed"] is False               # mock 엔진


def test_other_is_reinterpreted_and_limited():
    a = FakeAgent()
    s = flow.start(a, "구좌읍 버스가 줄었어요")
    for i in range(flow.MAX_OTHER_ROUNDS):
        assert any(o["kind"] == "other" for o in flow.meaning_view(s)[0]["options"])
        s = flow.submit_meanings(a, s, {"c1": {"other": f"직접 쓴 문장 {i}"}})
        assert s["stage"] == "meanings"
    assert not any(o["kind"] == "other" for o in flow.meaning_view(s)[0]["options"])   # 더는 기타를 받지 않는다
    s = flow.submit_meanings(a, s, {"c1": {"other": "또 쓴 문장"}})
    assert s["stage"] == "confirm" and s["query"]["claims"][0]["text"] == "직접 쓴 문장 1"


def test_empty_other_keeps_sentence_and_moves_on():
    a = FakeAgent()
    s = flow.start(a, "구좌읍 버스가 줄었어요")
    s = flow.submit_meanings(a, s, {"c1": {"other": "  "}})
    assert s["stage"] == "confirm" and s["query"]["claims"][0]["text"] == "버스가 줄었다"


def test_confirm_no_goes_back_with_original_claims_then_stops():
    a = FakeAgent()
    s = flow.start(a, "구좌읍 버스가 줄었어요")
    s = flow.submit_meanings(a, s, {"c1": "c1-o2"})
    s = flow.confirm(a, s, False)
    assert s["stage"] == "meanings" and s["query"]["claims"] == [{"id": "c1", "text": "버스가 줄었다"}]
    s = flow.submit_meanings(a, s, {})
    s = flow.confirm(a, s, False)
    assert s["stage"] == "stopped"


def test_detail_answer_is_kept_as_said():
    a = FakeAgent(questions=[{"field": "claims", "question": "어떤 점이 불편하신지?"}], status="need_input")
    s = flow.start(a, "구좌읍이우다")
    s = flow.answer_question(a, s, text="버스가 안 와마씸")
    assert s["said"] == ["구좌읍이우다", "버스가 안 와마씸"]


def test_real_agent_rules_and_mock_engine_end_to_end(monkeypatch):
    """실제 Agent: 유료 호출 없이(규칙 구조화) mock 엔진으로 초안까지."""
    pytest.importorskip("yaml")
    pytest.importorskip("duckdb")
    monkeypatch.delenv("ALLOW_PAID_CALLS", raising=False)
    from complaint_agent.pipeline import Agent
    agent = Agent(mock_engine=True)
    try:
        s = flow.start(agent, "우리 동네 버스가 확 줄어부난 할망덜 병원 가기가 막 힘들어마씸 구좌읍")
        guard = 0
        while s["stage"] == "questions" and guard < 6:
            s = flow.answer_question(agent, s, text="")
            guard += 1
        assert s["stage"] == "meanings"
        s = flow.submit_meanings(agent, s, {})
        assert s["stage"] == "confirm"
        s = flow.confirm(agent, s, True)
        assert s["stage"] == "draft" and validate_draft(s["draft"]) == []
        assert "raw_text" not in s["handoff"]["query"]
        assert "줄어부난" in next(x for x in s["draft"]["sections"] if x["id"] == "content")["dialect"]
    finally:
        agent.close()
