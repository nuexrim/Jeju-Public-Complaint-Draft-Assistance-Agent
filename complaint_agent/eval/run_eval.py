"""무료 평가: 데이터셋 현황 · 주장 이해(규칙 구조화) · 근거 수와 판정 · 남은 한계를 한 번에 표로 뽑는다.

    python -m complaint_agent.eval.run_eval                 # complaint_agent/eval/results/eval_<날짜>.md, .json
    python -m complaint_agent.eval.run_eval --print         # 표준 출력에도

유료 호출 없음
  - 구조화: Claude 호출을 막고 규칙 구조화(structured_by="rules")로만 잰다. Claude 경로는 유료 평가에서 따로 잰다.
  - 근거 탐색 에이전트: config 의 llm.provider 를 mock 으로 고정 → 교차 확인은 registry related 목록.

정답 세트: complaint_agent/eval/gold.yaml (뜻 정답은 accept 목록, 판정 정답은 사람이 원자료로 확인한 뒤 채움)

재는 것
  1) 데이터셋: 적재 표·지표·빈칸 수, 읍면동 키 비율, 오래된 자료, 데이터허브 목록 중 근거에 쓸 수 있는 비율
  2) 이해(규칙): 읍면동 인식, 주제 인식, 측면 인식, 정답 뜻이 선택지에 있는 비율, 추천 1순위 정답률,
                근거 자료가 없는 민원에 엉뚱한 선택지를 내지 않는 비율
  3) 근거: 사용자가 정답 뜻을 골랐을 때(picked)와 고르지 않고 엔진에 맡겼을 때(auto)
           값이 계산된 근거 비율, 판정 분포, 정답 뜻으로 계산했는지, 출처·교차 확인 수, 오래된 자료 주의 수
  4) 한계: 위에서 틀린 항목을 종류별로 모은다
"""
from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from datetime import date
from pathlib import Path

import yaml

import complaint_agent.pipeline as P
from indicators.library import claim_aspect, claim_topics, gap_by_id, indicator_by_id, load_registry

HERE = Path(__file__).resolve().parent
GOLD = HERE / "gold.yaml"
RESULTS = HERE / "results"
from package.labels import CROSS_CHECK, TEMPLATE_MARK  # noqa: E402
NO_INDICATOR = "해당 지표 없음"


def _block_claude():
    """구조화의 Claude 호출을 막는다. 돌려받은 함수로 원래대로 되돌린다(같은 프로세스의 다른 코드에 남지 않게)."""
    original = P.S.llm.call_json

    def blocked(*a, **k):
        raise P.llm.PaidCallNotAllowed("평가: 규칙 구조화로만 잰다")
    P.S.llm.call_json = blocked

    def restore() -> None:
        P.S.llm.call_json = original
    return restore


def _config() -> dict:
    cfg = copy.deepcopy(P.run_engine.load_config())
    cfg["llm"] = {**cfg.get("llm", {}), "provider": "mock"}
    return cfg


def _kind(meaning: str) -> str:
    if meaning in ("template", "none"):
        return meaning
    return "gap" if meaning.startswith("gap:") else "indicator"


def _topic(meaning: str) -> str | None:
    if meaning.startswith("gap:"):
        return (gap_by_id(meaning[4:]) or {}).get("topic")
    ind = indicator_by_id(meaning)
    return ind.get("topic") if ind else None


def _indicator_id(evidence: dict) -> str | None:
    name = evidence["indicator"].rsplit(" · ", 1)[0]
    return next((i["id"] for i in load_registry()["indicators"] if i["name"] == name), None)


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({n / d:.0%})" if d else "0/0"


# ---------------------------------------------------------------- 1. 데이터셋
def dataset_stats(agent: P.Agent, cfg: dict) -> dict:
    from retrieval.merge import freshness
    con = agent.engine.runner.con
    reg = load_registry()
    today = date.fromisoformat(cfg["snapshot"]) if cfg.get("snapshot") else date.today()
    max_years = cfg["thresholds"]["max_staleness_years"]
    rows = con.execute("SELECT table_name, source_kind, hub_id, emd_ratio, period_end, updated_at, fetched_at "
                       "FROM dataset").fetchall()
    stale = [t for t, _, _, _, pe, up, fe in rows if freshness(pe or up or (fe or "")[:10], today, max_years) == 0]
    with_emd = [t for t, _, _, r, *_ in rows if r is not None and r >= 1 - cfg["thresholds"]["max_missing_ratio"]]
    out = {
        "기준일": today.isoformat(),
        "적재 표(핵심)": len(rows),
        "적재 표 종류": dict(Counter(k for _, k, *_ in rows)),
        "읍면동 키 70% 이상 표": len(with_emd),
        f"기준일 {max_years:.0f}년 넘은 표": len(stale),
        "판정 지표": sum(1 for i in reg["indicators"] if i.get("role") != "context"),
        "맥락 지표": sum(1 for i in reg["indicators"] if i.get("role") == "context"),
        "주제": [t.get("name", t["id"]) for t in reg["topics"]],
        "등록 빈칸(자료 없음)": [g["concept"] for g in reg["known_gaps"]],
    }
    hubs_core = {h for _, _, h, *_ in rows if h is not None}
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "prefetch_profile" in tables:
        n, hubs, emd, places = con.execute(
            "SELECT count(*), count(DISTINCT hub_id), count(emd_mode), count(*) FILTER (WHERE place_list) "
            "FROM prefetch_profile").fetchone()
        out.update({"미리 받은 표": n, "미리 받은 데이터셋": hubs, "미리 받은 표 중 읍면동 키": emd,
                    "미리 받은 표 중 시설 목록": places,
                    "범용 틀로 쓸 수 있는 시설 목록": len(agent.engine.place_tables)})
        hubs_pf = {r[0] for r in con.execute("SELECT DISTINCT hub_id FROM prefetch_profile").fetchall()}
    else:
        hubs_pf = set()
    catalog = sorted((P.ENGINE_DIR / "data/ingest/raw/jejudatahub").glob("catalog_*.json"))
    if catalog:
        total = len(json.loads(catalog[-1].read_text(encoding="utf-8")))
        out["데이터허브 목록"] = total
        out["받은 데이터허브 데이터셋(핵심+미리 받기)"] = _pct(len(hubs_core | hubs_pf), total)
    return out


# ---------------------------------------------------------------- 2·3. 이해와 근거
def run_item(agent: P.Agent, item: dict) -> dict:
    claims = item["claims"]
    accepts = [c["accept"] for c in claims]
    union = {m for a in accepts for m in a}
    expect_topics = {t for m in union if (t := _topic(m))}
    out_of_scope = all(_kind(m) in ("none", "template") for m in union)

    r = agent.understand(item["text"])
    detected = r["query"]["location"]["emd_name"]
    picker = any(q.get("choices") for q in r["questions"])
    confirm = any(q.get("confirm") for q in r["questions"])
    if item["emd"]:
        emd_ok = detected == item["emd"]
    else:
        emd_ok = detected == "" and picker
    found_topics = set(claim_topics(r["query"]["normalized_text"]))
    topic_ok = (not found_topics) if out_of_scope else expect_topics <= found_topics
    aspect = claim_aspect(r["query"]["normalized_text"])
    no_evidence_question = any(q["field"] == "complaint_type" for q in r["questions"])

    # 사용자 답: 장소(고르기 또는 확인), 요구사항 없음
    places = item.get("places") or [item["emd"]]
    if confirm:
        r = agent.answer(r, confirm_emd=detected == item["emd"])
    if not item["emd"] or detected != item["emd"]:
        r = agent.answer(r, emd_names=places)
    r = agent.answer(r, user_request="")
    query = r["query"]

    interp = agent.meaning_options(query)
    options = [o for c in interp["claims"] for o in c["options"] if o["kind"] != "other"]
    option_ids = [o["indicator_id"] for o in options]
    recommended = {c["recommended"] for c in interp["claims"]}
    rec_ids = {o["indicator_id"] for c in interp["claims"] for o in c["options"] if o["option_id"] in recommended}
    in_options = [bool(set(a) & set(option_ids)) for a in accepts if _kind(a[0]) in ("indicator", "gap")]
    rec_ok = bool(rec_ids & union) if not out_of_scope else not option_ids
    wrong_options = bool(option_ids) if out_of_scope else False

    # 사용자가 정답 뜻을 고른 경우 (규칙 구조화: 주장 하나에서 여러 개 고르기)
    picks = []
    for a in accepts:
        hit = next((o["option_id"] for o in options if o["indicator_id"] in a), None)
        if hit and hit not in picks:
            picks.append(hit)
    max_picks = interp["claims"][0].get("max_picks", 1) if interp["claims"] else 1
    picked_query = query
    if picks and interp["claims"]:
        picked_query, _ = agent.choose(query, interp, {interp["claims"][0]["claim_id"]: picks[:max_picks]})
    picked = agent.evidence(picked_query, r.get("places"))
    auto = agent.evidence(query, r.get("places"))
    return {
        "id": item["id"], "style": item["style"], "text": item["text"],
        "structured_by": r.get("structured_by"),
        "emd_detected": detected, "emd_ok": emd_ok, "picker_offered": picker, "confirm_asked": confirm,
        "topics_found": sorted(found_topics), "topic_ok": topic_ok, "aspect": aspect,
        "out_of_scope": out_of_scope, "no_evidence_question": no_evidence_question,
        "gold_claims": len(accepts), "rule_claims": len(query["claims"]),
        "in_options": in_options, "recommended_ok": rec_ok, "wrong_options": wrong_options,
        "picked": _score_package(picked, accepts), "auto": _score_package(auto, accepts),
    }


def _score_package(pkg: dict, accepts: list[list[str]]) -> dict:
    ev = pkg["evidence"]
    got_ids = {_indicator_id(e) for e in ev} - {None}
    got_gaps = {g["concept"] for g in pkg["gaps"]}
    template = any(TEMPLATE_MARK in e["indicator"] for e in ev)
    correct = []
    for a in accepts:
        ok = False
        for m in a:
            k = _kind(m)
            if k == "indicator" and m in got_ids:
                ok = True
            elif k == "gap" and (gap_by_id(m[4:]) or {}).get("concept") in got_gaps:
                ok = True
            elif k == "template" and template:
                ok = True
            elif k == "none" and not template and all(e["indicator"] == NO_INDICATOR for e in ev):
                ok = True
        correct.append(ok)
    return {
        "evidence": len(ev),
        "computed": sum(e["value"] is not None for e in ev),
        "verdicts": dict(Counter(e["verdict"] for e in ev)),
        "template": template,
        "sources": sum(len(e["sources"]) for e in ev),
        "cross_checks": sum(sum(c.startswith(CROSS_CHECK) for c in e["caveats"]) for e in ev),
        "stale": sum(sum("넘게 지난 자료" in c for c in e["caveats"]) for e in ev),
        "indicators": sorted(got_ids), "gaps": sorted(got_gaps),
        "correct": correct,
    }


# ---------------------------------------------------------------- 요약·한계
def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    in_scope = [r for r in rows if not r["out_of_scope"]]
    opt = [x for r in rows for x in r["in_options"]]

    def ev(kind: str) -> dict:
        claims = [x for r in rows for x in r[kind]["correct"]]
        total = sum(r[kind]["evidence"] for r in rows)
        verdicts = Counter()
        for r in rows:
            verdicts.update(r[kind]["verdicts"])
        return {
            "정답 뜻으로 근거를 냄(주장 기준)": _pct(sum(claims), len(claims)),
            "근거 수(합계)": total,
            "값이 계산된 근거": _pct(sum(r[kind]["computed"] for r in rows), total),
            "판정 분포": dict(verdicts),
            "범용 틀 사용 민원": sum(r[kind]["template"] for r in rows),
            "출처 수(근거당 평균)": round(sum(r[kind]["sources"] for r in rows) / total, 2) if total else 0,
            "교차 확인 수(근거당 평균)": round(sum(r[kind]["cross_checks"] for r in rows) / total, 2) if total else 0,
            "오래된 자료 주의가 붙은 근거": sum(r[kind]["stale"] for r in rows),
        }
    by_style = {}
    for st in sorted({r["style"] for r in rows}):
        rs = [r for r in rows if r["style"] == st]
        claims = [x for r in rs for x in r["picked"]["correct"]]
        by_style[st] = {"민원": len(rs), "읍면동": _pct(sum(r["emd_ok"] for r in rs), len(rs)),
                        "주제": _pct(sum(r["topic_ok"] for r in rs), len(rs)),
                        "근거 정답(뜻 고른 뒤)": _pct(sum(claims), len(claims))}
    return {
        "민원 수": n, "주장 수(정답)": sum(r["gold_claims"] for r in rows),
        "이해(규칙 구조화)": {
            "읍면동 인식(없으면 고르기 목록 제시)": _pct(sum(r["emd_ok"] for r in rows), n),
            "주제 인식(자료 없는 민원은 '주제 없음'이 정답)": _pct(sum(r["topic_ok"] for r in rows), n),
            "측면 인식(자료 있는 민원)": _pct(sum(r["aspect"] is not None for r in in_scope), len(in_scope)),
            "정답 뜻이 선택지에 있음(주장 기준)": _pct(sum(opt), len(opt)),
            "추천 1순위가 정답": _pct(sum(r["recommended_ok"] for r in rows), n),
            "자료 없는 민원에 선택지를 내지 않음": _pct(sum(not r["wrong_options"] for r in rows if r["out_of_scope"]),
                                              sum(r["out_of_scope"] for r in rows)),
            "주장이 둘 이상인 민원(규칙은 하나로 묶음)": sum(r["gold_claims"] > 1 for r in rows),
        },
        "근거: 사용자가 정답 뜻을 고름": ev("picked"),
        "근거: 엔진에 맡김(뜻 고르기 없음)": ev("auto"),
        "말투·장소 유형별": by_style,
    }


def limits(rows: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}

    def add(key: str, r: dict, why: str = "") -> None:
        out.setdefault(key, []).append(f"{r['id']} {r['text']}" + (f" — {why}" if why else ""))
    for r in rows:
        if not r["emd_ok"]:
            add("읍면동을 잘못 알아들음", r, f"인식: {r['emd_detected'] or '없음'}")
        if not r["topic_ok"]:
            add("주제를 잘못 알아들음", r, f"인식: {', '.join(r['topics_found']) or '없음'}")
        if not r["out_of_scope"] and r["aspect"] is None:
            add("측면(부족·거리·위험 등)을 못 읽음", r)
        if not all(r["in_options"]):
            add("정답 뜻이 선택지에 없음", r)
        if r["wrong_options"]:
            add("자료 없는 민원에 선택지를 냄", r)
        if not all(r["picked"]["correct"]):
            add("정답 뜻을 골라도 근거가 안 나옴", r, f"나온 지표: {', '.join(r['picked']['indicators']) or '없음'}")
        if r["picked"]["evidence"] and not r["picked"]["computed"] and not r["out_of_scope"]:
            add("근거 값이 하나도 계산되지 않음", r, f"판정: {r['picked']['verdicts']}")
        if r["picked"]["stale"]:
            add("오래된 자료가 근거에 들어감", r)
        if not all(r["auto"]["correct"]) and all(r["picked"]["correct"]):
            add("뜻을 고르지 않으면 엔진이 다른 지표를 고름(뜻 고르기가 필요한 이유)", r,
                f"엔진: {', '.join(r['auto']['indicators']) or '없음'}")
    return out


def render(ds: dict, summary: dict, lim: dict, rows: list[dict]) -> str:
    lines = [f"# 평가 결과 ({date.today().isoformat()}, 규칙 구조화·유료 호출 없음)", "",
             "정답 세트: complaint_agent/eval/gold.yaml (초안, 사람 확인 필요). 판정이 맞는지는 아직 재지 않았다"
             "(정답 판정을 원자료로 확인한 뒤 채운다).", "", "## 1. 데이터셋", ""]
    for k, v in ds.items():
        lines.append(f"- {k}: {', '.join(v) if isinstance(v, list) else v}")
    for title in ("이해(규칙 구조화)", "근거: 사용자가 정답 뜻을 고름", "근거: 엔진에 맡김(뜻 고르기 없음)"):
        lines += ["", f"## {'2' if title.startswith('이해') else '3'}. {title}", ""]
        lines += [f"- {k}: {v}" for k, v in summary[title].items()]
    lines += ["", "## 말투·장소 유형별", "", "| 유형 | 민원 | 읍면동 | 주제 | 근거 정답(뜻 고른 뒤) |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {k} | {v['민원']} | {v['읍면동']} | {v['주제']} | {v['근거 정답(뜻 고른 뒤)']} |"
              for k, v in summary["말투·장소 유형별"].items()]
    lines += ["", "## 4. 남은 한계 (틀린 항목)", ""]
    for k, items in lim.items():
        lines.append(f"**{k}** ({len(items)})")
        lines += [f"- {x}" for x in items]
        lines.append("")
    lines += ["## 민원별", "", "| id | 유형 | 읍면동 | 주제 | 선택지 | 추천 | 근거 정답(고름/맡김) | 근거·계산 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        ok = lambda b: "O" if b else "X"  # noqa: E731
        lines.append(
            f"| {r['id']} | {r['style']} | {ok(r['emd_ok'])} {r['emd_detected'] or '-'} | {ok(r['topic_ok'])} | "
            f"{sum(r['in_options'])}/{len(r['in_options'])} | {ok(r['recommended_ok'])} | "
            f"{sum(r['picked']['correct'])}/{len(r['picked']['correct'])} · {sum(r['auto']['correct'])}/{len(r['auto']['correct'])} | "
            f"{r['picked']['computed']}/{r['picked']['evidence']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gold", type=Path, default=GOLD)
    ap.add_argument("--out", type=Path, default=RESULTS)
    ap.add_argument("--print", action="store_true")
    args = ap.parse_args(argv)
    from win_console import safe_output
    safe_output()
    restore = _block_claude()
    cfg = _config()
    agent = P.Agent(cfg)
    try:
        if agent.mock_engine:
            print("근거 DB(evidence_engine/data/jeju.duckdb)가 없어 평가할 수 없다. build_db.py 또는 snapshot.py --restore 먼저.")
            return 2
        items = yaml.safe_load(args.gold.read_text(encoding="utf-8"))["items"]
        rows = [run_item(agent, it) for it in items]
        ds = dataset_stats(agent, cfg)
    finally:
        agent.close()
        restore()
    summary, lim = summarize(rows), limits(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    stem = f"eval_{date.today().isoformat()}"
    (args.out / f"{stem}.json").write_text(json.dumps({"dataset": ds, "summary": summary, "limits": lim, "items": rows},
                                                      ensure_ascii=False, indent=1), encoding="utf-8")
    md = render(ds, summary, lim, rows)
    (args.out / f"{stem}.md").write_text(md, encoding="utf-8")
    if args.print:
        print(md)
    print(f"저장: {args.out / stem}.md, .json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
