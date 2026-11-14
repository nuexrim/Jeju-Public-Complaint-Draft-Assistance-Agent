"""카탈로그 카드 검색 인덱스 (BM25).

    python catalog/index.py "버스 정류소 부족"      # 상위 10개 확인

토큰: 한글·영숫자 어절 전체 + 한글 음절 2-gram. 형태소 분석기 없이 조사가 붙은 말도 걸리게 한다
("정류소가" → 정류, 류소, 소가 + 어절). 제목은 3배, 분류·컬럼 이름은 2배 가중한다.
인덱스는 카드 1,330개 기준 1초 안에 메모리에서 만들므로 파일로 따로 저장하지 않는다.

bge-m3 밀집 임베딩은 아직 붙이지 않았다(모델 약 2GB 내려받기 필요). 붙이면 dense_score를 더한다.
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import duckdb

ENGINE_ROOT = Path(__file__).resolve().parent.parent
CARDS = ENGINE_ROOT / "catalog" / "cards.parquet"
_WORD = re.compile(r"[가-힣]+|[A-Za-z0-9]+")
K1, B = 1.2, 0.75


def tokenize(text: str | None) -> list[str]:
    out: list[str] = []
    for w in _WORD.findall(text or ""):
        w = w.lower()
        out.append(w)
        if w[0] >= "가" and len(w) > 2:
            out.extend(w[i:i + 2] for i in range(len(w) - 1))
    return out


def _column_text(columns: str | None) -> str:
    try:
        return " ".join(c.get("label") or c.get("name") or "" for c in json.loads(columns or "[]"))
    except (TypeError, ValueError):
        return ""


@dataclass
class Card:
    card_id: str
    title: str
    source: str
    data_type: str | None
    url: str | None
    concepts: tuple[str, ...] = ()      # 개념 태그 (seed 개념 이름)


class BM25Index:
    def __init__(self, cards: list[Card], docs: list[list[str]]):
        self.cards = cards
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = sum(self.len) / max(len(docs), 1)
        df = Counter(t for d in self.tf for t in d)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        q = Counter(tokenize(query))
        out = []
        for tf, dl in zip(self.tf, self.len):
            s = 0.0
            for t, qf in q.items():
                f = tf.get(t)
                if f:
                    s += qf * self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / self.avg))
            out.append(s)
        return out

    def search(self, query: str, top_k: int = 10) -> list[tuple[Card, float]]:
        scored = sorted(zip(self.cards, self.scores(query)), key=lambda x: -x[1])
        return [(c, s) for c, s in scored[:top_k] if s > 0]


def _concept_labels() -> dict[str, str]:
    try:
        from catalog.tag_concepts import vocabulary
        return {n: v["ko"] for n, v in vocabulary().items()}
    except Exception:          # seed 파일이나 rdflib 없이도 검색은 돌아야 한다
        return {}


@lru_cache(maxsize=None)
def load_index(path: Path = CARDS) -> BM25Index:
    con = duckdb.connect()
    rows = con.execute(
        "SELECT card_id, source, title, category, description, data_type, url, columns, concepts "
        f"FROM read_parquet('{path.as_posix()}')"
    ).fetchall()
    ko = _concept_labels()
    cards, docs = [], []
    for card_id, source, title, category, desc, dtype, url, columns, concepts in rows:
        cols = _column_text(columns)
        names = tuple(json.loads(concepts or "[]")) if concepts else ()
        tags = " ".join(ko.get(n, "") for n in names)          # 태그는 한글 라벨로 색인 (질의가 한글이라)
        docs.append(tokenize(title) * 3 + tokenize(category) * 2 + tokenize(cols) * 2 + tokenize(desc) + tokenize(tags) * 2)
        cards.append(Card(card_id, (title or "").strip(), source, dtype, url, names))
    return BM25Index(cards, docs)


def main() -> None:
    query = " ".join(sys.argv[1:]) or "버스 정류소"
    sys.stdout.reconfigure(encoding="utf-8")
    for card, score in load_index().search(query):
        print(f"{score:6.2f}  {card.card_id:10} {card.title}")


if __name__ == "__main__":
    main()
