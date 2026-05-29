"""STCIS long CSV → DB 적재 + boarding_data 집계 재계산.

전제:
  - stcis_stop_mapping 테이블 적재 완료 (stcis_load_mapping.py 실행 후)
  - data/processed/stcis_boarding_long.csv 존재 (stcis_fetch_boarding.py 산출물)

수행:
  [1] long CSV 로드 (stcis_sttn_id, date, hour, boarding, alighting 컬럼)
  [2] stcis_boarding_raw 테이블 UPSERT
  [3] boarding_data 테이블 집계 재계산 (TAGO stop_id 단위)
      - boarding = SUM(매칭 STCIS) / n_tago_in_grp
      - alighting 동일
      - n_sttn_ids, n_tago_in_grp 메타도 갱신
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402
from src.db.upsert import upsert_rows  # noqa: E402

DEFAULT_CSV = PROJECT_ROOT / "data" / "processed" / "stcis_boarding_long.csv"


AGG_SQL = """
INSERT INTO boarding_data
    (stop_id, date, hour, boarding, alighting, n_sttn_ids, n_tago_in_grp)
WITH
  grp_sttn AS (
    -- 각 TAGO stop 이 어떤 STCIS sttn_id 들에 매핑되는지
    SELECT tago_stop_id,
           array_agg(stcis_sttn_id ORDER BY stcis_sttn_id) AS sttn_ids,
           COUNT(*) AS n_sttn_ids
    FROM stcis_stop_mapping
    GROUP BY tago_stop_id
  ),
  grp_size AS (
    -- 같은 sttn_id 집합을 공유하는 TAGO stop 수
    SELECT g1.tago_stop_id,
           g1.n_sttn_ids,
           (SELECT COUNT(*)
              FROM grp_sttn g2
              WHERE g2.sttn_ids = g1.sttn_ids) AS n_tago_in_grp
    FROM grp_sttn g1
  ),
  agg AS (
    SELECT m.tago_stop_id, b.date, b.hour,
           SUM(b.boarding)  AS boarding_sum,
           SUM(b.alighting) AS alighting_sum
    FROM stcis_boarding_raw b
    JOIN stcis_stop_mapping m USING (stcis_sttn_id)
    GROUP BY m.tago_stop_id, b.date, b.hour
  )
SELECT a.tago_stop_id, a.date, a.hour,
       (a.boarding_sum::numeric  / g.n_tago_in_grp)::numeric(10,2) AS boarding,
       (a.alighting_sum::numeric / g.n_tago_in_grp)::numeric(10,2) AS alighting,
       g.n_sttn_ids, g.n_tago_in_grp
FROM agg a JOIN grp_size g USING (tago_stop_id)
ON CONFLICT (stop_id, date, hour) DO UPDATE
   SET boarding      = EXCLUDED.boarding,
       alighting     = EXCLUDED.alighting,
       n_sttn_ids    = EXCLUDED.n_sttn_ids,
       n_tago_in_grp = EXCLUDED.n_tago_in_grp;
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=str, default=str(DEFAULT_CSV),
                        help="long format CSV 경로")
    parser.add_argument("--skip-raw", action="store_true",
                        help="raw 테이블 적재 건너뛰고 집계만 재계산")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    engine = get_engine()

    if not args.skip_raw:
        print(f"[1] long CSV 로드: {csv_path}")
        if not csv_path.exists():
            print(f"    [ERR] CSV 없음. stcis_fetch_boarding.py 먼저 실행.")
            return 1
        df = pd.read_csv(csv_path)
        df = df[["stcis_sttn_id", "date", "hour", "boarding", "alighting"]].copy()
        # NaN date/hour 인 행 (빈 HTML 응답 등) 제외
        before = len(df)
        df = df.dropna(subset=["stcis_sttn_id", "date", "hour"])
        if len(df) < before:
            print(f"    NaN 행 제거: {before - len(df):,} → 유효 {len(df):,}")
        df["stcis_sttn_id"] = df["stcis_sttn_id"].astype(int)
        df["hour"] = df["hour"].astype(int)
        df["boarding"] = df["boarding"].fillna(0).astype(int)
        df["alighting"] = df["alighting"].fillna(0).astype(int)
        print(f"    {len(df):,} 행")

        print(f"\n[2] stcis_boarding_raw UPSERT")
        rows = [
            (r["stcis_sttn_id"], r["date"], r["hour"], r["boarding"], r["alighting"])
            for _, r in df.iterrows()
        ]
        n = upsert_rows(
            engine,
            table="stcis_boarding_raw",
            columns=["stcis_sttn_id", "date", "hour", "boarding", "alighting"],
            rows=rows,
            conflict_cols=["stcis_sttn_id", "date", "hour"],
            update_cols=["boarding", "alighting"],
        )
        print(f"    {n:,} 행 처리")
    else:
        print("[1-2] --skip-raw → raw 테이블 적재 스킵")

    print(f"\n[3] boarding_data 집계 INSERT (SUM ÷ n_tago_in_grp)")
    with engine.begin() as c:
        c.execute(text(AGG_SQL))

    print(f"\n[4] 검증")
    with engine.connect() as c:
        stcis_total_b = c.execute(text(
            "SELECT COALESCE(SUM(boarding),0) FROM stcis_boarding_raw"
        )).scalar()
        stcis_total_a = c.execute(text(
            "SELECT COALESCE(SUM(alighting),0) FROM stcis_boarding_raw"
        )).scalar()
        tago_total_b = c.execute(text(
            "SELECT COALESCE(SUM(boarding),0) FROM boarding_data"
        )).scalar()
        tago_total_a = c.execute(text(
            "SELECT COALESCE(SUM(alighting),0) FROM boarding_data"
        )).scalar()
        n_rows = c.execute(text("SELECT count(*) FROM boarding_data")).scalar()
        n_stop = c.execute(text(
            "SELECT count(DISTINCT stop_id) FROM boarding_data"
        )).scalar()

    print(f"    boarding_data: {n_rows:,} 행 ({n_stop} 정류장)")
    print(f"    승차 합: STCIS raw = {float(stcis_total_b):,.0f}  ↔  TAGO boarding_data = {float(tago_total_b):,.2f}")
    print(f"    하차 합: STCIS raw = {float(stcis_total_a):,.0f}  ↔  TAGO boarding_data = {float(tago_total_a):,.2f}")

    diff_b = abs(float(stcis_total_b) - float(tago_total_b))
    diff_a = abs(float(stcis_total_a) - float(tago_total_a))
    if diff_b < 1 and diff_a < 1:
        print(f"    ✅ 더블카운팅 보정 OK (오차 < 1)")
    else:
        print(f"    ⚠️ 차이 발견: 승차 {diff_b:.2f}, 하차 {diff_a:.2f}")
        print(f"    원인 가능성:")
        print(f"      - 매핑 안 된 sttn_id 의 데이터가 stcis_boarding_raw 에 있을 수 있음")
        print(f"      - 또는 boarding_data 에 옛 데이터가 남아있음")

    return 0


if __name__ == "__main__":
    sys.exit(main())
