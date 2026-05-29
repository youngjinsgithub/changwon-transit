"""파일 인코딩 자동 감지 헬퍼.

공공데이터 CSV 는 EUC-KR / UTF-8 / UTF-8-BOM / CP949 가 혼재.
chardet 으로 감지하되, 한국어 인코딩에 대한 휴리스틱을 보강.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import chardet

from .logger import get_logger

logger = get_logger(__name__)

# chardet 신뢰도가 낮을 때 한국어 데이터 기본값
_DEFAULT_KO_ENCODING: Final[str] = "cp949"

# chardet 가 ISO-8859 / Windows-1252 류로 잘못 감지하는 경우 보정
_LATIN_LIKE: Final[set[str]] = {
    "iso-8859-1",
    "iso-8859-2",
    "windows-1252",
    "ascii",
}


def detect_encoding(path: str | Path, sample_size: int = 65536) -> str:
    """파일의 인코딩을 감지하여 반환.

    Args:
        path: 대상 파일 경로
        sample_size: 감지에 사용할 바이트 수 (기본 64KB)

    Returns:
        인코딩 문자열 (예: 'utf-8', 'cp949', 'euc-kr', 'utf-8-sig')
    """
    p = Path(path)
    with p.open("rb") as f:
        sample = f.read(sample_size)

    # UTF-8 BOM 우선 처리
    if sample.startswith(b"\xef\xbb\xbf"):
        logger.debug("%s : UTF-8 BOM 감지", p.name)
        return "utf-8-sig"

    result = chardet.detect(sample)
    enc = (result.get("encoding") or "").lower()
    conf = result.get("confidence") or 0.0
    logger.debug("%s : chardet=%s (%.2f)", p.name, enc, conf)

    # 신뢰도 낮거나 라틴어 계열로 잘못 감지된 경우 → 한국어 기본 인코딩
    if not enc or conf < 0.6 or enc in _LATIN_LIKE:
        logger.debug("%s : 신뢰도 부족, %s 로 폴백", p.name, _DEFAULT_KO_ENCODING)
        return _DEFAULT_KO_ENCODING

    # euc-kr 과 cp949 는 호환되지만 cp949 가 상위호환 → 통일
    if enc == "euc-kr":
        return "cp949"

    return enc
