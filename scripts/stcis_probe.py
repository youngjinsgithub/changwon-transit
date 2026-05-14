"""STCIS indicatorAjax.do 응답 형식 탐색.

목적: 캡처한 cURL 을 Python 으로 재현해서 응답이 JSON/HTML/XML 중 무엇인지 확인.
       응답 첫 500자 + Content-Type 헤더 출력.

⚠️ 사용자 세션 쿠키 (JSESSIONID, WMONID) 사용. 만료되면 사이트에서 새로 캡처 필요.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "data" / "raw" / "stcis"
OUT_DIR.mkdir(parents=True, exist_ok=True)

URL = "https://www.stcis.go.kr/pivotIndi/indicatorAjax.do"

# 사용자 캡처한 쿠키 — 만료되면 갱신 필요
COOKIES = {
    "JSESSIONID": "IrxD2mwLQNyW7L4BE0EB_3gDEvC_m16MSDyELxNj.ipts11",
    "WMONID": "DOookSuukY5",
}

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "ko,en;q=0.9,en-US;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://www.stcis.go.kr",
    "Referer": (
        "https://www.stcis.go.kr/pivotIndi/wpsPivotIndicator.do"
        ";jsessionid=IrxD2mwLQNyW7L4BE0EB_3gDEvC_m16MSDyELxNj.ipts11"
        "?siteGb=P&indiClss=IC03&indiSel=IC0308"
    ),
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

DATA = {
    "indiCd": "Z01723",
    "siteGb": "P",
    "indiNm": "노선·정류장 지표(정류장별 이용량)",
    "searchDateGubun": "3",
    "searchFromYear": "2025",
    "searchToYear": "2025",
    "searchFromMonth": "2026-03",
    "searchToMonth": "2026-03",
    "searchFromDay": "2026-04-01",
    "searchFromDayDD": "20260401",
    "searchToDay": "2026-04-14",
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
    "tcboIdSttn": "03",
    "excclcAreaCdSttn": "MM10148015",
    "sttnId": "4833010",
    "sttnIdGrp": "",
    "sttnSdCd": "",
    "sttnSggCd": "48125",
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


def main() -> int:
    print(f"[1] POST {URL}")
    r = requests.post(URL, headers=HEADERS, cookies=COOKIES, data=DATA, timeout=30)
    print(f"    status={r.status_code}  size={len(r.content):,}B  elapsed={r.elapsed.total_seconds():.2f}s")
    print(f"    Content-Type: {r.headers.get('Content-Type', '?')}")

    out = OUT_DIR / "probe_response.txt"
    out.write_bytes(r.content)
    print(f"\n[2] 응답 저장: {out.relative_to(PROJECT_ROOT)}")

    body = r.text
    print(f"\n[3] 응답 첫 1500자:\n{'-'*60}")
    print(body[:1500])
    print(f"{'-'*60}")

    # JSON 시도
    try:
        j = r.json()
        print("\n[4] JSON 파싱 성공!")
        if isinstance(j, dict):
            print(f"    top-level keys: {list(j.keys())[:20]}")
            for k, v in list(j.items())[:5]:
                tp = type(v).__name__
                if isinstance(v, list):
                    print(f"      {k}: list[{len(v)}]  first={v[0] if v else None}")
                else:
                    sample = str(v)[:80]
                    print(f"      {k}: {tp}  {sample}")
        elif isinstance(j, list):
            print(f"    list[{len(j)}]  first={j[0] if j else None}")
    except Exception as e:
        print(f"\n[4] JSON 파싱 실패 ({e}) — HTML/XML 일 가능성")
        # HTML 표시 여부
        if "<table" in body.lower() or "<html" in body.lower():
            print("    HTML 감지됨. 테이블 파싱 필요 (BeautifulSoup/pandas.read_html)")
        elif "<?xml" in body[:200].lower():
            print("    XML 감지됨")

    return 0


if __name__ == "__main__":
    sys.exit(main())
