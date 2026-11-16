"""민원서 초안: 섹션 대응, 방언본 원칙, 숫자 그대로, 제외 항목, mock 처리, 화면 HTML."""
import copy
import json
from pathlib import Path

import pytest

from complaint_draft.draft import SECTION_IDS, build_draft, format_value, load_handoff, plain_text, validate_draft
from complaint_draft.view import render_html

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


@pytest.fixture(params=["supported", "mixed", "mock"])
def sample(request):
    return load_handoff(SAMPLES / f"{request.param}.json")


def test_every_sample_is_valid_and_sections_line_up(sample):
    d = build_draft(sample)
    assert validate_draft(d) == []
    assert [s["id"] for s in d["sections"]] == SECTION_IDS


def test_dialect_uses_only_source_text_never_generated():
    h = load_handoff(SAMPLES / "supported.json")
    d = build_draft(h)
    by = {s["id"]: s for s in d["sections"]}
    assert by["content"]["dialect_source"] == "original"
    assert by["content"]["dialect"] == h["source_text"]
    for sid in ("title", "request", "evidence"):
        assert by[sid]["dialect_source"] == "standard_fallback"
        assert by[sid]["dialect"] == by[sid]["standard"]


def test_no_source_text_means_every_section_falls_back():
    d = build_draft(load_handoff(SAMPLES / "mixed.json"))
    assert all(s["dialect_source"] == "standard_fallback" and s["dialect"] == s["standard"] for s in d["sections"])
    assert any("원문" in w for w in d["warnings"])


def test_source_text_argument_overrides_and_is_masked():
    h = load_handoff(SAMPLES / "supported.json")
    d = build_draft(h, source_text="김철수 씨가 010-1234-5678로 연락해도 버스가 안 와마씸")
    content = next(s for s in d["sections"] if s["id"] == "content")
    assert "010-1234-5678" not in content["dialect"] and "김철수" not in content["dialect"]
    assert d["pii_masked"]


def test_numbers_and_sources_come_only_from_package():
    h = load_handoff(SAMPLES / "supported.json")
    d = build_draft(h)
    ev_text = next(s for s in d["sections"] if s["id"] == "evidence")["standard"]
    for e in h["package"]["evidence"]:
        assert format_value(e["value"], e["unit"]) in ev_text
        for src in e["sources"]:
            assert src["dataset"] in ev_text and src["as_of"] in ev_text
    assert [e["value"] for e in d["evidence"]] == [e["value"] for e in h["package"]["evidence"]]


def test_only_supported_and_partial_go_into_evidence():
    d = build_draft(load_handoff(SAMPLES / "mixed.json"))
    assert [e["claim_id"] for e in d["evidence"]] == ["c1"]
    reasons = {(x["kind"], x["claim_id"]): x["reason"] for x in d["excluded"]}
    assert reasons[("evidence", "c2")] == "자료와 다름"
    assert reasons[("evidence", "c3")] == "자료로 확인 불가"
    assert ("gap", None) in reasons
    ev_text = next(s for s in d["sections"] if s["id"] == "evidence")["standard"]
    assert "CCTV" not in ev_text


def test_partial_is_marked():
    d = build_draft(load_handoff(SAMPLES / "supported.json"))
    ev_text = next(s for s in d["sections"] if s["id"] == "evidence")["standard"]
    assert "일부만 확인된 근거" in ev_text


def test_no_usable_evidence_says_so():
    h = load_handoff(SAMPLES / "mixed.json")
    h["package"]["evidence"] = [e for e in h["package"]["evidence"] if e["verdict"] != "supported"]
    d = build_draft(h)
    assert d["evidence"] == []
    assert next(s for s in d["sections"] if s["id"] == "evidence")["standard"] == "데이터로 확인된 근거는 없습니다."


def test_mock_engine_blocks_copy():
    d = build_draft(load_handoff(SAMPLES / "mock.json"))
    assert d["copy_allowed"] is False
    assert any("형식 예시" in w for w in d["warnings"])
    assert not any("mock" in n for n in d["notices"])


def test_rules_structuring_warns():
    d = build_draft(load_handoff(SAMPLES / "mixed.json"))
    assert any("규칙" in w for w in d["warnings"])


def test_request_default_and_user_request():
    h = load_handoff(SAMPLES / "supported.json")
    assert "버스 증차" in next(s for s in build_draft(h)["sections"] if s["id"] == "request")["standard"]
    h2 = copy.deepcopy(h)
    h2["query"].pop("user_request")
    assert "개선 방안" in next(s for s in build_draft(h2)["sections"] if s["id"] == "request")["standard"]


def test_title_does_not_repeat_place():
    h = load_handoff(SAMPLES / "supported.json")
    h["query"].pop("targets")
    title = build_draft(h)["sections"][0]["standard"]
    assert title.count("구좌읍") == 1


def test_plain_text_versions():
    d = build_draft(load_handoff(SAMPLES / "supported.json"))
    assert "[민원 내용]" in plain_text(d)
    assert "줄어부난" in plain_text(d, "dialect") and "줄어부난" not in plain_text(d)


def test_html_embeds_draft_safely():
    h = load_handoff(SAMPLES / "supported.json")
    d = build_draft(h, source_text="</script><script>alert(1)</script> 버스가 안 와마씸")
    html = render_html(d)
    script = html.split("const DRAFT = ", 1)[1].split("\n", 1)[0]
    assert "</script>" not in script and "<script>" not in script
    embedded = json.loads(script.rstrip(";"))
    assert embedded["sections"][1]["dialect"].startswith("</script>")
