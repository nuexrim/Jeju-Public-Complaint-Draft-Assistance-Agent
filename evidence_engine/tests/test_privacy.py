"""개인정보 마스킹(CLAUDE.md 5장 9항): 입력의 이름·연락처·상세주소는 처리 전에 가리고, 빈칸 기록에 민원 문장을 남기지 않는다."""
import json
from pathlib import Path

import pytest

import run_engine
from package.privacy import mask_query, mask_text

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jeju.duckdb"


@pytest.mark.parametrize("text,expected", [
    ("김철수 씨가 010-1234-5678로 연락 달래요", "[이름] 씨가 [연락처]로 연락 달래요"),
    ("박영희님이 말했다", "[이름]님이 말했다"),
    ("연락처 01098765432, 메일 a.b@test.co.kr", "연락처 [연락처], 메일 [이메일]"),
    ("064-710-1234", "[연락처]"),
    ("주민번호 900101-1234567", "주민번호 [주민번호]"),
    ("제주시 한림읍 한림로 650 앞 정류장", "제주시 한림읍 한림로 [번지] 앞 정류장"),
    ("평화로2835", "평화로 [번지]"),
    ("연북로 12번길 3", "연북로 [번지]"),
    ("도남동 123-4번지 10호 근처", "도남동 [번지] 근처"),
    ("이도2동 현대아파트 101동 1203호 삽니다", "이도2동 현대아파트 [동호수] 삽니다"),
])
def test_personal_data_is_masked(text, expected):
    assert mask_text(text) == expected


@pytest.mark.parametrize("text", [
    "구좌읍 버스가 줄었다", "이도2동 클린하우스", "노인 1천 명당 의원 3곳", "2024-01부터 2026-10까지",
    "할머니가 병원 가기 힘들어요", "의사 선생님이 부족", "이장님께 말했는데", "버스기사님 친절",
    "제1동 2호선", "버스 20분 간격, 7번 노선", "중앙로 지하상가",
])
def test_ordinary_complaint_words_are_kept(text):
    assert mask_text(text) == text


def test_mask_query_covers_all_free_text_fields_and_drops_raw_text():
    q = {"request_id": "r", "raw_text": "원문 김철수 씨", "normalized_text": "김철수 씨 010-1111-2222",
         "complaint_type": "교통", "claims": [{"id": "c1", "text": "박영희님 집 앞 한림로 650 정류장이 없다"}],
         "location": {"emd_name": "한림읍"}, "targets": ["010-3333-4444"], "user_request": "a@b.kr 로 회신"}
    m = mask_query(q)
    dumped = json.dumps(m, ensure_ascii=False)
    for secret in ("김철수", "010-1111-2222", "박영희", "한림로 650", "010-3333-4444", "a@b.kr", "원문"):
        assert secret not in dumped
    assert "raw_text" not in m and m["location"]["emd_name"] == "한림읍"
    assert q["normalized_text"].startswith("김철수")          # 원본은 건드리지 않는다


PII_QUERY = {
    "request_id": "r-pii", "raw_text": "김철수 씨 원문",
    "normalized_text": "저는 김철수 씨 대리인입니다. 010-1234-5678, 한림로 650 사는데 경로당이 너무 멀어요",
    "complaint_type": "교통",
    "claims": [{"id": "c1", "text": "김철수 씨 집(한림로 650) 근처에 경로당까지 가는 길이 위험하다"},
               {"id": "c2", "text": "버스가 줄었다"}],
    "location": {"emd_name": "한림읍"},
}
SECRETS = ("김철수", "010-1234-5678", "한림로 650", "원문")


def test_mock_package_has_no_personal_data():
    out = json.dumps(run_engine.run(PII_QUERY, mock=True), ensure_ascii=False)
    assert not [s for s in SECRETS if s in out]


@pytest.mark.skipif(not DB.exists(), reason="data/jeju.duckdb 없음")
@pytest.mark.parametrize("use_ontology", [False, True])
def test_engine_package_has_no_personal_data_or_claim_text_in_gaps(use_ontology):
    p = run_engine.run(PII_QUERY, use_ontology=use_ontology)
    out = json.dumps(p, ensure_ascii=False)
    assert not [s for s in SECRETS if s in out]
    claim_texts = [c["text"] for c in mask_query(PII_QUERY)["claims"]]
    for g in p["gaps"]:
        assert not any(t in g["concept"] or t in g["reason"] for t in claim_texts)
