"""파생 테이블: 법정동 인구 → 행정동 합산, 경계 면적·중심점."""
import duckdb

from data.derived import build_bus_usage, population_by_emd
from data.spatial.boundary import emd_geometry, haversine_km

EMD = "emd_code,sigungu_name,emd_name\n50110250,제주시,한림읍\n50130570,서귀포시,동홍동\n50130580,서귀포시,서홍동\n"
LINK = ("시도명,시군구명,행정동명,법정동명,행정구역코드,행정동코드,법정동코드\n"
        "제주특별자치도,제주시,한림읍,한림리,50110250,5011025000,5011025021\n"
        "제주특별자치도,제주시,한림읍,협재리,50110250,5011025000,5011025022\n"
        "제주특별자치도,서귀포시,동홍동,동홍동,50130570,5013057000,5013010700\n"
        "제주특별자치도,서귀포시,서홍동,동홍동,50130580,5013058000,5013010700\n")


def _pop_csv():
    ages = [f"{a}세{s}" for a in range(0, 101) for s in ("남자", "여자")]
    head = "법정동코드,기준연월,시도명,시군구명,읍면동명,리명,계,남자,여자," + ",".join(ages)

    def row(code, total, old):
        cells = ["0"] * len(ages)
        cells[ages.index("70세남자")] = str(old)
        cells[ages.index("30세여자")] = str(total - old)
        return f"{code},202610,제주특별자치도,x,x,x,{total},0,0," + ",".join(cells)

    return "\n".join([head, row("5011025021", 100, 30), row("5011025022", 50, 10), row("5013010700", 999, 99)]) + "\n"


def test_population_sums_unique_links_and_blanks_split_dongs(tmp_path):
    (tmp_path / "emd.csv").write_text(EMD, encoding="utf-8")
    (tmp_path / "link.csv").write_text(LINK, encoding="utf-8-sig")
    (tmp_path / "pop.csv").write_bytes(_pop_csv().encode("cp949"))
    rows, month = population_by_emd(tmp_path / "pop.csv", tmp_path / "link.csv", tmp_path / "emd.csv")
    got = {r[0]: r[1:] for r in rows}
    assert got["50110250"] == (150, 40, "ok")
    # 동홍동 법정동이 두 행정동에 걸치므로 둘 다 인구를 지어내지 않고 비운다
    assert got["50130570"] == (None, None, "ambiguous")
    assert got["50130580"] == (None, None, "ambiguous")
    assert month == "2026-10"


def test_split_dongs_filled_from_tongban_population(tmp_path):
    (tmp_path / "emd.csv").write_text(EMD, encoding="utf-8")
    (tmp_path / "link.csv").write_text(LINK, encoding="utf-8-sig")
    (tmp_path / "pop.csv").write_bytes(_pop_csv().encode("cp949"))
    rows, _ = population_by_emd(tmp_path / "pop.csv", tmp_path / "link.csv", tmp_path / "emd.csv",
                                tongban={"50130570": 600, "50130580": 399})
    got = {r[0]: r[1:] for r in rows}
    # 통·반 인구는 행정동코드가 있어 정확히 나뉜다. 연령은 없어 65세 이상은 비워 둔다
    assert got["50130570"] == (600, None, "tongban")
    assert got["50130580"] == (399, None, "tongban")
    assert got["50110250"] == (150, 40, "ok")


def test_ri_missing_from_link_table_falls_back_to_eupmyeon_name(tmp_path):
    (tmp_path / "emd.csv").write_text(EMD, encoding="utf-8")
    (tmp_path / "link.csv").write_text(LINK, encoding="utf-8-sig")
    pop = _pop_csv().replace("5011025022,202610,제주특별자치도,x,x,x,",
                             "5011025099,202610,제주특별자치도,제주시,한림읍,새리,")
    (tmp_path / "pop.csv").write_bytes(pop.encode("cp949"))
    rows, _ = population_by_emd(tmp_path / "pop.csv", tmp_path / "link.csv", tmp_path / "emd.csv")
    assert {r[0]: r[1] for r in rows}["50110250"] == 150


def test_bus_usage_matches_names_despite_spacing_and_skips_split_names():
    con = duckdb.connect()
    con.execute("CREATE TABLE bus_stop (stationName VARCHAR, emd_code VARCHAR)")
    con.executemany("INSERT INTO bus_stop VALUES (?, ?)", [
        ("제주도청 신제주로터리", "A"),
        ("제주국제공항3(용담,시청)", "B"),
        ("한림여중(동)", "C"),
        ("시민회관", "D"), ("시민회관", "E"),          # 같은 이름이 두 행정동에 있음
    ])
    con.execute("CREATE TABLE bus_stop_usage_by_user_type (station_name VARCHAR, user_type VARCHAR, "
                "user_count BIGINT, period VARCHAR)")
    con.executemany("INSERT INTO bus_stop_usage_by_user_type VALUES (?, '일반', ?, '2026-01-01')", [
        ("제주도청신제주로터리", 10), ("제주국제공항3(용담시청)", 20), ("한림여중", 30),
        ("시민회관", 40), ("없는정류소", 0),
    ])
    assert build_bus_usage(con) == "이용 인원의 60.0%를 행정동에 연결"
    got = dict(con.execute("SELECT station_name, emd_code FROM bus_station_emd").fetchall())
    assert got == {"제주도청신제주로터리": "A", "제주국제공항3(용담시청)": "B", "한림여중": "C"}


def test_area_and_centroid_of_square():
    # 위도 33.5 부근 0.01° x 0.01° 정사각형 ≈ 0.928 km x 1.106 km
    sq = [[126.50, 33.50], [126.51, 33.50], [126.51, 33.51], [126.50, 33.51], [126.50, 33.50]]
    geo = emd_geometry([("x", (126.50, 33.50, 126.51, 33.51), [[sq]])])
    area, lat, lon = geo["x"]
    assert abs(area - 1.026) < 0.01
    assert abs(lat - 33.505) < 1e-6 and abs(lon - 126.505) < 1e-6


def test_haversine():
    assert abs(haversine_km(33.5, 126.5, 33.5, 126.6) - 9.28) < 0.05
