"""공공데이터포털 Open API 수집기: 행정동 인구 (통·반 단위).

    python data/ingest/portal_api.py --month 2026-10

왜 필요한가
  pop_emd 는 법정동 인구를 연계표로 행정동에 묶는다. 한 법정동이 여러 행정동에 걸치면(서귀포 서귀동·동홍동 등)
  나눌 근거가 없어 그 행정동들이 비었다. 행정안전부 API 는 통·반마다 행정기관코드(admmCd)를 주므로
  통·반 인구를 행정동별로 더하면 그 행정동 인구를 정확히 얻는다.

원자료  행정안전부_법정동별(행정동 통반단위) 주민등록 인구 및 세대현황 (data.go.kr 15108071, 이용허락 제한 없음)
대상    연계표에서 두 개 이상의 행정동에 걸치는 제주 법정동만 (그 법정동들의 통·반 전체)
저장    raw/emd_link/행정동_통반인구_<YYYYMM>.json  (응답 행 그대로 + 출처·수집 시각. 키는 저장하지 않는다)
키      .env 의 DATA_GO_KR_SERVICE_KEY (일반 인증키 Decoding 값). 과금 없음.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ENGINE_ROOT))

from data.derived import EMD_CSV, LINK_DIR, _find, _read_csv  # noqa: E402

ENDPOINT = "https://apis.data.go.kr/1741000/stdgPpltnHhStus/selectStdgPpltnHhStus"
SOURCE_URL = "https://www.data.go.kr/data/15108071/openapi.do"
DATASET = "행정안전부_법정동별(행정동 통반단위) 주민등록 인구 및 세대현황"
KEEP = ("stdgCd", "stdgNm", "admmCd", "dongNm", "tong", "ban", "totNmprCnt", "statsYm")


def load_key(env_path: Path = ENGINE_ROOT / ".env") -> str:
    key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "")
    if not key and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATA_GO_KR_SERVICE_KEY="):
                key = line.split("=", 1)[1]
    key = key.strip().strip('"').strip("'")
    if not key:
        raise RuntimeError("DATA_GO_KR_SERVICE_KEY 가 없다. evidence_engine/.env 에 넣는다.")
    return key


def split_legal_dongs(link_csv: Path, emd_csv: Path = EMD_CSV) -> dict[str, set[str]]:
    """{법정동코드: 걸치는 행정동 8자리 코드들} 중 행정동이 둘 이상인 제주 법정동만."""
    import csv
    with emd_csv.open(encoding="utf-8", newline="") as f:
        valid = {r["emd_code"] for r in csv.DictReader(f)}
    legal: dict[str, set[str]] = defaultdict(set)
    for r in _read_csv(link_csv):
        if r["시도명"].startswith("제주") and r["행정동코드"][:8] in valid:
            legal[r["법정동코드"]].add(r["행정동코드"][:8])
    split = {k: v for k, v in legal.items() if len(v) > 1}
    # 걸치는 행정동에 인구를 보태는 다른 법정동도 함께 받아야 그 행정동 합계가 완전해진다
    touched = set().union(*split.values()) if split else set()
    return {k: v for k, v in legal.items() if v & touched}


def _get(params: dict, key: str, retries: int = 3) -> dict:
    url = ENDPOINT + "?" + urllib.parse.urlencode({"serviceKey": key, **params})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            head = body["Response"]["head"]
            if head.get("resultCode") != "0":
                raise RuntimeError(f"API 오류 {head.get('resultCode')}: {head.get('resultMsg')}")
            return body["Response"]
        except (urllib.error.URLError, TimeoutError) as e:   # 키가 든 URL은 메시지에 넣지 않는다
            if attempt == retries - 1:
                raise RuntimeError(f"요청 실패({type(e).__name__}) stdgCd={params.get('stdgCd')}") from None
            time.sleep(3 * (attempt + 1))
    raise AssertionError


def fetch_tongban(legal_codes: list[str], ym: str, key: str) -> list[dict]:
    rows = []
    for code in legal_codes:
        page = 1
        while True:
            resp = _get({"stdgCd": code, "srchFrYm": ym, "srchToYm": ym, "lv": "4", "regSeCd": "1",
                         "type": "JSON", "numOfRows": "100", "pageNo": str(page)}, key)
            items = (resp.get("items") or {}).get("item") or []
            items = [items] if isinstance(items, dict) else items
            rows += [{k: it.get(k) for k in KEEP} for it in items]
            if page * 100 >= int(resp["head"]["totalCount"]):
                break
            page += 1
        print(f"  {code}: 누적 {len(rows)}행")
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--month", required=True, help="기준 연월 YYYY-MM (pop_emd 인구 파일과 같은 달)")
    args = ap.parse_args(argv)
    ym = args.month.replace("-", "")
    link_csv = _find("법정동 연계정보")
    if not link_csv:
        print("raw/emd_link 에 법정동 연계정보 파일이 없다.", file=sys.stderr)
        return 2
    legal = split_legal_dongs(link_csv)
    print(f"대상 법정동 {len(legal)}개 (행정동 {len(set().union(*legal.values()))}개에 걸침)")
    rows = fetch_tongban(sorted(legal), ym, load_key())
    out = LINK_DIR / f"행정동_통반인구_{ym}.json"
    out.write_text(json.dumps({
        "dataset": DATASET, "source_url": SOURCE_URL, "month": f"{ym[:4]}-{ym[4:]}",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "legal_dongs": sorted(legal), "rows": rows,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {out.relative_to(ENGINE_ROOT)} ({len(rows)}행)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
