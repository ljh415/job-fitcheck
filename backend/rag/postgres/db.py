"""Plan B PostgreSQL 연결 헬퍼. `rag/ingest.py` 등이 매번 `sqlite3.connect(DB_PATH)`를 부르던
자리를 대신한다. `register_vector()`로 pgvector 타입을 등록해두면 이후 코드는 파이썬
`list[float]`을 그대로 INSERT/조회할 수 있다(BLOB pack/unpack 불필요).
"""
import psycopg
from pgvector.psycopg import register_vector

from config import settings


def get_connection() -> psycopg.Connection:
    conn = psycopg.connect(
        host=settings.rag_postgres_host,
        port=settings.rag_postgres_port,
        dbname=settings.rag_postgres_db,
        user=settings.rag_postgres_user,
        password=settings.rag_postgres_password,
        autocommit=False,
    )
    # register_vector()는 vector 타입이 DB에 이미 존재해야 하는데, 스키마 생성(CREATE EXTENSION)이
    # 이 함수 호출 시점엔 아직 안 됐을 수 있어(최초 실행) 여기서 먼저 만들어둔다 — idempotent.
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    register_vector(conn)
    return conn
