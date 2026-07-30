"""Plan A 3단계 청킹 규칙 (2026-07-22, Codex 결정).

빈 줄 기준으로 문단을 나누고, 순서대로 묶어 청크당 최대 1,200자로 만든다.
초기 overlap 없음. 문단 하나가 1,200자를 넘으면 그 문단 자체를 하나의 청크로 둔다 —
공고별 섹션 파서는 만들지 않고, 기준선 품질이 부족할 때만 overlap·섹션 분할을 비교한다.
"""
from dataclasses import dataclass

MAX_CHUNK_CHARS = 1200


@dataclass
class Chunk:
    text: str
    chunk_index: int
    start_line: int  # 1-based, inclusive
    end_line: int    # 1-based, inclusive


def _split_paragraphs(text: str) -> list[tuple[str, int, int]]:
    """빈 줄로 구분된 문단을 (텍스트, 시작줄, 끝줄) 리스트로 반환한다."""
    lines = text.split("\n")
    paragraphs: list[tuple[str, int, int]] = []
    buf: list[str] = []
    start: int | None = None
    for i, line in enumerate(lines, start=1):
        if line.strip() == "":
            if buf:
                paragraphs.append(("\n".join(buf), start, i - 1))
                buf = []
                start = None
            continue
        if start is None:
            start = i
        buf.append(line)
    if buf:
        paragraphs.append(("\n".join(buf), start, len(lines)))
    return paragraphs


def chunk_text(text: str) -> list[Chunk]:
    """문단을 순서대로 묶어 청크당 최대 MAX_CHUNK_CHARS자로 만든다."""
    paragraphs = _split_paragraphs(text)
    chunks: list[Chunk] = []
    buf_text = ""
    buf_start: int | None = None
    buf_end: int | None = None

    def flush() -> None:
        nonlocal buf_text, buf_start, buf_end
        if buf_text:
            chunks.append(Chunk(text=buf_text, chunk_index=len(chunks), start_line=buf_start, end_line=buf_end))
        buf_text, buf_start, buf_end = "", None, None

    for para_text, p_start, p_end in paragraphs:
        candidate = f"{buf_text}\n\n{para_text}" if buf_text else para_text
        if len(candidate) <= MAX_CHUNK_CHARS or not buf_text:
            buf_text = candidate
            buf_start = buf_start if buf_start is not None else p_start
            buf_end = p_end
        else:
            flush()
            buf_text = para_text
            buf_start = p_start
            buf_end = p_end
    flush()
    return chunks
