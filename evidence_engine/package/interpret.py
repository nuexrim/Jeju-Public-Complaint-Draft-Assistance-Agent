"""주장 해석 선택지: 근거를 계산하기 전에 "이 주장이 이런 뜻인가요?"를 사용자에게 고르게 한다.

같은 "버스가 줄었다"도 운행 횟수가 줄었다는 뜻일 수 있고, 타는 사람이 줄었다는 뜻일 수 있다.
엔진이 혼자 고르면 뜻이 다른 지표로 판정할 수 있으므로, 엔진이 계산할 수 있는 뜻을 쉬운 문장으로 보여 준다.

선택지 종류 (kind)
  indicator : 등록된 지표로 계산할 수 있는 뜻.        indicator_id = 지표 id
  no_data   : 그런 뜻이지만 지금 자료로는 확인할 수 없음. indicator_id = "gap:<id>" (판정 unverifiable + 빈칸 기록)
  other     : 기타(직접 쓰기). indicator_id = null

팀원 쪽 흐름
  1) 구조화 질의로 interpret() 를 부른다 → 주장마다 선택지 목록
  2) 사용자가 고르면 claims[i].text 를 고른 문장으로 바꾸고 claims[i].indicator_id 를 채워 run() 을 부른다
  3) 기타를 고르면 사용자가 쓴 문장을 claims[i].text 로 넣고 indicator_id 없이 다시 interpret() 또는 run()

선택지 문장은 registry.yaml 의 ask 틀로만 만든다(민원 원문을 옮기지 않는다 → 개인정보가 섞이지 않음).
"""
from __future__ import annotations

from indicators.library import (
    _has, aspect_name, claim_aspect, claim_topics, load_registry, select_indicator,
)

MAX_OPTIONS = 5          # 기타를 뺀 선택지 수
OTHER_TEXT = "기타 (직접 쓰기)"
OTHER_NOTE = "직접 쓴 문장을 claims[].text 로 보내면 다시 해석한다"
NO_DATA_NOTE = "지금 자료로는 확인할 수 없음 (근거 없이 '확인 불가'로 남김)"


def _fill(template: str, emd_label: str | None) -> str:
    return template.replace("{emd}", emd_label or "우리 동네")


def claim_options(claim: dict, query: dict, tables: set[str], emd_label: str | None,
                  registry: dict | None = None) -> dict:
    registry = registry or load_registry()
    text = claim["text"]
    targets = " ".join(query.get("targets", []))
    context = " ".join([query.get("normalized_text", ""), targets])
    aspect = claim_aspect(text, registry)
    topics = claim_topics(text, targets, registry)
    best, _ = select_indicator(text, context, tables, registry)

    # 점수: 주장 측면과 같은 뜻 +3, 지표 낱말이 하나라도 맞음 +1, 노인 등 가산어 +0.5, 엔진 기본 선택 +1,
    #       필수어(requires)가 문장에 없음 -1. 같은 점수면 계산 가능한 지표가 먼저.
    scored = []
    measured = set()
    for ind in registry["indicators"]:
        if ind.get("topic") not in topics or not ind.get("ask"):
            continue
        if not set(ind["tables"]) <= tables:
            continue
        if ind.get("option_requires") and not _has(text + " " + context, ind["option_requires"]):
            continue
        if _has(text + " " + context, ind.get("excludes")):
            continue
        measured.add(ind.get("measures"))
        score = (3 if aspect and ind.get("measures") == aspect else 0) \
            + min(1, len(_has(text, ind["keywords"]))) + 0.5 * len(_has(text, ind.get("bonus"))) \
            + (1 if best and best["id"] == ind["id"] else 0) \
            - (1 if ind.get("requires") and not _has(text, ind["requires"]) else 0)
        scored.append((score, 1, {
            "text": _fill(ind["ask"], emd_label), "kind": "indicator", "indicator_id": ind["id"],
            "measures": aspect_name(ind.get("measures"), registry),
            "note": "제주 전체 값만 있어 이 지역을 판정하지 않음" if ind["scope"] == "jeju" else None,
        }))
    for gap in registry.get("known_gaps", []):
        if gap.get("topic") not in topics or not gap.get("ask"):
            continue
        # 같은 측면을 재는 지표가 없을 때만(그 뜻을 말할 길이 이것뿐일 때만) 측면 가산
        score = (3 if aspect and gap.get("aspect") == aspect and aspect not in measured else 0) \
            + min(1, len(_has(text + " " + context, gap["keywords"])))
        scored.append((score, 0, {
            "text": _fill(gap["ask"], emd_label), "kind": "no_data", "indicator_id": f"gap:{gap['id']}",
            "measures": aspect_name(gap.get("aspect"), registry), "note": NO_DATA_NOTE,
        }))
    scored.sort(key=lambda s: (-s[0], -s[1]))
    options = [s[2] for s in scored[:MAX_OPTIONS]]
    options.append({"text": OTHER_TEXT, "kind": "other", "indicator_id": None, "measures": None, "note": OTHER_NOTE})
    for i, o in enumerate(options, 1):
        o["option_id"] = f"{claim['id']}-o{i}"
    return {
        "claim_id": claim["id"],
        "aspect": aspect_name(aspect, registry),
        "options": [{k: o[k] for k in ("option_id", "text", "kind", "indicator_id", "measures", "note")}
                    for o in options],
        "recommended": options[0]["option_id"],
    }


def interpret(query: dict, tables: set[str], emd_label: str | None, registry: dict | None = None) -> dict:
    """구조화 질의(마스킹된 사본) → 주장별 해석 선택지."""
    return {
        "request_id": query["request_id"],
        "claims": [claim_options(c, query, tables, emd_label, registry) for c in query["claims"]],
    }
