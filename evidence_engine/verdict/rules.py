"""규칙 기반 판정. LLM은 판정하지 않는다.

민원의 claim은 "부족하다·줄었다·멀다·자주 난다"처럼 문제가 있다고 주장하는 문장으로 본다.
지표가 그 문제 방향으로 얼마나 치우쳤는지로 판정한다.

rank   : 비교군(읍·면끼리 / 동끼리)에서 대상보다 '더 나은' 곳의 비율 p
         p ≥ supported_share → supported, p ≤ contradicted_share → contradicted, 사이 → partial
trend  : 증감률 ≤ trend_supported_pct → supported, ≥ trend_contradicted_pct → contradicted, 사이 → partial
zero   : 0 → supported, 1 이상 → contradicted
context: unverifiable (제주 전체 값이라 이 지역 주장을 판단할 수 없음)
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULTS = {
    "supported_share": 2 / 3,
    "contradicted_share": 1 / 3,
    "trend_supported_pct": -5.0,
    "trend_contradicted_pct": 2.0,
}


@dataclass
class Judgement:
    verdict: str
    detail: str


def worse_share(value: float, peers: list[float], worse: str) -> float:
    """비교군 중 대상보다 형편이 나은 곳의 비율 (0~1). 같은 값은 절반으로 센다."""
    if not peers:
        return 0.5
    better = sum(1 for v in peers if (v > value if worse == "low" else v < value))
    ties = sum(1 for v in peers if v == value)
    return (better + 0.5 * ties) / len(peers)


def judge(rule: str, value: float | None, *, peers: list[float] | None = None, worse: str = "high",
          params: dict | None = None) -> Judgement:
    p = {**DEFAULTS, **(params or {})}
    if value is None:
        return Judgement("unverifiable", "값을 계산하지 못함")
    if rule == "context":
        return Judgement("unverifiable", "제주 전체 참고값이라 이 지역 주장을 판정하지 않음")
    if rule == "zero":
        return Judgement("supported", "해당 시설이 없음") if value == 0 else Judgement("contradicted", f"해당 시설 {value:.0f}곳 있음")
    if rule == "trend":
        if value <= p["trend_supported_pct"]:
            return Judgement("supported", f"{p['trend_supported_pct']:.0f}% 이하로 감소")
        if value >= p["trend_contradicted_pct"]:
            return Judgement("contradicted", "감소하지 않고 늘어남")
        return Judgement("partial", "소폭 변화")
    if rule == "rank":
        others = [v for v in peers or []]
        share = worse_share(value, others, worse)
        n = len(others)
        if share >= p["supported_share"]:
            return Judgement("supported", f"비교 지역 {n}곳 중 {share:.0%}가 이곳보다 나음")
        if share <= p["contradicted_share"]:
            return Judgement("contradicted", f"비교 지역 {n}곳 중 {share:.0%}만 이곳보다 나음")
        return Judgement("partial", f"비교 지역 {n}곳 중 {share:.0%}가 이곳보다 나음(중간 수준)")
    raise ValueError(f"알 수 없는 판정 규칙: {rule}")
