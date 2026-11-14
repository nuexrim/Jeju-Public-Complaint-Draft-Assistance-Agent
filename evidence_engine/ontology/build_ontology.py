"""온톨로지 생성 (개발 단계 전용, RIGOR 사용). 센터 PC에서는 돌리지 않는다.

    # RIGOR 전용 가상환경(external/.venv-rigor)에서 실행
    python ontology/export_schema.py                          # 먼저 입력 만들기 (일반 .venv)
    external/.venv-rigor/bin/python ontology/build_ontology.py            # 결정적 빌드: LLM 호출 없음
    external/.venv-rigor/bin/python ontology/build_ontology.py --paid --max-calls 150   # RIGOR LLM 보강 (승인 후)
    external/.venv-rigor/bin/python ontology/build_ontology.py --accept    # 검수 끝난 후보를 jeju.ttl 로 확정

결정적 빌드 = seed_core.ttl + RIGOR 직접 매핑(테이블→클래스, 컬럼→속성, emd_code→읍면동 관계)
            + table_concepts.yaml 연결 + 한글 라벨(@ko: 데이터명·컬럼 원래 이름) + :sourceTable 주석.
--paid      = RIGOR run_semantic_enrichment(Gen-LLM + Judge-LLM)을 그대로 돌리되, 모든 LLM 호출을
              agent/llm_client.py(ALLOW_PAID_CALLS 가드·호출 수 상한·사용량 로그·디스크 캐시)로 돌린다.
              공급자·모델은 config.yaml dev_llm (기본 anthropic = ANTHROPIC_API_KEY). RIGOR 원래의 OpenRouter 호출은 쓰지 않는다.
              그 결과에 위 결정적 후처리를 똑같이 얹는다.
두 경우 모두 HermiT 일관성 검사를 하고 build/ 에 후보를 쓴다. jeju.ttl 은 --accept 로만 바뀐다.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))

ONTO = ENGINE_ROOT / "ontology"
BUILD = ONTO / "build"
RIGOR_DIR = ENGINE_ROOT / "external" / "RIGOR"
CANDIDATE = BUILD / "jeju_candidate.ttl"
FINAL = ONTO / "jeju.ttl"
REPORT = BUILD / "build_report.json"
DERIVED_KO = {   # dataset 테이블에 데이터명이 없는 파생·차원 테이블
    "bus_usage_monthly": "읍면동별 월별 버스 정류소 이용 인원(파생 집계)",
    "emd_geo": "행정동 경계 면적·중심점(파생)",
    "dim_emd": "제주 행정동 코드표",
}
KO_LABEL_INSTRUCTION = (
    "\n[ADDITIONAL REQUIREMENT]\n"
    "- In addition to the English label, give every class and property one rdfs:label in Korean, e.g. "
    'rdfs:label "버스 정류소"@ko. Use the Korean names in the retrieved documentation when available.\n'
    "- When a class is a kind of a seed core concept (e.g. TransportFacility, HealthcareFacility, ShelterFacility, "
    "PopulationGroup, ServiceUsage, Incident), add a SubClassOf axiom to that concept.\n"
)


def load_rigor():
    if not (RIGOR_DIR / "app.py").exists():
        raise SystemExit(f"RIGOR가 없다: {RIGOR_DIR} (config.yaml ontology_build.rigor_repo 를 clone)")
    sys.path.insert(0, str(RIGOR_DIR))
    import app  # RIGOR app.py
    return app


def route_llm_through_client(app, provider: str, model: str, max_calls: int, max_tokens: int):
    """RIGOR의 OpenRouter 호출을 우리 LLMClient(설정한 공급자)로 바꾼다 (가드·상한·로그·캐시)."""
    from agent.llm_client import client_from_config
    client = client_from_config("dev_llm", provider=provider, model=model, max_tokens=max_tokens,
                                max_calls=max_calls, cache_dir=BUILD / "llm_cache",
                                temperature=0.2 if provider == "openrouter" else None)   # Claude 최신 모델은 temperature 불가

    def generate(self, prompt, temperature=0.2, max_tokens=max_tokens, retries=1):
        return client.complete(prompt, purpose="rigor", max_tokens=min(max_tokens, client.max_tokens)).text

    app.OpenRouterLLM.__init__ = lambda self, api_key=None, model=model: setattr(self, "model", model)
    app.OpenRouterLLM.generate = generate
    original = app.OntologyLLM._build_prompt
    app.OntologyLLM._build_prompt = lambda self, *a, **k: original(self, *a, **k) + KO_LABEL_INSTRUCTION
    return client


def check_no_collision(app, seed, schema) -> None:
    """seed 개념 IRI가 테이블 클래스 IRI(RIGOR 이름 규칙)와 겹치면 둘이 한 클래스로 합쳐지므로 막는다."""
    from rdflib.namespace import OWL, RDF
    seed_classes = set(seed.subjects(RDF.type, OWL.Class))
    clash = sorted(t for t in schema if app.uri(app.to_class_name(t)) in seed_classes)
    if clash:
        raise SystemExit(f"seed 개념 이름이 테이블 클래스와 겹친다: {clash} → seed_core.ttl 이름을 바꾼다")


def deterministic_core(app, schema_path: Path):
    from rdflib import Graph
    schema = app.load_schema_from_json(schema_path)
    core = Graph()
    app.ensure_namespaces(core)
    core.parse(ONTO / "seed_core.ttl", format="turtle")
    check_no_collision(app, core, schema)
    for table in app.traverse_schema_fk_order(schema):
        app.merge_graph_strict(core, app.direct_mapping_graph_for_table(table, schema))
    return core, schema


def enrich_korean(app, core, schema) -> dict:
    """테이블 클래스를 seed 개념 아래에 붙이고, 한글 라벨과 원본 테이블 이름을 단다(결정적)."""
    import duckdb
    from rdflib import BNode, Literal, Namespace
    from rdflib.namespace import OWL, RDF, RDFS

    base = Namespace(f"{app.ONTOLOGY_IRI}#")
    source_table = base["sourceTable"]
    core.add((source_table, RDF.type, OWL.AnnotationProperty))
    core.add((source_table, RDFS.label, Literal("원본 테이블", lang="ko")))

    links = yaml.safe_load((ONTO / "table_concepts.yaml").read_text(encoding="utf-8"))
    colmap = json.loads((BUILD / "column_map.json").read_text(encoding="utf-8"))
    con = duckdb.connect(str(ENGINE_ROOT / "data" / "jeju.duckdb"), read_only=True)
    names = dict(con.execute("SELECT table_name, dataset FROM dataset").fetchall())
    con.close()
    from ontology.export_schema import COMPLAINT_TABLES

    stats = {"linked": 0, "unlinked": []}
    for table in schema:
        cls = app.uri(app.to_class_name(table))
        core.add((cls, source_table, Literal(table)))
        ko = names.get(table) or COMPLAINT_TABLES.get(table, {}).get("ko") or DERIVED_KO.get(table)
        if ko:
            core.add((cls, RDFS.label, Literal(ko, lang="ko")))
        parents = links["tables"].get(table, [])
        for parent in parents:
            core.add((cls, RDFS.subClassOf, base[parent]))
        if parents:
            stats["linked"] += 1
        else:
            stats["unlinked"].append(table)
        for prop, target in (links.get("relations", {}).get(table) or {}).items():
            r = BNode()
            core.add((r, RDF.type, OWL.Restriction))
            core.add((r, OWL.onProperty, base[prop]))
            core.add((r, OWL.someValuesFrom, base[target]))
            core.add((cls, RDFS.subClassOf, r))
        for col_ascii, col_ko in colmap.get(table, {}).items():
            core.add((app.uri(app.data_property_name(table, col_ascii)), RDFS.label, Literal(col_ko, lang="ko")))
    # 민원 → 민원 분야 관계
    complaint = app.uri(app.to_class_name("complaint"))
    r = BNode()
    core.add((r, RDF.type, OWL.Restriction))
    core.add((r, OWL.onProperty, base["concernsDomain"]))
    core.add((r, OWL.someValuesFrom, base["ComplaintDomain"]))
    core.add((complaint, RDFS.subClassOf, r))
    return stats


def hermit_jar() -> Path:
    """RIGOR 저장소의 jars/HermiT.jar 는 빈 자리표시 파일(2바이트)이라 owlready2에 들어 있는 공식 HermiT를 쓴다."""
    jar = RIGOR_DIR / "jars" / "HermiT.jar"
    if jar.exists() and jar.stat().st_size > 1_000_000:
        return jar
    import owlready2
    return Path(owlready2.__file__).parent / "hermit" / "HermiT.jar"


def hermit(owl_path: Path) -> dict:
    jar = hermit_jar()
    cmd = ["java", "-cp", str(jar), "org.semanticweb.HermiT.cli.CommandLine",
           "--ignoreUnsupportedDatatypes", "-k", owl_path.resolve().as_uri()]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"consistent": None, "error": str(exc)}
    blob = (p.stdout + "\n" + p.stderr).lower()
    if "inconsistent" in blob or "is not satisfiable" in blob or "unsatisfiable" in blob:
        ok = False
    elif p.returncode == 0:
        ok = True
    else:
        ok = None
    return {"consistent": ok, "return_code": p.returncode, "output": p.stdout[-800:], "stderr": p.stderr[-800:]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paid", action="store_true", help="RIGOR LLM 보강 (ALLOW_PAID_CALLS=1 과 승인 필요)")
    ap.add_argument("--max-calls", type=int, default=0, help="--paid 때 최대 LLM 호출 수 (승인받은 수)")
    ap.add_argument("--max-tokens", type=int, default=16000,
                    help="호출당 최대 출력 토큰 (Claude 는 thinking 토큰도 여기 포함)")
    ap.add_argument("--provider", choices=["anthropic", "openrouter"], help="없으면 config.yaml dev_llm.provider")
    ap.add_argument("--model", help="모델 id (없으면 config.yaml dev_llm.model)")
    ap.add_argument("--embed-model", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                    help="--paid 때 RIGOR RAG 검색용 임베딩 모델(한국어 문서라 다국어 모델)")
    ap.add_argument("--accept", action="store_true", help="build/jeju_candidate.ttl 을 ontology/jeju.ttl 로 확정")
    args = ap.parse_args()

    if args.accept:
        if not REPORT.exists() or not CANDIDATE.exists():
            raise SystemExit("확정할 후보가 없다. 먼저 빌드한다.")
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        if report.get("hermit", {}).get("consistent") is not True:
            raise SystemExit("HermiT 일관성 검사를 통과한 후보만 확정한다.")
        shutil.copyfile(CANDIDATE, FINAL)
        print(f"확정: {FINAL} (mode={report['mode']}, {report['triples']} triples)")
        return

    for stale in (CANDIDATE, REPORT, BUILD / "jeju_candidate.owl"):   # 실패한 빌드의 옛 후보가 확정되지 않게
        stale.unlink(missing_ok=True)
    schema_path = BUILD / "schema_jeju.json"
    if not schema_path.exists():
        raise SystemExit("먼저 python ontology/export_schema.py 를 실행한다.")
    app = load_rigor()
    from rdflib import Graph

    if args.paid:
        from agent.llm_client import paid_calls_allowed
        if not paid_calls_allowed():
            raise SystemExit("ALLOW_PAID_CALLS=1 이 없다. 승인 후 사람이 직접 설정한다.")
        if args.max_calls <= 0:
            raise SystemExit("--max-calls 로 승인받은 최대 호출 수를 준다.")
        cfg = yaml.safe_load((ENGINE_ROOT / "config.yaml").read_text(encoding="utf-8"))
        dev = cfg.get("dev_llm") or {}
        provider = args.provider or dev.get("provider") or "anthropic"
        model = args.model or dev.get("model")
        if not model:
            raise SystemExit("모델 id 가 없다 (--model 또는 config.yaml dev_llm.model).")
        app.EMBED_MODEL_NAME = args.embed_model
        client = route_llm_through_client(app, provider, model, args.max_calls, args.max_tokens)
        raw = BUILD / "rigor_enriched.owl"
        import os
        # RIGOR 코드는 OPENROUTER_API_KEY 가 있어야 LLM 단계를 켠다. 실제 호출은 위에서 llm_client 로 바꿨으므로
        # 여기 값은 쓰이지 않는 표시용이다(진짜 키는 llm_client 가 .env 에서 읽는다).
        os.environ.setdefault("OPENROUTER_API_KEY", "routed-through-llm_client")
        app.run_semantic_enrichment(
            schema_path=schema_path, docs_path=ONTO / "rag_docs", ontology_path=BUILD / "no_external_ontologies",
            core_owl_path=BUILD / "seed_core.owl", output_path=raw, direct_mappings_dir=BUILD / "direct_mappings",
            model=model, use_llm=True,
        )
        core = Graph()
        app.ensure_namespaces(core)
        core.parse(raw, format="xml")
        schema = app.load_schema_from_json(schema_path)
        mode, calls = f"rigor:{provider}:{model}", client.calls
    else:
        core, schema = deterministic_core(app, schema_path)
        mode, calls = "deterministic", 0

    stats = enrich_korean(app, core, schema)
    report_v = app.validate_graph_against_rigor(core, schema)
    from rdflib import Literal, URIRef
    from rdflib.namespace import OWL, RDF
    onto = URIRef(app.ONTOLOGY_IRI)
    core.add((onto, RDF.type, OWL.Ontology))
    core.add((onto, app.PROV.generatedAtTime, Literal(datetime.now(timezone.utc).isoformat())))
    BUILD.mkdir(parents=True, exist_ok=True)
    core.serialize(CANDIDATE, format="turtle")
    owl = BUILD / "jeju_candidate.owl"
    core.serialize(owl, format="xml")
    h = hermit(owl)
    report = {
        "mode": mode, "llm_calls": calls, "triples": len(core),
        "classes": len(set(core.subjects(RDF.type, OWL.Class))),
        "object_properties": len(set(core.subjects(RDF.type, OWL.ObjectProperty))),
        "tables_linked_to_seed": stats["linked"], "tables_unlinked": stats["unlinked"],
        "rigor_validation_errors": report_v.errors[:20], "rigor_validation_warnings": len(report_v.warnings),
        "hermit": h,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "hermit"}, ensure_ascii=False, indent=2))
    print(f"HermiT 일관성: {h.get('consistent')}  → 후보 {CANDIDATE}")
    print("검수 후 --accept 로 ontology/jeju.ttl 확정")


if __name__ == "__main__":
    main()
