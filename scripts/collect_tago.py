"""TAGO 정류소·노선·경유정류소 수집 + DB 적재.

수행 단계:
  [1] 정류소 목록 (getSttnNoList, city=38010)        → stops 테이블 UPSERT
  [2] 노선 목록 (getRouteNoList, city=38010)         → routes 테이블 UPSERT (route_no 단위)
  [3] 각 노선별 경유 정류소 (getRouteAcctoThrghSttnList) → route_stops UPSERT
  [4] 정류장 쌍 거리 (cKDTree, r=2km)                → stop_distances UPSERT

응답은 모두 캐시 (data/raw/cache/*.bin) 라 재실행 시 네트워크 호출 없이 동작.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402
from tqdm import tqdm  # noqa: E402

from src.api.tago import (  # noqa: E402
    CHANGWON_CITY_CODE,
    fetch_route_stops,
    fetch_routes,
    fetch_stops,
)
from src.db.connection import get_engine  # noqa: E402
from src.db.upsert import upsert_rows  # noqa: E402
from src.geo.distances import find_nearby_pairs, write_stop_distances  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# [1] 정류소
# ---------------------------------------------------------------------------
def collect_stops() -> pd.DataFrame:
    print("\n[1/4] 정류소 수집 ...")
    items = fetch_stops(city_code=CHANGWON_CITY_CODE)
    df = pd.DataFrame(items)
    print(f"      수집 {len(df)}건, 컬럼={list(df.columns)}")
    return df


def upsert_stops(engine, stops_df: pd.DataFrame) -> int:
    """TAGO 정류소 응답 → stops 테이블.

    TAGO 응답 컬럼 매핑:
        nodeid    → stop_id (프로젝트 통합 ID)
        nodenm    → stop_name
        nodeno    → bis_number (정류장 번호 = 모바일단축번호 역할)
        nodeid    → tago_id (동일 — 마스터가 TAGO 라)
        gpslati   → latitude
        gpslong   → longitude
    """
    if stops_df.empty:
        return 0

    rows = []
    for _, r in stops_df.iterrows():
        nodeid = str(r["nodeid"])
        nodeno = str(r.get("nodeno", "")) if pd.notna(r.get("nodeno")) else None
        lat = float(r["gpslati"]) if pd.notna(r["gpslati"]) else None
        lon = float(r["gpslong"]) if pd.notna(r["gpslong"]) else None
        rows.append(
            (
                nodeid,
                str(r["nodenm"]),
                nodeno,
                nodeid,  # tago_id = nodeid
                lat,
                lon,
            )
        )

    # PostGIS POINT 컬럼은 raw SQL 로 별도 처리
    cols = ["stop_id", "stop_name", "bis_number", "tago_id", "latitude", "longitude"]
    n = upsert_rows(
        engine,
        table="stops",
        columns=cols,
        rows=rows,
        conflict_cols=["stop_id"],
        update_cols=["stop_name", "bis_number", "tago_id", "latitude", "longitude"],
    )

    # location 지오메트리 채우기 (좌표 있는 행만)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE stops
                SET location = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
                WHERE latitude IS NOT NULL
                  AND longitude IS NOT NULL
                  AND (location IS NULL
                       OR ST_X(location) <> longitude
                       OR ST_Y(location) <> latitude)
                """
            )
        )
    return n


# ---------------------------------------------------------------------------
# [2] 노선
# ---------------------------------------------------------------------------
def collect_routes() -> pd.DataFrame:
    print("\n[2/4] 노선 수집 ...")
    items = fetch_routes(city_code=CHANGWON_CITY_CODE)
    df = pd.DataFrame(items)
    print(f"      수집 {len(df)}건, 컬럼={list(df.columns)}")
    return df


def upsert_routes(engine, routes_df: pd.DataFrame) -> pd.DataFrame:
    """TAGO 노선 응답 → routes 테이블.

    routes 스키마는 route_id(SERIAL)·route_no(VARCHAR) 구조.
    routeid(TAGO ID)는 route_no 에 저장하여 매핑 키로 활용.

    응답 컬럼: routeid, routeno, routetp, startnodenm, endnodenm,
              startvehicletime, endvehicletime
    """
    if routes_df.empty:
        return routes_df

    # routes 테이블에 (route_no, route_name, route_type) INSERT (PK SERIAL)
    # route_no 는 UNIQUE 가 아니라 idx 만 있음 → ON CONFLICT 사용 불가.
    # routeid 기준으로 중복 INSERT 방지를 위해 사전 조회.
    routeids = routes_df["routeid"].astype(str).tolist()

    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT route_no FROM routes WHERE route_no = ANY(:ids)"),
            {"ids": routeids},
        ).all()
    existing_set = {row[0] for row in existing}

    new_rows = routes_df[~routes_df["routeid"].astype(str).isin(existing_set)]
    if not new_rows.empty:
        with engine.begin() as conn:
            for _, r in new_rows.iterrows():
                conn.execute(
                    text(
                        """
                        INSERT INTO routes (route_no, route_name, route_type)
                        VALUES (:no, :name, :type)
                        """
                    ),
                    {
                        "no": str(r["routeid"]),  # routeid 를 route_no 로 사용
                        "name": f'{r.get("startnodenm", "")} ↔ {r.get("endnodenm", "")}',
                        "type": str(r.get("routetp") or ""),
                    },
                )
        print(f"      신규 노선 {len(new_rows)}건 INSERT")
    else:
        print("      신규 노선 없음 (전부 기존)")

    # 갱신된 route_id 매핑 반환
    with engine.begin() as conn:
        mapping = conn.execute(
            text("SELECT route_id, route_no FROM routes WHERE route_no = ANY(:ids)"),
            {"ids": routeids},
        ).all()
    map_df = pd.DataFrame(mapping, columns=["route_id", "route_no"])
    return routes_df.merge(
        map_df, left_on=routes_df["routeid"].astype(str), right_on="route_no", how="left"
    )


# ---------------------------------------------------------------------------
# [3] 노선별 경유 정류소
# ---------------------------------------------------------------------------
def collect_route_stops(engine, routes_with_id: pd.DataFrame) -> int:
    """각 노선마다 fetch_route_stops 호출 → route_stops UPSERT.

    호출이 노선 수만큼 발생 — 캐싱으로 재실행 안전.
    """
    print(f"\n[3/4] 노선별 경유정류소 수집 — {len(routes_with_id)}개 노선")
    total = 0

    rows_to_upsert = []
    # 호출은 캐싱되어 빠르지만 그래도 안전하게 tqdm
    for _, route in tqdm(
        routes_with_id.iterrows(),
        total=len(routes_with_id),
        desc="route_stops",
    ):
        route_id_str = str(route["routeid"])
        internal_route_id = route["route_id"]
        if pd.isna(internal_route_id):
            continue
        internal_route_id = int(internal_route_id)

        items = fetch_route_stops(
            route_id=route_id_str,
            city_code=CHANGWON_CITY_CODE,
        )
        if not items:
            continue

        # 응답은 순서대로 옴 → sequence 부여
        # TAGO 응답에 updowncd(0=상행/1=하행)나 nodeord(순서) 존재 가능
        for idx, item in enumerate(items, start=1):
            nodeid = str(item.get("nodeid", "")).strip()
            if not nodeid:
                continue
            seq = int(item.get("nodeord") or idx)
            updown = item.get("updowncd")
            if updown is None or str(updown) == "":
                direction = "상행"
            else:
                direction = "상행" if str(updown) in {"0", "0.0"} else "하행"

            rows_to_upsert.append(
                (internal_route_id, nodeid, seq, direction)
            )

    if not rows_to_upsert:
        print("      경유정류소 결과 없음")
        return 0

    # route_stops 의 stop_id 가 stops 에 존재해야 FK 통과.
    # TAGO 정류소 호출에서 누락된 노드가 있을 수 있으므로 사전 필터.
    with engine.connect() as conn:
        valid_stop_ids = {
            r[0]
            for r in conn.execute(text("SELECT stop_id FROM stops")).all()
        }

    before = len(rows_to_upsert)
    rows_to_upsert = [r for r in rows_to_upsert if r[1] in valid_stop_ids]
    dropped = before - len(rows_to_upsert)
    if dropped:
        print(f"      stops 에 없는 정류소 {dropped}건 스킵 (FK)")

    # 동일 배치 내 (route_id, stop_id, direction, sequence) 완전 중복 dedup
    rows_to_upsert = list({r: None for r in rows_to_upsert}.keys())

    n = upsert_rows(
        engine,
        table="route_stops",
        columns=["route_id", "stop_id", "sequence", "direction"],
        rows=rows_to_upsert,
        conflict_cols=["route_id", "stop_id", "direction", "sequence"],
        update_cols=[],  # PK 외 갱신할 컬럼 없음 → DO NOTHING
    )
    total += n
    return total


# ---------------------------------------------------------------------------
# [4] 거리
# ---------------------------------------------------------------------------
def collect_distances(engine) -> int:
    print("\n[4/4] 정류장 쌍 거리 계산 (r=2km)")
    with engine.connect() as conn:
        stops = pd.read_sql(
            "SELECT stop_id, latitude, longitude FROM stops "
            "WHERE latitude IS NOT NULL AND longitude IS NOT NULL",
            conn,
        )
    print(f"      좌표 보유 정류장: {len(stops)}개")
    if len(stops) < 2:
        return 0

    pairs = find_nearby_pairs(stops, radius_m=2000.0)
    if pairs.empty:
        return 0
    return write_stop_distances(engine, pairs)


# ---------------------------------------------------------------------------
def verify(engine) -> None:
    print("\n[검증] 테이블 건수")
    with engine.connect() as conn:
        for tbl in ("stops", "routes", "route_stops", "stop_distances"):
            n = conn.execute(text(f"SELECT count(*) FROM {tbl}")).scalar()
            print(f"      {tbl:18s} {n:>8,d} 행")

        # 정류소 좌표 분포
        bbox = conn.execute(
            text(
                "SELECT min(latitude), max(latitude), min(longitude), max(longitude) "
                "FROM stops WHERE latitude IS NOT NULL"
            )
        ).one()
        print(
            f"      좌표 범위: lat {bbox[0]:.4f}~{bbox[1]:.4f}, "
            f"lon {bbox[2]:.4f}~{bbox[3]:.4f}"
        )

        # 노선 등급 분포
        rows = conn.execute(
            text(
                "SELECT route_type, count(*) FROM routes "
                "GROUP BY route_type ORDER BY count(*) DESC"
            )
        ).all()
        print("      노선 등급:")
        for rt, c in rows:
            print(f"         {rt or '(빈값)':<20s} {c}")


def main() -> int:
    engine = get_engine()
    try:
        stops_df = collect_stops()
        n_stops = upsert_stops(engine, stops_df)
        print(f"      stops UPSERT: {n_stops}행")

        routes_df = collect_routes()
        routes_mapped = upsert_routes(engine, routes_df)

        n_rs = collect_route_stops(engine, routes_mapped)
        print(f"      route_stops UPSERT: {n_rs}행")

        n_d = collect_distances(engine)
        print(f"      stop_distances UPSERT: {n_d}행")

        verify(engine)
    except Exception as e:  # noqa: BLE001
        print(f"\n[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    print("\n[OK] TAGO 수집·적재 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
