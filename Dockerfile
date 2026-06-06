FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir yt-dlp

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

EXPOSE 8080

ENV YT_DLP_COOKIES_PATH=""
ENV YT_DLP_USERNAME=""
ENV YT_DLP_PASSWORD=""

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
