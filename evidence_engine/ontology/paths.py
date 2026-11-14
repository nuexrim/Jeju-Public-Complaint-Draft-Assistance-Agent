"""온톨로지 그래프와 k-hop 경로 탐색, SPARQL 템플릿.

jeju.ttl 을 rdflib로 읽어 이름 있는 클래스 사이의 관계를 무방향 그래프로 만든다.
- rdfs:subClassOf (이름 있는 상위 클래스)      : 자식 —하위개념/상위개념— 부모
- rdfs:subClassOf [owl:onProperty p; some/only C] : 클래스 —p— C
- 객체 속성 domain/range (둘 다 이름 있는 클래스)  : D —p— R
경로는 '볼 대상'(데이터 테이블 클래스, :sourceTable 이 있는 클래스)을 고르는 데만 쓴다. 숫자는 만들지 않는다.

허브 차단: 모든 테이블이 emd_code 로 읍면동(dim_emd)에 이어져 있고, 최상위 개념(시설·민원 분야 등)은 너무 많은
클래스를 잇는다. 이런 노드를 '거쳐 가면' 무관한 테이블이 2~3단계 안에 다 잡히므로, 경로의 끝점은 될 수 있어도
중간 경유지는 되지 못하게 한다.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TTL = ENGINE_ROOT / "ontology" / "jeju.ttl"
NS = "http://example.org/ontology#"
DIM_TABLES = {"dim_emd", "emd_geo", "complaint", "complaint_claim"}

# SPARQL 템플릿: '볼 대상 선택'에만 사용
SPARQL_TABLES_UNDER = """
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX : <http://example.org/ontology#>
SELECT DISTINCT ?cls ?table WHERE {
  ?cls rdfs:subClassOf* ?concept ; :sourceTable ?table .
}"""
SPARQL_LABELS = """
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?label WHERE { ?cls rdfs:label ?label . FILTER(lang(?label) = "ko") }"""


@dataclass(frozen=True)
class Edge:
    target: str
    label: str           # 사람이 읽는 관계 이름 (한글)


class OntologyGraph:
    def __init__(self, ttl: Path):
        from rdflib import BNode, Graph, URIRef
        from rdflib.namespace import OWL, RDF, RDFS, SKOS

        g = Graph()
        g.parse(ttl, format="turtle")
        self.graph = g
        self.labels: dict[str, str] = {}
        self.alt_labels: dict[str, list[str]] = {}
        self.en_labels: dict[str, str] = {}
        self.table_of: dict[str, str] = {}
        self.adj: dict[str, list[Edge]] = {}
        source_table = URIRef(NS + "sourceTable")

        classes = {str(c) for c in g.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef)}
        for c in classes:
            u = URIRef(c)
            for lab in g.objects(u, RDFS.label):
                if getattr(lab, "language", None) == "ko":
                    self.labels.setdefault(c, str(lab))
                elif getattr(lab, "language", None) == "en":
                    self.en_labels.setdefault(c, str(lab))
            self.alt_labels[c] = [str(a) for a in g.objects(u, SKOS.altLabel)]
            for t in g.objects(u, source_table):
                self.table_of[c] = str(t)

        def prop_label(p) -> str:
            for lab in g.objects(p, RDFS.label):
                if getattr(lab, "language", None) == "ko":
                    return str(lab)
            return str(p).split("#")[-1]

        def add(a: str, b: str, fwd: str, back: str) -> None:
            if a == b or a not in classes or b not in classes:
                return
            self.adj.setdefault(a, []).append(Edge(b, fwd))
            self.adj.setdefault(b, []).append(Edge(a, back))

        for c in classes:
            for sup in g.objects(URIRef(c), RDFS.subClassOf):
                if isinstance(sup, URIRef):
                    add(c, str(sup), "상위개념", "하위개념")
                elif isinstance(sup, BNode):
                    p = g.value(sup, OWL.onProperty)
                    filler = g.value(sup, OWL.someValuesFrom) or g.value(sup, OWL.allValuesFrom)
                    if p is not None and isinstance(filler, URIRef):
                        name = prop_label(p)
                        add(c, str(filler), name, f"{name}(역)")
        for p in g.subjects(RDF.type, OWL.ObjectProperty):
            d, r = g.value(p, RDFS.domain), g.value(p, RDFS.range)
            if isinstance(d, URIRef) and isinstance(r, URIRef):
                name = prop_label(p)
                add(str(d), str(r), name, f"{name}(역)")

        # 경유 금지 허브: 차원 테이블 클래스 + 하위개념이 3개 이상인 seed 최상위 개념(시설·민원 분야·인구 집단 등)
        named_parent = {c for c in classes for s in g.objects(URIRef(c), RDFS.subClassOf) if isinstance(s, URIRef)}
        children: dict[str, int] = {}
        for c in classes:
            for s in g.objects(URIRef(c), RDFS.subClassOf):
                if isinstance(s, URIRef) and c not in self.table_of:
                    children[str(s)] = children.get(str(s), 0) + 1
        self.blocked = {c for c, t in self.table_of.items() if t in DIM_TABLES}
        self.blocked |= {c for c in classes
                         if c not in self.table_of and c not in named_parent and children.get(c, 0) >= 3}
        self.classes = classes

    def label(self, c: str) -> str:
        return self.labels.get(c) or self.en_labels.get(c) or c.split("#")[-1]

    def paths_to_tables(self, starts: list[str], max_hops: int = 3) -> dict[str, tuple[int, str]]:
        """시작 개념들 → 테이블 클래스까지 최단 경로. {table_name: (hop 수, '개념 —관계→ 개념 …')}"""
        best: dict[str, tuple[int, str]] = {}
        for s in starts:
            if s not in self.classes:
                continue
            seen = {s}
            queue = deque([(s, [])])
            while queue:
                node, path = queue.popleft()
                if node in self.table_of and node != s or (node == s and node in self.table_of):
                    table = self.table_of[node]
                    if table not in DIM_TABLES:
                        hops = len(path)
                        text = self._render(s, path)
                        if table not in best or hops < best[table][0]:
                            best[table] = (hops, text)
                if len(path) >= max_hops or (node in self.blocked and node != s):
                    continue
                for e in self.adj.get(node, []):
                    if e.target not in seen:
                        seen.add(e.target)
                        queue.append((e.target, path + [(e.label, e.target)]))
        return best

    HIERARCHY = {"상위개념", "하위개념"}

    def _tables_below(self, c: str) -> bool:
        """c 자신 또는 하위개념 사슬 아래에 데이터 테이블 클래스가 있는가."""
        stack, seen = [c], {c}
        while stack:
            n = stack.pop()
            if n in self.table_of and self.table_of[n] not in DIM_TABLES:
                return True
            for e in self.adj.get(n, []):
                if e.label == "하위개념" and e.target not in seen:
                    seen.add(e.target)
                    stack.append(e.target)
        return False

    def is_backed(self, c: str) -> bool:
        """데이터가 뒷받침하는 개념인가: 아래에 테이블이 있거나, 테이블을 가진 개념이 관계로 이 개념을 가리킨다.
        (예: 의료 접근 ← 관련 분야 ← 의료 시설(아래에 병·의원 정보) → 뒷받침됨 / 무더위쉼터 → 아무것도 없음)"""
        if self._tables_below(c):
            return True
        return any(e.label not in self.HIERARCHY and self._tables_below(e.target) for e in self.adj.get(c, []))

    def descendants_with_tables(self, concept: str) -> list[str]:
        from rdflib import URIRef
        q = SPARQL_TABLES_UNDER.replace("?concept", f"<{concept}>")
        return sorted({str(r.table) for r in self.graph.query(q)} - DIM_TABLES) if concept in self.classes else []

    def _render(self, start: str, path: list[tuple[str, str]]) -> str:
        text = self.label(start)
        for rel, node in path:
            text += f" —{rel}→ {self.label(node)}"
        return text


@lru_cache(maxsize=None)
def load_graph(ttl: Path = DEFAULT_TTL) -> OntologyGraph | None:
    return OntologyGraph(ttl) if Path(ttl).exists() else None


def main() -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    g = load_graph()
    if g is None:
        raise SystemExit("ontology/jeju.ttl 이 없다 (ontology/build_ontology.py 참고)")
    start = NS + (sys.argv[1] if len(sys.argv) > 1 else "HealthcareAccess")
    for table, (hops, text) in sorted(g.paths_to_tables([start]).items(), key=lambda x: x[1][0]):
        print(f"{hops}  {table:30} {text}")


if __name__ == "__main__":
    main()
