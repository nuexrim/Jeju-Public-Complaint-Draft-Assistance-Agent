"""초안 → 토글 화면 HTML. 두 버전을 한 페이지에 모두 그려 두고 CSS 클래스만 바꾼다(다시 그리지 않음)."""
from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "view.html"
ROOT_PX = {"작게": 15, "보통": 17, "크게": 20}       # 글자 크기 단추. view.html 의 글자는 모두 rem 이라 이 값에 따라 커진다.


def render_html(draft: dict, size: str = "크게", mode: str = "std", top: bool = True) -> str:
    """mode: 처음 보기('std' 표준어 | 'dia' 방언). top=False 면 안쪽 제목·전환 단추를 숨긴다(화면이 바깥에 따로 둠)."""
    from complaint_draft.draft import plain_text
    data = json.dumps({**draft, "plain_text": plain_text(draft, "standard")}, ensure_ascii=False)   # .txt 받기용
    # <script> 안에 넣으므로 태그가 닫히지 않게 막는다.
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__ROOT_PX__", str(ROOT_PX.get(size, ROOT_PX["크게"])))
    html = html.replace("__MODE__", "dia" if mode == "dia" else "std").replace("__HIDE_TOP__", "false" if top else "true")
    return html.replace("__DRAFT_JSON__", data)
