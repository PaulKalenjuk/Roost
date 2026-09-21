# syntax=docker/dockerfile:1
# Build context is the REPO ROOT:  podman build -f backend/Dockerfile -t localhost/roost:latest .

# ---- Stage 1: build the TypeScript SPA -----------------------------------
FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/ ./
RUN npm install --no-audit --no-fund && npm run build
# vite.config.ts emits to ../backend/spa => /backend/spa

# ---- Stage 2: Django app + the built SPA ---------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY --from=frontend /backend/spa ./spa

RUN chmod +x entrypoint.sh \
 && SECRET_KEY=collectstatic-only DB_ENGINE=sqlite python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["./entrypoint.sh"]
