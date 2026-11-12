"""좌표(위도·경도) → emd_code. 행정동 경계 폴리곤 안에 있는지로 판정한다.

경계 원자료: 통계청 SGIS 행정동 경계(공공누리 제1유형)를 vuski/admdongkor가 가공한 파일(CC BY 4.0).
    raw/boundary/HangJeongDong_ver*.geojson  (WGS84, adm_cd2 = 행정안전부 10자리 행정기관코드)
emd_code는 adm_cd2 앞 8자리이며, 현행 43개(emd_codes.csv)에 있는 것만 쓴다.
외부 라이브러리 없이(점-다각형 판정: 광선 교차법) 동작한다.
"""
from __future__ import annotations

import csv
import json
import unicodedata
from functools import lru_cache
from pathlib import Path

SPATIAL = Path(__file__).resolve().parent
BOUNDARY_DIR = SPATIAL.parent / "ingest" / "raw" / "boundary"
EMD_TABLE = SPATIAL / "emd_codes.csv"
JEJU_LAT = (33.0, 34.1)   # 마라도~추자도 포함 범위
JEJU_LON = (126.0, 127.1)


def _ring_contains(ring: list, x: float, y: float) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _polygon_contains(polygon: list, x: float, y: float) -> bool:
    """polygon = [바깥 고리, 구멍1, ...]"""
    return _ring_contains(polygon[0], x, y) and not any(_ring_contains(h, x, y) for h in polygon[1:])


def load_boundaries(path: Path, emd_table: Path = EMD_TABLE) -> list[tuple[str, tuple, list]]:
    """[(emd_code, (minx, miny, maxx, maxy), polygons)] 제주 현행 행정동만."""
    with emd_table.open(encoding="utf-8", newline="") as f:
        valid = {r["emd_code"] for r in csv.DictReader(f)}
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for feature in data["features"]:
        code = str(feature["properties"].get("adm_cd2", ""))[:8]
        if code not in valid:
            continue
        geom = feature["geometry"]
        polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        xs = [p[0] for poly in polygons for p in poly[0]]
        ys = [p[1] for poly in polygons for p in poly[0]]
        out.append((code, (min(xs), min(ys), max(xs), max(ys)), polygons))
    found = {c for c, _, _ in out}
    if found != valid:
        raise ValueError(f"경계 파일에 없는 행정동 코드: {sorted(valid - found)}")
    return out


@lru_cache(maxsize=None)
def default_boundaries():
    files = sorted(
        f for f in (BOUNDARY_DIR.glob("*.geojson") if BOUNDARY_DIR.exists() else [])
        if unicodedata.normalize("NFC", f.name).startswith("HangJeongDong_")
    )
    return load_boundaries(files[-1]) if files else None


def point_to_emd(lat, lon, boundaries=None) -> str | None:
    """위도·경도 → emd_code. 값이 없거나 제주 범위 밖이거나 어느 경계에도 안 들면 None."""
    try:
        y, x = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (JEJU_LAT[0] <= y <= JEJU_LAT[1] and JEJU_LON[0] <= x <= JEJU_LON[1]):
        return None
    boundaries = boundaries if boundaries is not None else default_boundaries()
    if not boundaries:
        return None
    for code, (minx, miny, maxx, maxy), polygons in boundaries:
        if minx <= x <= maxx and miny <= y <= maxy and any(_polygon_contains(p, x, y) for p in polygons):
            return code
    return None
