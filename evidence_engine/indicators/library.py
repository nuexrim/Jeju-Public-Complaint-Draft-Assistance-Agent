"""지표 라이브러리: registry.yaml의 지표를 고르고 계산한다.

숫자는 여기서만 만든다. 모든 SQL은 compute.sql_runner(읽기 전용·행 수·시간 제한)를 거친다.
"""
from __future__ import annotations

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


def _has(text: str, words: list[str] | None) -> list[str]:
    return [w for w in words or [] if w in text]


def select_indicator(claim_text: str, context_text: str, available_tables: set[str],
                     registry: dict | None = None) -> tuple[dict | None, list[str]]:
    """(가장 잘 맞는 지표, 다른 후보 id). 필요한 테이블이 DB에 없으면 후보에서 뺀다."""
    registry = registry or load_registry()
    scored = []
    for ind in registry["indicators"]:
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
