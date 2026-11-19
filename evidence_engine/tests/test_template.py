"""범용 틀(화면 이름: 자동 집계·참고용): 쓸 수 있을 때만 쓰고, 판정은 '일부만 확인됨'까지."""
from types import SimpleNamespace

import duckdb
import pytest

from compute.sql_runner import SQLRunner
from package import template as T

EMD = {"A1": "가읍", "A2": "나읍", "A3": "다읍", "A4": "라읍", "B1": "가동"}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE pop_emd (emd_code VARCHAR, total_pop DOUBLE)")
    con.executemany("INSERT INTO pop_emd VALUES (?, ?)", [(c, 10000) for c in EMD])
    # 가읍 1곳, 나읍 3곳, 다읍 4곳, 라읍 5곳 → 가읍이 가장 적다
    con.execute("CREATE TABLE pf_10_1 (이름 VARCHAR, emd_code VARCHAR)")
    con.executemany("INSERT INTO pf_10_1 VALUES (?, ?)",
                    [("p", "A1")] + [("p", "A2")] * 3 + [("p", "A3")] * 4 + [("p", "A4")] * 5)
    con.execute("CREATE TABLE pf_20_1 (이름 VARCHAR, emd_code VARCHAR)")
    con.execute("CREATE TABLE prefetch_profile (table_name VARCHAR, hub_id BIGINT, title VARCHAR, url VARCHAR, "
                "updated_at VARCHAR, fetched_at VARCHAR, rows BIGINT, emd_mode VARCHAR, emd_ratio DOUBLE, "
                "place_list BOOLEAN, error VARCHAR)")
    con.executemany("INSERT INTO prefetch_profile VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        ("pf_10_1", 10, "제주특별자치도_공영주차장 현황", "u10", "2026-01-01", None, 13, "address", 0.95, True, None),
        ("pf_20_1", 20, "공영주차장 위치", "u20", "2026-01-01", None, 10, "address", 0.5, True, None),   # 결측 50%
    ])
    con.close()
    return path


def _tables(path):
    con = duckdb.connect(str(path), read_only=True)
    t = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    return con, t


def test_only_well_mapped_place_lists(db):
    con, tables = _tables(db)
    places = T.load_place_tables(con, tables, max_missing=0.30)
    assert set(places) == {"hub-10"}                     # 읍면동을 50%만 찾은 표는 쓰지 않는다
    con.close()


def test_pick_needs_shortage_and_shared_word(db):
    con, tables = _tables(db)
    places = T.load_place_tables(con, tables, 0.30)
    cards = [SimpleNamespace(card_id="hub-99"), SimpleNamespace(card_id="hub-10")]
    assert T.pick("공영주차장이 너무 부족해요", cards, places)["table"] == "pf_10_1"
    assert T.pick("공영주차장 요금이 비싸요", cards, places) is None       # 시설 수·접근 주장이 아님
    assert T.pick("놀이터가 부족해요", cards, places) is None              # 데이터셋 이름과 겹치는 낱말 없음
    assert T.pick("공영주차장이 부족해요", [SimpleNamespace(card_id=f"hub-{i}") for i in range(3)] +
                  [SimpleNamespace(card_id="hub-10")], places) is None     # 검색 상위 3개 밖
    con.close()


def test_evaluate_caps_verdict_and_cites_source(db):
    con, tables = _tables(db)
    info = T.load_place_tables(con, tables, 0.30)["hub-10"]
    con.close()
    runner = SQLRunner(db, 1000, 10)
    out = T.evaluate(info, "A1", "가읍", runner, EMD, None, "공영주차장이 부족해요")
    assert out["value"] == 1.0 and out["verdict"] == "partial"             # 순위로는 확인됨 → 일부만 확인됨으로 낮춤
    assert any("'자료로 확인됨' 대신 '일부만 확인됨'으로 판정" in c for c in out["caveats"])
    assert out["sources"][0]["url"] == "u10"
    assert out["region_compare"].startswith("읍·면 4곳 중 이곳보다 적은 곳 0곳, 같은 곳 0곳")   # 동(B1)은 빠진다
    none = T.evaluate(info, None, "우리 동네", runner, EMD, None, "공영주차장이 부족해요")
    assert none["verdict"] == "unverifiable" and none["sources"] == []
    runner.close()


def test_claim_words_drop_particles_and_stopwords():
    assert T.claim_words("우리 동네 공영주차장이 너무 부족해요") == {"공영주차장"}


def test_city_scope():
    assert T.city_scope("제주시 관내 종자업 및 육묘업 등록 현황") == "제주시"
    assert T.city_scope("서귀포시 공영주차장") == "서귀포시"
    assert T.city_scope("제주 치매 안심 센터 위치 정보") is None
    assert T._in_city("50130310", "서귀포시") and not T._in_city("50110250", "서귀포시")
