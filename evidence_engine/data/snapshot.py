"""재현용 스냅샷: DuckDB 전체를 parquet(zstd)로 내보내고, 그것으로 DB를 다시 만든다.

    python data/snapshot.py --export              # data/snapshot/<오늘>/ 에 내보내기 (+ manifest.json)
    python data/snapshot.py --restore 2026-11-14  # 스냅샷으로 data/jeju.duckdb 재생성 (센터 PC 설치용)
    python data/snapshot.py --verify 2026-11-14   # manifest 의 해시·행 수와 대조

원본 raw(약 670MB)를 다시 받지 않고도 같은 숫자를 재현하기 위한 것이다(약 25MB).
config.yaml 의 snapshot 에 날짜를 적으면 엔진의 최신성 판단 기준일도 그 날짜로 고정된다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import duckdb

ENGINE_ROOT = Path(__file__).resolve().parent.parent
DB = ENGINE_ROOT / "data" / "jeju.duckdb"
SNAP = ENGINE_ROOT / "data" / "snapshot"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export(tag: str) -> Path:
    out = SNAP / tag
    if out.exists():
        shutil.rmtree(out)
    con = duckdb.connect(str(DB), read_only=True)
    con.execute(f"EXPORT DATABASE '{out.as_posix()}' (FORMAT parquet, COMPRESSION zstd)")
    tables = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
              for (t,) in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
    con.close()
    files = {p.name: _sha(p) for p in sorted(out.iterdir()) if p.is_file()}
    manifest = {"snapshot": tag, "tables": tables, "files": files}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    size = sum(p.stat().st_size for p in out.iterdir())
    print(f"스냅샷 {out} : 테이블 {len(tables)}개, {size / 1e6:.1f}MB")
    return out


def verify(tag: str) -> bool:
    src = SNAP / tag
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    bad = [n for n, h in manifest["files"].items() if not (src / n).exists() or _sha(src / n) != h]
    if bad:
        print(f"해시 불일치·누락: {bad}")
    return not bad


def restore(tag: str) -> None:
    src = SNAP / tag
    if not verify(tag):
        raise SystemExit("스냅샷이 손상되었다")
    if DB.exists():
        DB.rename(DB.with_suffix(".duckdb.bak"))
    con = duckdb.connect(str(DB))
    con.execute(f"IMPORT DATABASE '{src.as_posix()}'")
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    wrong = {t: n for t, n in manifest["tables"].items()
             if con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] != n}
    con.close()
    if wrong:
        raise SystemExit(f"행 수가 다른 테이블: {wrong}")
    print(f"복원 완료: {DB} (테이블 {len(manifest['tables'])}개, 행 수 일치)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", nargs="?", const=date.today().isoformat(), metavar="TAG")
    g.add_argument("--restore", metavar="TAG")
    g.add_argument("--verify", metavar="TAG")
    args = ap.parse_args()
    if args.export:
        export(args.export)
    elif args.restore:
        restore(args.restore)
    else:
        sys.exit(0 if verify(args.verify) else 1)


if __name__ == "__main__":
    main()
