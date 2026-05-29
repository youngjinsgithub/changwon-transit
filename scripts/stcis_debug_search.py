"""STCIS 검색 결과 디버그 — 왜 매칭 실패인지 확인."""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.stcis_scraper import STCISClient  # noqa: E402

NAMES = ["KT마산점", "3.15아트센터", "SM비즈타운"]


def main() -> int:
    client = STCISClient(sleep_range=(1.0, 1.5))
    client.refresh_session()
    for n in NAMES:
        print(f"\n=== '{n}' ===")
        rs = client.search_stops(n)
        print(f"  결과 {len(rs)}건")
        for r in rs:
            print(
                f"    sttnId={r.sttn_id}  sigungu='{r.sigungu_text}'  dong='{r.dong_text}'  "
                f"name='{r.stop_name}'"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
