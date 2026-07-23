FROM python:3.12-slim

WORKDIR /app

# RAG 서브프로젝트(rag/main 전용) — LocalEmbeddingProvider가 3050Ti로 SSH 터널을 여는 데 필요.
# main 브랜치엔 필요 없지만, 설치해도 용량·보안상 문제 없어 그대로 둠.
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/

RUN mkdir -p data/companies data/uploads

EXPOSE 8000

CMD ["python3", "backend/main.py"]
