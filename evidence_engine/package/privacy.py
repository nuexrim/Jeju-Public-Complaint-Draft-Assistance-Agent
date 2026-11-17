"""입력 개인정보 마스킹 (CLAUDE.md 5장 9항). 엔진은 처리 전에 이것을 거친 질의만 쓴다.

가리는 것
- 주민등록번호 → [주민번호]      예: 900101-1234567
- 전화번호     → [연락처]        예: 010-1234-5678, 01012345678, 064-123-4567
- 이메일       → [이메일]
- 상세주소     → 도로명 뒤 건물번호, 지번, 아파트 동·호수만 가린다. 읍면동·리·도로명은 근거 계산에 필요해 남긴다.
                 예: 한림로 650 → 한림로 [번지],  1820번지 10호 → [번지],  101동 1203호 → [동호수]
- 이름         → 성씨로 시작하는 2~4글자 + 씨/님 호칭 (예: 김철수 씨 → [이름] 씨)
                 자기소개 꼴도 가린다: '저는·제 이름은 ○○입니다/이에요', '○○(이)라고 합니다'
                 그 밖의 호칭 없는 이름(예: 문장 중간의 '김철수입니다')은 '정류소입니다'·'경로당입니다' 같은 낱말과
                 글자만으로 가를 수 없어 가리지 않는다(남은 빈틈. 화면에서 이름을 쓰지 말라고 안내한다).
- 지역번호 없는 번호 → [연락처]  예: 710-2584, 1588-1234 (하이픈이 있을 때만. 2018-2019 같은 연도 범위는 남긴다)
읍면동명(이도2동 등)과 숫자가 든 지명은 건드리지 않도록 패턴을 좁게 잡았다.

이 파일이 마스킹의 유일한 구현이다. 대화 모듈(dialect_normalizer/structure.py)도 mask_with_labels 를 쓴다.
그쪽은 LLM에 보내기 전에 가리며, 예문 데이터(AI Hub)의 가명 표기와 같은 '○○'를 자리표시로 쓴다.
"""
from __future__ import annotations

import copy
import re

SURNAMES = ("김이박최정강조윤장임한오서신권황안송류유전홍고문양손배백허남심노하곽성차주우구민진나지엄채원천방"
            "공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부")

_RRN = re.compile(r"\b\d{6}\s*-\s*[1-4]\d{6}\b")
_PHONE = re.compile(r"(?<!\d)(?:0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4})(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_APT = re.compile(r"(?<![가-힣\d])\d{1,4}\s*동\s*\d{1,4}\s*호(?!선)")
_LOT = re.compile(r"\d+(?:-\d+)?\s*번지(?:\s*\d+\s*호)?")
_ROAD = re.compile(r"([가-힣A-Za-z0-9]+(?:로|길))\s*(\d+(?:-\d+)?)(?=$|[\s,.)(]|번길)(?:번길\s*\d+(?:-\d+)?)?")
_NAME = re.compile(rf"(?<![가-힣])([{SURNAMES}][가-힣]{{1,3}})(\s?)(씨|님)")
# 성씨 글자로 시작하지만 이름이 아닌 호칭 명사 (선생님·기사님·이장님 등). 이것으로 끝나면 가리지 않는다.
TITLE_NOUNS = ("선생", "기사", "사장", "회장", "원장", "과장", "부장", "차장", "이장", "통장", "반장", "교장", "소장",
               "국장", "시장", "지사", "도지사", "의원", "위원", "대표", "목사", "신부", "주무관", "공무원", "담당자",
               "관리자", "직원", "고객", "주민", "환자", "보호자", "운전", "기관", "간호사", "의사", "약사")

# 자기소개: 앞에 '저는·제 이름은' 등이 있거나 뒤에 '(이)라고 합니다'가 올 때만 (성씨 + 1~2글자)
_SELF_NAME = re.compile(
    rf"(?:(?<=저는 )|(?<=전 )|(?<=저 )|(?<=나는 )|(?<=제 이름은 )|(?<=이름은 ))([{SURNAMES}][가-힣]{{1,2}})"
    rf"(?=입니다|이에요|예요|이고요|인데요|인데)"
    rf"|(?<![가-힣])([{SURNAMES}][가-힣]{{1,2}})(?=(?:이)?라고\s?(?:합니다|해요|하는데|합니당))")
# 자기소개 자리에 오지만 이름이 아닌 말 (저는 주민입니다, 저는 노인인데)
NOT_SELF_NAME = ("노인", "학생", "어르신", "엄마", "아빠", "부모", "농민", "어민", "상인", "주부", "기사", "임산부",
                 "장애인", "고령자", "운전자", "이장", "통장", "반장", "주민", "구민", "도민", "시민")

_LOCAL_PHONE = re.compile(r"(?<![\d-])(\d{3,4})-(\d{4})(?![\d-])")
_YEAR = re.compile(r"(?:19|20)\d\d")

# '씨'로 끝나지만 이름이 아닌 낱말 (날씨·솜씨·아저씨 등). 대화 모듈 테스트에서 '오늘날씨'가 이름으로 가려지는 것을 발견해 추가.
NOT_NAME_WORDS = ("날씨", "솜씨", "말씨", "글씨", "마음씨", "맵씨", "불씨", "씨앗", "아저씨", "아가씨", "맘씨", "아재씨")

PLACEHOLDERS = ("[주민번호]", "[연락처]", "[이메일]", "[동호수]", "[번지]", "[이름]")
# (라벨, 기본 자리표시) — 라벨은 사용자에게 "무엇을 가렸는지" 알려 줄 때 쓴다
_TAGS = {"주민번호": "[주민번호]", "이메일": "[이메일]", "전화번호": "[연락처]", "동호수": "[동호수]",
         "지번": "[번지]", "건물번호": "[번지]", "이름": "[이름]"}


def mask_with_labels(text: str | None, placeholder: str | None = None) -> tuple[str | None, list[str]]:
    """(가린 문장, 가린 종류 목록). placeholder 를 주면 모든 자리표시를 그 글자로 쓴다(예: '○○')."""
    if not text:
        return text, []
    found: list[str] = []

    def tag(label: str) -> str:
        found.append(label)
        return placeholder or _TAGS[label]

    def local_phone(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        if _YEAR.fullmatch(a) and _YEAR.fullmatch(b):        # 2018-2019 처럼 연도 범위
            return m.group(0)
        if text[m.end():m.end() + 1] == "년":
            return m.group(0)
        return tag("전화번호")

    def name(m: re.Match) -> str:
        if m.group(1).endswith(TITLE_NOUNS) or (m.group(1) + m.group(3)).endswith(NOT_NAME_WORDS):
            return m.group(0)
        return f"{tag('이름')}{m.group(2)}{m.group(3)}"

    def self_name(m: re.Match) -> str:
        word = m.group(1) or m.group(2)
        # '저는 한림읍입니다'·'저는 연동입니다'처럼 지명이면 남긴다(근거 계산에 필요). 이름이 '동'으로 끝나도 지명 쪽을 택한다.
        if word in NOT_SELF_NAME + TITLE_NOUNS or word.endswith(("읍", "면", "동", "리")):
            return m.group(0)
        return tag("이름")

    t = _RRN.sub(lambda m: tag("주민번호"), text)
    t = _EMAIL.sub(lambda m: tag("이메일"), t)
    t = _PHONE.sub(lambda m: tag("전화번호"), t)
    t = _LOCAL_PHONE.sub(local_phone, t)
    t = _APT.sub(lambda m: tag("동호수"), t)
    t = _LOT.sub(lambda m: tag("지번"), t)
    t = _ROAD.sub(lambda m: f"{m.group(1)} {tag('건물번호')}", t)
    t = _NAME.sub(name, t)
    t = _SELF_NAME.sub(self_name, t)
    return t, sorted(set(found))


def mask_text(text: str | None) -> str | None:
    return mask_with_labels(text)[0]


def mask_query(query: dict) -> dict:
    """구조화 질의의 자유 텍스트 필드를 모두 가린 사본. raw_text 는 아예 뺀다."""
    q = copy.deepcopy({k: v for k, v in query.items() if k != "raw_text"})
    for key in ("normalized_text", "user_request"):
        if key in q:
            q[key] = mask_text(q[key])
    q["claims"] = [{**c, "text": mask_text(c["text"])} for c in q.get("claims", [])]
    if "targets" in q:
        q["targets"] = [mask_text(t) for t in q["targets"]]
    loc = q.get("location") or {}
    if "emd_name" in loc:
        loc["emd_name"] = mask_text(loc["emd_name"])
    return q
