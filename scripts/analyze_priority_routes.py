"""
창원 버스 개편 우선순위 노선 선정
가중치 기준:
  1. 인구 (population)       - 많을수록 높은 점수
  2. 교통량 (boarding)       - 많을수록 높은 점수
  3. 세대수 (households)     - 많을수록 높은 점수 (차량보유 대리지표)
  4. 버스 운행 빈도 (routes) - 적을수록 높은 점수 (소외 지역)
"""

import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

url = (
    f"postgresql+psycopg2://"
    f"{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ.get('POSTGRES_HOST','localhost')}:{os.environ.get('POSTGRES_PORT','5432')}"
    f"/{os.environ['POSTGRES_DB']}"
)
engine = create_engine(url, pool_pre_ping=True, future=True)

with engine.connect() as conn:
    # 노선별 경유 행정동 → 인구 합산
    pop_sql = text("""
        SELECT
            r.route_id,
            r.route_no,
            r.route_name,
            r.route_type,
            COUNT(DISTINCT rs.stop_id)          AS n_stops,
            COUNT(DISTINCT d.district_id)        AS n_districts,
            SUM(DISTINCT d.population)           AS total_population,
            SUM(DISTINCT d.households)           AS total_households
        FROM routes r
        JOIN route_stops rs ON rs.route_id = r.route_id
        JOIN stops s ON s.stop_id = rs.stop_id
        JOIN districts d ON ST_Within(s.location, d.geometry)
        GROUP BY r.route_id, r.route_no, r.route_name, r.route_type
    """)
    df_pop = pd.read_sql(pop_sql, conn)

    # 노선별 총 승차량 (boarding_data 집계)
    board_sql = text("""
        SELECT
            r.route_id,
            SUM(bd.boarding) AS total_boarding
        FROM routes r
        JOIN route_stops rs ON rs.route_id = r.route_id
        JOIN stops s ON s.stop_id = rs.stop_id
        JOIN boarding_data bd ON bd.stop_id = s.stop_id
        GROUP BY r.route_id
    """)
    df_board = pd.read_sql(board_sql, conn)

    # 행정동별 경유 노선 수 (버스 운행 빈도 계산용)
    freq_sql = text("""
        SELECT
            r.route_id,
            COUNT(DISTINCT d.district_id) AS n_districts_served,
            AVG(sub.routes_per_district)  AS avg_routes_per_district
        FROM routes r
        JOIN route_stops rs ON rs.route_id = r.route_id
        JOIN stops s ON s.stop_id = rs.stop_id
        JOIN districts d ON ST_Within(s.location, d.geometry)
        JOIN (
            SELECT d2.district_id, COUNT(DISTINCT r2.route_id) AS routes_per_district
            FROM districts d2
            JOIN stops s2 ON ST_Within(s2.location, d2.geometry)
            JOIN route_stops rs2 ON rs2.stop_id = s2.stop_id
            JOIN routes r2 ON r2.route_id = rs2.route_id
            GROUP BY d2.district_id
        ) sub ON sub.district_id = d.district_id
        GROUP BY r.route_id
    """)
    df_freq = pd.read_sql(freq_sql, conn)

# 병합
df = df_pop.merge(df_board, on="route_id", how="left")
df = df.merge(df_freq, on="route_id", how="left")
df = df.fillna(0)

# 정규화 (0~100)
def normalize(series, ascending=True):
    mn, mx = series.min(), series.max()
    if mx == mn:
        return pd.Series([50.0] * len(series), index=series.index)
    norm = (series - mn) / (mx - mn) * 100
    return norm if ascending else 100 - norm

df["score_pop"]      = normalize(df["total_population"],        ascending=True)
df["score_boarding"] = normalize(df["total_boarding"],          ascending=True)
df["score_household"]= normalize(df["total_households"],        ascending=True)
df["score_freq"]     = normalize(df["avg_routes_per_district"], ascending=False)  # 적을수록 높음

# 가중 합산 (각 25%)
W_POP, W_BOARD, W_HOUSE, W_FREQ = 0.25, 0.35, 0.15, 0.25
df["total_score"] = (
    df["score_pop"]       * W_POP   +
    df["score_boarding"]  * W_BOARD +
    df["score_household"] * W_HOUSE +
    df["score_freq"]      * W_FREQ
)

df = df.sort_values("total_score", ascending=False).reset_index(drop=True)
df["rank"] = df.index + 1

# 출력
top10 = df.head(10)[[
    "rank", "route_no", "route_name", "route_type",
    "n_stops", "n_districts",
    "total_population", "total_boarding",
    "score_pop", "score_boarding", "score_household", "score_freq",
    "total_score"
]].copy()

top10.columns = [
    "순위", "노선번호", "노선명", "노선유형",
    "정류장수", "행정동수",
    "인구합계", "총승차량",
    "인구점수", "교통량점수", "세대수점수", "소외도점수",
    "종합점수"
]

top10["인구합계"]  = top10["인구합계"].apply(lambda x: f"{int(x):,}")
top10["총승차량"]  = top10["총승차량"].apply(lambda x: f"{x:,.0f}")
top10["인구점수"]  = top10["인구점수"].round(1)
top10["교통량점수"]= top10["교통량점수"].round(1)
top10["세대수점수"]= top10["세대수점수"].round(1)
top10["소외도점수"]= top10["소외도점수"].round(1)
top10["종합점수"]  = top10["종합점수"].round(2)

print("\n===== 창원 버스 개편 우선순위 TOP 10 =====")
print(f"가중치: 인구 {W_POP*100:.0f}% | 교통량 {W_BOARD*100:.0f}% | 세대수 {W_HOUSE*100:.0f}% | 소외도 {W_FREQ*100:.0f}%\n")
print(top10.to_string(index=False))

# CSV 저장
out_path = "data/processed/priority_top10.csv"
top10.to_csv(out_path, index=False, encoding="utf-8-sig")
print(f"\n저장 완료: {out_path}")
