"""기존 노선 vs 제안 신규 노선 5개 비교 시각화·정량 평가.

목적: 공모전 보고서용 — "기존 노선이 핫스팟을 얼마나 커버하는가?
       신규 노선 5개가 추가로 얼마나 미충족 수요를 흡수하는가?"

비교 지표:
  1. 핫스팟 100개의 기존 노선 커버리지 (정류장당 노선 수)
  2. 신규 corridor 가 핫스팟에 얼마나 인접 (1km 이내)
  3. 추가 인구 커버 (corridor 1km 버퍼 안 인구)
  4. 노선 등급별 분포 (좌석/간선/지선)

지도 레이어:
  - 회색 얇은 선: 기존 167 노선 (배경)
  - 12개 우선순위 노선: 등급별 색 (블루/그린/오렌지)
  - 검은 점: 일반 정류장
  - 노란 큰 점: NRPS 상위 100 핫스팟
  - 빨간 굵은 선: 신규 제안 corridor 5개
  - 빨간 점선 원: corridor 1km 버퍼

출력:
  - data/processed/maps/comparison_existing_vs_new.html
  - data/processed/route_comparison_metrics.csv
"""
from __future__ import annotations

import io
import math
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import folium  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402

PROPOSAL_CSV = PROJECT_ROOT / "data" / "processed" / "new_route_proposal.csv"
OUT_MAP = PROJECT_ROOT / "data" / "processed" / "maps" / "comparison_existing_vs_new.html"
OUT_METRICS = PROJECT_ROOT / "data" / "processed" / "route_comparison_metrics.csv"


# NRPS 가중치 (propose_new_routes.py 와 동일)
WEIGHTS = {"demand": 0.30, "shortage": 0.25, "pop": 0.15, "elder": 0.15, "density": 0.15}

# 노선 등급별 색상
TYPE_COLORS = {
    "좌석버스": "#e74c3c", "급행좌석": "#c0392b",
    "간선버스": "#3498db", "지선버스": "#2ecc71",
    "마을버스": "#f39c12", "읍면": "#9b59b6",
}


def z(s: pd.Series) -> pd.Series:
    mu, std = s.mean(), s.std()
    return s * 0 if std == 0 else (s - mu) / std


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def point_to_segment_m(plat, plon, lat1, lon1, lat2, lon2):
    """점에서 선분까지의 최소 거리 (m). 평면 근사 — 창원 스케일에 충분."""
    # 좌표를 미터 평면으로 (구좌표 → 거리 보존 평면화는 작은 영역에서 직접 비례 OK)
    # 간단히: 점 - 선분 양 끝점 거리 중 작은 값, 또는 직교 투영
    # 더 정확히: scipy 없이 평면 근사로
    # 점 P, 선분 AB
    P = np.array([plat, plon])
    A = np.array([lat1, lon1])
    B = np.array([lat2, lon2])
    AB = B - A
    AP = P - A
    t = np.dot(AP, AB) / (np.dot(AB, AB) + 1e-12)
    t = max(0, min(1, t))
    closest = A + t * AB
    return haversine_m(plat, plon, closest[0], closest[1])


def main() -> int:
    if not PROPOSAL_CSV.exists():
        print(f"[ERR] {PROPOSAL_CSV} 없음. propose_new_routes.py 먼저 실행")
        return 1

    engine = get_engine()
    print("[1] 데이터 로드 (정류장·노선·인구·boarding) — CTE 분리")
    with engine.connect() as conn:
        stops = pd.read_sql(
            text(
                """
                WITH
                  stop_routes AS (
                    SELECT stop_id, COUNT(DISTINCT route_id) AS n_routes
                    FROM route_stops GROUP BY stop_id
                  ),
                  stop_boarding AS (
                    SELECT stop_id,
                           SUM(boarding) AS total_boarding,
                           COUNT(DISTINCT date) AS n_days
                    FROM boarding_data GROUP BY stop_id
                  ),
                  district_meta AS (
                    SELECT district_id, sigungu, district_name,
                           population, pop_age_65_plus,
                           ST_Area(geometry::geography)/1e6 AS area_km2,
                           geometry
                    FROM districts
                  )
                SELECT
                    s.stop_id, s.stop_name, s.latitude, s.longitude,
                    d.sigungu, d.district_name,
                    d.population, d.pop_age_65_plus, d.area_km2,
                    COALESCE(sr.n_routes, 0) AS n_routes,
                    COALESCE(sb.total_boarding, 0) AS total_boarding,
                    COALESCE(sb.n_days, 0) AS n_days
                FROM stops s
                LEFT JOIN stop_routes sr ON sr.stop_id = s.stop_id
                LEFT JOIN stop_boarding sb ON sb.stop_id = s.stop_id
                LEFT JOIN district_meta d ON ST_Contains(d.geometry, s.location)
                """
            ),
            conn,
        )
        # 노선 + 경유 정류장 (등급 포함)
        routes_meta = pd.read_sql(
            text("SELECT route_id, route_no, route_name, route_type FROM routes"),
            conn,
        )
        route_stops = pd.read_sql(
            text(
                "SELECT route_id, stop_id, sequence, direction "
                "FROM route_stops ORDER BY route_id, direction, sequence"
            ),
            conn,
        )

    print(f"    정류장 {len(stops):,} · 노선 {len(routes_meta)} · "
          f"route_stops {len(route_stops):,}")

    # NRPS 재계산
    print("[2] NRPS 재계산 + 상위 100 hotspot")
    df = stops[stops["population"].notna() & (stops["population"] > 0)].copy()
    df["daily_boarding"] = df["total_boarding"] / df["n_days"].replace(0, np.nan)
    df["daily_boarding"] = df["daily_boarding"].fillna(0)
    df["n_routes_safe"] = df["n_routes"].replace(0, 0.5)
    df["elder_pct"] = df["pop_age_65_plus"] / df["population"]
    df["pop_density"] = df["population"] / df["area_km2"]
    df["nrps"] = (
        WEIGHTS["demand"]   * z(df["daily_boarding"])
        + WEIGHTS["shortage"] * z(1.0 / df["n_routes_safe"])
        + WEIGHTS["pop"]      * z(df["population"])
        + WEIGHTS["elder"]    * z(df["elder_pct"])
        + WEIGHTS["density"]  * z(df["pop_density"])
    )
    hot = df.nlargest(100, "nrps").copy()

    # 제안 노선 로드
    proposals = pd.read_csv(PROPOSAL_CSV)
    print(f"[3] 제안 노선 {len(proposals)}개 로드")

    # ============================================================
    # 비교 지표 1: 핫스팟 100개의 기존 노선 통과 수 분포
    # ============================================================
    print("\n[4] 비교 1: 핫스팟 100 정류장의 기존 노선 커버리지")
    cov_buckets = pd.cut(
        hot["n_routes"],
        bins=[-1, 0, 3, 10, 30, 100],
        labels=["0", "1~3", "4~10", "11~30", "31+"],
    ).value_counts().sort_index()
    print("    기존 노선 수 분포 (핫스팟 100개 중):")
    for b, c in cov_buckets.items():
        print(f"      {b:>6s}개 노선 통과: {c:>3d}개 핫스팟")

    # ============================================================
    # 비교 지표 2: 각 신규 corridor 가 1km 이내 흡수하는 핫스팟
    # ============================================================
    print("\n[5] 비교 2: 신규 corridor 1km 버퍼 내 핫스팟·인구 흡수")
    BUFFER_M = 1000
    summaries = []
    for _, p in proposals.iterrows():
        cid = int(p["corridor_id"])
        f_lat, f_lon = p["from_lat"], p["from_lon"]
        t_lat, t_lon = p["to_lat"], p["to_lon"]

        # 각 핫스팟 ↔ corridor 선분 거리
        dists_m = hot.apply(
            lambda r: point_to_segment_m(
                r["latitude"], r["longitude"], f_lat, f_lon, t_lat, t_lon
            ),
            axis=1,
        )
        within = hot[dists_m <= BUFFER_M].copy()
        within_pop = (
            df[df["stop_id"].isin(within["stop_id"])]
            .drop_duplicates("district_name")["population"].sum()
        )
        # 가까운 모든 정류장 (핫스팟 외 포함)
        all_dists = df.apply(
            lambda r: point_to_segment_m(
                r["latitude"], r["longitude"], f_lat, f_lon, t_lat, t_lon
            ),
            axis=1,
        )
        all_within = df[all_dists <= BUFFER_M]
        # 노선 다양성
        cur_routes_avg = all_within["n_routes"].mean() if len(all_within) > 0 else 0

        summaries.append({
            "corridor": f"#{cid} {p['from_anchor']} ↔ {p['to_anchor']}",
            "거리km": p["distance_km"],
            "버퍼내_핫스팟": int(len(within)),
            "버퍼내_정류장": int(len(all_within)),
            "기존_평균노선수": round(cur_routes_avg, 1),
            "흡수_NRPS합": round(within["nrps"].sum(), 2) if len(within) else 0,
            "행정동인구_커버": int(within_pop),
        })

    summary_df = pd.DataFrame(summaries)
    print(summary_df.to_string(index=False))
    OUT_METRICS.parent.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(OUT_METRICS, index=False, encoding="utf-8-sig")
    print(f"\n    → {OUT_METRICS.relative_to(PROJECT_ROOT)}")

    # ============================================================
    # 비교 지표 3: 노선 등급별 분포
    # ============================================================
    print("\n[6] 비교 3: 기존 167 노선 등급별 분포")
    by_type = routes_meta["route_type"].value_counts()
    print(by_type.to_string())

    # ============================================================
    # 지도 생성
    # ============================================================
    print("\n[7] 지도 생성")
    m = folium.Map(location=(35.230, 128.650), zoom_start=11,
                   tiles="cartodbpositron", control_scale=True)

    coords = stops.set_index("stop_id")[["latitude", "longitude"]]

    # 레이어 A: 기존 167 노선 (회색 얇게 — 배경)
    bg_grp = folium.FeatureGroup(name=f"기존 노선 {len(routes_meta)}개 (배경)", show=True)
    for route_id, sub in route_stops.groupby("route_id"):
        for direction, ssub in sub.groupby("direction"):
            seq = ssub.sort_values("sequence")["stop_id"].tolist()
            path = [(coords.loc[s, "latitude"], coords.loc[s, "longitude"])
                    for s in seq if s in coords.index]
            if len(path) >= 2:
                folium.PolyLine(path, color="#999", weight=1, opacity=0.25).add_to(bg_grp)
    bg_grp.add_to(m)

    # 일반 정류장 (검은 점, 작게)
    stop_grp = folium.FeatureGroup(name=f"일반 정류장 ({len(stops):,})", show=False)
    for _, s in stops.iterrows():
        folium.CircleMarker(
            location=(s["latitude"], s["longitude"]),
            radius=1.5, color="#444", weight=0.3,
            fill=True, fill_color="#888", fill_opacity=0.5,
        ).add_to(stop_grp)
    stop_grp.add_to(m)

    # 핫스팟 (노란 큰 점)
    hot_grp = folium.FeatureGroup(name=f"⭐ NRPS 상위 100 핫스팟", show=True)
    max_nrps = float(hot["nrps"].max())
    for _, r in hot.iterrows():
        radius = 4 + 8 * (r["nrps"] / max_nrps)
        popup_html = (
            f"<b>{r['stop_name']}</b><br>"
            f"{r['sigungu']} · {r['district_name']}<br>"
            f"기존 노선 <b>{int(r['n_routes'])}개</b> · "
            f"일승차 {r['daily_boarding']:.0f}<br>"
            f"NRPS <b>{r['nrps']:.2f}</b>"
        )
        folium.CircleMarker(
            location=(r["latitude"], r["longitude"]),
            radius=radius, color="#f1c40f", weight=1,
            fill=True, fill_color="#f39c12", fill_opacity=0.85,
            tooltip=f"{r['stop_name']} NRPS={r['nrps']:.2f}",
            popup=folium.Popup(popup_html, max_width=300),
        ).add_to(hot_grp)
    hot_grp.add_to(m)

    # 신규 corridor 5개 + 1km 버퍼
    new_grp = folium.FeatureGroup(name=f"⭐ 신규 제안 노선 5개 + 1km 버퍼", show=True)
    for _, p in proposals.iterrows():
        cid = int(p["corridor_id"])
        path = [(p["from_lat"], p["from_lon"]), (p["to_lat"], p["to_lon"])]
        # 중간선
        folium.PolyLine(
            path, color="#c0392b", weight=6, opacity=0.85,
            tooltip=f"신규 #{cid}: {p['from_anchor']} ↔ {p['to_anchor']} "
                    f"({p['distance_km']}km)",
            popup=folium.Popup(
                f"<b>신규 노선 #{cid}</b><br>"
                f"{p['from_anchor']} ↔ {p['to_anchor']}<br>"
                f"거리 {p['distance_km']}km<br>"
                f"버퍼 1km 내 핫스팟: "
                f"{summaries[cid-1]['버퍼내_핫스팟']}개<br>"
                f"기존 평균 노선 수: "
                f"{summaries[cid-1]['기존_평균노선수']}",
                max_width=320,
            ),
        ).add_to(new_grp)
        # 양 끝 1km 원
        for lat, lon in path:
            folium.Circle(
                location=(lat, lon), radius=BUFFER_M,
                color="#c0392b", weight=1, opacity=0.4,
                fill=True, fill_color="#c0392b", fill_opacity=0.05,
                dash_array="5,5",
            ).add_to(new_grp)
        # 번호
        mid_lat = (path[0][0] + path[1][0]) / 2
        mid_lon = (path[0][1] + path[1][1]) / 2
        folium.Marker(
            location=(mid_lat, mid_lon),
            icon=folium.DivIcon(
                html=f'<div style="background:#c0392b;color:white;width:32px;height:32px;'
                     f'border-radius:50%;display:flex;align-items:center;justify-content:center;'
                     f'font-weight:bold;font-size:16px;border:3px solid white;'
                     f'box-shadow:0 2px 4px rgba(0,0,0,0.3);">#{cid}</div>'
            ),
        ).add_to(new_grp)
    new_grp.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # 범례
    legend = """
    <div style="position: fixed; top: 12px; right: 12px; z-index: 9999;
                background: white; padding: 10px 14px; border: 1px solid #888;
                border-radius: 6px; font-family: sans-serif; font-size: 12px;
                box-shadow: 0 2px 6px rgba(0,0,0,0.15); max-width: 280px;">
        <div style="font-weight:bold;font-size:13px;margin-bottom:6px;">
            기존 vs 신규 비교
        </div>
        <div style="margin:4px 0;">
            <span style="display:inline-block;width:24px;height:2px;background:#999;
                vertical-align:middle;margin-right:6px;opacity:0.4;"></span>
            기존 167 노선 (배경)
        </div>
        <div style="margin:4px 0;">
            <span style="display:inline-block;width:14px;height:14px;border-radius:50%;
                background:#f39c12;margin-right:6px;vertical-align:middle;"></span>
            NRPS 상위 100 핫스팟 (크기=점수)
        </div>
        <div style="margin:4px 0;">
            <span style="display:inline-block;width:24px;height:4px;background:#c0392b;
                vertical-align:middle;margin-right:6px;"></span>
            <b>신규 제안 노선 5개</b>
        </div>
        <div style="margin:4px 0;">
            <span style="display:inline-block;width:14px;height:14px;border:1px dashed #c0392b;
                background:rgba(192,57,43,0.1);margin-right:6px;vertical-align:middle;
                border-radius:50%;"></span>
            corridor 단말 1km 버퍼
        </div>
        <hr style="margin:6px 0;">
        <div style="font-size:10px;color:#666;">
            보고서용: 핫스팟이 노란점, 신규 노선은 빨간선·#번호.<br>
            클릭 시 흡수 핫스팟 수 등 상세.
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))

    OUT_MAP.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(OUT_MAP))
    print(f"\n[8] 지도 저장: {OUT_MAP.relative_to(PROJECT_ROOT)}  "
          f"({OUT_MAP.stat().st_size/1024:,.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
