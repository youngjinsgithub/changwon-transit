"""신규 버스 노선 제안 — NRPS (New Route Priority Score) 알고리즘.

목적: 창원시 승용차 → 버스 전환 유도. 미충족 수요 핫스팟을 식별하고
       그 사이를 연결하는 신규 노선 corridor 를 자동 추천.

알고리즘 단계:
  1. 정류장별 5개 지표 z-score 계산
  2. 가중합 NRPS = 0.30×수요 + 0.25×공급부족 + 0.15×인구 + 0.15×고령 + 0.15×밀도
  3. 상위 100개 hotspot 추출
  4. KMeans(k=7) 로 hotspot 좌표 클러스터링 → 7개 핫존
  5. 핫존 centroid 간 거리 기반 Greedy minimum spanning tree (MST) 로
     5개 corridor 추천 (각 corridor = centroid 간 직선·곡선)
  6. 지도 + CSV 보고서

가중치 근거:
  - α 0.30 (수요): 가장 직접적인 이용 증거
  - β 0.25 (공급 부족): 본 분석 핵심 관점
  - γ 0.15 (잠재 인구): 환승 유도 가능 풀
  - δ 0.15 (고령 비중): 교통 약자 우선 — 공모전 포용성
  - ε 0.15 (인구 밀도): 차량 의존 줄일 잠재력 큰 지역

출력:
  - data/processed/maps/new_route_proposal.html  (지도)
  - data/processed/new_route_proposal.csv        (제안 표)
  - 콘솔: 핫존·corridor 상세
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
from sklearn.cluster import KMeans  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402

OUT_MAP = PROJECT_ROOT / "data" / "processed" / "maps" / "new_route_proposal.html"
OUT_CSV = PROJECT_ROOT / "data" / "processed" / "new_route_proposal.csv"


# 가중치
WEIGHTS = {
    "demand":   0.30,
    "shortage": 0.25,
    "pop":      0.15,
    "elder":    0.15,
    "density":  0.15,
}

N_HOTSPOTS = 100   # 상위 NRPS 핫스팟 수
N_CLUSTERS = 7     # 핫존 (corridor 단말 후보)


def z(s: pd.Series) -> pd.Series:
    """z-score (NaN → 0)."""
    mu, std = s.mean(), s.std()
    if std == 0:
        return s * 0
    return (s - mu) / std


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def main() -> int:
    engine = get_engine()
    print("[1] 정류장·인구·승차 데이터 로드 + 행정동 면적")
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                """
                WITH district_area AS (
                    SELECT district_id, district_name, sigungu,
                           population, pop_age_65_plus,
                           ST_Area(geometry::geography) / 1e6 AS area_km2
                    FROM districts
                )
                SELECT
                    s.stop_id, s.stop_name, s.latitude, s.longitude,
                    d.sigungu, d.district_name,
                    d.population, d.pop_age_65_plus, d.area_km2,
                    COUNT(DISTINCT rs.route_id) AS n_routes,
                    COALESCE(SUM(bd.boarding), 0) AS total_boarding,
                    COUNT(DISTINCT bd.date) AS n_days
                FROM stops s
                LEFT JOIN route_stops rs ON rs.stop_id = s.stop_id
                LEFT JOIN boarding_data bd ON bd.stop_id = s.stop_id
                LEFT JOIN district_area d ON ST_Contains(
                    (SELECT geometry FROM districts dd WHERE dd.district_id = d.district_id),
                    s.location
                )
                GROUP BY s.stop_id, s.stop_name, s.latitude, s.longitude,
                         d.sigungu, d.district_name,
                         d.population, d.pop_age_65_plus, d.area_km2
                """
            ),
            conn,
        )
    print(f"    정류장 {len(df):,}개")

    df = df[df["population"].notna() & (df["population"] > 0)].copy()
    df["daily_boarding"] = df["total_boarding"] / df["n_days"].replace(0, np.nan)
    df["daily_boarding"] = df["daily_boarding"].fillna(0)
    df["n_routes_safe"] = df["n_routes"].replace(0, 0.5)  # 0 노선도 일부 점수
    df["elder_pct"] = df["pop_age_65_plus"] / df["population"]
    df["pop_density"] = df["population"] / df["area_km2"]

    print(f"\n[2] 5개 지표 z-score + 가중합 (NRPS)")
    df["z_demand"]   = z(df["daily_boarding"])
    df["z_shortage"] = z(1.0 / df["n_routes_safe"])
    df["z_pop"]      = z(df["population"])
    df["z_elder"]    = z(df["elder_pct"])
    df["z_density"]  = z(df["pop_density"])

    df["nrps"] = (
        WEIGHTS["demand"]   * df["z_demand"]
        + WEIGHTS["shortage"] * df["z_shortage"]
        + WEIGHTS["pop"]      * df["z_pop"]
        + WEIGHTS["elder"]    * df["z_elder"]
        + WEIGHTS["density"]  * df["z_density"]
    )
    print(f"    NRPS 평균 {df['nrps'].mean():.2f} · 표준편차 {df['nrps'].std():.2f}")
    print(f"    상위 5% 임계: {df['nrps'].quantile(0.95):.2f}")

    print(f"\n[3] NRPS 상위 {N_HOTSPOTS} hotspot 추출")
    hot = df.nlargest(N_HOTSPOTS, "nrps").copy()
    print("    TOP 15:")
    show = hot.head(15)[["sigungu", "district_name", "stop_name",
                          "n_routes", "daily_boarding", "elder_pct", "nrps"]].copy()
    show["daily_boarding"] = show["daily_boarding"].round(1)
    show["elder_pct"] = (show["elder_pct"] * 100).round(1)
    show["nrps"] = show["nrps"].round(2)
    print(show.to_string(index=False))

    print(f"\n[4] KMeans 클러스터링 → {N_CLUSTERS}개 핫존")
    # 좌표만 사용. 점수 가중하려면 sample_weight 도 지원하나, 단순화
    coords = hot[["latitude", "longitude"]].values
    weights = hot["nrps"].values
    weights = weights - weights.min() + 0.1  # positive
    km = KMeans(n_clusters=N_CLUSTERS, random_state=42, n_init="auto")
    km.fit(coords, sample_weight=weights)
    hot["cluster"] = km.labels_
    centroids = km.cluster_centers_

    # 클러스터 통계
    print("\n    핫존별 통계:")
    for c in range(N_CLUSTERS):
        sub = hot[hot["cluster"] == c]
        if sub.empty:
            continue
        top_stops = sub.nlargest(3, "nrps")["stop_name"].tolist()
        avg_score = sub["nrps"].mean()
        n_stops = len(sub)
        sgg = sub["sigungu"].mode().iat[0] if not sub["sigungu"].mode().empty else "?"
        print(f"      핫존#{c}: {n_stops}개 정류장 · 평균 NRPS {avg_score:.2f} · "
              f"주요 시군구 {sgg}")
        print(f"        대표: {', '.join(top_stops)}")

    print(f"\n[5] 핫존 간 거리 행렬 + Greedy MST → corridor 5개 제안")
    # 거리 행렬 (km)
    n = len(centroids)
    dist = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i != j:
                dist[i, j] = haversine_m(*centroids[i], *centroids[j]) / 1000

    # Greedy MST (Kruskal 단순화: 가까운 페어부터 5개 — overlap 허용)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append((dist[i, j], i, j))
    pairs.sort()
    # 단순화: 짧은 5개 페어 선택 (실제 MST 가 아닌 corridor 추천 5개)
    corridors = pairs[:5]

    proposals = []
    for k, (d, i, j) in enumerate(corridors, 1):
        c1_stops = hot[hot["cluster"] == i].nlargest(5, "nrps")
        c2_stops = hot[hot["cluster"] == j].nlargest(5, "nrps")
        c1_name = c1_stops["stop_name"].iat[0] if not c1_stops.empty else "?"
        c2_name = c2_stops["stop_name"].iat[0] if not c2_stops.empty else "?"
        proposals.append({
            "corridor_id": k,
            "from_cluster": i, "to_cluster": j,
            "from_anchor": c1_name, "to_anchor": c2_name,
            "distance_km": round(d, 2),
            "from_lat": centroids[i][0], "from_lon": centroids[i][1],
            "to_lat": centroids[j][0],   "to_lon": centroids[j][1],
            "from_top_stops": " · ".join(c1_stops["stop_name"].tolist()),
            "to_top_stops":   " · ".join(c2_stops["stop_name"].tolist()),
            "from_avg_nrps": round(c1_stops["nrps"].mean(), 2),
            "to_avg_nrps":   round(c2_stops["nrps"].mean(), 2),
        })

    prop_df = pd.DataFrame(proposals)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    prop_df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n    제안 노선:")
    print(prop_df[["corridor_id", "from_anchor", "to_anchor",
                   "distance_km", "from_avg_nrps", "to_avg_nrps"]].to_string(index=False))
    print(f"    → {OUT_CSV.relative_to(PROJECT_ROOT)}")

    print(f"\n[6] 지도 생성")
    m = folium.Map(location=(35.230, 128.650), zoom_start=11,
                   tiles="cartodbpositron", control_scale=True)

    # NRPS 색상 (분위)
    cluster_colors = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12",
                      "#9b59b6", "#1abc9c", "#e67e22"]

    # 일반 정류장 (배경)
    grp_bg = folium.FeatureGroup(name="배경 정류장", show=False)
    for _, r in df.iterrows():
        folium.CircleMarker(
            location=(r["latitude"], r["longitude"]),
            radius=1, color="#ddd", weight=0.3,
            fill=True, fill_color="#eee", fill_opacity=0.3,
        ).add_to(grp_bg)
    grp_bg.add_to(m)

    # Hotspot 클러스터별
    for c in range(N_CLUSTERS):
        sub = hot[hot["cluster"] == c]
        if sub.empty:
            continue
        color = cluster_colors[c % len(cluster_colors)]
        grp = folium.FeatureGroup(name=f"핫존 #{c} ({len(sub)})", show=True)
        for _, r in sub.iterrows():
            radius = 4 + min(r["nrps"] * 2, 10)
            popup_html = (
                f"<b>{r['stop_name']}</b><br>"
                f"{r['sigungu']} · {r['district_name']}<br>"
                f"노선 {int(r['n_routes'])} / 일승차 {r['daily_boarding']:.0f}<br>"
                f"고령 {r['elder_pct']*100:.1f}% · 인구밀도 {r['pop_density']:.0f}/㎢<br>"
                f"<b>NRPS: {r['nrps']:.2f}</b> (핫존 #{c})"
            )
            folium.CircleMarker(
                location=(r["latitude"], r["longitude"]),
                radius=radius, color=color, weight=1,
                fill=True, fill_color=color, fill_opacity=0.8,
                tooltip=f"{r['stop_name']} NRPS={r['nrps']:.2f}",
                popup=folium.Popup(popup_html, max_width=280),
            ).add_to(grp)
        # 클러스터 centroid 도 표시
        folium.Marker(
            location=centroids[c],
            icon=folium.DivIcon(
                html=f'<div style="background:{color};color:white;width:24px;height:24px;'
                     f'border-radius:50%;display:flex;align-items:center;justify-content:center;'
                     f'font-weight:bold;font-size:12px;border:2px solid white;">'
                     f'C{c}</div>'
            ),
            tooltip=f"핫존 #{c} 중심",
        ).add_to(grp)
        grp.add_to(m)

    # 제안 corridor 5개
    grp_prop = folium.FeatureGroup(name=f"⭐ 제안 신규 노선 5개", show=True)
    for k, p in enumerate(proposals, 1):
        path = [(p["from_lat"], p["from_lon"]), (p["to_lat"], p["to_lon"])]
        popup_html = (
            f"<b>신규 노선 #{k}: {p['from_anchor']} ↔ {p['to_anchor']}</b><br>"
            f"거리 ~{p['distance_km']} km<br>"
            f"<b>출발 핫존 #{p['from_cluster']}</b><br>"
            f"  대표 정류장: {p['from_top_stops']}<br>"
            f"  평균 NRPS: {p['from_avg_nrps']:.2f}<br>"
            f"<b>도착 핫존 #{p['to_cluster']}</b><br>"
            f"  대표 정류장: {p['to_top_stops']}<br>"
            f"  평균 NRPS: {p['to_avg_nrps']:.2f}"
        )
        folium.PolyLine(
            path, color="#c0392b", weight=5, opacity=0.7,
            tooltip=f"제안 #{k}: {p['from_anchor']} ↔ {p['to_anchor']} ({p['distance_km']}km)",
            popup=folium.Popup(popup_html, max_width=320),
        ).add_to(grp_prop)
        # 중간에 번호 마커
        mid_lat = (path[0][0] + path[1][0]) / 2
        mid_lon = (path[0][1] + path[1][1]) / 2
        folium.Marker(
            location=(mid_lat, mid_lon),
            icon=folium.DivIcon(
                html=f'<div style="background:#c0392b;color:white;width:28px;height:28px;'
                     f'border-radius:50%;display:flex;align-items:center;justify-content:center;'
                     f'font-weight:bold;font-size:14px;border:2px solid white;">'
                     f'#{k}</div>'
            ),
        ).add_to(grp_prop)
    grp_prop.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # 범례
    legend_html = f"""
    <div style="position: fixed; top: 12px; right: 12px; z-index: 9999;
                background: white; padding: 10px 14px; border: 1px solid #888;
                border-radius: 6px; font-family: sans-serif; font-size: 12px;
                box-shadow: 0 2px 6px rgba(0,0,0,0.15); max-width: 280px;">
        <div style="font-weight:bold;margin-bottom:6px;">신규 노선 제안 알고리즘 (NRPS)</div>
        <div style="font-size:11px;color:#555;margin-bottom:6px;">
            상위 {N_HOTSPOTS}개 핫스팟 → KMeans({N_CLUSTERS}개 핫존)
            → 가까운 핫존 페어 5개 corridor
        </div>
        <div style="font-weight:bold;margin-top:6px;">핫존 색상</div>
    """
    for c in range(N_CLUSTERS):
        legend_html += (
            f'<div style="margin:2px 0;">'
            f'<span style="display:inline-block;width:14px;height:14px;border-radius:50%;'
            f'background:{cluster_colors[c]};margin-right:6px;vertical-align:middle;"></span>'
            f'핫존 #{c}</div>'
        )
    legend_html += """
        <div style="margin-top:6px;font-size:11px;">
            <span style="display:inline-block;width:18px;height:4px;background:#c0392b;
                vertical-align:middle;margin-right:4px;"></span>
            <b>제안 신규 노선</b>
        </div>
        <div style="font-size:10px;color:#888;margin-top:6px;">
            NRPS 가중치: 수요 0.30 / 공급부족 0.25 /<br>
            인구 0.15 / 고령 0.15 / 밀도 0.15
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    OUT_MAP.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(OUT_MAP))
    print(f"\n[7] 지도 저장: {OUT_MAP.relative_to(PROJECT_ROOT)}  "
          f"({OUT_MAP.stat().st_size/1024:,.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
