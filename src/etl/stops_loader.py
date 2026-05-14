"""국토교통부 전국 버스정류장 위치정보 CSV 로더.

국토부 공공데이터: '전국 버스정류장 위치정보 데이터'
일반적 컬럼 (변동 가능):
    정류장번호, 정류장명, 위도, 경도, 도시코드, 도시명, 관리도시명, 모바일단축번호 등

전체 데이터에서 창원만 필터링하는 헬퍼도 제공.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pandas as pd

from src.utils.encoding import detect_encoding
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 창원시 도시코드 (TAGO 기준)
CHANGWON_CITY_CODE: Final[int] = 38010

# 컬럼명이 데이터에 따라 다르게 들어올 수 있어 별칭 매핑
_LAT_ALIASES: Final[tuple[str, ...]] = ("위도", "정류장위도", "lat", "Latitude", "LAT")
_LON_ALIASES: Final[tuple[str, ...]] = ("경도", "정류장경도", "lon", "lng", "Longitude", "LON")
_NAME_ALIASES: Final[tuple[str, ...]] = ("정류장명", "정류소명", "stop_name", "BUS_STOP_NM")
_ID_ALIASES: Final[tuple[str, ...]] = (
    "정류장번호",
    "정류장ID",
    "stop_id",
    "BUS_STOP_ID",
    "노드ID",
)
_CITY_NAME_ALIASES: Final[tuple[str, ...]] = ("도시명", "관리도시명", "시도명", "지자체명")
_CITY_CODE_ALIASES: Final[tuple[str, ...]] = ("도시코드", "city_code", "CITY_CODE")


def _find_col(df: pd.DataFrame, aliases: tuple[str, ...]) -> str | None:
    """별칭 후보 중 DataFrame 에 존재하는 첫 컬럼명 반환."""
    cols = set(df.columns)
    for a in aliases:
        if a in cols:
            return a
    return None


def load_stops_csv(
    path: str | Path,
    *,
    encoding: str | None = None,
) -> pd.DataFrame:
    """국토부 정류장 위치 CSV 1개 로드 (원본 그대로).

    Args:
        path: CSV 경로
        encoding: 명시적 인코딩. None 이면 자동 감지.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"정류장 위치 CSV 를 찾을 수 없습니다: {p}")

    enc = encoding or detect_encoding(p)
    logger.info("정류장 위치 CSV 로딩: %s (encoding=%s)", p.name, enc)

    df = pd.read_csv(p, encoding=enc)
    logger.info("    rows=%d, cols=%d", len(df), df.shape[1])
    return df


def filter_changwon(df: pd.DataFrame) -> pd.DataFrame:
    """전국 데이터에서 창원시만 필터링.

    도시코드 컬럼이 있으면 38010 으로, 없으면 도시명에 '창원' 포함으로 필터.
    """
    code_col = _find_col(df, _CITY_CODE_ALIASES)
    if code_col is not None:
        out = df[df[code_col] == CHANGWON_CITY_CODE].copy()
        logger.info(
            "창원 필터 (%s == %d): %d → %d 행", code_col, CHANGWON_CITY_CODE, len(df), len(out)
        )
        return out

    name_col = _find_col(df, _CITY_NAME_ALIASES)
    if name_col is not None:
        out = df[df[name_col].astype(str).str.contains("창원", na=False)].copy()
        logger.info("창원 필터 (%s contains '창원'): %d → %d 행", name_col, len(df), len(out))
        return out

    raise KeyError(
        f"창원 필터 불가: 도시코드/도시명 컬럼을 찾을 수 없음. "
        f"가용 컬럼={list(df.columns)}"
    )


def normalize_stops(df: pd.DataFrame) -> pd.DataFrame:
    """입력 DataFrame 을 표준 스키마(stop_id/stop_name/latitude/longitude) 로 변환.

    원본 컬럼명은 한국어/영어가 혼재. 별칭 매핑으로 통일.
    좌표 결측·범위 이상치는 제거.
    """
    lat_col = _find_col(df, _LAT_ALIASES)
    lon_col = _find_col(df, _LON_ALIASES)
    name_col = _find_col(df, _NAME_ALIASES)
    id_col = _find_col(df, _ID_ALIASES)

    missing = [
        n
        for n, v in [
            ("lat", lat_col),
            ("lon", lon_col),
            ("name", name_col),
            ("id", id_col),
        ]
        if v is None
    ]
    if missing:
        raise KeyError(
            f"필수 컬럼 누락: {missing}. 가용 컬럼={list(df.columns)}"
        )

    out = pd.DataFrame(
        {
            "stop_id": df[id_col].astype(str),
            "stop_name": df[name_col].astype(str).str.strip(),
            "latitude": pd.to_numeric(df[lat_col], errors="coerce"),
            "longitude": pd.to_numeric(df[lon_col], errors="coerce"),
        }
    )

    before = len(out)
    # 좌표 결측 제거
    out = out.dropna(subset=["latitude", "longitude"])
    # 한반도 좌표 대략 범위로 이상치 제거 (위 33~39, 경 124~132)
    out = out[
        out["latitude"].between(33.0, 39.5)
        & out["longitude"].between(124.0, 132.0)
    ]
    dropped = before - len(out)
    if dropped:
        logger.warning("좌표 결측/이상치로 %d 행 제거 (%d → %d)", dropped, before, len(out))

    return out.reset_index(drop=True)
