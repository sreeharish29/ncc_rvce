# ---- 1. Build the Next.js static site ----
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
ENV NEXT_EXPORT=1
RUN npm run build            # writes /web/out

# ---- 2. FastAPI + static site + data ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CACHE_DIR=/tmp/cache \
    STATIC_DIR=/app/static
WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Keep the same layout main.py expects: BASE = /app
COPY backend/ ./backend/
COPY hf_model/output/ ./hf_model/output/
# COPY ["NSS ACTIVITY POINTS LIST FROM 2021 to 2024", "./NSS ACTIVITY POINTS LIST FROM 2021 to 2024"]
COPY --from=web /web/out ./static

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]