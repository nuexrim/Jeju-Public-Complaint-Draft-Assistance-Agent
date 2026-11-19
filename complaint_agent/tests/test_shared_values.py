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


def test_snapshot_date_is_the_same_for_install_and_zip():
    """설치.bat·제출 ZIP·엔진 설정의 스냅샷 날짜가 다르면 센터 PC 설치가 없는 폴더를 찾는다."""
    import re
    import yaml
    config = yaml.safe_load((ROOT / "evidence_engine" / "config.yaml").read_text(encoding="utf-8"))["snapshot"]
    bat = re.search(r"set SNAPSHOT=(\S+)", (ROOT / "설치.bat").read_text(encoding="utf-8")).group(1)
    tree = ast.parse((ROOT / "make_submission.py").read_text(encoding="utf-8"))
    zip_date = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and any(getattr(t, "id", "") == "SNAPSHOT" for t in n.targets))
    assert config == bat == zip_date


def test_run_bat_keeps_paid_calls_off_by_default():
    """실행.bat 의 Claude 스위치는 0 으로 둔다(사람이 직접 1 로 바꾼다)."""
    text = (ROOT / "실행.bat").read_text(encoding="utf-8")
    assert "set ALLOW_PAID_CALLS=0" in text and "set ALLOW_PAID_CALLS=1" not in text
