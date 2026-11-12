"""근거 패키지 엔진 CLI.

    python run_engine.py --query tests/fixtures/q01.json
    python run_engine.py --query tests/fixtures/q01.json --out out.json --no-ontology

상태: 입력 계약 검증 → 읍면동 코드 찾기 → MOCK 근거 패키지 반환까지만 동작한다.
실제 탐색·계산은 아직 연결되지 않았으며, 출력의 engine 값이 "mock"이다.
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


def load_config(path: Path | None = None) -> dict:
    return yaml.safe_load((path or ENGINE_ROOT / "config.yaml").read_text(encoding="utf-8"))


def run(query: dict, config: dict | None = None, *, use_ontology: bool | None = None) -> dict:
    """구조화 질의 dict → 근거 패키지 dict. raw_text는 읽지도 남기지도 않는다."""
    config = config or load_config()
    started = time.perf_counter()
    validate_query(query)
    query = {k: v for k, v in query.items() if k != "raw_text"}
    if use_ontology is None:
        use_ontology = bool(config["use_ontology"])
    package = build_mock_package(
        query,
        use_ontology=use_ontology,
        emd_codes_path=ENGINE_ROOT / config["paths"]["emd_codes"],
    )
    package["meta"]["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="근거 패키지 엔진 (현재 mock 응답)")
    parser.add_argument("--query", required=True, type=Path, help="구조화 질의 JSON 파일")
    parser.add_argument("--out", type=Path, help="근거 패키지를 저장할 파일 (없으면 표준 출력)")
    parser.add_argument("--no-ontology", action="store_true", help="use_ontology=false로 실행")
    args = parser.parse_args(argv)

    try:
        query = json.loads(args.query.read_text(encoding="utf-8"))
        package = run(query, use_ontology=False if args.no_ontology else None)
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
