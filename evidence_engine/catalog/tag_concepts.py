"""카탈로그 카드 개념 태그 (cards.parquet 의 concepts 컬럼).

태그 어휘 = 온톨로지 seed 개념(ontology/seed_core.ttl)의 IRI 이름 (예: HealthcareFacility, BusRidership).
닫힌 어휘라서 LLM이 새 개념을 지어낼 수 없고, 어휘 밖 응답은 버린다.

    python catalog/tag_concepts.py --rules                 # 규칙 태그: 제목·분류에 개념 라벨/별칭이 있으면 태그 (무료)
    python catalog/tag_concepts.py --llm --max-calls 60    # LLM 태그 (유료: 승인 후 ALLOW_PAID_CALLS=1 필요, config.yaml dev_llm)
    python catalog/tag_concepts.py --export-review         # 사람 검수용 CSV (catalog/concept_tags_review.csv)
    python catalog/tag_concepts.py --import-review         # 검수한 CSV를 반영 (concept_source=reviewed)

concept_source: rules | llm | reviewed. 실행 중(엔진)에는 LLM을 부르지 않고 저장된 태그만 쓴다.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import duckdb

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))

CARDS = ENGINE_ROOT / "catalog" / "cards.parquet"
REVIEW = ENGINE_ROOT / "catalog" / "concept_tags_review.csv"
SEED = ENGINE_ROOT / "ontology" / "seed_core.ttl"
BATCH = 25
NS = "http://example.org/ontology#"


def vocabulary() -> dict[str, dict]:
    """{개념 이름: {ko, alts, parent}} — seed_core.ttl 의 클래스."""
    from rdflib import Graph, URIRef
    from rdflib.namespace import OWL, RDF, RDFS, SKOS
    g = Graph()
    g.parse(SEED, format="turtle")
    out = {}
    for c in g.subjects(RDF.type, OWL.Class):
        if not isinstance(c, URIRef):
            continue
        name = str(c).replace(NS, "")
        ko = next((str(l) for l in g.objects(c, RDFS.label) if getattr(l, "language", None) == "ko"), name)
        parent = next((str(s).replace(NS, "") for s in g.objects(c, RDFS.subClassOf) if isinstance(s, URIRef)), None)
        out[name] = {"ko": ko, "alts": [str(a) for a in g.objects(c, SKOS.altLabel)], "parent": parent}
    return out


def rule_tags(title: str, category: str, vocab: dict[str, dict]) -> list[str]:
    text = re.sub(r"\s+", "", f"{title} {category}")
    tags = []
    for name, v in vocab.items():
        names = [v["ko"], *v["ko"].split("·"), *v["alts"]]
        if any(len(n.replace(" ", "")) >= 2 and n.replace(" ", "") in text for n in names):
            tags.append(name)
    # 더 구체적인 개념이 걸렸으면 그 조상 개념은 모두 뺀다
    def ancestors(n: str) -> set[str]:
        out, p = set(), vocab[n]["parent"]
        while p and p in vocab and p not in out:
            out.add(p)
            p = vocab[p]["parent"]
        return out
    covered = set().union(*(ancestors(t) for t in tags)) if tags else set()
    return sorted(t for t in tags if t not in covered)


def load_cards(con) -> list[dict]:
    cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{CARDS.as_posix()}')").fetchall()]
    return [dict(zip(cols, r)) for r in con.execute(f"SELECT * FROM read_parquet('{CARDS.as_posix()}')").fetchall()]


def save_cards(con, cards: list[dict]) -> None:
    tmp = CARDS.with_suffix(".jsonl")
    with tmp.open("w", encoding="utf-8") as f:
        for c in cards:
            f.write(json.dumps({k: (str(v) if hasattr(v, "isoformat") else v) for k, v in c.items()}, ensure_ascii=False) + "\n")
    con.execute(
        f"COPY (SELECT * FROM read_json_auto('{tmp.as_posix()}', format='newline_delimited', sample_size=-1)) "
        f"TO '{CARDS.as_posix()}' (FORMAT parquet)"
    )
    tmp.unlink()


def llm_prompt(batch: list[dict], vocab: dict[str, dict]) -> str:
    lines = [f"- {n}: {v['ko']}" + (f" (예: {', '.join(v['alts'][:4])})" if v["alts"] else "") for n, v in vocab.items()]
    items = [{"card_id": c["card_id"], "title": c["title"], "category": c["category"],
              "description": (c["description"] or "")[:300]} for c in batch]
    return (
        "제주 공공데이터 데이터셋 카드마다, 아래 개념 목록에서 해당하는 개념 이름을 0~3개 고르세요.\n"
        "목록에 없는 이름은 쓰지 마세요. 해당 없으면 빈 목록.\n\n[개념 목록]\n" + "\n".join(lines) +
        "\n\n[카드]\n" + json.dumps(items, ensure_ascii=False) +
        '\n\n[출력] JSON 객체 하나만: {"<card_id>": ["개념이름", ...], ...}'
    )


def parse_llm(text: str, vocab: dict[str, dict], ids: set[str]) -> dict[str, list[str]]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    return {cid: sorted({t for t in tags if t in vocab}) for cid, tags in raw.items()
            if cid in ids and isinstance(tags, list)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rules", action="store_true")
    mode.add_argument("--llm", action="store_true")
    mode.add_argument("--export-review", action="store_true")
    mode.add_argument("--import-review", action="store_true")
    ap.add_argument("--max-calls", type=int, default=0, help="--llm: 승인받은 최대 호출 수")
    ap.add_argument("--only-untagged", action="store_true", help="--llm: 규칙 태그가 없는 카드만")
    args = ap.parse_args()

    con = duckdb.connect()
    cards = load_cards(con)
    for c in cards:
        c.setdefault("concept_source", None)
    vocab = vocabulary()

    if args.rules:
        n = 0
        for c in cards:
            if c.get("concept_source") in ("llm", "reviewed"):
                continue
            tags = rule_tags(c["title"] or "", c["category"] or "", vocab)
            c["concepts"] = json.dumps(tags, ensure_ascii=False)
            c["concept_source"] = "rules" if tags else None
            n += bool(tags)
        save_cards(con, cards)
        print(f"규칙 태그: {n}/{len(cards)} 카드")
    elif args.llm:
        from agent.llm_client import client_from_config, paid_calls_allowed
        if not paid_calls_allowed():
            raise SystemExit("ALLOW_PAID_CALLS=1 이 없다. 승인 후 사람이 직접 설정한다.")
        if args.max_calls <= 0:
            raise SystemExit("--max-calls 로 승인받은 최대 호출 수를 준다.")
        # 닫힌 어휘 분류라 effort low (thinking 토큰도 max_tokens 에 들어가므로 여유 있게)
        client = client_from_config("dev_llm", max_tokens=8000, max_calls=args.max_calls, effort="low",
                                    cache_dir=ENGINE_ROOT / "catalog" / ".llm_cache")
        if not client.model:
            raise SystemExit("모델 id 가 없다 (config.yaml dev_llm.model).")
        todo = [c for c in cards if c.get("concept_source") != "reviewed"
                and (not args.only_untagged or c["concepts"] in (None, "[]"))]
        done = 0
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            r = client.complete(llm_prompt(batch, vocab), purpose="tag_concepts")
            tags = parse_llm(r.text, vocab, {c["card_id"] for c in batch})
            for c in batch:
                if c["card_id"] in tags:
                    c["concepts"] = json.dumps(tags[c["card_id"]], ensure_ascii=False)
                    c["concept_source"] = "llm"
                    done += 1
            save_cards(con, cards)              # 배치마다 저장: 중간에 멈춰도 결과가 남는다
        print(f"LLM 태그: {done}/{len(todo)} 카드, 실제 호출 {client.calls}회 (logs/usage.csv)")
    elif args.export_review:
        with REVIEW.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["card_id", "title", "category", "concepts", "concept_source", "검수(고친 개념, ;로 구분)"])
            for c in cards:
                w.writerow([c["card_id"], c["title"], c["category"], ";".join(json.loads(c["concepts"] or "[]")),
                            c.get("concept_source") or "", ""])
        print(f"검수용 → {REVIEW}")
    else:
        fixed = {}
        with REVIEW.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                v = r["검수(고친 개념, ;로 구분)"].strip()
                if v:
                    tags = [t.strip() for t in v.split(";") if t.strip()]
                    bad = [t for t in tags if t not in vocab and t != "-"]
                    if bad:
                        raise SystemExit(f"{r['card_id']}: 어휘에 없는 개념 {bad}")
                    fixed[r["card_id"]] = [t for t in tags if t != "-"]
        for c in cards:
            if c["card_id"] in fixed:
                c["concepts"] = json.dumps(fixed[c["card_id"]], ensure_ascii=False)
                c["concept_source"] = "reviewed"
        save_cards(con, cards)
        print(f"검수 반영: {len(fixed)} 카드")


if __name__ == "__main__":
    main()
