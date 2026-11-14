"""
제주 방언 라벨 JSON 전처리 스크립트 (v2.2: 중·노년층 데이터 추가, 비식별화 오류 처리)

사용법:  python3 preprocess.py
        python3 preprocess.py --daily ./label --senior ./label_senior --output ./processed

입력
  daily  : AI Hub 한국어 방언 발화(제주도)          - 일상 대화, 10~60대 이상
  senior : AI Hub 중·노년층 한국어 방언(제주도 부분) - 50대 이상 (폴더가 없으면 건너뜀)

만들어지는 파일 (processed/ 폴더)
  pairs_train.jsonl  : 방언-표준어 문장 쌍 (source 칸에 daily / senior 표시)
  test.jsonl         : 일상대화 평가셋 (학습 쪽과 대화 파일이 겹치지 않음)
  test_senior.jsonl  : 어르신 평가셋 (학습 쪽과 화자가 겹치지 않음)
  dialect_dict.json  : 방언 어절 → 표준어 후보 + 출처별 등장 횟수 (학습 쪽으로만 만듦)
  stats.json         : 데이터 통계
"""
import argparse
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

# ---------- 설정값 (평가 돌리기 전에 확정하고, 결과 본 뒤엔 바꾸지 않기) ----------
TEST_RATIO = 0.1        # 평가용으로 떼어둘 비율 (daily: 대화 파일 기준, senior: 화자 기준)
TEST_SIZE = 500         # 평가셋 하나당 문장 수
MIN_DIALECT_EOJEOL = 2  # 평가 문장 조건: 방언 어절 최소 개수
DICT_MIN_COUNT = 3      # 사전에 남길 최소 등장 횟수 (전체 합계)
SENIOR_MIN_COUNT = 2    # 어르신 데이터에서 2번 이상 나온 쌍은 전체 합계가 3 미만이어도 남김
SEED = 42

TAG_RE = re.compile(r"\{[^}]*\}")
MASK_RE = re.compile(r"&[a-zA-Z]+\d*&|@\S+?\d+|#[^#\s]+#")  # &name1&, @이름1, #이름#
PUNCT = "?.,!~…\"'“”‘’"


def clean(text) -> str:
    text = TAG_RE.sub(" ", text or "")
    text = MASK_RE.sub("○○", text)
    return re.sub(r"\s+", " ", text).strip()


def is_bad(text: str) -> bool:
    return (not text) or "((" in text or "))" in text


def held_out(key: str) -> bool:
    """같은 key는 항상 같은 쪽으로 감 → 평가셋과 학습셋이 섞이지 않음"""
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % 1000 < TEST_RATIO * 1000


def eojeol_pair(d, s):
    d, s = clean(d).strip(PUNCT), clean(s).strip(PUNCT)
    if "○○" in d or "○○" in s:      # 가림 처리된 어절은 원래 단어를 알 수 없음 → 사전에서 제외
        return None
    return [d, s] if d and s and d != s else None


# ---------- 데이터 1: 일상 대화 ----------
def read_daily(doc, fp):
    file_id = doc.get("id", fp.stem)
    ages = {s.get("id"): s.get("age", "") for s in doc.get("speaker", [])}
    for u in doc.get("utterance", []):
        pairs = [p for e in u.get("eojeolList", []) if e.get("isDialect")
                 if (p := eojeol_pair(e.get("eojeol"), e.get("standard")))]
        yield {
            "source": "daily",
            "group": file_id,                       # 평가셋 분리 기준: 대화 파일
            "utt_id": u.get("id"),
            "age": ages.get(u.get("speaker_id"), ""),
            "dialect": clean(u.get("dialect_form") or u.get("form")),
            "standard": clean(u.get("standard_form")),
            "dialect_eojeols": pairs,
        }


# ---------- 데이터 2: 중·노년층 ----------
def age_group(doc):
    try:
        birth = int(float(doc["speaker"][0]["birthYear"]))
        year = int(str(doc["audio"]["recordDate"])[:4])
        return f"{(year - birth) // 10 * 10}대"
    except Exception:
        return ""


def read_senior(doc, fp):
    spk = (doc.get("speaker") or [{}])[0]
    if spk.get("residenceProvince") not in (None, "jj"):   # 제주 화자만
        return
    segs = [s for s in doc.get("transcription", {}).get("segments", [])
            if s.get("voiceType", "voice_speech") == "voice_speech" and s.get("dialect")]
    if not segs:
        return
    dialect_words, standard_words, pairs = [], [], []
    for s in segs:
        d = s["dialect"]
        st = s.get("standard") or d          # standard가 null이면 방언이 아님 → 그대로
        if MASK_RE.search(d):                # 비식별화로 방언 쪽만 가려진 어절 → 표준어 쪽도 똑같이 가림
            st = d                           #   (문장은 살리고 그 어절만 정보 포기, 사전에는 안 들어감)
        dialect_words.append(d)
        standard_words.append(st)
        if s.get("standard") and (p := eojeol_pair(d, s["standard"])):
            pairs.append(p)
    script = doc.get("script", {})
    yield {
        "source": "senior",
        "group": spk.get("speakerId", fp.stem),  # 평가셋 분리 기준: 화자 (한 화자가 여러 파일에 나옴)
        "utt_id": doc.get("fileName", fp.stem),
        "age": age_group(doc),
        "domain": script.get("domain", ""),
        "speech_type": script.get("speechType", ""),
        "dialect": clean(" ".join(dialect_words)),
        "standard": clean(" ".join(standard_words)),
        "dialect_eojeols": pairs,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", default="./label")
    ap.add_argument("--senior", default="./label_senior")
    ap.add_argument("--output", default="./processed")
    args = ap.parse_args()
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    train = []
    test_pool = {"daily": [], "senior": []}
    pair_counter = defaultdict(lambda: defaultdict(Counter))  # 방언 → 표준어 → 출처별 횟수
    stats = Counter()
    ages = {"daily": Counter(), "senior": Counter()}
    domains = Counter()

    for name, folder, reader in [("daily", args.daily, read_daily),
                                 ("senior", args.senior, read_senior)]:
        folder = Path(folder)
        if not folder.exists():
            print(f"[{name}] 폴더 없음 ({folder}) → 건너뜀")
            continue
        files = sorted(folder.rglob("*.json"))
        print(f"[{name}] JSON {len(files)}개 읽는 중...")
        for i, fp in enumerate(files, 1):
            try:
                doc = json.loads(fp.read_text(encoding="utf-8"))
            except Exception as e:
                stats[f"{name}_broken"] += 1
                print(f"  [건너뜀] {fp.name}: {e}")
                continue
            for rec in reader(doc, fp):
                stats[f"{name}_total"] += 1
                if is_bad(rec["dialect"]) or is_bad(rec["standard"]):
                    stats[f"{name}_dropped"] += 1
                    continue
                # 비식별화 오류: 방언 쪽만 가려지고 표준어는 남은 문장 (예: #이름# ↔ 그런데) → 제외
                if rec["dialect"].count("○○") != rec["standard"].count("○○"):
                    stats[f"{name}_mask_mismatch"] += 1
                    continue
                rec["n_dialect"] = len(rec["dialect_eojeols"])
                ages[name][rec["age"]] += 1
                if name == "senior":
                    domains[rec["domain"]] += 1
                if held_out(f"{name}:{rec['group']}"):
                    test_pool[name].append(rec)
                else:
                    train.append(rec)
                    for d, s in rec["dialect_eojeols"]:
                        pair_counter[d][s][name] += 1
            if i % 5000 == 0:
                print(f"  {i}/{len(files)} 완료")

    # 평가셋
    random.seed(SEED)
    tests = {}
    for name, pool in test_pool.items():
        cand = [r for r in pool if r["n_dialect"] >= MIN_DIALECT_EOJEOL]
        tests[name] = random.sample(cand, min(TEST_SIZE, len(cand)))

    # 사전
    dictionary = {}
    for d, std_map in pair_counter.items():
        total = sum(sum(c.values()) for c in std_map.values())
        senior = sum(c["senior"] for c in std_map.values())
        if total >= DICT_MIN_COUNT or senior >= SENIOR_MIN_COUNT:
            ranked = sorted(std_map.items(), key=lambda kv: -sum(kv[1].values()))[:3]
            dictionary[d] = [{"standard": s, "count": sum(c.values()), "sources": dict(c)}
                             for s, c in ranked]

    def dump_jsonl(path, rows):
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    dump_jsonl(out_dir / "pairs_train.jsonl", train)
    dump_jsonl(out_dir / "test.jsonl", tests["daily"])
    dump_jsonl(out_dir / "test_senior.jsonl", tests["senior"])
    (out_dir / "dialect_dict.json").write_text(
        json.dumps(dictionary, ensure_ascii=False, indent=1), encoding="utf-8")

    src_count = Counter(r["source"] for r in train)
    only_senior = sum(1 for v in dictionary.values()
                      if all(set(x["sources"]) == {"senior"} for x in v))
    summary = {
        "train_pairs": dict(src_count),
        "test_daily": len(tests["daily"]),
        "test_senior": len(tests["senior"]),
        "dict_entries": len(dictionary),
        "dict_entries_only_from_senior": only_senior,
        "age_daily": dict(ages["daily"].most_common()),
        "age_senior": dict(ages["senior"].most_common()),
        "senior_domains": dict(domains.most_common()),
        **{k: v for k, v in stats.items()},
    }
    (out_dir / "stats.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== 결과 =====")
    for k, v in summary.items():
        print(f"{k}: {v}")
    print("\n===== 어르신 데이터 예시 3개 (방언 → 표준어) =====")
    for r in [r for r in train if r["source"] == "senior" and r["n_dialect"] >= 2][:3]:
        print(f"- [{r['domain']}, {r['age']}] {r['dialect']}\n  → {r['standard']}\n  어절: {r['dialect_eojeols']}")
    print(f"\n저장 위치: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
