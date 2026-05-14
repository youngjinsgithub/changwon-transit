"""행정동 경계 GeoJSON 다운로드 → 창원 필터 → districts 테이블 적재.

소스: https://github.com/vuski/admdongkor (통계청 SGIS 기반, WGS84)

수행:
  [1] GeoJSON 다운로드 (없으면)
  [2] 전국 → 창원시 필터
  [3] districts 테이블 INSERT (geometry: ST_GeomFromGeoJSON)
  [4] 통계 출력 (행정동 수, 면적 합)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import geopandas as gpd  # noqa: E402
import requests  # noqa: E402
from sqlalchemy import text  # noqa: E402
from tqdm import tqdm  # noqa: E402

from src.db.connection import get_engine  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)

GEO_DIR = PROJECT_ROOT / "data" / "geo"
GEOJSON_FILE = GEO_DIR / "HangJeongDong_ver20260401.geojson"
GEOJSON_URL = (
    "https://raw.githubusercontent.com/vuski/admdongkor/"
    "master/ver20260401/HangJeongDong_ver20260401.geojson"
)

CHANGWON_CITY_NAME = "창원시"


# ---------------------------------------------------------------------------
def download_if_missing() -> None:
    print(f"[1/4] GeoJSON 확보 ({GEOJSON_FILE.name})")
    if GEOJSON_FILE.exists():
        size_mb = GEOJSON_FILE.stat().st_size / 1024 / 1024
        print(f"      이미 존재 ({size_mb:.1f} MB), 스킵")
        return

    GEO_DIR.mkdir(parents=True, exist_ok=True)
    print(f"      다운로드: {GEOJSON_URL}")

    with requests.get(GEOJSON_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        with GEOJSON_FILE.open("wb") as f, tqdm(
            total=total, unit="B", unit_scale=True, desc="다운로드"
        ) as pbar:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                f.write(chunk)
                pbar.update(len(chunk))


# ---------------------------------------------------------------------------
def filter_changwon() -> gpd.GeoDataFrame:
    print("\n[2/4] 전국 → 창원 필터")
    gdf = gpd.read_file(GEOJSON_FILE)
    print(f"      전국 행정동: {len(gdf)}")
    print(f"      컬럼: {list(gdf.columns)}")

    # adm_nm 은 보통 "시도 시군구 행정동" 공백 구분 (예: "경상남도 창원시 의창구 의창동")
    name_col = "adm_nm"
    if name_col not in gdf.columns:
        # 다른 가능한 컬럼명
        candidates = [c for c in gdf.columns if "nm" in c.lower() or "name" in c.lower()]
        raise KeyError(f"행정동명 컬럼을 찾을 수 없음. 후보={candidates}")

    mask = gdf[name_col].str.contains(CHANGWON_CITY_NAME, na=False)
    cw = gdf[mask].copy()
    print(f"      창원시 행정동: {len(cw)}")

    # adm_nm 파싱: "경상남도 창원시 의창구 의창동" → sigungu="창원시 의창구", district_name="의창동"
    parts = cw[name_col].str.split(" ", expand=True)
    if parts.shape[1] >= 4:
        cw["sigungu"] = parts[1] + " " + parts[2]
        cw["district_name"] = parts[3]
    elif parts.shape[1] == 3:
        cw["sigungu"] = parts[1]
        cw["district_name"] = parts[2]
    else:
        cw["sigungu"] = "창원시"
        cw["district_name"] = cw[name_col]

    print(f"      구 분포:")
    for sg, c in cw["sigungu"].value_counts().items():
        print(f"         {sg:<15} {c}")

    return cw


# ---------------------------------------------------------------------------
def insert_districts(engine, cw: gpd.GeoDataFrame) -> int:
    print(f"\n[3/4] districts 테이블 적재 ({len(cw)}건)")

    # 기존 데이터 삭제 후 재삽입 (한 번에 통째로 갱신)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM districts"))

    inserted = 0
    with engine.begin() as conn:
        for _, r in cw.iterrows():
            # MultiPolygon 도 처리되도록 ST_GeomFromGeoJSON 사용
            geom_json = json.dumps(
                gpd.GeoSeries([r.geometry]).__geo_interface__["features"][0][
                    "geometry"
                ]
            )
            conn.execute(
                text(
                    """
                    INSERT INTO districts (district_name, sigungu, geometry)
                    VALUES (
                        :name, :sigungu,
                        ST_Multi(ST_GeomFromGeoJSON(:geom))
                    )
                    """
                ),
                {
                    "name": r["district_name"],
                    "sigungu": r["sigungu"],
                    "geom": geom_json,
                },
            )
            inserted += 1
    return inserted


# ---------------------------------------------------------------------------
def verify(engine) -> None:
    print("\n[4/4] 검증")
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT sigungu, count(*) AS n,
                       ROUND(SUM(ST_Area(geometry::geography)) / 1e6) AS area_km2
                FROM districts
                GROUP BY sigungu
                ORDER BY sigungu
                """
            )
        ).all()
        for sigungu, n, area in rows:
            print(f"      {sigungu:<15} 동={n:>3}개  면적≈{area} km²")

        # 정류장 spatial join: 동별 정류장 수
        print("\n      [동별 정류장 수 상위 10]")
        rows = conn.execute(
            text(
                """
                SELECT d.sigungu, d.district_name, count(*) AS n_stops
                FROM districts d
                JOIN stops s
                  ON ST_Contains(d.geometry, s.location)
                GROUP BY d.sigungu, d.district_name
                ORDER BY n_stops DESC
                LIMIT 10
                """
            )
        ).all()
        for sg, dn, ns in rows:
            print(f"         {sg:<12} {dn:<10} {ns:>4}")


def schema_check_or_fix(engine) -> None:
    """기존 districts.geometry 가 POLYGON 이라면 MULTIPOLYGON 으로 변경.

    스키마는 POLYGON 으로 정의되어 있는데 행정동에 MultiPolygon 이 섞이는 경우가 있어
    런타임에 컬럼 타입 확인 후 필요 시 ALTER.
    """
    with engine.connect() as conn:
        coltype = conn.execute(
            text(
                """
                SELECT type FROM geometry_columns
                WHERE f_table_name = 'districts' AND f_geometry_column = 'geometry'
                """
            )
        ).scalar()
    if coltype == "POLYGON":
        print("[schema] districts.geometry POLYGON → MULTIPOLYGON 변경")
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    ALTER TABLE districts
                    ALTER COLUMN geometry TYPE GEOMETRY(MULTIPOLYGON, 4326)
                    USING ST_Multi(geometry)
                    """
                )
            )


def main() -> int:
    engine = get_engine()
    try:
        schema_check_or_fix(engine)
        download_if_missing()
        cw = filter_changwon()
        n = insert_districts(engine, cw)
        print(f"      INSERT 완료: {n}건")
        verify(engine)
    except Exception as e:  # noqa: BLE001
        print(f"\n[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    print("\n[OK] 행정동 적재 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
