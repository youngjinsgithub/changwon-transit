"""STCIS 와이드 → 롱 ETL.

입력 (와이드 포맷):
    stop_id, date, "00승", "00하", "01승", "01하", ..., "23승", "23하"
    또는 영문 컬럼명 ("00_b", "00_a", ...). 별칭 인자로 조정 가능.

출력 (롱 포맷, boarding_data 스키마):
    stop_id, date, hour, boarding, alighting
"""

from __future__ import annotations

import re
from typing import Final

import pandas as pd
from sqlalchemy.engine import Engine

from src.db.upsert import upsert_rows
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 와이드 컬럼 패턴: "00승" / "00하" / "23승" / "23하" 등
# 그룹: (?P<hour>\d{1,2}) (?P<kind>승|하|board|alight|b|a)
_WIDE_COL_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<hour>\d{1,2})\s*(?P<kind>승|하|승차|하차|board|alight|b|a)$",
    re.IGNORECASE,
)

_BOARDING_KINDS: Final[set[str]] = {"승", "승차", "board", "b"}
_ALIGHTING_KINDS: Final[set[str]] = {"하", "하차", "alight", "a"}


def _classify_kind(kind: str) -> str | None:
    k = kind.strip().lower()
    if k in {x.lower() for x in _BOARDING_KINDS}:
        return "boarding"
    if k in {x.lower() for x in _ALIGHTING_KINDS}:
        return "alighting"
    return None


def wide_to_long(
    df: pd.DataFrame,
    *,
    id_cols: list[str] | None = None,
    stop_id_col: str = "stop_id",
    date_col: str = "date",
) -> pd.DataFrame:
    """STCIS 와이드 DataFrame → 롱 (boarding_data 스키마).

    Args:
        df: 와이드 DataFrame. 시간대×{승,하} 컬럼이 있어야 함.
        id_cols: ID 컬럼 명시 지정 (None 이면 [stop_id_col, date_col])
        stop_id_col: 정류장 ID 컬럼명
        date_col: 일자 컬럼명

    Returns:
        컬럼: stop_id, date, hour (0~23), boarding, alighting
    """
    if id_cols is None:
        id_cols = [stop_id_col, date_col]

    missing_id = [c for c in id_cols if c not in df.columns]
    if missing_id:
        raise KeyError(f"ID 컬럼 누락: {missing_id}. 가용 컬럼={list(df.columns)}")

    # 시간대 컬럼 식별
    parsed: dict[str, tuple[int, str]] = {}  # col -> (hour, kind)
    for col in df.columns:
        if col in id_cols:
            continue
        m = _WIDE_COL_RE.match(str(col).strip())
        if not m:
            continue
        hour = int(m.group("hour"))
        if not 0 <= hour <= 23:
            continue
        kind = _classify_kind(m.group("kind"))
        if kind is None:
            continue
        parsed[col] = (hour, kind)

    if not parsed:
        raise ValueError(
            "시간대×{승,하} 패턴 컬럼을 찾을 수 없습니다. "
            f"가용 컬럼={list(df.columns)}"
        )

    logger.info("와이드 컬럼 %d개 인식 (id=%s)", len(parsed), id_cols)

    # melt 후 hour/kind 부여
    long = df[id_cols + list(parsed.keys())].melt(
        id_vars=id_cols, var_name="_wide_col", value_name="_value"
    )
    long["hour"] = long["_wide_col"].map(lambda c: parsed[c][0])
    long["kind"] = long["_wide_col"].map(lambda c: parsed[c][1])
    long = long.drop(columns=["_wide_col"])

    # pivot 으로 boarding / alighting 컬럼으로 펼침
    wide_back = long.pivot_table(
        index=id_cols + ["hour"],
        columns="kind",
        values="_value",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    wide_back.columns.name = None

    # boarding / alighting 누락 보정
    if "boarding" not in wide_back.columns:
        wide_back["boarding"] = 0
    if "alighting" not in wide_back.columns:
        wide_back["alighting"] = 0

    # 표준 컬럼명·순서로 정렬
    out = wide_back.rename(columns={stop_id_col: "stop_id", date_col: "date"})
    out = out[["stop_id", "date", "hour", "boarding", "alighting"]]
    out["stop_id"] = out["stop_id"].astype(str)
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out["hour"] = out["hour"].astype(int)
    out["boarding"] = out["boarding"].fillna(0).astype(int)
    out["alighting"] = out["alighting"].fillna(0).astype(int)

    logger.info("롱 변환 완료: %d행 (정류장×일자×시간)", len(out))
    return out


def upsert_boarding(
    engine: Engine,
    long_df: pd.DataFrame,
    *,
    page_size: int = 2000,
) -> int:
    """boarding_data 테이블에 UPSERT.

    Args:
        engine: SQLAlchemy 엔진
        long_df: wide_to_long() 결과 또는 동일 스키마
        page_size: execute_values 배치 크기
    """
    cols = ["stop_id", "date", "hour", "boarding", "alighting"]
    if missing := set(cols) - set(long_df.columns):
        raise KeyError(f"필수 컬럼 누락: {missing}")

    rows = long_df[cols].itertuples(index=False, name=None)
    return upsert_rows(
        engine,
        table="boarding_data",
        columns=cols,
        rows=rows,
        conflict_cols=["stop_id", "date", "hour"],
        page_size=page_size,
    )
