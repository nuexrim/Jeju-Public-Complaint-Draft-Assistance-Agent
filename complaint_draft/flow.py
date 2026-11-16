"""대화 흐름(화면용): 말하기 → 되묻기 → 뜻 고르기 → 최종 확인 → 민원서 초안.

complaint_agent/conversation.py 의 터미널 흐름과 같은 순서·같은 규칙을, 화면이 한 단계씩 부를 수 있게 나눈 것이다.
Agent(complaint_agent.pipeline)의 메서드만 부르고 그쪽 코드는 고치지 않는다. 화면(Streamlit)과 무관해서 테스트하기 쉽다.

    state = start(agent, "우리 동네 버스가 확 줄어부난 ...")
    state["stage"]  →  "questions" | "meanings" | "confirm" | "draft" | "stopped"

    questions : question_view(state) 를 보여 주고 answer_question(agent, state, ...) 로 답을 넣는다
    meanings  : meaning_view(state) 를 보여 주고 submit_meanings(agent, state, picks) 로 고른 뜻을 넣는다
    confirm   : state["summary"] 를 보여 주고 confirm(agent, state, yes) 로 답한다
    draft     : state["draft"](build_draft 결과), state["handoff"]
    stopped   : state["stop_reason"]

state 는 dict 하나라 st.session_state 에 그대로 넣을 수 있다. 각 함수는 새 state 를 돌려준다(받은 것을 바꾸지 않음).
"""
from __future__ import annotations

import copy

from complaint_draft.draft import build_draft, friendly

MAX_QUESTION_ROUNDS = 4       # conversation.py 와 같음: 같은 빈 칸을 끝없이 묻지 않는다
MAX_OTHER_ROUNDS = 2          # pipeline.py 와 같음: '기타(직접 쓰기)'를 다시 해석하는 최대 횟수
MAX_CONFIRM_TRIES = 2         # 최종 확인에서 '아니요'를 받을 수 있는 횟수

STAGES = ["input", "questions", "meanings", "confirm", "draft"]
STAGE_LABELS = {"input": "말하기", "questions": "되묻기", "meanings": "뜻 고르기", "confirm": "최종 확인", "draft": "민원서"}


def new_state() -> dict:
    return {"stage": "input", "said": [], "result": None, "query": None, "interp": None, "pending": [],
            "q_rounds": 0, "other_rounds": 0, "confirm_tries": 0, "summary": "", "messages": [],
            "stop_reason": "", "handoff": None, "draft": None}


# ------------------------------------------------------------------ 1. 말하기
def start(agent, text: str) -> dict:
    text = " ".join((text or "").split())
    s = new_state()
    if not text:
        return s
    s["said"] = [text]
    r = agent.understand(text)
    s["result"] = r
    s["messages"] = [f"알림: {friendly(n)}" for n in r.get("notices", [])]
    if r.get("pii_masked"):
        s["messages"].append("개인정보를 가렸어요: " + ", ".join(r["pii_masked"]))
    return _after_questions(agent, s)


# ------------------------------------------------------------------ 2. 되묻기
def _after_questions(agent, s: dict) -> dict:
    r = s["result"]
    while r["questions"] and s["q_rounds"] < MAX_QUESTION_ROUNDS:
        q = r["questions"][0]
        if q["field"] == "complaint_type":           # 알리기만 하고 넘어간다
            s["messages"].append(q["question"].replace(" 근거 없이 민원서만 작성할까요?", " 근거 없이 민원서만 작성해요."))
            r["questions"] = [x for x in r["questions"] if x["field"] != "complaint_type"]
        elif q["field"] not in ("dialect", "emd_name", "claims", "user_request"):
            r["questions"] = r["questions"][1:]      # 모르는 질문 종류는 건너뛴다
        else:
            s["stage"] = "questions"
            return s
    if r["status"] != "ready":
        need = r.get("schema_errors") or [q["question"] for q in r["questions"] if not q.get("optional")]
        return _stop(s, "필요한 정보가 모자라 근거를 찾지 못했어요. 처음부터 조금 더 자세히 말씀해 주세요."
                        + (f" ({'; '.join(need)})" if need else ""))
    s["query"] = copy.deepcopy(r["query"])
    return _enter_meanings(agent, s)


def question_view(s: dict) -> dict:
    """지금 물을 질문 하나. kind: dialect | confirm_emd | emd | detail | request_suggest | request"""
    r = s["result"]
    q = r["questions"][0]
    field = q["field"]
    if field == "dialect":
        return {"kind": "dialect", "question": "이렇게 이해했어요. 맞나요?",
                "understood": r["query"]["normalized_text"],
                "asks": [x["question"] for x in r["questions"] if x["field"] == "dialect"]}
    if field == "emd_name":
        return {"kind": "confirm_emd" if q.get("confirm") else "emd", "question": q["question"]}
    if field == "claims":
        return {"kind": "detail", "question": q["question"]}
    if q.get("suggestion"):
        return {"kind": "request_suggest", "question": q["question"], "suggestion": q["suggestion"]}
    return {"kind": "request", "question": q["question"]}


def answer_question(agent, state: dict, *, text: str = "", yes: bool | None = None) -> dict:
    """질문 하나에 답한다. text: 자유 입력(빈 문자열이면 '없음/맞음'), yes: 네/아니요 질문의 답."""
    s = copy.deepcopy(state)
    r = s["result"]
    view = question_view(s)
    kind = view["kind"]
    text = (text or "").strip()
    before = len(r.get("notices", []))
    if kind == "dialect":
        r = agent.answer(r, corrected_sentence=text) if text else agent.answer(r, accept_sentence=True)
    elif kind == "confirm_emd":
        r = agent.answer(r, confirm_emd=bool(yes))
    elif kind == "emd":
        r = agent.answer(r, emd_name=text)
    elif kind == "detail":
        if text:
            s["said"].append(text)                   # 덧붙인 말도 '하신 말씀'이다
        r = agent.answer(r, detail=text)
    elif kind == "request_suggest":
        r = agent.answer(r, user_request=view["suggestion"] if yes else text)
    else:
        r = agent.answer(r, user_request=text)
    for n in r.get("notices", [])[before:]:
        s["messages"].append(n if "찾지 못했어요" in n else f"알림: {friendly(n)}")
    s["result"] = r
    s["q_rounds"] += 1
    return _after_questions(agent, s)


# ------------------------------------------------------------------ 3. 뜻 고르기
def _enter_meanings(agent, s: dict) -> dict:
    s["pending"] = [c["id"] for c in s["query"]["claims"]]
    s["other_rounds"] = 0
    s["interp"] = agent.meaning_options(s["query"])
    s["stage"] = "meanings"
    return s


def meaning_view(s: dict) -> list[dict]:
    """아직 뜻을 고르지 않은 주장마다: {claim_id, text, options[], recommended}.
    '기타'를 더 받을 수 없으면(횟수 초과) 기타 선택지를 뺀다."""
    allow_other = s["other_rounds"] < MAX_OTHER_ROUNDS
    text = {c["id"]: c["text"] for c in s["query"]["claims"]}
    out = []
    for c in s["interp"]["claims"]:
        if c["claim_id"] not in s["pending"]:
            continue
        options = [o for o in c["options"] if allow_other or o["kind"] != "other"]
        rec = c["recommended"] if any(o["option_id"] == c["recommended"] for o in options) else \
            (options[0]["option_id"] if options else None)
        out.append({"claim_id": c["claim_id"], "text": text.get(c["claim_id"], ""), "options": options, "recommended": rec})
    return out


def submit_meanings(agent, state: dict, picks: dict) -> dict:
    """picks: {claim_id: option_id} 또는 {claim_id: {"other": "직접 쓴 문장"}}. 안 고른 주장은 추천으로 간다."""
    s = copy.deepcopy(state)
    final = {}
    for v in meaning_view(s):
        pick = picks.get(v["claim_id"], v["recommended"])
        if isinstance(pick, dict):
            if (pick.get("other") or "").strip() and s["other_rounds"] < MAX_OTHER_ROUNDS:
                final[v["claim_id"]] = {"other": pick["other"]}
            continue                                   # 빈 문장이나 횟수 초과면 지금 문장 그대로 둔다
        opt = next((o for o in v["options"] if o["option_id"] == pick), None)
        if opt is None or opt["kind"] == "other":
            continue
        final[v["claim_id"]] = pick
    s["query"], s["pending"] = agent.choose(s["query"], s["interp"], final)
    s["other_rounds"] += 1
    if s["pending"] and s["other_rounds"] <= MAX_OTHER_ROUNDS:
        s["interp"] = agent.meaning_options(s["query"])
        s["stage"] = "meanings"
        return s
    s["pending"] = []
    s["summary"] = agent.summary(s["query"])
    s["stage"] = "confirm"
    return s


# ------------------------------------------------------------------ 4. 최종 확인 → 5. 초안
def confirm(agent, state: dict, yes: bool) -> dict:
    s = copy.deepcopy(state)
    if not yes:
        s["confirm_tries"] += 1
        if s["confirm_tries"] >= MAX_CONFIRM_TRIES:
            return _stop(s, "확인을 받지 못해 멈출게요. 처음부터 다시 말씀해 주세요.")
        original = s["result"]["query"]["claims"]
        s["query"] = {**s["query"], "claims": [{"id": c["id"], "text": c["text"]} for c in original]}
        return _enter_meanings(agent, s)
    package = agent.evidence(s["query"])
    handoff = agent.handoff(s["result"], s["query"], package)
    s["handoff"] = handoff
    s["draft"] = build_draft(handoff, source_text=" ".join(s["said"]))   # 원문은 build_draft 가 한 번 더 가린다
    s["stage"] = "draft"
    return s


def _stop(s: dict, reason: str) -> dict:
    s["stage"] = "stopped"
    s["stop_reason"] = reason
    return s
