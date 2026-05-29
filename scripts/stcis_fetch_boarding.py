"""STCIS 시간대별 승하차 데이터 일괄 수집.

전제: stop_mapping.csv 가 이미 만들어진 상태 (stcis_build_mapping.py 실행 후).

수행:
  1. stop_mapping.csv 로드 → 매핑된 STCIS sttnId 목록
  2. 각 (sttnId, 기간) 조합으로 indicatorAjax.do 호출 → HTML 응답 저장
  3. HTML 파싱 → long-format DataFrame (sttnId, date, hour, 승차, 하차)
  4. 모든 결과를 통합 CSV 저장: data/processed/stcis_boarding_long.csv

기본 기간 (사용자 지정):
  - 2026-04-01 ~ 2026-04-14 (1차)
  - 2026-04-15 ~ 2026-04-28 (2차)

체크포인트:
  - HTML 원본이 이미 존재하면 재호출 안 함 (멱등)
  - 모든 응답 파싱 후 하나의 CSV 로 합침
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from tqdm import tqdm  # noqa: E402

from src.api.stcis_scraper import STCISClient, STCISStop  # noqa: E402

MAPPING_CSV = PROJECT_ROOT / "data" / "raw" / "stcis" / "stop_mapping.csv"
HTML_DIR = PROJECT_ROOT / "data" / "raw" / "stcis" / "responses"
OUT_CSV = PROJECT_ROOT / "data" / "processed" / "stcis_boarding_long.csv"

# 기본: 2026-04 두 회차
DEFAULT_PERIODS: list[tuple[str, str]] = [
    ("2026-04-01", "2026-04-14"),
    ("2026-04-15", "2026-04-28"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# 24시간 컬럼 순서 (운영 기준 04→03시)
HOURS_ORDER = [f"{h:02d}" for h in [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
                                     16, 17, 18, 19, 20, 21, 22, 23, 0, 1, 2, 3]]


def parse_indicator_html(html: str, sttn_id: str) -> pd.DataFrame:
    """indicatorAjax.do 응답 HTML → long-format DataFrame.

    응답 구조:
      Table 3: 정류장명·번호·일자 (N rows)
      Table 4: N rows × 48 cols (24시간 × 승/하)

    Returns:
        columns = ['stcis_sttn_id', 'stop_name', 'date', 'hour', 'boarding', 'alighting']
    """
    tables = pd.read_html(io.StringIO(html))
    if len(tables) < 5:
        return pd.DataFrame()

    meta = tables[3]   # (N, 3): 정류장명, 정류장번호, 일자
    data = tables[4]   # (N, 48): 시간×승하차

    if len(meta) != len(data):
        logger.warning(
            "sttnId=%s: meta rows=%d ≠ data rows=%d", sttn_id, len(meta), len(data)
        )
        return pd.DataFrame()

    rows = []
    for i in range(len(meta)):
        stop_name = str(meta.iloc[i, 0])
        date_raw = str(meta.iloc[i, 2])  # 예: '2026-04-01(수)'
        date = date_raw.split("(")[0].strip()

        data_row = data.iloc[i].tolist()
        if len(data_row) < 48:
            continue

        for h_idx, hour in enumerate(HOURS_ORDER):
            b = data_row[h_idx * 2]
            a = data_row[h_idx * 2 + 1]
            try:
                b = int(b)
                a = int(a)
            except Exception:
                b = a = 0
            rows.append({
                "stcis_sttn_id": sttn_id,
                "stop_name": stop_name,
                "date": date,
                "hour": int(hour),
                "boarding": b,
                "alighting": a,
            })

    return pd.DataFrame(rows)


def fetch_one(
    client: STCISClient,
    stop: STCISStop,
    date_from: str,
    date_to: str,
    html_dir: Path,
) -> Path:
    """단일 정류장 × 단일 기간 HTML 다운로드 (캐시 멱등)."""
    fn = f"sttn{stop.sttn_id}_{date_from.replace('-','')}_{date_to.replace('-','')}.html"
    fp = html_dir / fn
    if fp.exists():
        return fp
    html = client.fetch_indicator(stop, date_from, date_to)
    fp.write_text(html, encoding="utf-8")
    return fp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="고유 STCIS sttnId N개만 처리 (테스트용)")
    parser.add_argument(
        "--periods", nargs="*",
        help="기간 list. 예) 2026-04-01:2026-04-14 2026-04-15:2026-04-28"
    )
    args = parser.parse_args()

    if args.periods:
        periods = [tuple(p.split(":")) for p in args.periods]
    else:
        periods = DEFAULT_PERIODS
    print(f"[기간] {periods}")

    print(f"[1] 매핑 로드: {MAPPING_CSV.relative_to(PROJECT_ROOT)}")
    mp = pd.read_csv(MAPPING_CSV)
    mp = mp[mp["stcis_sttn_id"].notna()].copy()
    print(f"    매핑 행 {len(mp)} (매칭된 것만)")

    # STCIS sttnId 단위 dedup — 같은 sttnId 가 여러 TAGO stop_id 에 매핑돼있을 수 있음
    uniq = mp.drop_duplicates("stcis_sttn_id").reset_index(drop=True)
    print(f"    고유 STCIS sttnId: {len(uniq)}")

    if args.limit:
        uniq = uniq.head(args.limit)
        print(f"    --limit {args.limit} 적용 → {len(uniq)}")

    HTML_DIR.mkdir(parents=True, exist_ok=True)

    # 시작은 3-7초 (빠르게). 503 발생 시 자동으로 5-10초로 폴백.
    client = STCISClient(sleep_range=(3.0, 7.0))
    client.refresh_session()

    print(f"\n[2] 다운로드 시작 — {len(uniq) * len(periods)}회 호출 예상")
    fetched_files: list[tuple[Path, str]] = []
    for _, r in tqdm(uniq.iterrows(), total=len(uniq), desc="stops"):
        # tcbo_id_sttn: CSV 에 3.0 으로 저장 → "03" 으로 복원 (leading zero 보존)
        tcbo_int = int(r["tcbo_id_sttn"])
        tcbo_str = f"{tcbo_int:02d}"
        sttn = STCISStop(
            tcbo_id_sttn=tcbo_str,
            excclc_area_cd=str(r["excclc_area_cd"]),
            sttn_id=str(int(r["stcis_sttn_id"])),
            sttn_sgg_cd=str(int(r["sttn_sgg_cd"])),
            stop_name=str(r["stop_name"]),
            ars_no="",
        )
        for d_from, d_to in periods:
            try:
                fp = fetch_one(client, sttn, d_from, d_to, HTML_DIR)
                fetched_files.append((fp, sttn.sttn_id))
            except Exception as e:
                logger.error("fetch 실패 sttn=%s %s~%s err=%s",
                             sttn.sttn_id, d_from, d_to, e)

    print(f"\n[3] HTML {len(fetched_files)}개 수집 — 파싱 시작")
    all_rows: list[pd.DataFrame] = []
    for fp, sid in tqdm(fetched_files, desc="parse"):
        html = fp.read_text(encoding="utf-8")
        df = parse_indicator_html(html, sid)
        if not df.empty:
            all_rows.append(df)

    if not all_rows:
        print("[!] 파싱 결과 없음")
        return 1

    boarding = pd.concat(all_rows, ignore_index=True)
    # 타입 정렬 (merge 시 object↔float 충돌 방지)
    boarding["stcis_sttn_id"] = boarding["stcis_sttn_id"].astype(int)
    mp_join = mp[["stcis_sttn_id", "tago_stop_id", "sigungu", "dong_tago"]].copy()
    mp_join = mp_join.dropna(subset=["stcis_sttn_id"])
    mp_join["stcis_sttn_id"] = mp_join["stcis_sttn_id"].astype(int)
    mp_join = mp_join.drop_duplicates("stcis_sttn_id")
    boarding = boarding.merge(mp_join, on="stcis_sttn_id", how="left")

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    boarding.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n[4] 저장 → {OUT_CSV.relative_to(PROJECT_ROOT)}  ({len(boarding):,}행)")
    print(f"    sttn 수: {boarding['stcis_sttn_id'].nunique()}")
    print(f"    날짜 범위: {boarding['date'].min()} ~ {boarding['date'].max()}")
    print(f"    총 승차: {boarding['boarding'].sum():,}")
    print(f"    총 하차: {boarding['alighting'].sum():,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
