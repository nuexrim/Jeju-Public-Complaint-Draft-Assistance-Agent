"""
A/B 평가: A(LLM만, zero-shot) vs B(사전+예문+LLM, RAG)

사용법
  python3 evaluate.py --limit 20                 먼저 20문장씩만 (비용·오류 확인용)
  python3 evaluate.py                            전체: 평가셋 2개 × 방법 2개 = 2,000번 호출
  python3 evaluate.py --sets senior --modes rag  일부만
  python3 evaluate.py --report                   API 호출 없이 저장된 결과로 점수만 다시 계산

특징
  - 결과를 results/ 폴더에 한 줄씩 저장 → 중간에 끊겨도 다시 실행하면 이어서 진행
  - 같은 모델·같은 문장으로 A와 B를 비교하므로, 점수 차이 = 우리 데이터(사전·예문)의 효과

지표
  방언 어절 정답률 : 정답 데이터에서 방언으로 표시된 어절의 표준어가 결과에 들어 있는 비율 (메인 지표)
  과잉 수정률      : 원래 방언이 아니었던 어절을 바꿔버린 비율 (낮을수록 좋음)
  문장 완전 일치율  : 띄어쓰기·문장부호를 빼고 정답 문장과 똑같은 비율
  chrF            : 정답 문장과 글자 조각이 얼마나 겹치는지 (0~100, sacrebleu 설치 시)
  추정 변경 / 질문  : 문장당 사전 근거 없는 변경 수, 사용자 확인 질문 수
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
SET_NAME = {"daily": "일상대화", "senior": "어르신"}
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


def score(preds):
    n_pairs = hit = 0
    n_plain = over = 0
    exact = 0
    for p in preds:
        pred_sq = squash(p["pred"])
        pred_words = bare(p["pred"])
        dialect_words = {d for d, _ in p["gold_pairs"]}
        for d, s in p["gold_pairs"]:
            n_pairs += 1
            hit += squash(s) in pred_sq
        for w in bare(p["dialect"]):
            if w in dialect_words or w == "○○":
                continue
            n_plain += 1
            over += w not in pred_words
        exact += pred_sq == squash(p["gold"])
    n = len(preds)
    s = {
        "문장 수": n,
        "방언 어절 정답률": round(100 * hit / max(n_pairs, 1), 1),
        "과잉 수정률": round(100 * over / max(n_plain, 1), 1),
        "문장 완전 일치율": round(100 * exact / max(n, 1), 1),
        "추정 변경/문장": round(sum(p["n_guess"] for p in preds) / max(n, 1), 2),
        "질문/문장": round(sum(p["n_ask"] for p in preds) / max(n, 1), 2),
    }
    try:
        import sacrebleu
        s["chrF"] = round(sacrebleu.corpus_chrf([p["pred"] for p in preds],
                                                [[p["gold"] for p in preds]]).score, 1)
    except ImportError:
        s["chrF"] = None
    return s


def write_examples(set_name, a, b, k=15):
    """A는 틀리고 B는 맞힌 문장 / 그 반대 → 기획서·발표용 사례"""
    def hits(p):
        return sum(squash(s) in squash(p["pred"]) for _, s in p["gold_pairs"])
    common = [u for u in a if u in b]
    better = sorted(common, key=lambda u: hits(a[u]) - hits(b[u]))[:k]
    worse = sorted(common, key=lambda u: hits(b[u]) - hits(a[u]))[:k]
    lines = [f"# {SET_NAME[set_name]} 평가셋: A(LLM만) vs B(우리 방식) 사례\n"]
    for title, ids in [("B가 더 잘한 문장", better), ("A가 더 잘한 문장 (개선 참고용)", worse)]:
        lines.append(f"\n## {title}\n")
        for u in ids:
            if hits(a[u]) == hits(b[u]):
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
    keys = list(next(iter(summary.values())).keys())
    print(f"\n===== 평가 결과 (모델: {os.getenv('OPENAI_MODEL', '?')}) =====")
    print("| 평가셋 | 방법 | " + " | ".join(keys) + " |")
    print("|---|---|" + "---|" * len(keys))
    for k, v in summary.items():
        st, m = k.split("/")
        print(f"| {SET_NAME[st]} | {MODE_NAME[m]} | " + " | ".join(str(v[x]) for x in keys) + " |")
    print(f"\n저장: {RESULTS / 'summary.json'}")


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE / ".env")
    except ImportError:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", nargs="+", default=["daily", "senior"], choices=["daily", "senior"])
    ap.add_argument("--modes", nargs="+", default=["zero_shot", "rag"], choices=["zero_shot", "rag"])
    ap.add_argument("--limit", type=int, default=0, help="평가셋마다 앞에서 N문장만")
    ap.add_argument("--report", action="store_true", help="API 호출 없이 점수만 다시 계산")
    args = ap.parse_args()
    RESULTS.mkdir(exist_ok=True)

    if not args.report:
        import normalize
        normalize._load(True)          # 사전·색인을 미리 한 번만 불러옴
        for st in args.sets:
            for m in args.modes:
                run(st, m, args.limit)
    report(args.sets, args.modes)


if __name__ == "__main__":
    main()
