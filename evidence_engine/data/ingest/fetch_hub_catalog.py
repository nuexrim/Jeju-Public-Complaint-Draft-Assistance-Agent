"""제주데이터허브 공개 데이터 목록과 빅데이터센터 제공 테이블 목록을 받아 raw/에 저장한다.

    python data/ingest/fetch_hub_catalog.py            # 오늘 날짜로 저장

누리집 목록 화면이 쓰는 공개 조회 주소를 그대로 부른다(로그인·키 불필요, 과금 없음).
데이터 본문(FILE·API 값)은 받지 않는다. 그것은 수집기(11/12)가 맡는다.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

BASE = "https://jejudatahub.net/api"
RAW = Path(__file__).resolve().parent / "raw" / "jejudatahub"
PAGE = 100


def get(path: str, params: dict | None = None) -> dict:
    url = f"{BASE}/{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_catalog() -> list[dict]:
    rows: dict[int, dict] = {}
    page = 1
    while True:
        body = get("data", {
            "orderBy": "createAt", "authYn": "false", "autoUpdateYn": "false",
            "keywordOrTagFlag": "keyword", "isPaging": "true",
            "start": (page - 1) * PAGE, "pageNumber": page, "length": PAGE,
        })
        for row in body["data"]:
            rows[row["id"]] = row
        if page * PAGE >= body["recordsTotal"] or not body["data"]:
            return list(rows.values())
        page += 1
        time.sleep(1)


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    catalog = fetch_catalog()
    (RAW / f"catalog_{stamp}.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    center = get("ndapTable/list")
    (RAW / f"center_tables_{stamp}.json").write_text(json.dumps(center, ensure_ascii=False), encoding="utf-8")
    print(f"데이터허브 {len(catalog)}건, 빅데이터센터 테이블 {center['recordsTotal']}건 저장: {RAW}")


if __name__ == "__main__":
    main()
