"""근거 패키지 엔진 CLI.

    python run_engine.py --query tests/fixtures/q01.json
    python run_engine.py --query tests/fixtures/q01.json --out out.json --no-ontology
    python run_engine.py --query tests/fixtures/q01.json --mock      # 계산 없이 형식만 맞춘 가짜 응답

처리: 입력 검증 → 읍면동 코드 → (B) 온톨로지 개념 확장 → (A) 카탈로그 검색 → 병합·순위
→ 지표 계산(DuckDB 읽기 전용) → 규칙 판정 → 계약 검증.
use_ontology=true 이고 ontology/jeju.ttl 이 있으면 engine="hybrid", 아니면 "catalog".
DB(data/jeju.duckdb)가 없으면 README의 데이터 준비 단계를 먼저 해야 한다. 그 전에는 --mock을 쓴다.
종료 코드: 0 성공, 2 계약 위반 또는 입력 파일 오류.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

ENGINE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_ROOT))

from package.builder import ContractError, build_mock_package, validate_query  # noqa: E402
from package.privacy import mask_query  # noqa: E402


def load_config(path: Path | None = None) -> dict:
    return yaml.safe_load((path or ENGINE_ROOT / "config.yaml").read_text(encoding="utf-8"))


def run(query: dict, config: dict | None = None, *, use_ontology: bool | None = None,
        mock: bool = False, engine=None) -> dict:
    """구조화 질의 dict → 근거 패키지 dict. raw_text는 읽지도 남기지도 않는다.
    검증 직후 이름·연락처·상세주소 등을 가린 사본(package/privacy.py)만 쓴다.

    engine: 여러 질의를 돌릴 때 package.assemble.Engine 하나를 만들어 넘기면 DB를 한 번만 연다.
    """
    config = config or load_config()
    started = time.perf_counter()
    validate_query(query)
    query = mask_query(query)            # raw_text 제거 + 개인정보 마스킹 (이후 모든 단계는 이 사본만 본다)
    if use_ontology is None:
        use_ontology = bool(config["use_ontology"])
    if mock:
        package = build_mock_package(
            query,
            use_ontology=use_ontology,
            emd_codes_path=ENGINE_ROOT / config["paths"]["emd_codes"],
        )
        package["meta"]["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return package

    from package.assemble import Engine
    own = engine is None
    engine = engine or Engine(config)
    try:
        return engine.build(query, use_ontology)   # jeju.ttl 이 없으면 자동으로 A 경로
    finally:
        if own:
            engine.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="근거 패키지 엔진")
    parser.add_argument("--query", required=True, type=Path, help="구조화 질의 JSON 파일")
    parser.add_argument("--out", type=Path, help="근거 패키지를 저장할 파일 (없으면 표준 출력)")
    parser.add_argument("--no-ontology", action="store_true", help="use_ontology=false로 실행")
    parser.add_argument("--mock", action="store_true", help="계산 없이 계약 형식만 맞춘 가짜 패키지")
    args = parser.parse_args(argv)

    try:
        query = json.loads(args.query.read_text(encoding="utf-8"))
        package = run(query, use_ontology=False if args.no_ontology else None, mock=args.mock)
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    text = json.dumps(package, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
