"""STCIS sttnListAjax.do 응답 형식 탐색.

목적: 정류장 검색 API 가 어떤 형식 (JSON/HTML) 으로 sttnId 를 반환하는지 확인.
       응답에서 우리 TAGO stop_id ↔ STCIS sttnId 매핑이 가능한지 검증.

테스트 케이스: '청량산터널앞' (창원 가포동) — 우리 DB 에 2개 정류장 (상행/하행) 있음
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "data" / "raw" / "stcis"

URL = "https://www.stcis.go.kr/pivotIndi/sttnListAjax.do"

COOKIES = {
    "JSESSIONID": "IrxD2mwLQNyW7L4BE0EB_3gDEvC_m16MSDyELxNj.ipts11",
    "WMONID": "MmsxQ7KSvvz",
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
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

# 케이스 1: 사용자가 원래 캡처한 값 (가포동까지 지정)
DATA_NARROW = {
    "searchDateGubun": "3",
    "searchFromMonth": "2026-03",
    "searchFromDay": "2026-04-01",
    "searchPopSttnZoneSd": "48",
    "searchPopSttnZoneSgg": "48120_48121_48123_48125_48127_48129",
    "searchPopSttnZoneEmd": "4812510100",  # 가포동
    "popupSearchSttnNma": "청량산터널앞",
    "popupSearchSttnArsno": "",
    "searchFromYear": "2025",
    "searchToYear": "2025",
    "searchToMonth": "2026-03",
    "searchToDay": "2026-04-14",
    "indiCd": "Z01723",
}

# 케이스 2: 읍면동 비워서 창원 전체 검색
DATA_WIDE = dict(DATA_NARROW)
DATA_WIDE["searchPopSttnZoneEmd"] = ""


def probe(name: str, data: dict, out_filename: str) -> None:
    print(f"\n[{name}] POST {URL}")
    print(f"    name='{data['popupSearchSttnNma']}'  emd='{data['searchPopSttnZoneEmd']}'")
    r = requests.post(URL, headers=HEADERS, cookies=COOKIES, data=data, timeout=30)
    print(f"    status={r.status_code}  size={len(r.content):,}B  elapsed={r.elapsed.total_seconds():.2f}s")
    print(f"    Content-Type: {r.headers.get('Content-Type', '?')}")

    out = OUT_DIR / out_filename
    out.write_bytes(r.content)
    print(f"    저장: {out.relative_to(PROJECT_ROOT)}")

    body = r.text
    print(f"\n    응답 첫 1500자:\n    {'-'*56}")
    for line in body[:1500].splitlines():
        print(f"    {line}")
    print(f"    {'-'*56}")

    try:
        j = r.json()
        print(f"\n    JSON 파싱 OK — top-level: {list(j.keys()) if isinstance(j, dict) else f'list[{len(j)}]'}")
        if isinstance(j, dict):
            for k, v in list(j.items())[:10]:
                if isinstance(v, list):
                    print(f"      {k}: list[{len(v)}]  first={v[0] if v else None}")
                else:
                    print(f"      {k}: {type(v).__name__}  {str(v)[:80]}")
    except Exception:
        print(f"\n    JSON 아님 (HTML 가능)")


def main() -> int:
    probe("케이스 1: 가포동만", DATA_NARROW, "probe_sttnlist_narrow.txt")
    probe("케이스 2: 창원 전체", DATA_WIDE, "probe_sttnlist_wide.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
