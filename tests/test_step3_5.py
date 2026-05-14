"""Step 3/4/5 단위 검증 (가짜 데이터 기반, DB UPSERT 까지).

실행: `python tests/test_step3_5.py`

- Step 3 matching:  좌표 매칭 + 이름 매칭 + 통합 정확성
- Step 4 distances: Haversine, find_nearby_pairs, write_stop_distances(DB)
- Step 5 stcis_loader: 와이드→롱 변환 + boarding_data UPSERT(DB)

검증 후 임시 DB 데이터는 정리.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402
from src.etl.stcis_loader import upsert_boarding, wide_to_long  # noqa: E402
from src.geo.distances import (  # noqa: E402
    find_nearby_pairs,
    haversine_m,
    write_stop_distances,
)
from src.geo.matching import (  # noqa: E402
    match_by_coord,
    match_by_name,
    match_stops,
)


# ---------------------------------------------------------------------------
# Step 3 — matching
# ---------------------------------------------------------------------------
def test_matching() -> None:
    print("\n=== Step 3: 정류장 매칭 ===")

    # 창원 일대 가짜 정류장 (위경도, 차이 약 10~50m)
    source = pd.DataFrame(
        {
            "stop_id": ["S1", "S2", "S3", "S4"],
            "stop_name": ["창원시청", "도계동", "마산역", "용호동"],
            "latitude": [35.2280, 35.2412, 35.2056, 35.2330],
            "longitude": [128.6811, 128.6789, 128.5689, 128.6750],
        }
    )
    # target: 좌표 약간 어긋남(10~20m), 일부는 좌표가 멀고 이름은 유사
    target = pd.DataFrame(
        {
            "stop_id": ["T1", "T2", "T3", "T4", "T5"],
            "stop_name": ["창원시청", "도계동.대원아파트", "마산역광장", "용호동", "ZZZ"],
            "latitude": [35.22802, 35.24121, 35.20561, 35.40000, 36.00000],
            "longitude": [128.68111, 128.67891, 128.56891, 128.90000, 128.00000],
        }
    )

    coord = match_by_coord(source, target, threshold_m=30.0)
    print(f"좌표 매칭: {len(coord)}건")
    print(coord.to_string(index=False))
    assert len(coord) == 3, f"기대 3건, 실제 {len(coord)}"
    assert set(coord["source_id"]) == {"S1", "S2", "S3"}

    # 이름 매칭 보조: 좌표가 멀었던 S4 ↔ T4 ("용호동")는 같음
    name = match_by_name(
        source, target, threshold=80,
        exclude_source_ids={"S1", "S2", "S3"},
        exclude_target_ids={"T1", "T2", "T3"},
    )
    print(f"이름 매칭: {len(name)}건")
    print(name.to_string(index=False))
    assert len(name) >= 1
    assert name.iloc[0]["source_id"] == "S4"

    # 통합
    matched, unmatched, stats = match_stops(source, target)
    print(f"통합: {stats}")
    assert stats.matched_by_coord == 3
    assert stats.matched_by_name >= 1
    assert len(unmatched) == 1  # T5 만 unmatched
    assert unmatched.iloc[0]["stop_id"] == "T5"

    print("  ✓ Step 3 OK")


# ---------------------------------------------------------------------------
# Step 4 — distances
# ---------------------------------------------------------------------------
def test_distances() -> None:
    print("\n=== Step 4: 거리 계산 ===")

    # Haversine 단일: 창원시청 ↔ 마산역 (실거리 약 11km)
    d = haversine_m(35.2280, 128.6811, 35.2056, 128.5689)
    print(f"Haversine 창원시청↔마산역 ≈ {d:.1f} m")
    assert 9_000 < d < 13_000

    # 반경 내 쌍: 4개 정류장, 2km 이내 쌍만
    stops = pd.DataFrame(
        {
            "stop_id": ["A", "B", "C", "D"],
            "latitude": [35.2280, 35.2300, 35.2500, 35.5000],
            "longitude": [128.6811, 128.6850, 128.7000, 128.9000],
        }
    )
    pairs = find_nearby_pairs(stops, radius_m=2_000.0)
    print(f"2km 내 쌍: {len(pairs)} (양방향)")
    print(pairs.to_string(index=False))
    # A-B 는 ~약 400m, A-C 와 B-C 는 약 2km 근처. D 는 멀어서 제외.
    pair_set = {tuple(sorted([r["from_stop_id"], r["to_stop_id"]])) for _, r in pairs.iterrows()}
    assert ("A", "B") in pair_set
    assert "D" not in {x for pair in pair_set for x in pair}

    # DB 적재
    engine = get_engine()
    # FK 제약 충족용으로 stops 임시 INSERT
    with engine.begin() as conn:
        for sid, lat, lon in zip(stops["stop_id"], stops["latitude"], stops["longitude"]):
            conn.execute(
                text(
                    """
                    INSERT INTO stops (stop_id, stop_name, latitude, longitude, location)
                    VALUES (:id, :name, :lat, :lon,
                            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
                    ON CONFLICT (stop_id) DO NOTHING
                    """
                ),
                {"id": sid, "name": f"테스트{sid}", "lat": float(lat), "lon": float(lon)},
            )

    n = write_stop_distances(engine, pairs)
    print(f"stop_distances UPSERT: {n}행")
    assert n == len(pairs)

    # 검증: 다시 한번 실행해도 멱등
    n2 = write_stop_distances(engine, pairs)
    assert n2 == len(pairs), "재실행 멱등성 깨짐"
    print("  ✓ 멱등 재실행 OK")

    # cleanup
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM stop_distances WHERE from_stop_id IN :ids"),
                     {"ids": tuple(stops["stop_id"])})
        conn.execute(text("DELETE FROM stops WHERE stop_id IN :ids"),
                     {"ids": tuple(stops["stop_id"])})
    print("  ✓ Step 4 OK")


# ---------------------------------------------------------------------------
# Step 5 — STCIS ETL
# ---------------------------------------------------------------------------
def test_stcis_etl() -> None:
    print("\n=== Step 5: STCIS 와이드→롱 ETL ===")

    # 가짜 와이드: 정류장 2개 × 일자 1개 × 24시간 × {승, 하}
    cols: dict = {"stop_id": ["X1", "X2"], "date": ["2026-05-01", "2026-05-01"]}
    for h in range(24):
        cols[f"{h:02d}승"] = [h * 2, h * 3]
        cols[f"{h:02d}하"] = [h, h + 1]
    wide = pd.DataFrame(cols)
    print(f"입력 와이드: shape={wide.shape}")

    long = wide_to_long(wide)
    print(f"출력 롱: shape={long.shape}")
    print(long.head().to_string(index=False))
    assert len(long) == 2 * 24
    assert set(long.columns) == {"stop_id", "date", "hour", "boarding", "alighting"}
    # X1, h=5 → boarding=10, alighting=5
    row = long[(long["stop_id"] == "X1") & (long["hour"] == 5)].iloc[0]
    assert row["boarding"] == 10
    assert row["alighting"] == 5

    # DB UPSERT
    engine = get_engine()
    with engine.begin() as conn:
        # FK 충족용 임시 stops
        for sid in ("X1", "X2"):
            conn.execute(
                text(
                    """
                    INSERT INTO stops (stop_id, stop_name, latitude, longitude, location)
                    VALUES (:id, :n, 35.0, 128.0, ST_SetSRID(ST_MakePoint(128.0, 35.0), 4326))
                    ON CONFLICT (stop_id) DO NOTHING
                    """
                ),
                {"id": sid, "n": sid},
            )

    n = upsert_boarding(engine, long)
    print(f"boarding_data UPSERT: {n}행")
    assert n == len(long)

    # 재실행 멱등성
    n2 = upsert_boarding(engine, long)
    assert n2 == len(long)
    print("  ✓ 멱등 재실행 OK")

    # 조회 검증
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT count(*), sum(boarding), sum(alighting) "
                "FROM boarding_data WHERE stop_id IN ('X1', 'X2')"
            )
        ).one()
    print(f"DB 집계: count={rows[0]}, sum_board={rows[1]}, sum_alight={rows[2]}")
    assert rows[0] == 48

    # cleanup
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM boarding_data WHERE stop_id IN ('X1', 'X2')"))
        conn.execute(text("DELETE FROM stops WHERE stop_id IN ('X1', 'X2')"))
    print("  ✓ Step 5 OK")


def main() -> int:
    try:
        test_matching()
        test_distances()
        test_stcis_etl()
    except AssertionError as e:
        print(f"\n[FAIL] {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"\n[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    print("\n[OK] Step 3/4/5 모듈 검증 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
