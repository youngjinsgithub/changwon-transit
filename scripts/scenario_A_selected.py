# =============================================================================
# 시나리오 A — 선택 노선 지도 (체크박스 ON/OFF)
# 표시 노선: BRT일반(5000), 100, 122
# =============================================================================

import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import folium
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

u    = os.environ["POSTGRES_USER"]
p    = os.environ["POSTGRES_PASSWORD"]
h    = os.environ.get("POSTGRES_HOST", "localhost")
port = os.environ.get("POSTGRES_PORT", "5432")
d    = os.environ["POSTGRES_DB"]
engine = create_engine(
    f"postgresql+psycopg2://{u}:{p}@{h}:{port}/{d}",
    pool_pre_ping=True, future=True
)

# 표시할 버스번호 목록 (순서 = 범례 순서)
TARGET_BUS_NOS = ["BRT일반(5000)", "100", "122"]

# 노선별 색상
COLOR_MAP = {
    "BRT일반(5000)": "#e63946",   # 빨강
    "100":           "#2a9d8f",   # 청록
    "122":           "#6a4c93",   # 보라
}

# =============================================================================
# [0] TAGO API — routeid ↔ routeno 매핑
# =============================================================================
print("[0] TAGO API 조회 중...")
api_key = os.environ.get("TAGO_API_KEY", "")
tago_url = (
    "https://apis.data.go.kr/1613000/BusRouteInfoInqireService/getRouteNoList"
    f"?serviceKey={urllib.parse.quote(api_key, safe='')}"
    "&cityCode=38010&numOfRows=1000&pageNo=1"
)
routeno_map  = {}   # routeid  → routeno
routeid_map  = {}   # routeno  → routeid
try:
    with urllib.request.urlopen(tago_url, timeout=15) as resp:
        root = ET.fromstring(resp.read())
    for item in root.findall(".//item"):
        rid = (item.findtext("routeid") or "").strip()
        rno = (item.findtext("routeno") or "").strip()
        if rid and rno:
            routeno_map[rid] = rno
            routeid_map[rno] = rid
    print(f"    {len(routeno_map)}개 수집")
except Exception as e:
    print(f"    실패: {e}")

# 대상 routeid 목록
target_ids = [routeid_map[n] for n in TARGET_BUS_NOS if n in routeid_map]
print(f"    대상 route_no: {target_ids}")

# =============================================================================
# [1] 시나리오 A 결과 CSV 로드 (점수·지표)
# =============================================================================
csv_path = PROJECT_ROOT / "data/processed/scenario_A_result.csv"
df_score = pd.read_csv(csv_path)
df_score["bus_no"] = df_score["route_no"].map(routeno_map).fillna(df_score["route_no"])
df_target = df_score[df_score["bus_no"].isin(TARGET_BUS_NOS)].copy()

# =============================================================================
# [2] DB — 정류장 좌표 조회
# =============================================================================
print("[1] 정류장 좌표 조회 중...")
with engine.connect() as conn:
    df_coords = pd.read_sql(
        text("""
            SELECT r.route_no,
                   rs.direction,
                   rs.sequence,
                   s.latitude,
                   s.longitude,
                   s.stop_name
            FROM routes r
            JOIN route_stops rs ON rs.route_id = r.route_id
            JOIN stops s ON s.stop_id = rs.stop_id
            WHERE r.route_no = ANY(:ids)
              AND s.latitude IS NOT NULL
            ORDER BY r.route_no, rs.direction, rs.sequence
        """).bindparams(ids=target_ids),
        conn
    )

# =============================================================================
# [3] folium 지도 생성 (FeatureGroup + LayerControl)
# =============================================================================
print("[2] 지도 생성 중...")
m = folium.Map(location=[35.23, 128.68], zoom_start=12, tiles="CartoDB positron")

for bus_no in TARGET_BUS_NOS:
    rid = routeid_map.get(bus_no)
    if not rid:
        print(f"    {bus_no}번 routeid 없음, 스킵")
        continue

    color = COLOR_MAP.get(bus_no, "#333333")

    # 이 노선의 점수 정보
    score_row = df_target[df_target["bus_no"] == bus_no]
    score     = round(score_row["priority_score"].values[0], 4) if len(score_row) else "N/A"
    boarding  = int(score_row["passenger_count"].values[0]) if len(score_row) else 0
    pop       = int(score_row["population"].values[0]) if len(score_row) else 0
    vuln      = round(score_row["vulnerability_index"].values[0], 3) if len(score_row) else 0
    rank      = int(score_row["rank"].values[0]) if len(score_row) else "-"

    # FeatureGroup = 체크박스 한 칸 (show=True → 기본 활성)
    fg = folium.FeatureGroup(name=f"{bus_no}번  (점수 {score})", show=True)

    route_df = df_coords[df_coords["route_no"] == rid]
    dirs = route_df["direction"].unique()
    use_dir = "상행" if "상행" in dirs else dirs[0]
    coords = (
        route_df[route_df["direction"] == use_dir]
        .sort_values("sequence")[["latitude", "longitude"]]
        .values.tolist()
    )
    if len(coords) < 2:
        coords = route_df.sort_values("sequence")[["latitude", "longitude"]].values.tolist()

    # 정류장 점 표시 (전 노선)
    if bus_no in TARGET_BUS_NOS:
        stop_df = route_df[route_df["direction"] == use_dir].sort_values("sequence")
        if len(stop_df) < 2:
            stop_df = route_df.sort_values("sequence")
        for _, srow in stop_df.drop_duplicates(subset=["latitude","longitude"]).iterrows():
            sname = srow.get("stop_name", "")
            folium.CircleMarker(
                location=[srow["latitude"], srow["longitude"]],
                radius=5,
                color="white",
                fill=True,
                fill_color=color,
                fill_opacity=0.95,
                weight=1.5,
                tooltip=folium.Tooltip(
                    f"<b>{sname}</b>",
                    style="font-size:13px;padding:4px 8px"
                ),
                popup=folium.Popup(
                    f"<b>{sname}</b><br>"
                    f"<span style='color:#888;font-size:11px'>{bus_no}번 정류장</span>",
                    max_width=200
                )
            ).add_to(fg)

    if len(coords) >= 2:
        folium.PolyLine(
            coords,
            color=color,
            weight=6,
            opacity=0.9,
            tooltip=f"{bus_no}번 | 시나리오A 점수: {score}",
            popup=folium.Popup(
                f"<b>{bus_no}번 버스</b><br>"
                f"시나리오A 순위: <b>#{rank}</b><br>"
                f"종합점수: <b>{score}</b><br>"
                f"승차량: {boarding:,}<br>"
                f"인구: {pop:,}<br>"
                f"소외도: {vuln}",
                max_width=220
            )
        ).add_to(fg)

    # 기점 마커
    if coords:
        folium.Marker(
            coords[0],
            icon=folium.DivIcon(
                html=(
                    f'<div style="background:{color};color:white;'
                    f'border-radius:4px;padding:2px 6px;'
                    f'font-weight:bold;font-size:12px;white-space:nowrap;'
                    f'border:2px solid white;box-shadow:1px 1px 3px rgba(0,0,0,0.4)">'
                    f'{bus_no}번</div>'
                ),
                icon_size=(52, 24),
                icon_anchor=(26, 12)
            ),
            tooltip=f"{bus_no}번 기점"
        ).add_to(fg)

    fg.add_to(m)

# 체크박스 컨트롤 (우측 상단)
folium.LayerControl(position="topright", collapsed=False).add_to(m)

# 범례
legend = """
<div style="position:fixed;bottom:30px;left:30px;z-index:1000;
     background:white;padding:12px 16px;border-radius:8px;
     box-shadow:2px 2px 8px rgba(0,0,0,0.3);font-size:13px">
  <b>시나리오 A 선택 노선</b><br>
  <span style="color:#888;font-size:11px">우측 상단 체크박스로 ON/OFF</span><br><br>
"""
for bus_no in TARGET_BUS_NOS:
    c = COLOR_MAP.get(bus_no, "#333")
    score_row = df_target[df_target["bus_no"] == bus_no]
    score = round(score_row["priority_score"].values[0], 4) if len(score_row) else "N/A"
    rank  = int(score_row["rank"].values[0]) if len(score_row) else "-"
    legend += (
        f'<div style="margin:4px 0;display:flex;align-items:center;gap:6px">'
        f'<span style="background:{c};display:inline-block;'
        f'width:28px;height:5px;border-radius:3px"></span>'
        f'<b>{bus_no}번</b>'
        f'<span style="color:#666"> 순위#{rank} · {score}점</span></div>\n'
    )
legend += "</div>"
m.get_root().html.add_child(folium.Element(legend))

out = PROJECT_ROOT / "data/processed/maps/scenario_A_top3.html"
out.parent.mkdir(parents=True, exist_ok=True)
m.save(str(out))
print(f"[3] 저장 완료: {out}")
