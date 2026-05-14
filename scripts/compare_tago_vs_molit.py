"""TAGO API 정류소 마스터 vs 국토교통부 CSV — 창원(38010) 비교.

**용도:** TAGO 가 메인 마스터지만 누락분이 있는지 주기적으로 확인.
          TAGO 가 갱신되거나 새 MOLIT CSV (data.go.kr/15067528) 가 나오면
          이 스크립트로 재비교 → 비교 표를 data/README.md 의 "TAGO vs 국토부 CSV
          비교 결과" 섹션에 갱신.

**입력:**
  - TAGO: PostGIS `stops` 테이블 (collect_tago.py 로 적재됨)
  - MOLIT: data/raw/stops/국토교통부_전국 버스정류장 위치정보_*.csv (CP949)

**출력:** 표준출력에 비교 표/통계. (파일 저장 없음 — 결과는 README 로 옮길 것)

**2026-05-14 기준 비교 결과** (참고): 공통 2,741건 (좌표 96% 가 1m 이내),
    MOLIT-only 777건 (주로 장유·외곽), TAGO-only 10건 (신규 정류장).
    상세는 data/README.md 의 "1. 국토교통부 전국 버스정류장 위치 CSV" 섹션.

리포트 항목:
  1) 행 수 / 컬럼 차이
  2) 식별자(stop_id, mobile_no) 매칭 결과
  3) 한쪽에만 있는 정류장 수와 샘플
  4) 양쪽에 있는 정류장의 좌표·이름 차이
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import math

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402

MOLIT_CSV = PROJECT_ROOT / "data" / "raw" / "stops" / "국토교통부_전국 버스정류장 위치정보_20251031.csv"
CHANGWON_CITY_CODE = 38010


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def load_tago() -> pd.DataFrame:
    engine = get_engine()
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                """
                SELECT stop_id, stop_name, bis_number, latitude, longitude
                FROM stops
                """
            ),
            conn,
        )
    df["mobile_no"] = pd.to_numeric(df["bis_number"], errors="coerce").astype("Int64")
    return df


def load_molit() -> pd.DataFrame:
    raw = pd.read_csv(MOLIT_CSV, encoding="cp949")
    cw = raw[raw["도시코드"] == CHANGWON_CITY_CODE].copy()
    cw = cw.rename(
        columns={
            "정류장번호": "stop_id",
            "정류장명": "stop_name",
            "위도": "latitude",
            "경도": "longitude",
            "모바일단축번호": "mobile_no",
        }
    )
    cw["mobile_no"] = pd.to_numeric(cw["mobile_no"], errors="coerce").astype("Int64")
    return cw[["stop_id", "stop_name", "mobile_no", "latitude", "longitude"]]


def show_overlap(name: str, a: pd.Series, b: pd.Series) -> None:
    sa, sb = set(a.dropna()), set(b.dropna())
    common = sa & sb
    print(
        f"  {name:14s}  TAGO {len(sa):>5,}  MOLIT {len(sb):>5,}  "
        f"공통 {len(common):>5,}  TAGO-only {len(sa - sb):>5,}  MOLIT-only {len(sb - sa):>5,}"
    )


def main() -> int:
    print("[1] 데이터 로드")
    tago = load_tago()
    molit = load_molit()
    print(f"  TAGO  rows={len(tago):,}  cols={list(tago.columns)}")
    print(f"  MOLIT rows={len(molit):,}  cols={list(molit.columns)}")

    print("\n[2] 좌표 결측")
    print(f"  TAGO  lat NaN={tago['latitude'].isna().sum()}  lon NaN={tago['longitude'].isna().sum()}")
    print(f"  MOLIT lat NaN={molit['latitude'].isna().sum()}  lon NaN={molit['longitude'].isna().sum()}")

    print("\n[3] 식별자 매칭 (양쪽 set 비교)")
    show_overlap("stop_id 동일", tago["stop_id"], molit["stop_id"])
    show_overlap("mobile_no 동일", tago["mobile_no"], molit["mobile_no"])

    print("\n[4] stop_id prefix 분포")
    print("  TAGO  :")
    print(tago["stop_id"].str.slice(0, 3).value_counts().head(10).to_string())
    print("  MOLIT :")
    print(molit["stop_id"].str.slice(0, 3).value_counts().head(10).to_string())

    print("\n[5] 양쪽 공통(stop_id) 정류장 — 좌표·이름 차이")
    merged = tago.merge(
        molit,
        on="stop_id",
        suffixes=("_tago", "_molit"),
        how="inner",
    )
    print(f"  공통 행: {len(merged):,}")
    if len(merged):
        merged["dist_m"] = merged.apply(
            lambda r: haversine_m(
                r["latitude_tago"], r["longitude_tago"],
                r["latitude_molit"], r["longitude_molit"],
            ) if pd.notna(r["latitude_tago"]) and pd.notna(r["latitude_molit"]) else None,
            axis=1,
        )
        merged["name_diff"] = merged["stop_name_tago"] != merged["stop_name_molit"]
        print(f"  좌표 정확히 동일(±0m)     : {(merged['dist_m'] == 0).sum():,}")
        print(f"  좌표 1m 이내              : {(merged['dist_m'] < 1).sum():,}")
        print(f"  좌표 10m 이내             : {(merged['dist_m'] < 10).sum():,}")
        print(f"  좌표 50m 이상 차이        : {(merged['dist_m'] >= 50).sum():,}")
        print(f"  이름이 다른 행            : {merged['name_diff'].sum():,}")
        print(f"  좌표 차이 통계 (m): mean={merged['dist_m'].mean():.2f}  "
              f"median={merged['dist_m'].median():.2f}  max={merged['dist_m'].max():.2f}")

        big = merged.sort_values("dist_m", ascending=False).head(10)
        print("\n  [좌표 차이 큰 TOP 10]")
        for _, r in big.iterrows():
            print(
                f"    {r['stop_id']}  dist={r['dist_m']:7.1f}m  "
                f"name(tago)={r['stop_name_tago'][:18]:<18s}  "
                f"name(molit)={r['stop_name_molit'][:18]}"
            )

        diff_name = merged[merged["name_diff"]].head(10)
        if len(diff_name):
            print("\n  [이름이 다른 정류장 TOP 10]")
            for _, r in diff_name.iterrows():
                print(
                    f"    {r['stop_id']}  TAGO={r['stop_name_tago']:<20s} "
                    f"MOLIT={r['stop_name_molit']}"
                )

    print("\n[6] 한쪽에만 있는 정류장 — 샘플")
    tago_only = tago[~tago["stop_id"].isin(molit["stop_id"])]
    molit_only = molit[~molit["stop_id"].isin(tago["stop_id"])]
    print(f"  TAGO 만 있음 : {len(tago_only):,}")
    if len(tago_only):
        print(tago_only.head(8).to_string(index=False))
    print(f"\n  MOLIT 만 있음: {len(molit_only):,}")
    if len(molit_only):
        print(molit_only.head(8).to_string(index=False))

    print("\n[7] 좌표 범위 비교")
    print(f"  TAGO  lat {tago['latitude'].min():.4f}~{tago['latitude'].max():.4f}  "
          f"lon {tago['longitude'].min():.4f}~{tago['longitude'].max():.4f}")
    print(f"  MOLIT lat {molit['latitude'].min():.4f}~{molit['latitude'].max():.4f}  "
          f"lon {molit['longitude'].min():.4f}~{molit['longitude'].max():.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
