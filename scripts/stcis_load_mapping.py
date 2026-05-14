"""stop_mapping.csv → stcis_stop_mapping 테이블 적재 (한 번만).

입력:  data/raw/stcis/stop_mapping.csv (2,259 행, build_mapping 산출물)
출력:  stcis_stop_mapping 테이블 (TAGO ↔ STCIS 매핑)

멱등: ON CONFLICT (tago_stop_id, stcis_sttn_id) DO UPDATE — 재실행 안전.
미매칭(stcis_sttn_id 가 비어있는) 행은 스킵.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from src.db.connection import get_engine  # noqa: E402
from src.db.upsert import upsert_rows  # noqa: E402

MAPPING_CSV = PROJECT_ROOT / "data" / "raw" / "stcis" / "stop_mapping.csv"


def main() -> int:
    print(f"[1] 로드: {MAPPING_CSV.relative_to(PROJECT_ROOT)}")
    df = pd.read_csv(MAPPING_CSV)
    print(f"    {len(df)} 행")

    valid = df[df["stcis_sttn_id"].notna()].copy()
    print(f"    매핑된 행 (stcis_sttn_id 있음): {len(valid)}")
    valid["stcis_sttn_id"] = valid["stcis_sttn_id"].astype(int)

    # (tago_stop_id, stcis_sttn_id) PK 중복 dedup — 같은 쌍이 여러 번 등장 가능
    before = len(valid)
    valid = valid.drop_duplicates(subset=["tago_stop_id", "stcis_sttn_id"])
    if len(valid) < before:
        print(f"    PK 중복 dedup: {before} → {len(valid)} ({before - len(valid)} 제거)")

    rows = [
        (r["tago_stop_id"], int(r["stcis_sttn_id"]), r["match_tier"])
        for _, r in valid.iterrows()
    ]

    print(f"\n[2] stcis_stop_mapping UPSERT")
    engine = get_engine()
    n = upsert_rows(
        engine,
        table="stcis_stop_mapping",
        columns=["tago_stop_id", "stcis_sttn_id", "match_tier"],
        rows=rows,
        conflict_cols=["tago_stop_id", "stcis_sttn_id"],
        update_cols=["match_tier"],
    )
    print(f"    {n} 행 처리")

    print(f"\n[3] 검증")
    from sqlalchemy import text
    with engine.connect() as c:
        total = c.execute(text("SELECT count(*) FROM stcis_stop_mapping")).scalar()
        by_tier = c.execute(text(
            "SELECT match_tier, count(*) FROM stcis_stop_mapping "
            "GROUP BY match_tier ORDER BY count(*) DESC"
        )).all()
        n_tago = c.execute(text(
            "SELECT count(DISTINCT tago_stop_id) FROM stcis_stop_mapping"
        )).scalar()
        n_sttn = c.execute(text(
            "SELECT count(DISTINCT stcis_sttn_id) FROM stcis_stop_mapping"
        )).scalar()
    print(f"    총 매핑 행 수: {total}")
    print(f"    고유 TAGO stop_id: {n_tago}")
    print(f"    고유 STCIS sttn_id: {n_sttn}")
    print(f"    tier 분포:")
    for tier, cnt in by_tier:
        print(f"      {tier:<12s} {cnt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
