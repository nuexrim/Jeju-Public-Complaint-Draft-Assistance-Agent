"""민원 말해봅서 연결 파이프라인: 도민 발화 → 이해(정규화·구조화) → 되묻기 → 뜻 선택 → 최종 확인 → 근거 패키지.

두 모듈을 한 프로세스에서 잇는다.
  dialect_normalizer/  제주어 → 표준어(normalize.py), 구조화·되묻기 질문(structure.py)
  evidence_engine/     해석 선택지(run_engine.interpret), 근거 패키지(run_engine.run)
민원 초안 모델은 handoff() 결과(가린 질의 + 근거 패키지)를 받는다.

단계와 상태(status)
  understand(text)        → need_input(되물을 질문 있음) | ready
  answer(result, ...)     → 사용자 답 반영(자유 입력은 먼저 가림) → need_input | ready
  meaning_options(query)  → 주장별 뜻 선택지 (엔진)
  choose(...)             → 고른 뜻을 질의에 반영 (기타는 다시 선택지)
  summary(query)          → 최종 확인 문장 (질의 필드로만 만든다. LLM 없음)
  evidence(query)         → 근거 패키지 (엔진)

되묻기 나눔
  읍면동·주장·요구사항·민원 유형·제주어 뜻은 structure.py 가 묻는다.
  주장의 뜻(무엇을 재야 하는지)은 엔진 해석 선택지가 묻는다.
  기간은 묻지 않는다: 엔진의 증감 지표는 원자료의 최근 24개월로 정해져 있어 기간을 받아도 계산이 바뀌지 않는다
  (사용자가 말한 기간만 caveat 에 반영된다).

실패해도 멈추지 않기
  - 제주어 사전·예문(processed/)이 없으면: 표준어 변환 없이 구조화 (notices 에 기록)
  - Claude 호출이 막히거나 실패하면: 규칙 구조화(rules) — 읍면동은 문장 속 이름, 유형은 엔진 주제 단서,
    주장은 문장 전체 하나. 결과에 structured_by="rules" 와 notices 를 남긴다.
  - 근거 DB(data/jeju.duckdb)가 없으면: 엔진 mock(형식만, 계산값 아님)
키는 .env 에서 읽되 ALLOW_PAID_CALLS 는 읽지 않는다(사람이 실행할 때 셸에서 직접 켠다. 근거 엔진과 같은 규칙).
"""
from __future__ import annotations

import copy
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "evidence_engine"
DIALECT_DIR = ROOT / "dialect_normalizer"
for _p in (ENGINE_DIR, DIALECT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import llm                                   # noqa: E402  (dialect_normalizer)
import structure as S                        # noqa: E402
from normalize import ResourceMissing        # noqa: E402
import run_engine                            # noqa: E402  (evidence_engine)
from indicators.library import claim_topics, load_registry   # noqa: E402
from package.builder import resolve_emd      # noqa: E402
from package.privacy import mask_query       # noqa: E402

# .env 에서 읽어도 되는 이름. ALLOW_PAID_CALLS 는 일부러 뺀다.
ENV_KEYS = ("ANTHROPIC_API_KEY", "CLAUDE_MODEL", "CLAUDE_THINKING")
ENV_FILES = (ENGINE_DIR / ".env", DIALECT_DIR / ".env")

# 규칙 구조화: 엔진 registry 의 주제 → 민원 유형. 테스트 질의 10개의 유형을 모두 맞힌다(tests 참고).
TOPIC_TO_TYPE = {"bus": "교통", "traffic_safety": "교통", "medical": "의료 접근", "waste": "환경·청소",
                 "disaster": "안전·재난", "safety": "안전·재난"}
MAX_OTHER_ROUNDS = 2          # '기타(직접 쓰기)'를 다시 해석하는 최대 횟수. 넘으면 그 문장 그대로 계산한다.


def load_env(files=ENV_FILES, keys=ENV_KEYS) -> list[str]:
    """.env 에서 허용한 이름만 환경 변수로 올린다(이미 있으면 덮지 않음). 올린 이름 목록(값 아님)을 돌려준다."""
    loaded = []
    for path in files:
        if not Path(path).exists():
            continue
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in keys and v and not os.environ.get(k):
                os.environ[k] = v
                loaded.append(k)
    return loaded


def _llm_errors() -> tuple[type[BaseException], ...]:
    errs: list[type[BaseException]] = [llm.PaidCallNotAllowed, llm.LLMError]
    try:
        import anthropic
        errs.append(anthropic.AnthropicError)  # SDK 오류 전부(연결 실패·인증·요청 한도·거절 등 APIError 포함)
    except ImportError:
        pass
    return tuple(errs)


class Agent:
    def __init__(self, config: dict | None = None, *, mock_engine: bool | None = None):
        load_env()
        self.config = config or run_engine.load_config()
        db = ENGINE_DIR / self.config["paths"]["duckdb"]
        self.mock_engine = (not db.exists()) if mock_engine is None else mock_engine
        self.engine = None
        if not self.mock_engine:
            from package.assemble import Engine
            self.engine = Engine(self.config)
        emd_csv = ENGINE_DIR / self.config["paths"]["emd_codes"]
        import csv
        with emd_csv.open(encoding="utf-8", newline="") as f:
            self._emd_by_code = {r["emd_code"]: r["emd_name"] for r in csv.DictReader(f)}
        self._emd_csv = emd_csv

    def close(self) -> None:
        if self.engine is not None:
            self.engine.close()
            self.engine = None

    # ------------------------------------------------------------------ 1. 이해
    def understand(self, text: str, *, use_normalize: bool = True) -> dict:
        notices: list[str] = []
        try:
            r = S.structure(text, use_normalize=use_normalize)
            by = "llm"
        except (ResourceMissing, ModuleNotFoundError) as e:
            # 사전·색인이 없거나(processed/) 색인을 읽을 패키지(scikit-learn)가 없음 → 변환 없이 구조화
            notices.append(f"제주어 표준어 변환을 하지 못해 원문 그대로 이해했어요 ({type(e).__name__})")
            try:
                r = S.structure(text, use_normalize=False)
                by = "llm"
            except _llm_errors() as e2:
                r, by = self._by_rules(text), "rules"
                notices.append(f"Claude 구조화를 쓰지 못해 규칙으로 정리했어요 ({type(e2).__name__})")
        except _llm_errors() as e:
            r, by = self._by_rules(text), "rules"
            notices.append(f"Claude 구조화를 쓰지 못해 규칙으로 정리했어요 ({type(e).__name__})")
        r["structured_by"] = by
        r["notices"] = notices
        r["source_text"] = S.mask_pii(" ".join(text.split()))[0]   # 다시 이해할 때 쓰는 가린 원문
        return r

    def _resolve_place(self, text: str) -> str:
        """43개 행정동 이름, 또는 리·법정동 이름(예: 세화리 → 구좌읍)이 하나로 정해질 때만."""
        name = S.find_emd_in_text(text)
        if name:
            return name
        found = set()
        for word in re.findall(r"[가-힣]{1,6}(?:리|동)(?![가-힣])", text):
            code, _ = resolve_emd({"emd_name": word}, self._emd_csv)
            if code in self._emd_by_code:
                found.add(self._emd_by_code[code])
        return found.pop() if len(found) == 1 else ""

    def _by_rules(self, text: str) -> dict:
        """Claude 없이 만드는 비상 구조화. 원문(가림)을 주장 하나로 쓰고, 빈 칸은 structure.py 와 같은 질문으로 묻는다."""
        masked, pii = S.mask_pii(" ".join(text.split()))
        topics = claim_topics(masked)
        ctype = next((TOPIC_TO_TYPE[t] for t in topics if t in TOPIC_TO_TYPE), "기타")
        cues = [c for t in load_registry().get("topics", []) if t["id"] in topics for c in t["cues"] if c in masked]
        emd = self._resolve_place(masked)
        query = {
            "request_id": S.new_request_id(),
            "normalized_text": masked,
            "dialect_confidence": 1.0,
            "complaint_type": ctype,
            "claims": [{"id": "c1", "text": masked}] if masked else [],
            "location": {"emd_name": emd, "emd_code": ""},
            "targets": list(dict.fromkeys(cues)),
        }
        questions = S._questions(query, "", "", None, "", emd_in_text=bool(emd))
        return {"status": S._status(query, questions), "query": query, "questions": questions,
                "schema_errors": S.validate(query), "pii_masked": pii, "location_mention": "", "normalize": None}

    # ------------------------------------------------------------------ 2. 되묻기 답 반영
    def answer(self, result: dict, *, emd_name: str | None = None, confirm_emd: bool | None = None,
               user_request: str | None = None, complaint_type: str | None = None,
               accept_sentence: bool = False, corrected_sentence: str | None = None,
               detail: str | None = None) -> dict:
        """사용자 답을 반영한다. 자유 입력은 모두 먼저 가린다(개인정보가 질의·LLM으로 가지 않게).

        corrected_sentence: 제주어 뜻 확인에서 사용자가 문장을 고쳐 쓴 것 → 그 문장으로 다시 이해(표준어 변환 없이)
        detail: '어떤 점이 불편하신지' 질문의 답 → 원래 문장에 덧붙여 다시 이해
        다시 이해할 때 앞서 받은 읍면동·요구사항 답은 유지한다.
        """
        r = copy.deepcopy(result)
        if corrected_sentence and corrected_sentence.strip():
            r = self._reunderstand(r, S.mask_pii(corrected_sentence)[0], use_normalize=False)
        elif detail and detail.strip():
            r = self._reunderstand(r, f"{r['query']['normalized_text']} {S.mask_pii(detail)[0]}", use_normalize=True)
        elif accept_sentence:
            r = self._update(r, normalized_text=r["query"]["normalized_text"])

        if emd_name is not None and emd_name.strip():
            name = emd_name.strip()
            resolved = name if name in S.EMD_NAMES else (S.guess_emd(name) or self._resolve_place(name))
            if resolved:
                r = self._update(r, emd_name=resolved)
            else:
                r.setdefault("notices", []).append(f"'{S.mask_pii(name)[0]}'에 해당하는 읍·면·동을 찾지 못했어요")
        elif confirm_emd is True:
            r = self._update(r, emd_name=r["query"]["location"]["emd_name"])
        elif confirm_emd is False:
            r["query"]["location"]["emd_name"] = ""
            r["questions"] = [q for q in r["questions"] if q["field"] != "emd_name"] + [
                {"field": "emd_name", "question": "어느 읍·면·동인가요? (예: 구좌읍, 노형동)"}]
            r["schema_errors"] = S.validate(r["query"])
            r["status"] = S._status(r["query"], r["questions"])
        if user_request is not None and user_request.strip():
            r = self._update(r, user_request=S.mask_pii(user_request)[0])
        elif user_request is not None:       # 빈 답 = 요구사항 없이 진행
            r["questions"] = [q for q in r["questions"] if q["field"] != "user_request"]
            r["status"] = S._status(r["query"], r["questions"])
        if complaint_type:
            r = self._update(r, complaint_type=complaint_type)
        return r

    def _update(self, r: dict, **kw) -> dict:
        keep = {k: r.get(k) for k in ("structured_by", "notices", "source_text")}
        out = S.update(r, **kw)
        out.update(keep)
        return out

    def _reunderstand(self, r: dict, text: str, *, use_normalize: bool) -> dict:
        prev = r["query"]
        new = self.understand(text, use_normalize=use_normalize)
        new["notices"] = list(dict.fromkeys(r.get("notices", []) + new["notices"]))
        if not new["query"]["location"]["emd_name"] and prev["location"].get("emd_name"):
            new = self._update(new, emd_name=prev["location"]["emd_name"])
        if not new["query"].get("user_request") and prev.get("user_request"):
            new = self._update(new, user_request=prev["user_request"])
        return new

    # ------------------------------------------------------------------ 3. 뜻 선택
    def meaning_options(self, query: dict) -> dict:
        return run_engine.interpret(query, self.config, mock=self.mock_engine, engine=self.engine)

    @staticmethod
    def choose(query: dict, interpretation: dict, picks: dict[str, str | dict]) -> tuple[dict, list[str]]:
        """picks: {claim_id: option_id} 또는 {claim_id: {"other": "직접 쓴 문장"}}.
        (새 질의, 다시 해석해야 할 claim_id 목록) — '기타'를 고른 주장은 다시 선택지를 받는다."""
        q = copy.deepcopy(query)
        by_claim = {c["claim_id"]: c for c in interpretation["claims"]}
        again = []
        for claim in q["claims"]:
            pick = picks.get(claim["id"])
            if pick is None:
                continue
            if isinstance(pick, dict):
                text = S.mask_pii(pick.get("other", ""))[0]
                if text:
                    claim["text"] = text
                    claim.pop("indicator_id", None)
                    again.append(claim["id"])
                continue
            opt = next((o for o in by_claim[claim["id"]]["options"] if o["option_id"] == pick), None)
            if opt is None:
                raise ValueError(f"{claim['id']}: 선택지에 없는 option_id '{pick}'")
            if opt["kind"] == "other":
                raise ValueError(f"{claim['id']}: 기타는 {{'other': 문장}} 으로 고른다")
            claim["text"] = opt["text"]
            claim["indicator_id"] = opt["indicator_id"]
        return q, again

    # ------------------------------------------------------------------ 4. 최종 확인
    @staticmethod
    def summary(query: dict) -> str:
        place = query["location"]["emd_name"] or "어느 지역인지 모름"
        period = query.get("period")
        when = f", {period['from']}~{period['to']}" if period else ""
        claims = " / ".join(c["text"] for c in query["claims"])
        req = f" 바라는 점: {query['user_request']}." if query.get("user_request") else ""
        return f"[{query['complaint_type']} · {place}{when}] {claims}.{req} 이렇게 이해했어요. 맞나요?"

    # ------------------------------------------------------------------ 5. 근거
    def evidence(self, query: dict) -> dict:
        return run_engine.run(query, self.config, mock=self.mock_engine, engine=self.engine)

    def handoff(self, result: dict, query: dict, package: dict) -> dict:
        """민원 초안 모델에 넘길 것. 질의는 엔진과 같은 규칙으로 한 번 더 가린 사본(raw_text 없음)."""
        return {
            "query": mask_query(query),
            "package": package,
            "structured_by": result.get("structured_by"),
            "notices": result.get("notices", []) + ([
                "근거 DB가 없어 엔진 mock 결과(계산값 아님)"] if self.mock_engine else []),
            "pii_masked": result.get("pii_masked", []),
        }
