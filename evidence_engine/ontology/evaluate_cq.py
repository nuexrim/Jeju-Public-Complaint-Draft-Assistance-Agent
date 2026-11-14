"""역량 질문 15개로 온톨로지(B)를 평가하고 채택 여부를 판정한다.

    python ontology/evaluate_cq.py            # 표 출력, 10개 이상 통과면 '채택'
비교용으로 같은 질문을 카탈로그 BM25 상위 5개만으로 찾았을 때 맞힌 수도 함께 보여 준다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))

from ontology.paths import load_graph  # noqa: E402
from retrieval.ontology_expand import expand  # noqa: E402

CQ = ENGINE_ROOT / "tests" / "competency_questions.yaml"
PASS_MARK = 10


def evaluate(graph=None, table_cards: dict | None = None) -> list[dict]:
    graph = graph or load_graph()
    if graph is None:
        raise SystemExit("ontology/jeju.ttl 이 없다")
    out = []
    for q in yaml.safe_load(CQ.read_text(encoding="utf-8"))["questions"]:
        e = expand(q["question"], "", graph, table_cards or {})
        if q.get("expect_gap"):
            ok = any(q["expect_gap"] in g["concept"] for g in e.gaps)
            found = [g["concept"] for g in e.gaps]
            path = ""
        else:
            hits = [t for t in q["expect_any"] if t in e.table_paths]
            ok = bool(hits)
            found = hits
            path = e.table_paths[hits[0]][1] if hits else ""
        out.append({"id": q["id"], "question": q["question"], "ok": ok, "found": found,
                    "concepts": e.concepts, "path": path})
    return out


def catalog_baseline(questions: list[dict]) -> int:
    """비교: 온톨로지 없이 카탈로그 BM25 상위 5개에 기대 테이블의 카드가 있었는가."""
    import duckdb
    from catalog.index import load_index
    con = duckdb.connect(str(ENGINE_ROOT / "data" / "jeju.duckdb"), read_only=True)
    card_of = {t: f"hub-{h}" for t, h in con.execute("SELECT table_name, hub_id FROM dataset WHERE hub_id IS NOT NULL").fetchall()}
    con.close()
    n = 0
    for q in questions:
        if q.get("expect_gap"):
            continue
        want = {card_of.get(t) for t in q["expect_any"]} - {None}
        top = {c.card_id for c, _ in load_index().search(q["question"], 5)}
        n += bool(want & top)
    return n


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    results = evaluate()
    for r in results:
        mark = "통과" if r["ok"] else "실패"
        print(f"{r['id']} {mark}  {r['question']}\n      개념 {r['concepts']} → {r['found']}\n      {r['path']}")
    passed = sum(r["ok"] for r in results)
    raw = yaml.safe_load(CQ.read_text(encoding="utf-8"))["questions"]
    base = catalog_baseline(raw)
    print(f"\n온톨로지: {passed}/{len(results)} 통과 (기준 {PASS_MARK})  → "
          f"{'use_ontology=true 유지' if passed >= PASS_MARK else 'use_ontology=false 권고'}")
    print(f"비교(카탈로그 BM25 상위 5개만): {base}/{sum(1 for q in raw if not q.get('expect_gap'))} (빈칸 질문 제외)")


if __name__ == "__main__":
    main()
