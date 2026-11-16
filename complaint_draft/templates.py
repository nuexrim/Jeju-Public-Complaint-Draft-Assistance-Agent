"""민원서 문장 틀. LLM 없이 handoff 필드만 채운다(숫자는 근거 패키지 값만 옮긴다).

문장을 바꾸고 싶으면 이 파일만 고치면 된다. {중괄호} 자리는 draft.py 가 채운다.
"""
from __future__ import annotations

# 섹션 순서와 제목. 표준어본·방언본이 같은 순서를 쓴다(토글해도 같은 자리에 같은 섹션).
SECTIONS = [
    ("title", "제목"),
    ("content", "민원 내용"),
    ("request", "요청 사항"),
    ("evidence", "근거 자료"),
]

TITLE = "[{complaint_type}] {place} {topic} 관련 민원"
TITLE_NO_PLACE = "[{complaint_type}] {topic} 관련 민원"

CONTENT_INTRO = "{place}의 생활 불편에 대해 말씀드립니다."
CONTENT_INTRO_NO_PLACE = "생활 불편에 대해 말씀드립니다."
CONTENT_CLAIMS_HEAD = "불편한 점은 다음과 같습니다."

REQUEST = "다음 사항을 검토해 주시기 바랍니다: {user_request}"
REQUEST_DEFAULT = "현황을 확인하시고 개선 방안을 검토해 주시기 바랍니다."

EVIDENCE_HEAD = "제주 공공데이터로 확인한 내용입니다."
EVIDENCE_ITEM = "{n}. {indicator}: {value}"
EVIDENCE_COMPARE = "   비교: {region_compare}"
EVIDENCE_SOURCE = "   출처: {sources}"
EVIDENCE_PARTIAL = "   ※ 일부만 확인된 근거입니다."
EVIDENCE_NONE = "데이터로 확인된 근거는 없습니다."

# 판정 → 화면 표시
VERDICT_KO = {
    "supported": "자료로 확인됨",
    "partial": "일부만 확인됨",
    "contradicted": "자료와 다름",
    "unverifiable": "자료로 확인 불가",
}

MOCK_WARNING = "근거 DB 없이 만든 형식 예시입니다. 수치는 실제 계산값이 아니라서 복사·제출할 수 없어요."
RULES_WARNING = "AI 이해 단계를 쓰지 못해 규칙으로 정리한 결과입니다. 내용이 맞는지 꼭 확인해 주세요."
NO_SOURCE_WARNING = "원래 하신 말씀(원문)이 없어 제주어 화면도 표준어로 보여 드려요."
