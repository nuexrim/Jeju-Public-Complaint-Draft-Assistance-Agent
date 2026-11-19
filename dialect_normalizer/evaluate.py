"""
A/B 평가: A(LLM만, zero-shot) vs B(사전+예문+LLM, RAG)

사용법
  python evaluate.py --limit 20                 먼저 20문장씩만 (비용·오류 확인용)
  python evaluate.py                            전체: 평가셋 2개 × 방법 2개 = 2,000번 호출
  python evaluate.py --sets senior --modes rag  일부만
  python evaluate.py --report                   API 호출 없이 저장된 결과로 점수만 다시 계산

특징
  - 결과를 results/<모델 이름>/ 폴더에 한 줄씩 저장 → 중간에 끊겨도 다시 실행하면 이어서 진행
  - 같은 모델·같은 문장으로 A와 B를 비교하므로, 점수 차이 = 우리 데이터(사전·예문)의 효과

지표 (계산식·근거 논문·적용 규칙은 README "평가 지표" 참고)
  [어절 단위]  입력 어절과 출력 어절을 편집 거리로 정렬한 뒤 TP/FN/FP/TN으로 분류
    ERR (메인)     = (TP − FP) / (TP + FN)          van der Goot (2019), MultiLexNorm (2021)
    재현율         = TP / (TP + FN)                   바꿔야 할 방언 어절 중 맞게 바꾼 비율
    FPR            = FP / (FP + TN)                   원래 표준어인 어절을 잘못 바꾼 비율
    단어 정확도    = (TP + TN) / 전체 어절            Kuparinen 외 (2023)
    LAI 정확도     = (FP + TN) / 전체 어절            아무것도 안 바꿨을 때의 정확도 (기준선)
  [문장 단위]
    CER(공백 포함), CER(공백 제외) = 글자 편집 거리 합 / 정답 글자 수 합
    BLEU, chrF     = sacrebleu 표준 구현 (corpus 단위)
    문장 완전 일치율 = 띄어쓰기·문장부호를 빼고 정답 문장과 같은 비율 (보조)
  [시스템 동작]  추정 변경/문장, 질문/문장
"""
import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = Path(__file__).resolve().parent
PROC = BASE / "processed"
RESULTS = BASE / "results"
TEST_FILES = {"daily": PROC / "test.jsonl", "senior": PROC / "test_senior.jsonl"}
MODE_NAME = {"zero_shot": "A: LLM만", "rag": "B: 사전+예문+LLM"}
SET_NAME = {"daily": "일상대화", "senior": "중고령자"}
PUNCT = "?.,!~…\"'“”‘’()[]"
WORKERS = 4          # 동시에 보내는 요청 수 (요청 한도 에러가 나면 줄이기)


def squash(t):
    """띄어쓰기·문장부호 제거 (비교용)"""
    return re.sub(r"[\s" + re.escape(PUNCT) + r"]", "", t or "")


def bare(t):
    return [w.strip(PUNCT) for w in (t or "").split() if w.strip(PUNCT)]


def load_done(path):
    done = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if "error" not in r:
                done[r["utt_id"]] = r
    return done


def run_one(rec, mode):
    from normalize import normalize
    for attempt in range(3):
        try:
            r = normalize(rec["dialect"], use_rag=(mode == "rag"))
            return {"utt_id": rec["utt_id"], "dialect": rec["dialect"], "gold": rec["standard"],
                    "gold_pairs": rec["dialect_eojeols"], "pred": r["standard"],
                    "n_guess": sum(c["type"] == "추정" for c in r["changes"]),
                    "n_ask": len(r["ask_user"]), "n_warn": len(r["warnings"]),
                    "age": rec.get("age", "")}
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))
    return {"utt_id": rec["utt_id"], "error": err}


def run(set_name, mode, limit):
    rows = [json.loads(l) for l in TEST_FILES[set_name].read_text(encoding="utf-8").splitlines()]
    if limit:
        rows = rows[:limit]
    out_path = RESULTS / f"pred_{set_name}_{mode}.jsonl"
    done = load_done(out_path)
    todo = [r for r in rows if r["utt_id"] not in done]
    print(f"[{SET_NAME[set_name]} / {MODE_NAME[mode]}] 전체 {len(rows)} · 완료 {len(rows) - len(todo)} · 남음 {len(todo)}")
    if not todo:
        return
    errors = 0
    with ThreadPoolExecutor(WORKERS) as ex, open(out_path, "a", encoding="utf-8") as f:
        futs = [ex.submit(run_one, r, mode) for r in todo]
        for i, fu in enumerate(as_completed(futs), 1):
            res = fu.result()
            if "error" in res:
                errors += 1
                print(f"  ✗ {res['utt_id']}: {res['error'][:120]}")
            else:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
                f.flush()
            if i % 20 == 0:
                print(f"  {i}/{len(todo)}")
    if errors:
        print(f"  오류 {errors}건 → 다시 실행하면 그 문장만 재시도해요")


def _gold_labels(dialect, gold_pairs):
    """입력 어절마다 정답 라벨: (어절, 정답 표준형 또는 None).  None = 작업자가 안 바꾼 어절.
       정답 쌍의 방언 쪽이 띄어쓰기를 포함하면(예: '바빠 노난', 구 단위 주석) 연속된 입력 어절을 하나로 묶는다."""
    toks = bare(dialect)
    pairs = [(bare(d), s) for d, s in gold_pairs if bare(d)]
    labels, i, k = [], 0, 0
    while i < len(toks):
        if k < len(pairs):
            d_toks, s = pairs[k]
            n = len(d_toks)
            if toks[i:i + n] == d_toks:
                labels.append((" ".join(d_toks), s))
                i += n
                k += 1
                continue
        labels.append((toks[i], None))
        i += 1
    return labels, len(pairs) - k          # 두 번째 값: 입력에서 찾지 못한 정답 쌍 수


def align_counts(dialect, gold_pairs, pred):
    """입력·출력 어절을 편집 거리(difflib)로 정렬하고 MultiLexNorm 정의대로 TP/FN/FP/TN을 센다.
       - 같은 수의 어절끼리 바뀐 구간은 1:1로, 수가 다르면(쪼개짐·합쳐짐) 구간 전체로 비교
       - 비교는 띄어쓰기·문장부호를 무시(squash)
       - 방언 어절을 틀린 표준형으로 바꾼 경우는 FN만 (FP 아님)
       - 가명 어절 ○○ 은 제외"""
    import difflib
    labels, unmatched = _gold_labels(dialect, gold_pairs)
    a = [t for t, _ in labels]
    b = bare(pred)
    c = {"TP": 0, "FN": 0, "FP": 0, "TN": 0, "unmatched_gold": unmatched}

    def judge(i, out_text):
        tok, gold = labels[i]
        if tok == "○○":
            return
        out = squash(out_text)
        if gold is not None:                                   # 작업자가 바꾼 어절
            c["TP" if squash(gold) and squash(gold) in out else "FN"] += 1
        else:                                                  # 작업자가 안 바꾼 어절
            c["TN" if squash(tok) in out else "FP"] += 1

    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "insert":
            continue                                           # 새로 생긴 어절은 문장 단위 지표(CER)에서 반영
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                judge(i1 + k, b[j1 + k])
        else:                                                  # 쪼개짐·합쳐짐·삭제
            span = " ".join(b[j1:j2])
            for i in range(i1, i2):
                judge(i, span)
    return c


def edit_distance(x, y):
    prev = list(range(len(y) + 1))
    for i, cx in enumerate(x, 1):
        cur = [i]
        for j, cy in enumerate(y, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (cx != cy)))
        prev = cur
    return prev[-1]


def score(preds):
    tot = {"TP": 0, "FN": 0, "FP": 0, "TN": 0, "unmatched_gold": 0}
    exact = 0
    cer = {"with": [0, 0], "without": [0, 0]}                  # [편집 거리 합, 정답 글자 수 합]
    for p in preds:
        for k, v in align_counts(p["dialect"], p["gold_pairs"], p["pred"]).items():
            tot[k] += v
        exact += squash(p["pred"]) == squash(p["gold"])
        g, h = " ".join(p["gold"].split()), " ".join(p["pred"].split())
        cer["with"][0] += edit_distance(h, g)
        cer["with"][1] += len(g)
        g2, h2 = re.sub(r"\s", "", g), re.sub(r"\s", "", h)
        cer["without"][0] += edit_distance(h2, g2)
        cer["without"][1] += len(g2)

    TP, FN, FP, TN = tot["TP"], tot["FN"], tot["FP"], tot["TN"]
    n_norm, n_keep = TP + FN, FP + TN
    n = len(preds)
    pct = lambda x, y: round(100 * x / y, 1) if y else None
    s = {
        "문장 수": n,
        "ERR": pct(TP - FP, n_norm),
        "재현율": pct(TP, n_norm),
        "FPR": pct(FP, n_keep),
        "단어 정확도": pct(TP + TN, n_norm + n_keep),
        "LAI 정확도": pct(n_keep, n_norm + n_keep),
        "CER(공백 포함)": pct(*cer["with"]),
        "CER(공백 제외)": pct(*cer["without"]),
        "BLEU": None,
        "chrF": None,
        "문장 완전 일치율": pct(exact, n),
        "추정 변경/문장": round(sum(p["n_guess"] for p in preds) / max(n, 1), 2),
        "질문/문장": round(sum(p["n_ask"] for p in preds) / max(n, 1), 2),
        "_counts": tot,
    }
    try:
        import sacrebleu
        hyps, refs = [p["pred"] for p in preds], [[p["gold"] for p in preds]]
        s["BLEU"] = round(sacrebleu.corpus_bleu(hyps, refs).score, 1)
        s["chrF"] = round(sacrebleu.corpus_chrf(hyps, refs).score, 1)
    except ImportError:
        pass
    return s


def write_examples(set_name, a, b, k=15):
    """A는 틀리고 B는 맞힌 문장 / 그 반대 → 기획서·발표용 사례 (문장별 TP − FP 기준)"""
    def gain(p):
        c = align_counts(p["dialect"], p["gold_pairs"], p["pred"])
        return c["TP"] - c["FP"]
    common = [u for u in a if u in b]
    diff = {u: gain(b[u]) - gain(a[u]) for u in common}
    better = sorted(common, key=lambda u: -diff[u])[:k]
    worse = sorted(common, key=lambda u: diff[u])[:k]
    lines = [f"# {SET_NAME[set_name]} 평가셋: A(LLM만) vs B(우리 방식) 사례\n"]
    for title, ids, sign in [("B가 더 잘한 문장", better, 1), ("A가 더 잘한 문장 (개선 참고용)", worse, -1)]:
        lines.append(f"\n## {title}\n")
        for u in ids:
            if diff[u] * sign <= 0:
                continue
            lines += [f"- **방언**: {a[u]['dialect']}", f"  - 정답: {a[u]['gold']}",
                      f"  - A: {a[u]['pred']}", f"  - B: {b[u]['pred']}",
                      f"  - 방언 어절: {', '.join(f'{d}→{s}' for d, s in a[u]['gold_pairs'])}\n"]
    path = RESULTS / f"examples_{set_name}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def report(sets, modes):
    summary = {}
    for st in sets:
        preds = {m: load_done(RESULTS / f"pred_{st}_{m}.jsonl") for m in modes}
        for m in modes:
            if preds[m]:
                summary[f"{st}/{m}"] = score(list(preds[m].values()))
        if all(preds.get(m) for m in ("zero_shot", "rag")):
            print(f"사례 저장: {write_examples(st, preds['zero_shot'], preds['rag'])}")
    if not summary:
        print("저장된 결과가 없어요. 먼저 평가를 실행하세요.")
        return
    (RESULTS / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    import llm
    groups = [("어절 단위 (MultiLexNorm 정의, 편집 거리 정렬)", ["ERR", "재현율", "FPR", "단어 정확도", "LAI 정확도"]),
              ("문장 단위", ["CER(공백 포함)", "CER(공백 제외)", "BLEU", "chrF", "문장 완전 일치율"]),
              ("시스템 동작", ["추정 변경/문장", "질문/문장"])]
    print(f"\n===== 평가 결과 (모델: {llm.model_name()}) =====")
    print("ERR·재현율·정확도·BLEU·chrF는 높을수록, FPR·CER은 낮을수록 좋음 (단위: %)")
    for title, keys in groups:
        print(f"\n[{title}]")
        print("| 평가셋 | 방법 | 문장 수 | " + " | ".join(keys) + " |")
        print("|---|---|---|" + "---|" * len(keys))
        for k, v in summary.items():
            st, m = k.split("/")
            print(f"| {SET_NAME[st]} | {MODE_NAME[m]} | {v['문장 수']} | "
                  + " | ".join(str(v[x]) for x in keys) + " |")
    c = {k: v["_counts"] for k, v in summary.items()}
    print("\n[어절 분류 개수 TP/FN/FP/TN, 입력에서 못 찾은 정답 쌍]")
    for k, v in c.items():
        print(f"  {k}: TP {v['TP']} · FN {v['FN']} · FP {v['FP']} · TN {v['TN']} · 미매칭 {v['unmatched_gold']}")
    print(f"\n저장: {RESULTS / 'summary.json'}")


def main():
    from win_console import safe_output
    safe_output()
    import llm
    llm.load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", nargs="+", default=["daily", "senior"], choices=["daily", "senior"])
    ap.add_argument("--modes", nargs="+", default=["zero_shot", "rag"], choices=["zero_shot", "rag"])
    ap.add_argument("--limit", type=int, default=0, help="평가셋마다 앞에서 N문장만")
    ap.add_argument("--report", action="store_true", help="API 호출 없이 점수만 다시 계산")
    args = ap.parse_args()
    global RESULTS
    RESULTS = BASE / "results" / llm.model_name()     # 모델별로 폴더를 나눠 결과가 섞이지 않게
    RESULTS.mkdir(parents=True, exist_ok=True)

    if not args.report:
        import normalize
        normalize._load(True)          # 사전·색인을 미리 한 번만 불러옴
        for st in args.sets:
            for m in args.modes:
                run(st, m, args.limit)
    report(args.sets, args.modes)


if __name__ == "__main__":
    main()
