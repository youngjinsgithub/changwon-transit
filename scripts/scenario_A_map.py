# =============================================================================
# 창원시 버스 노선 개편 우선순위 — 시나리오 A (실제 DB 데이터 + 지도 시각화)
#
# 대리지표 처리 (DB에 없는 항목):
#   traffic_volume   → 정류장 총 승차량으로 대체 (교통 수요 비례)
#   headway_minutes  → 해당 노선의 행정동당 경쟁 노선 수 역산으로 대체
#                      (경쟁 노선이 적을수록 배차간격이 긴 것으로 간주)
# =============================================================================

import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import folium
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

u = os.environ["POSTGRES_USER"]
p = os.environ["POSTGRES_PASSWORD"]
h = os.environ.get("POSTGRES_HOST", "localhost")
port = os.environ.get("POSTGRES_PORT", "5432")
d = os.environ["POSTGRES_DB"]
engine = create_engine(
    f"postgresql+psycopg2://{u}:{p}@{h}:{port}/{d}",
    pool_pre_ping=True, future=True
)

# =============================================================================
# [0] TAGO API 에서 실제 버스 번호 조회
# =============================================================================
print("[0] TAGO API 버스 번호 조회 중...")
api_key = os.environ.get("TAGO_API_KEY", "")
tago_url = (
    "https://apis.data.go.kr/1613000/BusRouteInfoInqireService/getRouteNoList"
    f"?serviceKey={urllib.parse.quote(api_key, safe='')}"
    "&cityCode=38010&numOfRows=1000&pageNo=1"
)
routeno_map = {}
try:
    with urllib.request.urlopen(tago_url, timeout=15) as resp:
        root = ET.fromstring(resp.read())
    for item in root.findall(".//item"):
        rid = (item.findtext("routeid") or "").strip()
        rno = (item.findtext("routeno") or "").strip()
        if rid and rno:
            routeno_map[rid] = rno
    print(f"    {len(routeno_map)}개 노선번호 수집")
except Exception as e:
    print(f"    API 실패: {e}")

# =============================================================================
# [1] DB 에서 노선별 지표 수집
# =============================================================================
print("[1] DB 데이터 수집 중...")
with engine.connect() as conn:

    # 노선별 총 승차량 (traffic_volume 대리지표)
    df_board = pd.read_sql(text("""
        SELECT r.route_no,
               SUM(bd.boarding) AS passenger_count
        FROM routes r
        JOIN route_stops rs ON rs.route_id = r.route_id
        JOIN stops s ON s.stop_id = rs.stop_id
        JOIN boarding_data bd ON bd.stop_id = s.stop_id
        GROUP BY r.route_no
    """), conn)

    # 노선별 경유 인구·세대수
    df_pop = pd.read_sql(text("""
        SELECT r.route_no,
               COUNT(DISTINCT d.district_id)  AS n_districts,
               SUM(DISTINCT d.population)     AS population,
               SUM(DISTINCT d.households)     AS household_count,
               SUM(DISTINCT d.pop_age_65_plus) AS pop_65plus
        FROM routes r
        JOIN route_stops rs ON rs.route_id = r.route_id
        JOIN stops s ON s.stop_id = rs.stop_id
        JOIN districts d ON ST_Within(s.location, d.geometry)
        GROUP BY r.route_no
    """), conn)

    # 행정동별 경쟁 노선 수 (배차간격 대리지표 계산용)
    df_freq = pd.read_sql(text("""
        SELECT r.route_no,
               AVG(sub.routes_per_district) AS avg_routes_per_district
        FROM routes r
        JOIN route_stops rs ON rs.route_id = r.route_id
        JOIN stops s ON s.stop_id = rs.stop_id
        JOIN districts d ON ST_Within(s.location, d.geometry)
        JOIN (
            SELECT d2.district_id,
                   COUNT(DISTINCT r2.route_id) AS routes_per_district
            FROM districts d2
            JOIN stops s2 ON ST_Within(s2.location, d2.geometry)
            JOIN route_stops rs2 ON rs2.stop_id = s2.stop_id
            JOIN routes r2 ON r2.route_id = rs2.route_id
            GROUP BY d2.district_id
        ) sub ON sub.district_id = d.district_id
        GROUP BY r.route_no
    """), conn)

    # 노선별 정류장 좌표 (지도용)
    df_coords = pd.read_sql(text("""
        SELECT r.route_no,
               rs.direction,
               rs.sequence,
               s.latitude,
               s.longitude
        FROM routes r
        JOIN route_stops rs ON rs.route_id = r.route_id
        JOIN stops s ON s.stop_id = rs.stop_id
        WHERE s.latitude IS NOT NULL
        ORDER BY r.route_no, rs.direction, rs.sequence
    """), conn)

# 병합
df = df_board.merge(df_pop, on="route_no", how="inner")
df = df.merge(df_freq, on="route_no", how="left")
df = df.fillna(0)

# 대리지표 컬럼명 맞추기
#   traffic_volume  → passenger_count (동일 값, 교통 수요 비례)
#   headway_minutes → avg_routes_per_district 역산
#                     (경쟁 노선 적을수록 배차간격 긴 것으로 간주)
df["traffic_volume"]  = df["passenger_count"]
df["headway_minutes"] = df["avg_routes_per_district"].max() - df["avg_routes_per_district"]

# 소외도: 고령자 비율 (65+ / 인구) 로 계산
df["vulnerability_index"] = np.where(
    df["population"] > 0,
    df["pop_65plus"] / df["population"],
    0.0
)

print(f"    노선 수: {len(df)}개")

# =============================================================================
# [단계 1] household_count 드롭 (다중공선성 방지)
# =============================================================================
df_bus = df.drop(columns=["household_count", "pop_65plus",
                           "n_districts", "avg_routes_per_district"], errors="ignore")

# =============================================================================
# [단계 2] Min-Max 정규화
# =============================================================================
def minmax_pos(s):
    mn, mx = s.min(), s.max()
    return pd.Series(0.0, index=s.index) if mx == mn else (s - mn) / (mx - mn)

def minmax_neg(s):
    mn, mx = s.min(), s.max()
    return pd.Series(0.0, index=s.index) if mx == mn else (mx - s) / (mx - mn)

df_bus["traffic_volume_scaled"]      = minmax_pos(df_bus["traffic_volume"])
df_bus["passenger_count_scaled"]     = minmax_pos(df_bus["passenger_count"])
df_bus["headway_minutes_scaled"]     = minmax_pos(df_bus["headway_minutes"])
df_bus["vulnerability_index_scaled"] = minmax_pos(df_bus["vulnerability_index"])
df_bus["population_scaled"]          = minmax_neg(df_bus["population"])

# =============================================================================
# [단계 3] 시나리오 A 가중치 결합
# =============================================================================
df_bus["priority_score"] = (
    df_bus["traffic_volume_scaled"]      * 0.30 +
    df_bus["passenger_count_scaled"]     * 0.30 +
    df_bus["headway_minutes_scaled"]     * 0.20 +
    df_bus["vulnerability_index_scaled"] * 0.10 +
    df_bus["population_scaled"]          * 0.10
)

# =============================================================================
# [단계 4] 정렬 + TOP 10 선정
# =============================================================================
df_result = df_bus.sort_values("priority_score", ascending=False).reset_index(drop=True)
df_result["rank"] = df_result.index + 1
df_result["bus_no"] = df_result["route_no"].map(routeno_map).fillna(df_result["route_no"])

top10 = df_result.head(10)
print("\n[시나리오 A] 우선순위 TOP 10")
print(top10[["rank","bus_no","route_no","priority_score",
             "passenger_count","vulnerability_index","population"]].to_string(index=False))

# CSV 저장
csv_path = PROJECT_ROOT / "data/processed/scenario_A_result.csv"
df_result.to_csv(csv_path, index=False, encoding="utf-8-sig")
print(f"\nCSV 저장: {csv_path}")

# =============================================================================
# [단계 5] folium 지도 시각화
# =============================================================================
print("\n[지도 생성 중...]")
COLORS = [
    "#e63946","#f4a261","#2a9d8f","#457b9d","#6a4c93",
    "#e76f51","#264653","#2196f3","#ff5722","#607d8b"
]

m = folium.Map(location=[35.23, 128.68], zoom_start=12, tiles="CartoDB positron")

for i, row in top10.iterrows():
    rno   = row["route_no"]
    rank  = row["rank"]
    bno   = row["bus_no"]
    score = round(row["priority_score"], 4)
    color = COLORS[i % len(COLORS)]

    route_df = df_coords[df_coords["route_no"] == rno]
    if route_df.empty:
        continue

    dirs = route_df["direction"].unique()
    use_dir = "상행" if "상행" in dirs else dirs[0]
    coords = (
        route_df[route_df["direction"] == use_dir]
        .sort_values("sequence")[["latitude","longitude"]]
        .values.tolist()
    )
    if len(coords) < 2:
        coords = route_df.sort_values("sequence")[["latitude","longitude"]].values.tolist()

    folium.PolyLine(
        coords, color=color, weight=5, opacity=0.85,
        tooltip=f"#{rank} {bno}번 | 점수: {score}",
        popup=folium.Popup(
            f"<b>#{rank} {bno}번</b><br>"
            f"TAGO ID: {rno}<br>"
            f"종합점수: <b>{score}</b><br>"
            f"승차량: {int(row['passenger_count']):,}<br>"
            f"인구: {int(row['population']):,}<br>"
            f"소외도: {row['vulnerability_index']:.3f}",
            max_width=240
        )
    ).add_to(m)

    if coords:
        folium.Marker(
            coords[0],
            icon=folium.DivIcon(
                html=(
                    f'<div style="background:{color};color:white;border-radius:50%;'
                    f'width:26px;height:26px;text-align:center;line-height:26px;'
                    f'font-weight:bold;font-size:12px;border:2px solid white;'
                    f'box-shadow:1px 1px 3px rgba(0,0,0,0.4)">#{rank}</div>'
                ),
                icon_size=(26,26), icon_anchor=(13,13)
            ),
            tooltip=f"#{rank} {bno}번"
        ).add_to(m)

# 범례
legend = """
<div style="position:fixed;bottom:30px;left:30px;z-index:1000;
     background:white;padding:12px 16px;border-radius:8px;
     box-shadow:2px 2px 8px rgba(0,0,0,0.3);font-size:13px;min-width:240px">
  <b>시나리오 A 우선순위 TOP 10</b><br>
  <span style="color:#888;font-size:11px">교통량30%·수요30%·배차20%·소외10%·인구역산10%</span><br><br>
"""
for i, row in top10.iterrows():
    c = COLORS[i % len(COLORS)]
    legend += (
        f'<div style="margin:3px 0">'
        f'<span style="background:{c};color:white;border-radius:3px;'
        f'padding:1px 6px;font-weight:bold">#{row["rank"]}</span> '
        f'<b>{row["bus_no"]}번</b> '
        f'<span style="color:#666">({round(row["priority_score"],3)}점)</span></div>\n'
    )
legend += "</div>"
m.get_root().html.add_child(folium.Element(legend))

out = PROJECT_ROOT / "data/processed/maps/scenario_A_map.html"
out.parent.mkdir(parents=True, exist_ok=True)
m.save(str(out))
print(f"지도 저장 완료: {out}")
