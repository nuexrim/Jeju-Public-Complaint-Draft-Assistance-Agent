"""사용자에게 보이는 판정 이름과 주의 문구 머리말. 엔진·터미널·민원서 화면이 모두 이 이름을 쓴다.

한 화면에 같은 판정이 다른 말로 나오지 않게 하려고 한곳에 둔다(전에는 엔진 '부분 지지', 화면 '일부만 확인됨',
터미널 '일부 맞음'이 섞여 나왔다). 이름은 민원서 화면(complaint_draft/templates.py VERDICT_KO)이 먼저 정한 말을 따른다.
화면 쪽은 엔진 없이도 열리도록 같은 값을 따로 들고 있고, 테스트가 둘이 같은지 확인한다.
"""
from __future__ import annotations

VERDICT_LABEL = {
    "supported": "자료로 확인됨",
    "partial": "일부만 확인됨",
    "contradicted": "자료와 다름",
    "unverifiable": "자료로 확인 불가",
}

# 주의 문구(caveats) 머리말. 평가 스크립트·테스트가 이 머리말로 문구 종류를 센다.
CROSS_CHECK = "함께 볼 자료 · "          # 판정 옆에 붙이는 맥락 지표 값 (판정을 바꾸지 않음)
INDIRECT = "간접 근거: "                 # 주장이 말하는 것과 자료가 재는 것이 다름
WEAK = "약한 자료: "                     # 자료 성격상 판정을 낮춤
TEMPLATE_MARK = "(자동 집계·참고용)"     # 등록 지표가 아니라 미리 받은 시설 목록을 센 근거의 지표 이름 끝


def downgraded(verdict: str, why: str) -> str:
    """'자료로 확인됨'·'자료와 다름'을 '일부만 확인됨'으로 낮춘 이유 문구."""
    return f"{why} '{VERDICT_LABEL[verdict]}' 대신 '{VERDICT_LABEL['partial']}'으로 판정"
