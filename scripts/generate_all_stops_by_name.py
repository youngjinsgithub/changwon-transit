"""TAGO 창원 전체 정류장 2,751개의 stops_by_name CSV 생성.

stcis_build_mapping.py 의 입력 포맷에 맞춰 생성:
  컬럼: 정류장명, 시군구, 읍면동, 동내동명N, 카카오맵, 네이버맵, ARS, stop_id, lat, lon

산출물: data/raw/stcis/stops_by_name_full.csv (전체 2,751)
       (priority 938 만 담은 stops_by_name_all.csv 는 그대로 둠)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from urllib.parse import quote

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402

OUT_CSV = PROJECT_ROOT / "data" / "raw" / "stcis" / "stops_by_name_full.csv"


def main() -> int:
    engine = get_engine()
    print("[1] stops + 행정동 공간 조인 (창원 전체)")
    with engine.connect() as conn:
        df = pd.read_sql(
            text("""
                SELECT s.stop_id, s.stop_name, s.bis_number,
                       s.latitude, s.longitude,
                       d.sigungu, d.district_name
                FROM stops s
                LEFT JOIN districts d ON ST_Contains(d.geometry, s.location)
            """),
            conn,
        )
    print(f"    {len(df):,} 정류장")

    print("[2] 동내동명 개수 산정")
    df["__dong_filled"] = df["district_name"].fillna("__미상__")
    dup = (
        df.drop_duplicates("stop_id")
        .groupby(["stop_name", "__dong_filled"])["stop_id"]
        .count().rename("동내동명N").reset_index()
    )
    df = df.merge(dup, on=["stop_name", "__dong_filled"], how="left")

    print("[3] 지도 링크 생성")
    def kakao(row):
        name = quote(str(row["stop_name"]), safe="")
        return f"https://map.kakao.com/link/map/{name},{row['latitude']},{row['longitude']}"
    def naver(row):
        return f"https://map.naver.com/p?lng={row['longitude']}&lat={row['latitude']}&zoom=18"

    out = df.assign(
        정류장명=df["stop_name"],
        시군구=df["sigungu"].fillna("(미상)"),
        읍면동=df["district_name"].fillna("(미상)"),
        동내동명N=df["동내동명N"].astype("Int64"),
        카카오맵=df.apply(kakao, axis=1),
        네이버맵=df.apply(naver, axis=1),
        ARS=pd.to_numeric(df["bis_number"], errors="coerce").astype("Int64"),
        lat=df["latitude"].round(6),
        lon=df["longitude"].round(6),
    )[["정류장명", "시군구", "읍면동", "동내동명N", "카카오맵", "네이버맵",
       "ARS", "stop_id", "lat", "lon"]].sort_values(
        ["시군구", "읍면동", "정류장명"]
    )

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n[4] 저장 → {OUT_CSV.relative_to(PROJECT_ROOT)}  ({len(out):,} 행)")
    print(f"    유니크 정류장명: {out['정류장명'].nunique():,}")
    print(f"    시군구별 분포:")
    print(out["시군구"].value_counts().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
