"""DuckDB → RIGOR 입력 (개발 단계 전용).

    python ontology/export_schema.py

만드는 것 (ontology/build/, rag_docs/generated/ 는 git 제외 · 다시 만들 수 있음)
- build/schema_jeju.json : RIGOR sql_schema 형식. 차원(dim_emd)·사실(적재 테이블)·민원 테이블.
                           emd_code 가 있는 테이블은 모두 dim_emd 로 외래키를 건다.
                           컬럼명은 ASCII 식별자로 바꾼다(column_names.yaml). RIGOR가 한글을 "_"로 뭉개기 때문.
- build/column_map.json  : {테이블: {ASCII 식별자: 원래 한글 컬럼명}}
- build/seed_core.owl    : seed_core.ttl 의 RDF/XML 판 (RIGOR core_owl_path 입력)
- rag_docs/generated/*.md: 테이블별 데이터 카드 설명(데이터명·제공기관·설명·컬럼 한글명)과 민원 분야·지표 설명
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import duckdb
import yaml

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))

ONTO = ENGINE_ROOT / "ontology"
BUILD = ONTO / "build"
DOCS = ONTO / "rag_docs" / "generated"
DB = ENGINE_ROOT / "data" / "jeju.duckdb"
CARDS = ENGINE_ROOT / "catalog" / "cards.parquet"

SKIP_TABLES = {"dataset", "bus_station_emd", "pop_tongban"}          # 메타·기술용 테이블
SKIP_COLUMNS = {"emd_method", "period"}               # 적재 과정에서 붙인 기술 컬럼
TYPE_MAP = {
    "VARCHAR": "TEXT", "BIGINT": "INTEGER", "INTEGER": "INTEGER", "SMALLINT": "INTEGER", "TINYINT": "INTEGER",
    "HUGEINT": "INTEGER", "DOUBLE": "DOUBLE", "FLOAT": "FLOAT", "DATE": "DATE", "TIME": "TIME",
    "TIMESTAMP": "DATETIME", "BOOLEAN": "BOOLEAN",
}
_ASCII = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# 민원 쪽 개념(구조화 질의 → 엔진)도 스키마에 넣어 온톨로지가 민원·주장·읍면동을 알게 한다.
COMPLAINT_TABLES = {
    "complaint": {
        "columns": {
            "request_id": {"type": "TEXT", "primary_key": True, "nullable": False},
            "complaint_type": {"type": "TEXT", "nullable": False},
            "normalized_text": {"type": "TEXT", "nullable": False},
            "user_request": {"type": "TEXT", "nullable": True},
            "emd_code": {"type": "TEXT", "nullable": True},
        },
        "foreign_keys": [{"column": "emd_code", "references_table": "dim_emd", "references_column": "emd_code"}],
        "ko": "민원(구조화 질의)",
    },
    "complaint_claim": {
        "columns": {
            "claim_id": {"type": "TEXT", "primary_key": True, "nullable": False},
            "request_id": {"type": "TEXT", "nullable": False},
            "claim_text": {"type": "TEXT", "nullable": False},
        },
        "foreign_keys": [{"column": "request_id", "references_table": "complaint", "references_column": "request_id"}],
        "ko": "민원 속 주장(claim)",
    },
}


def ascii_name(col: str, names: dict[str, str]) -> str | None:
    if _ASCII.match(col):
        return col
    return names.get(col)


def build_schema(con, names: dict[str, str]) -> tuple[dict, dict, list[str]]:
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY 1").fetchall()]
    schema, colmap, dropped = {}, {}, []
    for t in tables:
        if t in SKIP_TABLES:
            continue
        cols, cmap, seen = {}, {}, set()
        for name, dtype, *_ in con.execute(f'DESCRIBE "{t}"').fetchall():
            if name in SKIP_COLUMNS:
                continue
            a = ascii_name(name, names)
            if not a or a in seen:
                dropped.append(f"{t}.{name}")
                continue
            seen.add(a)
            base = dtype.split("(")[0].upper()
            cols[a] = {
                "type": TYPE_MAP.get(base, "TEXT"), "raw_type": dtype,
                "nullable": not (t == "dim_emd" and a == "emd_code"),
                "primary_key": a == "emd_code" and t in {"dim_emd", "pop_emd", "emd_geo"},
                "unique": False, "default": None,
            }
            if a != name:
                cmap[a] = name
        fks = []
        if "emd_code" in cols and t != "dim_emd":
            fks.append({"column": "emd_code", "references_table": "dim_emd", "references_column": "emd_code",
                        "on_update": "NO ACTION", "on_delete": "NO ACTION"})
        schema[t] = {"columns": cols, "foreign_keys": fks}
        colmap[t] = cmap
    for t, spec in COMPLAINT_TABLES.items():
        schema[t] = {"columns": {c: {**v, "raw_type": v["type"], "unique": False, "default": None}
                                 for c, v in spec["columns"].items()},
                     "foreign_keys": spec["foreign_keys"]}
        colmap[t] = {}
    return schema, colmap, dropped


def _card_columns(columns_json: str | None) -> dict[str, str]:
    try:
        return {c["name"]: c.get("label") or "" for c in json.loads(columns_json or "[]")}
    except (TypeError, ValueError):
        return {}


def write_docs(con, schema: dict, colmap: dict) -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    for old in DOCS.glob("*.md"):
        old.unlink()
    meta = {r[0]: r for r in con.execute(
        "SELECT table_name, dataset, publisher, hub_id, period_end, url FROM dataset").fetchall()}
    cards = {}
    if CARDS.exists():
        for cid, title, desc, cols in duckdb.connect().execute(
                f"SELECT card_id, title, description, columns FROM read_parquet('{CARDS.as_posix()}')").fetchall():
            cards[cid] = (title, desc, _card_columns(cols))
    concepts = yaml.safe_load((ONTO / "table_concepts.yaml").read_text(encoding="utf-8"))["tables"]
    n = 0
    for t, spec in schema.items():
        lines = [f"# 테이블 {t}"]
        m = meta.get(t)
        card = cards.get(f"hub-{m[3]}") if m and m[3] is not None else None
        if m:
            lines += [f"- 데이터명: {m[1]}", f"- 제공: {m[2]}", f"- 기준일: {m[4] or '미상'}", f"- 출처: {m[5]}"]
        elif t in COMPLAINT_TABLES:
            lines += [f"- 의미: {COMPLAINT_TABLES[t]['ko']}"]
        if t in concepts:
            lines.append(f"- 상위 개념(seed): {', '.join(concepts[t])}")
        if card and card[1]:
            lines += ["", "## 설명", card[1].strip()]
        lines += ["", "## 컬럼"]
        labels = card[2] if card else {}
        for c in spec["columns"]:
            ko = colmap.get(t, {}).get(c) or labels.get(c) or ""
            lines.append(f"- {c}" + (f": {ko}" if ko else ""))
        if "emd_code" in spec["columns"]:
            lines.append("- (emd_code 는 제주 행정동 8자리 코드, dim_emd 참조)")
        (DOCS / f"{t}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        n += 1

    reg = yaml.safe_load((ENGINE_ROOT / "indicators" / "registry.yaml").read_text(encoding="utf-8"))
    dom = ["# 제주 민원 분야와 근거 지표",
           "민원 유형: 교통, 의료 접근, 환경·청소, 안전·재난, 생활물가.",
           "읍면동(행정동) 43곳이 공간 단위이며 emd_code 로 모든 테이블이 연결된다.", "", "## 지표"]
    for ind in reg["indicators"]:
        dom.append(f"- {ind['name']} ({ind['unit']}): 테이블 {', '.join(ind['tables'])}; 관련 낱말 {', '.join(ind['keywords'])}")
    for g in reg.get("known_gaps", []):
        dom.append(f"- 빈칸: {g['concept']} — {g['reason']}")
    (DOCS / "_domain.md").write_text("\n".join(dom) + "\n", encoding="utf-8")
    return n + 1


def write_seed_owl() -> Path:
    from rdflib import Graph
    g = Graph()
    g.parse(ONTO / "seed_core.ttl", format="turtle")
    out = BUILD / "seed_core.owl"
    g.serialize(out, format="xml")
    return out


def main() -> None:
    names = yaml.safe_load((ONTO / "column_names.yaml").read_text(encoding="utf-8"))
    con = duckdb.connect(str(DB), read_only=True)
    schema, colmap, dropped = build_schema(con, names)
    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "schema_jeju.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    (BUILD / "column_map.json").write_text(json.dumps(colmap, ensure_ascii=False, indent=2), encoding="utf-8")
    docs = write_docs(con, schema, colmap)
    seed = write_seed_owl()
    con.close()
    ncols = sum(len(s["columns"]) for s in schema.values())
    print(f"테이블 {len(schema)}개, 컬럼 {ncols}개 → {BUILD / 'schema_jeju.json'}")
    print(f"RAG 문서 {docs}개 → {DOCS}")
    print(f"seed core → {seed}")
    if dropped:
        print(f"영문 식별자가 없어 뺀 컬럼 {len(dropped)}개: {', '.join(dropped[:12])}{' …' if len(dropped) > 12 else ''}")


if __name__ == "__main__":
    main()
