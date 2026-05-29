"""12개 우선순위 노선만 지도 시각화 (등급별 색상).

용도: 12개 노선이 창원시 권역을 어떻게 커버하는지 시각 확인.
       priority_routes_12.csv 가 있어야 함 (select_priority_routes.py 실행 후).

출력: data/processed/maps/priority_routes_12.html
  - 노선 polyline: 등급별 색상 (좌석=red, 간선=blue, 지선=green, 마을=orange)
  - 정류장 마커: stop_id 기준 dedup, 통과 노선 목록 팝업
  - 행정동 경계 배경 (시군구·동명 툴팁)
  - LayerControl 로 등급별 토글
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
OUTPUT_FILE = PROJECT_ROOT / "data" / "processed" / "maps" / "priority_routes_12.html"

# 노선 등급별 색상 (visualize_map.py 와 동일)
TYPE_COLORS: dict[str, str] = {
    "좌석버스": "#e74c3c",  # red
    "급행좌석": "#c0392b",  # darkred
    "간선버스": "#3498db",  # blue
    "지선버스": "#27ae60",  # green
    "마을버스": "#f39c12",  # orange
    "읍면": "#9b59b6",      # purple
}
DEFAULT_COLOR = "#7f8c8d"  # gray
CHANGWON_CENTER = (35.230, 128.650)


def load_data(engine, picked_routes: pd.DataFrame):
    """picked_routes 에 있는 12개 노선만 필터해서 stops·route_stops·districts 로드.

    stops 에는 PostGIS ST_Contains 로 시군구·읍면동 컬럼도 함께 채움.
    """
    # route_no = TAGO routeid (visualize_map.py 처럼)
    route_nos = picked_routes["route_no"].astype(str).tolist()

    with engine.connect() as conn:
        # 우리 12개 노선만
        routes = pd.read_sql(
            text(
                "SELECT route_id, route_no, route_name, route_type "
                "FROM routes WHERE route_no = ANY(:rs)"
            ),
            conn, params={"rs": route_nos},
        )

        route_stops = pd.read_sql(
            text(
                """
                SELECT rs.route_id, rs.stop_id, rs.sequence, rs.direction
                FROM route_stops rs
                WHERE rs.route_id = ANY(:ids)
                ORDER BY rs.route_id, rs.direction, rs.sequence
                """
            ),
            conn, params={"ids": routes["route_id"].tolist()},
        )

        # 노선 정류장만 + 행정동 조인
        stop_ids = route_stops["stop_id"].unique().tolist()
        stops = pd.read_sql(
            text(
                """
                SELECT s.stop_id, s.stop_name, s.bis_number,
                       s.latitude, s.longitude,
                       d.sigungu, d.district_name
                FROM stops s
                LEFT JOIN districts d ON ST_Contains(d.geometry, s.location)
                WHERE s.stop_id = ANY(:sids)
                """
            ),
            conn, params={"sids": stop_ids},
        )

        districts_geojson = conn.execute(
            text(
                """
                SELECT json_build_object(
                    'type', 'FeatureCollection',
                    'features', json_agg(
                        json_build_object(
                            'type', 'Feature',
                            'properties', json_build_object(
                                'district_name', district_name,
                                'sigungu', sigungu
                            ),
                            'geometry', ST_AsGeoJSON(geometry)::json
                        )
                    )
                ) FROM districts
                """
            )
        ).scalar()

    # importance_score 만 join (담당자 정보는 시각화에 필요 없음)
    routes = routes.merge(
        picked_routes[["route_no", "importance_score"]],
        on="route_no",
        how="left",
    )
    return routes, route_stops, stops, districts_geojson


def add_district_layer(m: folium.Map, districts_geojson) -> None:
    if not districts_geojson:
        return
    folium.GeoJson(
        districts_geojson,
        name="행정동 경계",
        style_function=lambda f: {
            "fillColor": "#aaaaaa",
            "color": "#444444",
            "weight": 1,
            "fillOpacity": 0.05,
        },
        highlight_function=lambda f: {
            "fillColor": "#ffff00",
            "fillOpacity": 0.25,
            "weight": 2,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["sigungu", "district_name"],
            aliases=["구:", "동:"],
        ),
    ).add_to(m)


def add_routes(
    m: folium.Map,
    routes: pd.DataFrame,
    route_stops: pd.DataFrame,
    stops: pd.DataFrame,
) -> None:
    coords = stops.set_index("stop_id")[["latitude", "longitude"]]

    # 1) 노선 polyline — 등급별 FeatureGroup
    line_groups: dict[str, folium.FeatureGroup] = {}
    for rt in routes["route_type"].dropna().unique():
        n = (routes["route_type"] == rt).sum()
        line_groups[rt] = folium.FeatureGroup(name=f"노선 · {rt} ({n}개)", show=True)

    for _, route in routes.iterrows():
        rt = route["route_type"]
        color = TYPE_COLORS.get(rt, DEFAULT_COLOR)
        grp = line_groups.get(rt)
        if grp is None:
            grp = line_groups.setdefault(
                rt or "기타", folium.FeatureGroup(name=f"노선 · {rt or '기타'}", show=True)
            )

        rs_route = route_stops[route_stops["route_id"] == route["route_id"]]
        for direction, sub in rs_route.groupby("direction"):
            seq = sub.sort_values("sequence")["stop_id"].tolist()
            path = [
                (coords.loc[sid, "latitude"], coords.loc[sid, "longitude"])
                for sid in seq if sid in coords.index
            ]
            if len(path) < 2:
                continue

            popup_html = (
                f"<b>{route['route_name']}</b><br>"
                f"등급: {rt} · 방향: {direction}<br>"
                f"정류장 {len(path)}개 · 점수 {route['importance_score']:.0f}"
            )
            folium.PolyLine(
                path,
                color=color,
                weight=4,
                opacity=0.75,
                popup=folium.Popup(popup_html, max_width=320),
            ).add_to(grp)

    for grp in line_groups.values():
        grp.add_to(m)

    # 2) 정류장 마커 — stop_id 기준 dedup, 통과 노선 목록 팝업
    rs_with_route = route_stops.merge(
        routes[["route_id", "route_name", "route_type"]], on="route_id"
    )
    # 정류장별로 통과 노선 모음
    per_stop = (
        rs_with_route.groupby("stop_id")
        .agg(
            routes=("route_name", lambda s: sorted(set(s))),
            types=("route_type", lambda s: sorted(set(s.dropna()))),
        )
        .reset_index()
    )
    stops_full = stops.merge(per_stop, on="stop_id", how="inner")

    stop_group = folium.FeatureGroup(
        name=f"정류장 ({len(stops_full)}개, 통과 노선 표시)", show=True
    )
    for _, s in stops_full.iterrows():
        # 정류장 색 = 통과 노선 중 등급 가중치 가장 높은 것 (좌석 > 간선 > 지선 > 기타)
        rank = {"좌석버스": 4, "급행좌석": 4, "간선버스": 3, "지선버스": 2, "마을버스": 1, "읍면": 1}
        primary_type = max(s["types"], key=lambda t: rank.get(t, 0)) if s["types"] else None
        color = TYPE_COLORS.get(primary_type, DEFAULT_COLOR)

        sigungu = s["sigungu"] if pd.notna(s["sigungu"]) else "(미상)"
        dong = s["district_name"] if pd.notna(s["district_name"]) else "(미상)"
        ars = s["bis_number"] if pd.notna(s["bis_number"]) else "—"
        routes_html = "<br>".join(f"  • {r}" for r in s["routes"])

        popup_html = (
            f"<div style='min-width:240px;'>"
            f"<div style='font-weight:bold;font-size:14px;'>{s['stop_name']}</div>"
            f"<div style='color:#666;font-size:12px;margin-top:2px;'>"
            f"{sigungu} · {dong}</div>"
            f"<div style='font-size:12px;margin-top:6px;'>"
            f"ARS: {ars} · stop_id: <code>{s['stop_id']}</code></div>"
            f"<div style='font-size:11px;color:#666;'>"
            f"좌표: {s['latitude']:.5f}, {s['longitude']:.5f}</div>"
            f"<div style='font-weight:bold;margin-top:8px;'>통과 노선 ({len(s['routes'])})</div>"
            f"<div style='font-size:12px;'>{routes_html}</div>"
            f"</div>"
        )

        folium.CircleMarker(
            location=(s["latitude"], s["longitude"]),
            radius=4,
            color=color,
            weight=1,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            tooltip=s["stop_name"],
            popup=folium.Popup(popup_html, max_width=320),
        ).add_to(stop_group)
    stop_group.add_to(m)


def add_legend(m: folium.Map, routes: pd.DataFrame) -> None:
    """우상단 범례 (HTML/CSS)."""
    present = routes["route_type"].dropna().unique().tolist()
    rows = "".join(
        f'<div style="margin:4px 0;">'
        f'<span style="display:inline-block;width:18px;height:6px;'
        f'background:{TYPE_COLORS.get(rt, DEFAULT_COLOR)};margin-right:8px;'
        f'vertical-align:middle;"></span>'
        f'{rt}</div>'
        for rt in present if rt
    )
    legend_html = f"""
    <div style="
        position: fixed; top: 12px; right: 12px; z-index: 9999;
        background: white; padding: 10px 14px; border: 1px solid #888;
        border-radius: 6px; font-family: sans-serif; font-size: 13px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15);">
        <div style="font-weight:bold;margin-bottom:6px;">12개 우선순위 노선</div>
        {rows}
        <div style="font-size:11px;color:#666;margin-top:6px;">
            마커 hover: 정류장명 · 클릭: 시군구·동·ARS·통과 노선
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))


def main() -> int:
    if not PRIORITY_CSV.exists():
        print(f"[ERR] {PRIORITY_CSV} 가 없습니다. select_priority_routes.py 먼저 실행.")
        return 1

    picked = pd.read_csv(PRIORITY_CSV, encoding="utf-8-sig")
    print(f"[1/3] 우선순위 노선 {len(picked)}개 로드")

    engine = get_engine()
    routes, route_stops, stops, districts = load_data(engine, picked)
    print(f"[2/3] DB 로드: routes={len(routes)} route_stops={len(route_stops)} stops={len(stops)}")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    m = folium.Map(
        location=CHANGWON_CENTER,
        zoom_start=11,
        tiles="OpenStreetMap",
        control_scale=True,
    )
    add_district_layer(m, districts)
    add_routes(m, routes, route_stops, stops)
    folium.LayerControl(collapsed=False).add_to(m)
    add_legend(m, routes)

    m.save(str(OUTPUT_FILE))
    size_kb = OUTPUT_FILE.stat().st_size / 1024
    print(f"[3/3] 저장 → {OUTPUT_FILE.relative_to(PROJECT_ROOT)}  ({size_kb:,.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
