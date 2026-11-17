"""
제주 방언 → 표준어 변환 모듈 (Claude API)

흐름: ① 어절 나누기 → ② 사전 찾기 → ③ 비슷한 예문 찾기 → ④ LLM 변환 → ⑤ 근거 검사(감시)

준비
  pip install -r requirements.txt
  .env 파일에 (.env.example 참고):
    ANTHROPIC_API_KEY=sk-ant-...
    CLAUDE_MODEL=claude-haiku-5-5
    ALLOW_PAID_CALLS=1               ← 사람이 직접 적어야 실제 호출됨 (팀 규칙)

사용법
  python normalize.py --build-index           예문 검색 색인 만들기 (처음 한 번, 1~3분)
  python normalize.py "고냉이가 막 울엄수다"      한 문장 변환
  python normalize.py --zero-shot "..."        사전·예문 없이 LLM만 (비교용 A방법)
  python normalize.py                          대화형 모드 (여러 문장 연속 입력)

다른 코드에서 쓰기
  from normalize import normalize
  result = normalize("문장")            # dict 반환
"""
import argparse
import difflib
import json
import pickle
import random
import sys
from collections import defaultdict
from pathlib import Path

import llm

BASE = Path(__file__).resolve().parent
PROC = BASE / "processed"
DICT_PATH = PROC / "dialect_dict.json"
PAIRS_PATH = PROC / "pairs_train.jsonl"
INDEX_PATH = PROC / "example_index.pkl"

# ---------- 설정값 ----------
INDEX_DAILY_MAX = 150_000        # 예문 색인에 넣을 일상대화 문장 수 (어르신 문장은 전부 넣음)
EXAMPLE_MAX_LEN = 200            # 이보다 긴 문장은 예문에서 제외 (프롬프트가 너무 길어지지 않게)
TOP_K_EXAMPLES = 5               # LLM에게 보여줄 예문 수
PREFIX_MIN_COVER = 0.5           # 앞부분 일치: 사전 단어가 어절 길이의 50% 이상이어야 인정
FUZZY_CUTOFF = 0.85              # 비슷한 철자 찾기 기준 (1.0 = 완전히 같음)
MIN_PROMPT_RATIO = 0.1           # 방언 비율이 이보다 낮은 단어(예: '안', '잘')는 프롬프트에 넣지 않음
STRONG_RATIO = 0.5               # 이 이상이면 '강한 근거', 그 아래는 '표준어로도 자주 쓰임' 표시
SEED = 42
PUNCT = "?.,!~…\"'“”‘’()[]"
SOURCE_NAME = {"daily": "일상대화", "senior": "어르신", "official": "도청"}


# =====================================================================
# ② 사전 찾기
# =====================================================================
class DialectDict:
    def __init__(self, path=DICT_PATH):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.d = {}
        for k, v in raw.items():          # v2.2 이전 형식(목록)과 v2.3 형식(dict) 모두 지원
            self.d[k] = v if isinstance(v, dict) else {"dialect_ratio": 1.0, "seen": 0, "cands": v}
        self.by_first = defaultdict(list)      # 첫 글자별로 묶어서 비슷한 철자 찾기를 빠르게
        for k in self.d:
            self.by_first[k[0]].append(k)

    def lookup(self, word):
        """어절 하나를 사전에서 찾음. 정확히 일치 → 앞부분 일치 → 비슷한 철자 순서."""
        w = word.strip(PUNCT)
        if not w:
            return None
        if w in self.d:
            return self._hit(w, "정확히 일치", w)
        # 앞부분 일치: '고냉이가'는 사전에 없어도 '고냉이'가 있으면 인정 (뒤에 붙은 조사 등)
        for L in range(len(w) - 1, 1, -1):
            k = w[:L]
            if k in self.d and L / len(w) >= PREFIX_MIN_COVER:
                return self._hit(w, "앞부분 일치", k)
        # 비슷한 철자: 받아쓰기·음성인식 오류 대비
        if len(w) >= 3:
            m = difflib.get_close_matches(w, self.by_first.get(w[0], []), n=1, cutoff=FUZZY_CUTOFF)
            if m:
                return self._hit(w, "비슷한 철자", m[0])
        return None

    def _hit(self, word, match, key):
        e = self.d[key]
        r = e["dialect_ratio"]
        strength = "강함" if r >= STRONG_RATIO else ("보통" if r >= MIN_PROMPT_RATIO else "약함")
        return {"word": word, "match": match, "key": key, "cands": e["cands"],
                "ratio": r, "strength": strength}


def describe_cands(cands):
    parts = []
    for c in cands:
        src = ", ".join(f"{SOURCE_NAME.get(s, s)} {n}" for s, n in c.get("sources", {}).items())
        parts.append(f"{c['standard']}({c['count']}회{': ' + src if src else ''})")
    return " / ".join(parts)


# =====================================================================
# ③ 비슷한 예문 찾기 (글자 조각 TF-IDF)
# =====================================================================
class ExampleIndex:
    def __init__(self, path=INDEX_PATH):
        with open(path, "rb") as f:
            obj = pickle.load(f)
        self.vec, self.X, self.examples = obj["vec"], obj["X"], obj["examples"]

    @staticmethod
    def build(pairs_path=PAIRS_PATH, out_path=INDEX_PATH):
        from sklearn.feature_extraction.text import TfidfVectorizer

        print("예문 고르는 중... (pairs_train.jsonl 전체를 한 번 훑음)")
        random.seed(SEED)
        senior, daily, seen = [], [], 0
        with open(pairs_path, encoding="utf-8") as f:
            for line in f:
                if line.rstrip().endswith('"n_dialect": 0}'):   # 방언 없는 문장은 빠르게 건너뜀
                    continue
                r = json.loads(line)
                if len(r["dialect"]) > EXAMPLE_MAX_LEN:
                    continue
                ex = (r["dialect"], r["standard"], r["source"])
                if r["source"] == "daily":
                    seen += 1                                    # 저수지 표집: 전체에서 고르게 뽑기
                    if len(daily) < INDEX_DAILY_MAX:
                        daily.append(ex)
                    else:
                        j = random.randrange(seen)
                        if j < INDEX_DAILY_MAX:
                            daily[j] = ex
                else:
                    senior.append(ex)
        examples = senior + daily
        print(f"어르신 {len(senior)}개 + 일상대화 {len(daily)}개 = {len(examples)}개로 색인 만드는 중...")
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3), min_df=2, sublinear_tf=True)
        X = vec.fit_transform([e[0] for e in examples])
        with open(out_path, "wb") as f:
            pickle.dump({"vec": vec, "X": X, "examples": examples}, f)
        print(f"완료: {out_path}")

    def search(self, text, k=TOP_K_EXAMPLES):
        import numpy as np
        scores = (self.X @ self.vec.transform([text]).T).toarray().ravel()
        top = np.argsort(-scores)[: k * 4]
        out, used = [], set()
        for i in top:
            d, s, src = self.examples[i]
            if d in used or scores[i] <= 0:
                continue
            used.add(d)
            out.append({"dialect": d, "standard": s, "source": src, "score": round(float(scores[i]), 3)})
            if len(out) == k:
                break
        return out


# =====================================================================
# ④ LLM 변환
# =====================================================================
RULES_RAG = """너는 제주 방언 문장을 표준어 문장으로 바꾸는 도우미다. 결과는 민원서 작성에 쓰인다.
규칙:
1. [사전 근거]에 나온 단어는 후보 중 문맥에 맞는 것으로 바꾼다. 후보가 여러 개면 문맥과 [비슷한 예문]을 보고 고른다.
   단, 그 단어가 이 문장에서 이미 표준어로 쓰였다면 바꾸지 않는다.
   '표준어로도 자주 쓰임' 표시가 붙은 단어는 특히 주의해서, 이 문장에서 방언 뜻으로 쓰였을 때만 바꾼다.
2. [사전 근거]에 없는 단어는 함부로 바꾸지 않는다. 방언으로 보이는데 뜻이 확실하지 않으면 원래 단어를 그대로 두고 unclear에 넣는다.
3. 뜻을 더하거나 빼지 않는다. 요약하거나 공손하게 고쳐 쓰지 말고, 단어만 표준어로 바꾼다. 문장부호를 새로 붙이지 않는다.
4. '○○'는 가려진 이름이니 그대로 둔다. 지명(읍·면·동·리, 마을 이름)은 방언이 아니므로 바꾸지 않고 unclear에도 넣지 않는다.
5. changes에는 실제로 바꾼 단어만 원래 어절(from)과 바꾼 표현(to)으로 적는다. 바꾸지 않은 단어는 적지 않는다.
6. unclear는 뜻을 모르는 방언 단어에만 쓴다. question에는 그 단어를 작은따옴표로 넣고, 어르신이 이해하기 쉬운 짧은 존댓말로 뜻만 묻는다.
   (예: "'○○'는 어떤 뜻인가요?") 문장 내용이나 상황을 묻지 않는다."""

RULES_ZERO = """너는 제주 방언 문장을 표준어 문장으로 바꾸는 도우미다.
규칙:
1. 뜻을 더하거나 빼지 않는다. 요약하거나 공손하게 고쳐 쓰지 말고, 단어만 표준어로 바꾼다.
2. '○○'는 가려진 이름이니 그대로 둔다.
3. 바꾼 단어는 changes에 원래 어절(from)과 바꾼 표현(to)으로 적는다.
4. 뜻이 확실하지 않은 단어는 unclear에 넣는다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "standard": {"type": "string"},
        "changes": {"type": "array", "items": {
            "type": "object",
            "properties": {"from": {"type": "string"}, "to": {"type": "string"}},
            "required": ["from", "to"], "additionalProperties": False}},
        "unclear": {"type": "array", "items": {
            "type": "object",
            "properties": {"word": {"type": "string"}, "guess": {"type": "string"},
                           "question": {"type": "string"}},
            "required": ["word", "guess", "question"], "additionalProperties": False}},
    },
    "required": ["standard", "changes", "unclear"],
    "additionalProperties": False,
}


def build_prompt(text, hits, examples):
    lines = [f"[입력 문장]\n{text}\n", "[사전 근거]"]
    if hits:
        for h in hits:
            via = "" if h["match"] == "정확히 일치" else f" (사전 단어 '{h['key']}', {h['match']})"
            weak = "" if h["strength"] == "강함" else \
                f"  ※ 표준어로도 자주 쓰임 (방언으로 쓰인 비율 {h['ratio']:.0%})"
            lines.append(f"- {h['word']}{via} → {describe_cands(h['cands'])}{weak}")
    else:
        lines.append("- (사전에서 찾은 단어 없음)")
    lines.append("\n[비슷한 예문]")
    for i, e in enumerate(examples, 1):
        lines.append(f"{i}. 방언: {e['dialect']}\n   표준어: {e['standard']}")
    return "\n".join(lines)


def call_llm(system, user):
    return llm.call_json(system, user, SCHEMA, purpose="normalize")


# =====================================================================
# ⑤ 근거 검사 (감시)
# =====================================================================
def _bare(t):
    return [w.strip(PUNCT) for w in t.split() if w.strip(PUNCT)]


def check_evidence(text, llm_out, ddict):
    out = llm_out["standard"]
    in_words, out_words = _bare(text), _bare(out)
    changes, ask, warnings, ignored = [], [], [], []

    for c in llm_out["changes"]:
        src, dst = c["from"].strip().strip(PUNCT), c["to"].strip().strip(PUNCT)
        if not src or src == dst:
            continue
        if src not in text:
            warnings.append(f"입력에 없는 단어를 바꿨다고 함: '{src}'")
            continue
        # 가짜 변경: '바꿨다'고 했지만 결과 문장에 그대로 남아 있음 → 무시
        if out_words.count(src) >= in_words.count(src) and src in out_words:
            ignored.append(f"{src} → {dst}")
            continue
        hit = ddict.lookup(src) if ddict else None
        matched = None
        if hit:
            for cand in hit["cands"]:
                if cand["standard"] in dst or dst in cand["standard"]:
                    matched = cand
                    break
        if matched and hit["strength"] != "약함":
            changes.append({"from": src, "to": dst, "type": "사전",
                            "evidence": f"{hit['key']}→{matched['standard']} "
                                        f"({describe_cands([matched])}, {hit['match']}, "
                                        f"방언 비율 {hit['ratio']:.0%})"})
        else:
            if matched:
                why = f"사전에 있지만 표준어로 더 자주 쓰이는 단어 (방언 비율 {hit['ratio']:.0%})"
            elif hit:
                why = f"사전 후보({describe_cands(hit['cands'])})와 다름"
            else:
                why = "사전 근거 없음"
            changes.append({"from": src, "to": dst, "type": "추정", "evidence": why})
            ask.append(f"'{src}'을(를) '{dst}'(으)로 이해했는데 맞나요?")

    changed = {c["from"] for c in changes}
    for u in llm_out["unclear"]:
        w = u["word"].strip().strip(PUNCT)
        if not w or w not in text or w in changed:      # 입력에 없는 단어, 이미 처리한 단어는 버림
            continue
        q = u["question"] if f"'{w}'" in u["question"] else ""   # 단어를 '따옴표'로 묻는 질문만 인정
        ask.append(q or f"'{w}'은(는) 어떤 뜻인가요?")  # 단어가 안 들어간 질문은 기본 질문으로 교체

    # changes에 적지 않고 몰래 바꾼 단어 찾기 (문장부호·띄어쓰기 차이는 무시)
    declared = " ".join(c["from"] for c in llm_out["changes"])
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=in_words, b=out_words).get_opcodes():
        if op == "equal":
            continue
        if "".join(in_words[i1:i2]) == "".join(out_words[j1:j2]):   # 띄어쓰기만 다름
            continue
        hidden = [w for w in in_words[i1:i2] if w not in declared]
        if hidden:
            warnings.append(f"기록 없이 바뀐 단어: {', '.join(hidden)}  "
                            f"(구간: '{' '.join(in_words[i1:i2])}' → '{' '.join(out_words[j1:j2])}')")
    return changes, ask, warnings, ignored


# =====================================================================
# 전체 실행
# =====================================================================
_res = {}


class ResourceMissing(FileNotFoundError):
    """방언 사전·예문 색인(processed/)이 없을 때. 라이브러리로 쓸 때 호출한 쪽 프로세스를 끝내지 않도록 예외로 알린다."""


def _load(use_rag):
    if use_rag and "dict" not in _res:
        if not DICT_PATH.exists():
            raise ResourceMissing(f"방언 사전이 없어요: {DICT_PATH} (먼저 python preprocess.py)")
        if not INDEX_PATH.exists():
            raise ResourceMissing("예문 색인이 없어요. 먼저 실행하세요:  python normalize.py --build-index")
        _res["dict"] = DialectDict()
        _res["index"] = ExampleIndex()
    return _res.get("dict"), _res.get("index")


def normalize(text, use_rag=True):
    text = " ".join(text.split())
    ddict, index = _load(use_rag)
    if use_rag:
        all_hits = [h for w in text.split() if (h := ddict.lookup(w))]   # ① 어절 나누기 + ② 사전
        hits = [h for h in all_hits if h["strength"] != "약함"]          # '안', '잘' 같은 단어는 제외
        examples = index.search(text)                                 # ③ 예문
        llm_out = call_llm(RULES_RAG, build_prompt(text, hits, examples))  # ④ LLM
    else:
        hits, examples = [], []
        llm_out = call_llm(RULES_ZERO, f"[입력 문장]\n{text}")
    changes, ask, warnings, ignored = check_evidence(text, llm_out, ddict)  # ⑤ 감시
    return {
        "input": text,
        "standard": llm_out["standard"],
        "changes": changes,
        "ask_user": ask,
        "warnings": warnings,
        "ignored_changes": ignored,
        "dict_hits": [{"word": h["word"], "key": h["key"], "match": h["match"],
                       "ratio": h["ratio"], "cands": h["cands"]} for h in hits],
        "examples": examples,
        "mode": "rag" if use_rag else "zero_shot",
        "model": llm.model_name(),
    }


def pretty(r):
    print(f"\n입력  : {r['input']}\n표준어: {r['standard']}")
    if r["changes"]:
        print("\n[바꾼 단어]")
        for c in r["changes"]:
            mark = "✅" if c["type"] == "사전" else "⚠️ "
            print(f"  {mark} {c['from']} → {c['to']}   ({c['type']}: {c['evidence']})")
    if r["ask_user"]:
        print("\n[사용자에게 확인할 것]")
        for q in r["ask_user"]:
            print(f"  ❓ {q}")
    if r["warnings"]:
        print("\n[경고]")
        for w in r["warnings"]:
            print(f"  🚨 {w}")
    print()


def main():
    from win_console import safe_output
    safe_output()
    llm.load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?")
    ap.add_argument("--zero-shot", action="store_true", help="사전·예문 없이 LLM만 (비교용)")
    ap.add_argument("--build-index", action="store_true")
    ap.add_argument("--json", action="store_true", help="결과를 JSON 그대로 출력")
    args = ap.parse_args()

    if args.build_index:
        ExampleIndex.build()
        return

    def run(t):
        try:
            r = normalize(t, use_rag=not args.zero_shot)
        except ResourceMissing as e:          # 명령줄에서는 전처리 안내를 보여 주고 끝낸다(예전 동작과 같음)
            sys.exit(str(e))
        print(json.dumps(r, ensure_ascii=False, indent=2)) if args.json else pretty(r)

    if args.text:
        run(args.text)
    else:
        print("방언 문장을 입력하세요. (끝내려면 빈 줄에서 Enter)")
        while (t := input("> ").strip()):
            run(t)


if __name__ == "__main__":
    main()
