"""
PDF 텍스트 추출 모듈.

pdfplumber를 사용해 텍스트와 표를 함께 추출한다.
표는 셀을 " | "로 이어붙인 텍스트로 변환해 LLM이 읽을 수 있게 한다.

PyPDF2, pdfminer 대신 pdfplumber를 선택한 이유:
표 레이아웃 처리가 강하고 이력서처럼 복잡한 레이아웃에서도 정확도가 높음.
"""
import logging
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)

# LLM 토큰 한도 고려 — 약 10,000 토큰 분량
_MAX_CHARS = 40_000


class PDFExtractError(Exception):
    """PDF 파일을 열거나 읽는 데 실패한 경우."""


def extract_text(path: Path) -> str:
    """단일 PDF에서 텍스트를 추출한다. 표가 있으면 [표] 섹션으로 추가."""
    parts: list[str] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""

                # 표가 있으면 텍스트와 별도로 추출해 병합
                tables = page.extract_tables()
                table_text = ""
                for table in tables:
                    for row in table:
                        cells = [c or "" for c in row]
                        table_text += " | ".join(cells) + "\n"

                combined = page_text
                if table_text.strip():
                    combined += f"\n[표]\n{table_text}"
                if combined.strip():
                    parts.append(f"[페이지 {i}]\n{combined.strip()}")
    except PDFExtractError:
        raise
    except Exception as e:
        logger.warning("PDF 텍스트 추출 실패 (%s): %s", path.name, e)
        raise PDFExtractError(
            f"'{path.name}' 파일을 읽을 수 없습니다. 암호화됐거나 손상된 PDF일 수 있습니다."
        ) from e

    full_text = "\n\n".join(parts)
    return full_text[:_MAX_CHARS]


def extract_texts(paths: list[Path]) -> str:
    """여러 PDF(이력서 + 포트폴리오 등)를 하나의 텍스트로 합친다.
    파일명을 구분자로 넣어 LLM이 출처를 파악할 수 있게 한다."""
    segments: list[str] = []
    for path in paths:
        text = extract_text(path)
        if text.strip():
            segments.append(f"=== {path.name} ===\n{text}")
    return "\n\n".join(segments)
