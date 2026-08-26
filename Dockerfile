# API container for Google Cloud Run (or any host). Set env: OPENAI_API_KEY, PINECONE_*,
# FIREBASE_SERVICE_ACCOUNT_JSON or mount GOOGLE_APPLICATION_CREDENTIALS.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY scripts ./scripts

# Cloud Run sets PORT; local default 8080
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
