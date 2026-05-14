"""DB 초기화 자동화 스크립트.

수행 순서:
  1. docker compose up -d    (PostGIS + pgAdmin 컨테이너 기동)
  2. PostGIS 가 ready 될 때까지 polling (최대 60초)
  3. sql/01_schema.sql 멱등 적용 (스키마 갱신용 — 최초 기동 시에는 docker
     entrypoint 가 이미 적용했지만 여기서 한 번 더 실행하면 변경사항 반영)
  4. PostGIS 버전 출력

사용: `python scripts/init_db.py`
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

# src 패키지를 import 할 수 있도록 PROJECT_ROOT 를 sys.path 에 추가
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402


SCHEMA_FILE = PROJECT_ROOT / "sql" / "01_schema.sql"
READINESS_TIMEOUT_S = 60
READINESS_INTERVAL_S = 2


def run_compose_up() -> None:
    """docker compose up -d 실행."""
    print("[1/4] docker compose up -d ...")
    # 'docker compose' (v2) 우선, 실패 시 'docker-compose' (v1) 폴백
    candidates = [
        ["docker", "compose", "up", "-d"],
        ["docker-compose", "up", "-d"],
    ]
    last_err: Exception | None = None
    for cmd in candidates:
        try:
            subprocess.run(
                cmd,
                cwd=PROJECT_ROOT,
                check=True,
            )
            print(f"      → 성공: {' '.join(cmd)}")
            return
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            last_err = e
    raise RuntimeError(
        f"docker compose 실행 실패. Docker Desktop 이 실행 중인지 확인하세요. ({last_err})"
    )


def wait_for_postgis() -> None:
    """PostGIS 가 연결 가능해질 때까지 polling."""
    print(f"[2/4] PostGIS readiness 대기 (최대 {READINESS_TIMEOUT_S}s) ...")
    engine = get_engine()
    deadline = time.monotonic() + READINESS_TIMEOUT_S
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            print(f"      → ready (시도 {attempt}회)")
            return
        except Exception as e:  # noqa: BLE001 - 모든 연결 오류 흡수
            if attempt == 1:
                print(f"      대기 중... ({type(e).__name__})")
            time.sleep(READINESS_INTERVAL_S)
    raise TimeoutError(f"PostGIS 가 {READINESS_TIMEOUT_S}초 내에 ready 되지 않았습니다.")


def apply_schema() -> None:
    """sql/01_schema.sql 멱등 적용."""
    print(f"[3/4] schema 적용: {SCHEMA_FILE.relative_to(PROJECT_ROOT)}")
    if not SCHEMA_FILE.exists():
        raise FileNotFoundError(f"스키마 파일을 찾을 수 없습니다: {SCHEMA_FILE}")

    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(sql))
    print("      → 적용 완료")


def show_versions() -> None:
    """PostgreSQL / PostGIS 버전 출력."""
    print("[4/4] 버전 확인")
    engine = get_engine()
    with engine.connect() as conn:
        pg_ver = conn.execute(text("SHOW server_version")).scalar()
        postgis_ver = conn.execute(text("SELECT PostGIS_Version()")).scalar()
    print(f"      PostgreSQL : {pg_ver}")
    print(f"      PostGIS    : {postgis_ver}")


def main() -> int:
    try:
        run_compose_up()
        wait_for_postgis()
        apply_schema()
        show_versions()
    except Exception as e:  # noqa: BLE001
        print(f"\n[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print("\nDB 초기화 완료. 다음: python scripts/test_connection.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
