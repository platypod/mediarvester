# --- Frontend build ---
FROM node:20-slim AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- App ---
FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/platypod/mediarvester

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY --from=frontend-builder /frontend/dist ./frontend/dist

RUN mkdir -p /app/data /app/downloads

EXPOSE 8080

ENV MEDIA_ROOT=/app/downloads
ENV DATABASE_URL=sqlite+aiosqlite:////app/data/mediarvester.db
ENV YT_DLP_COOKIES_PATH=""
ENV YT_DLP_USERNAME=""
ENV YT_DLP_PASSWORD=""
ENV LOG_LEVEL=INFO

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--app-dir", "src"]
