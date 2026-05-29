"""외부 API 공통 호출 인프라.

- 지수 백오프 재시도 (기본 3회)
- 응답 캐싱 (data/raw/cache/<hash>.bin)
- XML/JSON 자동 파싱
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import requests
import xmltodict

from src.utils.logger import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / "data" / "raw" / "cache"


class APIError(Exception):
    """외부 API 호출 실패 (재시도 소진)."""


def _cache_key(url: str, params: dict[str, Any] | None) -> str:
    """URL + 정렬된 파라미터로 고유 키 생성."""
    payload = json.dumps(
        {"url": url, "params": params or {}},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def http_get(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 15.0,
    retries: int = 3,
    backoff_factor: float = 1.5,
    cache: bool = True,
    cache_dir: Path | None = None,
) -> bytes:
    """HTTP GET (지수 백오프 + 응답 바이트 캐싱).

    Args:
        url: 요청 URL
        params: 쿼리 파라미터
        timeout: 단일 요청 타임아웃 (초)
        retries: 재시도 횟수 (총 시도 = retries+1)
        backoff_factor: 지수 백오프 배수 (대기시간 = backoff_factor ** attempt)
        cache: True 면 디스크 캐싱 사용
        cache_dir: 캐시 디렉토리 (None 이면 data/raw/cache)

    Returns:
        응답 본문 (bytes)

    Raises:
        APIError: 재시도 모두 실패한 경우
    """
    cache_file: Path | None = None
    if cache:
        cdir = cache_dir or CACHE_DIR
        cdir.mkdir(parents=True, exist_ok=True)
        cache_file = cdir / f"{_cache_key(url, params)}.bin"
        if cache_file.exists():
            logger.debug("cache hit: %s", cache_file.name)
            return cache_file.read_bytes()

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            body = resp.content
            if cache_file is not None:
                cache_file.write_bytes(body)
            return body
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                wait = backoff_factor ** attempt
                logger.warning(
                    "GET %s 실패 (%d/%d): %s → %.1fs 후 재시도",
                    url,
                    attempt + 1,
                    retries + 1,
                    type(e).__name__,
                    wait,
                )
                time.sleep(wait)
            else:
                logger.error("GET %s 최종 실패: %s", url, e)

    raise APIError(f"GET {url} 재시도 {retries}회 모두 실패: {last_err}")


def http_get_xml(
    url: str,
    params: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """HTTP GET 후 XML → dict 파싱."""
    body = http_get(url, params=params, **kwargs)
    return xmltodict.parse(body)


def http_get_json(
    url: str,
    params: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """HTTP GET 후 JSON 파싱."""
    body = http_get(url, params=params, **kwargs)
    return json.loads(body.decode("utf-8"))
