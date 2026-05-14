"""정류장 간 거리 계산.

전략 (명세):
    - Haversine 직선 거리, 2km 이내 쌍만 stop_distances 에 저장
    - 효율적 쌍 탐색: 좌표를 미터 평면(EPSG:5179)으로 변환 후 cKDTree.query_pairs
    - tqdm 으로 진행률 표시
    - distance_type='haversine'

추가 (선택):
    - 노선상 거리: route_stops 순서대로 인접 쌍의 Haversine 합산
"""

from __future__ import annotations

from typing import Final, Iterator

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from sqlalchemy.engine import Engine
from tqdm import tqdm

from src.db.upsert import upsert_rows
from src.utils.logger import get_logger

logger = get_logger(__name__)

KOREA_METRIC_CRS: Final[str] = "EPSG:5179"
WGS84_CRS: Final[str] = "EPSG:4326"

DEFAULT_RADIUS_M: Final[float] = 2000.0
EARTH_RADIUS_M: Final[float] = 6_371_000.0


def haversine_m(
    lat1: float | np.ndarray,
    lon1: float | np.ndarray,
    lat2: float | np.ndarray,
    lon2: float | np.ndarray,
) -> float | np.ndarray:
    """Haversine 거리 (미터). 스칼라/배열 모두 지원."""
    lat1_r = np.radians(lat1)
    lat2_r = np.radians(lat2)
    dlat = np.radians(np.asarray(lat2) - np.asarray(lat1))
    dlon = np.radians(np.asarray(lon2) - np.asarray(lon1))

    a = np.sin(dlat / 2) ** 2 + np.cos(lat1_r) * np.cos(lat2_r) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))


def find_nearby_pairs(
    stops: pd.DataFrame,
    radius_m: float = DEFAULT_RADIUS_M,
) -> pd.DataFrame:
    """반경 내 정류장 쌍 + Haversine 거리.

    Args:
        stops: stop_id, latitude, longitude 컬럼이 있는 DataFrame
        radius_m: 검색 반경 (기본 2km)

    Returns:
        컬럼: from_stop_id, to_stop_id, distance_m, distance_type
        대칭 양방향 모두 포함 (A→B, B→A). 자기자신 제외.
    """
    required = {"stop_id", "latitude", "longitude"}
    if missing := required - set(stops.columns):
        raise KeyError(f"필수 컬럼 누락: {missing}")

    if len(stops) < 2:
        logger.warning("정류장이 %d개라 쌍 탐색 불가", len(stops))
        return pd.DataFrame(
            columns=["from_stop_id", "to_stop_id", "distance_m", "distance_type"]
        )

    # 미터 좌표로 변환 → cKDTree 로 반경 내 쌍 빠르게 추출
    gdf = gpd.GeoDataFrame(
        stops[["stop_id", "latitude", "longitude"]].copy(),
        geometry=gpd.points_from_xy(stops["longitude"], stops["latitude"]),
        crs=WGS84_CRS,
    ).to_crs(KOREA_METRIC_CRS)

    coords_m = np.column_stack([gdf.geometry.x.values, gdf.geometry.y.values])
    tree = cKDTree(coords_m)

    logger.info("cKDTree query_pairs(r=%.0fm) — n=%d", radius_m, len(stops))
    pairs_np = tree.query_pairs(r=radius_m, output_type="ndarray")
    logger.info("    후보 쌍: %d개 (단방향)", len(pairs_np))

    if len(pairs_np) == 0:
        return pd.DataFrame(
            columns=["from_stop_id", "to_stop_id", "distance_m", "distance_type"]
        )

    ids = stops["stop_id"].astype(str).to_numpy()
    lats = stops["latitude"].to_numpy()
    lons = stops["longitude"].to_numpy()

    i_arr = pairs_np[:, 0]
    j_arr = pairs_np[:, 1]
    # 정확한 측지 거리는 Haversine 으로 (평면 거리는 검색용 근사)
    dists = haversine_m(lats[i_arr], lons[i_arr], lats[j_arr], lons[j_arr])

    # 양방향 저장 (A→B, B→A) — 스키마가 PK(from, to, type) 라 양쪽 모두 필요
    from_ids = np.concatenate([ids[i_arr], ids[j_arr]])
    to_ids = np.concatenate([ids[j_arr], ids[i_arr]])
    dists_both = np.concatenate([dists, dists])

    out = pd.DataFrame(
        {
            "from_stop_id": from_ids,
            "to_stop_id": to_ids,
            "distance_m": np.round(dists_both, 2),
            "distance_type": "haversine",
        }
    )
    logger.info("    저장 대상 (양방향): %d행", len(out))
    return out


def _batched(seq: list, size: int) -> Iterator[list]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def write_stop_distances(
    engine: Engine,
    pairs_df: pd.DataFrame,
    *,
    batch_size: int = 5000,
) -> int:
    """stop_distances 테이블에 UPSERT.

    Args:
        engine: SQLAlchemy 엔진
        pairs_df: find_nearby_pairs() 결과 또는 동일 스키마
        batch_size: 배치 크기 (메모리·진행률 표시 기준)

    Returns:
        총 삽입/갱신 행 수
    """
    cols = ["from_stop_id", "to_stop_id", "distance_m", "distance_type"]
    if missing := set(cols) - set(pairs_df.columns):
        raise KeyError(f"필수 컬럼 누락: {missing}")

    rows = pairs_df[cols].itertuples(index=False, name=None)
    rows_list = list(rows)
    total = 0
    for batch in tqdm(
        list(_batched(rows_list, batch_size)),
        desc="stop_distances UPSERT",
        unit="batch",
    ):
        total += upsert_rows(
            engine,
            table="stop_distances",
            columns=cols,
            rows=batch,
            conflict_cols=["from_stop_id", "to_stop_id", "distance_type"],
        )
    return total


def route_path_distances(
    route_stops: pd.DataFrame,
    stops: pd.DataFrame,
) -> pd.DataFrame:
    """노선 순서대로 인접 정류장의 Haversine 거리.

    Args:
        route_stops: route_id, stop_id, sequence, direction 컬럼
        stops: stop_id, latitude, longitude 컬럼

    Returns:
        컬럼: from_stop_id, to_stop_id, distance_m, distance_type='route'
    """
    if route_stops.empty or stops.empty:
        return pd.DataFrame(
            columns=["from_stop_id", "to_stop_id", "distance_m", "distance_type"]
        )

    coords = stops.set_index("stop_id")[["latitude", "longitude"]]
    rs = route_stops.sort_values(["route_id", "direction", "sequence"]).copy()
    rs["lat"] = rs["stop_id"].map(coords["latitude"])
    rs["lon"] = rs["stop_id"].map(coords["longitude"])

    # 좌표 결측은 거리 계산 불가 → 해당 인접 쌍은 NaN 으로 남김
    rs["next_id"] = rs.groupby(["route_id", "direction"])["stop_id"].shift(-1)
    rs["next_lat"] = rs.groupby(["route_id", "direction"])["lat"].shift(-1)
    rs["next_lon"] = rs.groupby(["route_id", "direction"])["lon"].shift(-1)

    valid = rs.dropna(subset=["next_id", "lat", "lon", "next_lat", "next_lon"])
    dists = haversine_m(
        valid["lat"].to_numpy(),
        valid["lon"].to_numpy(),
        valid["next_lat"].to_numpy(),
        valid["next_lon"].to_numpy(),
    )

    out = pd.DataFrame(
        {
            "from_stop_id": valid["stop_id"].astype(str).to_numpy(),
            "to_stop_id": valid["next_id"].astype(str).to_numpy(),
            "distance_m": np.round(dists, 2),
            "distance_type": "route",
        }
    )
    logger.info("노선상 인접 거리 계산: %d행", len(out))
    return out
