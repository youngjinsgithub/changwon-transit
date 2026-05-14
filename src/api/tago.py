"""국토교통부 TAGO (전국 대중교통 정보) Open API 클라이언트.

서비스: 도시버스정보 (BusSttnInfoInqireService / BusRouteInfoInqireService)
베이스: http://apis.data.go.kr/1613000
인증: serviceKey (data.go.kr 공공데이터포털 발급)

창원 도시코드 = 38010.

응답 포맷: XML 기본, type=json 파라미터로 JSON 도 가능. 이 모듈은 XML 사용.

엔드포인트 (대표):
    - 도시코드 목록:      /BusSttnInfoInqireService/getCtyCodeList
    - 정류소 목록:        /BusSttnInfoInqireService/getCtyAcctoBusSttnList
    - 노선 목록:          /BusRouteInfoInqireService/getRouteNoList
    - 노선 경유 정류소:   /BusRouteInfoInqireService/getRouteAcctoThrghSttnList

엔드포인트는 공공데이터포털 문서 기준으로 변동될 수 있어 함수 인자로 조정 가능.
"""

from __future__ import annotations

import os
from typing import Any, Final

from src.api.base import http_get_xml
from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_BASE_URL = "https://apis.data.go.kr/1613000"
CHANGWON_CITY_CODE: Final[int] = 38010

DEFAULT_NUM_OF_ROWS: Final[int] = 1000


def _get_api_key(explicit: str | None) -> str:
    key = explicit or os.environ.get("TAGO_API_KEY", "")
    if not key:
        raise RuntimeError(
            "TAGO API 키가 없습니다. .env 의 TAGO_API_KEY 를 설정하거나 "
            "함수 호출 시 api_key= 로 직접 전달하세요."
        )
    return key


def _unwrap_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """공공데이터포털 표준 응답 (header/body/items/item) 에서 items 추출."""
    try:
        body = payload["response"]["body"]
        items = body.get("items")
    except (KeyError, TypeError):
        logger.warning("표준 응답 구조 아님: %s", list(payload.keys()))
        return []

    if not items:
        return []

    item = items.get("item") if isinstance(items, dict) else None
    if item is None:
        return []
    if isinstance(item, list):
        return item
    return [item]


def _check_result_code(payload: dict[str, Any]) -> None:
    """resultCode != '00' 이면 경고 로그."""
    try:
        header = payload["response"]["header"]
        code = header.get("resultCode")
        msg = header.get("resultMsg")
    except (KeyError, TypeError):
        return
    if code and code != "00":
        logger.warning("TAGO resultCode=%s msg=%s", code, msg)


def _paged_fetch(
    url: str,
    base_params: dict[str, Any],
    cache: bool,
) -> list[dict[str, Any]]:
    """numOfRows / pageNo 페이지네이션으로 전 페이지 수집."""
    all_items: list[dict[str, Any]] = []
    page_no = 1

    while True:
        params = dict(base_params)
        params["pageNo"] = page_no
        params["numOfRows"] = base_params.get("numOfRows", DEFAULT_NUM_OF_ROWS)

        logger.debug("TAGO GET %s pageNo=%d", url, page_no)
        payload = http_get_xml(url, params=params, cache=cache)
        _check_result_code(payload)

        items = _unwrap_items(payload)
        if not items:
            break
        all_items.extend(items)

        # totalCount 가 있으면 종료 조건으로 사용
        total = None
        try:
            total = int(payload["response"]["body"].get("totalCount") or 0)
        except (KeyError, TypeError, ValueError):
            total = None

        if total is not None and len(all_items) >= total:
            break
        # 안전장치: 페이지가 numOfRows 보다 적게 왔으면 마지막 페이지
        if len(items) < params["numOfRows"]:
            break

        page_no += 1

    return all_items


def fetch_stops(
    city_code: int = CHANGWON_CITY_CODE,
    *,
    api_key: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    endpoint: str = "/BusSttnInfoInqireService/getSttnNoList",
    cache: bool = True,
) -> list[dict[str, Any]]:
    """도시별 정류소 목록 (페이지 전체 수집).

    응답 컬럼: nodeid, nodeno, nodenm, gpslati, gpslong, citycode
    """
    params: dict[str, Any] = {
        "serviceKey": _get_api_key(api_key),
        "cityCode": city_code,
        "_type": "xml",
    }
    url = f"{base_url}{endpoint}"
    logger.info("TAGO 정류소 조회: city=%d", city_code)
    items = _paged_fetch(url, params, cache=cache)
    logger.info("    수집 %d건", len(items))
    return items


def fetch_routes(
    city_code: int = CHANGWON_CITY_CODE,
    *,
    api_key: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    endpoint: str = "/BusRouteInfoInqireService/getRouteNoList",
    cache: bool = True,
) -> list[dict[str, Any]]:
    """도시별 노선 목록 (페이지 전체 수집)."""
    params: dict[str, Any] = {
        "serviceKey": _get_api_key(api_key),
        "cityCode": city_code,
        "_type": "xml",
    }
    url = f"{base_url}{endpoint}"
    logger.info("TAGO 노선 조회: city=%d", city_code)
    items = _paged_fetch(url, params, cache=cache)
    logger.info("    수집 %d건", len(items))
    return items


def fetch_route_stops(
    route_id: str,
    city_code: int = CHANGWON_CITY_CODE,
    *,
    api_key: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    endpoint: str = "/BusRouteInfoInqireService/getRouteAcctoThrghSttnList",
    cache: bool = True,
) -> list[dict[str, Any]]:
    """노선이 경유하는 정류소 목록."""
    params: dict[str, Any] = {
        "serviceKey": _get_api_key(api_key),
        "cityCode": city_code,
        "routeId": route_id,
        "_type": "xml",
    }
    url = f"{base_url}{endpoint}"
    logger.info("TAGO 노선 경유정류소 조회: route=%s, city=%d", route_id, city_code)
    items = _paged_fetch(url, params, cache=cache)
    logger.info("    수집 %d건", len(items))
    return items


def fetch_city_codes(
    *,
    api_key: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    endpoint: str = "/BusSttnInfoInqireService/getCtyCodeList",
    cache: bool = True,
) -> list[dict[str, Any]]:
    """전국 도시코드 목록 (참조용)."""
    params: dict[str, Any] = {
        "serviceKey": _get_api_key(api_key),
        "_type": "xml",
    }
    url = f"{base_url}{endpoint}"
    logger.info("TAGO 도시코드 목록 조회")
    items = _paged_fetch(url, params, cache=cache)
    logger.info("    수집 %d건", len(items))
    return items
