"""용언 단서(낱말 줄기): 활용형은 찾고, 글자만 같은 다른 낱말은 찾지 않는다."""
import pytest

from indicators.library import claim_aspect, claim_topics, cue_in, load_registry, stem_forms

ACCESS = ["정류소가 너무 멉니다", "병원이 멀어요", "병원이 머니까 못 가요", "먼 곳까지 가야 해요", "너무 멀리 있어요",
          "가기가 어려워요", "가기 어렵습니다", "가기 어려운 상황", "가기가 힘듭니다", "가기 힘든 마을",
          "가기 힘드네요", "병원 가기가 힘들어마씸"]
SUPPLY = ["의원이 없습니다", "의원이 없어요", "약국이 없는 동네", "의원이 적습니다", "의원이 적어요", "의원이 적은 편",
          "의원이 모자랍니다", "의원이 모자란 상황", "의원이 모자라요", "의원이 부족해요", "의원이 부족합니다"]
NOT_ASPECT = ["먼저 병원에 들렀어요", "할머니가 병원에 다녀요", "목적지가 병원이에요", "지적한 대로 병원",
              "멀티 의원", "의원 수 적정성", "먼지가 많은 의원"]


@pytest.mark.parametrize("text", ACCESS)
def test_access_conjugations(text):
    assert claim_aspect(text) == "access"


@pytest.mark.parametrize("text", SUPPLY)
def test_supply_conjugations(text):
    assert claim_aspect(text) == "supply"


@pytest.mark.parametrize("text", NOT_ASPECT)
def test_lookalikes_do_not_match(text):
    assert claim_aspect(text) is None


def test_irregular_forms():
    assert {"먼", "멉", "머니"} <= set(stem_forms("멀"))          # ㄹ 탈락
    assert {"어려워", "어려운"} <= set(stem_forms("어렵"))       # ㅂ 불규칙
    assert {"아파", "아픈"} <= set(stem_forms("아프"))           # ㅡ 탈락
    assert {"걸어"} <= set(stem_forms("걷"))                    # ㄷ 불규칙
    assert {"좁아"} <= set(stem_forms("좁"))                    # ㅂ 규칙
    assert "좁와" not in stem_forms("좁")


def test_topic_stems():
    assert claim_topics("밤에 아파도 갈 데가 없어요") == ["medical"]
    assert claim_topics("골목이 어두운데 무서워요") == ["safety"]


def test_plain_cue_unchanged():
    assert cue_in("버스", "버스가 줄었다") and not cue_in("버스", "택시가 줄었다")


def test_registry_stems_are_hangul_predicates():
    """붙임표 단서는 한글 줄기여야 한다(오타로 '-'만 붙거나 영문이 들어가면 찾지 못함)."""
    reg = load_registry()
    lists = [ind.get(k) or [] for ind in reg["indicators"] for k in ("keywords", "requires", "excludes", "bonus",
                                                                     "option_requires")]
    lists += [t["cues"] for t in reg["topics"]] + [a["cues"] for a in reg["aspects"]]
    lists += [g["keywords"] for g in reg["known_gaps"]]
    stems = [w for xs in lists for w in xs if w.endswith("-")]
    assert stems, "줄기 단서가 하나도 없다"
    for w in stems:
        assert all("가" <= ch <= "힣" for ch in w[:-1]), w
        assert stem_forms(w[:-1]), w


def test_apartment_is_not_sick():
    """'아파트'의 '아파'는 '아프다'가 아니다 (연결 시험에서 아파트 쓰레기 민원이 의료로 분류됐었다)."""
    assert claim_topics("노형동 아파트 쓰레기가 넘쳐요") == ["waste"]
    assert claim_topics("밤에 아파서 병원") == ["medical"]


COMPLAINT_WORDS = {
    "risk": ["차가 쌩쌩 달려서 아찔해요", "치일 뻔했어요", "사고 날 뻔했어요", "밤길이 위태로워요"],
    "supply": ["클린하우스가 넘쳐요", "쓰레기통이 꽉 차 있어요", "정류장이 드물어요", "CCTV를 더 설치해 주세요",
               "경로당이 있었으면 좋겠어요", "병원이 어서마씸", "병원이 엇수다", "밤길이 너무 어두워요", "의원을 늘려 주세요",
               "CCTV가 없어서 밤에 무서워요"],
    "access": ["병원까지 한참 걸려요", "병원 가는 데 오래 걸려요", "병원 가기 곤란해요"],
    "service": ["버스가 자주 안 와요", "버스가 띄엄띄엄 와요", "버스를 한참 기다려요"],
}


@pytest.mark.parametrize("aspect,text", [(a, t) for a, ts in COMPLAINT_WORDS.items() for t in ts])
def test_complaint_words(aspect, text):
    assert claim_aspect(text) == aspect


@pytest.mark.parametrize("text", ["병원이 멀지 않아요", "의원이 부족하지는 않아요", "정류장이 안 멀어요", "어서 오세요",
                                  "버스가 잘 와서 좋아요"])
def test_negated_or_not_a_complaint(text):
    assert claim_aspect(text) is None


def test_dialect_without_is_not_connective_ending():
    """제주어 '어서'(없어)는 띄어 쓴 낱말만. '줄어서마씸'의 '-어서'는 아니다."""
    assert claim_aspect("병원이 어서마씸") == "supply"
    assert claim_aspect("밥을 먹어서마씸") is None


def test_positive_tone_and_school_route():
    from indicators.library import positive_tone
    assert positive_tone("버스가 잘 와서 좋아요 감사합니다")
    assert not positive_tone("병원이 없어서 불편해요 감사합니다")
    assert claim_topics("봉개동 아이들 학교 가는 길이 위험해요") == ["traffic_safety"]
