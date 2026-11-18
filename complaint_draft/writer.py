"""민원서 문장 다듬기: 제목·민원 내용·요청 사항을 Claude 가 표준어 격식체로 쓴다.

왜: 틀(templates.py)에 끼워 넣으면 사용자가 쓴 말(제주어·구어·오타)이 그대로 들어가고, 바라는 점이 없을 때
제목이 뜻 고르기 선택지 문장이 되어 불편과 동떨어져 보였다(화면 시험에서 확인).

지키는 것
- 숫자를 쓰지 않는다. 근거 수치·출처는 '근거 자료' 칸에만 엔진 값 그대로 들어간다(이 모듈은 그 칸을 쓰지 않는다).
  결과에 입력에 없던 숫자가 있으면 쓰지 않고 틀로 돌아간다.
- 사용자가 말하지 않은 사실(장소·날짜·인원·사건)을 지어내지 않는다. 장소는 고른 읍면동만.
- 개인정보: 보내기 전에 가린 글만 보내고(○○), 받은 글도 한 번 더 가린다.
- Claude 를 못 쓰면(유료 호출 꺼짐·키 없음·오류) 틀 글을 그대로 쓰고 이유를 알린다. 호출 규칙은
  dialect_normalizer/llm.py 와 같다(ALLOW_PAID_CALLS=1 일 때만 실제 호출, 사용량 기록, 같은 입력은 다시 부르지 않음).
"""
from __future__ import annotations

import re

TITLE_MAX = 45                       # 국민신문고 작성 요령의 '한눈에 보이는 제목'. 창구 제한(200자)과 별개로 짧게
MAX_TOKENS = 1500

SYSTEM = """너는 제주 도민의 생활 불편을 행정기관에 내는 민원서 글로 다듬는 도우미다.
입력에는 도민이 하신 말씀(제주어·구어·오타가 섞일 수 있음), 엔진이 이해한 내용, 사용자가 고른 뜻, 바라는 점,
민원서에 함께 붙는 근거 자료의 이름이 들어 있다. 제목·민원 내용·요청 사항 세 칸을 쓴다.

규칙
- 표준어 격식체(합니다체)로 공손하고 간결하게 쓴다. 제주어·구어·오타는 뜻을 살려 표준어로 바르게 고친다.
- 하신 말씀과 이해한 내용에 있는 사실만 쓴다. 장소·날짜·사람 수·사건을 새로 지어내지 않는다.
- 숫자를 쓰지 않는다(읍면동 이름에 든 숫자는 괜찮다). 근거 수치는 민원서의 '근거 자료' 칸에 따로 붙는다.
  근거 자료가 있으면 '관련 공공데이터를 아래에 붙였습니다'처럼 있다는 것만 말하고 내용·수치는 옮기지 않는다.
- '○○'는 가린 개인정보다. 그대로 두고, 이름·연락처·주소를 새로 쓰지 않는다.
- 장소는 입력의 '장소'만 쓴다.

칸별
- title: 한 줄. '장소 + 핵심 불편 + 요청' (예: "구좌읍 학교 주변 CCTV 설치 요청"). 45자 이내. 끝은 '요청' 또는 '건의'.
- content: 민원 내용 2~4문장. 어떤 불편이 있고 누가 어떻게 걱정하거나 어려운지. 장소는 제목에 있으니 되풀이하지 않아도 된다.
- request: 요청 사항 1~2문장. 바라는 점이 있으면 그것을, 없으면 불편을 해결하는 조치를 '검토해 주시기 바랍니다'처럼 정중히.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "content": {"type": "string"},
        "request": {"type": "string"},
    },
    "required": ["title", "content", "request"],
    "additionalProperties": False,
}


class WriteFailed(Exception):
    """다듬은 글을 쓸 수 없음(호출 실패 또는 검사 통과 못 함). 메시지는 사용자에게 보여 줄 수 있는 짧은 이유."""


def build_prompt(query: dict, places: list[str], source: str, evidence_names: list[str]) -> str:
    claims = [c["text"] for c in query.get("claims") or [] if c.get("text")]
    lines = [
        f"장소: {', '.join(places) or '정하지 않음'}",
        f"민원 종류: {query.get('complaint_type') or '기타'}",
        f"하신 말씀: {source or '(없음)'}",
        f"이해한 내용: {query.get('normalized_text') or '(없음)'}",
        "고른 뜻:" + "".join(f"\n- {c}" for c in claims) if claims else "고른 뜻: (없음)",
        f"바라는 점: {query.get('user_request') or '(말하지 않음)'}",
        "함께 붙는 근거 자료:" + "".join(f"\n- {n}" for n in evidence_names) if evidence_names else "함께 붙는 근거 자료: (없음)",
    ]
    return "\n".join(lines)


_DIGITS = re.compile(r"\d+")


def allowed_text(query: dict, places: list[str], source: str) -> str:
    """숫자를 써도 되는 글: 사용자가 한 말과 고른 뜻, 장소 이름. 근거 자료 이름('최근 12개월', '65세 이상')은 넣지 않는다
    (근거 수치는 '근거 자료' 칸에만 둔다)."""
    claims = " ".join(c.get("text", "") for c in query.get("claims") or [])
    return " ".join([" ".join(places), source or "", query.get("normalized_text") or "", claims,
                     query.get("user_request") or ""])


def check(out: dict, allowed: str, places: list[str]) -> dict:
    """받은 글 검사·정리. 못 쓰면 WriteFailed. allowed: 숫자를 써도 되는 글(allowed_text)."""
    fields = {k: " ".join(str(out.get(k) or "").split()) if k == "title" else str(out.get(k) or "").strip()
              for k in ("title", "content", "request")}
    empty = [k for k, v in fields.items() if not v]
    if empty:
        raise WriteFailed(f"빈 칸이 있음({', '.join(empty)})")
    new = sorted({d for v in fields.values() for d in _DIGITS.findall(v)} - set(_DIGITS.findall(allowed)))
    if new:
        raise WriteFailed(f"입력에 없던 숫자가 나옴({', '.join(new)})")
    title = fields["title"]
    if places and places[0] != "제주 전체" and not any(p in title for p in places):
        title = f"{places[0]} {title}"
    if len(title) > TITLE_MAX:
        title = title[:TITLE_MAX - 1].rstrip() + "…"
    fields["title"] = title
    return fields


def write_sections(query: dict, places: list[str], source: str, evidence_names: list[str]) -> dict:
    """{title, content, request} (개인정보 가림·검사 끝난 글). 못 쓰면 WriteFailed.

    query 는 엔진이 가린 질의 사본, source 는 가린 원문, evidence_names 는 민원서에 넣기로 고른 근거 이름.
    """
    from complaint_draft.draft import mask
    try:
        import llm                                         # dialect_normalizer (Agent 를 불러오면 경로가 잡힌다)
    except ImportError as e:
        raise WriteFailed("문장 다듬기 모듈을 불러오지 못함") from e
    source = mask(source)[0]
    prompt = build_prompt(query, places, source, evidence_names)
    try:
        out = llm.call_json(SYSTEM, prompt, SCHEMA, purpose="draft_writer", max_tokens=MAX_TOKENS)
    except llm.PaidCallNotAllowed as e:
        raise WriteFailed("유료 호출이 꺼져 있음") from e
    except llm.LLMError as e:
        raise WriteFailed(str(e)[:80]) from e
    except Exception as e:                                 # noqa: BLE001  SDK 오류(연결·한도·거절 등)
        raise WriteFailed(f"Claude 호출 실패({type(e).__name__})") from e
    if not isinstance(out, dict):
        raise WriteFailed("응답 형식이 맞지 않음")
    fields = check(out, allowed_text(query, places, source), places)
    return {k: mask(v)[0] if k == "title" else "\n".join(mask(line)[0] for line in v.splitlines())
            for k, v in fields.items()}

