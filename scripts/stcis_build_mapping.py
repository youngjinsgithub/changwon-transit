"""TAGO stop_id ↔ STCIS sttnId 매핑 빌드.

priority_routes_stops.csv 의 정류장 이름들로 STCIS 정류장 검색 API 를 호출하고,
(시군구, 읍면동) 매칭으로 sttnId 를 찾아 매핑 CSV 를 저장한다.

산출물: data/raw/stcis/stop_mapping.csv

성격:
  - 같은 (정류장명, 동) 에 STCIS sttnId 가 여러 개 매칭될 수 있음 (상행/하행 등)
  - 매칭 안 되는 stop 은 unmatched 마크로 남김 (분석 단계에서 결손 처리)
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from tqdm import tqdm  # noqa: E402

from src.api.stcis_scraper import STCISClient, STCISStop  # noqa: E402

IN_CSV = PROJECT_ROOT / "data" / "raw" / "stcis" / "stops_by_name_all.csv"
OUT_CSV = PROJECT_ROOT / "data" / "raw" / "stcis" / "stop_mapping.csv"
CHECKPOINT = PROJECT_ROOT / "data" / "raw" / "stcis" / "stop_mapping_checkpoint.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


def normalize(s: str) -> str:
    """시군구·동명 비교용 공백 제거 정규화."""
    return "".join((s or "").split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="유니크 이름 N개만 처리 (테스트용)")
    parser.add_argument("--out", type=str, default=None, help="출력 CSV 경로")
    parser.add_argument("--sleep-min", type=float, default=5.0,
                        help="요청 간 최소 슬립 (초)")
    parser.add_argument("--sleep-max", type=float, default=10.0,
                        help="요청 간 최대 슬립 (초)")
    args = parser.parse_args()

    out_csv = Path(args.out) if args.out else OUT_CSV

    print(f"[1] 입력 로드: {IN_CSV.relative_to(PROJECT_ROOT)}")
    df = pd.read_csv(IN_CSV)
    print(f"    {len(df)} 정류장 (TAGO)")

    # 정류장명 단위로 검색 (이름 1개당 1회 API 호출)
    unique_names = sorted(df["정류장명"].dropna().unique())
    print(f"\n[2] 유니크 정류장명: {len(unique_names)}")
    if args.limit:
        unique_names = unique_names[: args.limit]
        df = df[df["정류장명"].isin(unique_names)]
        print(f"    --limit {args.limit} → {len(unique_names)} 이름만 처리, "
              f"TAGO 정류장 {len(df)}건으로 축소")

    client = STCISClient(sleep_range=(args.sleep_min, args.sleep_max))
    client.refresh_session()

    # 체크포인트 로드 (재시작 지원)
    name_to_results: dict[str, list[STCISStop]] = {}
    if CHECKPOINT.exists():
        with open(CHECKPOINT, encoding="utf-8") as f:
            cached = json.load(f)
        for n, lst in cached.items():
            name_to_results[n] = [STCISStop(**d) for d in lst]
        print(f"\n[체크포인트] {len(name_to_results)}개 이름 이미 검색됨 — 스킵")

    to_search = [n for n in unique_names if n not in name_to_results]
    print(f"\n[3] STCIS 검색 시작 (sleep {args.sleep_min}~{args.sleep_max}초) — "
          f"잔여 {len(to_search)} 이름  예상 ~{int(len(to_search) * (args.sleep_min + args.sleep_max) / 2 / 60)}분")

    SAVE_EVERY = 25
    for i, name in enumerate(tqdm(to_search, desc="search"), start=1):
        try:
            results = client.search_stops(name)
        except Exception as e:
            logger.error("search 실패: name=%s err=%s", name, e)
            results = []
        name_to_results[name] = results

        # 주기 저장
        if i % SAVE_EVERY == 0 or i == len(to_search):
            with open(CHECKPOINT, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        n: [r.__dict__ for r in rs]
                        for n, rs in name_to_results.items()
                    },
                    f, ensure_ascii=False,
                )

    print(f"\n[4] 매칭 — 2-tier 방식 (동까지 일치 → 시군구만 일치 → 미매칭)")
    rows = []
    tier_counts = {"동일치": 0, "시군구만일치": 0, "미매칭": 0}
    for _, r in df.iterrows():
        name = r["정류장명"]
        sigungu_t = normalize(r["시군구"]) if pd.notna(r["시군구"]) else ""
        dong_t = normalize(r["읍면동"]) if pd.notna(r["읍면동"]) else ""

        candidates = name_to_results.get(name, [])
        # Tier 1: 시군구 + 동 동시 일치
        tier1 = [
            c for c in candidates
            if normalize(c.sigungu_text) == sigungu_t and normalize(c.dong_text) == dong_t
        ]
        # Tier 2: 시군구만 일치 (TAGO·STCIS 행정동 분류 차이 대응)
        tier2 = [c for c in candidates if normalize(c.sigungu_text) == sigungu_t]

        if tier1:
            matches, tier = tier1, "동일치"
        elif tier2:
            matches, tier = tier2, "시군구만일치"
        else:
            matches, tier = [], "미매칭"

        tier_counts[tier] += 1

        if not matches:
            rows.append({
                "tago_stop_id": r["stop_id"],
                "stop_name": name,
                "sigungu": r["시군구"],
                "dong_tago": r["읍면동"],
                "dong_stcis": None,
                "n_matches": 0,
                "stcis_sttn_id": None,
                "excclc_area_cd": None,
                "sttn_sgg_cd": None,
                "tcbo_id_sttn": None,
                "match_tier": tier,
            })
        else:
            for m in matches:
                rows.append({
                    "tago_stop_id": r["stop_id"],
                    "stop_name": name,
                    "sigungu": r["시군구"],
                    "dong_tago": r["읍면동"],
                    "dong_stcis": m.dong_text,
                    "n_matches": len(matches),
                    "stcis_sttn_id": m.sttn_id,
                    "excclc_area_cd": m.excclc_area_cd,
                    "sttn_sgg_cd": m.sttn_sgg_cd,
                    "tcbo_id_sttn": m.tcbo_id_sttn,
                    "match_tier": tier,
                })

    out = pd.DataFrame(rows)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n[5] 저장 → {out_csv.relative_to(PROJECT_ROOT)}  ({len(out)}행)")

    print(f"\n[요약] TAGO 정류장 {len(df)} 매칭 결과:")
    for tier, n in tier_counts.items():
        print(f"       {tier:<10s} {n:>4d}")
    print(f"       매핑 행 수 (1 TAGO → N STCIS): {len(out)}")
    print("       n_matches 분포:")
    print(out["n_matches"].value_counts().sort_index().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
