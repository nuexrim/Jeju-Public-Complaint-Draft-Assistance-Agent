"""근거 차트 PNG. 숫자는 지표 계산 결과(IndicatorResult)만 그린다.

rank 규칙 지표: 비교군(읍·면끼리 / 동끼리) 막대그래프. 대상 읍면동은 진한 색, 나머지는 옅은 색, 중앙값 점선.
그 밖의 규칙(zero·trend·context)은 막대 비교가 의미 없어 차트를 만들지 않는다(chart=null).
파일: charts/<request_id>_<claim_id>.png  (패키지에는 evidence_engine 기준 상대 경로를 넣는다)
"""
from __future__ import annotations

import statistics
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
KOREAN_FONTS = ["Malgun Gothic", "AppleGothic", "Apple SD Gothic Neo", "NanumGothic", "Noto Sans CJK KR"]
TARGET, OTHER, MEDIAN = "#1f5fa8", "#c9d6e6", "#7a7a7a"


def _setup():
    import matplotlib
    matplotlib.use("Agg")                        # 화면 없이 파일로만 (센터 PC·서버)
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    have = {f.name for f in font_manager.fontManager.ttflist}
    font = next((f for f in KOREAN_FONTS if f in have), None)
    if font:
        plt.rcParams["font.family"] = font
    plt.rcParams["axes.unicode_minus"] = False
    return plt, font


def safe_name(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text)


def comparison_bar(values: dict[str, float], names: dict[str, str], target: str, *, title: str, unit: str,
                   out_dir: Path, file_stem: str) -> Path | None:
    """values: 비교군 emd_code → 값 (대상 포함)."""
    if target not in values or len(values) < 2:
        return None
    plt, _font = _setup()
    items = sorted(values.items(), key=lambda kv: kv[1])
    labels = [names.get(c, c) for c, _ in items]
    nums = [v for _, v in items]
    colors = [TARGET if c == target else OTHER for c, _ in items]
    height = max(3.0, 0.28 * len(items) + 1.2)
    fig, ax = plt.subplots(figsize=(7.2, height), dpi=120)
    ax.barh(labels, nums, color=colors)
    med = statistics.median(nums)
    ax.axvline(med, color=MEDIAN, linestyle="--", linewidth=1)
    ax.text(med, len(items) - 0.4, f" 중앙값 {med:,.2f}", color=MEDIAN, fontsize=8, va="top")
    ax.set_title(title, fontsize=11)
    ax.set_xlabel(unit, fontsize=9)
    ax.tick_params(axis="y", labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{safe_name(file_stem)}.png"
    fig.savefig(path)
    plt.close(fig)
    return path
