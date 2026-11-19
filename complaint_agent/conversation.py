"""터미널 대화: 민원서 화면과 같은 흐름(complaint_draft/flow.py)을 글로 묻고 답한다.

    python -m complaint_agent "우리 동네 버스가 확 줄어부난 할망덜 병원 가기가 막 힘들어마씸"
    python -m complaint_agent --place 구좌읍 --topic 버스·대중교통 "..."   화면의 지역·주제 고르기와 같다
    python -m complaint_agent --out handoff.json "..."                    민원 초안 모델에 넘길 JSON 저장
    python -m complaint_agent --mock-engine "..."                         근거 DB 없이 형식만

흐름(불편 쓰기 → 되묻기 → 뜻 고르기 → 최종 확인 → 근거 고르기 → 민원서)과 규칙(되묻기 횟수, 뜻 고르기 개수,
'아니요'면 다시 이해, 근거 고르기)은 flow.py 하나에만 있다. 여기는 묻고 보여 주는 일만 한다.
예전에는 터미널 흐름이 따로 있어 '기타' 처리와 '아니요' 동작이 화면과 달랐다(2026-11-18 하나로 합침).

ask(prompt) -> str, say(text) 를 바꿔 끼우면 테스트에서도 같은 흐름을 쓴다.
"""
from __future__ import annotations

from typing import Callable

from complaint_agent.pipeline import Agent
from complaint_draft import flow
from package.labels import VERDICT_LABEL as VERDICT_KO  # noqa: E402  (엔진·화면과 같은 판정 이름)

MAX_QUESTION_ROUNDS = flow.MAX_QUESTION_ROUNDS
MAX_STEPS = 20                # 같은 단계를 끝없이 돌지 않도록 (보통 5~8단계)


def _yes(ans: str) -> bool:
    return ans.strip().lower() in ("", "y", "yes", "네", "예", "응", "맞아요", "맞음")


def parse_picks(ans: str, n_options: int, max_picks: int) -> list[int] | None:
    """'1', '1,3', '1 3' → 0부터 센 번호 목록. 빈 답은 [] (추천), 잘못된 답은 None."""
    parts = [x for x in ans.replace(",", " ").split() if x]
    if not parts:
        return []
    if not all(x.isdigit() and 1 <= int(x) <= n_options for x in parts):
        return None
    idx = list(dict.fromkeys(int(x) - 1 for x in parts))
    return idx if len(idx) <= max_picks else None


def _question(agent: Agent, s: dict, ask: Callable[[str], str]) -> dict:
    v = flow.question_view(s)
    kind = v["kind"]
    if kind == "dialect":
        asks = "".join(f"\n  - {x}" for x in v["asks"])
        ans = ask(f"이렇게 이해했어요: \"{v['understood']}\"{asks}\n맞으면 Enter, 다르면 바른 문장을 써 주세요: ")
        return flow.answer_question(agent, s, text=ans)
    if kind in ("confirm_emd", "request_suggest"):
        hint = "\n  맞으면 y, 다른 요청은 직접 쓰기, 없으면 Enter: " if kind == "request_suggest" else " [Y/n]: "
        ans = ask(v["question"] + hint)
        yes = _yes(ans) if kind == "confirm_emd" else ans.strip().lower() in ("y", "네", "예")
        return flow.answer_question(agent, s, yes=yes, text="" if yes else ans)
    if kind == "emd":
        hint = " (예: 구좌읍 / 여러 곳은 구좌읍, 성산읍 / 전체)" if v.get("choices") else ""
        try:
            return flow.answer_question(agent, s, text=ask(f"{v['question']}{hint} ") or "")
        except ValueError as e:                 # 장소를 너무 많이 고름 → 같은 질문을 다시 한다
            return {**s, "messages": s["messages"] + [str(e)]}
    suffix = " (없으면 Enter) " if kind == "request" else " "
    return flow.answer_question(agent, s, text=ask(v["question"] + suffix) or "")


def _meanings(agent: Agent, s: dict, ask: Callable[[str], str]) -> dict:
    picks = {}
    for v in flow.meaning_view(s):
        opts = [o for o in v["options"] if o["kind"] != "other"]       # 화면처럼 '기타'는 하신 말씀 그대로
        limit = v["max_picks"]
        lines = [f"\n'{v['text']}' — 어떤 뜻인가요?" + (f" (해당하는 것을 {limit}개까지 고를 수 있어요)" if limit > 1 else "")]
        for i, o in enumerate(opts, 1):
            mark = " (추천)" if o["option_id"] == v["recommended"] else ""
            note = f"  · {o['note']}" if o.get("note") and o["kind"] == "no_data" else ""
            lines.append(f"  {i}. {o['text']}{mark}{note}")
        lines.append("  0. 맞는 뜻이 없음 (하신 말씀 그대로)")
        ans = ask("\n".join(lines) + "\n번호 (여러 개는 1,3 처럼 / Enter=추천): ").strip()
        if ans == "0":
            picks[v["claim_id"]] = []
            continue
        idx = parse_picks(ans, len(opts), limit)
        if idx is None:
            idx = parse_picks(ask(f"1~{len(opts)} 사이 번호를 {limit}개까지 써 주세요: "), len(opts), limit) or []
        if idx:
            picks[v["claim_id"]] = [opts[i]["option_id"] for i in idx]
    return flow.submit_meanings(agent, s, picks)


def _evidence(agent: Agent, s: dict, ask: Callable[[str], str], say: Callable[[str], None]) -> dict:
    say("\n찾은 근거입니다. 민원서에 넣을 근거를 고르세요. ([v] 넣음, [ ] 뺌, - 넣을 수 없음)")
    for it in s["evidence_items"]:
        mark = "[v]" if it["index"] in s["picked"] else ("[ ]" if it["usable"] else " - ")
        old = " · 오래된 자료" if it["stale"] else ""
        say(f"  {mark} {it['index'] + 1}. {it['verdict_ko']}: {it['indicator']} = {it['value_text']}{old}")
    ans = ask("넣을 번호 (여러 개는 1,3 / Enter=[v] 그대로 / 0=넣지 않음): ").strip()
    if not ans:
        picked = s["picked"]
    elif ans == "0":
        picked = []
    else:
        picked = [int(x) - 1 for x in ans.replace(",", " ").split() if x.isdigit()]
    return flow.choose_evidence(agent, s, picked)


def run_conversation(text: str, ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
                     agent: Agent | None = None, places: list[str] | None = None,
                     topics: list[str] | None = None) -> dict | None:
    """끝까지 가면 handoff(가린 질의·근거 패키지 등) + draft(민원서 초안), 멈추면 None."""
    own = agent is None
    agent = agent or Agent()
    try:
        s = flow.start(agent, text, places=places, topics=topics)
        shown = 0
        for _ in range(MAX_STEPS):
            for m in s["messages"][shown:]:
                say(m)
            shown = len(s["messages"])
            stage = s["stage"]
            if stage == "questions":
                s = _question(agent, s, ask)
            elif stage == "meanings":
                s = _meanings(agent, s, ask)
            elif stage == "confirm":
                rows = "\n".join(f"  {k}: {v}" for k, v in flow.confirm_rows(s) if v)
                s = flow.confirm(agent, s, _yes(ask(f"\n이렇게 이해했어요.\n{rows}\n맞나요? [Y/n]: ")))
            elif stage == "evidence":
                s = _evidence(agent, s, ask, say)
            elif stage == "draft":
                for e in s["handoff"]["package"]["evidence"]:
                    say(f"- {e['claim_id']} {VERDICT_KO[e['verdict']]}: {e['indicator']}"
                        + (f" = {e['value']}{e['unit'] or ''}" if e["value"] is not None else ""))
                for g in s["handoff"]["package"]["gaps"]:
                    say(f"- 빈칸: {g['concept']}")
                return {**s["handoff"], "draft": s["draft"]}
            else:                                   # stopped 또는 input(빈 글)
                say(s.get("stop_reason") or "불편한 점을 먼저 써 주세요.")
                return None
        say("단계가 너무 많아 멈출게요. 처음부터 다시 말씀해 주세요.")
        return None
    finally:
        if own:
            agent.close()
