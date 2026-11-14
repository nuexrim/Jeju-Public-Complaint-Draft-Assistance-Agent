"""planner(SQL 초안 폴백)·도구·개념 태그·차트. 실제 LLM은 부르지 않는다(가짜 클라이언트)."""
import json
from pathlib import Path

import pytest

from agent.llm_client import LLMResponse, PaidCallNotAllowed
from agent.planner import Planner, parse_draft
from catalog.tag_concepts import parse_llm, rule_tags, vocabulary

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jeju.duckdb"
needs_db = pytest.mark.skipif(not DB.exists(), reason="data/jeju.duckdb 없음")


class FakeClient:
    provider = "anthropic"

    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, 0

    def complete(self, prompt, *, system="", purpose="", max_tokens=None):
        self.calls += 1
        if self.error:
            raise self.error
        return LLMResponse(self.text, "fake", 1, 1)


def test_parse_draft_requires_all_fields_and_direction():
    ok = '{"name": "n", "unit": "곳", "worse": "low", "sql": "SELECT 1"}'
    assert parse_draft("앞말 " + ok + " 뒷말")["worse"] == "low"
    assert parse_draft(ok.replace('"low"', '"bad"')) is None
    assert parse_draft('{"name": "n"}') is None
    assert parse_draft("JSON 아님") is None


@pytest.fixture(scope="module")
def engine():
    import run_engine
    from package.assemble import Engine
    e = Engine(run_engine.load_config())
    yield e
    e.close()


@needs_db
def test_planner_runs_readonly_sql_draft(engine):
    sql = ("SELECT p.emd_code, 1000.0 * count(s.경로당) / p.total_pop AS value FROM pop_emd p "
           "LEFT JOIN senior_center_all s ON s.emd_code = p.emd_code WHERE p.total_pop IS NOT NULL GROUP BY 1, p.total_pop")
    reply = json.dumps({"name": "인구 1천 명당 경로당 수", "unit": "곳", "worse": "low", "sql": sql}, ensure_ascii=False)
    draft = Planner(FakeClient(reply), engine.tools).sql_fallback("경로당이 부족하다", ["senior_center_all", "pop_emd"])
    assert draft and draft.worse == "low" and "50110250" in draft.values


@needs_db
@pytest.mark.parametrize("reply", [
    '{"name": "x", "unit": "u", "worse": "low", "sql": "DROP TABLE hospital"}',             # 쓰기 SQL
    '{"name": "x", "unit": "u", "worse": "low", "sql": "SELECT 1 AS a, 2 AS b"}',           # 컬럼 형식 틀림
])
def test_planner_rejects_bad_drafts(engine, reply):
    assert Planner(FakeClient(reply), engine.tools).sql_fallback("주장", ["hospital"]) is None


@needs_db
def test_planner_falls_back_silently_when_llm_unavailable(engine):
    client = FakeClient(error=PaidCallNotAllowed("차단"))
    assert Planner(client, engine.tools).sql_fallback("주장", ["hospital"]) is None and client.calls == 1


@needs_db
def test_default_mock_provider_never_calls(engine):
    assert engine.planner.active is False


@needs_db
def test_fetch_external_respects_allow_list(engine):
    from agent.tools import ToolError
    with pytest.raises(ToolError):
        engine.tools.fetch_external("kma_heatwave")


@needs_db
def test_run_sql_tool_is_readonly(engine):
    from agent.tools import ToolError
    with pytest.raises(ToolError):
        engine.tools.run_sql("DELETE FROM hospital")


def test_rule_tags_drop_ancestors_and_parse_llm_keeps_vocabulary_only():
    vocab = vocabulary()
    assert rule_tags("제주특별자치도 서귀포시 이재민 임시주거시설 현황", "", vocab) == ["DisasterReliefShelter"]
    assert rule_tags("병·의원 정보", "", vocab) == ["Clinic"]
    parsed = parse_llm('{"a": ["Clinic", "MadeUpConcept"], "zz": ["Clinic"]}', vocab, {"a"})
    assert parsed == {"a": ["Clinic"]}


def test_comparison_chart_written(tmp_path):
    from compute.charts import comparison_bar
    p = comparison_bar({"A": 1.0, "B": 2.0, "C": 3.0}, {"A": "가동", "B": "나동", "C": "다동"}, "B",
                       title="테스트", unit="곳", out_dir=tmp_path, file_stem="r-1_c1")
    assert p and p.exists() and p.stat().st_size > 1000
    assert comparison_bar({"A": 1.0}, {}, "A", title="t", unit="u", out_dir=tmp_path, file_stem="x") is None
