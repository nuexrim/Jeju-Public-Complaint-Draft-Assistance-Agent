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

# 제목: 국민신문고 작성 요령대로 '장소 + 요청'을 한 문장으로. 요구사항이 없으면 주제로 쓴다.
TITLE_REQUEST = "{place} {user_request} 요청"
TITLE_TOPIC = "{place} {topic} 관련 불편 개선 요청"
TITLE_MAX = 60                    # 제목이 이보다 길면 줄인다(창구 공식 제한이 아니라 읽기 좋은 길이)
REQUEST_ENDINGS = ("요청", "요망", "건의", "바랍니다", "주세요", "주십시오")   # 이미 요청으로 끝나면 '요청'을 덧붙이지 않는다

CONTENT_INTRO = "다음과 같은 생활 불편이 있어 말씀드립니다."   # 장소는 제목과 [발생 장소]에 있으므로 되풀이하지 않는다
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
INFORMAL_WARNING = "일부 문장은 격식체(합니다체)로 바꾸지 못했어요. '고쳐 쓰기'에서 다듬어 주세요."
NO_SOURCE_WARNING = "원래 하신 말씀(원문)이 없어 제주어 화면도 표준어로 보여 드려요."

# ---------------------------------------------------------------- 제출 창구에 맞춘 글
# 국민신문고·제주도 '도지사에게 바란다' 모두 글 칸이 '제목'과 '내용' 두 개다.
# 내용 칸에는 아래 순서로 한 덩어리로 붙여 넣는다(사실 → 요청 → 근거).
BODY_PLACE_HEAD = "[발생 장소]"
BODY_HEADS = {"content": "[불편 사항]", "request": "[요청 사항]", "evidence": "[근거 자료]"}
PROVINCE = "제주특별자치도"

PORTALS = [
    {"name": "국민신문고", "url": "https://www.epeople.go.kr",
     "note": "법에 따른 민원으로 접수됩니다. 민원 → 일반민원 → 민원신청에서 본인 확인 후 제목·내용을 붙여 넣고 처리기관을 고릅니다."},
    {"name": "제주특별자치도 누리집 '도지사에게 바란다'", "url": "https://www.jeju.go.kr",
     "note": "생활 불편·도정 건의 창구입니다. 법에 따른 민원으로 처리받으려면 국민신문고를 이용합니다."},
]
SUBMIT_STEPS = [
    "국민신문고(epeople.go.kr)에 들어가 '민원신청'을 누릅니다.",
    "본인 확인을 합니다. 이름·연락처·주소는 그 화면에서 직접 넣습니다(이 민원서에는 넣지 않습니다).",
    "'제목 복사하기'를 눌러 제목 칸에, '내용 복사하기'를 눌러 내용 칸에 붙여 넣습니다.",
    "처리기관을 고릅니다. 잘못 골라도 맞는 기관으로 넘겨집니다.",
    "내용을 다시 읽어 보고 제출합니다.",
]

# 사용자가 직접 고칠 수 있는 섹션. 근거 자료는 숫자·출처가 바뀌지 않게 잠근다.
EDITABLE = ("title", "content", "request")
