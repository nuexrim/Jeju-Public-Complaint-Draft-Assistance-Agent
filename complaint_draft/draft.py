"""민원서 초안 생성: handoff(가린 구조화 질의 + 근거 패키지) → 표준어본·방언본 초안.

    from complaint_draft import build_draft
    draft = build_draft(handoff, source_text="우리 동네 버스가 확 줄어부난 ...")

입력 handoff 는 complaint_agent Agent.handoff() 결과와 같은 모양이다.
  query     구조화 질의(가린 사본): complaint_type, location.emd_name, normalized_text, claims, user_request
  package   근거 패키지: engine, evidence[], gaps[]
  structured_by, notices, pii_masked
원문(제주어)은 handoff 에 들어 있지 않으므로 source_text 로 따로 받는다
(handoff 에 "source_text" 키가 있으면 그것을 쓴다).

원칙
- 표준어본은 제출용, 방언본은 본인 확인용이다.
- 방언본에 LLM이 방언을 새로 만들지 않는다. 원문이 있는 '민원 내용'만 원문을 그대로 넣고(dialect_source="original"),
  나머지 섹션은 표준어를 그대로 둔다(dialect_source="standard_fallback").
- 숫자·출처는 package.evidence[].value·sources 만 옮긴다. 표시할 때 소수점 둘째 자리까지만 줄인다.
- 근거 자료에는 supported·partial 만 넣는다. contradicted·unverifiable·gaps 는 excluded 로 돌려 화면에서 안내한다.
- 엔진이 mock 이면 경고를 붙이고 copy_allowed=False 로 둔다.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from complaint_draft import templates as T

ROOT = Path(__file__).resolve().parent.parent
PRIVACY_PY = ROOT / "evidence_engine" / "package" / "privacy.py"
PII_PLACEHOLDER = "○○"            # 대화 모듈(dialect_normalizer)과 같은 자리표시
INCLUDED_VERDICTS = ("supported", "partial")
SECTION_IDS = [sid for sid, _ in T.SECTIONS]


# ---------------------------------------------------------------- 개인정보 가리기
def _load_privacy():
    """근거 엔진의 마스킹 구현(표준 라이브러리만 씀)을 그대로 불러온다. 마스킹 구현은 그 파일 하나뿐이다."""
    spec = importlib.util.spec_from_file_location("_jeju_privacy", PRIVACY_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_privacy = None


def mask(text: str | None) -> tuple[str, list[str]]:
    global _privacy
    if not text:
        return "", []
    if _privacy is None:
        _privacy = _load_privacy()
    masked, found = _privacy.mask_with_labels(text, PII_PLACEHOLDER)
    return " ".join((masked or "").split()), found


# ---------------------------------------------------------------- 입력
def load_handoff(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def format_value(value, unit: str | None) -> str:
    """값 표시. 계산하지 않고 소수점 둘째 자리까지만 줄인다."""
    if value is None:
        return "값 없음"
    if isinstance(value, float):
        text = f"{value:,.2f}".rstrip("0").rstrip(".")
    else:
        text = f"{value:,}"
    return f"{text}{unit}" if unit in ("%",) else (f"{text} {unit}" if unit else text)


def _sources_text(sources: list[dict]) -> str:
    parts = []
    for s in sources:
        bits = [b for b in (s.get("publisher"), s.get("as_of") and f"기준 {s['as_of']}") if b]
        parts.append(f"{s['dataset']}" + (f" ({', '.join(bits)})" if bits else ""))
    return "; ".join(parts) if parts else "출처 정보 없음"


# ---------------------------------------------------------------- 섹션 만들기
def _title(query: dict) -> str:
    """제목 주제: 대상(targets)이 있으면 그것을, 없으면 첫 주장에서 읍면동 이름을 뺀 문장."""
    place = query.get("location", {}).get("emd_name") or ""
    targets = [t for t in query.get("targets") or [] if t]
    if targets:
        topic = "·".join(targets[:3])
    else:
        claims = query.get("claims") or []
        topic = claims[0]["text"] if claims else query.get("normalized_text", "")
        if place:
            topic = topic.replace(place, "")
        topic = " ".join(topic.split()).rstrip(" .")
        if len(topic) > 25:
            topic = topic[:25].rstrip() + "…"
    tpl = T.TITLE if place else T.TITLE_NO_PLACE
    return tpl.format(complaint_type=query.get("complaint_type") or "기타", place=place, topic=topic)


def _content(query: dict) -> str:
    place = query.get("location", {}).get("emd_name") or ""
    lines = [T.CONTENT_INTRO.format(place=place) if place else T.CONTENT_INTRO_NO_PLACE]
    normalized = (query.get("normalized_text") or "").strip()
    if normalized:
        lines.append(normalized if normalized.endswith((".", "요", "다")) else normalized + ".")
    claims = [c["text"].strip() for c in query.get("claims") or [] if c.get("text", "").strip()]
    if claims and not (len(claims) == 1 and claims[0] == normalized):
        lines.append("")
        lines.append(T.CONTENT_CLAIMS_HEAD)
        lines.extend(f"- {c}" for c in claims)
    return "\n".join(lines)


def _request(query: dict) -> str:
    req = (query.get("user_request") or "").strip()
    return T.REQUEST.format(user_request=req) if req else T.REQUEST_DEFAULT


def _split_evidence(package: dict, query: dict) -> tuple[list[dict], list[dict]]:
    claim_text = {c["id"]: c["text"] for c in query.get("claims") or []}
    included, excluded = [], []
    for e in package.get("evidence") or []:
        if e.get("verdict") in INCLUDED_VERDICTS and e.get("value") is not None:
            included.append({
                "claim_id": e["claim_id"],
                "claim": claim_text.get(e["claim_id"], ""),
                "verdict": e["verdict"],
                "verdict_ko": T.VERDICT_KO[e["verdict"]],
                "indicator": e["indicator"],
                "value": e["value"],
                "unit": e.get("unit"),
                "value_text": format_value(e["value"], e.get("unit")),
                "region_compare": e.get("region_compare"),
                "sources": e.get("sources") or [],
                "caveats": e.get("caveats") or [],
                "chart": e.get("chart"),
            })
        else:
            verdict = e.get("verdict", "unverifiable")
            if verdict in INCLUDED_VERDICTS:      # 판정은 됐지만 값이 없으면 근거로 쓰지 않는다
                verdict = "unverifiable"
            excluded.append({
                "kind": "evidence",
                "claim_id": e.get("claim_id"),
                "claim": claim_text.get(e.get("claim_id"), ""),
                "label": e.get("indicator", ""),
                "reason": T.VERDICT_KO.get(verdict, verdict),
            })
    for g in package.get("gaps") or []:
        excluded.append({"kind": "gap", "claim_id": None, "claim": "", "label": g["concept"], "reason": g["reason"]})
    return included, excluded


def _evidence_text(items: list[dict]) -> str:
    if not items:
        return T.EVIDENCE_NONE
    lines = [T.EVIDENCE_HEAD]
    for n, it in enumerate(items, 1):
        lines.append(T.EVIDENCE_ITEM.format(n=n, indicator=it["indicator"], value=it["value_text"]))
        if it.get("region_compare"):
            lines.append(T.EVIDENCE_COMPARE.format(region_compare=it["region_compare"]))
        lines.append(T.EVIDENCE_SOURCE.format(sources=_sources_text(it["sources"])))
        if it["verdict"] == "partial":
            lines.append(T.EVIDENCE_PARTIAL)
    return "\n".join(lines)


# ---------------------------------------------------------------- 초안
def build_draft(handoff: dict, source_text: str | None = None) -> dict:
    query = handoff.get("query") or {}
    package = handoff.get("package") or {}
    if source_text is None:
        source_text = handoff.get("source_text")
    source, source_pii = mask(source_text)        # 원문이 가려지지 않은 채 들어와도 여기서 한 번 더 가린다

    included, excluded = _split_evidence(package, query)
    standard = {
        "title": _title(query),
        "content": _content(query),
        "request": _request(query),
        "evidence": _evidence_text(included),
    }

    sections = []
    for sid, title in T.SECTIONS:
        use_source = sid == "content" and bool(source)
        sections.append({
            "id": sid,
            "title": title,
            "standard": standard[sid],
            "dialect": source if use_source else standard[sid],
            "dialect_source": "original" if use_source else "standard_fallback",
        })

    is_mock = package.get("engine") == "mock"
    warnings = []
    if is_mock:
        warnings.append(T.MOCK_WARNING)
    if handoff.get("structured_by") == "rules":
        warnings.append(T.RULES_WARNING)
    if not source:
        warnings.append(T.NO_SOURCE_WARNING)
    notices = [n for n in handoff.get("notices") or [] if "mock" not in n]   # mock 안내는 위 경고로 대신한다

    return {
        "request_id": package.get("request_id") or query.get("request_id", ""),
        "sections": sections,
        "evidence": included,
        "excluded": excluded,
        "warnings": warnings,
        "notices": notices,
        "pii_masked": sorted(set(handoff.get("pii_masked") or []) | set(source_pii)),
        "engine": package.get("engine"),
        "copy_allowed": not is_mock,
    }


def plain_text(draft: dict, version: str = "standard") -> str:
    """복사·저장용 글. version='standard'(제출용) | 'dialect'(확인용)"""
    return "\n\n".join(f"[{s['title']}]\n{s[version]}" for s in draft["sections"])


def validate_draft(draft: dict) -> list[str]:
    """초안 형식 검사. 문제 목록(비면 정상)."""
    errs = []
    ids = [s.get("id") for s in draft.get("sections", [])]
    if ids != SECTION_IDS:
        errs.append(f"섹션 순서가 다름: {ids}")
    for s in draft.get("sections", []):
        for key in ("title", "standard", "dialect", "dialect_source"):
            if not s.get(key):
                errs.append(f"{s.get('id')}: {key} 비어 있음")
        if s.get("dialect_source") not in ("original", "standard_fallback"):
            errs.append(f"{s.get('id')}: dialect_source 값이 잘못됨")
        if s.get("dialect_source") == "standard_fallback" and s.get("dialect") != s.get("standard"):
            errs.append(f"{s.get('id')}: 원문이 없는데 방언본이 표준어본과 다름")
    for e in draft.get("evidence", []):
        if e.get("verdict") not in INCLUDED_VERDICTS:
            errs.append(f"{e.get('claim_id')}: 근거 자료에 넣을 수 없는 판정 {e.get('verdict')}")
    return errs
