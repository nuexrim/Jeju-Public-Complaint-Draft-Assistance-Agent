"""대화 흐름(화면용): 불편 쓰기 → 되묻기 → 뜻 고르기 → 최종 확인 → 근거 고르기 → 민원서 초안.

complaint_agent/conversation.py 의 터미널 흐름과 같은 순서·같은 규칙을, 화면이 한 단계씩 부를 수 있게 나눈 것이다.
Agent(complaint_agent.pipeline)의 메서드만 부르고 그쪽 코드는 고치지 않는다. 화면(Streamlit)과 무관해서 테스트하기 쉽다.

    state = start(agent, "우리 동네 버스가 확 줄어부난 ...", places=["구좌읍"], topics=["버스·대중교통"])
    state["stage"]  →  "questions" | "meanings" | "confirm" | "evidence" | "draft" | "stopped"

    questions : question_view(state) 를 보여 주고 answer_question(agent, state, text=/yes=/places=) 로 답을 넣는다
    meanings  : meaning_view(state) 를 보여 주고 submit_meanings(agent, state, {"c1": ["c1-o1", "c1-o3"]}) 로 고른 뜻을 넣는다
    confirm   : confirm_rows(state) 를 보여 주고 confirm(agent, state, yes) 로 답한다 (아니요 → 되묻기부터 다시)
    evidence  : state["evidence_items"] 를 카드로 보여 주고 choose_evidence(agent, state, [고른 index]) 로 넘긴다
    draft     : state["draft"](build_draft 결과), state["handoff"]
    stopped   : state["stop_reason"]

불편 쓰기에서 고른 읍면동은 understand() 바로 다음에 answer(r, emd_names=[...]) 로 넘긴다(고르지 않았으면
엔진이 되묻기에 읍면동 고르기 목록(choices)을 붙여 보낸다). 고른 장소는 r["places"] 로 summary·evidence 에 넘긴다.

state 는 dict 하나라 st.session_state 에 그대로 넣을 수 있다. 각 함수는 새 state 를 돌려준다(받은 것을 바꾸지 않음).
"""
from __future__ import annotations

import copy

from complaint_draft.draft import build_draft, evidence_choices, friendly

MAX_QUESTION_ROUNDS = 4       # conversation.py 와 같음: 같은 빈 칸을 끝없이 묻지 않는다
MAX_CONFIRM_TRIES = 2         # 최종 확인에서 '아니요'를 받을 수 있는 횟수
MAX_MEANING_OPTIONS = 5       # 주장마다 보여 줄 뜻 선택지(기타 빼고). 기타는 따로 하나 붙인다
EMD_ALL = "제주 전체"         # complaint_agent.pipeline.EMD_ALL 과 같은 값

STAGES = ["input", "questions", "meanings", "confirm", "evidence", "draft"]
STAGE_LABELS = {"input": "불편 쓰기", "questions": "되묻기", "meanings": "뜻 고르기", "confirm": "최종 확인",
                "evidence": "근거 고르기", "draft": "민원서"}


def new_state() -> dict:
    return {"stage": "input", "said": [], "places": [], "topics": [], "type_hint": "",
            "result": None, "query": None, "interp": None, "pending": [],
            "q_rounds": 0, "confirm_tries": 0, "summary": "", "messages": [], "stop_reason": "",
            "package": None, "evidence_items": [], "picked": [], "handoff": None, "draft": None}


def clean_places(places) -> list[str]:
    """고른 장소 정리: 빈 값·중복을 빼고, '제주 전체'가 있으면 그것 하나만 둔다."""
    names = list(dict.fromkeys(p.strip() for p in (places or []) if p and p.strip()))
    return [EMD_ALL] if EMD_ALL in names else names


def _note(s: dict, notices: list[str]) -> None:
    for n in notices:
        s["messages"].append(n if "찾지 못했어요" in n else f"알림: {friendly(n)}")


def _sync_places(s: dict) -> None:
    if "places" in (s["result"] or {}):
        s["places"] = list(s["result"]["places"] or [])


# ------------------------------------------------------------------ 1. 불편 쓰기
def start(agent, text: str, places=None, topics=None, type_hint: str = "") -> dict:
    """places: 고른 읍면동(없으면 엔진이 되묻는다). topics: 고른 주제 이름(structure.topic_names()).
    type_hint: 고른 주제로 정해지는 민원 유형. 엔진이 유형을 '기타'로 읽었을 때만 넘긴다."""
    text = " ".join((text or "").split())
    s = new_state()
    s["places"] = clean_places(places)
    s["topics"] = list(dict.fromkeys(t for t in (topics or []) if t))
    s["type_hint"] = type_hint or ""
    if not text:
        return s
    s["said"] = [text]
    return _understand(agent, s, text)


def _understand(agent, s: dict, text: str) -> dict:
    r = agent.understand(text)
    s["messages"] = []
    _note(s, r.get("notices", []))
    if r.get("pii_masked"):
        s["messages"].append("개인정보를 가렸어요: " + ", ".join(r["pii_masked"]))
    extra = {}
    if s["places"]:
        extra["emd_names"] = s["places"]
    if s["type_hint"] and r["query"].get("complaint_type") in ("", "기타"):
        extra["complaint_type"] = s["type_hint"]
    if extra:
        before = len(r.get("notices", []))
        r = agent.answer(r, **extra)
        _note(s, r.get("notices", [])[before:])
    s["result"] = r
    s["query"] = None
    s["q_rounds"] = 0
    _sync_places(s)
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
        return _stop(s, "필요한 정보가 모자라 근거를 찾지 못했어요. 처음부터 조금 더 자세히 써 주세요."
                        + (f" ({'; '.join(need)})" if need else ""))
    s["query"] = copy.deepcopy(r["query"])
    return _enter_meanings(agent, s)


def question_view(s: dict) -> dict:
    """지금 물을 질문 하나. kind: dialect | confirm_emd | emd | detail | request_suggest | request
    emd 질문에 choices(place_choices 결과)가 있으면 화면은 목록에서 고르게 한다."""
    r = s["result"]
    q = r["questions"][0]
    field = q["field"]
    if field == "dialect":
        return {"kind": "dialect", "question": "이렇게 이해했어요. 맞나요?",
                "understood": r["query"]["normalized_text"],
                "asks": [x["question"] for x in r["questions"] if x["field"] == "dialect" and x.get("question")]}
    if field == "emd_name":
        if q.get("confirm"):
            return {"kind": "confirm_emd", "question": q["question"]}
        return {"kind": "emd", "question": q["question"], "choices": q.get("choices")}
    if field == "claims":
        return {"kind": "detail", "question": q["question"]}
    if q.get("suggestion"):
        return {"kind": "request_suggest", "question": q["question"], "suggestion": q["suggestion"]}
    return {"kind": "request", "question": q["question"]}


def answer_question(agent, state: dict, *, text: str = "", yes: bool | None = None, places=None) -> dict:
    """질문 하나에 답한다. text: 자유 입력(빈 문자열이면 '없음/맞음'), yes: 네/아니요 질문의 답,
    places: 읍면동 질문에서 목록으로 고른 장소들."""
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
        picked = clean_places(places)
        r = agent.answer(r, emd_names=picked) if picked else agent.answer(r, emd_name=text)
    elif kind == "detail":
        if text:
            s["said"].append(text)                   # 덧붙인 말도 '쓰신 글'이다
        r = agent.answer(r, detail=text)
    elif kind == "request_suggest":
        r = agent.answer(r, user_request=view["suggestion"] if yes else text)
    else:
        r = agent.answer(r, user_request=text)
    _note(s, r.get("notices", [])[before:])
    s["result"] = r
    s["q_rounds"] += 1
    _sync_places(s)
    return _after_questions(agent, s)


# ------------------------------------------------------------------ 3. 뜻 고르기
def _askable(claim: dict) -> bool:
    """물어볼 만한 주장: 자료로 잴 수 있는 뜻(indicator)이 하나라도 있을 때.
    근거 자료가 없는 주제(선택지가 '자료 없음'·'기타'뿐)는 묻지 않고 하신 말씀 그대로 넘어간다."""
    return any(o["kind"] == "indicator" for o in claim["options"])


def _enter_meanings(agent, s: dict) -> dict:
    s["interp"] = agent.meaning_options(s["query"])
    s["pending"] = [c["claim_id"] for c in s["interp"]["claims"] if _askable(c)]
    if not s["pending"]:
        return _enter_confirm(agent, s)
    s["stage"] = "meanings"
    return s


def meaning_view(s: dict) -> list[dict]:
    """물어볼 주장마다: {claim_id, text, options[], recommended, max_picks, other_id}.
    options 는 뜻 최대 MAX_MEANING_OPTIONS 개 + 기타 하나. recommended 는 기타가 아닌 뜻."""
    text = {c["id"]: c["text"] for c in s["query"]["claims"]}
    out = []
    for c in s["interp"]["claims"]:
        if c["claim_id"] not in s["pending"]:
            continue
        meanings = [o for o in c["options"] if o["kind"] != "other"][:MAX_MEANING_OPTIONS]
        other = next((o for o in c["options"] if o["kind"] == "other"), None)
        options = meanings + ([other] if other else [])
        ids = [o["option_id"] for o in meanings]
        rec = c["recommended"] if c["recommended"] in ids else next(
            (o["option_id"] for o in meanings if o["kind"] == "indicator"), None)
        out.append({"claim_id": c["claim_id"], "text": text.get(c["claim_id"], ""), "options": options,
                    "recommended": rec, "max_picks": max(1, int(c.get("max_picks", 1))),
                    "other_id": other["option_id"] if other else None})
    return out


def submit_meanings(agent, state: dict, picks: dict) -> dict:
    """picks: {claim_id: [option_id, ...]} (하나면 문자열도 됨). 받은 키가 없는 주장은 추천 뜻으로 간다.
    기타만 고르거나 빈 목록이면 그 주장은 하신 말씀 그대로 둔다(직접 쓰기 없음)."""
    s = copy.deepcopy(state)
    final = {}
    for v in meaning_view(s):
        raw = picks.get(v["claim_id"], [v["recommended"]] if v["recommended"] else [])
        items = [raw] if isinstance(raw, str) else list(raw or [])
        valid = {o["option_id"]: o for o in v["options"]}
        chosen = [i for i in dict.fromkeys(items) if i in valid and valid[i]["kind"] != "other"]
        if chosen:
            final[v["claim_id"]] = chosen[: v["max_picks"]]
    if final:
        s["query"], _ = agent.choose(s["query"], s["interp"], final)
    s["pending"] = []
    return _enter_confirm(agent, s)


# ------------------------------------------------------------------ 4. 최종 확인
def _enter_confirm(agent, s: dict) -> dict:
    s["summary"] = agent.summary(s["query"], s["places"] or None)
    s["stage"] = "confirm"
    return s


def confirm_rows(s: dict) -> list[tuple[str, str]]:
    """최종 확인 화면의 '이렇게 이해했어요' 줄들. 값은 사용자 글에서 온 것이라 화면에서 가려 넣는다."""
    q = s["query"] or s["result"]["query"]
    place = ", ".join(s["places"]) or q.get("location", {}).get("emd_name") or "고르지 않음"
    claims = "\n".join(c["text"] for c in q.get("claims") or [])
    return [("지역", place), ("주제", ", ".join(s["topics"]) or "고르지 않음"),
            ("민원 종류", q.get("complaint_type") or ""), ("문제", claims), ("바라는 점", q.get("user_request") or "")]


def confirm(agent, state: dict, yes: bool) -> dict:
    """맞아요 → 근거를 찾아 근거 고르기로. 아니요 → 쓰신 글을 다시 이해해 되묻기부터(고른 지역·주제는 그대로)."""
    s = copy.deepcopy(state)
    if not yes:
        s["confirm_tries"] += 1
        if s["confirm_tries"] >= MAX_CONFIRM_TRIES:
            return _stop(s, "확인을 받지 못해 멈출게요. 처음부터 다시 써 주세요.")
        s = _understand(agent, s, " ".join(s["said"]))
        if s["stage"] != "questions" and s["stage"] != "stopped":
            # 엔진이 더 물을 것이 없다고 해도, 틀렸다고 하셨으니 이해한 문장을 한 번 보여 주고 고칠 기회를 드린다
            s["result"]["questions"] = [{"field": "dialect", "question": ""}] + s["result"]["questions"]
            s["stage"] = "questions"
        return s
    package = agent.evidence(s["query"], s["places"] or None)
    s["package"] = package
    s["evidence_items"] = evidence_choices(package, s["query"])
    s["picked"] = [it["index"] for it in s["evidence_items"] if it["usable"]]
    if not s["evidence_items"]:                       # 고를 근거가 없으면 바로 초안
        return _make_draft(agent, s)
    s["stage"] = "evidence"
    return s


# ------------------------------------------------------------------ 5. 근거 고르기 → 6. 초안
def choose_evidence(agent, state: dict, picked) -> dict:
    """picked: 민원서에 넣을 근거의 index. 넣을 수 없는 근거(usable=False)는 골라도 넣지 않는다."""
    s = copy.deepcopy(state)
    usable = {it["index"] for it in s["evidence_items"] if it["usable"]}
    s["picked"] = sorted(set(picked or []) & usable)
    return _make_draft(agent, s)


def _make_draft(agent, s: dict) -> dict:
    usable = {it["index"] for it in s["evidence_items"] if it["usable"]}
    handoff = agent.handoff(s["result"], s["query"], s["package"])
    s["handoff"] = handoff
    s["draft"] = build_draft(handoff, source_text=" ".join(s["said"]),        # 원문은 build_draft 가 한 번 더 가린다
                             exclude=sorted(usable - set(s["picked"])))
    s["stage"] = "draft"
    return s


def back_to_confirm(state: dict) -> dict:
    s = copy.deepcopy(state)
    s["stage"] = "confirm"
    return s


def back_to_evidence(state: dict) -> dict:
    s = copy.deepcopy(state)
    s["stage"] = "evidence" if s["evidence_items"] else "confirm"
    return s


def _stop(s: dict, reason: str) -> dict:
    s["stage"] = "stopped"
    s["stop_reason"] = reason
    return s
