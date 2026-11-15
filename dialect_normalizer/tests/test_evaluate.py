"""평가 지표 테스트: MultiLexNorm(van der Goot et al., 2021)의 TP/FN/FP/TN 정의대로 세는지 확인"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import evaluate as E  # noqa: E402

# 입력 10어절 = 방언 5개(고냉이·허고·강생이들·왕·파불민) + 표준어 5개(막·농사도·잘·안·돼요)
DIALECT = "고냉이 허고 강생이들 왕 막 파불민 농사도 잘 안 돼요"
GOLD = [["고냉이", "고양이"], ["허고", "하고"], ["강생이들", "강아지들"], ["왕", "와서"], ["파불민", "파버리면"]]


def counts(pred, dialect=DIALECT, gold=GOLD):
    c = E.align_counts(dialect, gold, pred)
    return c["TP"], c["FN"], c["FP"], c["TN"]


def test_perfect():
    assert counts("고양이 하고 강아지들 와서 막 파버리면 농사도 잘 안 돼요") == (5, 0, 0, 5)


def test_leave_as_is_gives_err_zero():
    tp, fn, fp, tn = counts(DIALECT)
    assert (tp, fn, fp, tn) == (0, 5, 0, 5)
    assert (tp - fp) / (tp + fn) == 0          # 논문: 아무것도 안 바꾸면 ERR = 0


def test_spacing_merge_is_not_error():
    # '고양이 하고' → '고양이하고' 처럼 띄어쓰기만 달라도 맞은 것으로
    assert counts("고양이하고 강아지들 와서 막 파버리면 농사도 잘 안 돼요") == (5, 0, 0, 5)


def test_over_correction_is_fp():
    # 표준어인 '막'을 '아주'로 바꿈 → FP
    assert counts("고양이 하고 강아지들 와서 아주 파버리면 농사도 잘 안 돼요") == (5, 0, 1, 4)


def test_wrong_candidate_is_fn_only():
    # 방언 '왕'을 틀린 표준형 '왕창'으로 → FN만 (FP 아님, 논문 정의)
    assert counts("고양이 하고 강아지들 왕창 막 파버리면 농사도 잘 안 돼요") == (4, 1, 0, 5)


def test_split_eojeol():
    tp, fn, fp, tn = counts("아버지 죽어 버려서", dialect="아부지 죽어부난",
                            gold=[["아부지", "아버지"], ["죽어부난", "죽어 버려서"]])
    assert (tp, fn, fp, tn) == (2, 0, 0, 0)


def test_particle_added_to_standard_word_is_tn():
    # '냄새' → '냄새가' (조사만 붙음) 은 과잉 수정으로 세지 않음
    assert counts("냄새가 아주 납니다", dialect="냄새 막 남수다",
                  gold=[["막", "아주"], ["남수다", "납니다"]]) == (2, 0, 0, 1)


def test_masked_token_excluded():
    assert counts("○○ 고양이", dialect="○○ 고냉이", gold=[["고냉이", "고양이"]]) == (1, 0, 0, 0)


def test_score_metrics_formula():
    preds = [{"dialect": DIALECT, "gold_pairs": GOLD, "n_guess": 0, "n_ask": 0,
              "gold": "고양이하고 강아지들 와서 막 파버리면 농사도 잘 안 돼요",
              "pred": "고양이 하고 강아지들 와서 아주 파버리면 농사도 잘 안 돼요"}]
    s = E.score(preds)
    assert s["재현율"] == 100.0                      # 5/5
    assert s["ERR"] == 80.0                          # (5-1)/5
    assert s["FPR"] == 20.0                          # 1/5
    assert s["단어 정확도"] == 90.0                  # (5+4)/10
    assert s["LAI 정확도"] == 50.0                   # 5/10
    assert s["CER(공백 제외)"] == round(100 * 2 / len("고양이하고강아지들와서막파버리면농사도잘안돼요"), 1)


def test_edit_distance():
    assert E.edit_distance("막", "아주") == 2 and E.edit_distance("abc", "abc") == 0


def test_multi_word_gold_pair():
    # 구 단위 주석: '바빠 노난' → '바빠 노니까'
    c = E.align_counts("다들 바빠 노난 못 와", [["바빠 노난", "바빠 노니까"]], "다들 바빠 노니까 못 와")
    assert (c["TP"], c["FN"], c["FP"], c["TN"], c["unmatched_gold"]) == (1, 0, 0, 3, 0)
