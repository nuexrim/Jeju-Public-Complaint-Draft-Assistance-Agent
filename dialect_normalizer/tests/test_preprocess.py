"""전처리 정제 규칙 테스트"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import preprocess as P  # noqa: E402


def test_dual_notation_dialect_and_standard_sides():
    t = "자 (그믄)/(그러면) 이제부터 얘기해 (보게예)/(봐요)"
    assert P.clean(t) == "자 그믄 이제부터 얘기해 보게예"
    assert P.clean(t, "standard") == "자 그러면 이제부터 얘기해 봐요"


def test_hash_removed_but_mask_kept():
    assert P.clean("예초하다가(이)/(#이) 뱀", "standard") == "예초하다가이 뱀"
    assert P.clean("언니 #이름# 반가워") == "언니 ○○ 반가워"
    assert P.eojeol_pair("이", "#이") is None          # '#'만 다른 쌍은 방언 쌍이 아님


def test_unclear_marker_still_detected():
    assert P.is_bad(P.clean("뭐라고 (()) 했어"))
