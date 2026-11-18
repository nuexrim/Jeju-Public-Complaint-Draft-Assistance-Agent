"""화면용 대화 흐름(flow.py): 가짜 Agent 로 단계 전환을 보고, 실제 Agent(규칙·mock 엔진)로 끝까지 한 번 돈다."""
import copy

import pytest

from complaint_draft import flow
from complaint_draft.draft import validate_draft


class FakeAgent:
    """Agent 와 같은 메서드 이름·모양. 네트워크·DB 없음."""
    mock_engine = True

    def __init__(self, questions=None, status="ready", claims=None, options=None, evidence=None, ctype="교통"):
        self.questions = questions or []
        self.status = status
        self.claims = claims or [{"id": "c1", "text": "버스가 줄었다"}]
        self.options = options
        self.evidence_rows = evidence
        self.ctype = ctype
        self.calls = []

    def understand(self, text, topics=None):
        self.calls.append(("understand", text))
        self.topics = topics
        return {"status": self.status, "questions": copy.deepcopy(self.questions), "schema_errors": [],
                "pii_masked": [], "notices": [], "structured_by": "rules", "source_text": text, "places": ["구좌읍"],
                "query": {"request_id": "t-1", "normalized_text": text, "complaint_type": self.ctype,
                          "claims": copy.deepcopy(self.claims), "location": {"emd_name": "구좌읍", "emd_code": ""}}}

    def answer(self, r, **kw):
        self.calls.append(("answer", kw))
        r = copy.deepcopy(r)
        if kw.get("emd_names"):
            r["places"] = list(kw["emd_names"])
            r["query"]["location"]["emd_name"] = kw["emd_names"][0]
            r["questions"] = [q for q in r["questions"] if q["field"] != "emd_name"]
        if kw.get("complaint_type"):
            r["query"]["complaint_type"] = kw["complaint_type"]
        rest = [k for k in kw if k not in ("emd_names", "complaint_type")]
        if rest:
            field = {"emd_name": "emd_name", "confirm_emd": "emd_name", "user_request": "user_request",
                     "detail": "claims", "accept_sentence": "dialect", "corrected_sentence": "dialect"}[rest[0]]
            if kw.get("emd_name") == "":
                return r                                    # 빈 답: 질문이 그대로 남는다
            if kw.get("user_request"):
                r["query"]["user_request"] = kw["user_request"]
            r["questions"] = [q for q in r["questions"] if q["field"] != field]
        r["status"] = "ready" if not [q for q in r["questions"] if not q.get("optional")] else "need_input"
        return r

    def meaning_options(self, query):
        def opts(cid):
            if self.options is not None:
                return copy.deepcopy(self.options)
            return [
                {"option_id": f"{cid}-o1", "text": "이용 인원이 줄었다", "kind": "indicator", "indicator_id": "ind_a", "measures": None, "note": None},
                {"option_id": f"{cid}-o2", "text": "정류장이 멀다", "kind": "indicator", "indicator_id": "ind_b", "measures": None, "note": None},
                {"option_id": f"{cid}-o3", "text": "운행 횟수가 줄었다", "kind": "no_data", "indicator_id": "gap:x", "measures": None, "note": "자료 없음"},
                {"option_id": f"{cid}-o4", "text": "기타 (직접 쓰기)", "kind": "other", "indicator_id": None, "measures": None, "note": None},
            ]
        return {"request_id": "t-1", "claims": [
            {"claim_id": c["id"], "aspect": None, "recommended": f"{c['id']}-o1", "max_picks": 3, "options": opts(c["id"])}
            for c in query["claims"]]}

    @staticmethod
    def choose(query, interp, picks):
        q = copy.deepcopy(query)
        out = []
        for c in q["claims"]:
            p = picks.get(c["id"])
            if not p:
                out.append(c)
                continue
            info = next(ic for ic in interp["claims"] if ic["claim_id"] == c["id"])
            for n, oid in enumerate(p if isinstance(p, list) else [p], 1):
                opt = next(o for o in info["options"] if o["option_id"] == oid)
                out.append({"id": c["id"] if n == 1 else f"{c['id']}-{n}", "text": opt["text"], "indicator_id": opt["indicator_id"]})
        q["claims"] = out
        return q, []

    def summary(self, query, places=None):
        self.calls.append(("summary", places))
        place = ", ".join(places) if places else query["location"]["emd_name"]
        return f"[{query['complaint_type']} · {place}] " + " / ".join(c["text"] for c in query["claims"]) + ". 이렇게 이해했어요. 맞나요?"

    def evidence(self, query, places=None):
        self.calls.append(("evidence", places))
        rows = self.evidence_rows
        if rows is None:
            rows = [{"claim_id": c["id"], "verdict": "supported", "indicator": f"(MOCK) 지표 {i}", "value": -0.1, "unit": "비율",
                     "region_compare": None, "sources": [{"dataset": "d", "publisher": "제주데이터허브", "as_of": "2026-09"}],
                     "found_by": "catalog", "relation_path": None, "chart": None, "caveats": []}
                    for i, c in enumerate(query["claims"])]
        return {"request_id": query["request_id"], "engine": "mock", "gaps": [],
                "meta": {"use_ontology": False, "snapshot": None, "elapsed_ms": 0}, "evidence": copy.deepcopy(rows)}

    def handoff(self, result, query, package):
        return {"query": query, "package": package, "places": result.get("places", []),
                "structured_by": result["structured_by"], "notices": result["notices"], "pii_masked": result["pii_masked"]}


Q_EMD = {"field": "emd_name", "question": "어느 읍·면·동인가요?"}
Q_REQ = {"field": "user_request", "question": "어떻게 바뀌었으면 하시나요?", "optional": True}
CHOICES = {"all": "제주 전체", "max_picks": 3, "groups": [{"label": "제주시 읍·면", "names": ["구좌읍", "조천읍"]}]}


def to_confirm(a, text="구좌읍 버스가 줄었어요", **kw):
    s = flow.start(a, text, **kw)
    return flow.submit_meanings(a, s, {}) if s["stage"] == "meanings" else s


# ---------------------------------------------------------------- 1. 불편 쓰기
def test_empty_text_stays_on_input():
    assert flow.start(FakeAgent(), "   ")["stage"] == "input"


def test_no_questions_goes_straight_to_meanings():
    s = flow.start(FakeAgent(), "구좌읍 버스가 줄었어요")
    assert s["stage"] == "meanings" and s["pending"] == ["c1"]


def test_picked_places_go_to_answer_right_after_understand():
    a = FakeAgent(questions=[Q_EMD], status="need_input")
    s = flow.start(a, "버스가 줄었어요", places=["구좌읍", "조천읍", "구좌읍", " "])
    assert a.calls[0][0] == "understand" and a.calls[1] == ("answer", {"emd_names": ["구좌읍", "조천읍"]})
    assert s["stage"] == "meanings" and s["places"] == ["구좌읍", "조천읍"]       # 지역을 골랐으니 되묻지 않는다


def test_jeju_all_wins_over_other_places():
    assert flow.clean_places(["구좌읍", "제주 전체"]) == ["제주 전체"]


def test_topics_go_to_engine():
    """고른 주제는 understand(topics=...) 로 넘기고, 유형은 엔진이 정한다(화면에서 유형을 덮어쓰지 않음)."""
    a = FakeAgent(ctype="기타")
    s = flow.start(a, "경로당이 멀어요", topics=["경로당"])
    assert a.topics == ["경로당"] and s["topics"] == ["경로당"]
    assert not any(c[0] == "answer" and "complaint_type" in c[1] for c in a.calls)
    b = FakeAgent()
    flow.start(b, "병원이 멀어요")
    assert b.topics is None                                       # 안 고르면 예전과 같다


# ---------------------------------------------------------------- 2. 되묻기
def test_questions_one_at_a_time_then_meanings():
    a = FakeAgent(questions=[Q_EMD, Q_REQ], status="need_input")
    s = flow.start(a, "버스가 줄었어요")
    assert s["stage"] == "questions" and flow.question_view(s)["kind"] == "emd"
    s = flow.answer_question(a, s, text="구좌읍")
    assert s["stage"] == "questions" and flow.question_view(s)["kind"] == "request"
    s = flow.answer_question(a, s, text="버스 증차")
    assert s["stage"] == "meanings" and s["query"]["user_request"] == "버스 증차"


def test_emd_question_with_choices_takes_picked_places():
    a = FakeAgent(questions=[{**Q_EMD, "choices": CHOICES}], status="need_input")
    s = flow.start(a, "버스가 줄었어요")
    v = flow.question_view(s)
    assert v["kind"] == "emd" and v["choices"] == CHOICES
    s = flow.answer_question(a, s, places=["구좌읍", "조천읍"])
    assert a.calls[-1] == ("answer", {"emd_names": ["구좌읍", "조천읍"]})
    assert s["stage"] == "meanings" and s["places"] == ["구좌읍", "조천읍"]


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


def test_detail_answer_is_kept_as_said():
    a = FakeAgent(questions=[{"field": "claims", "question": "어떤 점이 불편하신지?"}], status="need_input")
    s = flow.start(a, "구좌읍이우다")
    s = flow.answer_question(a, s, text="버스가 안 와마씸")
    assert s["said"] == ["구좌읍이우다", "버스가 안 와마씸"]


# ---------------------------------------------------------------- 3. 뜻 고르기
def test_meaning_view_caps_options_and_keeps_one_other():
    many = [{"option_id": f"c1-o{i}", "text": f"뜻 {i}", "kind": "indicator", "indicator_id": f"i{i}", "measures": None, "note": None}
            for i in range(1, 8)] + [{"option_id": "c1-o9", "text": "기타", "kind": "other", "indicator_id": None, "measures": None, "note": None}]
    s = flow.start(FakeAgent(options=many), "구좌읍 버스가 줄었어요")
    v = flow.meaning_view(s)[0]
    assert len(v["options"]) == flow.MAX_MEANING_OPTIONS + 1 and v["options"][-1]["kind"] == "other"
    assert v["other_id"] == "c1-o9" and v["max_picks"] == 3


def test_several_meanings_become_several_claims():
    a = FakeAgent()
    s = flow.start(a, "구좌읍 버스가 줄었어요")
    s = flow.submit_meanings(a, s, {"c1": ["c1-o1", "c1-o2"]})
    assert s["stage"] == "confirm"
    assert [c["text"] for c in s["query"]["claims"]] == ["이용 인원이 줄었다", "정류장이 멀다"]


def test_other_only_keeps_original_claim_without_asking_again():
    a = FakeAgent()
    s = flow.start(a, "구좌읍 버스가 줄었어요")
    s = flow.submit_meanings(a, s, {"c1": ["c1-o4"]})
    assert s["stage"] == "confirm" and s["query"]["claims"] == [{"id": "c1", "text": "버스가 줄었다"}]
    s = flow.submit_meanings(a, flow.start(a, "구좌읍 버스가 줄었어요"), {"c1": ["c1-o4", "c1-o2"]})
    assert [c["text"] for c in s["query"]["claims"]] == ["정류장이 멀다"]        # 기타와 함께 고르면 기타는 뺀다


def test_missing_pick_uses_recommended():
    a = FakeAgent()
    s = to_confirm(a)
    assert s["query"]["claims"][0]["indicator_id"] == "ind_a"


def test_claim_without_data_is_not_asked():
    no_data = [{"option_id": "c1-o1", "text": "자료 없는 뜻", "kind": "no_data", "indicator_id": "gap:y", "measures": None, "note": None},
               {"option_id": "c1-o2", "text": "기타", "kind": "other", "indicator_id": None, "measures": None, "note": None}]
    s = flow.start(FakeAgent(options=no_data), "구좌읍 가로등이 어두워요")
    assert s["stage"] == "confirm" and s["query"]["claims"] == [{"id": "c1", "text": "버스가 줄었다"}]


# ---------------------------------------------------------------- 4. 최종 확인
def test_summary_and_evidence_get_places():
    a = FakeAgent()
    s = to_confirm(a, places=["구좌읍", "조천읍"], topics=["버스·대중교통"])
    assert ("summary", ["구좌읍", "조천읍"]) in a.calls and "구좌읍, 조천읍" in s["summary"]
    rows = dict(flow.confirm_rows(s))
    assert rows["지역"] == "구좌읍, 조천읍" and rows["주제"] == "버스·대중교통"
    flow.confirm(a, s, True)
    assert ("evidence", ["구좌읍", "조천읍"]) in a.calls


def test_confirm_no_goes_back_to_questions_keeping_places_then_stops():
    a = FakeAgent()
    s = to_confirm(a, places=["조천읍"])
    s = flow.confirm(a, s, False)
    assert s["stage"] == "questions" and flow.question_view(s)["kind"] == "dialect"
    assert s["places"] == ["조천읍"] and [c[0] for c in a.calls].count("understand") == 2
    s = flow.answer_question(a, s)                          # 이해한 문장이 맞다
    s = flow.submit_meanings(a, s, {})
    s = flow.confirm(a, s, False)
    assert s["stage"] == "stopped"


# ---------------------------------------------------------------- 5. 근거 고르기 → 6. 초안
EV = [
    {"claim_id": "c1", "verdict": "supported", "indicator": "지표 가", "value": 1.5, "unit": "%", "region_compare": "평균보다 높음",
     "sources": [{"dataset": "d1", "publisher": "제주데이터허브", "as_of": "2026-09"}], "found_by": "catalog", "relation_path": None, "chart": None, "caveats": []},
    {"claim_id": "c1", "verdict": "partial", "indicator": "지표 나", "value": 3, "unit": "곳", "region_compare": None,
     "sources": [{"dataset": "d2", "publisher": "제주데이터허브", "as_of": "2021-12"}], "found_by": "catalog", "relation_path": None, "chart": None, "caveats": []},
    {"claim_id": "c1", "verdict": "contradicted", "indicator": "지표 다", "value": 9, "unit": None, "region_compare": None,
     "sources": [], "found_by": "catalog", "relation_path": None, "chart": None, "caveats": []},
]


def test_confirm_yes_shows_evidence_cards_with_usable_ones_picked():
    a = FakeAgent(evidence=EV)
    s = flow.confirm(a, to_confirm(a), True)
    assert s["stage"] == "evidence"
    assert [(it["index"], it["usable"]) for it in s["evidence_items"]] == [(0, True), (1, True), (2, False)]
    assert s["picked"] == [0, 1] and s["evidence_items"][1]["stale"] is True


def test_choose_evidence_puts_only_picked_into_draft():
    a = FakeAgent(evidence=EV)
    s = flow.confirm(a, to_confirm(a), True)
    s = flow.choose_evidence(a, s, [1, 2])                    # 2 는 넣을 수 없는 근거라 무시된다
    assert s["stage"] == "draft" and validate_draft(s["draft"]) == []
    assert [e["indicator"] for e in s["draft"]["evidence"]] == ["지표 나"]
    kinds = {(x["kind"], x["label"]) for x in s["draft"]["excluded"]}
    assert ("user", "지표 가") in kinds and ("evidence", "지표 다") in kinds
    s = flow.back_to_evidence(s)
    assert s["stage"] == "evidence" and s["picked"] == [1]


def test_no_evidence_skips_choosing():
    a = FakeAgent(evidence=[])
    s = flow.confirm(a, to_confirm(a), True)
    assert s["stage"] == "draft" and s["draft"]["evidence"] == []


def test_pick_recommended_then_confirm_then_draft():
    a = FakeAgent()
    s = to_confirm(a, text="구좌읍 버스가 확 줄어부난")
    s = flow.choose_evidence(a, flow.confirm(a, s, True), [0])
    assert s["stage"] == "draft" and validate_draft(s["draft"]) == []
    content = next(x for x in s["draft"]["sections"] if x["id"] == "content")
    assert content["dialect"] == "구좌읍 버스가 확 줄어부난" and content["dialect_source"] == "original"
    assert s["draft"]["copy_allowed"] is False               # mock 엔진


def test_real_agent_rules_and_mock_engine_end_to_end(monkeypatch):
    """실제 Agent: 유료 호출 없이(규칙 구조화) mock 엔진으로 초안까지. 지역은 목록에서 고른 것으로."""
    pytest.importorskip("yaml")
    pytest.importorskip("duckdb")
    monkeypatch.delenv("ALLOW_PAID_CALLS", raising=False)
    from complaint_agent.pipeline import Agent
    agent = Agent(mock_engine=True)
    try:
        choices = agent.place_choices()
        assert choices["all"] == flow.EMD_ALL
        s = flow.start(agent, "우리 동네 버스가 확 줄어부난 할망덜 병원 가기가 막 힘들어마씸", places=["구좌읍", "성산읍"])
        assert s["places"] == ["구좌읍", "성산읍"]
        guard = 0
        while s["stage"] == "questions" and guard < 6:
            s = flow.answer_question(agent, s, text="")
            guard += 1
        if s["stage"] == "meanings":
            s = flow.submit_meanings(agent, s, {})
        assert s["stage"] == "confirm" and "구좌읍" in s["summary"]
        s = flow.confirm(agent, s, True)
        if s["stage"] == "evidence":
            s = flow.choose_evidence(agent, s, s["picked"])
        assert s["stage"] == "draft" and validate_draft(s["draft"]) == []
        assert "raw_text" not in s["handoff"]["query"] and s["handoff"]["places"] == ["구좌읍", "성산읍"]
        assert "줄어부난" in next(x for x in s["draft"]["sections"] if x["id"] == "content")["dialect"]
    finally:
        agent.close()


# ---------------------------------------------------------------- 되묻기에 직접 쓴 글의 표준어 변환
def test_typed_request_with_dialect_is_standardized(monkeypatch):
    import sys, types
    sent = []
    fake = types.ModuleType("normalize")
    fake.ResourceMissing = FileNotFoundError
    fake.normalize = lambda text, use_rag=True: (sent.append(text), {"standard": "의자를 놓아 주세요"})[1]
    monkeypatch.setitem(sys.modules, "normalize", fake)
    monkeypatch.setattr(flow, "leftover_dialect", lambda t: ["놔줍서"] if "놔줍서" in t else [])
    a = FakeAgent(questions=[Q_REQ])
    s = flow.start(a, "구좌읍 정류장에 의자가 없어요")
    s = flow.answer_question(a, s, text="의자 좀 놔줍서 010-1234-5678")
    assert s["query"]["user_request"] == "의자를 놓아 주세요"
    assert "010-1234-5678" not in sent[0]                      # 보내기 전에 가린다


def test_standard_text_is_not_sent_to_normalizer(monkeypatch):
    import sys, types
    fake = types.ModuleType("normalize")
    fake.ResourceMissing = FileNotFoundError
    fake.normalize = lambda *a, **k: (_ for _ in ()).throw(AssertionError("호출하면 안 됨"))
    monkeypatch.setitem(sys.modules, "normalize", fake)
    monkeypatch.setattr(flow, "leftover_dialect", lambda t: [])
    assert flow.standardize("버스를 늘려 주세요") == "버스를 늘려 주세요"


def test_failed_conversion_keeps_text(monkeypatch):
    import sys, types
    fake = types.ModuleType("normalize")
    fake.ResourceMissing = FileNotFoundError
    fake.normalize = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("키 없음"))
    monkeypatch.setitem(sys.modules, "normalize", fake)
    monkeypatch.setattr(flow, "leftover_dialect", lambda t: ["놔줍서"])
    assert flow.standardize("의자 놔줍서") == "의자 놔줍서"
