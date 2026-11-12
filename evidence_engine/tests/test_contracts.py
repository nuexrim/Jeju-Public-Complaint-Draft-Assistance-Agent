import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

import run_engine
from package.builder import ContractError, validate_package, validate_query

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("q*.json"))
EXPECTED_TYPES = {"교통": 4, "의료 접근": 2, "환경·청소": 2, "안전·재난": 2}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_fixture_set_is_complete():
    assert len(FIXTURES) == 10
    counts = {}
    for path in FIXTURES:
        t = load(path)["complaint_type"]
        counts[t] = counts.get(t, 0) + 1
    assert counts == EXPECTED_TYPES


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_runs_to_valid_mock_package(path):
    query = load(path)
    validate_query(query)
    assert not query["location"].get("emd_code"), "fixtures는 emd_code를 비워 둔다"

    package = run_engine.run(query)
    validate_package(package)
    assert package["request_id"] == query["request_id"]
    assert package["engine"] == "mock"
    assert [e["claim_id"] for e in package["evidence"]] == [c["id"] for c in query["claims"]]
    # 모든 fixture의 읍면동은 코드표에서 찾아져야 한다(빈칸에 코드 실패가 없어야 함).
    assert not [g for g in package["gaps"] if g["concept"].startswith("읍면동 코드")]


def test_no_ontology_mode_has_no_relation_path():
    package = run_engine.run(load(FIXTURES[0]), use_ontology=False)
    assert package["meta"]["use_ontology"] is False
    assert all(e["found_by"] == "catalog" and e["relation_path"] is None for e in package["evidence"])


def test_unknown_emd_goes_to_gaps():
    query = load(FIXTURES[0])
    query["location"] = {"emd_name": "없는동"}
    package = run_engine.run(query)
    assert any(g["concept"] == "읍면동 코드(없는동)" for g in package["gaps"])


def test_raw_text_never_reaches_package():
    query = load(FIXTURES[0])
    assert query.get("raw_text")
    assert query["raw_text"] not in json.dumps(run_engine.run(query), ensure_ascii=False)


@pytest.mark.parametrize("mutate", [
    lambda q: q.pop("request_id"),
    lambda q: q.pop("normalized_text"),
    lambda q: q.pop("complaint_type"),
    lambda q: q.update(claims=[]),
    lambda q: q["location"].pop("emd_name"),
    lambda q: q.update(period={"from": "2024-13", "to": "2026-10"}),
    lambda q: q.update(unknown_field=1),
], ids=["no_request_id", "no_normalized", "no_type", "empty_claims", "no_emd_name", "bad_period", "extra_field"])
def test_invalid_query_is_rejected(mutate):
    query = copy.deepcopy(load(FIXTURES[0]))
    mutate(query)
    with pytest.raises(ContractError):
        run_engine.run(query)


@pytest.mark.parametrize("mutate", [
    lambda p: p["evidence"][0].update(verdict="maybe"),
    lambda p: p["evidence"][0].update(found_by="catalog", relation_path="A → B"),
    lambda p: p["evidence"][0].update(value="-0.18"),
    lambda p: p["gaps"][0].update(detected_by="llm"),
    lambda p: p.pop("meta"),
], ids=["bad_verdict", "path_without_ontology", "string_value", "bad_detected_by", "no_meta"])
def test_invalid_package_is_rejected(mutate):
    package = run_engine.run(load(FIXTURES[0]))
    mutate(package)
    with pytest.raises(ContractError):
        validate_package(package)


def test_cli_roundtrip(tmp_path):
    out = tmp_path / "pkg.json"
    cmd = [sys.executable, str(ROOT / "run_engine.py"), "--query", str(FIXTURES[0]), "--out", str(out)]
    assert subprocess.run(cmd, capture_output=True).returncode == 0
    validate_package(load(out))

    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "run_engine.py"), "--query", str(bad)], capture_output=True)
    assert result.returncode == 2
