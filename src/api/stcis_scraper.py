"""STCIS 사이트 클라이언트 — HTTP 요청 직접 호출.

⚠️ 공식 OPEN API 가 아니라 사이트 내부 endpoint 를 호출하는 방식.
   사이트 구조 변경 시 깨질 수 있으니 주기적으로 동작 확인 필요.

핵심 endpoint:
    - sttnListAjax.do  : 정류장 검색 → (정류장명) 으로 sttnId 등 메타 조회
    - indicatorAjax.do : 시간대별 승하차 데이터 (1정류장 × 14일)

매핑 추출 원리:
    HTML response 안에 <input checkbox value="03|MM10148015|4833010|48125|이름|~|1">
    형식으로 우리가 필요한 모든 파라미터가 들어있음.

세션 관리:
    requests.Session() 으로 메인 페이지 GET → JSESSIONID / WMONID 자동 수신.
    1시간마다 또는 401 에러 시 자동 재발급.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE = "https://www.stcis.go.kr"
PAGE_URL = (
    f"{BASE}/pivotIndi/wpsPivotIndicator.do"
    "?siteGb=P&indiClss=IC03&indiSel=IC0308"
)
URL_STTN_LIST = f"{BASE}/pivotIndi/sttnListAjax.do"
URL_INDICATOR = f"{BASE}/pivotIndi/indicatorAjax.do"

# 창원 5개 구 + 1 알 수 없음 — 사용자 캡처한 cURL 그대로
CHANGWON_SGG_CODES = "48120_48121_48123_48125_48127_48129"
GYEONGNAM_SD_CODE = "48"

DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "ko,en;q=0.9,en-US;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": BASE,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


@dataclass
class STCISStop:
    """sttnListAjax.do checkbox value 파싱 결과."""
    tcbo_id_sttn: str       # 구분 코드 ('03' = 버스)
    excclc_area_cd: str     # 운영지역코드 (예: MM10148015)
    sttn_id: str            # STCIS 정류장 ID (예: 4833010)
    sttn_sgg_cd: str        # 시군구 코드 (예: 48125)
    stop_name: str          # 정류장명
    ars_no: str             # ARS 번호 (대부분 '~' = 없음)
    # 응답 row 의 시도/시군구/읍면동 텍스트 (매칭 보조)
    sigungu_text: str = ""
    dong_text: str = ""


class STCISClient:
    """STCIS HTTP 클라이언트.

    사용:
        client = STCISClient()
        client.refresh_session()         # 매뉴얼 호출 (자동도 됨)
        stops = client.search_stops("청량산터널앞")
        html = client.fetch_indicator(stop, "2026-04-01", "2026-04-14")
    """

    def __init__(
        self,
        sleep_range: tuple[float, float] = (1.5, 3.5),
        timeout: int = 30,
        max_retries: int = 3,
        session_refresh_interval_sec: int = 3600,
    ):
        self.sleep_range = sleep_range
        self.timeout = timeout
        self.max_retries = max_retries
        self.session_refresh_interval_sec = session_refresh_interval_sec

        self._session: Optional[requests.Session] = None
        self._session_started_at: float = 0.0

    # ------------------------------------------------------------------
    # 세션 관리
    # ------------------------------------------------------------------
    def _ensure_session(self) -> requests.Session:
        now = time.time()
        if (
            self._session is None
            or now - self._session_started_at > self.session_refresh_interval_sec
        ):
            self.refresh_session()
        return self._session  # type: ignore[return-value]

    def refresh_session(self) -> None:
        logger.info("세션 갱신: %s", PAGE_URL)
        s = requests.Session()
        s.headers.update(DEFAULT_HEADERS)
        # 메인 페이지 GET → JSESSIONID + WMONID 자동 수신
        r = s.get(PAGE_URL, timeout=self.timeout)
        r.raise_for_status()
        # Referer 는 이후 요청에 동일하게 사용
        s.headers["Referer"] = r.url
        self._session = s
        self._session_started_at = time.time()
        logger.info("세션 OK (cookies=%d)", len(s.cookies))

    def _polite_sleep(self) -> None:
        lo, hi = self.sleep_range
        time.sleep(random.uniform(lo, hi))

    # ------------------------------------------------------------------
    # 정류장 검색
    # ------------------------------------------------------------------
    def search_stops(self, stop_name: str) -> list[STCISStop]:
        """sttnListAjax.do — 정류장명 (창원 전체 범위) 검색.

        Args:
            stop_name: 검색할 정류장 이름

        Returns:
            매칭된 STCIS 정류장 리스트
        """
        data = {
            "searchDateGubun": "3",
            "searchFromMonth": "2026-04",
            "searchFromDay": "2026-04-01",
            "searchPopSttnZoneSd": GYEONGNAM_SD_CODE,
            "searchPopSttnZoneSgg": CHANGWON_SGG_CODES,
            "searchPopSttnZoneEmd": "",  # 읍면동 비움 — 응답으로 받는 시군구/동 텍스트로 매칭
            "popupSearchSttnNma": stop_name,
            "popupSearchSttnArsno": "",
            "searchFromYear": "2025",
            "searchToYear": "2025",
            "searchToMonth": "2026-04",
            "searchToDay": "2026-04-14",
            "indiCd": "Z01723",
        }
        html = self._post(URL_STTN_LIST, data)
        self._polite_sleep()
        return self._parse_stop_list(html)

    @staticmethod
    def _parse_stop_list(html: str) -> list[STCISStop]:
        soup = BeautifulSoup(html, "lxml")
        results: list[STCISStop] = []
        for tr in soup.select("tbody tr"):
            cb = tr.select_one("input[type=checkbox][name=chkSttn]")
            if not cb:
                continue
            value = cb.get("value", "")
            parts = value.split("|")
            if len(parts) < 7:
                continue
            cells = tr.select("td")
            sigungu = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            dong = cells[4].get_text(strip=True) if len(cells) > 4 else ""
            results.append(
                STCISStop(
                    tcbo_id_sttn=parts[0],
                    excclc_area_cd=parts[1],
                    sttn_id=parts[2],
                    sttn_sgg_cd=parts[3],
                    stop_name=parts[4],
                    ars_no=parts[5],
                    sigungu_text=sigungu,
                    dong_text=dong,
                )
            )
        return results

    # ------------------------------------------------------------------
    # 지표 조회 (시간대별 승하차)
    # ------------------------------------------------------------------
    def fetch_indicator(
        self,
        stop: STCISStop,
        date_from: str,  # YYYY-MM-DD
        date_to: str,    # YYYY-MM-DD (14일 이내)
    ) -> str:
        """indicatorAjax.do — 1 정류장 × N일 시간대별 승하차 HTML.

        Args:
            stop: search_stops 결과의 STCISStop
            date_from, date_to: 'YYYY-MM-DD' 형식. 14일 제한.

        Returns:
            HTML 응답 본문 (재호출 위해 사용자 측에서 파싱)
        """
        date_from_dd = date_from.replace("-", "")
        # 사용자 캡처한 cURL 의 month 필드는 큰 의미 없어 보임 — 일자만 정확하면 됨
        month_str = date_from[:7]

        data = {
            "indiCd": "Z01723",
            "siteGb": "P",
            "indiNm": "노선·정류장 지표(정류장별 이용량)",
            "searchDateGubun": "3",
            "searchFromYear": "2025",
            "searchToYear": "2025",
            "searchFromMonth": month_str,
            "searchToMonth": month_str,
            "searchFromDay": date_from,
            "searchFromDayDD": date_from_dd,
            "searchToDay": date_to,
            "zoneSd": "",
            "zoneSgg": "",
            "zoneEmd": "",
            "zoneDstrct": "",
            "selectZoneSd": "",
            "selectZoneSgg": "",
            "tcboId": "",
            "excclcAreaCd": "",
            "routeId": "",
            "routeSdCd": "",
            "routeSggCd": "",
            "tcboIdSttn": stop.tcbo_id_sttn,
            "excclcAreaCdSttn": stop.excclc_area_cd,
            "sttnId": stop.sttn_id,
            "sttnIdGrp": "",
            "sttnSdCd": "",
            "sttnSggCd": stop.sttn_sgg_cd,
            "searchODAreaGubun": "",
            "searchODAreaGubun_2": "",
            "rdStgptSel": "Y",
            "searchStgptZoneSd": "",
            "searchStgptZoneSgg": "",
            "searchStgptZoneEmd": "",
            "rdAlocSel": "Y",
            "searchAlocZoneSd": "",
            "searchAlocZoneSgg": "",
            "searchAlocZoneEmd": "",
            "pgngYn": "N",
            "daybyTblNm": "DM_STTNBY_USECNT_001",
            "mnbyTblNm": "DM_MMBY_STTNBY_USECNT_001",
            "yrbyTblNm": "",
            "dstrctTblNm": "",
            "mnbyDstrctTblNm": "",
            "yrbyDstrctTblNm": "",
        }
        html = self._post(URL_INDICATOR, data)
        self._polite_sleep()
        return html

    # ------------------------------------------------------------------
    # 공통 POST + 재시도
    # ------------------------------------------------------------------
    def _post(self, url: str, data: dict) -> str:
        s = self._ensure_session()
        for attempt in range(self.max_retries):
            try:
                r = s.post(url, data=data, timeout=self.timeout)
            except requests.RequestException as e:
                logger.warning("요청 실패 (attempt %d): %s", attempt + 1, e)
                time.sleep(5)
                continue

            if r.status_code == 200:
                return r.text
            if r.status_code in (401, 403):  # 세션 만료 추정
                logger.warning("status=%d → 세션 재발급 후 재시도", r.status_code)
                self.refresh_session()
                s = self._session  # type: ignore[assignment]
                continue
            if r.status_code in (429, 503):  # 레이트 제한
                wait = 30 * (attempt + 1)
                # 슬립 범위 영구 증가 (다음 호출부터 더 느리게)
                lo, hi = self.sleep_range
                new_lo = min(lo + 3.0, 45.0)
                new_hi = min(hi + 3.0, 90.0)
                self.sleep_range = (new_lo, new_hi)
                logger.warning(
                    "status=%d → %d초 대기 후 재시도 / 슬립 범위 %.1f~%.1f초로 영구 증가",
                    r.status_code, wait, new_lo, new_hi,
                )
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {r.status_code} on {url}")

        raise RuntimeError(f"{self.max_retries}회 재시도 실패: {url}")
