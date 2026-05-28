"""교통 공급 vs 수요 미스매치 분석.

질문: 노선은 적은데 사람은 많은 정류장(구간)은 어디?

지표 정의:
  - n_routes        : 정류장을 지나는 고유 노선 수 (공급)
  - daily_boarding  : 28일 평균 일별 승차 수 (수요)
  - load_per_route  : daily_boarding ÷ n_routes (= 노선당 일평균 승차)
                      → 높을수록 "노선당 부담" 큰 정류장 (공급 부족 의심)

출력:
  - 콘솔: TOP 정류장 (전체·구별)
  - data/processed/supply_demand_mismatch.csv  (전체 결과)
  - data/processed/maps/supply_demand_map.html (지도 시각화)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import math

import folium  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402

OUT_CSV = PROJECT_ROOT / "data" / "processed" / "supply_demand_mismatch.csv"
OUT_MAP = PROJECT_ROOT / "data" / "processed" / "maps" / "supply_demand_map.html"


def main() -> int:
    engine = get_engine()
    print("[1] DB 쿼리: 정류장별 노선 수·승차·부하")
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                """
                WITH base AS (
                    SELECT
                        s.stop_id,
                        s.stop_name,
                        s.latitude,
                        s.longitude,
                        d.sigungu,
                        d.district_name,
                        COUNT(DISTINCT rs.route_id) AS n_routes,
                        COALESCE(SUM(bd.boarding), 0) AS total_boarding,
                        COALESCE(SUM(bd.alighting), 0) AS total_alighting,
                        COUNT(DISTINCT bd.date) AS n_days
                    FROM stops s
                    LEFT JOIN route_stops rs ON rs.stop_id = s.stop_id
                    LEFT JOIN boarding_data bd ON bd.stop_id = s.stop_id
                    LEFT JOIN districts d ON ST_Contains(d.geometry, s.location)
                    GROUP BY s.stop_id, s.stop_name, s.latitude, s.longitude,
                             d.sigungu, d.district_name
                )
                SELECT
                    stop_id, stop_name, latitude, longitude,
                    sigungu, district_name,
                    n_routes,
                    total_boarding,
                    total_alighting,
                    n_days,
                    CASE WHEN n_days > 0
                         THEN total_boarding::numeric / n_days ELSE 0 END
                         AS daily_boarding,
                    CASE WHEN n_days > 0 AND n_routes > 0
                         THEN (total_boarding::numeric / n_days) / n_routes
                         ELSE 0 END AS load_per_route
                FROM base
                """
            ),
            conn,
        )

    valid = df[(df["n_routes"] > 0) & (df["daily_boarding"] > 0)].copy()
    print(f"    전체 {len(df):,}개 · 유효 (노선+승차 둘 다 있음) {len(valid):,}개")

    # 노선당 부하 percentile
    valid["pctl"] = valid["load_per_route"].rank(pct=True) * 100

    print("\n[2] 결과 TOP 20 (노선당 일평균 승차 ↓)")
    top = valid.nlargest(20, "load_per_route")[
        ["sigungu", "district_name", "stop_name",
         "n_routes", "daily_boarding", "load_per_route"]
    ]
    top["daily_boarding"] = top["daily_boarding"].round(1)
    top["load_per_route"] = top["load_per_route"].round(1)
    print(top.to_string(index=False))

    print("\n[3] '공급 부족' 정의: 노선 수 적고 (≤3) + 일평균 승차 많음 (상위 20%)")
    quart80 = valid["daily_boarding"].quantile(0.8)
    print(f"    일평균 승차 상위 20% 기준: {quart80:.1f}명/일")
    mismatch = valid[(valid["n_routes"] <= 3) & (valid["daily_boarding"] >= quart80)]
    print(f"    조건 충족 정류장: {len(mismatch)}개")

    show = mismatch.sort_values("load_per_route", ascending=False).head(20)[
        ["sigungu", "district_name", "stop_name", "n_routes",
         "daily_boarding", "load_per_route"]
    ]
    show["daily_boarding"] = show["daily_boarding"].round(1)
    show["load_per_route"] = show["load_per_route"].round(1)
    print(show.to_string(index=False))

    print("\n[4] 구별 평균 부하")
    by_sgg = (
        valid.groupby("sigungu")
        .agg(
            n_stops=("stop_id", "count"),
            avg_routes=("n_routes", "mean"),
            avg_boarding=("daily_boarding", "mean"),
            avg_load=("load_per_route", "mean"),
        )
        .round(2)
        .sort_values("avg_load", ascending=False)
    )
    print(by_sgg.to_string())

    print(f"\n[5] CSV 저장: {OUT_CSV.relative_to(PROJECT_ROOT)}")
    out_df = valid.sort_values("load_per_route", ascending=False)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    print(f"\n[6] 지도 생성")
    m = folium.Map(location=(35.230, 128.650), zoom_start=11,
                   tiles="cartodbpositron", control_scale=True)

    # 노선당 부하 색상 (낮음=노랑 → 높음=빨강)
    max_load = float(valid["load_per_route"].quantile(0.95))

    grp_normal = folium.FeatureGroup(name="일반 정류장 (작게)", show=True)
    grp_mismatch = folium.FeatureGroup(
        name=f"⭐ 공급 부족 의심 ({len(mismatch)}) — 노선≤3 + 승차 상위20%",
        show=True,
    )

    for _, r in valid.iterrows():
        load = float(r["load_per_route"])
        is_mm = (r["n_routes"] <= 3) and (r["daily_boarding"] >= quart80)

        if is_mm:
            radius = 5 + 10 * math.sqrt(min(load / max_load, 1.0))
            ratio = min(load / max_load, 1.0)
            hue = int(60 * (1 - ratio))
            color = f"hsl({hue}, 90%, 50%)"
            grp = grp_mismatch
            weight = 2
            fill_op = 0.85
        else:
            radius = 2
            color = "#bbb"
            grp = grp_normal
            weight = 0.5
            fill_op = 0.4

        sigungu = r["sigungu"] if pd.notna(r["sigungu"]) else "(미상)"
        dong = r["district_name"] if pd.notna(r["district_name"]) else "(미상)"

        popup_html = (
            f"<b>{r['stop_name']}</b><br>"
            f"<span style='color:#666;'>{sigungu} · {dong}</span><br>"
            f"노선 수: <b>{int(r['n_routes'])}</b><br>"
            f"일평균 승차: <b>{r['daily_boarding']:.1f}명</b><br>"
            f"노선당 부하: <b style='color:#e74c3c;'>{r['load_per_route']:.1f}</b>"
        )
        folium.CircleMarker(
            location=(r["latitude"], r["longitude"]),
            radius=radius, color=color, weight=weight,
            fill=True, fill_color=color, fill_opacity=fill_op,
            tooltip=f"{r['stop_name']} (부하 {r['load_per_route']:.1f})",
            popup=folium.Popup(popup_html, max_width=300),
        ).add_to(grp)

    grp_normal.add_to(m)
    grp_mismatch.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    # 범례
    legend = """
    <div style="position: fixed; top: 12px; right: 12px; z-index: 9999;
                background: white; padding: 10px 14px; border: 1px solid #888;
                border-radius: 6px; font-family: sans-serif; font-size: 13px;
                box-shadow: 0 2px 6px rgba(0,0,0,0.15); max-width: 240px;">
        <div style="font-weight:bold;margin-bottom:6px;">공급 부족 의심 정류장</div>
        <div style="font-size:11px;color:#666;margin-bottom:6px;">
            조건: 노선 ≤ 3개 + 일평균 승차 상위 20%
        </div>
        <div style="margin:4px 0;">
            <span style="display:inline-block;width:18px;height:18px;border-radius:50%;
                background:hsl(0,90%,50%);margin-right:6px;vertical-align:middle;"></span>
            높은 부하
        </div>
        <div style="margin:4px 0;">
            <span style="display:inline-block;width:8px;height:8px;border-radius:50%;
                background:hsl(60,90%,50%);margin-right:8px;vertical-align:middle;"></span>
            낮은 부하
        </div>
        <div style="font-size:10px;color:#888;margin-top:6px;">
            크기·색: 노선당 일평균 승차
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))

    OUT_MAP.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(OUT_MAP))
    size_kb = OUT_MAP.stat().st_size / 1024
    print(f"    → {OUT_MAP.relative_to(PROJECT_ROOT)}  ({size_kb:,.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
