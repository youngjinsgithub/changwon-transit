"""정류장 매칭.

여러 소스(국토부 CSV, 창원 BIS, TAGO)의 정류장 ID 를 통합한다.

전략 (명세):
    1. 좌표 기반: 30m 이내면 동일 정류장 후보
    2. 이름 기반 (좌표 없을 때만 보조): 유사도 80+ 이면 동일 후보
    3. A/B 정류장(상하행)은 좌표가 떨어져 있으므로 자동 분리됨
       (30m 임계값 초과 시 별도 정류장)

입력 DataFrame 표준 컬럼:
    stop_id (str), stop_name (str), latitude (float), longitude (float)

좌표 변환: WGS84(EPSG:4326) → ITRF2000/UTM-K(EPSG:5179) 미터 평면 계산.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import geopandas as gpd
import pandas as pd
from fuzzywuzzy import fuzz

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 한국 평면좌표계 (단위: 미터)
KOREA_METRIC_CRS: Final[str] = "EPSG:5179"
WGS84_CRS: Final[str] = "EPSG:4326"

DEFAULT_COORD_THRESHOLD_M: Final[float] = 30.0
DEFAULT_NAME_THRESHOLD: Final[int] = 80


@dataclass(frozen=True)
class MatchStats:
    """매칭 통계 (단순 로깅용)."""

    source_rows: int
    target_rows: int
    matched_by_coord: int
    matched_by_name: int
    unmatched: int

    def __str__(self) -> str:
        return (
            f"source={self.source_rows}, target={self.target_rows}, "
            f"coord_match={self.matched_by_coord}, name_match={self.matched_by_name}, "
            f"unmatched={self.unmatched}"
        )


def _to_metric_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """위경도 컬럼을 가진 DataFrame → 미터 평면 GeoDataFrame."""
    if "latitude" not in df.columns or "longitude" not in df.columns:
        raise KeyError("입력 DataFrame 에 latitude/longitude 컬럼이 필요합니다")

    gdf = gpd.GeoDataFrame(
        df.copy(),
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs=WGS84_CRS,
    )
    return gdf.to_crs(KOREA_METRIC_CRS)


def match_by_coord(
    source: pd.DataFrame,
    target: pd.DataFrame,
    threshold_m: float = DEFAULT_COORD_THRESHOLD_M,
) -> pd.DataFrame:
    """좌표 기반 1:1 최근접 매칭 (threshold_m 이내).

    Args:
        source: 매칭 기준이 되는 정류장 (보통 국토부 좌표 데이터)
        target: 매칭 대상 (BIS 또는 TAGO)
        threshold_m: 동일 정류장 판정 거리 (기본 30m)

    Returns:
        컬럼: source_id, source_name, target_id, target_name, distance_m
        매칭된 행만 반환. target 의 정류장 중복 매칭은 가장 가까운 source 와만 매칭.
    """
    if source.empty or target.empty:
        return pd.DataFrame(
            columns=["source_id", "source_name", "target_id", "target_name", "distance_m"]
        )

    src_gdf = _to_metric_gdf(source).rename(
        columns={"stop_id": "source_id", "stop_name": "source_name"}
    )
    tgt_gdf = _to_metric_gdf(target).rename(
        columns={"stop_id": "target_id", "stop_name": "target_name"}
    )

    # 각 source 에 대해 가장 가까운 target 1개 (max_distance 내)
    joined = gpd.sjoin_nearest(
        src_gdf,
        tgt_gdf,
        how="inner",
        max_distance=threshold_m,
        distance_col="distance_m",
    )

    out = joined[
        ["source_id", "source_name", "target_id", "target_name", "distance_m"]
    ].copy()

    # 1:1 매칭 보장 — target 이 여러 source 에 매칭되면 가장 가까운 source 만 채택
    out = out.sort_values("distance_m").drop_duplicates(subset=["target_id"], keep="first")
    return out.reset_index(drop=True)


def match_by_name(
    source: pd.DataFrame,
    target: pd.DataFrame,
    threshold: int = DEFAULT_NAME_THRESHOLD,
    *,
    exclude_source_ids: set[str] | None = None,
    exclude_target_ids: set[str] | None = None,
) -> pd.DataFrame:
    """이름 유사도 기반 매칭 (좌표 매칭 실패분에 대한 보조).

    Args:
        source / target: 정류장 DataFrame
        threshold: fuzzywuzzy ratio 임계값 (0~100)
        exclude_source_ids / exclude_target_ids: 이미 좌표로 매칭된 ID 제외

    Returns:
        컬럼: source_id, source_name, target_id, target_name, name_score
    """
    if source.empty or target.empty:
        return pd.DataFrame(
            columns=["source_id", "source_name", "target_id", "target_name", "name_score"]
        )

    src = source
    tgt = target
    if exclude_source_ids:
        src = src[~src["stop_id"].astype(str).isin(exclude_source_ids)]
    if exclude_target_ids:
        tgt = tgt[~tgt["stop_id"].astype(str).isin(exclude_target_ids)]

    if src.empty or tgt.empty:
        return pd.DataFrame(
            columns=["source_id", "source_name", "target_id", "target_name", "name_score"]
        )

    matches: list[dict] = []
    tgt_records = tgt[["stop_id", "stop_name"]].to_dict("records")

    for _, s in src[["stop_id", "stop_name"]].iterrows():
        s_name = str(s["stop_name"])
        best_score = -1
        best_target: dict | None = None
        for t in tgt_records:
            score = fuzz.ratio(s_name, str(t["stop_name"]))
            if score > best_score:
                best_score = score
                best_target = t
        if best_target is not None and best_score >= threshold:
            matches.append(
                {
                    "source_id": str(s["stop_id"]),
                    "source_name": s_name,
                    "target_id": str(best_target["stop_id"]),
                    "target_name": str(best_target["stop_name"]),
                    "name_score": best_score,
                }
            )

    out = pd.DataFrame(matches)
    if not out.empty:
        # target 중복 시 최고 점수만 유지
        out = (
            out.sort_values("name_score", ascending=False)
            .drop_duplicates(subset=["target_id"], keep="first")
            .reset_index(drop=True)
        )
    return out


def match_stops(
    source: pd.DataFrame,
    target: pd.DataFrame,
    *,
    coord_threshold_m: float = DEFAULT_COORD_THRESHOLD_M,
    name_threshold: int = DEFAULT_NAME_THRESHOLD,
    use_name_fallback: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, MatchStats]:
    """좌표 우선 + 이름 보조 통합 매칭.

    Returns:
        (matched, unmatched_target, stats)
        - matched: 컬럼 [source_id, source_name, target_id, target_name,
                          distance_m, name_score, match_method]
        - unmatched_target: 매칭되지 않은 target 행들 (원본 그대로)
        - stats: MatchStats
    """
    coord_matched = match_by_coord(source, target, threshold_m=coord_threshold_m)
    coord_matched["match_method"] = "coord"
    coord_matched["name_score"] = float("nan")

    name_matched = pd.DataFrame()
    if use_name_fallback:
        matched_target_ids = set(coord_matched["target_id"].astype(str))
        matched_source_ids = set(coord_matched["source_id"].astype(str))
        name_matched = match_by_name(
            source,
            target,
            threshold=name_threshold,
            exclude_source_ids=matched_source_ids,
            exclude_target_ids=matched_target_ids,
        )
        if not name_matched.empty:
            name_matched["match_method"] = "name"
            name_matched["distance_m"] = float("nan")

    if name_matched.empty:
        matched = coord_matched
    else:
        # 컬럼을 동일하게 맞춰 dtype warning 방지
        cols = ["source_id", "source_name", "target_id", "target_name",
                "distance_m", "name_score", "match_method"]
        matched = pd.concat(
            [coord_matched[cols], name_matched[cols]],
            ignore_index=True,
        )

    matched_target_ids = set(matched["target_id"].astype(str)) if not matched.empty else set()
    unmatched_target = target[~target["stop_id"].astype(str).isin(matched_target_ids)].copy()

    stats = MatchStats(
        source_rows=len(source),
        target_rows=len(target),
        matched_by_coord=len(coord_matched),
        matched_by_name=len(name_matched),
        unmatched=len(unmatched_target),
    )
    logger.info("정류장 매칭 결과: %s", stats)
    return matched.reset_index(drop=True), unmatched_target.reset_index(drop=True), stats
