"""TOP 10 우선순위 노선을 folium 지도로 시각화."""

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

url = (
    f"postgresql+psycopg2://"
    f"{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ.get('POSTGRES_HOST','localhost')}:{os.environ.get('POSTGRES_PORT','5432')}"
    f"/{os.environ['POSTGRES_DB']}"
)
engine = create_engine(url, pool_pre_ping=True, future=True)

# TAGO API에서 routeno(실제 버스 번호) 조회
print("[0] TAGO API에서 실제 버스 번호 조회 중...")
api_key = os.environ.get("TAGO_API_KEY", "")
tago_url = (
    "https://apis.data.go.kr/1613000/BusRouteInfoInqireService/getRouteNoList"
    f"?serviceKey={urllib.parse.quote(api_key, safe='')}&cityCode=38010&numOfRows=1000&pageNo=1"
)
try:
    with urllib.request.urlopen(tago_url, timeout=15) as resp:
        xml_data = resp.read()
    root = ET.fromstring(xml_data)
    routeno_map = {}
    for item in root.findall(".//item"):
        rid = (item.findtext("routeid") or "").strip()
        rno = (item.findtext("routeno") or "").strip()
        if rid and rno:
            routeno_map[rid] = rno
    print(f"    노선번호 {len(routeno_map)}개 수집 완료")
except Exception as e:
    print(f"    API 호출 실패: {e} -> 내부 ID 사용")
    routeno_map = {}

# TOP 10 CSV 로드
top10 = pd.read_csv(PROJECT_ROOT / "data/processed/priority_top10.csv")
top10_ids = list(top10["노선번호"])

# 실제 버스 번호 컬럼 추가
top10["버스번호"] = top10["노선번호"].map(routeno_map).fillna(top10["노선번호"])
print("    TOP10 버스번호:", list(top10["버스번호"]))

# 노선별 정류장 좌표 (순서대로)
print("[1] 노선 좌표 조회 중...")
with engine.connect() as conn:
    rows = conn.execute(text("""
        SELECT
            r.route_no,
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
    """), {"ids": top10_ids}).fetchall()

df = pd.DataFrame(rows, columns=["route_no","direction","sequence","lat","lon","stop_name"])

# 색상 (순위별)
COLORS = [
    "#e63946","#f4a261","#2a9d8f","#457b9d","#6a4c93",
    "#e76f51","#264653","#2196f3","#ff5722","#607d8b"
]

print("[2] 지도 생성 중...")
m = folium.Map(location=[35.23, 128.68], zoom_start=12, tiles="CartoDB positron")

for i, row in top10.iterrows():
    rno = row["노선번호"]
    rank = row["순위"]
    name = row["노선명"]
    bus_no = row["버스번호"]
    score = row["종합점수"]
    boarding = row["총승차량"]
    pop = row["인구합계"]
    color = COLORS[i % len(COLORS)]

    route_df = df[df["route_no"] == rno]
    if route_df.empty:
        continue

    # 상행만 사용 (중복 방지)
    dirs = route_df["direction"].unique()
    use_dir = "상행" if "상행" in dirs else dirs[0]
    coords = route_df[route_df["direction"] == use_dir].sort_values("sequence")[["lat","lon"]].values.tolist()

    if len(coords) < 2:
        coords = route_df.sort_values("sequence")[["lat","lon"]].values.tolist()

    # 노선 라인
    folium.PolyLine(
        coords,
        color=color,
        weight=5,
        opacity=0.85,
        tooltip=f"#{rank} {bus_no}번 {name} | 종합점수: {score}",
        popup=folium.Popup(
            f"""<b>#{rank} {bus_no}번 | {name}</b><br>
            TAGO ID: {rno}<br>
            종합점수: <b>{score}</b><br>
            총승차량: {boarding}<br>
            인구합계: {pop}<br>
            행정동수: {row['행정동수']}개<br>
            정류장수: {row['정류장수']}개<br>
            <hr>
            인구점수: {row['인구점수']} | 교통량점수: {row['교통량점수']}<br>
            세대수점수: {row['세대수점수']} | 소외도점수: {row['소외도점수']}
            """,
            max_width=280
        )
    ).add_to(m)

    # 기점 마커
    if coords:
        folium.Marker(
            coords[0],
            icon=folium.DivIcon(
                html=f'<div style="background:{color};color:white;border-radius:50%;'
                     f'width:26px;height:26px;text-align:center;line-height:26px;'
                     f'font-weight:bold;font-size:13px;border:2px solid white;'
                     f'box-shadow:1px 1px 3px rgba(0,0,0,0.4)">#{rank}</div>',
                icon_size=(26, 26),
                icon_anchor=(13, 13)
            ),
            tooltip=f"#{rank} {bus_no}번 {name}"
        ).add_to(m)

# 범례
legend_html = """
<div style="position:fixed;bottom:30px;left:30px;z-index:1000;
     background:white;padding:12px 16px;border-radius:8px;
     box-shadow:2px 2px 8px rgba(0,0,0,0.3);font-size:13px;min-width:220px">
  <b>창원 버스 개편 우선순위 TOP 10</b><br>
  <span style="color:#888;font-size:11px">가중치: 인구25% | 교통량35% | 세대수15% | 소외도25%</span><br><br>
"""
for i, row in top10.iterrows():
    color = COLORS[i % len(COLORS)]
    bus_no = row["버스번호"]
    legend_html += (
        f'<div style="margin:3px 0">'
        f'<span style="background:{color};color:white;border-radius:3px;'
        f'padding:1px 6px;font-weight:bold">#{row["순위"]}</span> '
        f'<b>{bus_no}번</b> {row["노선명"][:18]} '
        f'<span style="color:#666">({row["종합점수"]}점)</span></div>\n'
    )
legend_html += "</div>"
m.get_root().html.add_child(folium.Element(legend_html))

out = PROJECT_ROOT / "data/processed/maps/priority_top10_map.html"
out.parent.mkdir(parents=True, exist_ok=True)
m.save(str(out))
size_kb = out.stat().st_size // 1024
print(f"[3] 저장 완료: {out}  ({size_kb} KB)")
