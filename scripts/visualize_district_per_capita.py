"""행정동 인구 ↔ 교통 통합 분석 지도.

레이어 (LayerControl 로 토글):
  - 인구 단계구분도 (Choropleth)
  - 1인당 일평균 승차 (boarding ÷ 인구 ÷ 28일)
  - 인구당 정류장 밀도 (1000명당 정류장 수)
  - 고령자 비율 (65+ ÷ 인구 × 100)
  - 정류장 마커 (boarding 강도)

출력: data/processed/maps/district_per_capita.html
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import branca.colormap as cm  # noqa: E402
import folium  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402

OUT = PROJECT_ROOT / "data" / "processed" / "maps" / "district_per_capita.html"
CHANGWON_CENTER = (35.230, 128.650)


def main() -> int:
    engine = get_engine()
    print("[1] 데이터 로드")
    with engine.connect() as conn:
        # 행정동별 인구 + 정류장 수 + 승하차 합
        df = pd.read_sql(
            text(
                """
                SELECT
                    d.district_id,
                    d.sigungu, d.district_name,
                    d.population, d.pop_male, d.pop_female,
                    d.pop_age_0_19, d.pop_age_20_64, d.pop_age_65_plus,
                    d.households,
                    COUNT(DISTINCT s.stop_id) AS n_stops,
                    COALESCE(SUM(bd.boarding), 0)   AS total_boarding,
                    COALESCE(SUM(bd.alighting), 0)  AS total_alighting,
                    COUNT(DISTINCT bd.date)         AS n_days,
                    ST_AsGeoJSON(d.geometry)::json AS geom
                FROM districts d
                LEFT JOIN stops s ON ST_Contains(d.geometry, s.location)
                LEFT JOIN boarding_data bd ON bd.stop_id = s.stop_id
                GROUP BY d.district_id, d.sigungu, d.district_name,
                         d.population, d.pop_male, d.pop_female,
                         d.pop_age_0_19, d.pop_age_20_64, d.pop_age_65_plus,
                         d.households, d.geometry
                """
            ),
            conn,
        )
        stops = pd.read_sql(
            text(
                """
                SELECT s.stop_id, s.stop_name, s.bis_number,
                       s.latitude, s.longitude,
                       d2.sigungu, d2.district_name,
                       COALESCE(SUM(bd.boarding), 0)  AS total_b,
                       COALESCE(SUM(bd.alighting), 0) AS total_a,
                       MAX(bd.n_tago_in_grp) AS n_grp,
                       COUNT(DISTINCT bd.date) AS n_days
                FROM stops s
                LEFT JOIN boarding_data bd ON bd.stop_id = s.stop_id
                LEFT JOIN districts d2 ON ST_Contains(d2.geometry, s.location)
                GROUP BY s.stop_id, s.stop_name, s.bis_number,
                         s.latitude, s.longitude, d2.sigungu, d2.district_name
                """
            ),
            conn,
        )
        # 정류장×시간 평균 (시간대 그래프용)
        hourly = pd.read_sql(
            text(
                """
                SELECT stop_id, hour, AVG(boarding) AS avg_b
                FROM boarding_data
                GROUP BY stop_id, hour
                ORDER BY stop_id, hour
                """
            ),
            conn,
        )

    print(f"    행정동 {len(df)}개 · 정류장 {len(stops):,}개")

    # 파생 지표
    df["per_capita_b"] = df.apply(
        lambda r: (float(r["total_boarding"]) / max(r["n_days"], 1) / r["population"])
        if r["population"] > 0 and r["n_days"] > 0 else 0.0,
        axis=1,
    )
    df["stop_per_1k"] = df.apply(
        lambda r: r["n_stops"] / r["population"] * 1000 if r["population"] > 0 else 0,
        axis=1,
    )
    df["elder_pct"] = df.apply(
        lambda r: (r["pop_age_65_plus"] / r["population"] * 100)
        if r["population"] > 0 else 0,
        axis=1,
    )

    print("[2] 지도 생성")
    m = folium.Map(location=CHANGWON_CENTER, zoom_start=11, tiles="cartodbpositron",
                   control_scale=True)

    PALETTES = {
        "Blues":   cm.linear.Blues_09,
        "YlOrRd":  cm.linear.YlOrRd_09,
        "Greens":  cm.linear.Greens_09,
        "Purples": cm.linear.Purples_09,
    }

    # 색 스케일 함수
    def add_choropleth(name, value_col, fmt, vmin=None, vmax=None, palette="YlOrRd"):
        vals = df[value_col].astype(float)
        vmin = vmin if vmin is not None else float(vals.quantile(0.05))
        vmax = vmax if vmax is not None else float(vals.quantile(0.95))
        colormap = PALETTES[palette].scale(vmin, vmax)
        colormap.caption = name
        grp = folium.FeatureGroup(name=name, show=False)
        for _, r in df.iterrows():
            v = float(r[value_col])
            color = colormap(min(max(v, vmin), vmax))
            tooltip_html = (
                f"<b>{r['sigungu']} {r['district_name']}</b><br>"
                f"인구 {int(r['population']):,} · 세대 {int(r['households']):,}<br>"
                f"정류장 {int(r['n_stops'])}개 · 총승차 {int(r['total_boarding']):,}<br>"
                f"<b>{name}: {fmt.format(v)}</b>"
            )
            folium.GeoJson(
                {"type": "Feature", "geometry": r["geom"], "properties": {}},
                style_function=lambda f, c=color: {
                    "fillColor": c, "color": "#333", "weight": 1, "fillOpacity": 0.7
                },
                tooltip=folium.Tooltip(tooltip_html, sticky=True),
            ).add_to(grp)
        grp.add_to(m)
        colormap.add_to(m)
        return colormap

    add_choropleth("인구수", "population", "{:,.0f}", palette="Blues")
    add_choropleth("1인당 일평균 승차", "per_capita_b", "{:.3f}", palette="YlOrRd")
    add_choropleth("1000명당 정류장 수", "stop_per_1k", "{:.2f}", palette="Greens")
    add_choropleth("65세이상 비율 (%)", "elder_pct", "{:.1f}%", palette="Purples")

    # 정류장 마커 (시간대 그래프 + 매핑 정보 popup)
    print("[2.5] 시간대별 데이터 정리")
    hourly_by_stop: dict[str, list[float]] = {}
    for stop_id, grp in hourly.groupby("stop_id"):
        h_map = dict(zip(grp["hour"].astype(int), grp["avg_b"].astype(float)))
        hourly_by_stop[stop_id] = [h_map.get(h, 0.0) for h in range(24)]

    import math
    stop_grp = folium.FeatureGroup(name=f"정류장 ({len(stops):,})", show=True)
    max_b = float(stops["total_b"].max()) if len(stops) else 1

    for _, s in stops.iterrows():
        b = float(s["total_b"])
        a = float(s["total_a"])
        has_data = b > 0 or a > 0

        if has_data:
            radius = 3 + 7 * math.sqrt(min(b / max_b, 1.0)) if max_b > 0 else 3
            ratio = min(b / max_b, 1.0) if max_b > 0 else 0
            hue = int(60 * (1 - ratio))  # 60(노랑) → 0(빨강)
            color = f"hsl({hue}, 80%, 50%)"
        else:
            radius = 2
            color = "#bbb"

        # 시간대 막대 그래프
        hours = hourly_by_stop.get(s["stop_id"], [0] * 24)
        max_h = max(hours) if max(hours) > 0 else 1
        bar_html = ""
        for h, v in enumerate(hours):
            bar_h = int((v / max_h) * 30) if max_h > 0 else 0
            bar_html += (
                f'<div style="display:inline-block;width:10px;height:{bar_h+1}px;'
                f'background:#3498db;vertical-align:bottom;margin-right:1px;"></div>'
            )
        hour_labels = "".join(
            f'<span style="display:inline-block;width:11px;font-size:8px;'
            f'text-align:center;color:#888;">{h if h % 3 == 0 else ""}</span>'
            for h in range(24)
        )

        sigungu = s["sigungu"] if pd.notna(s["sigungu"]) else "(미상)"
        dong = s["district_name"] if pd.notna(s["district_name"]) else "(미상)"
        ars = int(s["bis_number"]) if pd.notna(s["bis_number"]) else "—"
        n_grp = int(s["n_grp"]) if pd.notna(s["n_grp"]) else "-"
        n_days = int(s["n_days"]) if pd.notna(s["n_days"]) else 0

        if has_data:
            popup_html = (
                f"<div style='min-width:340px;'>"
                f"<div style='font-weight:bold;font-size:14px;'>{s['stop_name']}</div>"
                f"<div style='color:#666;font-size:12px;margin-top:2px;'>"
                f"{sigungu} · {dong} · ARS {ars}</div>"
                f"<div style='font-size:12px;margin-top:8px;'>"
                f"<b>수집 기간:</b> {n_days}일</div>"
                f"<div style='font-size:12px;'>"
                f"<b>총 승차:</b> <span style='color:#e74c3c;'>{int(b):,}</span> · "
                f"<b>총 하차:</b> <span style='color:#3498db;'>{int(a):,}</span></div>"
                f"<div style='font-size:11px;color:#666;'>"
                f"매핑 그룹 크기: {n_grp} (TAGO 정류장 수)</div>"
                f"<div style='margin-top:8px;font-weight:bold;font-size:12px;'>"
                f"시간대별 평균 승차 (00~23시)</div>"
                f"<div style='border-bottom:1px solid #ccc;padding-bottom:2px;'>{bar_html}</div>"
                f"<div>{hour_labels}</div></div>"
            )
        else:
            popup_html = (
                f"<div style='min-width:200px;'>"
                f"<div style='font-weight:bold;font-size:14px;'>{s['stop_name']}</div>"
                f"<div style='color:#666;font-size:12px;'>{sigungu} · {dong}</div>"
                f"<div style='font-size:12px;margin-top:6px;color:#999;'>"
                f"(STCIS 데이터 없음 — 미매핑 정류장)</div></div>"
            )

        folium.CircleMarker(
            location=(s["latitude"], s["longitude"]),
            radius=radius, color=color, weight=1,
            fill=True, fill_color=color, fill_opacity=0.75,
            tooltip=f"{s['stop_name']} (승차 {int(b):,})",
            popup=folium.Popup(popup_html, max_width=380),
        ).add_to(stop_grp)
    stop_grp.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(OUT))
    size_kb = OUT.stat().st_size / 1024
    print(f"[3] 저장 → {OUT.relative_to(PROJECT_ROOT)}  ({size_kb:,.0f} KB)")

    print("\n[요약]")
    print("  인구당 일평균 승차 TOP 5:")
    for _, r in df.sort_values("per_capita_b", ascending=False).head(5).iterrows():
        print(f"    {r['sigungu']:<14} {r['district_name']:<10} {r['per_capita_b']:.3f} (인구 {int(r['population']):,})")
    print("  65세+ 비율 TOP 5:")
    for _, r in df.sort_values("elder_pct", ascending=False).head(5).iterrows():
        print(f"    {r['sigungu']:<14} {r['district_name']:<10} {r['elder_pct']:.1f}%")
    print("  1000명당 정류장 TOP 5 (외곽 시골):")
    for _, r in df.sort_values("stop_per_1k", ascending=False).head(5).iterrows():
        print(f"    {r['sigungu']:<14} {r['district_name']:<10} {r['stop_per_1k']:.2f}개/1000명 ({int(r['n_stops'])}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
