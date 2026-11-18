"""변환 모듈 근거 검사 테스트 (네트워크 없음)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import normalize as N  # noqa: E402


class FakeDict:
    d = {"고냉이": {"dialect_ratio": 0.99, "cands": [{"standard": "고양이", "count": 159, "sources": {"daily": 159}}]},
         "안": {"dialect_ratio": 0.01, "cands": [{"standard": "않아", "count": 9, "sources": {"daily": 9}}]}}

    def lookup(self, w):
        w = w.strip(N.PUNCT)
        if w in self.d:
            e = self.d[w]
            strength = "강함" if e["dialect_ratio"] >= N.STRONG_RATIO else \
                ("보통" if e["dialect_ratio"] >= N.MIN_PROMPT_RATIO else "약함")
            return {"word": w, "match": "정확히 일치", "key": w, "cands": e["cands"],
                    "ratio": e["dialect_ratio"], "strength": strength}
        return None


def check(text, out):
    return N.check_evidence(text, out, FakeDict())


def test_dictionary_backed_and_guess():
    ch, ask, warn, ign = check("고냉이 죽언", {"standard": "고양이 죽어서",
                                               "changes": [{"from": "고냉이", "to": "고양이"},
                                                           {"from": "죽언", "to": "죽어서"}], "unclear": []})
    assert [c["type"] for c in ch] == ["사전", "추정"]
    assert len(ask) == 1 and "죽언" in ask[0]


def test_fake_change_ignored_and_punct_not_warned():
    ch, ask, warn, ign = check("농사도 잘 안 돼요", {"standard": "농사도 잘 안 돼요.",
                                                  "changes": [{"from": "안", "to": "안 돼요"}], "unclear": []})
    assert ch == [] and ask == [] and warn == [] and ign == ["안 → 안 돼요"]


def test_unclear_question_must_quote_word():
    _, ask, _, _ = check("농사도 잘 안 돼요", {"standard": "농사도 잘 안 돼요", "changes": [],
                                            "unclear": [{"word": "농사", "guess": "", "question": "농사는 잘 되시나요?"},
                                                        {"word": "없는말", "guess": "", "question": "'없는말'?"}]})
    assert ask == ["'농사'은(는) 어떤 뜻인가요?"]


def test_hidden_change_warned():
    _, _, warn, _ = check("집 앞이 고냉이가", {"standard": "집 앞에 고양이가",
                                           "changes": [{"from": "고냉이가", "to": "고양이가"}], "unclear": []})
    assert warn and "앞이" in warn[0]


def test_index_failure_raises_same_error_every_time(tmp_path, monkeypatch):
    """scikit-learn 이 없어 색인을 못 읽으면 두 번째 호출도 같은 ResourceMissing (전에는 AttributeError 로 멈춤)."""
    import pytest
    import normalize as N
    d, i = tmp_path / "dict.json", tmp_path / "index.pkl"
    d.write_text("{}", encoding="utf-8")
    i.write_bytes(b"x")
    monkeypatch.setattr(N, "DICT_PATH", d)
    monkeypatch.setattr(N, "INDEX_PATH", i)
    monkeypatch.setattr(N, "_res", {})
    monkeypatch.setattr(N, "DialectDict", lambda: object())

    def no_sklearn(*a, **k):
        raise ModuleNotFoundError("No module named 'sklearn'")
    monkeypatch.setattr(N, "ExampleIndex", no_sklearn)
    for _ in range(2):
        with pytest.raises(N.ResourceMissing):
            N.normalize("고냉이 막 울엄수다")
    assert N._res == {}                               # 반쯤 불러온 상태를 기억하지 않는다
