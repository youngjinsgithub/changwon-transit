"""STCIS (전국 대중교통 통계) CSV 로더.

STCIS 웹사이트에서 직접 다운로드한 정류장별 시간대별 승하차 CSV 를 읽는다.
와이드 포맷(예: '00승', '00하', '01승', ... '23하' 컬럼)을 **그대로** DataFrame
으로 반환한다. 와이드→롱 변환은 src/etl/stcis_loader.py 에서 처리.

CSV 사양 (관측된 일반적 STCIS 포맷):
    - 인코딩: EUC-KR/CP949 또는 UTF-8 (자동 감지)
    - 구분자: 콤마(,)
    - 헤더 행 존재
    - 정류장 식별/명칭 + 일자 + 24시간 × {승, 하} 컬럼

실제 다운로드 받은 파일의 컬럼은 STCIS 메뉴에 따라 다를 수 있어,
이 모듈은 **인코딩 처리 + 원본 그대로 로딩** 만 책임진다.
컬럼 표준화는 호출 측(노트북 또는 etl/stcis_loader)에서 진행.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.utils.encoding import detect_encoding
from src.utils.logger import get_logger

logger = get_logger(__name__)


def load_stcis_csv(
    path: str | Path,
    *,
    encoding: str | None = None,
    sep: str = ",",
    dtype: dict | None = None,
) -> pd.DataFrame:
    """STCIS CSV 1개 파일 로드.

    Args:
        path: CSV 경로
        encoding: 명시적 인코딩. None 이면 자동 감지.
        sep: 구분자 (기본 ',')
        dtype: pandas read_csv dtype 매핑 (옵션)

    Returns:
        와이드 포맷 그대로의 DataFrame
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"STCIS CSV 를 찾을 수 없습니다: {p}")

    enc = encoding or detect_encoding(p)
    logger.info("STCIS CSV 로딩: %s (encoding=%s)", p.name, enc)

    df = pd.read_csv(p, encoding=enc, sep=sep, dtype=dtype)
    logger.info("    rows=%d, cols=%d", len(df), df.shape[1])
    return df


def load_stcis_dir(
    dir_path: str | Path,
    *,
    pattern: str = "*.csv",
    encoding: str | None = None,
) -> pd.DataFrame:
    """디렉토리 내 STCIS CSV 들을 모두 읽어 단일 DataFrame 으로 concat.

    Args:
        dir_path: CSV 들이 있는 디렉토리
        pattern: 파일 매칭 글롭 (기본 '*.csv')
        encoding: 명시적 인코딩. None 이면 파일별 자동 감지.

    Returns:
        모든 파일을 세로로 합친 DataFrame. 빈 디렉토리면 빈 DataFrame.
    """
    d = Path(dir_path)
    if not d.is_dir():
        raise NotADirectoryError(f"디렉토리가 아닙니다: {d}")

    files = sorted(d.glob(pattern))
    if not files:
        logger.warning("매칭 파일 없음: %s/%s", d, pattern)
        return pd.DataFrame()

    logger.info("STCIS 디렉토리 로딩: %s (%d개 파일)", d, len(files))
    frames = [load_stcis_csv(f, encoding=encoding) for f in files]
    out = pd.concat(frames, ignore_index=True)
    logger.info("    합계 rows=%d", len(out))
    return out
