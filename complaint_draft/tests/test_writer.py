"""문장 다듬기(writer.py): 가짜 Claude 로 검사 규칙을 본다(네트워크·유료 호출 없음)."""
import copy
import json
from pathlib import Path

import pytest

import complaint_agent.pipeline as P  # noqa: F401  (dialect_normalizer 의 llm 경로를 올린다)
import llm
from complaint_draft import writer
from complaint_draft.draft import build_draft

SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "samples" / "supported.json").read_text(encoding="utf-8"))
GOOD = {"title": "구좌읍 버스 증차 요청",
        "content": "버스가 크게 줄어 어르신들이 병원에 가기가 매우 어렵습니다.\n관련 공공데이터를 아래에 붙였습니다.",
        "request": "버스를 늘려 주시기 바랍니다."}


@pytest.fixture
def fake(monkeypatch):
    sent = {}

    def call_json(system, user, schema, purpose="", **kw):
        sent.update(system=system, user=user, purpose=purpose)
        return copy.deepcopy(sent.get("reply", GOOD))
    monkeypatch.setattr(llm, "call_json", call_json)
    return sent


def test_claude_writes_three_sections_but_not_evidence(fake):
    d = build_draft(SAMPLE, write=writer.write_sections)
    std = {s["id"]: s["standard"] for s in d["sections"]}
    assert d["written_by"] == "claude" and std["title"] == GOOD["title"] and std["request"] == GOOD["request"]
    assert "-12.44" in std["evidence"]                       # 근거 칸은 엔진 값 그대로
    assert d["submission"]["title"] == GOOD["title"]
    assert not any("다듬지 못해" in w for w in d["warnings"])
    assert fake["purpose"] == "draft_writer"


def test_prompt_has_no_evidence_numbers_and_says_no_numbers(fake):
    build_draft(SAMPLE, write=writer.write_sections)
    assert "-12.4" not in fake["user"] and "1.27" not in fake["user"]
    assert "숫자를 쓰지 않는다" in fake["system"]
    assert "버스 정류소 이용 인원 증감률" in fake["user"]        # 근거는 이름만


def test_number_from_evidence_is_rejected(fake):
    fake["reply"] = {**GOOD, "content": "버스 이용이 12% 줄었습니다."}
    d = build_draft(SAMPLE, write=writer.write_sections)
    assert d["written_by"] == "template" and any("다듬지 못해" in w for w in d["warnings"])
    assert any("숫자" in n for n in d["notices"])


def test_number_the_user_said_is_allowed():
    q = {"claims": [{"text": "버스가 줄었다"}], "normalized_text": "3년째 버스가 줄었어요", "user_request": ""}
    out = writer.check({**GOOD, "content": "3년째 버스가 줄었습니다."}, writer.allowed_text(q, ["이도2동"], ""), ["이도2동"])
    assert "3년째" in out["content"]


def test_paid_calls_off_falls_back_to_template(monkeypatch):
    def blocked(*a, **k):
        raise llm.PaidCallNotAllowed("off")
    monkeypatch.setattr(llm, "call_json", blocked)
    d = build_draft(SAMPLE, write=writer.write_sections)
    assert d["written_by"] == "template" and any("유료 호출" in n for n in d["notices"])


def test_title_gets_place_and_is_short(fake):
    fake["reply"] = {**GOOD, "title": "버스 증차를 간곡히 요청드리며 어르신들의 병원 이용 불편을 해소해 주실 것을 건의"}
    d = build_draft(SAMPLE, write=writer.write_sections)
    title = d["sections"][0]["standard"]
    assert title.startswith("구좌읍 ") and len(title) <= writer.TITLE_MAX


def test_pii_in_reply_is_masked(fake):
    fake["reply"] = {**GOOD, "content": "연락은 010-1234-5678 로 주세요. 버스가 줄었습니다."}
    d = build_draft(SAMPLE, write=writer.write_sections)
    # 입력에 없던 숫자라 다듬은 글을 쓰지 않는다(개인정보가 섞여도 내보내지 않음)
    assert "010-1234-5678" not in json.dumps(d, ensure_ascii=False)


def test_masked_source_is_sent(fake):
    h = {**SAMPLE, "source_text": "김철수라고 합니다 010-9999-8888 버스가 줄어부난 힘들어마씸"}
    build_draft(h, write=writer.write_sections)
    assert "010-9999-8888" not in fake["user"] and "김철수" not in fake["user"]


def test_empty_section_falls_back(fake):
    fake["reply"] = {**GOOD, "request": " "}
    assert build_draft(SAMPLE, write=writer.write_sections)["written_by"] == "template"


def test_without_writer_nothing_changes():
    d = build_draft(SAMPLE)
    assert d["written_by"] == "template" and not any("다듬지 못해" in w for w in d["warnings"])
