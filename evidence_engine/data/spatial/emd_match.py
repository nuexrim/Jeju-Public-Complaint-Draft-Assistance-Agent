"""읍면동명·주소 문자열 → emd_code (행정동 8자리).

순서대로 시도하고, 처음 맞는 것을 쓴다.
- name  : 행정동명과 정확히 같음 (예: 구좌읍, 노형동)
- alias : 법정동 표기의 숫자만 한글로 바꾸면 행정동명이 됨 (예: 이도이동 → 이도2동)
- legal : 국가데이터처 「법정동 연계정보」에서 그 법정동이 속한 행정동이 하나뿐임 (예: 도남동 → 이도2동)
         한 법정동이 여러 행정동에 걸치면(N:M) 틀린 코드를 붙이지 않도록 비워 둔다.
연계표 파일(raw/emd_link/*법정동 연계정보*.csv)이 없으면 legal 단계는 건너뛴다.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

DEFAULT_TABLE = Path(__file__).resolve().parent / "emd_codes.csv"
LINK_DIR = Path(__file__).resolve().parent.parent / "ingest" / "raw" / "emd_link"
_NUMERAL = {"1": "일", "2": "이", "3": "삼"}
_TOKEN = re.compile(r"[가-힣0-9]+(?:읍|면|동)")
_LEGAL_TOKEN = re.compile(r"[가-힣0-9]+(?:동|리)")
# 연계표 공식 컬럼명 (data.go.kr 15136368 컬럼 정의서)
LINK_COLUMNS = ("시도명", "시군구명", "행정동명", "법정동명", "행정구역코드", "행정동코드", "법정동코드")


@lru_cache(maxsize=None)
def _lookup(path: Path) -> dict[str, tuple[str, str]]:
    table: dict[str, tuple[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    names = [r["emd_name"] for r in rows]
    assert len(names) == len(set(names)), "행정동명이 중복되어 이름만으로 구분할 수 없다"
    for r in rows:
        table[r["emd_name"]] = (r["emd_code"], "name")
    for r in rows:
        alias = re.sub(r"([123])(?=동$)", lambda m: _NUMERAL[m.group(1)], r["emd_name"])
        if alias != r["emd_name"]:
            table.setdefault(alias, (r["emd_code"], "alias"))
    return table


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8/cp949", raw[:0], 0, 0, f"{path.name} 인코딩을 알 수 없다")


def build_legal_lookup(link_csv: Path, emd_table: Path = DEFAULT_TABLE) -> dict:
    """{"by_name": {법정동명: code|None}, "by_city": {(시군구, 법정동명): code|None}, "code_column": str}

    None은 여러 행정동에 걸친 법정동(모호)이다. 행정동 코드는 현행 43개(emd_codes.csv)에 있는 것만 쓴다.
    코드 체계는 '행정구역코드'와 '행정동코드' 중 앞 8자리가 현행 43개와 더 많이 맞는 쪽을 고른다.
    """
    with emd_table.open(encoding="utf-8", newline="") as f:
        valid = {r["emd_code"] for r in csv.DictReader(f)}
    rows = [r for r in csv.DictReader(_read_text(link_csv).splitlines()) if (r.get("시도명") or "").startswith("제주")]
    missing = [c for c in LINK_COLUMNS if rows and c not in rows[0]]
    if not rows or missing:
        raise KeyError(f"연계표에 제주 행이 없거나 컬럼이 다르다: 없는 컬럼 {missing}")

    def coverage(col: str) -> int:
        return len({(r[col] or "").strip()[:8] for r in rows} & valid)

    code_col = max(("행정구역코드", "행정동코드"), key=coverage)
    if coverage(code_col) < len(valid) - 3:
        raise ValueError(f"연계표 코드가 현행 행정동 {len(valid)}개 중 {coverage(code_col)}개만 맞는다")

    by_name: dict[str, set[str]] = {}
    by_city: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        code = (r[code_col] or "").strip()[:8]
        if code not in valid:
            continue
        legal = (r["법정동명"] or "").strip().split(" ")[-1]  # "한림읍 한림리" 형태면 마지막 토막
        if not legal:
            continue
        by_name.setdefault(legal, set()).add(code)
        by_city.setdefault(((r["시군구명"] or "").strip(), legal), set()).add(code)
    one = lambda s: next(iter(s)) if len(s) == 1 else None  # noqa: E731
    return {
        "by_name": {k: one(v) for k, v in by_name.items()},
        "by_city": {k: one(v) for k, v in by_city.items()},
        "code_column": code_col,
    }


@lru_cache(maxsize=None)
def _legal(link_dir: Path, emd_table: Path):
    # macOS에서 받은 파일명은 한글이 NFD로 저장될 수 있어 NFC로 맞춰 비교한다.
    files = sorted(
        f for f in (link_dir.glob("*.csv") if link_dir.exists() else [])
        if "법정동 연계정보" in unicodedata.normalize("NFC", f.name)
    )
    return build_legal_lookup(files[-1], emd_table) if files else None


def match_emd(text: str | None, path: Path = DEFAULT_TABLE, link_dir: Path | None = LINK_DIR) -> tuple[str | None, str | None]:
    """(emd_code, method). 문자열 안에서 처음 맞는 읍면동 표기를 쓴다."""
    if not text:
        return None, None
    table = _lookup(Path(path))
    candidates = [text]
    if len(text) <= 10 and " " in text:
        candidates.append(re.sub(r"\s+", "", text))  # "연 동"처럼 이름 안에 공백이 들어간 표기
    for candidate in candidates:
        for token in _TOKEN.findall(candidate):
            if token in table:
                return table[token]

    legal = _legal(Path(link_dir), Path(path)) if link_dir else None
    if legal:
        city = "서귀포시" if "서귀포시" in text else ("제주시" if "제주시" in text else None)
        for token in _LEGAL_TOKEN.findall(text):
            code = legal["by_city"].get((city, token)) if city else legal["by_name"].get(token)
            if code:
                return code, "legal"
    return None, None
