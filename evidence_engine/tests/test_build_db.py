import duckdb
import pytest

from data import build_db
from data.spatial.emd_match import match_emd


@pytest.mark.parametrize("text, expected", [
    ("구좌읍", ("50110256", "name")),
    ("제주특별자치도 제주시 조천읍 남조로 1788", ("50110259", "name")),
    ("제주특별자치도 제주시 정든로 40(이도이동)", ("50110540", "alias")),   # 법정동 표기 → 이도2동
    ("제주특별자치도 제주시 도남동 1049", (None, None)),                     # 이름이 다른 법정동은 비워 둔다
    ("제주시 용담삼동 1", (None, None)),                                    # 용담3동은 없다
    ("", (None, None)),
    (None, (None, None)),
])
def test_match_emd(text, expected):
    assert match_emd(text) == expected


def test_ragged_csv_keeps_commas_inside_one_column(tmp_path):
    path = tmp_path / "bus.csv"
    path.write_text(
        "base_date,station_name,user_type,user_count\n"
        "20190101,제주시청,경로,3\n"
        "20190101,제주국제공항(대정,화순,일주서로),일반,92\n",
        encoding="utf-8-sig",
    )
    sql = build_db.ragged_sql(path, {"column": "station_name", "int_columns": ["user_count"]})
    assert duckdb.connect().execute(sql + " ORDER BY user_count").fetchall() == [
        ("20190101", "제주시청", "경로", 3),
        ("20190101", "제주국제공항(대정,화순,일주서로)", "일반", 92),
    ]


def test_load_table_adds_common_keys(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("기준연도,읍면동,값\n2023,구좌읍,10\n2023,도남동,5\n", encoding="cp949")
    con = duckdb.connect()
    spec = {"table": "t", "period": {"column": "기준연도", "format": "yyyy"}, "emd": {"mode": "name", "columns": ["읍면동"]}}
    stats = build_db.load_table(con, spec, path, tmp_path)
    assert stats["rows"] == 2 and stats["emd_mapped"] == 1 and stats["emd_ratio"] == 0.5
    assert con.execute("SELECT period, emd_code FROM t ORDER BY 값 DESC").fetchall() == [("2023", "50110256"), ("2023", None)]


def test_load_table_rejects_unknown_column(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(KeyError):
        build_db.load_table(duckdb.connect(), {"table": "t", "emd": {"mode": "name", "columns": ["없는컬럼"]}}, path, tmp_path)
