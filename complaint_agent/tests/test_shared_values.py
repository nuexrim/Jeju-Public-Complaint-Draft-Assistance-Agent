"""여러 모듈이 따로 들고 있는 같은 값이 어긋나지 않는지. 한쪽만 바꾸면 오류 없이 결과만 달라지므로 테스트로 묶는다.

화면(complaint_draft)의 flow·draft·templates 는 근거 엔진 없이도 열리도록(예시·파일로 보기) 값을 복사해 둔다.
"""
import ast
from pathlib import Path

import complaint_agent.pipeline as P
from complaint_agent import conversation
from complaint_draft import draft, flow, templates
from package.labels import VERDICT_LABEL

ROOT = Path(__file__).resolve().parents[2]


def _app_constant(name: str):
    """app.py 는 불러오면 Streamlit 화면이 그려지므로 글자로 읽어 상수만 꺼낸다."""
    tree = ast.parse((ROOT / "complaint_draft" / "app.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


def test_verdict_names_are_the_same_everywhere():
    assert templates.VERDICT_KO == VERDICT_LABEL
    assert conversation.VERDICT_KO == VERDICT_LABEL


def test_whole_jeju_value():
    assert flow.EMD_ALL == P.EMD_ALL


def test_pii_placeholder():
    assert draft.PII_PLACEHOLDER == P.S.PII_PLACEHOLDER


def test_topic_fallback_matches_engine_topics():
    assert _app_constant("TOPIC_FALLBACK") == P.S.topic_names()
