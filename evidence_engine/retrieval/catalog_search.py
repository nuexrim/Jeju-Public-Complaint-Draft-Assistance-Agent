"""2단계 카탈로그 검색.

claim 문장 + 질의의 targets + 민원 유형으로 카탈로그 카드를 BM25로 찾는다.
DuckDB에 적재된 데이터셋이면 그 테이블 이름도 같이 돌려준다(계산 가능 여부 판단용).
1단계(온톨로지)에서 확장 개념이 오면 extra_terms로 받아 같은 질의에 더한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from catalog.index import load_index


@dataclass
class Hit:
    card_id: str
    title: str
    score: float
    tables: list[str] = field(default_factory=list)   # 적재된 DuckDB 테이블 (없으면 미적재)


def claim_query(query: dict, claim: dict, extra_terms: list[str] | None = None) -> str:
    parts = [claim["text"], *query.get("targets", []), query.get("complaint_type", ""), *(extra_terms or [])]
    return " ".join(p for p in parts if p)


BOOST = 1.2   # 1단계에서 확장된 개념과 카드 개념 태그가 겹치면 곱하는 가산 (3장)


def search(text: str, loaded: dict[str, list[str]], top_k: int = 5, boost: set[str] | None = None) -> list[Hit]:
    """loaded: card_id → 적재 테이블 목록 (dataset.hub_id 기준). boost: 확장 개념 이름(seed IRI 이름)."""
    index = load_index()
    scored = []
    for card, score in zip(index.cards, index.scores(text)):
        if score <= 0:
            continue
        if boost and boost.intersection(card.concepts):
            score *= BOOST
        scored.append((card, score))
    scored.sort(key=lambda x: -x[1])
    return [Hit(c.card_id, c.title, round(s, 3), loaded.get(c.card_id, [])) for c, s in scored[:top_k]]
