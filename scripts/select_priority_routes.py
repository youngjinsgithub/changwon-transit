"""STCIS 승하차 데이터 수집을 위한 우선순위 노선 12개 선정.

알고리즘:
  1) 노선별 통과 행정동 set 추출 (PostGIS ST_Contains)
  2) 노선 중요도 점수 정렬 (visualize_map.py 와 동일 공식: n_stops × type_weight + n_districts × 10)
  3) Greedy + Jaccard 필터: 이미 선택된 노선과 행정동 집합 Jaccard < THRESH 인 노선만 채택
  4) 12개 모일 때까지 임계값 완화 (0.4 → 0.5 → 0.6 → 0.7)
  5) 팀원 4명에 round-robin 으로 3개씩 배정 (1위·5위·9위 → A, 2위·6위·10위 → B …)

산출물:
  - data/processed/priority_routes_12.csv   — 12개 노선 + 메타 + 담당자
  - data/processed/priority_routes_stops.csv — 노선별 정류장 마스터 (STCIS 매칭용)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402

# 등급별 가중치 (visualize_map.py 와 동일)
TYPE_WEIGHT: dict[str, float] = {
    "좌석버스": 3.0,
    "급행좌석": 3.0,
    "간선버스": 2.0,
    "지선버스": 1.0,
    "마을버스": 0.5,
    "읍면": 0.7,
}
DISTRICT_WEIGHT = 10.0

N_TARGET = 12
TEAM_MEMBERS = ["A", "B", "C", "D"]  # 4명
JACCARD_THRESHOLDS = [0.4, 0.5, 0.6, 0.7]  # 단계적 완화

OUT_ROUTES = PROJECT_ROOT / "data" / "processed" / "priority_routes_12.csv"
OUT_STOPS = PROJECT_ROOT / "data" / "processed" / "priority_routes_stops.csv"
OUT_ARS_DIR = PROJECT_ROOT / "data" / "raw" / "stcis"  # 담당자별 ARS 리스트


def load_route_info(engine) -> pd.DataFrame:
    """노선별 통과 행정동 set + 정류장 수 + 등급."""
    sql = """
        SELECT
            r.route_id,
            r.route_no,
            r.route_name,
            r.route_type,
            COUNT(DISTINCT rs.stop_id) AS n_stops,
            COUNT(DISTINCT d.district_id) AS n_districts,
            array_agg(DISTINCT d.district_id) FILTER (WHERE d.district_id IS NOT NULL) AS district_ids,
            array_agg(DISTINCT d.district_name) FILTER (WHERE d.district_name IS NOT NULL) AS district_names
        FROM routes r
        JOIN route_stops rs ON r.route_id = rs.route_id
        JOIN stops s ON rs.stop_id = s.stop_id
        LEFT JOIN districts d ON ST_Contains(d.geometry, s.location)
        GROUP BY r.route_id, r.route_no, r.route_name, r.route_type
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)

    df["type_weight"] = df["route_type"].map(TYPE_WEIGHT).fillna(1.0)
    df["importance_score"] = (
        df["n_stops"] * df["type_weight"] + df["n_districts"] * DISTRICT_WEIGHT
    )
    df["district_set"] = df["district_ids"].apply(
        lambda xs: frozenset(xs) if xs is not None and len(xs) > 0 else frozenset()
    )
    df = df.sort_values("importance_score", ascending=False).reset_index(drop=True)
    return df


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def greedy_select(df: pd.DataFrame, n: int, thresholds: list[float]) -> pd.DataFrame:
    """단계적 임계값 완화 Greedy."""
    selected: list[int] = []  # row indices

    for th in thresholds:
        if len(selected) >= n:
            break
        for i, row in df.iterrows():
            if len(selected) >= n:
                break
            if i in selected:
                continue
            cand_set = row["district_set"]
            if not cand_set:
                continue
            ok = all(jaccard(cand_set, df.loc[j, "district_set"]) < th for j in selected)
            if ok:
                selected.append(i)
        print(f"  임계값 {th}: 누적 {len(selected)}개")

    if len(selected) < n:
        print(f"  ⚠️ 모든 임계값 적용 후에도 {len(selected)}개만 선정됨 (목표 {n})")

    return df.loc[selected].reset_index(drop=True)


def assign_team(df: pd.DataFrame, members: list[str]) -> pd.DataFrame:
    """Round-robin 배정. 1·5·9 → A / 2·6·10 → B / 3·7·11 → C / 4·8·12 → D."""
    df = df.copy()
    df["담당자"] = [members[i % len(members)] for i in range(len(df))]
    df["담당순위"] = [i // len(members) + 1 for i in range(len(df))]
    return df


def load_route_stops(engine, route_ids: list[int]) -> pd.DataFrame:
    """노선별 정류장 + 시군구/읍면동 (PostGIS 공간조인)."""
    sql = """
        SELECT
            rs.route_id,
            r.route_no,
            r.route_name,
            r.route_type,
            rs.direction,
            rs.sequence,
            s.stop_id,
            s.stop_name,
            s.bis_number,
            s.latitude,
            s.longitude,
            d.sigungu,
            d.district_name
        FROM route_stops rs
        JOIN routes r ON rs.route_id = r.route_id
        JOIN stops s ON rs.stop_id = s.stop_id
        LEFT JOIN districts d ON ST_Contains(d.geometry, s.location)
        WHERE rs.route_id = ANY(:ids)
        ORDER BY rs.route_id, rs.direction, rs.sequence
    """
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params={"ids": route_ids})


def main() -> int:
    engine = get_engine()

    print("[1] 노선 메타 로드 + 행정동 set 산정")
    routes = load_route_info(engine)
    print(f"    전체 노선 {len(routes)}개, 점수 상위 5개:")
    for _, r in routes.head().iterrows():
        print(
            f"      {r['route_type']:6s} {r['route_name'][:32]:<32s} "
            f"stops={int(r['n_stops']):3d}  dist={int(r['n_districts']):2d}  "
            f"score={r['importance_score']:.0f}"
        )

    print(f"\n[2] Greedy 선정 (목표 {N_TARGET}개, Jaccard 단계 완화)")
    picked = greedy_select(routes, N_TARGET, JACCARD_THRESHOLDS)

    print(f"\n[3] 팀원 {len(TEAM_MEMBERS)}명 round-robin 배정")
    picked = assign_team(picked, TEAM_MEMBERS)

    OUT_ROUTES.parent.mkdir(parents=True, exist_ok=True)
    out_cols = [
        "담당자", "담당순위", "route_type", "route_no", "route_name",
        "n_stops", "n_districts", "importance_score", "district_names",
    ]
    picked_out = picked.copy()
    picked_out["district_names"] = picked_out["district_names"].apply(
        lambda xs: ", ".join(sorted(xs)) if xs is not None else ""
    )
    picked_out[out_cols].to_csv(OUT_ROUTES, index=False, encoding="utf-8-sig")
    print(f"    → {OUT_ROUTES.relative_to(PROJECT_ROOT)}")

    print("\n[4] 노선별 정류장 마스터 (STCIS 매칭용)")
    rs = load_route_stops(engine, picked["route_id"].tolist())
    rs = rs.merge(picked[["route_id", "담당자", "담당순위"]], on="route_id")
    rs = rs.sort_values(["담당자", "담당순위", "direction", "sequence"]).reset_index(drop=True)
    rs.to_csv(OUT_STOPS, index=False, encoding="utf-8-sig")
    print(f"    → {OUT_STOPS.relative_to(PROJECT_ROOT)}  ({len(rs):,}행)")

    print("\n[5] 선정 결과")
    print(
        f"  {'담당':<4}{'순위':>3}  {'등급':<8}{'정류장':>4} {'동':>3} {'점수':>6}  "
        f"노선명"
    )
    for _, r in picked.iterrows():
        print(
            f"  {r['담당자']:<4}{int(r['담당순위']):>3}  "
            f"{r['route_type']:<8}{int(r['n_stops']):>4} {int(r['n_districts']):>3} "
            f"{r['importance_score']:>6.0f}  {r['route_name'][:38]}"
        )

    print("\n[6] 정류장 합계 (담당자별)")
    by_member = (
        rs.groupby("담당자")
        .agg(노선수=("route_id", "nunique"), 정류장행=("stop_id", "count"),
             고유정류장=("stop_id", "nunique"))
        .reset_index()
    )
    print(by_member.to_string(index=False))

    print("\n[7] 담당자별 ARS 번호 리스트 (STCIS '정류장 ARS번호' 칸 입력용)")
    OUT_ARS_DIR.mkdir(parents=True, exist_ok=True)

    def _dump_ars(df: pd.DataFrame, path: Path) -> int:
        ars = (
            pd.to_numeric(df["bis_number"], errors="coerce")
            .dropna().astype(int).drop_duplicates().sort_values()
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(str(x) for x in ars))
        return len(ars)

    for member, grp in rs.groupby("담당자"):
        n = _dump_ars(grp, OUT_ARS_DIR / f"ars_list_{member}.txt")
        print(f"    → ars_list_{member}.txt  ({n} ARS)")

    n_all = _dump_ars(rs, OUT_ARS_DIR / "ars_list_all.txt")
    print(f"    → ars_list_all.txt  ({n_all} ARS, dedup 합본)")

    print("\n[8] 담당자별 정류장명 + 시군구/읍면동 + 지도 링크 (이름 검색용)")
    from urllib.parse import quote

    def _enrich(df: pd.DataFrame) -> pd.DataFrame:
        # 카카오맵: 좌표 기준 마커 (이름 인코딩 필요)
        # 네이버맵: 좌표 기준 핀
        def kakao(row):
            name = quote(str(row["stop_name"]), safe="")
            return f"https://map.kakao.com/link/map/{name},{row['latitude']},{row['longitude']}"
        def naver(row):
            return f"https://map.naver.com/p?lng={row['longitude']}&lat={row['latitude']}&zoom=18"

        # 동 내 동명 정류장 개수 (= STCIS 팝업에서 같이 뜨는 동명 row 수)
        # 동(읍면동)이 None 인 경우엔 그룹화 안 함 → 1
        df = df.copy()
        df["_dong_filled"] = df["district_name"].fillna("__미상__")
        dup_counts = (
            df.drop_duplicates("stop_id")
            .groupby(["stop_name", "_dong_filled"])["stop_id"]
            .count()
            .rename("동내동명N")
            .reset_index()
        )
        df = df.merge(dup_counts, on=["stop_name", "_dong_filled"], how="left")
        df = df.drop(columns=["_dong_filled"])

        return df.assign(
            정류장명=df["stop_name"],
            시군구=df["sigungu"].fillna("(미상)"),
            읍면동=df["district_name"].fillna("(미상)"),
            동내동명N=df["동내동명N"].astype("Int64"),
            카카오맵=df.apply(kakao, axis=1),
            네이버맵=df.apply(naver, axis=1),
            ARS=pd.to_numeric(df["bis_number"], errors="coerce").astype("Int64"),
            stop_id=df["stop_id"],
            lat=df["latitude"].round(6),
            lon=df["longitude"].round(6),
        )

    name_cols = ["정류장명", "시군구", "읍면동", "동내동명N",
                 "카카오맵", "네이버맵",
                 "ARS", "stop_id", "lat", "lon"]

    for member, grp in rs.groupby("담당자"):
        out = (
            _enrich(grp)[name_cols]
            .drop_duplicates(subset=["stop_id"])
            .sort_values(["시군구", "읍면동", "정류장명"])
        )
        out_csv = OUT_ARS_DIR / f"stops_by_name_{member}.csv"
        out.to_csv(out_csv, index=False, encoding="utf-8-sig")
        print(f"    → stops_by_name_{member}.csv  ({len(out)}행)")

    rs_all = (
        _enrich(rs)[name_cols]
        .drop_duplicates(subset=["stop_id"])
        .sort_values(["시군구", "읍면동", "정류장명"])
    )
    rs_all.to_csv(OUT_ARS_DIR / "stops_by_name_all.csv", index=False, encoding="utf-8-sig")
    print(f"    → stops_by_name_all.csv  ({len(rs_all)}행, dedup 합본)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
