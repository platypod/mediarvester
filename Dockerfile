# frontend/dist is built natively (not through QEMU) by the CI workflow
# before this Dockerfile ever runs -- see .github/workflows/build.yml. The
# frontend is pure JS/CSS/HTML with no architecture dependency, so building
# it once outside the multi-arch matrix (instead of once per emulated arch)
# is a large chunk of build time back.

FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/platypod/mediarvester

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY frontend/dist ./frontend/dist

RUN mkdir -p /app/data /app/downloads

EXPOSE 8080

ENV VERSION=dev
ENV MEDIA_ROOT=/app/downloads
ENV DATABASE_URL=sqlite+aiosqlite:////app/data/mediarvester.db
ENV YT_DLP_COOKIES_PATH=""
ENV YT_DLP_USERNAME=""
ENV YT_DLP_PASSWORD=""
ENV LOG_LEVEL=INFO

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--app-dir", "src"]
