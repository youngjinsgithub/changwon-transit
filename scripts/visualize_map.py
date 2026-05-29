"""창원 정류장·노선 인터랙티브 지도 생성.

생성물: data/processed/maps/changwon_transit.html
  - 베이스맵: OpenStreetMap
  - 레이어 1: 정류장 분포 (노선 등급별 색상)
  - 레이어 2: 노선별 polyline (등급별 색상)
  - LayerControl 로 토글 가능

노선 등급(routes.route_type): 좌석버스 / 간선버스 / 지선버스 / 마을버스
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import folium  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)

OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "maps"
OUTPUT_FILE = OUTPUT_DIR / "changwon_transit.html"
RANKED_CSV = PROJECT_ROOT / "data" / "processed" / "routes_ranked.csv"

# 등급별 잠정 가중치 — STCIS 이용량 도착 시 진짜 점수로 교체 예정
TYPE_WEIGHT: dict[str, float] = {
    "좌석버스": 3.0,
    "급행좌석": 3.0,
    "간선버스": 2.0,
    "지선버스": 1.0,
    "마을버스": 0.5,
    "읍면": 0.7,
}

# 상위 N개를 "주요 노선" 으로 강조
TOP_N_ROUTES: int = 30

# 노선 등급별 색상 (folium 지원 색)
TYPE_COLORS: dict[str, str] = {
    "좌석버스": "red",
    "간선버스": "blue",
    "지선버스": "green",
    "마을버스": "orange",
    "급행좌석": "darkred",
    "읍면": "purple",
}
DEFAULT_COLOR = "gray"

CHANGWON_CENTER = (35.230, 128.650)  # 대략 창원시 중심


def load_data(engine):
    """DB 에서 필요한 데이터를 모두 로드 (행정동 포함)."""
    print("[1/5] DB 로드 ...")
    with engine.connect() as conn:
        stops = pd.read_sql(
            text(
                """
                SELECT stop_id, stop_name, latitude, longitude
                FROM stops
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                """
            ),
            conn,
        )
        routes = pd.read_sql(
            text("SELECT route_id, route_no, route_name, route_type FROM routes"),
            conn,
        )
        route_stops = pd.read_sql(
            text(
                """
                SELECT route_id, stop_id, sequence, direction
                FROM route_stops
                ORDER BY route_id, direction, sequence
                """
            ),
            conn,
        )

        # 행정동 GeoJSON 도 함께 (DB → GeoJSON 변환)
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
                )
                FROM districts
                """
            )
        ).scalar()

        # 노선별 통과 행정동 수 (광역성 지표)
        route_districts = pd.read_sql(
            text(
                """
                SELECT rs.route_id, COUNT(DISTINCT d.district_id) AS n_districts
                FROM route_stops rs
                JOIN stops s ON rs.stop_id = s.stop_id
                JOIN districts d ON ST_Contains(d.geometry, s.location)
                GROUP BY rs.route_id
                """
            ),
            conn,
        )

    # 각 정류장이 어떤 노선 등급을 갖는지 — 정류장 등급은 통과 노선 중 가장 흔한 등급으로
    rs_with_type = route_stops.merge(routes[["route_id", "route_type"]], on="route_id")
    stop_dominant_type = (
        rs_with_type.groupby(["stop_id", "route_type"])
        .size()
        .reset_index(name="cnt")
        .sort_values("cnt", ascending=False)
        .drop_duplicates("stop_id")[["stop_id", "route_type"]]
    )
    stops = stops.merge(stop_dominant_type, on="stop_id", how="left")
    stops["route_type"] = stops["route_type"].fillna("기타")

    print(
        f"      stops={len(stops)} routes={len(routes)} "
        f"route_stops={len(route_stops)} districts(GeoJSON loaded)"
    )
    return stops, routes, route_stops, route_districts, districts_geojson


def add_stop_layer(m: folium.Map, stops: pd.DataFrame) -> None:
    """정류장 레이어. 등급별 색상, 클러스터 그룹."""
    print("[4/5] 정류장 레이어 생성 ...")

    # 등급별 FeatureGroup
    groups: dict[str, folium.FeatureGroup] = {}
    for rt, color in TYPE_COLORS.items():
        n = (stops["route_type"] == rt).sum()
        if n == 0:
            continue
        groups[rt] = folium.FeatureGroup(name=f"정류장 · {rt} ({n})", show=True)

    other_n = (~stops["route_type"].isin(TYPE_COLORS.keys())).sum()
    if other_n:
        groups["기타"] = folium.FeatureGroup(name=f"정류장 · 기타 ({other_n})", show=True)

    for _, r in stops.iterrows():
        rt = r["route_type"]
        color = TYPE_COLORS.get(rt, DEFAULT_COLOR)
        grp = groups.get(rt) or groups.get("기타")
        if grp is None:
            continue
        folium.CircleMarker(
            location=(r["latitude"], r["longitude"]),
            radius=3,
            color=color,
            weight=1,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            popup=folium.Popup(
                f"<b>{r['stop_name']}</b><br>"
                f"id: {r['stop_id']}<br>"
                f"등급: {rt}",
                max_width=250,
            ),
        ).add_to(grp)

    for grp in groups.values():
        grp.add_to(m)


def compute_route_importance(
    routes: pd.DataFrame,
    route_stops: pd.DataFrame,
    route_districts: pd.DataFrame,
) -> pd.DataFrame:
    """노선별 잠정 중요도 점수.

    공식: importance = (n_stops × type_weight) + (n_districts × DISTRICT_WEIGHT)
        - n_stops: 노선이 경유하는 고유 정류장 수
        - type_weight: 노선 등급 가중치
        - n_districts: 노선이 통과하는 행정동 수 (광역성)

    Returns:
        routes 에 컬럼 추가: n_stops, n_districts, type_weight,
                            importance_score, rank
    """
    DISTRICT_WEIGHT = 10.0  # 동 1개당 +10점

    n_stops_per_route = (
        route_stops.groupby("route_id")["stop_id"]
        .nunique()
        .reset_index(name="n_stops")
    )
    out = (
        routes
        .merge(n_stops_per_route, on="route_id", how="left")
        .merge(route_districts, on="route_id", how="left")
        .fillna({"n_stops": 0, "n_districts": 0})
    )
    out["type_weight"] = out["route_type"].map(TYPE_WEIGHT).fillna(1.0)
    out["importance_score"] = (
        out["n_stops"] * out["type_weight"]
        + out["n_districts"] * DISTRICT_WEIGHT
    )
    out = out.sort_values("importance_score", ascending=False).reset_index(drop=True)
    out["rank"] = out.index + 1
    return out


def add_route_layer(
    m: folium.Map,
    routes_ranked: pd.DataFrame,
    route_stops: pd.DataFrame,
    stops: pd.DataFrame,
) -> None:
    """노선 polyline 레이어. 두 그룹:
      1) 주요 노선 상위 N — 두꺼운 선, 기본 ON
      2) 그 외 — 옅은 선, 기본 OFF (등급별 토글)
    """
    print("\n[3/5] 노선 polyline 레이어 생성 ...")

    coords = stops.set_index("stop_id")[["latitude", "longitude"]]
    rs_with_meta = route_stops.merge(
        routes_ranked[
            ["route_id", "route_no", "route_name", "route_type", "rank", "importance_score"]
        ],
        on="route_id",
    )

    top_ids = set(routes_ranked.head(TOP_N_ROUTES)["route_id"].tolist())

    top_group = folium.FeatureGroup(
        name=f"⭐ 주요 노선 TOP {TOP_N_ROUTES}", show=True
    )
    other_groups: dict[str, folium.FeatureGroup] = {}
    drawn_top = 0
    drawn_other = 0

    for (route_id, direction), grp in rs_with_meta.groupby(["route_id", "direction"]):
        meta = grp.iloc[0]
        rt = meta["route_type"]
        color = TYPE_COLORS.get(rt, DEFAULT_COLOR)
        is_top = route_id in top_ids

        seq = grp.sort_values("sequence")["stop_id"].tolist()
        path = [
            (coords.loc[sid, "latitude"], coords.loc[sid, "longitude"])
            for sid in seq
            if sid in coords.index
        ]
        if len(path) < 2:
            continue

        popup_html = (
            f"<b>#{int(meta['rank'])} · 노선 {meta['route_no'][-4:]}</b><br>"
            f"{meta['route_name']}<br>"
            f"등급: {rt} · 방향: {direction} · {len(path)}개 정류장<br>"
            f"점수: {meta['importance_score']:.1f}"
        )

        if is_top:
            folium.PolyLine(
                path,
                color=color,
                weight=5,
                opacity=0.85,
                popup=folium.Popup(popup_html, max_width=320),
            ).add_to(top_group)
            drawn_top += 1
        else:
            if rt not in other_groups:
                other_groups[rt] = folium.FeatureGroup(
                    name=f"그 외 · {rt}", show=False
                )
            folium.PolyLine(
                path,
                color=color,
                weight=1.5,
                opacity=0.3,
                popup=folium.Popup(popup_html, max_width=320),
            ).add_to(other_groups[rt])
            drawn_other += 1

    top_group.add_to(m)
    for grp in other_groups.values():
        grp.add_to(m)
    print(
        f"      polyline: 주요 {drawn_top}개 (TOP {TOP_N_ROUTES} × 방향), "
        f"그 외 {drawn_other}개"
    )


def save_ranked_csv(routes_ranked: pd.DataFrame) -> None:
    """노선 순위표를 CSV 로 저장 (외부 참조용)."""
    RANKED_CSV.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "rank",
        "route_no",
        "route_name",
        "route_type",
        "n_stops",
        "type_weight",
        "importance_score",
    ]
    routes_ranked[cols].to_csv(RANKED_CSV, index=False, encoding="utf-8-sig")
    print(f"      → {RANKED_CSV.relative_to(PROJECT_ROOT)}")


def print_top_routes(routes_ranked: pd.DataFrame, n: int = 10) -> None:
    """상위 N 노선 콘솔 출력."""
    print(f"\n[상위 {n} 노선]")
    print(
        f"  {'rank':>4} {'등급':<10} {'정류장':>5} {'동':>3} {'점수':>7}  노선명"
    )
    for _, r in routes_ranked.head(n).iterrows():
        print(
            f"  {int(r['rank']):>4} {r['route_type']:<10} "
            f"{int(r['n_stops']):>5} {int(r['n_districts']):>3} "
            f"{r['importance_score']:>7.1f}  {r['route_name']}"
        )


def add_district_layer(m: folium.Map, districts_geojson) -> None:
    """행정동 폴리곤 — 경계만 옅게."""
    print("[2/5] 행정동 폴리곤 레이어 ...")
    if not districts_geojson:
        print("      districts GeoJSON 비어있음 — 스킵")
        return

    grp = folium.FeatureGroup(name="행정동 경계", show=True)
    folium.GeoJson(
        districts_geojson,
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
    ).add_to(grp)
    grp.add_to(m)


def build_map() -> folium.Map:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    engine = get_engine()

    stops, routes, route_stops, route_districts, districts_geojson = load_data(engine)

    print("\n[중요도 산정] 광역성 가산 (행정동 통과 수)")
    routes_ranked = compute_route_importance(routes, route_stops, route_districts)
    save_ranked_csv(routes_ranked)
    print_top_routes(routes_ranked, n=15)

    m = folium.Map(
        location=CHANGWON_CENTER,
        zoom_start=11,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    add_district_layer(m, districts_geojson)
    add_route_layer(m, routes_ranked, route_stops, stops)
    add_stop_layer(m, stops)

    folium.LayerControl(collapsed=False).add_to(m)

    print("\n[5/5] HTML 저장 ...")
    m.save(str(OUTPUT_FILE))
    size_kb = OUTPUT_FILE.stat().st_size / 1024
    print(f"      → {OUTPUT_FILE.relative_to(PROJECT_ROOT)}  ({size_kb:,.0f} KB)")
    return m


def main() -> int:
    try:
        build_map()
    except Exception as e:  # noqa: BLE001
        print(f"\n[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    print("\n[OK] 지도 생성 완료. 브라우저로 위 HTML 파일 열어 확인.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
