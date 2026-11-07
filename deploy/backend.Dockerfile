# Production image for the API (Render). Build context = repo root, so the demo data ships
# inside the image (docker-compose mounts it instead). Local dev keeps using backend/Dockerfile.
FROM python:3.11-slim

WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/backend/
COPY data/ /app/data/
COPY scripts/ /app/scripts/

ENV PYTHONUNBUFFERED=1 PORT=8000
EXPOSE 8000
# Render sets $PORT; shell form so it's expanded. No --reload in production.
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"
