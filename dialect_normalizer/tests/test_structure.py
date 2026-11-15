"""구조화 모듈 테스트 (네트워크·유료 호출 없음: LLM 응답을 가짜로 바꿔서 검사)"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import structure as S  # noqa: E402


def fake_llm(out):
    def _call(system, user, schema, purpose="", **kw):
        return out
    return _call


BASE_OUT = {
    "complaint_type": "교통", "location_mention": "구좌읍", "emd_name": "구좌읍",
    "claims": [{"text": "버스가 줄었다"}, {"text": "노인의 병원 접근이 어렵다"}],
    "targets": ["버스", "병원", "노인"], "user_request": "버스 증차",
    "period_from": "", "period_to": "",
}


@pytest.fixture
def run(monkeypatch):
    def _run(out, text="구좌읍 버스가 줄어서 병원 가기 힘들어요", **kw):
        monkeypatch.setattr(S.llm, "call_json", fake_llm(out))
        return S.structure(text, use_normalize=False, **kw)
    return _run


def test_mask_pii():
    t, found = S.mask_pii("김철수 씨 집 앞이에요 010-1234-5678 노형로 123 아저씨가 그래요 오늘날씨가 덥다")
    assert "김철수" not in t and "010-1234-5678" not in t and "노형로 ○○" in t
    assert "아저씨" in t and "오늘날씨" in t
    assert set(found) >= {"이름", "전화번호", "건물번호"}


def test_ready_query_matches_contract(run):
    r = run(BASE_OUT)
    assert r["status"] == "ready", r["questions"]
    assert r["schema_errors"] == []
    q = r["query"]
    assert q["claims"][0] == {"id": "c1", "text": "버스가 줄었다"}
    assert q["location"] == {"emd_name": "구좌읍", "emd_code": ""}
    assert "raw_text" not in q and "period" not in q


def test_missing_location_asks_then_update(run):
    r = run({**BASE_OUT, "location_mention": "", "emd_name": ""}, text="버스가 줄었어요")
    assert r["status"] == "need_input"
    assert any(x["field"] == "emd_name" for x in r["questions"])
    r2 = S.update(r, emd_name="구좌")
    assert r2["query"]["location"]["emd_name"] == "구좌읍"
    assert r2["status"] == "ready"


def test_short_emd_mention_is_resolved(run):
    r = run({**BASE_OUT, "location_mention": "노형", "emd_name": ""}, text="노형 쪽 버스가 줄었어요")
    assert r["query"]["location"]["emd_name"] == "노형동"


def test_period_validation(run):
    r = run({**BASE_OUT, "period_from": "2025-01", "period_to": "2026-10"})
    assert r["query"]["period"] == {"from": "2025-01", "to": "2026-10"}
    r = run({**BASE_OUT, "period_from": "작년", "period_to": "2026-13"})
    assert "period" not in r["query"]


def test_no_user_request_is_optional_question(run):
    r = run({**BASE_OUT, "user_request": ""})
    assert "user_request" not in r["query"]
    assert r["status"] == "ready"
    assert any(x["field"] == "user_request" and x.get("optional") for x in r["questions"])


def test_include_raw_is_masked(run):
    r = run(BASE_OUT, text="구좌읍 김철수 씨네 앞 버스 010-1111-2222", include_raw=True)
    assert "김철수" not in r["query"]["raw_text"] and "010" not in r["query"]["raw_text"]


def test_dialect_confidence():
    norm = {"changes": [{"type": "사전"}, {"type": "사전"}, {"type": "추정"}],
            "ask_user": ["'a'를 b로?", "'c'는 무슨 뜻?"]}
    assert S.dialect_confidence(norm) == 0.5          # 사전 2 / (변경 3 + 모르는 단어 1)
    assert S.dialect_confidence({"changes": [], "ask_user": []}) == 1.0


@pytest.mark.skipif(not S.FIXTURES.exists(), reason="evidence_engine/tests/fixtures 없음")
def test_fixtures_valid_against_contract():
    for fp in sorted(S.FIXTURES.glob("q*.json")):
        assert S.validate(json.loads(fp.read_text(encoding="utf-8"))) == [], fp.name


def test_emd_in_text_overrides_llm(run):
    r = run({**BASE_OUT, "location_mention": "", "emd_name": "연동"}, text="노형동 학교 앞 교차로에서 사고가 자주 나요")
    assert r["query"]["location"]["emd_name"] == "노형동"


def test_ungrounded_request_becomes_suggestion(run):
    r = run({**BASE_OUT, "user_request": "버스 증차"}, text="구좌읍 버스가 줄어서 병원 가기 힘들어요")
    assert "user_request" not in r["query"]
    q = [x for x in r["questions"] if x["field"] == "user_request"][0]
    assert q["suggestion"] == "버스 증차" and q.get("optional")
    r2 = S.update(r, user_request="버스 증차")
    assert r2["query"]["user_request"] == "버스 증차"


def test_grounded_request_kept(run):
    r = run({**BASE_OUT, "user_request": "버스 늘려"}, text="구좌읍 버스가 줄었어요 버스 좀 늘려 주세요")
    assert r["query"]["user_request"] == "버스 늘려"


def test_place_name_question_dropped():
    norm = {"standard": "x", "changes": [], "ask_user": ["'구좌읍'은 어떤 뜻인가요?", "'파불민'은 무슨 뜻인가요?"]}
    assert S.drop_place_questions(norm)["ask_user"] == ["'파불민'은 무슨 뜻인가요?"]
