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
from indicators.library import claim_topics, cue_in, load_registry, positive_tone   # noqa: E402
from package.builder import resolve_emd      # noqa: E402
from package.privacy import mask_query       # noqa: E402

# .env 에서 읽어도 되는 이름. ALLOW_PAID_CALLS 는 일부러 뺀다.
ENV_KEYS = ("ANTHROPIC_API_KEY", "CLAUDE_MODEL", "CLAUDE_THINKING")
ENV_FILES = (ENGINE_DIR / ".env", DIALECT_DIR / ".env")

# 규칙 구조화: 엔진 registry 의 주제 → 민원 유형. 테스트 질의 10개의 유형을 모두 맞힌다(tests 참고).
TOPIC_TO_TYPE = {"bus": "교통", "traffic_safety": "교통", "medical": "의료 접근", "waste": "환경·청소",
                 "disaster": "안전·재난", "safety": "안전·재난", "elderly_welfare": "복지"}
# 읍면동 고르기: 이름을 못 알아들었을 때 목록에서 고른다. 여러 곳(장소마다 엔진을 따로 돌림)과 '제주 전체'.
EMD_ALL = "제주 전체"         # 질의 location.emd_name 에 이 값이 오면 읍면동 비교 지표는 계산하지 않는다(제주 전체 값만)
# 장소 수만큼 근거가 늘어난다(주장 최대 3 × 장소). 민원서 한 장에 읽을 만한 양으로 뜻 고르기 상한(3)과 같게 둔다.
MAX_PLACES = 3


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
    def understand(self, text: str, *, use_normalize: bool = True, topics: list[str] | None = None) -> dict:
        """topics: 화면에서 고른 주제(이름 structure.topic_names() 또는 registry id). 힌트로만 쓴다:
        ① 유형이 비었거나 '기타'면 고른 주제의 유형(하나로 정해질 때), ② 주제 단서를 targets 에 보태 문장에 주제가
        없을 때 뜻 선택지·근거 주제를 찾게 한다. 문장에 주제가 있으면 문장이 이긴다(고른 주제로 막지 않음).
        안 고르면(None/빈 목록) 예전과 같다."""
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
        if positive_tone(r["query"]["normalized_text"]):
            notices.append("불편보다는 칭찬·감사 말씀으로 들려요. 불편한 점이 있으면 더 말씀해 주세요")
        r["notices"] = notices
        r["source_text"] = S.mask_pii(" ".join(text.split()))[0]   # 다시 이해할 때 쓰는 가린 원문
        r["places"] = [r["query"]["location"]["emd_name"]] if r["query"]["location"]["emd_name"] else []
        r["topics"] = self.topic_ids(topics)
        if r["topics"]:
            r = self._apply_topics(r)
        return self._with_choices(r)

    # ------------------------------------------------------------------ 고른 주제 (힌트)
    @staticmethod
    def topic_ids(topics: list[str] | None) -> list[str]:
        """주제 이름·id → registry 주제 id (모르는 값은 버린다, 순서 유지)."""
        reg = load_registry().get("topics", [])
        by = {t["id"]: t["id"] for t in reg} | {t.get("name", t["id"]): t["id"] for t in reg}
        return list(dict.fromkeys(by[x.strip()] for x in (topics or []) if x and x.strip() in by))

    def _apply_topics(self, r: dict) -> dict:
        q = r["query"]
        types = {TOPIC_TO_TYPE[t] for t in r["topics"] if t in TOPIC_TO_TYPE}
        if q.get("complaint_type") in ("", "기타") and len(types) == 1:
            q["complaint_type"] = types.pop()
        cues = {t["id"]: t["cues"][0].rstrip("-") for t in load_registry().get("topics", [])}
        q["targets"] = list(dict.fromkeys(list(q.get("targets", [])) + [cues[t] for t in r["topics"]]))
        if S.evidence_topics(q):                  # 고른 주제로 근거 자료가 생겼으면 '근거 없음' 안내는 뺀다
            r["questions"] = [x for x in r["questions"] if x["field"] != "complaint_type"]
        r["schema_errors"] = S.validate(q)
        r["status"] = S.status_of(q, r["questions"])
        return r

    # ------------------------------------------------------------------ 읍면동 고르기
    def place_choices(self) -> dict:
        """화면의 읍면동 고르기 목록. 시·읍면/동으로 묶고 '제주 전체'를 따로 둔다."""
        groups: dict[str, list[str]] = {}
        import csv
        with self._emd_csv.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                kind = "읍·면" if row["emd_name"].endswith(("읍", "면")) else "동"
                groups.setdefault(f"{row['sigungu_name']} {kind}", []).append(row["emd_name"])
        return {"all": EMD_ALL, "max_picks": MAX_PLACES,
                "groups": [{"label": k, "names": v} for k, v in groups.items()]}

    def _with_choices(self, r: dict) -> dict:
        """읍면동을 묻는 질문(확인 질문 제외)에 고르기 목록을 붙인다. 화면은 목록으로, 터미널은 글로 받는다."""
        for q in r.get("questions", []):
            if q["field"] == "emd_name" and not q.get("confirm"):
                q["choices"] = self.place_choices()
                q["question"] = "어느 읍·면·동의 일인가요? 여러 곳이면 모두 골라 주세요 (제주 전체도 고를 수 있어요)"
        return r

    def _set_places(self, r: dict, names: list[str]) -> dict:
        """고른 장소들을 반영한다. 질의에는 첫 장소(계약상 하나)를, 나머지는 r['places'] 에 둔다."""
        if EMD_ALL in names:
            r["query"]["location"] = {"emd_name": EMD_ALL, "emd_code": ""}
            r["questions"] = [q for q in r["questions"] if q["field"] != "emd_name"]
            r["schema_errors"] = S.validate(r["query"])
            r["status"] = S.status_of(r["query"], r["questions"])
            r["places"] = [EMD_ALL]
            return r
        r = self._update(r, emd_name=names[0])
        r["places"] = names
        return r

    def _resolve_place(self, text: str) -> tuple[str, str]:
        """(읍면동, 확인할 말). 43개 행정동 이름, 리·법정동 이름(예: 세화리 → 구좌읍), 줄인 이름(노형 → 노형동)
        순서로 찾고 하나로 정해질 때만 쓴다. 줄인 이름은 글자만 보고 고른 것이라 확인할 말을 함께 돌려준다."""
        name = S.find_emd_in_text(text)
        if name:
            return name, ""
        found = set()
        for word in re.findall(r"[가-힣]{1,6}(?:리|동)(?![가-힣])", text):
            code, _ = resolve_emd({"emd_name": word}, self._emd_csv)
            if code in self._emd_by_code:
                found.add(self._emd_by_code[code])
        if len(found) == 1:
            return found.pop(), ""
        if found:
            return "", ""
        return S.find_short_emd_in_text(text)

    def _by_rules(self, text: str) -> dict:
        """Claude 없이 만드는 비상 구조화. 원문(가림)을 주장 하나로 쓰고, 빈 칸은 structure.py 와 같은 질문으로 묻는다."""
        masked, pii = S.mask_pii(" ".join(text.split()))
        topics = claim_topics(masked)
        ctype = next((TOPIC_TO_TYPE[t] for t in topics if t in TOPIC_TO_TYPE), "기타")
        cues = [c.rstrip("-") for t in load_registry().get("topics", []) if t["id"] in topics
                for c in t["cues"] if cue_in(c, masked)]
        emd, short_mention = self._resolve_place(masked)
        query = {
            "request_id": S.new_request_id(),
            "normalized_text": masked,
            "dialect_confidence": 1.0,
            "complaint_type": ctype,
            "claims": [{"id": "c1", "text": masked}] if masked else [],
            "location": {"emd_name": emd, "emd_code": ""},
            "targets": list(dict.fromkeys(cues)),
        }
        questions = S.build_questions(query, "", "", None, "", emd_in_text=bool(emd))
        if short_mention:
            questions = S.add_short_name_question(questions, short_mention, emd)
        return {"status": S.status_of(query, questions), "query": query, "questions": questions,
                "schema_errors": S.validate(query), "pii_masked": pii, "location_mention": "", "normalize": None}

    # ------------------------------------------------------------------ 2. 되묻기 답 반영
    def answer(self, result: dict, *, emd_name: str | None = None, confirm_emd: bool | None = None,
               user_request: str | None = None, complaint_type: str | None = None,
               accept_sentence: bool = False, corrected_sentence: str | None = None,
               detail: str | None = None, emd_names: list[str] | None = None) -> dict:
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

        if emd_names:                        # 목록에서 고른 장소들 (여러 곳 또는 '제주 전체')
            names = [n for n in dict.fromkeys(x.strip() for x in emd_names) if n in S.EMD_NAMES or n == EMD_ALL]
            if not names:
                r.setdefault("notices", []).append("고른 읍·면·동을 찾지 못했어요")
            elif len(names) > MAX_PLACES and EMD_ALL not in names:
                raise ValueError(f"장소는 {MAX_PLACES}곳까지 고른다 (받은 수 {len(names)})")
            else:
                r = self._set_places(r, names)
        elif emd_name is not None and emd_name.strip():
            name = emd_name.strip()
            parts = [x.strip() for x in re.split(r"[,，/]", name) if x.strip()]
            if len(parts) > 1 or name == EMD_ALL or name == "전체":   # 터미널: '구좌읍, 성산읍' 또는 '전체'
                return self.answer(r, emd_names=[EMD_ALL if x in ("전체", EMD_ALL) else
                                                 (x if x in S.EMD_NAMES else S.guess_emd(x) or self._resolve_place(x)[0])
                                                 for x in parts], user_request=user_request,
                                   complaint_type=complaint_type)
            resolved = name if name in S.EMD_NAMES else (S.guess_emd(name) or self._resolve_place(name)[0])
            if resolved:
                r = self._set_places(r, [resolved])
            else:
                r.setdefault("notices", []).append(f"'{S.mask_pii(name)[0]}'에 해당하는 읍·면·동을 찾지 못했어요")
        elif confirm_emd is True:
            r = self._update(r, emd_name=r["query"]["location"]["emd_name"])
        elif confirm_emd is False:
            r["query"]["location"]["emd_name"] = ""
            r["questions"] = [q for q in r["questions"] if q["field"] != "emd_name"] + [
                {"field": "emd_name", "question": "어느 읍·면·동인가요? (예: 구좌읍, 노형동)"}]
            r["schema_errors"] = S.validate(r["query"])
            r["status"] = S.status_of(r["query"], r["questions"])
            r["places"] = []
        if user_request is not None and user_request.strip():
            r = self._update(r, user_request=S.mask_pii(user_request)[0])
        elif user_request is not None:       # 빈 답 = 요구사항 없이 진행
            r["questions"] = [q for q in r["questions"] if q["field"] != "user_request"]
            r["status"] = S.status_of(r["query"], r["questions"])
        if complaint_type:
            r = self._update(r, complaint_type=complaint_type)
        return self._with_choices(r)

    def _update(self, r: dict, **kw) -> dict:
        keep = {k: r.get(k) for k in ("structured_by", "notices", "source_text", "places", "topics")}
        out = S.update(r, **kw)
        out.update(keep)
        return out

    def _reunderstand(self, r: dict, text: str, *, use_normalize: bool) -> dict:
        prev = r["query"]
        new = self.understand(text, use_normalize=use_normalize, topics=r.get("topics"))
        new["notices"] = list(dict.fromkeys(r.get("notices", []) + new["notices"]))
        if not new["query"]["location"]["emd_name"] and r.get("places"):
            new = self._set_places(new, r["places"])
        elif not new["query"]["location"]["emd_name"] and prev["location"].get("emd_name"):
            new = self._update(new, emd_name=prev["location"]["emd_name"])
        if not new["query"].get("user_request") and prev.get("user_request"):
            new = self._update(new, user_request=prev["user_request"])
        return new

    # ------------------------------------------------------------------ 3. 뜻 선택
    def meaning_options(self, query: dict) -> dict:
        return run_engine.interpret(query, self.config, mock=self.mock_engine, engine=self.engine)

    @staticmethod
    def choose(query: dict, interpretation: dict, picks: dict[str, str | dict | list]) -> tuple[dict, list[str]]:
        """사용자가 고른 뜻을 질의에 넣는다. (새 질의, 다시 해석해야 할 claim_id 목록)

        picks[claim_id] 는 셋 중 하나:
          "c1-o2"                    뜻 하나
          ["c1-o1", "c1-o3"]         뜻 여러 개(최대 max_picks). 고른 뜻마다 주장이 하나씩 생긴다(c1, c1-2, ...)
          {"other": "직접 쓴 문장"}   기타. 목록 안에 넣어도 된다. 이 주장은 다시 선택지를 받는다
        """
        q = copy.deepcopy(query)
        by_claim = {c["claim_id"]: c for c in interpretation["claims"]}
        used_ids = {c["id"] for c in q["claims"]}
        out, again = [], []
        for claim in q["claims"]:
            pick = picks.get(claim["id"])
            if pick is None:
                out.append(claim)
                continue
            items = pick if isinstance(pick, list) else [pick]
            info = by_claim.get(claim["id"])
            if info is None:
                raise ValueError(f"{claim['id']}: 해석 선택지가 없는 주장")
            limit = info.get("max_picks", 1)
            if not items or len(items) > limit:
                raise ValueError(f"{claim['id']}: 뜻은 1~{limit}개 고른다 (받은 수 {len(items)})")
            made, seen = [], set()
            for item in items:
                if isinstance(item, dict):
                    text = S.mask_pii(item.get("other", ""))[0].strip()
                    if text:
                        made.append(({"text": text}, True))
                    continue
                opt = next((o for o in info["options"] if o["option_id"] == item), None)
                if opt is None:
                    raise ValueError(f"{claim['id']}: 선택지에 없는 option_id '{item}'")
                if opt["kind"] == "other":
                    raise ValueError(f"{claim['id']}: 기타는 {{'other': 문장}} 으로 고른다")
                if opt["indicator_id"] in seen:
                    continue
                seen.add(opt["indicator_id"])
                made.append(({"text": opt["text"], "indicator_id": opt["indicator_id"]}, False))
            if not made:                      # 기타 문장이 비었음 → 원래 주장 그대로
                out.append(claim)
                continue
            for n, (fields, is_other) in enumerate(made, 1):
                cid = claim["id"] if n == 1 else Agent._next_id(claim["id"], used_ids)
                used_ids.add(cid)
                out.append({"id": cid, **fields})
                if is_other:
                    again.append(cid)
        q["claims"] = out
        return q, again

    @staticmethod
    def _next_id(base: str, used: set[str]) -> str:
        n = 2
        while f"{base}-{n}" in used:
            n += 1
        return f"{base}-{n}"

    # ------------------------------------------------------------------ 4. 최종 확인
    @staticmethod
    def summary(query: dict, places: list[str] | None = None) -> str:
        place = ", ".join(places) if places and len(places) > 1 else (query["location"]["emd_name"] or "어느 지역인지 모름")
        period = query.get("period")
        when = f", {period['from']}~{period['to']}" if period else ""
        claims = " / ".join(c["text"] for c in query["claims"])
        req = f" 바라는 점: {query['user_request']}." if query.get("user_request") else ""
        return f"[{query['complaint_type']} · {place}{when}] {claims}.{req} 이렇게 이해했어요. 맞나요?"

    # ------------------------------------------------------------------ 5. 근거
    def evidence(self, query: dict, places: list[str] | None = None) -> dict:
        """근거 패키지. places 가 둘 이상이면 장소마다 엔진을 돌려 근거를 합친다
        (계약상 질의의 장소는 하나라서. 근거마다 지표 이름 끝에 '· 장소'가 붙어 있어 섞이지 않는다)."""
        if not places or len(places) < 2:
            return run_engine.run(query, self.config, mock=self.mock_engine, engine=self.engine)
        merged = None
        for name in places:
            q = {**query, "location": {"emd_name": name, "emd_code": ""}}
            pkg = run_engine.run(q, self.config, mock=self.mock_engine, engine=self.engine)
            if merged is None:
                merged = pkg
                continue
            merged["evidence"] += pkg["evidence"]
            merged["gaps"] += [g for g in pkg["gaps"] if g not in merged["gaps"]]
        return merged

    def handoff(self, result: dict, query: dict, package: dict) -> dict:
        """민원 초안 모델에 넘길 것. 질의는 엔진과 같은 규칙으로 한 번 더 가린 사본(raw_text 없음).
        places: 사용자가 고른 장소 전부(질의의 location 은 그중 첫 곳)."""
        places = result.get("places") or ([query["location"]["emd_name"]] if query["location"]["emd_name"] else [])
        return {
            "query": mask_query(query),
            "package": package,
            "places": places,
            "structured_by": result.get("structured_by"),
            "notices": result.get("notices", []) + ([
                "근거 DB가 없어 엔진 mock 결과(계산값 아님)"] if self.mock_engine else []) + ([
                "제주 전체를 골라 읍·면·동끼리 비교하는 지표는 계산하지 않았어요"] if EMD_ALL in places else []),
            "pii_masked": result.get("pii_masked", []),
        }
