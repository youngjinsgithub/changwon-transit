"""STCIS 수집 데이터 점검 — 합리성 검증용.

확인 항목:
  1) 정류장별 총 승하차 (top 10)
  2) 시간대별 평균 (출퇴근 피크 패턴 정상인지)
  3) 요일별 평균 (주말 vs 주중)
  4) 날짜별 합계 (튀는 날 있는지)
  5) boarding_data vs raw 일치 확인
"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"c:\Users\user\ai동아리")

import pandas as pd
from sqlalchemy import text
from src.db.connection import get_engine

pd.set_option("display.float_format", "{:,.1f}".format)


def main() -> int:
    engine = get_engine()
    with engine.connect() as c:
        print("=" * 65)
        print("[1] 수집 범위")
        meta = c.execute(text("""
            SELECT
                COUNT(DISTINCT stcis_sttn_id) AS n_sttn,
                MIN(date) AS d_min, MAX(date) AS d_max,
                COUNT(*) AS n_rows
            FROM stcis_boarding_raw
        """)).one()
        print(f"  STCIS sttn 수: {meta[0]}")
        print(f"  날짜 범위: {meta[1]} ~ {meta[2]}")
        print(f"  raw 행 수: {meta[3]:,} (= sttn × 28일 × 24시간)")

        print("\n" + "=" * 65)
        print("[2] 정류장별 총 승하차 (raw, sttnId 기준 TOP 10)")
        df = pd.read_sql(text("""
            SELECT b.stcis_sttn_id,
                   m.tago_stop_id, m.match_tier,
                   COALESCE(s.stop_name, '?') AS stop_name,
                   SUM(b.boarding)  AS boarding_sum,
                   SUM(b.alighting) AS alighting_sum
            FROM stcis_boarding_raw b
            LEFT JOIN stcis_stop_mapping m USING (stcis_sttn_id)
            LEFT JOIN stops s ON m.tago_stop_id = s.stop_id
            GROUP BY b.stcis_sttn_id, m.tago_stop_id, m.match_tier, s.stop_name
            ORDER BY boarding_sum DESC NULLS LAST
            LIMIT 10
        """), c)
        print(df.to_string(index=False))

        print("\n" + "=" * 65)
        print("[3] 시간대별 평균 (전체 raw 데이터, 시간당 평균 승차)")
        df = pd.read_sql(text("""
            SELECT hour, AVG(boarding) AS avg_b, AVG(alighting) AS avg_a
            FROM stcis_boarding_raw
            GROUP BY hour ORDER BY hour
        """), c)
        # 시각화 막대 (간단 ASCII)
        max_b = df["avg_b"].max() or 1
        print(f"  {'시':>3}  {'평균승':>7}  {'평균하':>7}  bar(승차)")
        for _, r in df.iterrows():
            bar_len = int(r["avg_b"] / max_b * 40)
            bar = "█" * bar_len
            print(f"  {int(r['hour']):>3}  {r['avg_b']:>7.2f}  {r['avg_a']:>7.2f}  {bar}")

        print("\n" + "=" * 65)
        print("[4] 요일별 평균 (0=월요일, 6=일요일)")
        df = pd.read_sql(text("""
            SELECT EXTRACT(DOW FROM date) AS dow,
                   AVG(boarding) AS avg_b, AVG(alighting) AS avg_a,
                   COUNT(DISTINCT date) AS n_days
            FROM stcis_boarding_raw
            GROUP BY dow ORDER BY dow
        """), c)
        dow_map = {0: "일", 1: "월", 2: "화", 3: "수", 4: "목", 5: "금", 6: "토"}
        df["요일"] = df["dow"].astype(int).map(dow_map)
        df = df[["요일", "n_days", "avg_b", "avg_a"]]
        print(df.to_string(index=False))

        print("\n" + "=" * 65)
        print("[5] 날짜별 총합 (튀는 날 체크)")
        df = pd.read_sql(text("""
            SELECT date,
                   SUM(boarding) AS total_b,
                   SUM(alighting) AS total_a
            FROM stcis_boarding_raw
            GROUP BY date ORDER BY date
        """), c)
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d(%a)")
        print(df.to_string(index=False))

        print("\n" + "=" * 65)
        print("[6] boarding_data ↔ STCIS raw 정합성")
        check = c.execute(text("""
            SELECT
                (SELECT SUM(boarding)  FROM stcis_boarding_raw) AS raw_b,
                (SELECT SUM(alighting) FROM stcis_boarding_raw) AS raw_a,
                (SELECT SUM(boarding)  FROM boarding_data)      AS bd_b,
                (SELECT SUM(alighting) FROM boarding_data)      AS bd_a
        """)).one()
        print(f"  raw 승차={check[0]:,}  하차={check[1]:,}")
        print(f"  bd  승차={float(check[2]):,.2f}  하차={float(check[3]):,.2f}")
        if abs(float(check[0]) - float(check[2])) < 1 and abs(float(check[1]) - float(check[3])) < 1:
            print("  ✅ 일치 (더블카운팅 보정 OK)")
        else:
            print("  ⚠️ 불일치 — 검토 필요")

        print("\n" + "=" * 65)
        print("[7] 매핑 그룹 분포")
        df = pd.read_sql(text("""
            SELECT n_tago_in_grp, COUNT(DISTINCT stop_id) AS n_stops
            FROM boarding_data
            GROUP BY n_tago_in_grp ORDER BY n_tago_in_grp
        """), c)
        print(df.to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
