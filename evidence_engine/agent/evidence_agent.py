"""근거 탐색 에이전트: 판정한 주장 옆에 무엇을 '교차 확인'으로 붙일지 Claude 가 도구를 써 가며 고른다.

원칙
- 숫자를 만들지 않는다. 쓸 수 있는 도구는 등록된 지표를 계산해 보는 check_indicator 와 끝내는 finish 둘뿐이다.
  SQL 을 쓰거나 새 지표를 만들 수 없다. 값·순위 문장은 엔진(package/assemble.py)이 만든다.
- 판정을 바꾸지 않는다. 고른 지표는 caveats 에 '교차 확인'으로만 붙는다.
- 사용자가 고른 뜻(주 지표)은 그대로 두고 근거를 보강한다.
- LLM 이 꺼져 있거나(provider=mock) 실패하면 registry 의 related 목록을 쓴다(결정적 기본 경로).

고를 수 있는 지표
- 맥락 지표(role: context): 인구 증감, 고령 비율, 인구 밀도 등 — 다른 설명 가능성
- 주 지표와 같은 주제의 다른 지표: 같은 주제의 다른 측면(예: 버스 이용 감소 → 정류소 수)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent.llm_client import CallBudgetExceeded, LLMCallFailed, LLMClient, PaidCallNotAllowed
from indicators.library import aspect_name, load_registry

SYSTEM = (
    "너는 제주 공공데이터로 도민 민원 주장을 확인하는 분석가다. 주 지표의 판정은 이미 끝났다. "
    "이 주장을 다르게 설명할 수 있는지, 같은 주제의 다른 측면은 어떤지 보여 줄 지표를 check_indicator 로 계산해 보고, "
    "초안을 읽는 공무원에게 도움이 되는 것만 finish 의 keep 에 남겨라. 최대 3개까지 확인한다. "
    "숫자를 지어내지 말고, 도구 결과에 없는 내용은 말하지 마라."
)
MAX_KEEP = 3


@dataclass
class AgentResult:
    keep: list[str]
    checked: list[str] = field(default_factory=list)
    used_llm: bool = False
    note: str | None = None            # 실패 사유 (caveat 용, 키·민원 문장 없음)


def candidates(indicator: dict, available_tables: set[str], registry: dict | None = None) -> list[dict]:
    """에이전트가 계산해 볼 수 있는 지표: 맥락 지표 + 같은 주제의 다른 지표 (읍면동 비교형, 테이블 있음)."""
    registry = registry or load_registry()
    out = []
    for ind in registry["indicators"]:
        if ind["id"] == indicator["id"] or ind["scope"] != "emd" or not set(ind["tables"]) <= available_tables:
            continue
        if ind.get("role") == "context" or (ind.get("topic") and ind.get("topic") == indicator.get("topic")):
            out.append(ind)
    return out


def describe_candidate(ind: dict) -> str:
    if ind.get("role") == "context":
        return f"{ind['id']}: {ind['name']} (확인 이유: {ind['why']})"
    return f"{ind['id']}: {ind['name']} (같은 주제의 다른 측면: {aspect_name(ind.get('measures')) or '-'})"


class EvidenceAgent:
    def __init__(self, client: LLMClient, max_steps: int = 4, max_tokens: int = 2000):
        self.client = client
        self.max_steps = max_steps
        self.max_tokens = max_tokens

    @property
    def active(self) -> bool:
        return self.client.provider != "mock"

    def choose(self, claim_text: str, indicator: dict, verdict: str, region_compare: str | None,
               emd_label: str, available_tables: set[str], check) -> AgentResult:
        """check(indicator_id) -> dict 는 엔진이 실제로 계산한 값·순위 문장을 돌려준다."""
        default = [i for i in indicator.get("related", [])]
        cands = candidates(indicator, available_tables)
        if not self.active or not cands:
            return AgentResult(keep=default)
        ids = [c["id"] for c in cands]
        tools = [
            {"name": "check_indicator",
             "description": "등록된 지표 하나를 대상 읍면동에 대해 계산하고, 같은 유형 지역 안에서의 위치를 돌려준다.",
             "input_schema": {"type": "object", "properties": {"indicator_id": {"type": "string", "enum": ids}},
                              "required": ["indicator_id"], "additionalProperties": False}},
            {"name": "finish",
             "description": "확인을 끝낸다. keep 에는 근거 패키지에 '교차 확인'으로 붙일 지표 id 를 넣는다(최대 3개, 확인한 것만).",
             "input_schema": {"type": "object",
                              "properties": {"keep": {"type": "array", "items": {"type": "string", "enum": ids},
                                                      "maxItems": MAX_KEEP}},
                              "required": ["keep"], "additionalProperties": False}},
        ]
        prompt = "\n".join([
            f"주장: {claim_text}",
            f"지역: {emd_label}",
            f"주 지표: {indicator['name']} → 판정 {verdict}" + (f" ({region_compare})" if region_compare else ""),
            "계산해 볼 수 있는 지표:",
            *[f"- {describe_candidate(c)}" for c in cands],
        ])
        checked: list[str] = []
        finished: list[str] | None = None

        def run_tool(name: str, args: dict) -> dict:
            nonlocal finished
            if name == "check_indicator":
                iid = args.get("indicator_id")
                if iid not in ids:
                    return {"error": "고를 수 없는 지표"}
                if iid in checked:
                    return {"error": "이미 확인한 지표"}
                if len(checked) >= MAX_KEEP + 1:
                    return {"error": "확인 횟수 상한"}
                checked.append(iid)
                return check(iid)
            if name == "finish":
                finished = list(dict.fromkeys(i for i in args.get("keep", []) if i in checked))[:MAX_KEEP]
                return {"ok": True}
            return {"error": "알 수 없는 도구"}

        try:
            self.client.tool_loop(prompt, system=SYSTEM, tools=tools, run_tool=run_tool, max_steps=self.max_steps,
                                  purpose="evidence_agent", max_tokens=self.max_tokens)
        except (PaidCallNotAllowed, CallBudgetExceeded, LLMCallFailed) as exc:
            return AgentResult(keep=default, note=f"근거 탐색 에이전트를 쓰지 못해 기본 교차 확인으로 대신함 ({type(exc).__name__})")
        if finished is None:          # finish 없이 끝남 → 확인한 것 중 앞에서부터
            finished = checked[:MAX_KEEP]
        return AgentResult(keep=finished, checked=checked, used_llm=True)
