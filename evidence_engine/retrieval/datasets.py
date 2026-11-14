"""DuckDB dataset 테이블 → 카탈로그 card_id별 적재 정보."""
from __future__ import annotations

from collections import defaultdict

from retrieval.merge import DatasetInfo

CITY_COLUMNS = ("행정시", "구분", "시군구", "sigungu")


def load_datasets(con) -> tuple[dict[str, DatasetInfo], dict[str, dict]]:
    """(card_id → DatasetInfo, table_name → dataset 행 dict). hub_id 없는 외부 자료는 card가 없어 두 번째에만 들어간다."""
    cols = [c[0] for c in con.execute("DESCRIBE dataset").fetchall()]
    rows = [dict(zip(cols, r)) for r in con.execute("SELECT * FROM dataset").fetchall()]
    by_table = {r["table_name"]: r for r in rows}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["hub_id"] is not None:
            grouped[f"hub-{r['hub_id']}"].append(r)
    out = {}
    for card_id, rs in grouped.items():
        ratios = [r["emd_ratio"] for r in rs if r["emd_ratio"] is not None]
        city = False
        for r in rs:
            names = {c[0] for c in con.execute(f'DESCRIBE "{r["table_name"]}"').fetchall()}
            city = city or any(c in names for c in CITY_COLUMNS)
        as_of = max((r["period_end"] or r["updated_at"] or "" for r in rs), default="") or None
        out[card_id] = DatasetInfo([r["table_name"] for r in rs], max(ratios) if ratios else None, city, as_of)
    return out, by_table
