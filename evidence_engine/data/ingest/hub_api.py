"""제주데이터허브 Open API 수집기.

    python data/ingest/hub_api.py               # datasets.yaml의 hub_apis 전체
    python data/ingest/hub_api.py --ids 612 578

- 키: .env 의 JEJUDATAHUB_PROJECT_KEY (코드·로그·파일에 키를 남기지 않는다)
- 주소: https://open.jejudatahub.net/api/proxy/<dataKey>/<projectKey>
- 각 API를 계정 프로젝트에 담아야 호출된다. 담지 않으면 403.
- 과금 없음. API별 하루 쿼터 1,000건이므로 데이터셋당 1회 호출을 기본으로 한다.

상태: 403 때문에 실제 응답을 아직 보지 못했다. 응답 안의 첫 번째 목록을 행으로 보고 평탄화하며,
페이지 나눔이 있는지는 첫 실제 응답을 보고 확인한다(응답 최상위 키를 출력한다).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ENGINE_ROOT = HERE.parent.parent
RAW = HERE / "raw" / "jejudatahub"
OUT = RAW / "api"
MANIFEST = OUT / "manifest.json"
PROXY = "https://open.jejudatahub.net/api/proxy/{data_key}/{project_key}"
VIEW = "https://jejudatahub.net/data/view/data/{id}"


def load_key(env_path: Path = ENGINE_ROOT / ".env") -> str:
    key = os.environ.get("JEJUDATAHUB_PROJECT_KEY", "")
    if not key and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("JEJUDATAHUB_PROJECT_KEY="):
                key = line.split("=", 1)[1]
    key = key.strip().strip('"').strip("'")
    if not key:
        raise RuntimeError("JEJUDATAHUB_PROJECT_KEY 가 없다. evidence_engine/.env 에 넣는다.")
    return key


def extract_rows(body) -> list[dict]:
    """응답에서 행 목록을 찾는다: 최상위 목록, 또는 dict 안(중첩 포함)의 첫 번째 dict 목록."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for value in body.values():
            if isinstance(value, list) and (not value or isinstance(value[0], dict)):
                return value
        for value in body.values():
            if isinstance(value, dict):
                rows = extract_rows(value)
                if rows:
                    return rows
    return []


def fetch(data_key: str, project_key: str) -> object:
    url = PROXY.format(data_key=data_key, project_key=project_key)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        hint = " (프로젝트에 이 API를 담았는지 확인)" if exc.code == 403 else ""
        raise RuntimeError(f"HTTP {exc.code}{hint}") from None  # 키가 들어간 URL은 남기지 않는다


def write_csv(rows: list[dict], path: Path) -> list[str]:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
    return columns


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", type=int, nargs="*")
    args = parser.parse_args(argv)

    specs = yaml.safe_load((HERE / "datasets.yaml").read_text(encoding="utf-8"))["hub_apis"]
    if args.ids:
        specs = [s for s in specs if s["id"] in args.ids]
    catalog = {r["id"]: r for r in json.loads(sorted(RAW.glob("catalog_*.json"))[-1].read_text(encoding="utf-8"))}
    key = load_key()
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}

    failed = 0
    for spec in specs:
        row = catalog[spec["id"]]
        try:
            body = fetch(row["dataKey"], key)
        except RuntimeError as exc:
            print(f"실패 {spec['id']} {row['title'].strip()}: {exc}")
            failed += 1
            continue
        (OUT / f"{spec['id']}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        rows = extract_rows(body)
        columns = write_csv(rows, OUT / f"{spec['id']}.csv") if rows else []
        top = list(body) if isinstance(body, dict) else f"list[{len(body)}]"
        print(f"받음 {spec['id']} {row['title'].strip()}: {len(rows)}행, 응답 최상위 {top}")
        manifest[str(spec["id"])] = {
            "dataset": row["title"].strip(),
            "publisher": "제주데이터허브",
            "complaint": spec.get("complaint"),
            "url": VIEW.format(id=spec["id"]),
            "period_start": row.get("startDate"),
            "period_end": row.get("endDate"),
            "updated_at": row.get("updateAt"),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rows": len(rows),
            "columns": columns,
            "files": [{"name": f"{spec['id']}.csv"}],
        }
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
