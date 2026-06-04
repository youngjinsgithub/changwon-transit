"""TAGO XML 응답 캐시 → 정류장·노선·노선경유 CSV.

DB·API키 없이 `data/raw/cache/*.bin` 의 캐시된 XML 응답을 직접 파싱하여
팀원이 즉시 쓸 수 있는 CSV 3개를 생성한다.

분류 기준 (응답 item 의 필드로 엔드포인트 판별):
  - routeid  + nodeid (+ nodeord) → 노선 경유 정류장
  - routeid  + (routeno or routetp), nodeid 없음 → 노선 목록
  - nodeid + gpslati,  routeid 없음 → 정류장 목록

출력:
  data/processed/tago_stops.csv         — 정류장 마스터 (창원)
  data/processed/tago_routes.csv        — 노선 마스터 (창원)
  data/processed/tago_route_stops.csv   — 노선별 경유 정류장 순서
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import xmltodict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_ROOT / "data" / "raw" / "cache"
OUT_STOPS = PROJECT_ROOT / "data" / "processed" / "tago_stops.csv"
OUT_ROUTES = PROJECT_ROOT / "data" / "processed" / "tago_routes.csv"
OUT_RS = PROJECT_ROOT / "data" / "processed" / "tago_route_stops.csv"


def parse_cache_file(p: Path) -> list[dict]:
    """XML bytes → items list (없으면 [])."""
    try:
        doc = xmltodict.parse(p.read_bytes())
    except Exception:
        return []
    try:
        body = doc["response"]["body"]
        items = body.get("items")
    except (KeyError, TypeError):
        return []
    if not items:
        return []
    item = items.get("item") if isinstance(items, dict) else None
    if item is None:
        return []
    return item if isinstance(item, list) else [item]


def classify(item: dict) -> str:
    """item 의 필드로 엔드포인트 분류."""
    has_route = "routeid" in item
    has_node = "nodeid" in item
    has_ord = "nodeord" in item
    if has_route and has_node:
        return "route_stops"
    if has_route and not has_node:
        return "routes"
    if has_node and "gpslati" in item and not has_route:
        return "stops"
    if has_ord:
        return "route_stops"
    return "unknown"


def main() -> int:
    print(f"[1] 캐시 디렉토리: {CACHE_DIR.relative_to(PROJECT_ROOT)}")
    files = sorted(CACHE_DIR.glob("*.bin"))
    print(f"    파일 {len(files)}개")

    stops, routes, route_stops = [], [], []
    counts = {"stops": 0, "routes": 0, "route_stops": 0, "unknown": 0, "empty": 0}

    for fp in files:
        items = parse_cache_file(fp)
        if not items:
            counts["empty"] += 1
            continue
        for it in items:
            kind = classify(it)
            counts[kind] += 1
            if kind == "stops":
                stops.append(it)
            elif kind == "routes":
                routes.append(it)
            elif kind == "route_stops":
                route_stops.append(it)

    print(f"    분류: {counts}")

    print("\n[2] DataFrame 변환·중복 제거")
    df_stops = pd.DataFrame(stops)
    if not df_stops.empty:
        df_stops = df_stops.drop_duplicates(subset=["nodeid"]).reset_index(drop=True)
        # 컬럼명 한글화 (사람 친화)
        df_stops = df_stops.rename(columns={
            "nodeid": "정류장ID",
            "nodenm": "정류장명",
            "nodeno": "정류장번호",
            "gpslati": "위도",
            "gpslong": "경도",
            "citycode": "도시코드",
        })

    df_routes = pd.DataFrame(routes)
    if not df_routes.empty:
        df_routes = df_routes.drop_duplicates(subset=["routeid"]).reset_index(drop=True)
        df_routes = df_routes.rename(columns={
            "routeid": "노선ID",
            "routeno": "노선번호",
            "routetp": "노선유형",
            "startnodenm": "기점",
            "endnodenm": "종점",
            "startvehicletime": "첫차",
            "endvehicletime": "막차",
        })

    df_rs = pd.DataFrame(route_stops)
    if not df_rs.empty:
        df_rs = df_rs.drop_duplicates(subset=["routeid", "nodeid", "nodeord"]).reset_index(drop=True)
        df_rs = df_rs.rename(columns={
            "routeid": "노선ID",
            "nodeid": "정류장ID",
            "nodenm": "정류장명",
            "nodeno": "정류장번호",
            "nodeord": "경유순서",
            "updowncd": "상하행구분",
            "gpslati": "위도",
            "gpslong": "경도",
        })
        # 경유순서 정수 변환
        if "경유순서" in df_rs.columns:
            df_rs["경유순서"] = pd.to_numeric(df_rs["경유순서"], errors="coerce").astype("Int64")
            df_rs = df_rs.sort_values(["노선ID", "경유순서"]).reset_index(drop=True)

    print(f"    정류장 {len(df_stops):,}개")
    print(f"    노선   {len(df_routes):,}개")
    print(f"    경유   {len(df_rs):,}건")

    print("\n[3] CSV 저장 (UTF-8 BOM, Excel 한글 OK)")
    OUT_STOPS.parent.mkdir(parents=True, exist_ok=True)
    df_stops.to_csv(OUT_STOPS, index=False, encoding="utf-8-sig")
    df_routes.to_csv(OUT_ROUTES, index=False, encoding="utf-8-sig")
    df_rs.to_csv(OUT_RS, index=False, encoding="utf-8-sig")

    for p in (OUT_STOPS, OUT_ROUTES, OUT_RS):
        kb = p.stat().st_size / 1024
        print(f"    {p.relative_to(PROJECT_ROOT)}  ({kb:,.1f} KB)")

    print("\n[4] 샘플 미리보기")
    print("\n--- tago_stops.csv (상위 5) ---")
    print(df_stops.head().to_string(index=False))
    print("\n--- tago_routes.csv (상위 5) ---")
    print(df_routes.head().to_string(index=False))
    print("\n--- tago_route_stops.csv (상위 5) ---")
    print(df_rs.head().to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
