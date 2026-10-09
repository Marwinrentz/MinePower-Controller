# All-in-One-Image: baut das React-Frontend und packt es ins Backend –
# FastAPI serviert die statischen Files selbst (kein nginx-Container nötig).
# Multi-Arch (amd64/arm64): docker buildx build --platform linux/amd64,linux/arm64 .

# --- Stage 1: Frontend bauen ---
FROM node:20-alpine AS frontend
WORKDIR /fe
# Lockfile mitkopieren: `npm ci` installiert exakt die geprüften Versionen,
# statt bei jedem Build neu aufzulösen. Reproduzierbar und schneller.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ .
# Einzige Versionsquelle (vite.config.ts liest sie)
COPY VERSION ./VERSION
RUN npm run build

# --- Stage 2: Backend + statische Files ---
FROM python:3.12-slim

# Version für das Image-Label: docker build --build-arg APP_VERSION=$(cat VERSION) .
ARG APP_VERSION=unbekannt
LABEL org.opencontainers.image.title="MinePower" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.vendor="Marwin Rentz" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY backend/ .
COPY VERSION CHANGELOG.md LICENSE NOTICE ./
COPY --from=frontend /fe/dist /app/static
# Datenverzeichnis (Schlüssel für Zugangsdaten) – als Volume einbinden
RUN mkdir -p /app/data /app/backups

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
