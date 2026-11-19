"""최종 제출 ZIP 만들기: 코드(저장소 파일) + 저장소에 넣지 않는 실행 자료.

    python make_submission.py                       # dist/제주민원도우미_제출_<날짜>.zip
    python make_submission.py --with-claude-key     # 평가용 Claude 키를 ZIP 안 evidence_engine/.env 에 넣는다

넣는 것
  코드        : git 이 관리하는 파일 전부(지금 작업 폴더의 내용)
  실행 자료   : 근거 DB 스냅샷, 데이터셋 카드, 법정동 연계정보, 제주어 사전·예문 색인(있을 때)
               → 원자료·가공 자료라 GitHub 에는 넣지 않고 심사·센터 운영용 ZIP 에만 넣는다(대회 안내).
  평가용 키   : --with-claude-key 를 줄 때만. 키는 환경 변수 EVAL_ANTHROPIC_API_KEY 에서 읽는다(화면에 찍지 않음).
               사용 한도를 건 평가용 키를 따로 만들어 쓰고, 평가가 끝나면 지운다. 데이터 API 키는 넣지 않는다.
넣지 않는 것: 개발용 .env, 원본(raw), 만들어진 DB(센터 PC 에서 스냅샷으로 다시 만든다), 기록·캐시, 설계서 docx

빠진 실행 자료가 있으면 ZIP 안 '제출물_구성.txt' 에 무엇이 빠졌고 어떻게 가져오는지 적는다.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / "evidence_engine"
SNAPSHOT = "2026-11-17"            # evidence_engine/config.yaml 의 snapshot, 설치.bat 의 SNAPSHOT 과 같아야 한다

# (ZIP 안 경로, 찾을 위치, 꼭 필요한가, 없을 때 가져오는 법)
EXTRAS = [
    (f"evidence_engine/data/snapshot/{SNAPSHOT}", ENGINE / "data" / "snapshot" / SNAPSHOT, True,
     f"근거 DB 스냅샷. 데이터를 적재한 PC에서 `cd evidence_engine` → `python data/snapshot.py --export {SNAPSHOT}` 로 만든다. "
     "원본부터 만들려면 README '데이터 준비' 1~10단계."),
    ("evidence_engine/catalog/cards.parquet", ENGINE / "catalog" / "cards.parquet", True,
     "데이터셋 카드(카탈로그 검색). `cd evidence_engine` → `python catalog/build_cards.py` (데이터허브 목록과 "
     "빅데이터센터 명세서 필요)."),
    ("evidence_engine/data/ingest/raw/emd_link", ENGINE / "data" / "ingest" / "raw" / "emd_link", False,
     "법정동 연계정보 CSV(리 이름 → 읍면동). 공공데이터포털 https://www.data.go.kr/data/15136368/fileData.do 에서 받아 "
     "evidence_engine/data/ingest/raw/emd_link/ 에 넣는다. 없으면 '종달리'처럼 리 이름만 말했을 때 읍면동을 되묻는다."),
    ("dialect_normalizer/processed", ROOT / "dialect_normalizer" / "processed", False,
     "제주어 사전·예문 색인. AI Hub '한국어 방언 발화(제주도)' 라벨 데이터를 신청해 받은 뒤 dialect_normalizer 에서 "
     "`python preprocess.py` → `python normalize.py --build-index`. 없으면 제주어를 표준어로 바꾸는 단계를 건너뛰고 "
     "(Claude 구조화·문장 다듬기는 그대로) 동작한다."),
]
# 연계정보 폴더에서는 연계표 CSV 만 넣는다(인구 API 받은 파일 등은 DB 에 이미 들어 있음)
LINK_KEEP = "연계정보"
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / p for p in out.decode("utf-8").split("\0") if p and (ROOT / p).is_file()]


def _arcname(path: str) -> str:
    return unicodedata.normalize("NFC", path.replace(os.sep, "/"))


def _add_tree(z: zipfile.ZipFile, src: Path, arc: str) -> int:
    n = 0
    if src.is_file():
        z.write(src, _arcname(arc))
        return 1
    for p in sorted(src.rglob("*")):
        if not p.is_file() or SKIP_DIRS & set(p.parts):
            continue
        if arc.endswith("emd_link") and LINK_KEEP not in unicodedata.normalize("NFC", p.name):
            continue
        z.write(p, _arcname(f"{arc}/{p.relative_to(src).as_posix()}"))
        n += 1
    return n


def build(out: Path, with_key: bool) -> tuple[list[str], list[str]]:
    included, missing = [], []
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:      # 파일 이름은 UTF-8(Windows 에서 한글이 깨지지 않게)
        for p in tracked_files():
            z.write(p, _arcname(p.relative_to(ROOT).as_posix()))
        for arc, src, required, how in EXTRAS:
            if src.exists() and (n := _add_tree(z, src, arc)):
                included.append(f"{arc} ({n}개 파일)")
            else:
                missing.append(f"[{'꼭 필요' if required else '있으면 좋음'}] {arc}\n    가져오는 법: {how}")
        if with_key:
            key = os.environ.get("EVAL_ANTHROPIC_API_KEY", "").strip()
            if not key:
                raise SystemExit("EVAL_ANTHROPIC_API_KEY 가 비어 있다. 평가용 키를 환경 변수로 준 뒤 다시 실행한다.")
            z.writestr("evidence_engine/.env", f"# 평가용 Claude 키(사용 한도 설정됨). 평가가 끝나면 이 키를 지운다.\n"
                                               f"ANTHROPIC_API_KEY={key}\n")
            included.append("evidence_engine/.env (평가용 Claude 키)")
        else:
            missing.append("[있으면 좋음] evidence_engine/.env (평가용 Claude 키)\n    가져오는 법: '설치.bat'가 만든 "
                           "evidence_engine\\.env 의 ANTHROPIC_API_KEY= 뒤에 평가용 키를 넣고, '실행.bat'의 "
                           "ALLOW_PAID_CALLS=0 을 1 로 바꾼다. 없으면 Claude 없이 규칙으로만 동작한다.")
        z.writestr("제출물_구성.txt", manifest_text(included, missing))
    return included, missing


def manifest_text(included: list[str], missing: list[str]) -> str:
    lines = [f"제주 민원 도우미 제출물 구성 ({date.today().isoformat()})", "",
             "설치·실행: 실행_매뉴얼.md 를 보세요. (요약: 설치.bat → evidence_engine\\.env 에 키 → 실행.bat)", "",
             "[들어 있는 실행 자료] (원자료·가공 자료라 GitHub 에는 없고 이 ZIP 에만 있습니다. 심사·센터 운영 외 재배포 금지)"]
    lines += [f"  - {x}" for x in included] or ["  - 없음"]
    lines += ["", "[빠진 것과 가져오는 법]"]
    lines += [f"  - {x}" for x in missing] or ["  - 없음"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "dist" / f"제주민원도우미_제출_{date.today().isoformat()}.zip")
    ap.add_argument("--with-claude-key", action="store_true", help="평가용 키(EVAL_ANTHROPIC_API_KEY)를 ZIP 에 넣는다")
    args = ap.parse_args(argv)
    included, missing = build(args.out, args.with_claude_key)
    size = args.out.stat().st_size / 1e6
    print(f"저장: {args.out} ({size:.1f}MB)")
    for x in included:
        print(f"  넣음: {x}")
    for x in missing:
        print(f"  빠짐: {x.splitlines()[0]}")
    return 1 if any(x.startswith("[꼭 필요]") for x in missing) else 0


if __name__ == "__main__":
    sys.exit(main())
