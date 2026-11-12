import json

import pytest

from data.spatial import boundary

# 가짜 경계: 한림읍(50110250) = 큰 사각형에서 가운데 구멍을 뺀 것, 구좌읍(50110256) = 그 구멍
OUTER = [[126.20, 33.30], [126.40, 33.30], [126.40, 33.50], [126.20, 33.50], [126.20, 33.30]]
HOLE = [[126.25, 33.35], [126.35, 33.35], [126.35, 33.45], [126.25, 33.45], [126.25, 33.35]]


@pytest.fixture
def boundaries(tmp_path):
    emd = tmp_path / "emd.csv"
    emd.write_text("emd_code,sigungu_name,emd_name\n50110250,제주시,한림읍\n50110256,제주시,구좌읍\n", encoding="utf-8")
    geo = tmp_path / "b.geojson"
    geo.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"properties": {"adm_cd2": "5011025000"}, "geometry": {"type": "Polygon", "coordinates": [OUTER, HOLE]}},
        {"properties": {"adm_cd2": "5011025600"}, "geometry": {"type": "MultiPolygon", "coordinates": [[HOLE]]}},
        {"properties": {"adm_cd2": "1111053000"}, "geometry": {"type": "Polygon", "coordinates": [OUTER]}},  # 제주 밖: 무시
    ]}), encoding="utf-8")
    return boundary.load_boundaries(geo, emd)


@pytest.mark.parametrize("lat, lon, expected", [
    (33.32, 126.22, "50110250"),     # 바깥 고리 안
    (33.40, 126.30, "50110256"),     # 구멍 안 → 다른 행정동
    ("33.32", "126.22", "50110250"),  # 문자열 좌표
    (33.60, 126.30, None),           # 어느 경계에도 없음
    (37.50, 127.00, None),           # 제주 범위 밖
    (None, 126.3, None),
    ("", "", None),
])
def test_point_to_emd(boundaries, lat, lon, expected):
    assert boundary.point_to_emd(lat, lon, boundaries) == expected


def test_missing_district_in_boundary_file_is_an_error(tmp_path):
    emd = tmp_path / "emd.csv"
    emd.write_text("emd_code,sigungu_name,emd_name\n50110250,제주시,한림읍\n50110256,제주시,구좌읍\n", encoding="utf-8")
    geo = tmp_path / "b.geojson"
    geo.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"properties": {"adm_cd2": "5011025000"}, "geometry": {"type": "Polygon", "coordinates": [OUTER]}},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError):
        boundary.load_boundaries(geo, emd)
