"""민원서 초안: 섹션 대응, 방언본 원칙, 숫자 그대로, 제외 항목, mock 처리, 화면 HTML."""
import copy
import json
from pathlib import Path

import pytest

from complaint_draft.draft import SECTION_IDS, apply_edits, build_draft, friendly, format_value, load_handoff, plain_text, validate_draft
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
    assert not any("원문" in w for w in d["warnings"])      # 방언 보기를 뺐으므로 원문 없음 경고도 없다


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
    assert plain_text(d).startswith("제목: ") and "[불편 사항]" in plain_text(d)
    assert "줄어부난" in plain_text(d, "dialect") and "줄어부난" not in plain_text(d)


def test_html_embeds_draft_safely():
    h = load_handoff(SAMPLES / "supported.json")
    d = build_draft(h, source_text="</script><script>alert(1)</script> 버스가 안 와마씸")
    html = render_html(d)
    script = html.split("const DRAFT = ", 1)[1].split("\n", 1)[0]
    assert "</script>" not in script and "<script>" not in script
    embedded = json.loads(script.rstrip(";"))
    assert embedded["sections"][1]["dialect"].startswith("</script>")


def test_submission_has_title_and_body_for_the_two_form_fields():
    h = load_handoff(SAMPLES / "supported.json")
    d = build_draft(h)
    sub = d["submission"]
    assert sub["title"] == "구좌읍 버스 증차 요청" == d["sections"][0]["standard"]
    order = [sub["body"].index(x) for x in ("[발생 장소]", "[불편 사항]", "[요청 사항]", "[근거 자료]")]
    assert order == sorted(order)
    assert "제주특별자치도 제주시 구좌읍" in sub["body"] and sub["agency"] == "제주특별자치도 제주시"
    assert sub["title"] not in sub["body"]                     # 제목은 제목 칸에만
    for sec in d["sections"][1:]:
        assert sec["standard"] in sub["body"]                  # 화면에 보인 글과 붙여 넣는 글이 같다
    assert sub["title_chars"] == len(sub["title"]) and sub["body_chars"] == len(sub["body"])
    assert h["source_text"] not in sub["body"]                 # 제출 글에 방언 원문은 들어가지 않는다


def test_title_without_request_uses_topic_and_stays_short():
    h = load_handoff(SAMPLES / "mixed.json")
    assert build_draft(h)["submission"]["title"] == "노형동 클린하우스·CCTV 관련 불편 개선 요청"
    h["query"]["user_request"] = "클린하우스를 더 설치해 주세요"
    d = build_draft(h)
    assert d["submission"]["title"] == "노형동 클린하우스를 더 설치해 주시기 바랍니다"            # 이미 요청으로 끝나면 덧붙이지 않음
    assert "[요청 사항]\n클린하우스를 더 설치해 주시기 바랍니다." in d["submission"]["body"]      # 문장 요청은 틀 없이 그대로
    h["query"]["user_request"] = "가" * 200
    assert len(build_draft(h)["submission"]["title"]) <= 61


def test_seogwipo_and_unknown_place():
    h = load_handoff(SAMPLES / "supported.json")
    h["query"]["location"]["emd_name"] = "성산읍"
    assert build_draft(h)["submission"]["agency"] == "제주특별자치도 서귀포시"
    h["query"]["location"]["emd_name"] = ""
    sub = build_draft(h)["submission"]
    assert "[발생 장소]" not in sub["body"] and sub["agency"] == "제주특별자치도"


def test_html_has_two_copy_buttons():
    html = render_html(build_draft(load_handoff(SAMPLES / "supported.json")))
    assert 'data-part="title"' in html and 'data-part="body"' in html and 'id="print"' in html


def test_html_font_size_follows_setting():
    d = build_draft(load_handoff(SAMPLES / "supported.json"))
    assert 'style="font-size: 20px"' in render_html(d) and 'style="font-size: 17px"' in render_html(d, "보통")


def test_style_escapes_user_text():
    from complaint_draft import style
    bad = '<script>alert(1)</script>'
    for html in (style.said_card(bad), style.understood_card([("장소", bad, True)]), style.ask_head(bad), style.title(bad, bad)):
        assert "<script>" not in html and "&lt;script&gt;" in html
    assert style.stepper(1).count("aria-current") == 1


def test_edits_change_standard_and_submission_but_not_evidence_or_original():
    base = build_draft(load_handoff(SAMPLES / "supported.json"))
    before = copy.deepcopy(base)
    d = apply_edits(base, {"title": "구좌읍 버스 노선 증편 요청", "content": "버스가 하루 세 번만 다닙니다.\n어르신들이 병원에 가기 어렵습니다.",
                           "request": "버스를 늘려 주세요.", "evidence": "지어낸 숫자 99%"})
    assert base == before                                           # 받은 초안은 바꾸지 않는다
    by = {s["id"]: s for s in d["sections"]}
    assert by["title"]["standard"] == "구좌읍 버스 노선 증편 요청" and by["title"]["edited"]
    assert by["title"]["dialect"] == by["title"]["standard"]        # 표준어를 보여 주던 섹션은 따라간다
    assert by["content"]["dialect"] == before["sections"][1]["dialect"]   # 원문은 그대로
    assert by["content"]["standard"].count("\n") == 1               # 줄바꿈 유지
    assert by["evidence"]["standard"] == before["sections"][3]["standard"] and "edited" not in by["evidence"]
    assert d["evidence"] == before["evidence"]
    sub = d["submission"]
    assert sub["title"] == "구좌읍 버스 노선 증편 요청" and "버스를 늘려 주세요." in sub["body"]
    assert "지어낸 숫자" not in sub["body"] and before["sections"][3]["standard"] in sub["body"]
    assert sub["body_chars"] == len(sub["body"]) and validate_draft(d) == []


def test_edits_are_masked_and_empty_edit_keeps_original():
    base = build_draft(load_handoff(SAMPLES / "supported.json"))
    d = apply_edits(base, {"content": "연락은 010-1234-5678 로 주세요", "title": "   "})
    by = {s["id"]: s for s in d["sections"]}
    assert "010-1234-5678" not in by["content"]["standard"] and "010-1234-5678" not in d["submission"]["body"]
    assert d["pii_masked"]
    assert by["title"]["standard"] == base["sections"][0]["standard"] and "edited" not in by["title"]
    assert apply_edits(base, None) == base and apply_edits(base, {}) == base


def test_friendly_strips_developer_codes_only():
    assert friendly("Claude 구조화를 쓰지 못해 규칙으로 정리했어요 (PaidCallNotAllowed)") == "Claude 구조화를 쓰지 못해 규칙으로 정리했어요"
    assert friendly("'가나동'에 해당하는 읍·면·동을 찾지 못했어요") == "'가나동'에 해당하는 읍·면·동을 찾지 못했어요"
    assert friendly("비교 (읍·면 12곳)") == "비교 (읍·면 12곳)"
    d = build_draft(load_handoff(SAMPLES / "mixed.json"))
    assert d["notices"] == ["Claude 구조화를 쓰지 못해 규칙으로 정리했어요"]


def test_content_does_not_repeat_place_sentence():
    d = build_draft(load_handoff(SAMPLES / "supported.json"))
    assert d["submission"]["body"].count("구좌읍") == 3              # 발생 장소 1 + 주장 문장 2


def test_standard_version_is_formal_and_dialect_is_untouched():
    h = load_handoff(SAMPLES / "supported.json")
    d = build_draft(h)
    by = {s["id"]: s for s in d["sections"]}
    std = by["content"]["standard"]
    assert "매우 힘듭니다." in std and "힘들어요" not in std
    assert "- 구좌읍 정류소에서 버스를 타는 사람이 1년 전보다 줄었습니다." in std
    assert "- 구좌읍 어르신 수에 비해 의원이 적습니다." in std
    assert by["content"]["dialect"] == h["source_text"]                 # 원문은 그대로
    assert d["evidence"][0]["claim"].endswith("줄었다")                  # 자료(주장 원문)는 바꾸지 않는다
    assert not any("격식체" in w for w in d["warnings"])


def test_leftover_informal_ending_is_flagged():
    h = load_handoff(SAMPLES / "supported.json")
    h["query"]["normalized_text"] = "버스가 정말 안 오네요"
    assert any("격식체" in w for w in build_draft(h)["warnings"])


# ---------------------------------------------------------------- 근거 고르기 · 오래된 자료 · 글자 수 · 남은 제주어
def test_is_stale_reads_common_date_shapes():
    from datetime import date
    from complaint_draft.draft import is_stale
    today = date(2026, 11, 17)
    assert is_stale("2024-10", today) and is_stale("2023", today) and is_stale("2024-01-31", today)
    assert not is_stale("2024-12", today) and not is_stale("2026-09", today) and not is_stale("모름", today) and not is_stale("0000-00", today)


def test_exclude_moves_picked_out_evidence_to_excluded():
    h = load_handoff(SAMPLES / "supported.json")
    d = build_draft(h, exclude=[0])
    assert [e["index"] for e in d["evidence"]] == list(range(1, len(h["package"]["evidence"])))
    assert any(x["kind"] == "user" and x["reason"] == "직접 뺀 근거" for x in d["excluded"])
    assert validate_draft(d) == []


def test_evidence_choices_marks_usable_and_keeps_numbers():
    from complaint_draft.draft import evidence_choices
    h = load_handoff(SAMPLES / "mixed.json")
    items = evidence_choices(h["package"], h["query"])
    assert [it["usable"] for it in items] == [e["verdict"] in ("supported", "partial") and e["value"] is not None
                                              for e in h["package"]["evidence"]]
    assert all(it["value"] == e["value"] for it, e in zip(items, h["package"]["evidence"]))


def test_submission_carries_portal_limits():
    sub = build_draft(load_handoff(SAMPLES / "supported.json"))["submission"]
    assert sub["title_limit"] == 200 and sub["body_limit"] == 40000


def test_title_over_limit_is_warned_after_edit():
    d = build_draft(load_handoff(SAMPLES / "supported.json"))
    d2 = apply_edits(d, {"title": "가" * 201})
    assert any("제목 글자 수" in w for w in d2["warnings"])


def test_leftover_dialect_words_are_flagged_and_cleared_by_edit(monkeypatch):
    from complaint_draft import draft as D
    monkeypatch.setattr(D, "_dialect", {"줄어부난", "할망덜"})
    h = load_handoff(SAMPLES / "supported.json")
    h["query"]["normalized_text"] = "버스가 확 줄어부난 할망덜이 병원 가기가 힘들어요."
    d = build_draft(h)
    assert d["dialect_words"] == ["줄어부난", "할망덜"]
    assert any(w.startswith("표준어로 바뀌지 않은 제주어 낱말") for w in d["warnings"])
    content = next(s for s in d["sections"] if s["id"] == "content")["standard"]
    fixed = apply_edits(d, {"content": content.replace("줄어부난", "줄어서").replace("할망덜이", "할머니들이")})
    assert fixed["dialect_words"] == [] and not any(w.startswith("표준어로 바뀌지 않은") for w in fixed["warnings"])


def test_no_dialect_dictionary_means_no_check(monkeypatch, tmp_path):
    from complaint_draft import draft as D
    monkeypatch.setattr(D, "_dialect", None)
    monkeypatch.setattr(D, "DIALECT_DICT", tmp_path / "없음.json")
    assert D.leftover_dialect("버스가 확 줄어부난") == []


def test_html_shows_counts_and_marks_dialect_words(monkeypatch):
    from complaint_draft import draft as D
    monkeypatch.setattr(D, "_dialect", {"줄어부난"})
    h = load_handoff(SAMPLES / "supported.json")
    h["query"]["normalized_text"] = "버스가 확 줄어부난 힘들어요."
    html = render_html(build_draft(h))
    assert '"dialect_words": ["줄어부난"]' in html and "dia-word" in html and "title_limit" in html


def test_style_evidence_card_escapes_and_shows_stale():
    from complaint_draft import style
    card = style.evidence_card({"verdict": "partial", "verdict_ko": "일부만 확인됨", "indicator": "<b>x</b>", "value_text": "3 곳",
                                "region_compare": None, "claim": "", "sources": [], "as_of": "2021-12", "stale": True})
    assert "&lt;b&gt;" in card and "오래된 자료" in card


def test_html_font_size_has_three_steps():
    d = build_draft(load_handoff(SAMPLES / "supported.json"))
    assert 'style="font-size: 15px"' in render_html(d, "작게")


def test_no_evidence_note_says_draft_is_still_possible():
    from complaint_draft import style
    html = style.no_evidence_note([{"verdict": "contradicted"}, {"verdict": "contradicted"}, {"verdict": "unverifiable"}])
    assert "근거 없이도 민원서는 만들 수 있어요" in html
    assert html.count("자료와 다름") == 1 and "확인 불가" in html


def test_html_no_evidence_card_reassures():
    h = load_handoff(SAMPLES / "supported.json")
    h["package"]["evidence"] = []
    assert "근거 없이도 민원서는 낼 수 있어요" in render_html(build_draft(h))


def test_no_evidence_means_no_evidence_block_in_submission():
    h = load_handoff(SAMPLES / "mixed.json")
    h["package"]["evidence"] = [e for e in h["package"]["evidence"] if e["verdict"] != "supported"]
    d = build_draft(h)
    assert "[근거 자료]" not in d["submission"]["body"] and "[요청 사항]" in d["submission"]["body"]
    assert validate_draft(d) == []
    d2 = apply_edits(d, {"request": "버스를 늘려 주세요"})
    assert "[근거 자료]" not in d2["submission"]["body"]
    assert "[근거 자료]" in build_draft(load_handoff(SAMPLES / "supported.json"))["submission"]["body"]


def test_html_hides_evidence_section_when_none():
    h = load_handoff(SAMPLES / "supported.json")
    h["package"]["evidence"] = []
    assert 's.id === "evidence" && !DRAFT.evidence.length' in render_html(build_draft(h))


def test_title_without_request_uses_chosen_meaning():
    h = load_handoff(SAMPLES / "supported.json")
    h["query"].pop("user_request")
    assert build_draft(h)["submission"]["title"] == "구좌읍 불편 개선 요청: 정류소에서 버스를 타는 사람이 1년 전보다 줄었다 외 1건"
    h["query"]["claims"] = h["query"]["claims"][1:]
    assert build_draft(h)["submission"]["title"] == "구좌읍 불편 개선 요청: 어르신 수에 비해 의원이 적다"
    h["query"]["claims"] = [{"id": "c1", "text": "버스 배차가 줄었다", "indicator_id": "gap:bus_service"}]
    assert "배차" not in build_draft(h)["submission"]["title"]          # 자료 없는 뜻은 제목에 쓰지 않고 대상으로


def test_html_has_no_dialect_toggle():
    html = render_html(build_draft(load_handoff(SAMPLES / "supported.json")))
    assert 'data-mode="dia"' not in html and "하신 말씀 그대로" not in html
