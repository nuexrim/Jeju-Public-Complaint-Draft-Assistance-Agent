"""지표 라이브러리: registry.yaml의 지표를 고르고 계산한다.

숫자는 여기서만 만든다. 모든 SQL은 compute.sql_runner(읽기 전용·행 수·시간 제한)를 거친다.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from compute.sql_runner import SQLRunner

REGISTRY = Path(__file__).resolve().parent / "registry.yaml"


@lru_cache(maxsize=None)
def load_registry(path: Path = REGISTRY) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 용언 단서 (낱말 줄기)
# registry 단서 중 '멀-', '어렵-' 처럼 붙임표로 끝나는 것은 용언 줄기다(국어사전 표기와 같다).
# 줄기를 활용한 겉모양(멀어·먼·멉니다·머니 / 어려워·어려운 / 힘듭니다·힘든 / 아파·아픈)을 모두 찾는다.
# 그냥 낱말(붙임표 없음)은 예전처럼 글자 그대로 찾는다.
# 다루는 활용: 규칙(없어·없습니다), ㄹ 탈락(먼·멉니다·머니), ㅂ 불규칙(어려워·어려운), ㄷ 불규칙(걸어),
#             ㅡ 탈락(아파), 하다(부족해), 모음 줄기(모자라·모자란·모자랍니다), 르 불규칙(달라).
_GA, _JUNG_N, _JONG_N = 0xAC00, 21, 28
_J_NONE, _J_N, _J_D, _J_L, _J_B, _J_SS = 0, 4, 7, 8, 17, 20
_V_A, _V_AE, _V_EO, _V_E, _V_YEO, _V_O, _V_WA, _V_U, _V_WEO, _V_EU, _V_I = 0, 1, 4, 5, 6, 8, 9, 13, 14, 18, 20
# 받침 있는 한 음절 줄기 바로 뒤에 오는 어미 첫 글자. '적대', '없이'처럼 다른 낱말이 되는 것은 뺐다.
_ENDINGS = ("다", "어", "아", "고", "지", "기", "게", "네", "습", "은", "는", "음", "으", "을", "던", "더",
            "겠", "었", "았", "잖", "면")
_B_REGULAR = {"좁", "잡", "씹", "업", "입", "뽑", "집"}       # ㅂ 받침이지만 규칙 활용(좁아)
_D_IRREGULAR = {"걷", "듣", "묻", "싣", "깨닫"}              # ㄷ → ㄹ (걸어)
# 활용형과 글자가 같은 다른 낱말(뒤 글자로 가린다). 제주데이터허브 데이터 설명 1,259건에서 찾은 것:
# '먼저'·'먼지'는 '멀다'가 아니고, '아파트'는 '아프다'가 아니다.
_NOT_FORM = {"먼": ("저", "지"), "아파": ("트",)}


def _split(ch: str) -> tuple[int, int, int]:
    n = ord(ch) - _GA
    return n // (_JUNG_N * _JONG_N), (n // _JONG_N) % _JUNG_N, n % _JONG_N


def _join(cho: int, jung: int, jong: int = _J_NONE) -> str:
    return chr(_GA + (cho * _JUNG_N + jung) * _JONG_N + jong)


def stem_forms(stem: str) -> list[str]:
    """용언 줄기 → 문장에서 찾을 겉모양들 (앞부분만. 그 뒤 어미는 무엇이 와도 된다)."""
    head, last = stem[:-1], stem[-1]
    cho, jung, jong = _split(last)
    one = len(stem) == 1
    forms: list[str] = []
    if jong == _J_NONE:                                   # 모음 줄기: 모자라, 아프, 부족하, 느리
        forms += [stem] if not one else [stem + e for e in ("다", "고", "지", "기", "게", "네", "면")]
        forms += [head + _join(cho, jung, j) for j in (_J_N, _J_L, _J_B)]   # 모자란·모자랄·모자랍
        if jung in (_V_A, _V_EO, _V_AE, _V_E, _V_YEO) and last != "하":
            forms += [head + _join(cho, jung, _J_SS)]     # 모자랐
        if last == "하":
            forms += [head + "해", head + "했"]
        elif last == "르" and head:                       # 다르 → 달라
            pc, pj, _ = _split(head[-1])
            v2 = "라" if pj in (_V_A, _V_O) else "러"
            forms += [head[:-1] + _join(pc, pj, _J_L) + v2]
        elif jung == _V_EU:                               # 아프 → 아파, 크 → 커
            prev = _split(head[-1])[1] if head else _V_EO
            v = _V_A if prev in (_V_A, _V_O) else _V_EO
            forms += [head + _join(cho, v), head + _join(cho, v, _J_SS)]
        else:
            v = {_V_O: _V_WA, _V_U: _V_WEO, _V_I: _V_YEO}.get(jung)
            if v is not None:
                forms += [head + _join(cho, v), head + _join(cho, v, _J_SS)]
    else:
        if one:   # ㄹ 줄기는 ㄴ·ㅅ·ㅂ·으 앞에서 ㄹ이 빠지므로 그런 어미는 아래 탈락형으로만 찾는다
            forms += [stem + e for e in _ENDINGS if not (jong == _J_L and e in ("습", "은", "는", "으", "을", "음"))]
        else:
            forms += [stem]
        if jong == _J_L:                                  # 멀 → 먼·멉·머니, 힘들 → 힘든·힘듭·힘드니
            bare = head + _join(cho, jung)
            forms += [head + _join(cho, jung, _J_N), head + _join(cho, jung, _J_B)]
            forms += [bare + e for e in ("니", "네", "세", "시", "나")]
            if one:
                forms += [stem + "리"]                    # 멀리
        elif jong == _J_B and stem not in _B_REGULAR:     # 어렵 → 어려워·어려운·어려울
            bare = head + _join(cho, jung)
            w = "와" if jung == _V_O and one else "워"      # 돕 → 도와, 어렵 → 어려워
            forms += [bare + e for e in (w, "우", "운", "울", "웠" if w == "워" else "왔")]
        elif jong == _J_D and stem in _D_IRREGULAR:       # 걷 → 걸어·걸으
            lstem = head + _join(cho, jung, _J_L)
            forms += [lstem + e for e in ("어", "으", "었", "을")]
    return list(dict.fromkeys(forms))


@lru_cache(maxsize=None)
def _stem_re(stem: str) -> re.Pattern:
    # 한 음절 줄기는 앞 글자가 한글이 아니어야 한다(할'머니', 목'적지', 지'적'하다를 잡지 않게)
    before = r"(?<![가-힣])" if len(stem) == 1 else ""
    alts = []
    for f in sorted(stem_forms(stem), key=len, reverse=True):
        alts.append(re.escape(f) + "".join(f"(?!{re.escape(x)})" for x in _NOT_FORM.get(f, ())))
    return re.compile(before + "(?:" + "|".join(alts) + ")")


def cue_in(word: str, text: str) -> bool:
    """단서 하나가 문장에 있는지. '멀-'처럼 붙임표로 끝나면 용언 줄기로 보고 활용형까지 찾는다."""
    if word.endswith("-") and len(word) > 1:
        return _stem_re(word[:-1]).search(text) is not None
    return word in text


def _spans(word: str, text: str):
    if word.endswith("-") and len(word) > 1:
        yield from (m.span() for m in _stem_re(word[:-1]).finditer(text))
        return
    i = text.find(word)
    while i >= 0:
        yield i, i + len(word)
        i = text.find(word, i + 1)


# 부정: '멀지 않아요', '부족하지는 않아요', '안 멀어요' 처럼 단서를 부정하면 그 단서는 주장의 측면으로 보지 않는다
_NEG_AFTER = re.compile(r"^(?:하)?(?:지|진|지는|지도)?\s?않")
_NEG_BEFORE = re.compile(r"(?:^|\s)안\s$")


def cue_affirmed(word: str, text: str) -> bool:
    """단서가 문장에 있고, 적어도 한 번은 부정되지 않은 채로 쓰였는지."""
    return any(not _NEG_AFTER.match(text[e:]) and not _NEG_BEFORE.search(text[:s]) for s, e in _spans(word, text))


def _has(text: str, words: list[str] | None) -> list[str]:
    return [w for w in words or [] if cue_in(w, text)]


def select_indicator(claim_text: str, context_text: str, available_tables: set[str],
                     registry: dict | None = None) -> tuple[dict | None, list[str]]:
    """(가장 잘 맞는 지표, 다른 후보 id). 필요한 테이블이 DB에 없으면 후보에서 뺀다."""
    registry = registry or load_registry()
    scored = []
    for ind in registry["indicators"]:
        if ind.get("role") == "context":          # 맥락 지표는 주장을 직접 판정하지 않는다
            continue
        if ind.get("requires") and not _has(claim_text, ind["requires"]):
            continue
        if _has(claim_text + " " + context_text, ind.get("excludes")):
            continue
        if not set(ind["tables"]) <= available_tables:
            continue
        hits = _has(claim_text, ind["keywords"])
        if not hits:
            continue
        scored.append((len(hits) + 0.5 * len(_has(claim_text, ind.get("bonus"))), ind["id"], ind))
    scored.sort(key=lambda x: -x[0])
    return (scored[0][2] if scored else None), [s[1] for s in scored[1:]]


def known_gap(claim_text: str, context_text: str, registry: dict | None = None) -> dict | None:
    registry = registry or load_registry()
    for gap in registry.get("known_gaps", []):
        if _has(claim_text + " " + context_text, gap["keywords"]):
            return gap
    return None


def indicator_by_id(indicator_id: str, registry: dict | None = None) -> dict | None:
    registry = registry or load_registry()
    return next((i for i in registry["indicators"] if i["id"] == indicator_id), None)


def gap_by_id(gap_id: str, registry: dict | None = None) -> dict | None:
    registry = registry or load_registry()
    return next((g for g in registry.get("known_gaps", []) if g.get("id") == gap_id), None)


def claim_aspect(claim_text: str, registry: dict | None = None) -> str | None:
    """주장이 말하는 측면(usage/service/risk/access/supply). 단서가 없으면 None(판단 보류)."""
    registry = registry or load_registry()
    for a in registry.get("aspects", []):
        if any(cue_affirmed(c, claim_text) for c in a["cues"]):     # '멀지 않아요'처럼 부정한 단서는 빼고
            return a["id"]
    return None


def positive_tone(text: str, registry: dict | None = None) -> bool:
    """불편이 아니라 칭찬·감사로 들리는지: 긍정 단서가 있고, 불편 측면 단서는 없을 때."""
    registry = registry or load_registry()
    return bool(_has(text, registry.get("positive_cues"))) and claim_aspect(text, registry) is None


def aspect_name(aspect_id: str | None, registry: dict | None = None) -> str | None:
    registry = registry or load_registry()
    return next((a["name"] for a in registry.get("aspects", []) if a["id"] == aspect_id), None)


def claim_topics(claim_text: str, context_text: str = "", registry: dict | None = None) -> list[str]:
    """주장 문장의 주제. 문장에 없으면 context(targets 등)에서 찾는다."""
    registry = registry or load_registry()
    for text in (claim_text, context_text):
        found = [t["id"] for t in registry.get("topics", []) if _has(text, t["cues"])]
        if found:
            return found
    return []


def proxy_gap(indicator: dict, aspect: str | None, registry: dict | None = None) -> dict | None:
    """주장 측면이 지표 측면과 다를 때, 그 측면을 재는 데이터가 없다고 등록된 빈칸(예: 버스 운행 횟수)."""
    registry = registry or load_registry()
    if not aspect or aspect == indicator.get("measures"):
        return None
    # 같은 주제에 그 측면을 재는 지표가 있으면 데이터가 없는 게 아니라 지표를 잘못 고른 것 → 빈칸으로 보지 않는다
    if any(i.get("topic") == indicator.get("topic") and i.get("measures") == aspect for i in registry["indicators"]):
        return None
    return next((g for g in registry.get("known_gaps", [])
                 if g.get("topic") == indicator.get("topic") and g.get("aspect") == aspect), None)


@dataclass
class IndicatorResult:
    indicator: dict
    emd_code: str | None
    value: float | None
    values: dict[str, float] = field(default_factory=dict)   # scope=emd: 43개 행정동 값 (NULL 제외)
    window: dict[str, str] | None = None
    note: str | None = None                                  # 값이 없을 때 사유


def _shift(month: str, n: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    k = y * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def full_month_window(runner: SQLRunner, table: str) -> dict[str, str]:
    """원자료 마지막 날짜가 월말이 아니면 그 달은 빼고, 최근 24개 완결 월을 앞뒤 12개월로 나눈다."""
    (last,) = runner.run(f'SELECT max(period) FROM "{table}"').rows[0]
    last = str(last)[:10]
    d = date.fromisoformat(last)
    end = last[:7] if (date(d.year + d.month // 12, d.month % 12 + 1, 1) - d).days == 1 else _shift(last[:7], -1)
    return {"end": end, "mid": _shift(end, -12), "start": _shift(end, -24)}


def compute(indicator: dict, emd_code: str | None, runner: SQLRunner) -> IndicatorResult:
    params = {}
    window = None
    if indicator.get("window"):
        window = full_month_window(runner, indicator["window_source"])
        params = {k: v for k, v in window.items() if f"${k}" in indicator["sql"]}
    result = runner.run(indicator["sql"], params)
    if indicator["scope"] == "jeju":
        value = result.rows[0][0] if result.rows else None
        return IndicatorResult(indicator, emd_code, None if value is None else float(value), window=window)

    values = {code: float(v) for code, v in result.rows if v is not None}
    if emd_code is None:
        return IndicatorResult(indicator, None, None, values, window, "읍면동을 특정하지 못해 읍면동 지표를 계산하지 않음")
    if emd_code not in values:
        return IndicatorResult(indicator, emd_code, None, values, window,
                               "이 읍면동은 분모(인구 등) 또는 원자료가 없어 계산할 수 없음")
    return IndicatorResult(indicator, emd_code, values[emd_code], values, window)


def comparison_group(emd_code: str, emd_names: dict[str, str]) -> tuple[str, list[str]]:
    """읍·면은 읍·면끼리, 동은 동끼리 비교한다 (도농 차이가 커서 한데 섞으면 판정이 왜곡됨)."""
    is_rural = emd_names[emd_code].endswith(("읍", "면"))
    label = "읍·면" if is_rural else "동"
    return label, [c for c, n in emd_names.items() if n.endswith(("읍", "면")) == is_rural]


def describe(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2f}".rstrip("0").rstrip(".")


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None
