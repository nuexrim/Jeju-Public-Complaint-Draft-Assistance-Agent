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
- 근거 고르기 화면에서 사용자가 뺀 근거(build_draft 의 exclude)도 excluded 로 돌린다.
- 표준어본에 남은 제주어 낱말은 방언 사전(dialect_normalizer/processed/dialect_dict.json)으로 찾아 경고한다. 사전이 없으면 건너뛴다.
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
from datetime import date
from pathlib import Path

from complaint_draft import templates as T
from complaint_draft.formal import formalize, has_informal

ROOT = Path(__file__).resolve().parent.parent
PRIVACY_PY = ROOT / "evidence_engine" / "package" / "privacy.py"
EMD_CSV = ROOT / "evidence_engine" / "data" / "spatial" / "emd_codes.csv"   # emd_code, sigungu_name, emd_name
DIALECT_DICT = ROOT / "dialect_normalizer" / "processed" / "dialect_dict.json"   # 제주어 낱말 → 표준어 후보
DIALECT_MIN_RATIO = 0.9          # 사전에서 제주어로 쓰인 비율이 이 이상이고
DIALECT_MIN_SEEN = 20            # 예문에 이만큼 이상 나온 낱말만 '남은 제주어'로 본다(표준어와 겹치는 낱말을 거르려고)
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


def is_stale(as_of: str | None, today: date | None = None) -> bool:
    """기준일이 STALE_MONTHS(2년)보다 오래됐는지. '2026-09', '2025-12-31', '2024' 모양을 읽는다. 못 읽으면 False."""
    m = re.match(r"\s*(\d{4})(?:[-./](\d{1,2}))?", as_of or "")
    if not m:
        return False
    today = today or date.today()
    year, month = int(m.group(1)), int(m.group(2) or 12)
    if year < 1900 or not 1 <= month <= 12:     # '0000-00' 같은 자리표시·잘못된 날짜는 판단하지 않는다
        return False
    return (today.year * 12 + today.month) - (year * 12 + month) > T.STALE_MONTHS


def _usable(e: dict) -> bool:
    return e.get("verdict") in INCLUDED_VERDICTS and e.get("value") is not None


def _item(e: dict, claim_text: dict) -> dict:
    """근거 하나를 화면·초안에서 쓰는 모양으로. 숫자·출처는 패키지 값을 그대로 옮긴다."""
    sources = e.get("sources") or []
    dates = list(dict.fromkeys(s.get("as_of") for s in sources if s.get("as_of")))
    verdict = e.get("verdict", "unverifiable")
    return {
        "claim_id": e.get("claim_id"),
        "claim": claim_text.get(e.get("claim_id"), ""),
        "verdict": verdict,
        "verdict_ko": T.VERDICT_KO.get(verdict, verdict),
        "indicator": e.get("indicator", ""),
        "value": e.get("value"),
        "unit": e.get("unit"),
        "value_text": format_value(e.get("value"), e.get("unit")),
        "region_compare": e.get("region_compare"),
        "sources": sources,
        "as_of": ", ".join(dates),
        "stale": any(is_stale(d) for d in dates),
        "caveats": e.get("caveats") or [],
        "chart": e.get("chart"),
    }


def evidence_choices(package: dict, query: dict) -> list[dict]:
    """근거 고르기 화면에 보여 줄 근거 전부. index 는 package.evidence 의 순서,
    usable=False 는 민원서에 넣을 수 없는 근거(자료와 다름·확인 불가·값 없음)라 체크할 수 없다."""
    claim_text = {c["id"]: c["text"] for c in query.get("claims") or []}
    return [{**_item(e, claim_text), "index": i, "usable": _usable(e)}
            for i, e in enumerate(package.get("evidence") or [])]


def _split_evidence(package: dict, query: dict, exclude=()) -> tuple[list[dict], list[dict]]:
    claim_text = {c["id"]: c["text"] for c in query.get("claims") or []}
    exclude = set(exclude or ())
    included, excluded = [], []
    for i, e in enumerate(package.get("evidence") or []):
        if _usable(e) and i not in exclude:
            included.append({**_item(e, claim_text), "index": i})
        elif _usable(e):
            excluded.append({"kind": "user", "claim_id": e["claim_id"], "claim": claim_text.get(e["claim_id"], ""),
                             "label": e.get("indicator", ""), "reason": T.USER_EXCLUDED})
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


# ---------------------------------------------------------------- 남은 제주어 낱말
_dialect = None


def _dialect_words() -> set[str]:
    """방언 사전에서 '거의 제주어로만 쓰이는' 낱말. 사전 파일이 없거나 읽지 못하면 빈 집합(검사를 건너뛴다)."""
    global _dialect
    if _dialect is None:
        _dialect = set()
        if DIALECT_DICT.exists():
            try:
                data = json.loads(DIALECT_DICT.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            _dialect = {w for w, info in data.items()
                        if len(w) >= 2 and isinstance(info, dict)
                        and info.get("dialect_ratio", 0) >= DIALECT_MIN_RATIO and info.get("seen", 0) >= DIALECT_MIN_SEEN}
    return _dialect


def leftover_dialect(*texts: str) -> list[str]:
    """표준어본 글에 남은 제주어 낱말, 나온 순서대로. 어절에서 문장부호를 떼고, 조사가 붙은 경우('할망덜이')도
    잡도록 어절 앞부분 중 사전에 있는 가장 긴 것(두 글자 이상)을 찾는다."""
    words = _dialect_words()
    found: list[str] = []
    if not words:
        return found
    for text in texts:
        for token in (text or "").split():
            w = re.sub(r"[^가-힣]", "", token)
            hit = next((w[:n] for n in range(len(w), 1, -1) if w[:n] in words), None)
            if hit and hit not in found:
                found.append(hit)
    return found


def _check_text(d: dict) -> None:
    """남은 제주어 낱말과 글자 수 제한을 다시 검사해 d 의 dialect_words·warnings 를 맞춘다(고친 뒤에도 부른다)."""
    std = {sec["id"]: sec["standard"] for sec in d["sections"]}
    d["dialect_words"] = leftover_dialect(std["title"], std["content"], std["request"])
    keep = [w for w in d["warnings"] if not w.startswith((T.DIALECT_WARNING_HEAD, "제목 글자 수", "내용 글자 수"))]
    if d["dialect_words"]:
        keep.append(T.DIALECT_WARNING.format(words=", ".join(d["dialect_words"])))
    sub = d["submission"]
    for name, n, limit in (("제목", sub["title_chars"], T.TITLE_LIMIT), ("내용", sub["body_chars"], T.BODY_LIMIT)):
        if n > limit:
            keep.append(T.LIMIT_WARNING.format(name=name, n=n, limit=limit))
    d["warnings"] = keep


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
def build_draft(handoff: dict, source_text: str | None = None, exclude=None) -> dict:
    """exclude: 근거 고르기에서 사용자가 뺀 근거의 index(package.evidence 순서). 빠진 근거는 excluded 로 간다."""
    query = handoff.get("query") or {}
    package = handoff.get("package") or {}
    if source_text is None:
        source_text = handoff.get("source_text")
    source, source_pii = mask(source_text)        # 원문이 가려지지 않은 채 들어와도 여기서 한 번 더 가린다

    included, excluded = _split_evidence(package, query, exclude)
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

    draft = {
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
        "dialect_words": [],
    }
    _check_text(draft)
    return draft


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
        "title_limit": T.TITLE_LIMIT,
        "body_limit": T.BODY_LIMIT,
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
    d.setdefault("warnings", [])
    _check_text(d)
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
