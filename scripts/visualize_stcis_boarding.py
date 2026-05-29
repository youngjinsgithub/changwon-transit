"""STCIS 수집 승하차 데이터 지도 시각화.

12개 우선순위 노선 정류장 위에 boarding_data 의 승하차 통계를 매핑.
시범 수집 단계에서는 일부 정류장만 데이터 있음 → 데이터 있는 stop 만 강조.

출력: data/processed/maps/stcis_boarding_map.html

마커:
  - 데이터 있는 stop: 원 크기 ∝ 총 승차, 색 = 승하차 비율
  - 데이터 없는 stop: 옅은 회색
  - 클릭 → 일별·시간대 요약 + n_sttn_ids / n_tago_in_grp
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import folium  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402

PRIORITY_CSV = PROJECT_ROOT / "data" / "processed" / "priority_routes_12.csv"
OUT = PROJECT_ROOT / "data" / "processed" / "maps" / "stcis_boarding_map.html"

CHANGWON_CENTER = (35.230, 128.650)


def color_by_intensity(boarding: float, max_b: float) -> str:
    """승차 강도 → 색상. 0=옅은 노랑 → 최대=진한 빨강."""
    if max_b == 0:
        return "#bbb"
    ratio = min(boarding / max_b, 1.0)
    # HSL: 60(yellow) → 0(red), saturation 100%, lightness 50%
    hue = int(60 * (1 - ratio))
    return f"hsl({hue}, 80%, 50%)"


def radius_by_boarding(boarding: float, max_b: float) -> float:
    """승차 총량 → 마커 반경. min 4 ~ max 18."""
    if max_b == 0:
        return 4
    import math
    # sqrt 스케일로 너무 큰 차이 완화
    return 4 + 14 * math.sqrt(min(boarding / max_b, 1.0))


def main() -> int:
    print("[1] 데이터 로드")
    picked = pd.read_csv(PRIORITY_CSV, encoding="utf-8-sig")
    engine = get_engine()
    with engine.connect() as conn:
        routes = pd.read_sql(
            text("SELECT route_id, route_no, route_type "
                 "FROM routes WHERE route_no = ANY(:rs)"),
            conn, params={"rs": picked["route_no"].astype(str).tolist()},
        )
        route_stops = pd.read_sql(
            text("SELECT route_id, stop_id, sequence, direction "
                 "FROM route_stops WHERE route_id = ANY(:ids) "
                 "ORDER BY route_id, direction, sequence"),
            conn, params={"ids": routes["route_id"].tolist()},
        )
        stops = pd.read_sql(
            text("SELECT s.stop_id, s.stop_name, s.bis_number, "
                 "       s.latitude, s.longitude, "
                 "       d.sigungu, d.district_name "
                 "FROM stops s "
                 "LEFT JOIN districts d ON ST_Contains(d.geometry, s.location) "
                 "WHERE s.stop_id = ANY(:sids)"),
            conn, params={"sids": route_stops["stop_id"].unique().tolist()},
        )
        # boarding_data 집계 (TAGO stop_id 단위)
        boarding = pd.read_sql(
            text("""
                SELECT
                    stop_id,
                    SUM(boarding)  AS total_boarding,
                    SUM(alighting) AS total_alighting,
                    AVG(boarding)  AS avg_hour_boarding,
                    MAX(n_sttn_ids) AS n_sttn_ids,
                    MAX(n_tago_in_grp) AS n_tago_in_grp,
                    MIN(date) AS d_min, MAX(date) AS d_max
                FROM boarding_data
                GROUP BY stop_id
            """),
            conn,
        )
        # 시간대별 평균 (정류장×시간)
        hourly = pd.read_sql(
            text("""
                SELECT stop_id, hour, AVG(boarding) AS avg_b
                FROM boarding_data
                GROUP BY stop_id, hour
                ORDER BY stop_id, hour
            """),
            conn,
        )
        districts_geojson = conn.execute(
            text("""
                SELECT json_build_object('type','FeatureCollection',
                    'features', json_agg(json_build_object(
                        'type','Feature',
                        'properties', json_build_object(
                            'district_name', district_name, 'sigungu', sigungu),
                        'geometry', ST_AsGeoJSON(geometry)::json)))
                FROM districts
            """)
        ).scalar()

    print(f"    데이터 있는 정류장: {len(boarding)} / 전체 {len(stops)}")

    # stops + boarding 병합
    full = stops.merge(boarding, on="stop_id", how="left")
    full["has_data"] = full["total_boarding"].notna()

    if not full["has_data"].any():
        print("[!] boarding_data 가 비어있음. 수집 먼저 진행")
        return 1

    max_b = float(full["total_boarding"].max())

    # 시간대별 데이터 dict 화
    hourly_by_stop: dict[str, list[float]] = {}
    for stop_id, grp in hourly.groupby("stop_id"):
        # 0~23 시 채우기 (없으면 0)
        h_map = dict(zip(grp["hour"].astype(int), grp["avg_b"].astype(float)))
        hourly_by_stop[stop_id] = [h_map.get(h, 0.0) for h in range(24)]

    print("[2] 지도 생성")
    m = folium.Map(location=CHANGWON_CENTER, zoom_start=11, tiles="OpenStreetMap",
                   control_scale=True)

    # 행정동 배경
    folium.GeoJson(
        districts_geojson, name="행정동 경계",
        style_function=lambda f: {"fillColor": "#aaaaaa", "color": "#444444",
                                   "weight": 1, "fillOpacity": 0.05},
        tooltip=folium.GeoJsonTooltip(
            fields=["sigungu", "district_name"], aliases=["구:", "동:"]),
    ).add_to(m)

    # 노선 폴리라인 (회색 배경)
    coords = stops.set_index("stop_id")[["latitude", "longitude"]]
    routes_grp = folium.FeatureGroup(name="12개 노선 (배경)", show=True)
    for route_id, sub in route_stops.groupby("route_id"):
        for direction, ssub in sub.groupby("direction"):
            seq = ssub.sort_values("sequence")["stop_id"].tolist()
            path = [(coords.loc[s, "latitude"], coords.loc[s, "longitude"])
                    for s in seq if s in coords.index]
            if len(path) >= 2:
                folium.PolyLine(path, color="#888", weight=2, opacity=0.4).add_to(routes_grp)
    routes_grp.add_to(m)

    # 데이터 없는 정류장 (배경)
    nodata_grp = folium.FeatureGroup(
        name=f"데이터 미수집 정류장 ({(~full['has_data']).sum()})", show=True)
    for _, r in full[~full["has_data"]].iterrows():
        folium.CircleMarker(
            location=(r["latitude"], r["longitude"]),
            radius=2.5, color="#bbb", weight=0.5,
            fill=True, fill_color="#ddd", fill_opacity=0.5,
            tooltip=r["stop_name"],
        ).add_to(nodata_grp)
    nodata_grp.add_to(m)

    # 데이터 있는 정류장 (강조)
    data_grp = folium.FeatureGroup(
        name=f"⭐ 데이터 수집 정류장 ({full['has_data'].sum()})", show=True)
    for _, r in full[full["has_data"]].iterrows():
        b_total = float(r["total_boarding"])
        a_total = float(r["total_alighting"])
        radius = radius_by_boarding(b_total, max_b)
        color = color_by_intensity(b_total, max_b)

        # 시간대 미니 막대 (텍스트)
        hours = hourly_by_stop.get(r["stop_id"], [0] * 24)
        max_h = max(hours) if hours else 1
        bar_html = ""
        for h, v in enumerate(hours):
            bar_h = int((v / max_h) * 30) if max_h else 0
            bar_html += (
                f'<div style="display:inline-block;width:10px;height:{bar_h+1}px;'
                f'background:#3498db;vertical-align:bottom;margin-right:1px;"></div>'
            )
        hour_labels = "".join(
            f'<span style="display:inline-block;width:11px;font-size:8px;'
            f'text-align:center;color:#888;">{h if h % 3 == 0 else ""}</span>'
            for h in range(24)
        )

        sigungu = r["sigungu"] if pd.notna(r["sigungu"]) else "(미상)"
        dong = r["district_name"] if pd.notna(r["district_name"]) else "(미상)"
        ars = r["bis_number"] if pd.notna(r["bis_number"]) else "—"

        popup_html = (
            f"<div style='min-width:340px;'>"
            f"<div style='font-weight:bold;font-size:14px;'>{r['stop_name']}</div>"
            f"<div style='color:#666;font-size:12px;margin-top:2px;'>"
            f"{sigungu} · {dong} · ARS {ars}</div>"
            f"<div style='font-size:12px;margin-top:8px;'>"
            f"<b>기간:</b> {r['d_min']} ~ {r['d_max']}</div>"
            f"<div style='font-size:12px;'>"
            f"<b>총 승차:</b> <span style='color:#e74c3c;'>{int(b_total):,}</span> · "
            f"<b>총 하차:</b> <span style='color:#3498db;'>{int(a_total):,}</span></div>"
            f"<div style='font-size:12px;'>"
            f"<b>시간당 평균 승차:</b> {float(r['avg_hour_boarding']):.2f}</div>"
            f"<div style='font-size:11px;color:#666;'>"
            f"매핑: STCIS sttn {int(r['n_sttn_ids'])}개 · "
            f"그룹 크기 {int(r['n_tago_in_grp'])} (TAGO 정류장 수)</div>"
            f"<div style='margin-top:8px;font-weight:bold;font-size:12px;'>"
            f"시간대별 평균 승차 (00~23시)</div>"
            f"<div style='border-bottom:1px solid #ccc;padding-bottom:2px;'>{bar_html}</div>"
            f"<div>{hour_labels}</div>"
            f"</div>"
        )

        folium.CircleMarker(
            location=(r["latitude"], r["longitude"]),
            radius=radius, color=color, weight=2,
            fill=True, fill_color=color, fill_opacity=0.7,
            tooltip=f"{r['stop_name']} (승차 {int(b_total):,})",
            popup=folium.Popup(popup_html, max_width=400),
        ).add_to(data_grp)
    data_grp.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # 범례
    legend = f"""
    <div style="
        position: fixed; top: 12px; right: 12px; z-index: 9999;
        background: white; padding: 10px 14px; border: 1px solid #888;
        border-radius: 6px; font-family: sans-serif; font-size: 13px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15); max-width: 240px;">
        <div style="font-weight:bold;margin-bottom:6px;">STCIS 승차 데이터 (시범)</div>
        <div style="font-size:11px;color:#666;margin-bottom:6px;">
            기간: {full['d_min'].dropna().min()} ~ {full['d_max'].dropna().max()}<br>
            정류장 {int(full['has_data'].sum())}개 / 전체 {len(full)}개
        </div>
        <div style="display:flex;align-items:center;gap:6px;margin:4px 0;">
            <span style="width:8px;height:8px;border-radius:50%;background:hsl(60,80%,50%);"></span>
            <span>낮은 승차</span>
        </div>
        <div style="display:flex;align-items:center;gap:6px;margin:4px 0;">
            <span style="width:14px;height:14px;border-radius:50%;background:hsl(30,80%,50%);"></span>
            <span>중간</span>
        </div>
        <div style="display:flex;align-items:center;gap:6px;margin:4px 0;">
            <span style="width:20px;height:20px;border-radius:50%;background:hsl(0,80%,50%);"></span>
            <span>높은 승차 (최대 {int(max_b):,}명)</span>
        </div>
        <div style="font-size:10px;color:#888;margin-top:6px;">
            크기 = 총 승차 (sqrt 스케일)<br>
            색 = 강도 (노랑→빨강)
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(OUT))
    size_kb = OUT.stat().st_size / 1024
    print(f"[3] 저장 → {OUT.relative_to(PROJECT_ROOT)}  ({size_kb:,.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
