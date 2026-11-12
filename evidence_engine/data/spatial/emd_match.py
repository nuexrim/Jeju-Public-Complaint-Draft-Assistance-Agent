"""읍면동명·주소 문자열 → emd_code (행정동 8자리).

경계 폴리곤과 법정동↔행정동 연계표가 아직 없으므로 이름으로만 맞춘다.
- name  : 행정동명과 정확히 같음 (예: 구좌읍, 노형동)
- alias : 법정동 표기의 한글 숫자만 바꾸면 행정동명이 됨 (예: 이도이동 → 이도2동)
이 두 경우가 아니면 None을 돌려준다. 이름이 다른 법정동(예: 도남동 → 이도2동)은
연계표를 확보한 뒤 채운다. 틀린 코드를 붙이느니 비워 둔다.
"""
from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

DEFAULT_TABLE = Path(__file__).resolve().parent / "emd_codes.csv"
_NUMERAL = {"1": "일", "2": "이", "3": "삼"}
_TOKEN = re.compile(r"[가-힣0-9]+(?:읍|면|동)")


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


def match_emd(text: str | None, path: Path = DEFAULT_TABLE) -> tuple[str | None, str | None]:
    """(emd_code, method). 문자열 안에서 처음 맞는 읍면동 표기를 쓴다."""
    if not text:
        return None, None
    table = _lookup(Path(path))
    for token in _TOKEN.findall(text):
        if token in table:
            return table[token]
    return None, None
