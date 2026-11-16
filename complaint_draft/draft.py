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
- 표준어본은 격식체(합니다체)로 맞춘다(formal.py, 문장 끝만 규칙으로 바꿈). 방언본의 원문과 사용자가 직접 고친 글은 바꾸지 않는다.
- 사용자가 고칠 수 있는 것은 제목·민원 내용·요청 사항이다(apply_edits). 근거 자료는 잠근다.
- 제출용 글은 창구(국민신문고 등)의 글 칸에 맞춰 submission.title(제목 칸)·submission.body(내용 칸)로 따로 만든다.
"""
from __future__ import annotations

import copy
import csv
import importlib.util
import json
import re
from pathlib import Path

from complaint_draft import templates as T
from complaint_draft.formal import formalize, has_informal

ROOT = Path(__file__).resolve().parent.parent
PRIVACY_PY = ROOT / "evidence_engine" / "package" / "privacy.py"
EMD_CSV = ROOT / "evidence_engine" / "data" / "spatial" / "emd_codes.csv"   # emd_code, sigungu_name, emd_name
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
_sigungu = None


def sigungu_of(emd_name: str) -> str:
    """읍면동 이름 → 제주시/서귀포시 (근거 엔진의 행정동 코드표). 못 찾으면 빈 문자열."""
    global _sigungu
    if _sigungu is None:
        _sigungu = {}
        if EMD_CSV.exists():
            with EMD_CSV.open(encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    _sigungu[row["emd_name"]] = row["sigungu_name"]
    return _sigungu.get(emd_name or "", "")


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split()).rstrip(" .")
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _title(query: dict) -> str:
    """제목: '장소 + 요청' 한 문장. 요구사항이 없으면 대상(targets)이나 첫 주장을 주제로 쓴다."""
    place = query.get("location", {}).get("emd_name") or ""
    request = _shorten(formalize(query.get("user_request") or ""), 30)
    if request:
        title = f"{place} {request}" if request.endswith(T.REQUEST_ENDINGS) else \
            T.TITLE_REQUEST.format(place=place, user_request=request)
    else:
        targets = [t for t in query.get("targets") or [] if t]
        if targets:
            topic = "·".join(targets[:3])
        else:
            claims = query.get("claims") or []
            topic = claims[0]["text"] if claims else query.get("normalized_text", "")
            if place:
                topic = topic.replace(place, "")
            topic = _shorten(topic, 25)
        title = T.TITLE_TOPIC.format(place=place, topic=topic)
    return _shorten(title.strip(), T.TITLE_MAX)


def _stop(text: str) -> str:
    """문장 끝에 마침표가 없으면 붙인다."""
    text = text.rstrip()
    return text if not text or text[-1] in ".!?…" else text + "."


def _content(query: dict) -> str:
    """민원 내용. 표준어본은 격식체(합니다체)로 맞춘다: 이해한 문장은 해요체 끝을, 주장은 '-다' 끝도 바꾼다."""
    lines = [T.CONTENT_INTRO]
    raw = (query.get("normalized_text") or "").strip()
    if raw:
        lines.append(_stop(formalize(raw)))
    raw_claims = [c["text"].strip() for c in query.get("claims") or [] if c.get("text", "").strip()]
    if raw_claims and not (len(raw_claims) == 1 and raw_claims[0] == raw):
        lines.append("")
        lines.append(T.CONTENT_CLAIMS_HEAD)
        lines.extend(f"- {_stop(formalize(c, plain=True))}" for c in raw_claims)
    return "\n".join(lines)


def _request(query: dict) -> str:
    """요청 사항. 문장으로 쓴 요청('…해 주세요')은 격식체로 바꿔 그대로 쓰고, 낱말로 쓴 요청('버스 증차')은 틀에 넣는다."""
    req = formalize((query.get("user_request") or "").strip())
    if not req:
        return T.REQUEST_DEFAULT
    if req.rstrip(".").endswith(("니다", "시오")):
        return _stop(req)
    return T.REQUEST.format(user_request=req)


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
    if has_informal(standard["content"]) or has_informal(standard["request"]):
        warnings.append(T.INFORMAL_WARNING)
    if not source:
        warnings.append(T.NO_SOURCE_WARNING)
    notices = [friendly(n) for n in handoff.get("notices") or [] if "mock" not in n]   # mock 안내는 위 경고로 대신한다

    return {
        "request_id": package.get("request_id") or query.get("request_id", ""),
        "sections": sections,
        "submission": _submission(query, standard),
        "evidence": included,
        "excluded": excluded,
        "warnings": warnings,
        "notices": notices,
        "pii_masked": sorted(set(handoff.get("pii_masked") or []) | set(source_pii)),
        "engine": package.get("engine"),
        "copy_allowed": not is_mock,
    }


def _compose_body(place: str, standard: dict) -> str:
    parts = [f"{T.BODY_PLACE_HEAD}\n{place}"] if place else []
    for sid in ("content", "request", "evidence"):
        parts.append(f"{T.BODY_HEADS[sid]}\n{standard[sid]}")
    return "\n\n".join(parts)


def _submission(query: dict, standard: dict) -> dict:
    """제출 창구(국민신문고 등)의 글 칸 두 개(제목·내용)에 그대로 붙여 넣을 글과 처리기관 안내.
    이름·연락처·주소는 창구 화면에서 본인이 직접 넣으므로 여기에 넣지 않는다."""
    emd = query.get("location", {}).get("emd_name") or ""
    sigungu = sigungu_of(emd)
    place = " ".join(x for x in (T.PROVINCE, sigungu, emd) if x) if emd else ""
    title, body = standard["title"], _compose_body(place, standard)
    return {
        "title": title,
        "body": body,
        "title_chars": len(title),
        "body_chars": len(body),
        "place": place,
        "complaint_type": query.get("complaint_type") or "",
        "agency": " ".join(x for x in (T.PROVINCE, sigungu) if x),   # 처리기관 고를 때 참고(추천일 뿐)
        "portals": T.PORTALS,
        "steps": T.SUBMIT_STEPS,
    }


def apply_edits(draft: dict, edits: dict | None) -> dict:
    """사용자가 표준어본을 직접 고친 것을 반영한 새 초안. edits: {"title"|"content"|"request": 고친 글}

    - 고칠 수 있는 것은 제목·민원 내용·요청 사항뿐이다. 근거 자료(숫자·출처)는 고칠 수 없다(들어와도 무시).
    - 고친 글도 개인정보를 가린다. 빈 글이면 원래 글을 그대로 둔다.
    - 제주어 화면: 원문을 보여 주는 섹션(original)은 그대로, 표준어를 보여 주던 섹션은 고친 글을 따라간다.
    - 받은 draft 는 바꾸지 않는다.
    """
    d = copy.deepcopy(draft)
    found = set(d.get("pii_masked") or [])
    for sec in d["sections"]:
        if sec["id"] not in T.EDITABLE:
            continue
        raw = (edits or {}).get(sec["id"])
        if raw is None:
            continue
        lines = []
        for line in str(raw).replace("\r\n", "\n").split("\n"):      # 줄바꿈은 살리고 줄마다 가린다
            masked, pii = mask(line)
            found.update(pii)
            lines.append(masked)
        text = "\n".join(lines).strip()
        if sec["id"] == "title":
            text = " ".join(text.split())
        if not text or text == sec["standard"]:
            continue
        if sec["dialect_source"] == "standard_fallback":
            sec["dialect"] = text
        sec["standard"] = text
        sec["edited"] = True
    standard = {sec["id"]: sec["standard"] for sec in d["sections"]}
    sub = d["submission"]
    sub["title"], sub["body"] = standard["title"], _compose_body(sub.get("place", ""), standard)
    sub["title_chars"], sub["body_chars"] = len(sub["title"]), len(sub["body"])
    d["pii_masked"] = sorted(found)
    return d


_CODE_TAIL = re.compile(r"\s*\((?:[A-Za-z_][A-Za-z0-9_.]*)\)\s*$")


def friendly(notice: str) -> str:
    """알림 끝의 개발용 표시 '(PaidCallNotAllowed)' 등을 화면에 보이지 않게 뗀다. 원래 알림은 handoff 에 남아 있다."""
    return _CODE_TAIL.sub("", notice or "")


def plain_text(draft: dict, version: str = "standard") -> str:
    """복사·저장용 글. version='standard'(제출용: 제목 + 내용) | 'dialect'(확인용: 섹션 그대로)"""
    if version == "standard" and draft.get("submission"):
        sub = draft["submission"]
        return f"제목: {sub['title']}\n\n{sub['body']}"
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
    sub = draft.get("submission") or {}
    if not sub.get("title") or not sub.get("body"):
        errs.append("submission: 제목이나 내용이 비어 있음")
    for e in draft.get("evidence", []):
        if e.get("verdict") not in INCLUDED_VERDICTS:
            errs.append(f"{e.get('claim_id')}: 근거 자료에 넣을 수 없는 판정 {e.get('verdict')}")
    return errs
