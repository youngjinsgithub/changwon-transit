"""STCIS probe_response.txt 의 HTML 테이블을 pandas 로 파싱.

목적: indicatorAjax.do 응답의 데이터 구조 파악
       - 몇 개의 테이블이 있는지
       - 컬럼·행 구조 (정류장×일자×시간대 ?)
       - 단일 정류장 응답인지, 여러 정류장 가능한지
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESP = PROJECT_ROOT / "data" / "raw" / "stcis" / "probe_response.txt"


def main() -> int:
    html = RESP.read_text(encoding="utf-8")
    print(f"[1] 응답 크기: {len(html):,}자")

    tables = pd.read_html(io.StringIO(html))
    print(f"[2] HTML 테이블 개수: {len(tables)}")
    print()

    for i, t in enumerate(tables):
        print(f"=== 테이블 {i}  shape={t.shape} ===")
        # 컬럼 이름 출력 (멀티헤더일 수 있음)
        if isinstance(t.columns, pd.MultiIndex):
            print("  컬럼 (MultiIndex):")
            for col in t.columns.tolist()[:20]:
                print(f"    {col}")
            if t.shape[1] > 20:
                print(f"    ... 총 {t.shape[1]}개 컬럼")
        else:
            print(f"  컬럼: {list(t.columns)[:20]}")
            if t.shape[1] > 20:
                print(f"    ... 총 {t.shape[1]}개 컬럼")

        print(f"\n  첫 5행:")
        print(t.head().to_string(max_cols=15))
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
