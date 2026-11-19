"""화면에서 사용자 글을 마크다운으로 해석하지 않는지(링크·바깥 그림·수식 문법이 살아나지 않게)."""
import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app.py"


def _plain():
    """app.py 는 불러오면 화면을 그리므로, plain 함수와 정규식만 글자로 꺼내 실행한다."""
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    keep = [n for n in tree.body if (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_MD_SPECIAL" for t in n.targets))
            or (isinstance(n, ast.FunctionDef) and n.name == "plain")]
    ns = {"re": re}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "app_plain", "exec"), ns)
    return ns["plain"]


def test_markdown_syntax_is_escaped():
    plain = _plain()
    out = plain("![x](http://evil.example/a.png) [링크](javascript:alert(1)) **굵게** $x$")
    for raw in ("![", "](", "**", "$x$"):
        assert raw not in out, raw
    assert plain(None) == "" and plain("버스가 줄었어요") == "버스가 줄었어요"
