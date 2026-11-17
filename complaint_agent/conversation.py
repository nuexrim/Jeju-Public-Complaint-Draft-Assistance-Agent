"""대화형 실행: 되묻기 → 뜻 선택 → 최종 확인 → 근거 패키지. 화면(UI)이 생기기 전까지 이 흐름이 기준이다.

    python -m complaint_agent "우리 동네 버스가 확 줄어부난 할망덜 병원 가기가 막 힘들어마씸 구좌읍"
    python -m complaint_agent --out handoff.json "..."     민원 초안 모델에 넘길 JSON 저장
    python -m complaint_agent --mock-engine "..."           근거 DB 없이 형식만

ask(prompt) -> str 를 바꿔 끼우면 화면·테스트에서도 같은 흐름을 쓴다.
"""
from __future__ import annotations

from typing import Callable

from complaint_agent.pipeline import MAX_OTHER_ROUNDS, Agent

MAX_QUESTION_ROUNDS = 4       # 같은 빈 칸을 끝없이 묻지 않도록 (질문은 보통 0~3개)
VERDICT_KO = {"supported": "맞음", "partial": "일부 맞음", "contradicted": "자료와 다름", "unverifiable": "확인 불가"}


def _yes(ans: str) -> bool:
    return ans.strip().lower() in ("", "y", "yes", "네", "예", "응", "맞아요", "맞음")


def ask_questions(agent: Agent, r: dict, ask: Callable[[str], str], say: Callable[[str], None]) -> dict:
    for _ in range(MAX_QUESTION_ROUNDS):
        if not r["questions"]:
            break
        q = r["questions"][0]
        field = q["field"]
        if field == "dialect":
            asks = "\n".join(f"  - {x['question']}" for x in r["questions"] if x["field"] == "dialect")
            ans = ask(f"이렇게 이해했어요: \"{r['query']['normalized_text']}\"\n{asks}\n"
                      f"맞으면 Enter, 다르면 바른 문장을 써 주세요: ")
            r = agent.answer(r, accept_sentence=True) if not ans.strip() else agent.answer(r, corrected_sentence=ans)
        elif field == "emd_name" and q.get("confirm"):
            r = agent.answer(r, confirm_emd=_yes(ask(f"{q['question']} [Y/n]: ")))
        elif field == "emd_name":
            r = agent.answer(r, emd_name=ask(f"{q['question']} ") or "")
        elif field == "claims":
            r = agent.answer(r, detail=ask(f"{q['question']} ") or "")
        elif field == "user_request":
            if q.get("suggestion"):
                ans = ask(f"{q['question']}\n  맞으면 y, 다른 요청은 직접 쓰기, 없으면 Enter: ")
                r = agent.answer(r, user_request=q["suggestion"] if ans.strip().lower() in ("y", "네", "예") else ans)
            else:
                r = agent.answer(r, user_request=ask(f"{q['question']} (없으면 Enter) "))
        elif field == "complaint_type":
            say(q["question"])
            r["questions"] = [x for x in r["questions"] if x["field"] != "complaint_type"]
        else:                                   # 모르는 질문 종류는 건너뛴다(필수면 아래에서 멈춤)
            r["questions"] = r["questions"][1:]
        for n in r.get("notices", [])[-1:]:
            if "찾지 못했어요" in n:
                say(n)
    return r


def parse_picks(ans: str, n_options: int, max_picks: int) -> list[int] | None:
    """'1', '1,3', '1 3' → 0부터 센 번호 목록. 빈 답은 [] (추천), 잘못된 답은 None."""
    parts = [x for x in ans.replace(",", " ").split() if x]
    if not parts:
        return []
    if not all(x.isdigit() and 1 <= int(x) <= n_options for x in parts):
        return None
    idx = list(dict.fromkeys(int(x) - 1 for x in parts))
    return idx if len(idx) <= max_picks else None


def choose_meanings(agent: Agent, query: dict, ask: Callable[[str], str], say: Callable[[str], None]) -> dict:
    pending = [c["id"] for c in query["claims"]]
    rounds = 0
    while pending:
        interp = agent.meaning_options(query)
        picks = {}
        for c in interp["claims"]:
            if c["claim_id"] not in pending:
                continue
            text = next(x["text"] for x in query["claims"] if x["id"] == c["claim_id"])
            opts = c["options"]
            if all(o["kind"] == "other" for o in opts):
                continue        # 고를 뜻이 없음(엔진 주제 밖) → 문장 그대로 (범용 틀이나 빈칸으로 처리된다)
            limit = c.get("max_picks", 1)
            lines = [f"\n'{text}' — 어떤 뜻인가요?" + (f" (해당하는 것을 {limit}개까지 고를 수 있어요)" if limit > 1 else "")]
            for i, o in enumerate(opts, 1):
                mark = " (추천)" if o["option_id"] == c["recommended"] else ""
                note = f"  · {o['note']}" if o.get("note") and o["kind"] == "no_data" else ""
                lines.append(f"  {i}. {o['text']}{mark}{note}")
            prompt = "\n".join(lines) + "\n번호 (여러 개는 1,3 처럼 / Enter=추천): "
            idx = parse_picks(ask(prompt), len(opts), limit)
            if idx is None:
                idx = parse_picks(ask(f"1~{len(opts)} 사이 번호를 {limit}개까지 써 주세요: "), len(opts), limit) or []
            if not idx:
                idx = [next(i for i, o in enumerate(opts) if o["option_id"] == c["recommended"])]
            chosen: list[str | dict] = []
            for i in idx:
                if opts[i]["kind"] != "other":
                    chosen.append(opts[i]["option_id"])
                elif rounds >= MAX_OTHER_ROUNDS:
                    say("직접 쓴 문장 그대로 근거를 찾을게요.")
                else:
                    chosen.append({"other": ask("직접 써 주세요: ")})
            if chosen:
                picks[c["claim_id"]] = chosen
        query, pending = agent.choose(query, interp, picks)
        rounds += 1
        if rounds > MAX_OTHER_ROUNDS:
            break
    return query


def run_conversation(text: str, ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
                     agent: Agent | None = None) -> dict | None:
    own = agent is None
    agent = agent or Agent()
    try:
        r = agent.understand(text)
        for n in r["notices"]:
            say(f"알림: {n}")
        if r["pii_masked"]:
            say(f"개인정보를 가렸어요: {', '.join(r['pii_masked'])}")
        r = ask_questions(agent, r, ask, say)
        if r["status"] != "ready":
            say("필요한 정보가 모자라 근거를 찾지 못했어요: " + "; ".join(r["schema_errors"] or
                [q["question"] for q in r["questions"] if not q.get("optional")]))
            return None
        query = r["query"]
        for _ in range(2):
            query = choose_meanings(agent, query, ask, say)
            if _yes(ask("\n" + agent.summary(query) + " [Y/n]: ")):
                break
            query = {**query, "claims": [{"id": c["id"], "text": c["text"]} for c in r["query"]["claims"]]}
        else:
            say("확인을 받지 못해 멈출게요. 처음부터 다시 말씀해 주세요.")
            return None
        package = agent.evidence(query)
        for e in package["evidence"]:
            say(f"- {e['claim_id']} {VERDICT_KO[e['verdict']]}: {e['indicator']}"
                + (f" = {e['value']}{e['unit'] or ''}" if e["value"] is not None else ""))
        for g in package["gaps"]:
            say(f"- 빈칸: {g['concept']}")
        return agent.handoff(r, query, package)
    finally:
        if own:
            agent.close()
