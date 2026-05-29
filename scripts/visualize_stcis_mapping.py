"""STCIS 매핑 상태 시각화 — 우선순위 12노선 938 정류장 어디까지 매핑됐는지.

색상:
  🟢 초록 — 동일치 (tier 1: TAGO/STCIS 동 이름까지 같음)
  🟠 주황 — 시군구만일치 (tier 2: 동 이름은 다르지만 시군구 같음 — TAGO/STCIS 행정동 분류 차이)
  🔴 빨강 — 미매칭 (STCIS DB 에 없음 또는 좌표 미상)

출력: data/processed/maps/stcis_mapping_status.html
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
MAPPING_CSV = PROJECT_ROOT / "data" / "raw" / "stcis" / "stop_mapping.csv"
OUT = PROJECT_ROOT / "data" / "processed" / "maps" / "stcis_mapping_status.html"

TIER_COLORS = {
    "동일치": "#2ecc71",        # green
    "시군구만일치": "#f39c12",   # orange
    "미매칭": "#e74c3c",        # red
}
CHANGWON_CENTER = (35.230, 128.650)


def main() -> int:
    print("[1] 매핑 결과 로드")
    mp = pd.read_csv(MAPPING_CSV)
    print(f"    {len(mp)} 행 ({mp['tago_stop_id'].nunique()} 고유 TAGO 정류장)")

    # 정류장 단위 dedup — 매칭 후보 N개 중 첫 tier 가 대표 (이미 매칭 빌드 시 dedup 했음)
    by_stop = (
        mp.groupby("tago_stop_id")
        .agg(
            stop_name=("stop_name", "first"),
            sigungu=("sigungu", "first"),
            dong_tago=("dong_tago", "first"),
            n_stcis=("stcis_sttn_id", lambda s: s.dropna().nunique()),
            tier=("match_tier", "first"),
            sttn_ids=("stcis_sttn_id", lambda s: sorted(set(int(x) for x in s.dropna()))),
            stcis_dongs=("dong_stcis", lambda s: sorted(set(x for x in s.dropna()))),
        )
        .reset_index()
    )

    print("[2] 12개 우선순위 노선 정보 + DB 조인 (좌표·노선)")
    picked = pd.read_csv(PRIORITY_CSV, encoding="utf-8-sig")
    engine = get_engine()
    with engine.connect() as conn:
        routes = pd.read_sql(
            text(
                "SELECT route_id, route_no, route_name, route_type "
                "FROM routes WHERE route_no = ANY(:rs)"
            ),
            conn, params={"rs": picked["route_no"].astype(str).tolist()},
        )
        route_stops = pd.read_sql(
            text(
                "SELECT route_id, stop_id, sequence, direction "
                "FROM route_stops WHERE route_id = ANY(:ids) "
                "ORDER BY route_id, direction, sequence"
            ),
            conn, params={"ids": routes["route_id"].tolist()},
        )
        stops = pd.read_sql(
            text(
                "SELECT stop_id, stop_name, latitude, longitude "
                "FROM stops WHERE stop_id = ANY(:sids)"
            ),
            conn, params={"sids": route_stops["stop_id"].unique().tolist()},
        )
        districts_geojson = conn.execute(
            text(
                """
                SELECT json_build_object('type','FeatureCollection',
                    'features', json_agg(json_build_object(
                        'type','Feature',
                        'properties', json_build_object(
                            'district_name', district_name, 'sigungu', sigungu),
                        'geometry', ST_AsGeoJSON(geometry)::json)))
                FROM districts
                """
            )
        ).scalar()

    # by_stop ↔ stops join (좌표 추가). stop_name 중복 → by_stop 쪽 제거
    full = stops.merge(
        by_stop.drop(columns=["stop_name"]),
        left_on="stop_id", right_on="tago_stop_id", how="left",
    )
    full["tier"] = full["tier"].fillna("미매칭")
    full["n_stcis"] = full["n_stcis"].fillna(0).astype(int)

    counts = full["tier"].value_counts().reindex(["동일치", "시군구만일치", "미매칭"]).fillna(0).astype(int)
    print(f"    동일치={counts['동일치']}  시군구만={counts['시군구만일치']}  미매칭={counts['미매칭']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)

    print("[3] 지도 생성")
    m = folium.Map(location=CHANGWON_CENTER, zoom_start=11, tiles="OpenStreetMap",
                   control_scale=True)

    folium.GeoJson(
        districts_geojson, name="행정동 경계",
        style_function=lambda f: {"fillColor": "#aaaaaa", "color": "#444444",
                                   "weight": 1, "fillOpacity": 0.05},
        highlight_function=lambda f: {"fillColor": "#ffff00", "fillOpacity": 0.25,
                                       "weight": 2},
        tooltip=folium.GeoJsonTooltip(
            fields=["sigungu", "district_name"], aliases=["구:", "동:"]),
    ).add_to(m)

    # 노선 polyline (회색, 옅게) — 컨텍스트만 제공
    coords = stops.set_index("stop_id")[["latitude", "longitude"]]
    routes_grp = folium.FeatureGroup(name="12개 노선 (회색 배경)", show=True)
    for route_id, sub in route_stops.groupby("route_id"):
        for direction, ssub in sub.groupby("direction"):
            seq = ssub.sort_values("sequence")["stop_id"].tolist()
            path = [(coords.loc[s, "latitude"], coords.loc[s, "longitude"])
                    for s in seq if s in coords.index]
            if len(path) >= 2:
                folium.PolyLine(path, color="#888", weight=2, opacity=0.4).add_to(routes_grp)
    routes_grp.add_to(m)

    # 매칭 상태별 정류장 그룹
    groups = {
        tier: folium.FeatureGroup(name=f"{tier} ({int(counts.get(tier,0))})", show=True)
        for tier in TIER_COLORS
    }

    for _, r in full.iterrows():
        tier = r["tier"]
        color = TIER_COLORS.get(tier, "#7f8c8d")
        grp = groups.get(tier)
        if grp is None:
            continue

        sttn_ids = r.get("sttn_ids") or []
        stcis_dongs = r.get("stcis_dongs") or []
        sttn_html = (
            ", ".join(str(x) for x in sttn_ids[:8])
            + ("…" if len(sttn_ids) > 8 else "")
            if sttn_ids else "—"
        )
        stcis_dong_html = ", ".join(stcis_dongs) if stcis_dongs else "—"

        sigungu = r["sigungu"] if pd.notna(r["sigungu"]) else "(미상)"
        dong_tago = r["dong_tago"] if pd.notna(r["dong_tago"]) else "(미상)"

        popup_html = (
            f"<div style='min-width:260px;'>"
            f"<div style='font-weight:bold;font-size:14px;'>{r['stop_name']}</div>"
            f"<div style='color:#666;font-size:12px;margin-top:2px;'>"
            f"TAGO {sigungu} · {dong_tago}</div>"
            f"<div style='font-size:12px;margin-top:6px;'>"
            f"<b>매칭 상태:</b> <span style='color:{color};font-weight:bold;'>{tier}</span></div>"
            f"<div style='font-size:12px;'>"
            f"STCIS sttnId ({int(r['n_stcis'])}개): {sttn_html}</div>"
            f"<div style='font-size:12px;'>STCIS 동: {stcis_dong_html}</div>"
            f"<div style='font-size:11px;color:#666;margin-top:4px;'>"
            f"stop_id: <code>{r['stop_id']}</code></div>"
            f"</div>"
        )

        folium.CircleMarker(
            location=(r["latitude"], r["longitude"]),
            radius=4.5,
            color=color, weight=1,
            fill=True, fill_color=color, fill_opacity=0.85,
            tooltip=f"{r['stop_name']} ({tier})",
            popup=folium.Popup(popup_html, max_width=340),
        ).add_to(grp)

    for grp in groups.values():
        grp.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # 범례
    legend = f"""
    <div style="
        position: fixed; top: 12px; right: 12px; z-index: 9999;
        background: white; padding: 10px 14px; border: 1px solid #888;
        border-radius: 6px; font-family: sans-serif; font-size: 13px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15);">
        <div style="font-weight:bold;margin-bottom:6px;">STCIS 매핑 상태</div>
        <div style="margin:4px 0;">
            <span style="display:inline-block;width:14px;height:14px;border-radius:50%;
                background:{TIER_COLORS['동일치']};margin-right:8px;vertical-align:middle;"></span>
            동일치 ({int(counts.get('동일치',0))})
        </div>
        <div style="margin:4px 0;">
            <span style="display:inline-block;width:14px;height:14px;border-radius:50%;
                background:{TIER_COLORS['시군구만일치']};margin-right:8px;vertical-align:middle;"></span>
            시군구만일치 ({int(counts.get('시군구만일치',0))})
        </div>
        <div style="margin:4px 0;">
            <span style="display:inline-block;width:14px;height:14px;border-radius:50%;
                background:{TIER_COLORS['미매칭']};margin-right:8px;vertical-align:middle;"></span>
            미매칭 ({int(counts.get('미매칭',0))})
        </div>
        <div style="font-size:11px;color:#666;margin-top:6px;">
            마커 클릭: STCIS sttnId·매칭 동
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))

    m.save(str(OUT))
    size_kb = OUT.stat().st_size / 1024
    print(f"[4] 저장 → {OUT.relative_to(PROJECT_ROOT)}  ({size_kb:,.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
