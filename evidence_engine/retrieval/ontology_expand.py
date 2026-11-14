"""1단계 개념 확장 (B).

claim 문장(+ 민원 유형·targets) → 온톨로지 개념 → 3-hop 이내 경로 → 데이터 테이블 후보와 relation_path.
- extra_terms : 걸린 개념과 경로상 개념의 한글 라벨. 2단계 카탈로그 검색 질의에 더한다.
- hits        : (card_id, 제목, 경로 길이, relation_path, 테이블) — retrieval.merge.rank 의 ontology_hits
- gaps        : 질의가 직접 가리킨 개념인데 그 아래에 데이터 테이블이 하나도 없으면 빈칸 (detected_by=ontology)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ontology.concept_index import match_concepts
from ontology.paths import OntologyGraph


@dataclass
class Expansion:
    concepts: list[str] = field(default_factory=list)
    concept_ids: list[str] = field(default_factory=list)      # seed 개념 이름 (카드 태그 부스팅용)
    extra_terms: list[str] = field(default_factory=list)
    hits: list[tuple[str, str, int, str, str]] = field(default_factory=list)
    table_paths: dict[str, tuple[int, str]] = field(default_factory=dict)
    gaps: list[dict] = field(default_factory=list)


def expand(claim_text: str, context: str, graph: OntologyGraph, table_cards: dict[str, tuple[str, str]],
           max_hops: int = 3) -> Expansion:
    """table_cards: table_name → (card_id, 데이터명). 카드가 없는 파생 테이블은 hits에서 빠지고 table_paths에만 남는다."""
    direct = match_concepts(claim_text, graph)
    # claim에 개념이 없을 때만 민원 본문·유형으로 넓힌다(본문 전체를 쓰면 다른 claim의 개념이 섞임).
    matches = direct or match_concepts(context, graph, limit=3)
    out = Expansion(concepts=[m.label for m in matches], concept_ids=[m.iri.split("#")[-1] for m in matches])
    if not matches:
        return out
    paths = graph.paths_to_tables([m.iri for m in matches], max_hops)
    out.table_paths = paths
    for table, (hops, text) in paths.items():
        if table in table_cards:
            card_id, title = table_cards[table]
            out.hits.append((card_id, title, max(hops, 1), text, table))
    terms = {m.label for m in matches}
    for _, (_, text) in paths.items():
        for node in text.split("→ ")[1:-1]:
            terms.add(node.split(" —")[0].strip())
    out.extra_terms = sorted(t for t in terms if t)
    for m in direct:
        if not graph.is_backed(m.iri):
            out.gaps.append({
                "concept": m.label,
                "reason": f"온톨로지 개념 '{m.label}'에 연결된 데이터 테이블이 없음 (질의 낱말 '{m.matched}')",
                "detected_by": "ontology",
            })
    return out
