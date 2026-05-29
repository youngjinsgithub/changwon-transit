"""
창원시 버스 노선 데이터 수집 스크립트 (개편 전)
공공데이터포털 국토교통부 버스정보 API 사용

사전 준비:
  1. https://www.data.go.kr 회원가입 후 인증키 발급
  2. 아래 두 API 활용신청 (승인 즉시 또는 자동):
     - 국토교통부 시내버스 노선정보 조회 서비스
     - 국토교통부 시내버스 정류소정보 조회 서비스
  3. pip install requests pandas
"""

import os
import time
import requests
import pandas as pd
from pathlib import Path

# ── 설정 ──────────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("DATA_GO_KR_API_KEY", "여기에_API키_입력")
BASE_URL = "https://apis.data.go.kr/1613000/BusRouteInfoInqireService"
OUTPUT_DIR = Path("before")
DELAY = 0.3  # API 호출 간격 (초) — 과호출 방지

OUTPUT_DIR.mkdir(exist_ok=True)


# ── 도시코드 확인 ──────────────────────────────────────────────────────────────
def find_city_code(keyword: str = "창원") -> None:
    """도시코드 목록을 출력해 창원시 코드를 확인한다."""
    url = "https://apis.data.go.kr/1613000/BusRouteInfoInqireService/getCtyCodeList"
    resp = requests.get(url, params={"serviceKey": API_KEY, "_type": "json"}, timeout=10)
    resp.raise_for_status()
    items = resp.json()["response"]["body"]["items"]["item"]
    print("── 도시코드 목록 ──")
    for item in items:
        name = item.get("cityname", "")
        code = item.get("citycode", "")
        if keyword in name or not keyword:
            print(f"  {name}: {code}")


# ── API 호출 공통 함수 ─────────────────────────────────────────────────────────
def fetch_all(endpoint: str, params: dict) -> list:
    """페이지네이션을 자동 처리하며 전체 결과를 반환한다."""
    base_params = {
        "serviceKey": API_KEY,
        "numOfRows": 100,
        "pageNo": 1,
        "_type": "json",
    }
    base_params.update(params)

    results = []
    while True:
        resp = requests.get(
            f"{BASE_URL}/{endpoint}", params=base_params, timeout=10
        )
        resp.raise_for_status()

        body = resp.json().get("response", {}).get("body", {})
        total = body.get("totalCount", 0)
        items = body.get("items")

        if not items:
            break

        item_list = items.get("item", [])
        if isinstance(item_list, dict):  # 단일 결과일 때 dict로 옴
            item_list = [item_list]

        results.extend(item_list)

        if len(results) >= total:
            break

        base_params["pageNo"] += 1
        time.sleep(DELAY)

    return results


# ── 1단계: 노선 목록 수집 ──────────────────────────────────────────────────────
def collect_routes(city_code: int) -> pd.DataFrame:
    print(f"\n[1/3] 노선 목록 수집 (도시코드: {city_code})...")
    items = fetch_all("getRouteNoList", {"cityCode": city_code})

    if not items:
        print("  ⚠ 노선 데이터 없음 — 도시코드를 확인하세요 (find_city_code() 실행)")
        return pd.DataFrame()

    df = pd.DataFrame(items)
    df.to_csv(OUTPUT_DIR / "routes_raw.csv", index=False, encoding="utf-8-sig")
    print(f"  → {len(df)}개 노선 수집 / before/routes_raw.csv 저장")
    return df


# ── 2단계: 노선별 정류장 수집 ─────────────────────────────────────────────────
def collect_stops(routes_df: pd.DataFrame, city_code: int) -> pd.DataFrame:
    print(f"\n[2/3] 노선별 정류장 수집 ({len(routes_df)}개 노선)...")
    all_stops = []

    for i, row in routes_df.iterrows():
        route_id = row.get("routeId") or row.get("routeid")
        route_no = row.get("routeNo") or row.get("routeno") or route_id

        try:
            items = fetch_all(
                "getRouteAcctoThrghSttnList",
                {"cityCode": city_code, "routeId": route_id},
            )
            for item in items:
                item["route_id"] = route_id
                item["route_no"] = route_no
            all_stops.extend(items)
            print(f"  [{i+1}/{len(routes_df)}] {route_no}: {len(items)}개 정류장")
        except Exception as e:
            print(f"  [{i+1}/{len(routes_df)}] {route_no}: 오류 → {e}")

        time.sleep(DELAY)

    df = pd.DataFrame(all_stops)
    df.to_csv(OUTPUT_DIR / "stops_raw.csv", index=False, encoding="utf-8-sig")
    print(f"  → 총 {len(df)}개 레코드 / before/stops_raw.csv 저장")
    return df


# ── 3단계: 정규화 CSV 생성 ────────────────────────────────────────────────────
def normalize(routes_df: pd.DataFrame, stops_df: pd.DataFrame) -> None:
    print("\n[3/3] 정규화 CSV 생성...")

    # ── routes.csv ────────────────────────────────────────────────────────────
    col_map_routes = {
        "routeId": "route_id", "routeid": "route_id",
        "routeNo": "route_name", "routeno": "route_name",
        "startNodeName": "start_stop", "startnodenm": "start_stop",
        "endNodeName": "end_stop", "endnodenm": "end_stop",
        "headwaytime": "headway_peak",
    }
    r = routes_df.rename(columns=col_map_routes)
    for col in ["route_id", "route_name", "start_stop", "end_stop", "headway_peak"]:
        if col not in r.columns:
            r[col] = ""
    r["headway_off"] = ""   # API 미제공 — 수동 보완 필요
    r["trips_weekday"] = "" # API 미제공 — 수동 보완 필요
    r[["route_id", "route_name", "start_stop", "end_stop",
       "headway_peak", "headway_off", "trips_weekday"]].to_csv(
        OUTPUT_DIR / "routes.csv", index=False, encoding="utf-8-sig"
    )

    # ── stops.csv ─────────────────────────────────────────────────────────────
    col_map_stops = {
        "route_id": "route_id",
        "route_no": "route_name",
        "nodeord": "sequence", "nodeOrd": "sequence",
        "nodeid": "stop_id", "nodeId": "stop_id",
        "nodenm": "stop_name", "nodeNm": "stop_name",
        "gpslati": "lat", "gpsLati": "lat",
        "gpslong": "lon", "gpsLong": "lon",
    }
    s = stops_df.rename(columns=col_map_stops)
    for col in ["route_id", "route_name", "sequence", "stop_id", "stop_name", "lat", "lon"]:
        if col not in s.columns:
            s[col] = ""
    s[["route_id", "route_name", "sequence", "stop_id",
       "stop_name", "lat", "lon"]].to_csv(
        OUTPUT_DIR / "stops.csv", index=False, encoding="utf-8-sig"
    )

    print("  → before/routes.csv 저장 완료")
    print("  → before/stops.csv  저장 완료")
    print("\n⚠  headway_off, trips_weekday 컬럼은 API 미제공 → 수동 보완 필요")


# ── 메인 ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if API_KEY == "여기에_API키_입력":
        print("=" * 60)
        print("오류: API 키가 설정되지 않았습니다.")
        print()
        print("방법 1 (권장) — 환경변수 설정:")
        print('  set DATA_GO_KR_API_KEY=발급받은키  (Windows CMD)')
        print('  $env:DATA_GO_KR_API_KEY="발급받은키"  (PowerShell)')
        print()
        print("방법 2 — 이 파일 상단 API_KEY 변수에 직접 입력")
        print()
        print("API 키 발급: https://www.data.go.kr → 마이페이지 → 인증키")
        print("필요 API: '시내버스 노선정보 조회 서비스' 활용신청")
        print("=" * 60)
        raise SystemExit(1)

    # 창원시 도시코드 확인 (코드 확인 후 아래 CITY_CODE에 반영하고 다시 주석 처리)
    find_city_code("창원")

    CITY_CODE = 38010  # 창원시 — find_city_code()로 확인 후 수정

    routes_df = collect_routes(CITY_CODE)
    if routes_df.empty:
        raise SystemExit(1)

    stops_df = collect_stops(routes_df, CITY_CODE)
    normalize(routes_df, stops_df)

    print("\n완료! before/ 폴더를 확인하세요.")
    print("다음 단계: after/ 폴더에 개편 후 데이터를 입력하세요.")
