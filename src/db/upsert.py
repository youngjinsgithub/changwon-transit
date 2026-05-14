"""PostgreSQL UPSERT 헬퍼.

PostgreSQL 의 `INSERT ... ON CONFLICT DO UPDATE` 를 사용해 멱등 적재.
psycopg2.extras.execute_values 로 배치 효율 확보.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from psycopg2.extras import execute_values
from sqlalchemy.engine import Engine

from src.utils.logger import get_logger

logger = get_logger(__name__)


def upsert_rows(
    engine: Engine,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
    conflict_cols: Sequence[str],
    update_cols: Sequence[str] | None = None,
    *,
    page_size: int = 1000,
) -> int:
    """범용 UPSERT.

    Args:
        engine: SQLAlchemy 엔진
        table: 대상 테이블명 (스키마 prefix 없음 = public)
        columns: 삽입 컬럼 순서
        rows: 행 데이터 (각 row 는 columns 순서와 일치하는 시퀀스)
        conflict_cols: ON CONFLICT 기준 컬럼들 (PK 또는 UNIQUE)
        update_cols: 충돌 시 갱신할 컬럼들. None 이면 conflict_cols 를 제외한 전 컬럼.
        page_size: execute_values 배치 크기

    Returns:
        실제 처리된 행 수
    """
    rows = list(rows)
    if not rows:
        logger.info("UPSERT %s: 빈 입력 (스킵)", table)
        return 0

    if update_cols is None:
        update_cols = [c for c in columns if c not in conflict_cols]

    cols_sql = ", ".join(f'"{c}"' for c in columns)
    conflict_sql = ", ".join(f'"{c}"' for c in conflict_cols)
    if update_cols:
        update_sql = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_cols)
        sql = (
            f'INSERT INTO "{table}" ({cols_sql}) VALUES %s '
            f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {update_sql}"
        )
    else:
        # 갱신할 컬럼이 없으면 DO NOTHING
        sql = (
            f'INSERT INTO "{table}" ({cols_sql}) VALUES %s '
            f"ON CONFLICT ({conflict_sql}) DO NOTHING"
        )

    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=page_size)
        raw_conn.commit()
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        raw_conn.close()

    logger.info("UPSERT %s: %d행 처리 (conflict=%s)", table, len(rows), conflict_sql)
    return len(rows)
