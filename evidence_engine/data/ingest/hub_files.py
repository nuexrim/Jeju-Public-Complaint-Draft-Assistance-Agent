"""제주데이터허브 FILE 데이터 수집기.

    python data/ingest/hub_files.py               # datasets.yaml의 hub_files (large 제외)
    python data/ingest/hub_files.py --large       # large 표시된 것도 받음
    python data/ingest/hub_files.py --ids 512 549

누리집 상세 화면의 첨부파일 내려받기 주소를 그대로 부른다(로그인·키 불필요, 과금 없음).
받은 파일은 raw/jejudatahub/files/<id>/ 에 두고, 출처·기준일·해시를 manifest.json에 남긴다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw" / "jejudatahub"
FILES = RAW / "files"
MANIFEST = FILES / "manifest.json"
DOWNLOAD = "https://jejudatahub.net/api/file/test-download"
VIEW = "https://jejudatahub.net/data/view/data/{id}"


def load_catalog() -> dict[int, dict]:
    path = sorted(RAW.glob("catalog_*.json"))[-1]
    return {row["id"]: row for row in json.loads(path.read_text(encoding="utf-8"))}


def download(info: dict, dest: Path) -> str:
    params = {k: info[k] for k in (
        "id", "fileId", "fileName", "fileSize", "contentType", "saveFileName", "downloadCount", "createAt", "createBy",
    )}
    params["size"] = info["fileSize"]
    request = urllib.request.Request(
        DOWNLOAD + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "Mozilla/5.0"}
    )
    digest = hashlib.sha256()
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
            digest.update(chunk)
    if tmp.stat().st_size != info["fileSize"]:
        tmp.unlink()
        raise IOError(f"크기 불일치: {info['fileName']}")
    tmp.replace(dest)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", type=int, nargs="*")
    parser.add_argument("--large", action="store_true")
    args = parser.parse_args(argv)

    targets = yaml.safe_load((HERE / "datasets.yaml").read_text(encoding="utf-8"))["hub_files"]
    if args.ids:
        targets = [t for t in targets if t["id"] in args.ids]
    elif not args.large:
        targets = [t for t in targets if not t.get("large")]

    catalog = load_catalog()
    FILES.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}

    for target in targets:
        row = catalog.get(target["id"])
        if not row or not row.get("dataFile"):
            print(f"건너뜀 {target['id']}: 카탈로그에 FILE 데이터로 없음")
            continue
        folder = FILES / str(row["id"])
        folder.mkdir(exist_ok=True)
        files = []
        for info in row["dataFile"]["fileInfos"]:
            dest = folder / info["fileName"].strip()
            known = next((f for f in manifest.get(str(row["id"]), {}).get("files", []) if f["name"] == dest.name), None)
            if dest.exists() and known and dest.stat().st_size == info["fileSize"]:
                files.append(known)
                continue
            sha = download(info, dest)
            files.append({"name": dest.name, "size": info["fileSize"], "sha256": sha})
            print(f"받음 {row['id']} {dest.name} ({info['fileSize'] / 1e6:.2f}MB)")
            time.sleep(1)
        manifest[str(row["id"])] = {
            "dataset": row["title"].strip(),
            "publisher": "제주데이터허브",
            "owner": row.get("owner"),
            "category": row.get("categoryName"),
            "complaint": target.get("complaint"),
            "period_start": row.get("startDate"),
            "period_end": row.get("endDate"),
            "updated_at": row.get("updateAt"),
            "url": VIEW.format(id=row["id"]),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": files,
        }
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"완료: {len(targets)}종, manifest: {MANIFEST}")


if __name__ == "__main__":
    main()
