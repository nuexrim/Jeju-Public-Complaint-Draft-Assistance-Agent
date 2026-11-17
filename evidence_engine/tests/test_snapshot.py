"""스냅샷 복원: 두 번 연속 복원해도 된다(Windows는 rename 이 기존 .bak 를 덮지 못해 두 번째에 실패했었다)."""
import duckdb

import data.snapshot as snap


def test_restore_twice_and_quote_in_path(tmp_path, monkeypatch):
    root = tmp_path / "O'Neil"                    # 설치 경로에 작은따옴표가 있어도 SQL 이 깨지지 않아야 한다
    root.mkdir()
    db = root / "jeju.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t AS SELECT range AS x FROM range(5)")
    con.close()
    monkeypatch.setattr(snap, "DB", db)
    monkeypatch.setattr(snap, "SNAP", root / "snapshot")
    snap.export("t1")
    snap.restore("t1")
    snap.restore("t1")                            # .bak 가 이미 있는 상태
    assert (root / "jeju.duckdb.bak").exists()
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 5
    con.close()
