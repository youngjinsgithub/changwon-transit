"""창원시 BIS (Bus Information System) Open API 클라이언트.

⚠️ DEPRECATED — 2027-01-01 종료 예정 + 신규 키 발급 막힘 (2026-05-14 확인).
    창원시 공지:
        "2027년 1월 1일부로 창원시 API 서비스가 종료 예정이오니, 신규가입자 및
         기존가입자께서는 국토교통부 TAGO API로 이용 및 전환하시기 바랍니다.
         (※ 현재 창원시 신규 신청자 API 인증키 승인 불가)"

    → **본 프로젝트는 TAGO API 만 사용**한다 (src/api/tago.py).
       이 모듈은 호환·참고용으로만 남겨두며, import 시 DeprecationWarning 발생.
       창원 BIS 가 제공하던 4개 서비스의 TAGO 대체는 data/README.md 참고.

기본 정보 (참고):
    - 베이스 URL: http://openapi.changwon.go.kr
    - 인증: serviceKey (공공데이터포털 또는 창원 BIS 발급 키)
    - 응답: 일반적으로 XML
"""

from __future__ import annotations

import os
import warnings
from typing import Any

from src.api.base import http_get_xml
from src.utils.logger import get_logger

logger = get_logger(__name__)

warnings.warn(
    "src.api.changwon_bis 는 deprecated 입니다. 창원시 BIS API 는 2027-01-01 종료 예정이며 "
    "신규 키 발급도 막혔습니다. 대신 src.api.tago 를 사용하세요.",
    DeprecationWarning,
    stacklevel=2,
)

DEFAULT_BASE_URL = "http://openapi.changwon.go.kr"


def _get_api_key(explicit: str | None) -> str:
    """명시 인자 → 환경변수 CHANGWON_BIS_API_KEY 순으로 키 조회."""
    key = explicit or os.environ.get("CHANGWON_BIS_API_KEY", "")
    if not key:
        raise RuntimeError(
            "창원 BIS API 키가 없습니다. .env 의 CHANGWON_BIS_API_KEY 를 설정하거나 "
            "함수 호출 시 api_key= 로 직접 전달하세요."
        )
    return key


def _unwrap_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """공공데이터포털 표준 응답에서 items 추출.

    표준 응답:
        response.body.items.item : [dict, ...] 또는 dict (단건)
    """
    try:
        items = payload["response"]["body"]["items"]
    except (KeyError, TypeError):
        logger.warning("표준 응답 구조 아님: keys=%s", list(payload.keys()))
        return []

    if items is None or items == "":
        return []

    item = items.get("item") if isinstance(items, dict) else None
    if item is None:
        return []
    if isinstance(item, list):
        return item
    return [item]


def fetch_stops(
    *,
    api_key: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    endpoint: str = "/rest/busInfo/getStaInfo",
    extra_params: dict[str, Any] | None = None,
    cache: bool = True,
) -> list[dict[str, Any]]:
    """정류장 전체 목록 조회.

    Args:
        api_key: 미지정 시 .env 의 CHANGWON_BIS_API_KEY 사용
        base_url: 기본 도메인
        endpoint: 정류장 목록 엔드포인트 (운영 변경 가능)
        extra_params: 페이지·정렬 등 추가 파라미터
        cache: 응답 캐싱

    Returns:
        정류장 dict 의 리스트 (응답 스키마 그대로)
    """
    params: dict[str, Any] = {"serviceKey": _get_api_key(api_key)}
    if extra_params:
        params.update(extra_params)

    url = f"{base_url}{endpoint}"
    logger.info("창원 BIS 정류장 조회: %s", url)
    payload = http_get_xml(url, params=params, cache=cache)
    items = _unwrap_items(payload)
    logger.info("    조회 %d건", len(items))
    return items


def fetch_route_stops(
    route_no: str,
    *,
    api_key: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    endpoint: str = "/rest/busInfo/getRouteByStaInfo",
    extra_params: dict[str, Any] | None = None,
    cache: bool = True,
) -> list[dict[str, Any]]:
    """노선이 경유하는 정류장 조회.

    Args:
        route_no: 노선 번호
        나머지 인자는 fetch_stops 와 동일
    """
    params: dict[str, Any] = {
        "serviceKey": _get_api_key(api_key),
        "routeNo": route_no,
    }
    if extra_params:
        params.update(extra_params)

    url = f"{base_url}{endpoint}"
    logger.info("창원 BIS 노선 경유정류장 조회: route=%s", route_no)
    payload = http_get_xml(url, params=params, cache=cache)
    items = _unwrap_items(payload)
    logger.info("    경유 정류장 %d건", len(items))
    return items
