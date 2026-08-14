FROM python:3.12-slim

WORKDIR /app

# RAG(Agentic RAG) — LocalEmbeddingProvider가 opt-in으로 켜졌을 때 GPU 임베딩 서버로 SSH
# 터널을 여는 데 필요. RAG_LOCAL_SSH_HOST 미설정 배포엔 안 쓰이지만, 설치해도 용량·보안상
# 문제 없어 항상 포함한다.
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/

RUN mkdir -p data/companies data/uploads

EXPOSE 8000

CMD ["python3", "backend/main.py"]
