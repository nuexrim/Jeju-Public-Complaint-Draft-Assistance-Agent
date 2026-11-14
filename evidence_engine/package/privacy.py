"""입력 개인정보 마스킹 (CLAUDE.md 5장 9항). 엔진은 처리 전에 이것을 거친 질의만 쓴다.

가리는 것
- 주민등록번호 → [주민번호]      예: 900101-1234567
- 전화번호     → [연락처]        예: 010-1234-5678, 01012345678, 064-123-4567
- 이메일       → [이메일]
- 상세주소     → 도로명 뒤 건물번호, 지번, 아파트 동·호수만 가린다. 읍면동·리·도로명은 근거 계산에 필요해 남긴다.
                 예: 한림로 650 → 한림로 [번지],  1820번지 10호 → [번지],  101동 1203호 → [동호수]
- 이름         → 성씨로 시작하는 2~4글자 + 씨/님 호칭 (예: 김철수 씨 → [이름] 씨)
                 호칭 없는 이름은 문장만으로 확실히 가를 수 없어 가리지 않는다(팀원 모듈의 정규화 단계와 함께 막는다).
읍면동명(이도2동 등)과 숫자가 든 지명은 건드리지 않도록 패턴을 좁게 잡았다.
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

PLACEHOLDERS = ("[주민번호]", "[연락처]", "[이메일]", "[동호수]", "[번지]", "[이름]")


def mask_text(text: str | None) -> str | None:
    if not text:
        return text
    t = _RRN.sub("[주민번호]", text)
    t = _EMAIL.sub("[이메일]", t)
    t = _PHONE.sub("[연락처]", t)
    t = _APT.sub("[동호수]", t)
    t = _LOT.sub("[번지]", t)
    t = _ROAD.sub(lambda m: f"{m.group(1)} [번지]", t)
    t = _NAME.sub(lambda m: m.group(0) if m.group(1).endswith(TITLE_NOUNS)
                  else f"[이름]{m.group(2)}{m.group(3)}", t)
    return t


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
