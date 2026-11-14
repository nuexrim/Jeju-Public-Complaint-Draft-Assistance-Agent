"""3단계 병합·순위.

score = w.path * path_score + w.search * search_score + w.spatial * spatial_match + w.fresh * freshness
- path_score   : 1 / 경로 길이 (온톨로지 경로가 없으면 0. use_ontology=false면 항상 0)
- search_score : 카탈로그 BM25 점수를 후보 중 최댓값으로 나눈 값 (0~1)
- spatial_match: 적재된 테이블이 읍면동 코드로 계산 가능(매핑률 ≥ 1 - max_missing_ratio)하면 1,
                 행정시 단위 컬럼만 있으면 0.5, 그 외 0
- freshness    : 기준일이 1년 이내 1, 2년 이내 0.5, 그 외 0
같은 데이터셋(card_id)은 하나로 합치고, found_by는 ontology / catalog / both 로 표시한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class DatasetInfo:
    """DuckDB dataset 테이블에서 읽은 적재 데이터셋 정보 (card 하나에 테이블 여러 개일 수 있음)."""
    tables: list[str]
    emd_ratio: float | None
    city_level: bool
    as_of: str | None


@dataclass
class Candidate:
    card_id: str
    title: str
    score: float
    search_score: float
    path_score: float
    spatial_match: float
    freshness: float
    found_by: str
    tables: list[str] = field(default_factory=list)
    relation_path: str | None = None


def freshness(as_of: str | None, today: date, max_years: float = 2) -> float:
    if not as_of or len(as_of) < 4 or not as_of[:4].isdigit():
        return 0.0
    y, m = int(as_of[:4]), int(as_of[5:7]) if len(as_of) >= 7 and as_of[5:7].isdigit() else 12
    age = (today.year - y) + (today.month - m) / 12
    return 1.0 if age <= max_years / 2 else 0.5 if age <= max_years else 0.0


def spatial(info: DatasetInfo | None, max_missing: float) -> float:
    if not info:
        return 0.0
    if info.emd_ratio is not None and info.emd_ratio >= 1 - max_missing:
        return 1.0
    return 0.5 if info.city_level else 0.0


def rank(catalog_hits: list, ontology_hits: list | None, datasets: dict[str, DatasetInfo],
         weights: dict, *, today: date, max_missing: float, max_years: float, top_k: int = 5) -> list[Candidate]:
    """catalog_hits: retrieval.catalog_search.Hit 목록. ontology_hits: (card_id, title, path_len, relation_path)."""
    top = max((h.score for h in catalog_hits), default=0) or 1
    merged: dict[str, Candidate] = {}
    for h in catalog_hits:
        info = datasets.get(h.card_id)
        merged[h.card_id] = Candidate(
            h.card_id, h.title, 0, h.score / top, 0, spatial(info, max_missing),
            freshness(info.as_of if info else None, today, max_years), "catalog", info.tables if info else [],
        )
    for card_id, title, path_len, relation in ontology_hits or []:
        c = merged.get(card_id)
        if c is None:
            info = datasets.get(card_id)
            c = merged[card_id] = Candidate(card_id, title, 0, 0, 0, spatial(info, max_missing),
                                            freshness(info.as_of if info else None, today, max_years),
                                            "ontology", info.tables if info else [])
        else:
            c.found_by = "both"
        if path_len and 1 / path_len > c.path_score:
            c.path_score, c.relation_path = 1 / path_len, relation
    for c in merged.values():
        c.score = round(weights["path_score"] * c.path_score + weights["search_score"] * c.search_score
                        + weights["spatial_match"] * c.spatial_match + weights["freshness"] * c.freshness, 4)
    # 동점이면 경로 보유 후보 우선 (3장)
    return sorted(merged.values(), key=lambda c: (-c.score, -c.path_score, -c.search_score))[:top_k]
