"""에이전트 도구 모음. 모두 package.assemble.Engine 위에서 돌고, 입출력은 JSON으로 바꿀 수 있는 값이다.

search_catalog, describe_table, compute_indicator, run_sql, compare_region,
fetch_external, make_chart, log_gap, expand_concepts

숫자를 만드는 도구(compute_indicator·run_sql·compare_region)는 DuckDB 읽기 전용 결과만 돌려준다.
LLM 이 직접 고르는 도구는 agent/evidence_agent.py 에 따로 있다(등록 지표 계산·끝내기 두 개뿐).
"""
from __future__ import annotations

from typing import Any

from compute.charts import comparison_bar
from compute.sql_runner import SQLRejected, SQLTimeout
from indicators.library import comparison_group, compute, load_registry, median
from retrieval.catalog_search import search


class ToolError(ValueError):
    pass


class Tools:
    def __init__(self, engine):
        self.e = engine
        self.gaps: list[dict] = []

    # ---- 찾기 ----
    def search_catalog(self, text: str, top_k: int = 5) -> list[dict]:
        return [{"card_id": h.card_id, "title": h.title, "score": h.score,
                 "tables": self.e.datasets[h.card_id].tables if h.card_id in self.e.datasets else []}
                for h in search(text, {}, top_k=top_k)]

    def describe_table(self, table: str) -> dict:
        if table not in self.e.tables:
            raise ToolError(f"없는 테이블: {table}")
        cols = [{"name": n, "type": t} for n, t, *_ in self.e.runner.con.execute(f'DESCRIBE "{table}"').fetchall()]
        row = self.e.by_table.get(table) or {}
        return {"table": table, "dataset": row.get("dataset"), "publisher": row.get("publisher"),
                "as_of": row.get("period_end") or row.get("updated_at"), "rows": row.get("rows"),
                "emd_ratio": row.get("emd_ratio"), "columns": cols}

    def expand_concepts(self, text: str) -> dict:
        if self.e.graph is None:
            return {"concepts": [], "paths": {}}
        from retrieval.ontology_expand import expand
        x = expand(text, "", self.e.graph, self.e.table_cards, self.e.config["thresholds"]["ontology_max_hops"])
        return {"concepts": x.concepts, "paths": {t: p for t, (_, p) in x.table_paths.items()}, "gaps": x.gaps}

    # ---- 계산 ----
    def compute_indicator(self, indicator_id: str, emd_code: str | None) -> dict:
        ind = next((i for i in load_registry()["indicators"] if i["id"] == indicator_id), None)
        if ind is None:
            raise ToolError(f"없는 지표: {indicator_id}")
        r = compute(ind, emd_code, self.e.runner)
        return {"indicator": ind["name"], "unit": ind["unit"], "value": r.value, "note": r.note, "window": r.window}

    def compare_region(self, indicator_id: str, emd_code: str) -> dict:
        ind = next((i for i in load_registry()["indicators"] if i["id"] == indicator_id), None)
        if ind is None or ind["scope"] != "emd":
            raise ToolError("읍면동 단위 지표만 비교할 수 있다")
        r = compute(ind, emd_code, self.e.runner)
        label, group = comparison_group(emd_code, self.e.emd_names)
        peers = {self.e.emd_names[c]: r.values[c] for c in group if c in r.values}
        return {"group": label, "value": r.value, "group_median": median(list(peers.values())), "peers": peers}

    def run_sql(self, sql: str) -> dict:
        try:
            res = self.e.runner.run(sql)
        except (SQLRejected, SQLTimeout) as exc:
            raise ToolError(str(exc)) from None
        return {"columns": res.columns, "rows": [list(r) for r in res.rows], "truncated": res.truncated}

    def make_chart(self, values: dict[str, float], target: str, title: str, unit: str, file_stem: str) -> str | None:
        from package.assemble import ENGINE_ROOT
        path = comparison_bar(values, self.e.emd_names, target, title=title, unit=unit,
                              out_dir=ENGINE_ROOT / self.e.config["paths"]["charts_dir"], file_stem=file_stem)
        return path.relative_to(ENGINE_ROOT).as_posix() if path else None

    # ---- 외부·빈칸 ----
    def fetch_external(self, name: str, params: dict | None = None) -> Any:
        allowed = self.e.config.get("external_apis", {}).get("allowed") or []
        if name not in allowed:
            raise ToolError(f"허용 목록에 없는 외부 API: {name} (config.yaml external_apis.allowed)")
        raise ToolError(f"{name}: 수집기가 아직 연결되지 않음")

    def log_gap(self, concept: str, reason: str) -> dict:
        gap = {"concept": concept, "reason": reason, "detected_by": "catalog"}
        if gap not in self.gaps:
            self.gaps.append(gap)
        return gap
