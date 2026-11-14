"""
제주 방언 → 표준어 변환 모듈 (OpenAI API)

흐름: ① 어절 나누기 → ② 사전 찾기 → ③ 비슷한 예문 찾기 → ④ LLM 변환 → ⑤ 근거 검사(감시)

준비
  pip install openai python-dotenv scikit-learn numpy
  .env 파일에:
    OPENAI_API_KEY=sk-...
    OPENAI_MODEL=사용할_모델_이름        (python3 normalize.py --list-models 로 확인)

사용법
  python3 normalize.py --build-index           예문 검색 색인 만들기 (처음 한 번, 1~3분)
  python3 normalize.py "고냉이가 막 울엄수다"      한 문장 변환
  python3 normalize.py --zero-shot "..."        사전·예문 없이 LLM만 (비교용 A방법)
  python3 normalize.py                          대화형 모드 (여러 문장 연속 입력)
  python3 normalize.py --list-models            내 키로 쓸 수 있는 모델 목록

다른 코드에서 쓰기
  from normalize import normalize
  result = normalize("문장")            # dict 반환
"""
import argparse
import difflib
import json
import os
import pickle
import random
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
PROC = BASE / "processed"
DICT_PATH = PROC / "dialect_dict.json"
PAIRS_PATH = PROC / "pairs_train.jsonl"
INDEX_PATH = PROC / "example_index.pkl"

# ---------- 설정값 ----------
DEFAULT_MODEL = "gpt-4o-mini"   # .env의 OPENAI_MODEL이 있으면 그걸 씀
INDEX_DAILY_MAX = 150_000        # 예문 색인에 넣을 일상대화 문장 수 (어르신 문장은 전부 넣음)
EXAMPLE_MAX_LEN = 200            # 이보다 긴 문장은 예문에서 제외 (프롬프트가 너무 길어지지 않게)
TOP_K_EXAMPLES = 5               # LLM에게 보여줄 예문 수
PREFIX_MIN_COVER = 0.5           # 앞부분 일치: 사전 단어가 어절 길이의 50% 이상이어야 인정
FUZZY_CUTOFF = 0.85              # 비슷한 철자 찾기 기준 (1.0 = 완전히 같음)
SEED = 42
PUNCT = "?.,!~…\"'“”‘’()[]"
SOURCE_NAME = {"daily": "일상대화", "senior": "어르신", "official": "도청"}


# =====================================================================
# ② 사전 찾기
# =====================================================================
class DialectDict:
    def __init__(self, path=DICT_PATH):
        self.d = json.loads(Path(path).read_text(encoding="utf-8"))
        self.by_first = defaultdict(list)      # 첫 글자별로 묶어서 비슷한 철자 찾기를 빠르게
        for k in self.d:
            self.by_first[k[0]].append(k)

    def lookup(self, word):
        """어절 하나를 사전에서 찾음. 정확히 일치 → 앞부분 일치 → 비슷한 철자 순서."""
        w = word.strip(PUNCT)
        if not w:
            return None
        if w in self.d:
            return {"word": w, "match": "정확히 일치", "key": w, "cands": self.d[w]}
        # 앞부분 일치: '고냉이가'는 사전에 없어도 '고냉이'가 있으면 인정 (뒤에 붙은 조사 등)
        for L in range(len(w) - 1, 1, -1):
            k = w[:L]
            if k in self.d and L / len(w) >= PREFIX_MIN_COVER:
                return {"word": w, "match": "앞부분 일치", "key": k, "cands": self.d[k]}
        # 비슷한 철자: 받아쓰기·음성인식 오류 대비
        if len(w) >= 3:
            m = difflib.get_close_matches(w, self.by_first.get(w[0], []), n=1, cutoff=FUZZY_CUTOFF)
            if m:
                return {"word": w, "match": "비슷한 철자", "key": m[0], "cands": self.d[m[0]]}
        return None


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
2. [사전 근거]에 없는 단어는 함부로 바꾸지 않는다. 방언으로 보이는데 뜻이 확실하지 않으면 원래 단어를 그대로 두고 unclear에 넣는다.
3. 뜻을 더하거나 빼지 않는다. 요약하거나 공손하게 고쳐 쓰지 말고, 단어만 표준어로 바꾼다.
4. '○○'는 가려진 이름이니 그대로 둔다.
5. 바꾼 단어는 하나도 빠짐없이 changes에 원래 어절(from)과 바꾼 표현(to)으로 적는다.
6. unclear의 question은 어르신이 이해하기 쉬운 짧은 존댓말 질문으로 쓴다."""

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
            lines.append(f"- {h['word']}{via} → {describe_cands(h['cands'])}")
    else:
        lines.append("- (사전에서 찾은 단어 없음)")
    lines.append("\n[비슷한 예문]")
    for i, e in enumerate(examples, 1):
        lines.append(f"{i}. 방언: {e['dialect']}\n   표준어: {e['standard']}")
    return "\n".join(lines)


_client = None


def call_llm(system, user):
    global _client
    from openai import OpenAI
    if _client is None:
        _client = OpenAI()          # OPENAI_API_KEY 환경변수를 자동으로 읽음
    resp = _client.responses.create(
        model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        text={"format": {"type": "json_schema", "name": "dialect_conversion",
                         "schema": SCHEMA, "strict": True}},
    )
    return json.loads(resp.output_text)


# =====================================================================
# ⑤ 근거 검사 (감시)
# =====================================================================
def check_evidence(text, llm_out, ddict):
    changes, ask, warnings = [], [], []
    for c in llm_out["changes"]:
        src, dst = c["from"].strip(), c["to"].strip()
        if src and src not in text:
            warnings.append(f"입력에 없는 단어를 바꿨다고 함: '{src}'")
        hit = ddict.lookup(src) if ddict else None
        matched = None
        if hit:
            for cand in hit["cands"]:
                if cand["standard"] in dst or dst in cand["standard"]:
                    matched = cand
                    break
        if matched:
            changes.append({"from": src, "to": dst, "type": "사전",
                            "evidence": f"{hit['key']}→{matched['standard']} "
                                        f"({describe_cands([matched])}, {hit['match']})"})
        else:
            changes.append({"from": src, "to": dst, "type": "추정",
                            "evidence": "사전 근거 없음" if not hit else
                                        f"사전 후보({describe_cands(hit['cands'])})와 다름"})
            ask.append(f"'{src}'을(를) '{dst}'(으)로 이해했는데 맞나요?")

    for u in llm_out["unclear"]:
        ask.append(u["question"] or f"'{u['word']}'은(는) 어떤 뜻인가요?")

    # changes에 적지 않고 몰래 바꾼 부분 찾기
    a, b = text.split(), llm_out["standard"].split()
    declared = " ".join(c["from"] for c in llm_out["changes"])
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b).get_opcodes():
        if op == "equal":
            continue
        hidden = [w for w in a[i1:i2] if w.strip(PUNCT) and w.strip(PUNCT) not in declared]
        if hidden:
            warnings.append(f"기록 없이 바뀐 단어: {', '.join(hidden)}  "
                            f"(구간: '{' '.join(a[i1:i2])}' → '{' '.join(b[j1:j2])}')")
    return changes, ask, warnings


# =====================================================================
# 전체 실행
# =====================================================================
_res = {}


def _load(use_rag):
    if use_rag and "dict" not in _res:
        _res["dict"] = DialectDict()
        if not INDEX_PATH.exists():
            sys.exit("예문 색인이 없어요. 먼저 실행하세요:  python3 normalize.py --build-index")
        _res["index"] = ExampleIndex()
    return _res.get("dict"), _res.get("index")


def normalize(text, use_rag=True):
    text = " ".join(text.split())
    ddict, index = _load(use_rag)
    if use_rag:
        hits = [h for w in text.split() if (h := ddict.lookup(w))]   # ① 어절 나누기 + ② 사전
        examples = index.search(text)                                 # ③ 예문
        llm_out = call_llm(RULES_RAG, build_prompt(text, hits, examples))  # ④ LLM
    else:
        hits, examples = [], []
        llm_out = call_llm(RULES_ZERO, f"[입력 문장]\n{text}")
    changes, ask, warnings = check_evidence(text, llm_out, ddict)     # ⑤ 감시
    return {
        "input": text,
        "standard": llm_out["standard"],
        "changes": changes,
        "ask_user": ask,
        "warnings": warnings,
        "dict_hits": [{"word": h["word"], "key": h["key"], "match": h["match"],
                       "cands": h["cands"]} for h in hits],
        "examples": examples,
        "mode": "rag" if use_rag else "zero_shot",
        "model": os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
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
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE / ".env")
    except ImportError:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?")
    ap.add_argument("--zero-shot", action="store_true", help="사전·예문 없이 LLM만 (비교용)")
    ap.add_argument("--build-index", action="store_true")
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--json", action="store_true", help="결과를 JSON 그대로 출력")
    args = ap.parse_args()

    if args.build_index:
        ExampleIndex.build()
        return
    if args.list_models:
        from openai import OpenAI
        for m in sorted(x.id for x in OpenAI().models.list()):
            print(m)
        return

    def run(t):
        r = normalize(t, use_rag=not args.zero_shot)
        print(json.dumps(r, ensure_ascii=False, indent=2)) if args.json else pretty(r)

    if args.text:
        run(args.text)
    else:
        print("방언 문장을 입력하세요. (끝내려면 빈 줄에서 Enter)")
        while (t := input("> ").strip()):
            run(t)


if __name__ == "__main__":
    main()
