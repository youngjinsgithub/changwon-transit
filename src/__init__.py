"""창원 대중교통 데이터 분석 - 핵심 패키지.

패키지 import 시점에 프로젝트 루트의 .env 를 1회 로드해
모든 하위 모듈이 환경변수를 사용할 수 있게 한다.
"""

from pathlib import Path

from dotenv import load_dotenv


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ENV_PATH = _PROJECT_ROOT / ".env"

if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)
