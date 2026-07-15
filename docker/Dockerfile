FROM python:3.12-slim

WORKDIR /app


COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/

RUN mkdir -p data/companies data/uploads

EXPOSE 8000

CMD ["python3", "backend/main.py"]
