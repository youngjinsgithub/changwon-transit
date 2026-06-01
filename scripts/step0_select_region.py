"""Step 0 — 분석 대상 도시 선정.

KOSIS '1인당 자동차 등록대수(시도/시/군/구)' 통계표 (DT_1YL20731, 2025년)
데이터로 경남·부산·울산 권역 주요 시(市) 의 1인당 차량 보유 대수를
비교하여 분석 대상으로 **창원시**를 선정한 근거를 제시한다.

출처: 국토교통부 자동차등록현황보고 + 행안부 주민등록인구현황 (KOSIS 가공)
산식: 1인당 자동차 등록대수 = 자동차등록대수 ÷ 주민등록인구

출력:
  - 콘솔: 후보 도시 랭킹 표
  - data/processed/step0_region_ranking.csv  (전체 결과)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SRC_XLSX = PROJECT_ROOT / "data" / "raw" / "vehicle" / "kosis_per_capita_2025.xlsx"
OUT_CSV = PROJECT_ROOT / "data" / "processed" / "step0_region_ranking.csv"

# 비교 대상: 경남 주요 시 + 부울 광역시
# (군 지역은 산업·관용차 영향이 크고 인구 규모가 작아 시 비교에서 제외)
CANDIDATES = [
    "창원시", "김해시", "양산시", "진주시",
    "거제시", "통영시", "사천시", "밀양시",
    "부산광역시", "울산광역시",
]


def load_kosis(path: Path) -> pd.DataFrame:
    """KOSIS xlsx 의 '데이터' 시트를 읽어 컬럼 정리한 DataFrame 반환."""
    df = pd.read_excel(path, sheet_name="데이터", header=0)
    df.columns = ["region", "per_capita", "vehicles", "population"]
    # 첫 행은 컬럼 설명 (스킵)
    df = df.iloc[1:].copy()
    # 시군구 행은 앞에 전각 공백이 들어 있음 → strip
    df["region"] = df["region"].astype(str).str.strip()
    for col in ["per_capita", "vehicles", "population"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["vehicles", "population"]).reset_index(drop=True)


def main() -> int:
    print(f"[1] KOSIS 데이터 로드: {SRC_XLSX.relative_to(PROJECT_ROOT)}")
    df = load_kosis(SRC_XLSX)
    print(f"    전체 {len(df):,} 행 (전국 + 시도 + 시군구)")

    print(f"\n[2] 비교 대상 {len(CANDIDATES)}개 도시 추출")
    sub = df[df["region"].isin(CANDIDATES)].copy()
    # 자체 계산 1인당 (KOSIS 반올림 → 소수 첫째 자리만 보임 → 더 정밀하게)
    sub["per_capita_calc"] = sub["vehicles"] / sub["population"]
    sub = sub.sort_values("per_capita_calc", ascending=False).reset_index(drop=True)
    sub["rank"] = sub.index + 1

    print(f"    매칭 {len(sub)}개:")
    missing = set(CANDIDATES) - set(sub["region"])
    if missing:
        print(f"    [경고] 매칭 안 됨: {missing}")

    print("\n[3] 1인당 차량 등록대수 랭킹 (높을수록 차량 의존도 높음)")
    print()
    header = f"{'순위':>4} {'도시':>10} {'자동차':>12} {'인구':>12} {'1인당':>8}"
    print(header)
    print("-" * len(header))
    for _, r in sub.iterrows():
        marker = " ★" if r["region"] == "창원시" else ""
        print(
            f"{int(r['rank']):>4} {r['region']:>10} "
            f"{int(r['vehicles']):>12,} {int(r['population']):>12,} "
            f"{r['per_capita_calc']:>8.3f}{marker}"
        )

    print(f"\n[4] CSV 저장: {OUT_CSV.relative_to(PROJECT_ROOT)}")
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    sub[["rank", "region", "vehicles", "population",
         "per_capita", "per_capita_calc"]].to_csv(
        OUT_CSV, index=False, encoding="utf-8-sig"
    )

    chang = sub[sub["region"] == "창원시"].iloc[0]
    print()
    print("=" * 60)
    print(f"  결론: 창원시 1인당 차량 {chang['per_capita_calc']:.3f}대 "
          f"→ 비교 시(市) 중 {int(chang['rank'])}위")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
