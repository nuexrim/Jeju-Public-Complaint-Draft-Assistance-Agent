"""표준어본 문체를 격식체(합니다체)로 맞춘다. LLM 없이 문장 끝만 규칙으로 바꾼다.

    formalize("버스가 줄어서 병원 가기가 힘들어요")        → "버스가 줄어서 병원 가기가 힘듭니다"
    formalize("버스가 줄었다", plain=True)                  → "버스가 줄었습니다"     (주장 문장: '-다'로 끝나는 글)
    formalize("의자를 놓아 주세요")                          → "의자를 놓아 주시기 바랍니다"

바꾸는 것은 문장 맨 끝 낱말뿐이다(문장 가운데의 '-어서', '-는데' 등은 그대로).
확실히 바꿀 수 있는 끝만 바꾸고, 애매한 끝('-네요', '-거든요', 물음 등)은 그대로 둔다.
그대로 남은 해요체가 있는지는 has_informal() 로 알 수 있다(화면에서 '고쳐 쓰기'를 권한다).
"""
from __future__ import annotations

import re

# 한글 음절 = 0xAC00 + (초성*21 + 중성)*28 + 종성
_A, _AE, _EO, _E, _YEO, _O, _WA, _WAE, _OE, _U, _WO, _WI, _EU, _UI, _I = 0, 1, 4, 5, 6, 8, 9, 10, 11, 13, 14, 16, 18, 19, 20
_N, _L, _B, _S = 4, 8, 17, 19          # 받침 ㄴ ㄹ ㅂ ㅅ


def _split(ch: str) -> tuple[int, int, int] | None:
    code = ord(ch) - 0xAC00
    if not 0 <= code < 11172:
        return None
    return code // 588, (code % 588) // 28, code % 28


def _join(cho: int, jung: int, jong: int = 0) -> str:
    return chr(0xAC00 + (cho * 21 + jung) * 28 + jong)


def _with_b(ch: str, jung: int | None = None) -> str:
    """받침 없는 음절에 ㅂ 받침을 붙인다(가→갑). jung 을 주면 모음도 바꾼다(려→립)."""
    cho, j, _ = _split(ch)
    return _join(cho, j if jung is None else jung, _B)


# ---------------------------------------------------------------- 해요체 → 합니다체
# 바꾸지 않는 끝(뜻이 달라지거나 규칙으로 정하기 어려움)
_KEEP = ("잖아요", "거든요", "데요", "네요", "군요", "구나", "지요", "죠", "까요", "나요", "대요", "고요", "구요", "걸요",
         "인가요", "한가요", "은가요", "는가요", "던가요", "을게요", "ㄹ게요", "게요", "래요", "려고요")
_AMBIGUOUS = {"걸어요", "들어요", "물어요", "실어요"}          # 걷다/걸다, 듣다/들다 … 낱말만으로 정할 수 없음
# 끝이 정해진 것(긴 것 먼저)
_FIXED = [
    ("주세요", "주시기 바랍니다"), ("아니에요", "아닙니다"), ("아니예요", "아닙니다"), ("거예요", "것입니다"), ("거에요", "것입니다"),
    ("이에요", "입니다"), ("이예요", "입니다"), ("예요", "입니다"), ("으세요", "으십시오"), ("세요", "십시오"),
    ("그래요", "그렇습니다"), ("이래요", "이렇습니다"), ("저래요", "저렇습니다"), ("도와요", "돕습니다"), ("고와요", "곱습니다"),
]
# ㅂ 불규칙(어려워요 → 어렵습니다). 이 줄기로 끝나지 않는 '-워요'는 '우' 동사로 본다(배워요 → 배웁니다).
_B_STEMS = ("어렵", "무섭", "덥", "춥", "가깝", "시끄럽", "더럽", "어둡", "무겁", "가볍", "고맙", "아쉽", "쉽", "즐겁", "반갑", "두렵",
            "안타깝", "롭", "럽", "뜨겁", "차갑", "두껍", "답", "밉", "싱겁", "맵", "눕", "굽", "줍")
# 으 불규칙(아파요 → 아픕니다) 등
_EU = {"아파": "아픕", "바빠": "바쁩", "나빠": "나쁩", "고파": "고픕", "잠가": "잠급", "담가": "담급", "따라": "따릅",
       "커": "큽", "써": "씁", "꺼": "끕", "떠": "뜹", "퍼": "픕", "뻐": "쁩"}
_S_IRREGULAR = "나지부이저그"          # 나아요→낫습니다, 지어요→짓습니다


def _from_yo(word: str) -> str | None:
    """'…요'로 끝나는 낱말 하나를 합니다체로. 바꿀 수 없으면 None."""
    if word in _AMBIGUOUS:
        return None
    if word.endswith(_KEEP) and word != "나요" and not word.endswith(("그래요", "이래요", "저래요")):   # '냄새가 나요'는 바꾼다
        return None
    for suffix, repl in _FIXED:
        if word.endswith(suffix):
            return word[:-len(suffix)] + repl
    body = word[:-1]
    if not body or _split(body[-1]) is None:
        return None
    last = body[-1]
    cho, jung, jong = _split(last)

    # 1) 받침 있는 줄기 + 어/아:  먹어요→먹습니다, 줄었어요→줄었습니다, 힘들어요→힘듭니다
    if last in "어아" and len(body) >= 2 and _split(body[-2]) is not None:
        p_cho, p_jung, p_jong = _split(body[-2])
        if p_jong == _L:
            return body[:-2] + _join(p_cho, p_jung, _B) + "니다"
        if p_jong:
            return body[:-1] + "습니다"
        if p_jung in (_WI, _OE, _UI):                       # 쉬어요→쉽니다, 되어요→됩니다
            return body[:-2] + _join(p_cho, p_jung, _B) + "니다"
        if body[-2] in _S_IRREGULAR:                        # 나아요→낫습니다
            return body[:-2] + _join(p_cho, p_jung, _S) + "습니다"
        return None
    if jong:
        return None

    # 2) 줄어든 꼴
    if last == "워" and len(body) >= 2 and _split(body[-2]) is not None:
        p_cho, p_jung, p_jong = _split(body[-2])
        if not p_jong:
            stem = body[:-2] + _join(p_cho, p_jung, _B)
            if stem.endswith(_B_STEMS):
                return stem + "습니다"                      # 어려워요→어렵습니다
        return body[:-1] + "웁니다"                         # 배워요→배웁니다
    for tail, repl in _EU.items():
        if body.endswith(tail):
            return body[:-len(tail)] + repl + "니다"
    if last in "라러" and len(body) >= 2 and _split(body[-2]) is not None and not body.endswith("놀라"):
        p_cho, p_jung, p_jong = _split(body[-2])
        if p_jong == _L:                                     # 달라요→다릅니다, 불러요→부릅니다
            return body[:-2] + _join(p_cho, p_jung) + "릅니다"
    if jung == _AE and cho == 18:                            # 해요→합니다
        return body[:-1] + _join(cho, _A, _B) + "니다"
    if jung == _WAE:                                         # 돼요→됩니다
        return body[:-1] + _with_b(last, _OE) + "니다"
    if jung == _YEO:                                         # 걸려요→걸립니다 (켜요·펴요는 켭니다·폅니다)
        return body[:-1] + (_with_b(last) if last in "켜펴" else _with_b(last, _I)) + "니다"
    if jung == _WA:                                          # 와요→옵니다, 봐요→봅니다
        return body[:-1] + _with_b(last, _O) + "니다"
    if jung == _WO:                                          # 줘요→줍니다, 바꿔요→바꿉니다
        return body[:-1] + _with_b(last, _U) + "니다"
    if jung in (_A, _EO, _AE, _E):                           # 가요→갑니다, 서요→섭니다, 보내요→보냅니다
        return body[:-1] + _with_b(last) + "니다"
    return None


# ---------------------------------------------------------------- '-다'로 끝나는 글(주장 문장) → 합니다체
_NOT_VERB = {"바다", "모두다", "다"}


def _from_da(word: str) -> str | None:
    if word in _NOT_VERB or len(word) < 2 or word.endswith(("니다", "시다")):
        return None
    body = word[:-1]
    if _split(body[-1]) is None:
        return None
    last = body[-1]
    cho, jung, jong = _split(last)
    if last == "는" and len(body) >= 2 and _split(body[-2]) and _split(body[-2])[2]:   # 먹는다→먹습니다
        return body[:-1] + "습니다"
    if jong in (_N, _L):                                     # 간다→갑니다, 멀다→멉니다
        return body[:-1] + _join(cho, jung, _B) + "니다"
    if jong:                                                 # 줄었다→줄었습니다, 적다→적습니다
        return body + "습니다"
    return body[:-1] + _with_b(last) + "니다"                # 크다→큽니다, 위험하다→위험합니다, 이다→입니다


# ---------------------------------------------------------------- 문장 단위
# 문장 끝: (끝 낱말)(괄호 설명)?(마침표 등)
_END = re.compile(r"(?P<word>[가-힣]+)(?P<tail>\s*(?:\([^()\n]*\))?[.!…~]*)$")
_SENT = re.compile(r"(?<=[.!?…])\s+")


def _sentence(sentence: str, plain: bool) -> str:
    if "?" in sentence[-3:]:                                 # 물음은 바꾸지 않는다
        return sentence
    m = _END.search(sentence)
    if not m:
        return sentence
    word = m.group("word")
    new = None
    if word.endswith("요"):
        new = _from_yo(word)
    elif plain and word.endswith("다"):
        new = _from_da(word)
    if new is None:
        return sentence
    return sentence[:m.start("word")] + new + m.group("tail").replace("~", "")


def formalize(text: str, plain: bool = False) -> str:
    """문장마다 끝 낱말을 합니다체로 바꾼다. plain=True 면 '-다'로 끝나는 글(주장 문장)도 바꾼다."""
    out = []
    for line in (text or "").split("\n"):
        parts = _SENT.split(line.rstrip())
        out.append(" ".join(_sentence(p, plain) for p in parts))
    return "\n".join(out)


def has_informal(text: str) -> bool:
    """합니다체로 바꾸지 못하고 남은 해요체 문장 끝이 있는가."""
    for line in (text or "").split("\n"):
        for part in _SENT.split(line.rstrip()):
            m = _END.search(part)
            if m and m.group("word").endswith("요"):
                return True
    return False
