"""근거 탐색 에이전트: 등록 지표 안에서만 고르고, 판정·숫자는 엔진이 만든다. 실제 API 대신 가짜 SDK."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import run_engine
from agent.evidence_agent import EvidenceAgent, candidates
from agent.llm_client import LLMClient
from indicators.library import indicator_by_id, load_registry

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jeju.duckdb"
TABLES = {t for i in load_registry()["indicators"] for t in i["tables"]}
BUS = indicator_by_id("bus_usage_change_pct")


def use(name, inp, i):
    return NS(type="tool_use", name=name, input=inp, id=f"tu{i}")


def msg(*blocks, stop="tool_use"):
    return NS(model="claude-x", stop_reason=stop, content=[NS(type="thinking", thinking=""), *blocks],
              usage=NS(input_tokens=100, output_tokens=20))


@pytest.fixture
def scripted(monkeypatch):
    """ALLOW_PAID_CALLS 와 가짜 키는 이 테스트 프로세스 안에서만 쓴다. 네트워크 호출은 없다."""
    import agent.llm_client as mod
    monkeypatch.setenv("ALLOW_PAID_CALLS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    seen, replies = [], []
    monkeypatch.setattr(mod.LLMClient, "_anthropic_create", staticmethod(lambda p: (seen.append(p), replies.pop(0))[1]))
    return seen, replies


def agent(tmp_path, **kw):
    return EvidenceAgent(LLMClient("anthropic", "claude-sonnet-5-5", 2000, usage_log=tmp_path / "u.csv", **kw))


def test_candidates_are_context_or_same_topic_only():
    ids = {c["id"] for c in candidates(BUS, TABLES)}
    assert {"pop_change_pct", "elderly_share_pct", "pop_density", "bus_stop_per_1k_pop"} <= ids
    assert "bus_usage_change_pct" not in ids and "clinic_per_1k_pop" not in ids


def test_mock_uses_registry_related_without_any_call(monkeypatch):
    monkeypatch.delenv("ALLOW_PAID_CALLS", raising=False)
    a = EvidenceAgent(LLMClient("mock", usage_log=None))
    r = a.choose("버스가 줄었다", BUS, "partial", None, "구좌읍", TABLES, lambda i: pytest.fail("계산하면 안 됨"))
    assert r.keep == ["pop_change_pct"] and not r.used_llm


def test_agent_checks_then_keeps_only_checked_ids(scripted, tmp_path):
    seen, replies = scripted
    replies += [msg(use("check_indicator", {"indicator_id": "pop_change_pct"}, 1)),
                msg(use("check_indicator", {"indicator_id": "bus_stop_per_1k_pop"}, 2)),
                msg(use("finish", {"keep": ["pop_change_pct", "elderly_share_pct"]}, 3))]
    computed = []
    r = agent(tmp_path).choose("버스가 줄었다", BUS, "partial", None, "구좌읍", TABLES,
                               lambda i: computed.append(i) or {"result": f"{i} 값"})
    assert r.used_llm and computed == ["pop_change_pct", "bus_stop_per_1k_pop"]
    assert r.keep == ["pop_change_pct"]                 # 확인하지 않은 elderly_share_pct 는 버린다
    p = seen[0]
    assert p["tool_choice"] == {"type": "auto"} and {t["name"] for t in p["tools"]} == {"check_indicator", "finish"}
    assert "sk-test" not in json.dumps(p, ensure_ascii=False, default=str)
    # 두 번째 호출에는 첫 응답(thinking 포함)과 도구 결과가 그대로 이어진다
    assert seen[1]["messages"][1]["content"][0].type == "thinking"
    assert seen[1]["messages"][2]["content"][0]["type"] == "tool_result"


def test_agent_stops_at_max_steps(scripted, tmp_path):
    _, replies = scripted
    replies += [msg(use("check_indicator", {"indicator_id": "pop_density"}, i)) for i in range(10)]
    a = agent(tmp_path)
    a.max_steps = 2
    r = a.choose("버스가 줄었다", BUS, "partial", None, "구좌읍", TABLES, lambda i: {"result": "v"})
    assert a.client.calls == 2 and r.keep == ["pop_density"]     # 같은 지표를 두 번 골라도 한 번만


def test_blocked_without_permission_falls_back_to_related(monkeypatch, tmp_path):
    monkeypatch.delenv("ALLOW_PAID_CALLS", raising=False)
    r = agent(tmp_path).choose("버스가 줄었다", BUS, "partial", None, "구좌읍", TABLES, lambda i: {"result": "v"})
    assert r.keep == ["pop_change_pct"] and "PaidCallNotAllowed" in r.note and not r.used_llm


def test_same_input_replays_cached_choices_without_new_call(scripted, tmp_path):
    seen, replies = scripted
    replies += [msg(use("check_indicator", {"indicator_id": "pop_change_pct"}, 1)),
                msg(use("finish", {"keep": ["pop_change_pct"]}, 2))]
    a = agent(tmp_path, cache_dir=tmp_path / "cache")
    first = a.choose("버스가 줄었다", BUS, "partial", None, "구좌읍", TABLES, lambda i: {"result": "v"})
    again = agent(tmp_path, cache_dir=tmp_path / "cache").choose(
        "버스가 줄었다", BUS, "partial", None, "구좌읍", TABLES, lambda i: {"result": "v"})
    assert first.keep == again.keep == ["pop_change_pct"] and len(seen) == 2


@pytest.mark.skipif(not DB.exists(), reason="data/jeju.duckdb 없음")
def test_engine_attaches_agent_choice_without_changing_verdict(scripted, tmp_path):
    _, replies = scripted
    replies += [msg(use("check_indicator", {"indicator_id": "pop_density"}, 1)),
                msg(use("finish", {"keep": ["pop_density"]}, 2))]
    from package.assemble import Engine
    cfg = run_engine.load_config()
    cfg["llm"] = {"provider": "anthropic", "model": "claude-sonnet-5-5", "max_tokens": 2000}
    cfg["evidence_agent"] = {"enabled": True, "max_steps": 4, "max_calls": 4}
    e = Engine(cfg)
    e.agent.client.cache_dir = None
    e.planner.client.provider = "mock"          # SQL 폴백은 이 테스트에서 끈다
    try:
        query = json.loads((ROOT / "tests" / "fixtures" / "q01.json").read_text(encoding="utf-8"))
        query["claims"] = query["claims"][:1]
        ev = run_engine.run(query, engine=e)["evidence"][0]
    finally:
        e.close()
    checks = [c for c in ev["caveats"] if c.startswith("함께 볼 자료 ·")]
    assert ev["verdict"] == "partial" and len(checks) == 1 and "인구 밀도" in checks[0]
    assert any("근거 탐색 에이전트)가 고름" in c for c in ev["caveats"])
