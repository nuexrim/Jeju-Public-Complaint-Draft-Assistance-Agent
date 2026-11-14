"""질의 낱말 → 온톨로지 개념(클래스) 찾기.

지금은 어휘 일치다: 개념의 한글 라벨(@ko)과 별칭(skos:altLabel)이 질의 문장 안에 그대로 있으면 그 개념을 고른다.
라벨이 길수록(구체적일수록) 점수가 높다. 같은 낱말이 상위·하위 개념 모두에 걸리면 둘 다 시작점이 된다.
bge-m3 임베딩 유사도는 모델을 받은 뒤 dense_match 로 더한다(현재 미사용).
테이블 클래스(데이터명 라벨)는 시작점으로 쓰지 않는다. 시작점은 seed 개념뿐이다.
"""
from __future__ import annotations

from dataclasses import dataclass

from ontology.paths import OntologyGraph


@dataclass(frozen=True)
class ConceptMatch:
    iri: str
    label: str
    matched: str      # 질의에서 걸린 낱말
    score: float


def match_concepts(text: str, graph: OntologyGraph, limit: int = 6) -> list[ConceptMatch]:
    out: dict[str, ConceptMatch] = {}
    compact = text.replace(" ", "")
    for c in graph.classes:
        if c in graph.table_of:
            continue
        label = graph.labels.get(c, "")
        # 라벨은 통째로(공백·가운뎃점 무시) 비교한다. "의료 시설"을 쪼개 "시설"만으로 걸리지 않게.
        names = [label, *label.split("·")] + graph.alt_labels.get(c, [])
        for name in names:
            key = name.replace(" ", "")
            if len(key) >= 2 and key in compact:
                score = len(key) + (1 if name == label else 0)
                if c not in out or score > out[c].score:
                    out[c] = ConceptMatch(c, graph.label(c), name, score)
    # 더 긴 낱말에 포함되는 짧은 일치는 버린다 ("대피시설"이 걸렸으면 "시설" 단독 일치는 시작점에서 뺀다)
    kept = [m for m in out.values()
            if not any(o is not m and m.matched.replace(" ", "") in o.matched.replace(" ", "")
                       and len(o.matched.replace(" ", "")) > len(m.matched.replace(" ", "")) for o in out.values())]
    return sorted(kept, key=lambda m: -m.score)[:limit]
