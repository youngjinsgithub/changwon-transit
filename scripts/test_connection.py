"""PostGIS 연결 및 공간 기능 동작 검증.

수행 내용:
  1. PostGIS 버전 출력
  2. 임시 테이블 생성 → POINT(창원시청 좌표) 삽입 → ST_AsText 조회 → DROP
  3. ST_Distance 로 거리 계산 동작 확인
모두 성공하면 PostGIS 가 정상 작동하는 것.

사용: `python scripts/test_connection.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402


# 창원시청 좌표 (WGS84)
CHANGWON_CITY_HALL_LON = 128.6811
CHANGWON_CITY_HALL_LAT = 35.2280

# 마산합포구청 좌표 (WGS84) — 거리 비교용
MASAN_LON = 128.5689
MASAN_LAT = 35.2056


def check_versions() -> None:
    print("[1/3] PostGIS 버전 확인")
    engine = get_engine()
    with engine.connect() as conn:
        pg_ver = conn.execute(text("SHOW server_version")).scalar()
        postgis_ver = conn.execute(text("SELECT PostGIS_Version()")).scalar()
    print(f"      PostgreSQL : {pg_ver}")
    print(f"      PostGIS    : {postgis_ver}")


def check_point_io() -> None:
    """POINT 삽입/조회 — PostGIS 가 제대로 동작하는지 확인."""
    print("[2/3] POINT 삽입·조회 테스트")
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS _postgis_smoke_test"))
        conn.execute(
            text(
                """
                CREATE TABLE _postgis_smoke_test (
                    id   SERIAL PRIMARY KEY,
                    name TEXT,
                    geom GEOMETRY(POINT, 4326)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO _postgis_smoke_test (name, geom)
                VALUES (:name, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
                """
            ),
            {
                "name": "창원시청",
                "lon": CHANGWON_CITY_HALL_LON,
                "lat": CHANGWON_CITY_HALL_LAT,
            },
        )
        row = conn.execute(
            text(
                "SELECT name, ST_AsText(geom) AS wkt, ST_SRID(geom) AS srid "
                "FROM _postgis_smoke_test"
            )
        ).one()

        assert row.srid == 4326, f"SRID 가 4326 이 아님: {row.srid}"
        assert row.wkt.startswith("POINT("), f"WKT 형식이 이상함: {row.wkt}"

        print(f"      삽입: {row.name}")
        print(f"      WKT : {row.wkt}")
        print(f"      SRID: {row.srid}")

        conn.execute(text("DROP TABLE _postgis_smoke_test"))


def check_distance() -> None:
    """ST_Distance(geography) 로 창원시청 ↔ 마산합포구청 거리 계산."""
    print("[3/3] 거리 계산 테스트 (geography 캐스팅)")
    engine = get_engine()
    with engine.connect() as conn:
        dist = conn.execute(
            text(
                """
                SELECT ST_Distance(
                    ST_SetSRID(ST_MakePoint(:lon1, :lat1), 4326)::geography,
                    ST_SetSRID(ST_MakePoint(:lon2, :lat2), 4326)::geography
                ) AS meters
                """
            ),
            {
                "lon1": CHANGWON_CITY_HALL_LON,
                "lat1": CHANGWON_CITY_HALL_LAT,
                "lon2": MASAN_LON,
                "lat2": MASAN_LAT,
            },
        ).scalar()
    # 두 지점은 약 11~12km 거리. 합리적 범위 검증.
    assert 5_000 < dist < 30_000, f"거리가 비현실적: {dist} m"
    print(f"      창원시청 ↔ 마산합포구청 ≈ {dist:,.1f} m  ({dist / 1000:.2f} km)")


def main() -> int:
    try:
        check_versions()
        check_point_io()
        check_distance()
    except Exception as e:  # noqa: BLE001
        print(f"\n[FAIL] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print("\n[OK] PostGIS 정상 작동 확인 완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
