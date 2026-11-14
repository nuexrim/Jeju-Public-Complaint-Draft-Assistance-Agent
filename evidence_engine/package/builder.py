"""근거 패키지 조립과 계약 검증.

계약 검증, 읍면동 코드 찾기, mock 패키지(--mock)를 맡는다.
실제 근거 조립(검색 → 지표 계산 → 판정 → 패키지)은 package/assemble.py.
"""
from __future__ import annotations

import csv
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

ENGINE_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ENGINE_ROOT / "contracts"

MOCK_CAVEAT = "MOCK 응답: 실제 데이터로 계산한 값이 아님"
# 팀원이 네 가지 판정 분기를 모두 시험할 수 있도록 claim 순서대로 돌려 쓴다.
_MOCK_VERDICTS = ["supported", "partial", "contradicted", "unverifiable"]


class ContractError(ValueError):
    """입력 또는 출력이 계약 스키마와 맞지 않을 때."""


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / f"{name}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _validate(name: str, doc: dict) -> None:
    errors = sorted(_validator(name).iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        lines = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errors]
        raise ContractError(f"{name} 계약 위반:\n  " + "\n  ".join(lines))


def validate_query(doc: dict) -> None:
    _validate("query", doc)


def validate_package(doc: dict) -> None:
    _validate("evidence", doc)


@lru_cache(maxsize=None)
def _emd_table(path: Path) -> tuple[tuple[str, str, str], ...]:
    with path.open(encoding="utf-8", newline="") as f:
        return tuple((r["emd_code"], r["sigungu_name"], r["emd_name"]) for r in csv.DictReader(f))


def resolve_emd(location: dict, emd_codes_path: Path) -> tuple[str | None, str | None]:
    """(emd_code, 실패 사유). 코드가 이미 있으면 그대로 쓴다.

    emd_name은 "구좌읍" 또는 "제주시 구좌읍" 형태를 받는다.
    """
    if location.get("emd_code"):
        return location["emd_code"], None
    name = " ".join(location["emd_name"].split())
    matches = [
        code for code, sigungu, emd in _emd_table(Path(emd_codes_path))
        if name in (emd, f"{sigungu} {emd}")
    ]
    if len(matches) == 1:
        return matches[0], None
    if matches:
        return None, "같은 이름의 읍면동이 여러 개라 특정할 수 없음"
    # 법정동·리 이름(예: 도남동, 세화리)이면 연계표로 행정동을 찾는다. 여러 행정동에 걸치면 비워 둔다.
    from data.spatial.emd_match import match_emd
    code, _ = match_emd(name, Path(emd_codes_path))
    if code:
        return code, None
    return None, "행정동 코드표·법정동 연계표에서 찾지 못한 읍면동명(또는 여러 행정동에 걸친 법정동)"


def build_mock_package(query: dict, *, use_ontology: bool, emd_codes_path: Path, elapsed_ms: int = 0) -> dict:
    """계약 형식만 맞춘 가짜 근거 패키지. 수치는 실제 계산값이 아니다."""
    emd_code, emd_fail = resolve_emd(query["location"], emd_codes_path)
    scope = query["location"]["emd_name"] if emd_code else "제주 전체"

    evidence = []
    for i, claim in enumerate(query["claims"]):
        verdict = _MOCK_VERDICTS[i % len(_MOCK_VERDICTS)]
        unverifiable = verdict == "unverifiable"
        by_ontology = use_ontology and i % 2 == 0
        evidence.append({
            "claim_id": claim["id"],
            "verdict": verdict,
            "indicator": f"(MOCK) '{claim['text']}' 관련 지표 · {scope}",
            "value": None if unverifiable else round(-0.1 * (i + 1), 2),
            "unit": None if unverifiable else "비율",
            "region_compare": None if unverifiable else "(MOCK) 제주 읍면동 비교 자리",
            "sources": [] if unverifiable else [{
                "dataset": "(MOCK) 데이터셋 이름 자리",
                "publisher": "제주데이터허브",
                "as_of": "0000-00",
                "url": None,
            }],
            "found_by": "ontology" if by_ontology else "catalog",
            "relation_path": "(MOCK) 개념A —관계→ 개념B" if by_ontology else None,
            "chart": None,
            "caveats": [MOCK_CAVEAT],
        })

    gaps = [{"concept": "(MOCK) 빈칸 개념 자리", "reason": MOCK_CAVEAT, "detected_by": "catalog"}]
    if emd_fail:
        gaps.append({
            "concept": f"읍면동 코드({query['location']['emd_name']})",
            "reason": f"{emd_fail} → 제주 전체 기준으로 계산",
            "detected_by": "catalog",
        })

    package = {
        "request_id": query["request_id"],
        "engine": "mock",
        "evidence": evidence,
        "gaps": gaps,
        "meta": {"use_ontology": use_ontology, "snapshot": None, "elapsed_ms": elapsed_ms},
    }
    validate_package(package)
    return package
