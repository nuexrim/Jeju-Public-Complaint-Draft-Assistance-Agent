"""초안 → 토글 화면 HTML. 두 버전을 한 페이지에 모두 그려 두고 CSS 클래스만 바꾼다(다시 그리지 않음)."""
from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "view.html"
ROOT_PX = {"작게": 15, "보통": 17, "크게": 20}       # 글자 크기 단추. view.html 의 글자는 모두 rem 이라 이 값에 따라 커진다.


def render_html(draft: dict, size: str = "크게") -> str:
    data = json.dumps(draft, ensure_ascii=False)
    # <script> 안에 넣으므로 태그가 닫히지 않게 막는다.
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__ROOT_PX__", str(ROOT_PX.get(size, ROOT_PX["크게"])))
    return html.replace("__DRAFT_JSON__", data)
