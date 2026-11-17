"""평가 정답 세트와 평가 스크립트: 정답의 지표·빈칸 id 가 등록부와 맞고, 평가가 끝까지 돈다(유료 호출 없음)."""
from pathlib import Path

import pytest
import yaml

import complaint_agent.pipeline  # noqa: F401  (근거 엔진·대화 모듈 경로를 올린다)
from indicators.library import gap_by_id, indicator_by_id

GOLD = Path(__file__).resolve().parents[1] / "eval" / "gold.yaml"
HAS_DB = (Path(__file__).resolve().parents[2] / "evidence_engine" / "data" / "jeju.duckdb").exists()


def test_gold_ids_exist_in_registry():
    items = yaml.safe_load(GOLD.read_text(encoding="utf-8"))["items"]
    assert len({i["id"] for i in items}) == len(items) >= 30
    for it in items:
        assert it["claims"], it["id"]
        assert it["emd"] or it.get("places"), it["id"]          # 장소를 알아듣거나, 고를 장소가 있어야 한다
        for c in it["claims"]:
            for m in c["accept"]:
                if m in ("template", "none"):
                    continue
                if m.startswith("gap:"):
                    assert gap_by_id(m[4:]), (it["id"], m)
                else:
                    ind = indicator_by_id(m)
                    assert ind and ind.get("role") != "context", (it["id"], m)


@pytest.mark.skipif(not HAS_DB, reason="근거 DB 없음")
def test_eval_runs_without_paid_calls(tmp_path, monkeypatch):
    from complaint_agent.eval import run_eval
    monkeypatch.delenv("ALLOW_PAID_CALLS", raising=False)
    import complaint_agent.pipeline as P
    before = P.S.llm.call_json
    items = yaml.safe_load(GOLD.read_text(encoding="utf-8"))["items"]
    small = tmp_path / "gold.yaml"
    small.write_text(yaml.safe_dump({"items": [items[0], items[24]]}, allow_unicode=True), encoding="utf-8")
    assert run_eval.main(["--gold", str(small), "--out", str(tmp_path)]) == 0
    out = next(tmp_path.glob("eval_*.json")).read_text(encoding="utf-8")
    assert '"structured_by": "rules"' in out
    import complaint_agent.pipeline as P
    assert P.S.llm.call_json is before                 # 평가가 끝나면 Claude 차단을 되돌린다
