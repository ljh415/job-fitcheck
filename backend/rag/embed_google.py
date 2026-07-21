"""Plan A 3단계 — Google(`gemini-embedding-2`) 임베딩 기준선.

공고 원문(.raw.txt)만 청킹·임베딩한다. 개인 프로필은 Google 무료 티어로 보내지 않고
로컬 임베딩 전용으로 남긴다(`00_claude_handoff.md` "개인 데이터 사용 제한" 참고).

실행: backend/ 에서 `python3 -m rag.embed_google`
"""
import hashlib
import sqlite3
import struct
import time

from google import genai
from google.genai import errors, types

from config import settings
from rag.chunking import chunk_text
from rag.ingest import DB_PATH

PROVIDER = "google"
MODEL = "gemini-embedding-2"
DIMENSIONS = 1536
BATCH_SIZE = 10  # 20개는 429(RESOURCE_EXHAUSTED)가 실측됨 — 보수적으로 10개, 배치 사이 딜레이도 둠
BATCH_DELAY_SECONDS = 2
MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 20


def _embed_with_retry(client: genai.Client, texts: list[str]) -> list:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            res = client.models.embed_content(
                model=MODEL,
                contents=texts,
                config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT", output_dimensionality=DIMENSIONS),
            )
            return res.embeddings
        except errors.ClientError as e:
            if getattr(e, "code", None) == 429 and attempt < MAX_RETRIES:
                print(f"  429 재시도 {attempt}/{MAX_RETRIES} — {RETRY_WAIT_SECONDS}초 대기")
                time.sleep(RETRY_WAIT_SECONDS)
                continue
            raise


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _vector_to_blob(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def populate_posting_chunks(conn: sqlite3.Connection) -> int:
    """document_chunk에 공고 원문(source_type='posting_raw') 청크를 채운다.
    재실행 가능하도록 기존 posting_raw 청크(및 연결된 임베딩)를 지우고 다시 만든다."""
    conn.execute(
        "DELETE FROM chunk_embedding WHERE chunk_id IN"
        " (SELECT id FROM document_chunk WHERE source_type = 'posting_raw')"
    )
    conn.execute("DELETE FROM document_chunk WHERE source_type = 'posting_raw'")

    total = 0
    for slug, raw_path in conn.execute("SELECT slug, raw_path FROM posting").fetchall():
        raw_text = open(raw_path, encoding="utf-8").read()
        for chunk in chunk_text(raw_text):
            conn.execute(
                "INSERT INTO document_chunk"
                " (source_type, source_id, section, text, chunk_index, start_line, end_line, text_hash)"
                " VALUES ('posting_raw', ?, ?, ?, ?, ?, ?, ?)",
                (slug, None, chunk.text, chunk.chunk_index, chunk.start_line, chunk.end_line, _text_hash(chunk.text)),
            )
            total += 1
    conn.commit()
    return total


def embed_pending_chunks(conn: sqlite3.Connection, client: genai.Client) -> int:
    """chunk_embedding에 아직 (chunk_id, provider, model, dimensions) 조합이 없는 청크만 임베딩한다."""
    rows = conn.execute(
        "SELECT dc.id, dc.text, dc.text_hash FROM document_chunk dc"
        " WHERE dc.source_type = 'posting_raw'"
        " AND NOT EXISTS ("
        "   SELECT 1 FROM chunk_embedding ce"
        "   WHERE ce.chunk_id = dc.id AND ce.provider = ? AND ce.model = ? AND ce.dimensions = ?"
        " )",
        (PROVIDER, MODEL, DIMENSIONS),
    ).fetchall()

    embedded = 0
    n_batches = (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE
    for batch_num, i in enumerate(range(0, len(rows), BATCH_SIZE), start=1):
        batch = rows[i : i + BATCH_SIZE]
        embeddings = _embed_with_retry(client, [text for _, text, _ in batch])
        for (chunk_id, _, text_hash), embedding in zip(batch, embeddings):
            conn.execute(
                "INSERT INTO chunk_embedding (chunk_id, provider, model, dimensions, vector, input_hash)"
                " VALUES (?,?,?,?,?,?)",
                (chunk_id, PROVIDER, MODEL, DIMENSIONS, _vector_to_blob(embedding.values), text_hash),
            )
            embedded += 1
        conn.commit()
        print(f"  배치 {batch_num}/{n_batches} 완료 ({len(batch)}개)")
        if i + BATCH_SIZE < len(rows):
            time.sleep(BATCH_DELAY_SECONDS)
    return embedded


def run() -> None:
    conn = sqlite3.connect(DB_PATH)
    n_chunks = populate_posting_chunks(conn)
    client = genai.Client(api_key=settings.google_api_key)
    n_embedded = embed_pending_chunks(conn, client)
    print(f"청크 생성: {n_chunks}개, 임베딩 완료: {n_embedded}개 (model={MODEL}, dim={DIMENSIONS})")


if __name__ == "__main__":
    run()
