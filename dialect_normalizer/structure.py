"""
구조화 모듈: 민원 문장 → 구조화 질의 JSON (근거 패키지 엔진 입력, contracts/query.schema.json)

흐름
  ① 개인정보 가리기 (LLM에 보내기 전)
  ② 방언 → 표준어 (normalize.py)
  ③ Claude로 구조화: 민원 유형, 읍면동, 주장(claims), 대상(targets), 요구사항, 기간
  ④ 코드로 검사: 읍면동 목록 확인, 기간 형식, 빈칸 → 사용자에게 되물을 질문 생성
  ⑤ 계약 스키마 검증 (evidence_engine/contracts/query.schema.json 이 있으면)

사용법
  python3 structure.py "우리 동네 버스가 확 줄어부난 할망덜 병원 가기가 막 힘들어마씸 구좌읍"
  python3 structure.py --no-normalize "표준어 문장"      방언 변환 없이 구조화만
  python3 structure.py --out q.json "문장"               질의 JSON을 파일로 저장 (엔진 --query 에 바로 넣기)
  python3 structure.py --check-fixtures                  팀원 예시 질의 10개로 구조화 정확도 확인

다른 코드에서
  from structure import structure, update
  r = structure("문장")          # r["status"]: "ready" | "need_input",  r["query"], r["questions"]
  r = update(r, emd_name="구좌읍", user_request="버스 증차")   # 사용자가 답한 값 반영 후 재검사
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import secrets
from datetime import date, datetime
from pathlib import Path

import llm

BASE = Path(__file__).resolve().parent
REPO = BASE.parent
CONTRACT = REPO / "evidence_engine" / "contracts" / "query.schema.json"
FIXTURES = REPO / "evidence_engine" / "tests" / "fixtures"

# 엔진 README의 권장 유형 + 기타
COMPLAINT_TYPES = ["교통", "의료 접근", "환경·청소", "안전·재난", "생활물가", "기타"]

# 제주 43개 읍면동 (행정동)
EMD_NAMES = [
    # 제주시 읍면
    "한림읍", "애월읍", "구좌읍", "조천읍", "한경면", "추자면", "우도면",
    # 제주시 동
    "일도1동", "일도2동", "이도1동", "이도2동", "삼도1동", "삼도2동", "용담1동", "용담2동",
    "건입동", "화북동", "삼양동", "봉개동", "아라동", "오라동", "연동", "노형동", "외도동",
    "이호동", "도두동",
    # 서귀포시 읍면
    "대정읍", "남원읍", "성산읍", "안덕면", "표선면",
    # 서귀포시 동
    "송산동", "정방동", "중앙동", "천지동", "효돈동", "영천동", "동홍동", "서홍동",
    "대륜동", "대천동", "중문동", "예래동",
]
MAX_CLAIMS = 3


# =====================================================================
# ① 개인정보 가리기
#    구현은 근거 엔진의 evidence_engine/package/privacy.py 하나뿐이다(두 벌로 두면 규칙이 어긋난다).
#    LLM에 보내기 전에 가리고, 자리표시는 예문 데이터(AI Hub)의 가명 표기와 같은 '○○'를 쓴다.
# =====================================================================
import sys as _sys

_ENGINE = REPO / "evidence_engine"
if str(_ENGINE) not in _sys.path:
    _sys.path.insert(0, str(_ENGINE))
from package.privacy import mask_with_labels  # noqa: E402

PII_PLACEHOLDER = "○○"


def mask_pii(text: str) -> tuple[str, list[str]]:
    """(가린 문장, 가린 종류). 종류: 주민번호·전화번호·이메일·동호수·지번·건물번호·이름"""
    masked, found = mask_with_labels(text, PII_PLACEHOLDER)
    return re.sub(r"\s+", " ", masked or "").strip(), found


# =====================================================================
# ③ Claude로 구조화
# =====================================================================
SCHEMA = {
    "type": "object",
    "properties": {
        "complaint_type": {"type": "string", "enum": COMPLAINT_TYPES},
        "location_mention": {"type": "string"},
        "emd_name": {"type": "string", "enum": EMD_NAMES + [""]},
        "claims": {"type": "array", "items": {
            "type": "object", "properties": {"text": {"type": "string"}},
            "required": ["text"], "additionalProperties": False}},
        "targets": {"type": "array", "items": {"type": "string"}},
        "user_request": {"type": "string"},
        "period_from": {"type": "string"},
        "period_to": {"type": "string"},
    },
    "required": ["complaint_type", "location_mention", "emd_name", "claims", "targets",
                 "user_request", "period_from", "period_to"],
    "additionalProperties": False,
}

RULES = """너는 제주 도민의 민원 문장을 근거 확인 엔진에 넘길 구조화 질의로 바꾸는 도우미다.
엔진은 공공데이터로 각 주장(claim)이 사실인지 계산한다. 그러므로 원문에 없는 내용을 지어내지 않는다.

항목 규칙
- complaint_type: 보기 중 하나. 보기에 맞는 것이 없으면 "기타".
- location_mention: 문장에서 장소를 가리키는 말을 그대로 (예: "세화리", "우리 동네 노형"). 없으면 "".
- emd_name: 제주 행정 읍면동 중 하나. 문장에 읍면동이 나오거나, 리·마을 이름으로 읍면동을 확실히 알 수 있을 때만 쓴다.
  확실하지 않으면 "". "제주시", "서귀포시"만으로는 정하지 않는다.
- claims: 데이터로 확인할 수 있는 짧은 평서문 1~3개. "~다"로 끝낸다. 하나의 주장에는 한 가지 측면만 담는다.
  장소 이름(읍면동·마을)은 location에 있으므로 claims에 넣지 않는다. 요구사항(무엇을 해달라)은 claims에 넣지 않는다.
  숫자를 만들지 않는다. 사용자가 말한 측면(줄었다, 부족하다, 멀다, 자주 발생한다 등)을 바꾸지 않는다.
- targets: 주장에 나오는 핵심 대상 명사 (시설, 교통수단, 인구 집단, 시간대 등).
- user_request: 사용자가 원하는 조치를 사용자가 쓴 말 그대로. 사용자가 말하지 않았으면 "" (추측하지 않는다).
- period_from, period_to: 사용자가 기간을 말했을 때만 YYYY-MM 형식. 오늘 날짜를 기준으로 계산한다. 말하지 않았으면 둘 다 ""."""


def _load_examples(exclude_id: str | None = None, k: int = 4) -> list[dict]:
    """팀원 예시 질의(fixtures)를 few-shot 예시로 사용. 없으면 기본 예시 2개."""
    exs = []
    if FIXTURES.exists():
        for fp in sorted(FIXTURES.glob("q*.json")):
            q = json.loads(fp.read_text(encoding="utf-8"))
            if q.get("request_id") == exclude_id:
                continue
            exs.append(q)
    if not exs:
        exs = [
            {"normalized_text": "우리 동네 버스가 크게 줄어서 할머니들이 병원 가기가 매우 힘들어요",
             "complaint_type": "교통", "claims": [{"text": "버스가 줄었다"}, {"text": "노인의 병원 접근이 어렵다"}],
             "location": {"emd_name": "구좌읍"}, "targets": ["버스", "병원", "노인"], "user_request": ""},
            {"normalized_text": "클린하우스가 항상 넘쳐서 쓰레기가 길에 쌓여 있습니다",
             "complaint_type": "환경·청소", "claims": [{"text": "클린하우스가 인구에 비해 부족하다"}],
             "location": {"emd_name": "이도2동"}, "targets": ["클린하우스", "쓰레기"], "user_request": ""},
        ]
    # 유형이 겹치지 않게 골라 담기
    picked, seen = [], set()
    for q in exs:
        if q["complaint_type"] not in seen:
            picked.append(q)
            seen.add(q["complaint_type"])
    for q in exs:
        if len(picked) >= k:
            break
        if q not in picked:
            picked.append(q)
    return picked[:k]


def build_prompt(text: str, exclude_id: str | None = None) -> str:
    lines = [f"오늘 날짜: {date.today().isoformat()}", "", "[예시]"]
    flat = re.sub(r"\s", "", text)
    for q in _load_examples(exclude_id, k=5):
        if re.sub(r"\s", "", q["normalized_text"]) in flat:   # 입력과 같은 문장의 예시는 답을 베끼게 되므로 제외
            continue
        out = {"complaint_type": q["complaint_type"],
               "emd_name": q.get("location", {}).get("emd_name", ""),
               "claims": [c["text"] for c in q["claims"]],
               "targets": q.get("targets", []),
               "user_request": q["user_request"] if is_grounded(q.get("user_request", ""),
                                                                q["normalized_text"]) else ""}
        lines.append(f"문장: {q['normalized_text']}\n결과: {json.dumps(out, ensure_ascii=False)}")
    lines += ["", "[민원 문장]", text]
    return "\n".join(lines)


# =====================================================================
# ④ 검사 + 질문 만들기
# =====================================================================
_YM = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# 읍면동 이름 바로 뒤에 올 수 있는 말(조사·장소 말). 이것이 아닌 한글이 붙으면 다른 낱말로 본다
# (예: '연동되어'의 '연동'은 지명이 아님 — 연결 시험에서 발견).
_AFTER_PLACE = ("에서", "에는", "에도", "에", "은", "는", "이", "가", "을", "를", "의", "도", "만", "과", "와", "까지",
                "부터", "으로", "로", "쪽", "주민", "사람", "근처", "일대", "지역", "인데", "이고", "이에요", "입니다", "에요",
                "사무소", "행정복지센터", "주민센터")
_PLACE_END = "(?=$|[^가-힣]|" + "|".join(sorted(_AFTER_PLACE, key=len, reverse=True)) + ")"


def find_emd_in_text(text: str) -> str:
    """문장에 읍면동 이름이 그대로 들어 있으면 그것을 씀 (하나일 때만). LLM 판단보다 우선."""
    found = {e for e in EMD_NAMES if re.search(rf"(?<![가-힣]){e}{_PLACE_END}", text)}
    return found.pop() if len(found) == 1 else ""


def guess_emd(mention: str) -> str:
    """'노형' → '노형동', '구좌' → '구좌읍'처럼 줄여 말한 읍면동 찾기 (하나로 정해질 때만)."""
    m = re.sub(r"\s", "", mention or "")
    if not m:
        return ""
    if m in EMD_NAMES:
        return m
    cands = [e for e in EMD_NAMES if e in m or (len(m) >= 2 and e.startswith(m))]
    return cands[0] if len(cands) == 1 else ""


def drop_place_questions(norm: dict | None) -> dict | None:
    """'구좌읍'은 어떤 뜻인가요? 같은 지명 질문은 뺌 (지명은 방언이 아님)."""
    if not norm:
        return norm
    norm = copy.deepcopy(norm)
    keep = []
    for q in norm["ask_user"]:
        m = re.search(r"'([^']+)'", q)
        if m and guess_emd(m.group(1).strip()):
            continue
        keep.append(q)
    norm["ask_user"] = keep
    return norm


def _questions(query: dict, mention: str, emd_from_llm: str, norm: dict | None,
               suggested_request: str = "", emd_in_text: bool = False) -> list[dict]:
    qs = []
    if norm:
        for q in norm.get("ask_user", []):
            qs.append({"field": "dialect", "question": q})
    emd = query.get("location", {}).get("emd_name", "")
    if not emd:
        where = f"말씀하신 '{mention}'은(는) " if mention else ""
        qs.append({"field": "emd_name", "question": f"{where}어느 읍·면·동인가요? (예: 구좌읍, 노형동)"})
    elif not emd_in_text and mention and emd_from_llm and re.sub(r"\s", "", mention) not in (emd, emd[:-1]) \
            and emd[:-1] not in mention:
        qs.append({"field": "emd_name", "question": f"말씀하신 '{mention}'은(는) '{emd}'이(가) 맞나요?",
                   "confirm": True})
    if not query.get("claims"):
        qs.append({"field": "claims", "question": "어떤 점이 불편하신지 조금 더 자세히 말씀해 주시겠어요?"})
    if not query.get("user_request"):
        if suggested_request:
            qs.append({"field": "user_request", "optional": True, "suggestion": suggested_request,
                       "question": f"'{suggested_request}'을(를) 요청하시는 게 맞나요? 아니면 원하시는 조치를 말씀해 주세요."})
        else:
            qs.append({"field": "user_request", "question": "어떻게 바뀌었으면 하시나요? (예: 버스 증차, 시설 설치)",
                       "optional": True})
    if query.get("complaint_type") == "기타":
        qs.append({"field": "complaint_type",
                   "question": "현재 근거 확인은 교통, 의료 접근, 환경·청소, 안전·재난, 생활물가 민원만 가능해요. "
                               "근거 없이 민원서만 작성할까요?", "optional": True})
    return qs


def _status(query: dict, questions: list[dict]) -> str:
    blocking = [q for q in questions if not q.get("optional") and not q.get("confirm")]
    return "ready" if not blocking and validate(query) == [] else "need_input"


def validate(query: dict) -> list[str]:
    """계약 스키마 검증. 스키마 파일이나 jsonschema가 없으면 필수 항목만 확인."""
    errors = []
    try:
        import jsonschema
        if CONTRACT.exists():
            schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
            v = jsonschema.Draft202012Validator(schema)
            return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in v.iter_errors(query)]
    except ImportError:
        pass
    for k in ["request_id", "normalized_text", "complaint_type", "claims", "location"]:
        if not query.get(k):
            errors.append(f"{k}: 비어 있음")
    if query.get("location") is not None and not query["location"].get("emd_name"):
        errors.append("location/emd_name: 비어 있음")
    return errors


def is_grounded(phrase: str, text: str) -> bool:
    """요구사항의 단어(2글자 이상)가 모두 사용자 문장에 나오는지. 아니면 LLM이 짐작한 것."""
    words = [w for w in re.findall(r"[가-힣A-Za-z0-9]+", phrase) if len(w) >= 2]
    flat = re.sub(r"\s", "", text)
    return bool(words) and all(w[:2] in flat for w in words)


def dialect_confidence(norm: dict | None) -> float:
    """바꾼 단어 + 모르는 단어 중 사전 근거가 있는 비율. 바꾼 것도 모르는 것도 없으면 1.0."""
    if not norm:
        return 1.0
    n_dict = sum(c["type"] == "사전" for c in norm["changes"])
    n_guess = sum(c["type"] == "추정" for c in norm["changes"])
    n_unclear = max(len(norm["ask_user"]) - n_guess, 0)
    total = len(norm["changes"]) + n_unclear
    return round(n_dict / total, 2) if total else 1.0


# =====================================================================
# 전체 실행
# =====================================================================
def new_request_id() -> str:
    return f"r-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2)}"


def structure(text: str, *, use_normalize: bool = True, include_raw: bool = False,
              exclude_example_id: str | None = None) -> dict:
    raw = " ".join(text.split())
    masked, pii = mask_pii(raw)                                        # ①

    norm = None
    normalized = masked
    if use_normalize:                                                  # ②
        from normalize import normalize
        norm = drop_place_questions(normalize(masked))
        normalized = norm["standard"]

    out = llm.call_json(RULES, build_prompt(normalized, exclude_example_id), SCHEMA,
                        purpose="structure")                           # ③

    text_emd = find_emd_in_text(normalized)
    emd = text_emd \
        or (out["emd_name"] if out["emd_name"] in EMD_NAMES else "") \
        or guess_emd(out["location_mention"])                         # ④
    claims = [c["text"].strip() for c in out["claims"] if c["text"].strip()][:MAX_CLAIMS]

    query = {
        "request_id": new_request_id(),
        "normalized_text": normalized,
        "dialect_confidence": dialect_confidence(norm),
        "complaint_type": out["complaint_type"],
        "claims": [{"id": f"c{i}", "text": t} for i, t in enumerate(claims, 1)],
        "location": {"emd_name": emd, "emd_code": ""},
        "targets": [t for t in out["targets"] if t.strip()],
        "user_request": "",
    }
    req = out["user_request"].strip()
    suggested = ""
    if req and is_grounded(req, normalized):
        query["user_request"] = req
    elif req:
        suggested = req                       # 사용자가 말하지 않은 요구사항 → 질의에 넣지 않고 확인 질문
    if _YM.match(out["period_from"]) and _YM.match(out["period_to"]) \
            and out["period_from"] <= out["period_to"]:
        query["period"] = {"from": out["period_from"], "to": out["period_to"]}
    if include_raw:
        query["raw_text"] = masked                                     # 원문도 가린 뒤에만
    if not query["user_request"]:
        query.pop("user_request")

    questions = _questions(query, out["location_mention"], out["emd_name"], norm, suggested,
                           emd_in_text=bool(text_emd))   # 문장에 읍면동이 직접 있으면 확인 질문 생략
    result = {
        "status": "",
        "query": query,
        "questions": questions,
        "schema_errors": validate(query),
        "pii_masked": pii,
        "location_mention": out["location_mention"],
        "normalize": norm,
    }
    result["status"] = _status(query, questions)
    return result


def update(result: dict, *, emd_name: str | None = None, user_request: str | None = None,
           claims: list[str] | None = None, complaint_type: str | None = None,
           normalized_text: str | None = None) -> dict:
    """사용자가 질문에 답한 값을 반영하고 다시 검사."""
    r = copy.deepcopy(result)
    q = r["query"]
    answered = set()
    if emd_name is not None:
        e = emd_name if emd_name in EMD_NAMES else guess_emd(emd_name)
        if e:
            q["location"]["emd_name"] = e
            answered.add("emd_name")
    if user_request:
        q["user_request"] = user_request.strip()
        answered.add("user_request")
    if claims:
        q["claims"] = [{"id": f"c{i}", "text": t.strip()} for i, t in enumerate(claims[:MAX_CLAIMS], 1)
                       if t.strip()]
        answered.add("claims")
    if complaint_type in COMPLAINT_TYPES:
        q["complaint_type"] = complaint_type
        answered.add("complaint_type")
    if normalized_text:
        q["normalized_text"] = normalized_text.strip()
        answered.add("dialect")
    r["questions"] = [x for x in r["questions"] if x["field"] not in answered]
    r["schema_errors"] = validate(q)
    r["status"] = _status(q, r["questions"])
    return r


def check_fixtures():
    """팀원 예시 질의 10개의 표준어 문장을 넣어 유형·읍면동·주장 수가 맞는지 확인 (자기 예시는 빼고)."""
    fps = sorted(FIXTURES.glob("q*.json"))
    if not fps:
        print(f"예시 질의가 없어요: {FIXTURES}")
        return
    ok_type = ok_emd = ok_valid = 0
    for fp in fps:
        gold = json.loads(fp.read_text(encoding="utf-8"))
        # 예시 문장에는 읍면동이 없으므로 끝에 붙여서 넣음
        text = f"{gold['location']['emd_name']} {gold['normalized_text']}"
        r = structure(text, use_normalize=False, exclude_example_id=gold["request_id"])
        q = r["query"]
        t, e = q["complaint_type"] == gold["complaint_type"], \
            q["location"]["emd_name"] == gold["location"]["emd_name"]
        ok_type += t
        ok_emd += e
        ok_valid += not r["schema_errors"]
        print(f"{fp.stem}: 유형 {'✓' if t else '✗ ' + q['complaint_type']} | "
              f"읍면동 {'✓' if e else '✗ ' + q['location']['emd_name']} | "
              f"주장 {[c['text'] for c in q['claims']]}  (정답 {[c['text'] for c in gold['claims']]})")
    n = len(fps)
    print(f"\n유형 {ok_type}/{n} · 읍면동 {ok_emd}/{n} · 스키마 통과 {ok_valid}/{n}")


def pretty(r: dict):
    print(f"\n상태: {'✅ 엔진에 보낼 준비 완료' if r['status'] == 'ready' else '❓ 추가 정보 필요'}")
    if r["pii_masked"]:
        print(f"개인정보 가림: {', '.join(r['pii_masked'])}")
    if r["normalize"]:
        print(f"표준어: {r['query']['normalized_text']}  (방언 이해 신뢰도 {r['query']['dialect_confidence']})")
    print("\n[구조화 질의]")
    print(json.dumps(r["query"], ensure_ascii=False, indent=2))
    if r["questions"]:
        print("\n[사용자에게 물어볼 것]")
        for q in r["questions"]:
            tag = " (선택)" if q.get("optional") else (" (확인)" if q.get("confirm") else "")
            print(f"  ❓ [{q['field']}]{tag} {q['question']}")
    if r["schema_errors"]:
        print("\n[계약 스키마 오류]")
        for e in r["schema_errors"]:
            print(f"  🚨 {e}")
    print()


def main():
    llm.load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?")
    ap.add_argument("--no-normalize", action="store_true", help="방언 변환 없이 구조화만")
    ap.add_argument("--include-raw", action="store_true", help="원문(개인정보 가린 것)을 raw_text로 포함")
    ap.add_argument("--out", help="질의 JSON 저장 경로")
    ap.add_argument("--check-fixtures", action="store_true")
    args = ap.parse_args()
    if args.check_fixtures:
        check_fixtures()
        return
    text = args.text or input("민원 내용을 입력하세요: ")
    r = structure(text, use_normalize=not args.no_normalize, include_raw=args.include_raw)
    pretty(r)
    if args.out:
        Path(args.out).write_text(json.dumps(r["query"], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {args.out}")


if __name__ == "__main__":
    main()
