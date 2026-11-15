"""근거 패키지 조립: (B 개념 확장) → 카탈로그 검색 → 병합·순위 → 지표 계산 → 검증 → 판정 → 패키징.

use_ontology=true 이고 ontology/jeju.ttl 이 있으면 하이브리드(engine="hybrid"):
  claim → 온톨로지 개념 → 3-hop 경로 → 데이터 후보(relation_path)와 확장 낱말 → 카탈로그 검색에 더해 병합.
  지표에 쓴 테이블이 온톨로지 경로로 닿았으면 found_by=ontology/both 와 relation_path 를 단다.
  데이터가 뒷받침하지 않는 개념(예: 무더위쉼터)은 gaps 에 detected_by=ontology 로 남는다.
그 밖에는 카탈로그 경로(A)만 쓴다(engine="catalog").
"""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from compute.charts import comparison_bar
from compute.sql_runner import SQLRunner
from indicators.library import (
    aspect_name, claim_aspect, comparison_group, compute, describe, gap_by_id, indicator_by_id, known_gap,
    median, proxy_gap, select_indicator,
)
from package.builder import resolve_emd, validate_package
from retrieval.catalog_search import claim_query, search
from retrieval.datasets import load_datasets
from retrieval.merge import freshness, rank
from retrieval.ontology_expand import Expansion, expand
from verdict.rules import judge

ENGINE_ROOT = Path(__file__).resolve().parent.parent

# 파생 테이블 → 원자료 (출처 표기용)
DERIVED_SOURCES = {
    "bus_usage_monthly": ["bus_stop_usage_by_user_type", "bus_stop"],
    "bus_station_emd": ["bus_stop"],
    "pop_emd": ["pop_emd", "pop_tongban"],   # 법정동이 여러 행정동에 걸친 서귀포 7개 동은 통·반 인구로 채움
}
BOUNDARY_SOURCE = {
    "dataset": "행정동 경계(통계청 SGIS 원자료, vuski/admdongkor 가공 HangJeongDong_ver20261001)",
    "publisher": "통계청 SGIS · vuski/admdongkor (CC BY 4.0)",
    "as_of": "2026-10-01",
    "url": "https://github.com/vuski/admdongkor",
}
SKIP_TABLES = {"dim_emd"}
EVIDENCE_ORDER = ["claim_id", "verdict", "indicator", "value", "unit", "region_compare", "sources",
                  "found_by", "relation_path", "chart", "caveats"]


class Engine:
    """한 번 열어 여러 질의에 재사용한다(DB·인덱스 로딩 비용 절약)."""

    def __init__(self, config: dict):
        self.config = config
        th = config["thresholds"]
        self.runner = SQLRunner(ENGINE_ROOT / config["paths"]["duckdb"], th["sql_max_rows"], th["sql_timeout_sec"])
        self.datasets, self.by_table = load_datasets(self.runner.con)
        self.tables = {r[0] for r in self.runner.con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        self.emd_names = dict(self.runner.con.execute("SELECT emd_code, emd_name FROM dim_emd").fetchall())
        self.emd_codes_path = ENGINE_ROOT / config["paths"]["emd_codes"]
        self.today = date.fromisoformat(config["snapshot"]) if config.get("snapshot") else date.today()
        from ontology.paths import load_graph
        ttl = ENGINE_ROOT / config["paths"]["ontology_ttl"]
        self.graph = load_graph(ttl) if ttl.exists() else None
        self.table_cards = {
            r["table_name"]: (f"hub-{r['hub_id']}", r["dataset"]) for r in self.by_table.values() if r["hub_id"] is not None
        }
        from agent.llm_client import client_from_config
        from agent.planner import Planner
        from agent.tools import Tools
        self.tools = Tools(self)
        self.planner = Planner(client_from_config("llm", config, usage_log=ENGINE_ROOT / config["paths"]["usage_log"]),
                               self.tools)

    def close(self) -> None:
        self.runner.close()

    # ---- 출처·검증 ----
    def _sources(self, tables: list[str]) -> tuple[list[dict], list[str]]:
        th = self.config["thresholds"]
        sources, caveats, seen = [], [], set()
        expanded = []
        for t in tables:
            expanded.extend(DERIVED_SOURCES.get(t, [t]))
        for t in expanded:
            if t in SKIP_TABLES or t in seen:
                continue
            seen.add(t)
            if t == "emd_geo":
                sources.append(dict(BOUNDARY_SOURCE))
                continue
            row = self.by_table.get(t)
            if not row:
                continue
            as_of = row["period_end"] or row["updated_at"] or (row["fetched_at"] or "")[:10]
            sources.append({"dataset": row["dataset"], "publisher": row["publisher"], "as_of": as_of, "url": row["url"]})
            if as_of and freshness(as_of, self.today, th["max_staleness_years"]) == 0:
                caveats.append(f"{row['dataset']}: 기준일 {as_of}로 {th['max_staleness_years']:.0f}년 넘게 지난 자료")
            if row["rows"] == 0:
                caveats.append(f"{row['dataset']}: 행 수 0")
            ratio = row["emd_ratio"]
            if ratio is not None and ratio < 1 - th["max_missing_ratio"]:
                caveats.append(f"{row['dataset']}: 읍면동 코드를 붙이지 못한 행이 {1 - ratio:.0%} (기준 {th['max_missing_ratio']:.0%} 초과)")
        return sources, caveats

    # ---- claim 하나 ----
    def _claim(self, query: dict, claim: dict, emd_code: str | None, emd_label: str, gaps: list,
               use_ontology: bool) -> dict:
        context = " ".join([query["normalized_text"], *query.get("targets", [])])
        th = self.config["thresholds"]
        exp = Expansion()
        if use_ontology:
            exp = expand(claim["text"], context + " " + query["complaint_type"], self.graph, self.table_cards,
                         th["ontology_max_hops"])
        hits = search(claim_query(query, claim, exp.extra_terms), {}, top_k=30, boost=set(exp.concept_ids))
        onto_hits = _best_per_card(exp.hits)
        kw = dict(today=self.today, max_missing=th["max_missing_ratio"], max_years=th["max_staleness_years"])
        cands = rank(hits, onto_hits, self.datasets, self.config["ranking"], **kw)
        catalog_top = {c.card_id for c in rank(hits, None, self.datasets, self.config["ranking"], **kw)}
        empty = {"claim_id": claim["id"], "value": None, "unit": None, "region_compare": None, "sources": [],
                 "found_by": "catalog", "relation_path": None, "chart": None}
        chosen = claim.get("indicator_id")          # 해석 선택지에서 사용자가 고른 뜻 (package/interpret.py)
        if chosen and chosen.startswith("gap:"):
            gap = gap_by_id(chosen[4:])
            entry = {"concept": gap["concept"], "reason": gap["reason"], "detected_by": "catalog"}
            if entry not in gaps:
                gaps.append(entry)
            return {**empty, "verdict": "unverifiable", "indicator": "해당 지표 없음",
                    "caveats": [f"사용자가 고른 뜻({gap['concept']})을 확인할 데이터가 없어 판정하지 않음"]}
        if chosen:
            indicator = indicator_by_id(chosen)
            if not set(indicator["tables"]) <= self.tables:
                indicator = None
        else:
            indicator, _ = select_indicator(claim["text"], context, self.tables)

        for g in exp.gaps:
            if g not in gaps:
                gaps.append(g)
        if indicator is None:
            if exp.gaps:
                return {**empty, "found_by": "ontology", "verdict": "unverifiable", "indicator": "해당 지표 없음",
                        "caveats": ["온톨로지 개념은 있으나 연결된 데이터가 없어 판정하지 않음"]}
            gap = known_gap(claim["text"], context)
            if gap is None and self.planner.active:
                drafted = self._sql_fallback(query, claim, cands, emd_code, emd_label, empty)
                if drafted:
                    return drafted
            names = ", ".join(c.title for c in cands[:3]) or "없음"
            entry = {
                # 빈칸 기록에는 민원 문장을 남기지 않는다(CLAUDE.md 5장 9항): claim id 로만 가리킨다
                "concept": gap["concept"] if gap else f"claim {claim['id']} 관련 지표",
                "reason": gap["reason"] if gap else f"이 주장을 계산할 지표가 아직 정의되지 않음 (검색 상위 데이터셋: {names})",
                "detected_by": "catalog",
            }
            if entry not in gaps and not any(g["detected_by"] == "ontology" for g in gaps):
                gaps.append(entry)
            return {**empty, "verdict": "unverifiable", "indicator": "해당 지표 없음",
                    "caveats": ["확인할 데이터가 없어 판정하지 않음"]}

        result = compute(indicator, emd_code, self.runner)
        sources, caveats = self._sources(indicator["tables"])
        caveats = list(indicator.get("caveats", [])) + caveats
        if result.window:
            w = result.window
            caveats.append(f"기간: {_next(w['mid'])}~{w['end']}" + (f" (비교: {_next(w['start'])}~{w['mid']})" if indicator["rule"] == "trend" else ""))
            want = query.get("period") or {}
            if want.get("to") and w["end"] < want["to"]:
                caveats.append(f"자료가 {w['end']}까지만 있어 요청 기간 끝({want['to']})까지 보지 못함")
            if want.get("from") and w["start"] < want["from"] and indicator["rule"] == "trend":
                caveats.append(f"증감 비교를 위해 요청 기간 시작({want['from']})보다 앞선 자료도 사용")
        if result.note:
            caveats.append(result.note)

        region_compare, peers, chart = None, [], None
        if indicator["scope"] == "emd" and emd_code and result.value is not None:
            label, group = comparison_group(emd_code, self.emd_names)
            peers = [result.values[c] for c in group if c != emd_code and c in result.values]
            region_compare = self._compare_text(indicator, result, label, peers)
            chart = self._chart(query, claim, indicator, result, group, label)
            missing = [self.emd_names[c] for c in group if c not in result.values]
            if missing:
                caveats.append(f"비교군에서 값이 없는 곳 제외: {', '.join(missing)}")
        elif indicator["scope"] == "jeju":
            region_compare = "읍면동별 자료 없음 · 제주 전체 값"

        j = judge(indicator["rule"], result.value, peers=peers, worse=indicator.get("worse", "high"),
                  params=self.config.get("verdict"))
        caveats.append(f"판정 근거: {j.detail}")
        verdict = j.verdict
        if chosen:
            caveats.append("사용자가 해석 선택지에서 고른 뜻으로 계산")
        else:
            verdict = self._proxy_check(claim["text"], indicator, verdict, caveats, gaps)
        found_by, relation_path = self._found_by(indicator, catalog_top, exp)
        if found_by == "catalog" and not self._main_card(indicator) in catalog_top:
            caveats.append("카탈로그 검색 상위 5개 밖의 데이터셋을 지표 정의로 사용")
        return {
            **empty,
            "found_by": found_by,
            "relation_path": relation_path,
            "chart": chart,
            "verdict": verdict,
            "indicator": f"{indicator['name']} · {emd_label if indicator['scope'] == 'emd' else '제주 전체'}",
            "value": None if result.value is None else round(result.value, 3),
            "unit": indicator["unit"] if result.value is not None else None,
            "region_compare": region_compare,
            "sources": sources if result.value is not None else [],
            "caveats": caveats,
        }

    @staticmethod
    def _proxy_check(claim_text: str, indicator: dict, verdict: str, caveats: list, gaps: list) -> str:
        """주장이 말하는 측면과 지표가 재는 측면이 다르면 간접 지표: 지지는 부분으로 낮추고 이유를 남긴다."""
        aspect = claim_aspect(claim_text)
        if not aspect or aspect == indicator.get("measures"):
            return verdict
        caveats.append(f"간접 지표: 주장은 '{aspect_name(aspect)}'에 관한 것인데 이 지표는 "
                       f"'{aspect_name(indicator.get('measures'))}'을(를) 잼")
        gap = proxy_gap(indicator, aspect)
        if gap:
            entry = {"concept": gap["concept"], "reason": gap["reason"], "detected_by": "catalog"}
            if entry not in gaps:
                gaps.append(entry)
        if verdict == "supported":
            caveats.append("간접 지표라 '지지'를 '부분 지지'로 낮춤")
            return "partial"
        if verdict == "contradicted":
            caveats.append("간접 지표라 반박 근거로도 약함")
        return verdict

    def interpret(self, query: dict) -> dict:
        """주장별 해석 선택지 (package/interpret.py). query 는 마스킹된 사본."""
        from package.builder import validate_interpretation
        from package.interpret import interpret
        emd_code, _ = resolve_emd(query["location"], self.emd_codes_path)
        out = interpret(query, self.tables, self.emd_names.get(emd_code) if emd_code else None)
        validate_interpretation(out)
        return out

    def _sql_fallback(self, query, claim, cands, emd_code, emd_label, empty) -> dict | None:
        """지표가 없는 claim: LLM이 쓴 읽기 전용 SQL 초안을 실행하고, 판정은 규칙(rank)으로 한다."""
        tables = [t for c in cands for t in c.tables]
        draft = self.planner.sql_fallback(claim["text"], tables)
        if not draft or not emd_code or emd_code not in draft.values:
            return None
        label, group = comparison_group(emd_code, self.emd_names)
        peers = [draft.values[c] for c in group if c != emd_code and c in draft.values]
        value = draft.values[emd_code]
        j = judge("rank", value, peers=peers, worse=draft.worse, params=self.config.get("verdict"))
        used = [t for t in self.tables if t in draft.sql]
        sources, caveats = self._sources(used)
        return {
            **empty, "verdict": j.verdict, "indicator": f"{draft.name} · {emd_label}",
            "value": round(value, 3), "unit": draft.unit,
            "region_compare": f"{label} {len(peers) + 1}곳 중앙값 {describe(median(peers + [value]))}{draft.unit}",
            "sources": sources,
            "caveats": ["LLM이 작성한 SQL로 계산(지표 레지스트리 밖, 사람 검토 전)", f"SQL: {draft.sql}",
                        *caveats, f"판정 근거: {j.detail}"],
        }

    def _chart(self, query, claim, indicator, result, group, label) -> str | None:
        if indicator["rule"] != "rank" or not self.config.get("charts", {}).get("enabled", True):
            return None
        values = {c: result.values[c] for c in group if c in result.values}
        out_dir = ENGINE_ROOT / self.config["paths"]["charts_dir"]
        try:
            path = comparison_bar(values, self.emd_names, result.emd_code,
                                  title=f"{indicator['name']} ({label} 비교)", unit=indicator["unit"],
                                  out_dir=out_dir, file_stem=f"{query['request_id']}_{claim['id']}")
        except Exception:          # 차트는 부가 정보: 실패해도 근거 패키지는 낸다
            return None
        return path.relative_to(ENGINE_ROOT).as_posix() if path else None

    def _main_card(self, indicator: dict) -> str | None:
        for t in indicator["tables"]:
            for src in DERIVED_SOURCES.get(t, [t]):
                if src in self.table_cards:
                    return self.table_cards[src][0]
        return None

    def _found_by(self, indicator: dict, catalog_top: set[str], exp: Expansion) -> tuple[str, str | None]:
        """지표의 주 테이블(앞쪽 우선)이 온톨로지 경로·카탈로그 상위 5개 중 어디서 잡혔는가."""
        path = None
        for t in indicator["tables"]:
            if t in exp.table_paths:
                path = exp.table_paths[t][1]
                break
        by_catalog = self._main_card(indicator) in catalog_top
        if path and by_catalog:
            return "both", path
        if path:
            return "ontology", path
        return "catalog", None

    def _compare_text(self, indicator: dict, result, label: str, peers: list[float]) -> str:
        all_med = median(list(result.values.values()))
        grp_med = median(peers + [result.value])
        unit = indicator["unit"]
        if indicator["rule"] == "zero":
            have = sum(1 for v in result.values.values() if v > 0)
            return f"제주 전체 {describe(sum(result.values.values()))}{unit} · 있는 읍면동 {have}곳/{len(result.values)}곳"
        worse = indicator.get("worse", "high")
        ordered = sorted(peers + [result.value], reverse=(worse == "high"))
        pos = ordered.index(result.value) + 1
        return (f"{label} {len(ordered)}곳 중 불리한 순 {pos}위 · {label} 중앙값 {describe(grp_med)}{unit}"
                f" · 제주 {len(result.values)}개 읍면동 중앙값 {describe(all_med)}{unit}")

    # ---- 질의 하나 ----
    def build(self, query: dict, use_ontology: bool | None = None) -> dict:
        started = time.perf_counter()
        if use_ontology is None:
            use_ontology = bool(self.config.get("use_ontology"))
        use_ontology = use_ontology and self.graph is not None
        emd_code, emd_fail = resolve_emd(query["location"], self.emd_codes_path)
        emd_label = self.emd_names.get(emd_code, query["location"]["emd_name"]) if emd_code else "제주 전체"
        gaps: list[dict] = []
        if emd_fail:
            gaps.append({"concept": f"읍면동 코드({query['location']['emd_name']})",
                         "reason": f"{emd_fail} → 읍면동 지표는 계산하지 않음", "detected_by": "catalog"})
        evidence = [self._claim(query, c, emd_code, emd_label, gaps, use_ontology) for c in query["claims"]]
        evidence = [{k: ev[k] for k in EVIDENCE_ORDER} for ev in evidence]
        package = {
            "request_id": query["request_id"],
            "engine": "hybrid" if use_ontology else "catalog",
            "evidence": evidence,
            "gaps": gaps,
            "meta": {"use_ontology": use_ontology, "snapshot": self.config.get("snapshot"),
                     "elapsed_ms": int((time.perf_counter() - started) * 1000)},
        }
        validate_package(package)
        return package


def _best_per_card(hits: list[tuple]) -> list[tuple[str, str, int, str]]:
    best: dict[str, tuple] = {}
    for card_id, title, hops, path, _table in hits:
        if card_id not in best or hops < best[card_id][2]:
            best[card_id] = (card_id, title, hops, path)
    return list(best.values())


def _next(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"
