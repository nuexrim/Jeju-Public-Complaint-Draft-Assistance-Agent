"""좌표(위도·경도) → emd_code. 행정동 경계 폴리곤 안에 있는지로 판정한다.

경계 원자료: 통계청 SGIS 행정동 경계(공공누리 제1유형)를 vuski/admdongkor가 가공한 파일(CC BY 4.0).
    raw/boundary/HangJeongDong_ver*.geojson  (WGS84, adm_cd2 = 행정안전부 10자리 행정기관코드)
emd_code는 adm_cd2 앞 8자리이며, 현행 43개(emd_codes.csv)에 있는 것만 쓴다.
외부 라이브러리 없이(점-다각형 판정: 광선 교차법) 동작한다.
"""
from __future__ import annotations

import csv
import json
import math
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


def _ring_area_centroid(ring: list, lat0: float) -> tuple[float, float, float]:
    """등장방형 근사로 고리 면적(km²)과 무게중심(경도, 위도). 시계 방향이면 면적이 음수."""
    kx, ky = 111.320 * math.cos(math.radians(lat0)), 110.574
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if a == 0:
        return 0.0, ring[0][0], ring[0][1]
    return a / 2 * kx * ky, cx / (3 * a), cy / (3 * a)


def emd_geometry(boundaries=None) -> dict[str, tuple[float, float, float]]:
    """emd_code → (면적 km², 중심 위도, 중심 경도). 섬이 여러 개면 면적 가중 중심."""
    boundaries = boundaries if boundaries is not None else default_boundaries()
    out = {}
    for code, (minx, miny, maxx, maxy), polygons in boundaries or []:
        lat0 = (miny + maxy) / 2
        total = sx = sy = 0.0
        for poly in polygons:
            for k, ring in enumerate(poly):
                area, cx, cy = _ring_area_centroid([p[:2] for p in ring], lat0)
                area = abs(area) * (1 if k == 0 else -1)   # 구멍은 뺀다
                total += area
                sx += cx * area
                sy += cy * area
        out[code] = (total, sy / total, sx / total)
    return out


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


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
