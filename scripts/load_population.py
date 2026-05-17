"""행안부 인구통계 CSV (mois_*.csv) → districts 테이블 적재.

입력: data/raw/population/mois_<YYYYMM>.csv
  - 통/반 단위 → (sggNm, dongNm) 으로 SUM 합산
  - 컬럼: totNmprCnt, hhCnt, maleNmprCnt, femlNmprCnt, maleNAgeNmprCnt, femlNAgeNmprCnt

연령 그룹화:
  - pop_age_0_19   = male0Age + male10Age + feml0Age + feml10Age
  - pop_age_20_64  = (20·30·40·50·60세 남여)
  - pop_age_65_plus = (70·80·90·100세 남여) + (60AgeNmprCnt 의 65-69 부분은 추정 불가, 60 전체로 가정)

매칭: (sigungu, district_name) 정규화 (공백 제거) 후 UPDATE.
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402

DEFAULT_CSV = PROJECT_ROOT / "data" / "raw" / "population" / "mois_202604.csv"


def normalize(s: str) -> str:
    return "".join(str(s or "").split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, default=str(DEFAULT_CSV))
    args = parser.parse_args()

    csv_path = Path(args.input)
    print(f"[1] CSV 로드: {csv_path.name}")
    df = pd.read_csv(csv_path)
    print(f"    원본 {len(df):,}행 (통/반 단위)")

    # 연령 컬럼 매핑
    age_groups = {
        "pop_age_0_19": [
            "male0AgeNmprCnt", "male10AgeNmprCnt",
            "feml0AgeNmprCnt", "feml10AgeNmprCnt",
        ],
        "pop_age_20_64": [
            "male20AgeNmprCnt", "male30AgeNmprCnt", "male40AgeNmprCnt",
            "male50AgeNmprCnt", "male60AgeNmprCnt",
            "feml20AgeNmprCnt", "feml30AgeNmprCnt", "feml40AgeNmprCnt",
            "feml50AgeNmprCnt", "feml60AgeNmprCnt",
        ],
        "pop_age_65_plus": [
            "male70AgeNmprCnt", "male80AgeNmprCnt",
            "male90AgeNmprCnt", "male100AgeNmprCnt",
            "feml70AgeNmprCnt", "feml80AgeNmprCnt",
            "feml90AgeNmprCnt", "feml100AgeNmprCnt",
        ],
    }
    # 누락 컬럼은 0 채움
    all_age_cols = [c for cols in age_groups.values() for c in cols]
    for c in all_age_cols:
        if c not in df.columns:
            df[c] = 0

    print("[2] 통/반 → 행정동 합산")
    sum_cols = ["totNmprCnt", "hhCnt", "maleNmprCnt", "femlNmprCnt"] + all_age_cols
    sum_cols = [c for c in sum_cols if c in df.columns]
    grouped = (
        df.groupby(["sggNm", "dongNm"], as_index=False)[sum_cols]
        .sum()
    )
    print(f"    행정동 {len(grouped)}개")

    # 연령 합산
    for grp_col, cols in age_groups.items():
        grouped[grp_col] = grouped[cols].sum(axis=1).astype(int)

    # pop_date: statsYm 202604 → 2026-04-01 (기준일)
    yymm = str(df["statsYm"].iloc[0])
    pop_date = f"{yymm[:4]}-{yymm[4:6]}-01"
    print(f"    기준일: {pop_date}")

    print("[3] districts 매칭 (정규화)")
    engine = get_engine()
    with engine.connect() as conn:
        districts = pd.read_sql(
            text("SELECT district_id, sigungu, district_name FROM districts"),
            conn,
        )
    districts["__sg"] = districts["sigungu"].apply(normalize)
    districts["__dn"] = districts["district_name"].apply(normalize)

    grouped["__sg"] = grouped["sggNm"].apply(normalize)
    grouped["__dn"] = grouped["dongNm"].apply(normalize)

    merged = grouped.merge(
        districts[["district_id", "__sg", "__dn"]],
        on=["__sg", "__dn"], how="left",
    )
    matched = merged["district_id"].notna().sum()
    print(f"    매칭: {matched}/{len(grouped)}")
    if matched < len(grouped):
        unmatch = merged[merged["district_id"].isna()][["sggNm", "dongNm"]]
        print(f"    [!] 미매칭: {unmatch.values.tolist()}")

    print("[4] UPDATE districts")
    updated = 0
    with engine.begin() as conn:
        for _, r in merged.iterrows():
            if pd.isna(r["district_id"]):
                continue
            conn.execute(
                text(
                    """
                    UPDATE districts SET
                        population       = :pop,
                        households       = :hh,
                        pop_male         = :m,
                        pop_female       = :f,
                        pop_age_0_19     = :a019,
                        pop_age_20_64    = :a2064,
                        pop_age_65_plus  = :a65,
                        pop_date         = :date
                    WHERE district_id = :id
                    """
                ),
                {
                    "id": int(r["district_id"]),
                    "pop": int(r["totNmprCnt"]),
                    "hh": int(r.get("hhCnt", 0) or 0),
                    "m": int(r.get("maleNmprCnt", 0) or 0),
                    "f": int(r.get("femlNmprCnt", 0) or 0),
                    "a019": int(r["pop_age_0_19"]),
                    "a2064": int(r["pop_age_20_64"]),
                    "a65": int(r["pop_age_65_plus"]),
                    "date": pop_date,
                },
            )
            updated += 1
    print(f"    UPDATE {updated}건")

    print("\n[5] 검증")
    with engine.connect() as conn:
        s = conn.execute(text(
            "SELECT SUM(population), SUM(pop_male), SUM(pop_female), "
            "SUM(pop_age_0_19), SUM(pop_age_20_64), SUM(pop_age_65_plus), "
            "COUNT(*) FILTER (WHERE population IS NOT NULL) "
            "FROM districts"
        )).one()
    print(f"    총인구: {s[0]:,}  남자: {s[1]:,}  여자: {s[2]:,}")
    print(f"    0-19: {s[3]:,}  20-64: {s[4]:,}  65+: {s[5]:,}")
    print(f"    인구 채워진 동: {s[6]}/55")
    return 0


if __name__ == "__main__":
    sys.exit(main())
