"""교통 공급·수요 고급 분석.

지표:
  1. 시간대별 부하 (정류장별 출근/퇴근/양피크/균등 분류)
  2. 인구·고령자 가중치 (1인당 부담 + 교통약자 보정)
  3. 노선 구간 분석 (인접 정류장 페어)

색상 카테고리 (지도):
  🔴 morning_peak  : 출근(7~9시) 비중 ≥ 35%
  🔵 evening_peak  : 퇴근(17~19시) 비중 ≥ 30%
  🟣 dual_peak     : 양 피크 모두 강함
  🟢 even          : 시간대 분산
  ⚫ low_traffic   : 일평균 승차 < 50명 (참고)

가중 부하:
  weighted_load = (daily_boarding / n_routes) × (1 + 2 × elder_pct)
  → 인구 약자 지역의 부하를 더 중요하게 평가

출력:
  - data/processed/maps/load_patterns_map.html  (메인 지도)
  - data/processed/load_segment_top.csv         (구간 TOP)
  - 콘솔에 카테고리·구간 통계 표
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
import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402

OUT_MAP = PROJECT_ROOT / "data" / "processed" / "maps" / "load_patterns_map.html"
OUT_SEG_CSV = PROJECT_ROOT / "data" / "processed" / "load_segment_top.csv"


CATEGORY_COLORS = {
    "morning_peak": "#e74c3c",   # 빨강 = 출근 피크형
    "evening_peak": "#3498db",   # 파랑 = 퇴근 피크형
    "dual_peak":    "#9b59b6",   # 보라 = 양 피크
    "even":         "#27ae60",   # 초록 = 분산형
    "low_traffic":  "#bdc3c7",   # 회색 = 저이용
}


def classify_pattern(hour_dist: dict[int, float]) -> str:
    """시간대별 비중 → 패턴 분류."""
    total = sum(hour_dist.values())
    if total < 50:  # 일평균 50명 미만은 저이용
        return "low_traffic"

    morning_share = sum(hour_dist.get(h, 0) for h in (7, 8, 9)) / total
    evening_share = sum(hour_dist.get(h, 0) for h in (17, 18, 19)) / total

    m_strong = morning_share >= 0.35
    e_strong = evening_share >= 0.30

    if m_strong and e_strong:
        return "dual_peak"
    if m_strong:
        return "morning_peak"
    if e_strong:
        return "evening_peak"
    return "even"


def main() -> int:
    engine = get_engine()
    print("[1] 정류장·시간대 데이터 로드")
    with engine.connect() as conn:
        stops = pd.read_sql(
            text(
                """
                SELECT
                    s.stop_id, s.stop_name, s.latitude, s.longitude,
                    d.sigungu, d.district_name,
                    d.population, d.pop_age_65_plus,
                    COUNT(DISTINCT rs.route_id) AS n_routes,
                    COALESCE(SUM(bd.boarding), 0) AS total_boarding,
                    COUNT(DISTINCT bd.date) AS n_days
                FROM stops s
                LEFT JOIN route_stops rs ON rs.stop_id = s.stop_id
                LEFT JOIN boarding_data bd ON bd.stop_id = s.stop_id
                LEFT JOIN districts d ON ST_Contains(d.geometry, s.location)
                GROUP BY s.stop_id, s.stop_name, s.latitude, s.longitude,
                         d.sigungu, d.district_name, d.population, d.pop_age_65_plus
                """
            ),
            conn,
        )
        hourly = pd.read_sql(
            text(
                """
                SELECT stop_id, hour, AVG(boarding) AS avg_b
                FROM boarding_data
                GROUP BY stop_id, hour
                """
            ),
            conn,
        )

    print(f"    정류장 {len(stops):,} · 시간대 행 {len(hourly):,}")

    # 시간대 dict
    hour_by_stop = (
        hourly.groupby("stop_id")
        .apply(lambda g: dict(zip(g["hour"].astype(int), g["avg_b"].astype(float))))
        .to_dict()
    )

    # 정류장 단위 메트릭 계산
    valid = stops[(stops["n_routes"] > 0) & (stops["n_days"] > 0)].copy()
    valid["daily_boarding"] = valid["total_boarding"] / valid["n_days"]
    valid["load_per_route"] = valid["daily_boarding"] / valid["n_routes"]
    valid["elder_pct"] = valid.apply(
        lambda r: r["pop_age_65_plus"] / r["population"]
        if r["population"] and r["population"] > 0 else 0,
        axis=1,
    )
    valid["weighted_load"] = valid["load_per_route"] * (1 + 2 * valid["elder_pct"])

    # 시간대 패턴 분류
    valid["pattern"] = valid["stop_id"].map(
        lambda s: classify_pattern(hour_by_stop.get(s, {}))
    )

    print("\n[2] 시간대 패턴 분포")
    print(valid["pattern"].value_counts().to_string())

    print("\n[3] 가중 부하 TOP 20 (가중치 = 부하 × (1 + 2 × 고령자비율))")
    top = valid.nlargest(20, "weighted_load")[
        ["sigungu", "district_name", "stop_name", "pattern",
         "n_routes", "daily_boarding", "load_per_route",
         "elder_pct", "weighted_load"]
    ].copy()
    top["daily_boarding"] = top["daily_boarding"].round(1)
    top["load_per_route"] = top["load_per_route"].round(1)
    top["elder_pct"] = (top["elder_pct"] * 100).round(1)
    top["weighted_load"] = top["weighted_load"].round(1)
    print(top.to_string(index=False))

    print("\n[4] 구간 분석 (노선상 인접 정류장 페어)")
    with engine.connect() as conn:
        segments = pd.read_sql(
            text(
                """
                WITH ordered AS (
                    SELECT rs.route_id, rs.direction, rs.sequence, rs.stop_id,
                           LEAD(rs.stop_id) OVER (
                               PARTITION BY rs.route_id, rs.direction
                               ORDER BY rs.sequence
                           ) AS next_stop_id
                    FROM route_stops rs
                ),
                stop_b AS (
                    SELECT bd.stop_id,
                           COALESCE(SUM(bd.boarding), 0) /
                               NULLIF(COUNT(DISTINCT bd.date), 0) AS daily_b
                    FROM boarding_data bd
                    GROUP BY bd.stop_id
                )
                SELECT
                    r.route_no AS route_id_tago,
                    r.route_name,
                    r.route_type,
                    o.direction,
                    s1.stop_name AS from_stop,
                    s2.stop_name AS to_stop,
                    s1.stop_id AS from_id,
                    s2.stop_id AS to_id,
                    s1.latitude AS from_lat, s1.longitude AS from_lon,
                    s2.latitude AS to_lat,   s2.longitude AS to_lon,
                    COALESCE(b1.daily_b, 0) AS from_daily,
                    COALESCE(b2.daily_b, 0) AS to_daily,
                    (COALESCE(b1.daily_b, 0) + COALESCE(b2.daily_b, 0)) / 2 AS avg_segment_load
                FROM ordered o
                JOIN routes r ON r.route_id = o.route_id
                JOIN stops s1 ON s1.stop_id = o.stop_id
                JOIN stops s2 ON s2.stop_id = o.next_stop_id
                LEFT JOIN stop_b b1 ON b1.stop_id = o.stop_id
                LEFT JOIN stop_b b2 ON b2.stop_id = o.next_stop_id
                WHERE o.next_stop_id IS NOT NULL
                """
            ),
            conn,
        )
    print(f"    전체 구간(방향 포함): {len(segments):,}개")

    top_seg = segments.nlargest(30, "avg_segment_load")[
        ["route_name", "route_type", "direction", "from_stop", "to_stop",
         "from_daily", "to_daily", "avg_segment_load"]
    ].copy()
    top_seg["from_daily"] = top_seg["from_daily"].round(1)
    top_seg["to_daily"] = top_seg["to_daily"].round(1)
    top_seg["avg_segment_load"] = top_seg["avg_segment_load"].round(1)
    print("\n  TOP 30 구간 (인접 정류장 평균 일승차):")
    print(top_seg.head(20).to_string(index=False))

    OUT_SEG_CSV.parent.mkdir(parents=True, exist_ok=True)
    segments.sort_values("avg_segment_load", ascending=False).to_csv(
        OUT_SEG_CSV, index=False, encoding="utf-8-sig"
    )
    print(f"\n  → 전체 구간 저장: {OUT_SEG_CSV.relative_to(PROJECT_ROOT)}")

    print("\n[5] 지도 생성")
    m = folium.Map(location=(35.230, 128.650), zoom_start=11,
                   tiles="cartodbpositron", control_scale=True)

    # 행정동 배경 (고령자 비율 색)
    with engine.connect() as conn:
        d_geo = conn.execute(text(
            """
            SELECT json_build_object('type','FeatureCollection',
                'features', json_agg(json_build_object(
                    'type','Feature',
                    'properties', json_build_object(
                        'sigungu', sigungu, 'district_name', district_name,
                        'elder_pct',
                        CASE WHEN population > 0
                             THEN pop_age_65_plus::float / population
                             ELSE 0 END),
                    'geometry', ST_AsGeoJSON(geometry)::json)))
            FROM districts
            """
        )).scalar()

    def elder_color(f):
        ep = f["properties"]["elder_pct"]
        if ep >= 0.30:
            c = "#9b59b6"
        elif ep >= 0.20:
            c = "#c39bd3"
        elif ep >= 0.13:
            c = "#e8daef"
        else:
            c = "#ffffff"
        return {"fillColor": c, "color": "#888", "weight": 0.5, "fillOpacity": 0.35}

    folium.GeoJson(
        d_geo, name="행정동 (고령자 비율 색)",
        style_function=elder_color,
        tooltip=folium.GeoJsonTooltip(
            fields=["sigungu", "district_name", "elder_pct"],
            aliases=["구:", "동:", "65+비율:"],
        ),
    ).add_to(m)

    # 정류장 마커 (패턴별 색, 크기는 weighted_load)
    max_w = float(valid["weighted_load"].quantile(0.97))

    pattern_groups = {p: folium.FeatureGroup(
        name=f"{p} ({(valid['pattern']==p).sum()})", show=(p != "low_traffic"))
        for p in CATEGORY_COLORS}

    for _, r in valid.iterrows():
        pat = r["pattern"]
        color = CATEGORY_COLORS[pat]
        w = float(r["weighted_load"])
        radius = 2 + 10 * math.sqrt(min(w / max_w, 1.0)) if max_w > 0 else 2

        # 시간대 막대그래프
        hd = hour_by_stop.get(r["stop_id"], {})
        hours = [hd.get(h, 0.0) for h in range(24)]
        max_h = max(hours) if max(hours) > 0 else 1
        bar_html = ""
        for h, v in enumerate(hours):
            bh = int((v / max_h) * 30) if max_h > 0 else 0
            bcolor = "#e74c3c" if h in (7, 8, 9) else ("#3498db" if h in (17, 18, 19) else "#95a5a6")
            bar_html += (
                f'<div style="display:inline-block;width:9px;height:{bh+1}px;'
                f'background:{bcolor};vertical-align:bottom;margin-right:1px;"></div>'
            )
        hour_labels = "".join(
            f'<span style="display:inline-block;width:10px;font-size:8px;'
            f'text-align:center;color:#888;">{h if h % 3 == 0 else ""}</span>'
            for h in range(24)
        )

        sigungu = r["sigungu"] if pd.notna(r["sigungu"]) else "(미상)"
        dong = r["district_name"] if pd.notna(r["district_name"]) else "(미상)"
        elder = r["elder_pct"] * 100

        popup_html = (
            f"<div style='min-width:300px;'>"
            f"<div style='font-weight:bold;font-size:14px;'>{r['stop_name']}</div>"
            f"<div style='color:#666;font-size:12px;'>{sigungu} · {dong}</div>"
            f"<div style='font-size:12px;margin-top:6px;'>"
            f"<b>패턴:</b> <span style='color:{color};font-weight:bold;'>{pat}</span></div>"
            f"<div style='font-size:12px;'>"
            f"노선 <b>{int(r['n_routes'])}</b>개 · 일평균 승차 <b>{r['daily_boarding']:.0f}</b></div>"
            f"<div style='font-size:12px;'>"
            f"노선당 부하 <b>{r['load_per_route']:.0f}</b> · "
            f"가중 부하 <b style='color:#e74c3c;'>{w:.0f}</b></div>"
            f"<div style='font-size:11px;color:#666;'>"
            f"동 고령자 비율 {elder:.1f}%</div>"
            f"<div style='margin-top:8px;font-size:11px;font-weight:bold;'>"
            f"시간대별 평균 승차 (빨강=출근, 파랑=퇴근)</div>"
            f"<div style='border-bottom:1px solid #ccc;padding-bottom:2px;'>{bar_html}</div>"
            f"<div>{hour_labels}</div></div>"
        )

        folium.CircleMarker(
            location=(r["latitude"], r["longitude"]),
            radius=radius, color=color, weight=1,
            fill=True, fill_color=color, fill_opacity=0.75,
            tooltip=f"{r['stop_name']} [{pat}] 가중부하 {w:.0f}",
            popup=folium.Popup(popup_html, max_width=340),
        ).add_to(pattern_groups[pat])

    for grp in pattern_groups.values():
        grp.add_to(m)

    # TOP 30 구간 polyline (빨강 굵게)
    seg_grp = folium.FeatureGroup(name=f"🚨 부하 TOP 30 구간", show=True)
    for _, s in top_seg.iterrows():
        # 좌표는 segments 원본에서
        row = segments[(segments["from_id"] == s.get("from_id", "")) &
                       (segments["to_id"] == s.get("to_id", ""))]
        if row.empty:
            continue
        row = row.iloc[0]
        load = float(s["avg_segment_load"])
        weight = 3 + min(load / 1000, 10)
        folium.PolyLine(
            [(row["from_lat"], row["from_lon"]), (row["to_lat"], row["to_lon"])],
            color="#c0392b", weight=weight, opacity=0.6,
            tooltip=(f"{row['route_name']} ({row['direction']}) "
                     f"{row['from_stop']} → {row['to_stop']} "
                     f"평균 {load:.0f}명"),
        ).add_to(seg_grp)
    seg_grp.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # 범례
    legend = """
    <div style="position: fixed; top: 12px; right: 12px; z-index: 9999;
                background: white; padding: 10px 14px; border: 1px solid #888;
                border-radius: 6px; font-family: sans-serif; font-size: 13px;
                box-shadow: 0 2px 6px rgba(0,0,0,0.15); max-width: 260px;">
        <div style="font-weight:bold;margin-bottom:6px;">시간대 패턴 (정류장 색)</div>
        <div style="margin:3px 0;">
            <span style="display:inline-block;width:14px;height:14px;border-radius:50%;
                background:#e74c3c;margin-right:6px;vertical-align:middle;"></span>
            출근 피크형 (7~9시 ≥35%)
        </div>
        <div style="margin:3px 0;">
            <span style="display:inline-block;width:14px;height:14px;border-radius:50%;
                background:#3498db;margin-right:6px;vertical-align:middle;"></span>
            퇴근 피크형 (17~19시 ≥30%)
        </div>
        <div style="margin:3px 0;">
            <span style="display:inline-block;width:14px;height:14px;border-radius:50%;
                background:#9b59b6;margin-right:6px;vertical-align:middle;"></span>
            양 피크형 (둘 다 강함)
        </div>
        <div style="margin:3px 0;">
            <span style="display:inline-block;width:14px;height:14px;border-radius:50%;
                background:#27ae60;margin-right:6px;vertical-align:middle;"></span>
            분산형
        </div>
        <div style="margin:3px 0;">
            <span style="display:inline-block;width:14px;height:14px;border-radius:50%;
                background:#bdc3c7;margin-right:6px;vertical-align:middle;"></span>
            저이용 (50명/일 미만)
        </div>
        <div style="font-size:11px;color:#666;margin-top:6px;">
            크기 = 가중 부하<br>= 부하 × (1 + 2 × 고령자비율)
        </div>
        <hr style="margin:6px 0;">
        <div style="font-size:11px;">
            <b>행정동 배경 (보라 농도)</b><br>고령자 비율 13%·20%·30%+
        </div>
        <div style="font-size:11px;margin-top:4px;">
            <span style="display:inline-block;width:16px;height:3px;background:#c0392b;
                vertical-align:middle;margin-right:4px;"></span>
            구간 TOP 30 (인접 정류장 평균)
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))

    OUT_MAP.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(OUT_MAP))
    print(f"\n[6] 지도 저장: {OUT_MAP.relative_to(PROJECT_ROOT)}  ({OUT_MAP.stat().st_size/1024:,.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
