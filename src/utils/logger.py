"""프로젝트 전역 로깅 설정.

사용:
    from src.utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("...")

LOG_LEVEL 환경변수로 레벨 조정 가능 (기본 INFO).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Final

_CONFIGURED: bool = False

_FORMAT: Final[str] = (
    "%(asctime)s [%(levelname).1s] %(name)s : %(message)s"
)
_DATEFMT: Final[str] = "%Y-%m-%d %H:%M:%S"


def _configure_root() -> None:
    """루트 로거를 1회 설정 (멱등)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))

    root = logging.getLogger()
    root.setLevel(level)
    # 중복 핸들러 방지 (Jupyter 재실행 대응)
    root.handlers = [handler]

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """이름 기반 로거 반환. 최초 호출 시 루트 핸들러 자동 설정."""
    _configure_root()
    return logging.getLogger(name)
