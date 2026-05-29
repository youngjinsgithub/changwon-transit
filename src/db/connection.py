"""PostgreSQL + PostGIS 연결 헬퍼.

.env 파일에서 접속 정보를 읽어 SQLAlchemy 엔진을 생성한다.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


def _load_env() -> None:
    """프로젝트 루트의 .env 를 1회 로드."""
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)


def get_db_url() -> str:
    """SQLAlchemy 접속 URL 생성.

    Returns:
        예: postgresql+psycopg2://user:pass@localhost:5432/transit
    """
    _load_env()
    user = os.environ.get("POSTGRES_USER", "transit")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "transit")

    if not password:
        raise RuntimeError(
            "POSTGRES_PASSWORD 가 설정되지 않았습니다. "
            f".env 파일을 확인하세요 (경로: {ENV_PATH})"
        )

    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


@lru_cache(maxsize=1)
def get_engine(echo: bool = False) -> Engine:
    """SQLAlchemy 엔진 (싱글톤).

    Args:
        echo: True 면 모든 SQL 문을 stdout 으로 출력.
    """
    return create_engine(
        get_db_url(),
        echo=echo,
        pool_pre_ping=True,
        future=True,
    )
