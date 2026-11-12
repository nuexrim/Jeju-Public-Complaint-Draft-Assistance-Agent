"""데이터셋 카드 생성: raw 목록(데이터허브) + 데이터 명세서(빅데이터센터) → catalog/cards.parquet

    python catalog/build_cards.py
    python catalog/build_cards.py --spec <데이터명세서.xlsx> --hub <catalog_YYYYMMDD.json>

카드에는 목록·명세서에 적힌 사실만 넣는다. 공간 단위·시간 단위·이용조건처럼
원문에 없는 항목은 비워 두고(null), 개념 태그(concepts)는 tag_concepts.py가 채운다.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import duckdb
import openpyxl

ENGINE_ROOT = Path(__file__).resolve().parent.parent
RAW = ENGINE_ROOT / "data" / "ingest" / "raw"
OUT = ENGINE_ROOT / "catalog" / "cards.parquet"
HUB_VIEW = "https://jejudatahub.net/data/view/data/{id}"
CENTER_URL = "https://jejudatahub.net/center/data"


def _latest(folder: Path, pattern: str) -> Path:
    files = sorted(folder.glob(pattern))
    if not files:
        raise FileNotFoundError(f"{folder}에 {pattern} 파일이 없다. 먼저 수집기를 실행한다.")
    return files[-1]


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip() if text is not None else ""


def _key(title: str) -> str:
    """명세서와 누리집의 띄어쓰기 차이를 무시하고 제목을 맞춘다."""
    return re.sub(r"\s+", "", title)


def hub_cards(path: Path) -> list[dict]:
    cards = []
    for row in json.loads(path.read_text(encoding="utf-8")):
        api, link = row.get("dataApi"), row.get("dataLink")
        kinds = [k for k, v in (("API", api), ("FILE", row.get("dataFile")), ("LINK", link)) if v]
        columns = [
            {"name": e["name"], "label": _clean(e.get("description")), "role": e["type"]}
            for e in (api or {}).get("dataApiElements") or []
        ]
        cards.append({
            "card_id": f"hub-{row['id']}",
            "source": "제주데이터허브",
            "title": _clean(row["title"]),
            "category": row.get("categoryName"),
            "description": _clean(row.get("description")),
            "data_type": "+".join(kinds),
            "owner": row.get("owner"),
            "period_start": row.get("startDate"),
            "period_end": row.get("endDate"),
            "updated_at": row.get("updateAt"),
            "auto_update": row.get("autoUpdatePeriod") or None,
            "auth_required": row.get("authYn"),
            "takeout_allowed": None,
            "url": HUB_VIEW.format(id=row["id"]),
            "link_url": (link or {}).get("url"),
            "data_key": row.get("dataKey"),
            "table_name": None,
            "columns": json.dumps(columns, ensure_ascii=False),
            "concepts": json.dumps([], ensure_ascii=False),
        })
    return cards


def _sheet_rows(ws) -> list[list[str]]:
    rows = []
    for row in ws.iter_rows(values_only=True):
        cells = [_clean(c) for c in row if c is not None and _clean(c)]
        if cells:
            rows.append(cells)
    return rows


def center_cards(spec: Path, tables: Path | None) -> list[dict]:
    table_names = {}
    if tables:
        for t in json.loads(tables.read_text(encoding="utf-8"))["data"]:
            table_names[_key(t["title"])] = t["tableName"]

    workbook = openpyxl.load_workbook(spec, read_only=True, data_only=True)
    cards = []
    for ws in workbook.worksheets[1:]:  # 첫 시트는 목록
        rows = _sheet_rows(ws)
        info = {r[0]: r for r in rows if r[0] in ("카테고리", "데이터명(한글)", "설명", "등록일자")}
        if "데이터명(한글)" not in info:
            continue
        title = info["데이터명(한글)"][1]
        meta = info.get("등록일자", [])
        pairs = dict(zip(meta[0::2], meta[1::2]))
        columns, in_table = [], False
        for r in rows:
            if r[0] == "No" and len(r) > 1 and r[1].replace(" ", "") in ("컬럼명(영문)", "Column명"):
                in_table = True
            elif in_table and r[0].isdigit() and len(r) >= 4:
                columns.append({"name": r[1], "label": r[2], "type": r[3], "note": " | ".join(r[4:])})
            elif in_table:
                break
        cards.append({
            "card_id": "center-" + ws.title.split(" ")[0],
            "source": "제주빅데이터센터",
            "title": title,
            "category": info.get("카테고리", ["", None])[1],
            "description": info.get("설명", ["", ""])[1],
            "data_type": "CENTER_TABLE",
            "owner": pairs.get("제공처"),
            "period_start": None,
            "period_end": None,
            "updated_at": None,
            "auto_update": None,
            "auth_required": None,
            "takeout_allowed": "불가능" not in pairs.get("반출", "") if "반출" in pairs else None,
            "url": CENTER_URL,
            "link_url": None,
            "data_key": None,
            "table_name": table_names.get(_key(title)),
            "columns": json.dumps(columns, ensure_ascii=False),
            "concepts": json.dumps([], ensure_ascii=False),
        })
    return cards


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", type=Path)
    parser.add_argument("--center-tables", type=Path)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    hub = args.hub or _latest(RAW / "jejudatahub", "catalog_*.json")
    tables = args.center_tables or _latest(RAW / "jejudatahub", "center_tables_*.json")
    spec = args.spec or _latest(RAW / "center", "*.xlsx")

    cards = hub_cards(hub) + center_cards(spec, tables)
    tmp = args.out.with_suffix(".jsonl")
    tmp.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cards), encoding="utf-8")
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT * FROM read_json_auto('{tmp.as_posix()}', format='newline_delimited', sample_size=-1)) "
        f"TO '{args.out.as_posix()}' (FORMAT PARQUET)"
    )
    tmp.unlink()
    for source, n, with_cols in con.execute(
        f"SELECT source, count(*), count(*) FILTER (columns <> '[]') FROM '{args.out.as_posix()}' GROUP BY 1 ORDER BY 1"
    ).fetchall():
        print(f"{source}: 카드 {n}건 (컬럼 정보 있음 {with_cols}건)")
    print(f"저장: {args.out}")


if __name__ == "__main__":
    main()
