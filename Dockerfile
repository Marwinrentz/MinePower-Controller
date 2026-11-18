# All-in-One-Image: baut das React-Frontend und packt es ins Backend –
# FastAPI serviert die statischen Files selbst (kein nginx-Container nötig).
# Multi-Arch (amd64/arm64): docker buildx build --platform linux/amd64,linux/arm64 .

# --- Stage 1: Frontend bauen ---
# Node 22 (LTS): Node 20 erhält seit April 2026 keine Sicherheitsupdates mehr.
FROM node:22-alpine AS frontend
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

# Exakt gepinnte Versionen (requirements.txt, erzeugt aus requirements.in)
COPY backend/requirements.txt .
RUN pip install --no-compile -r requirements.txt

COPY backend/ .
COPY VERSION CHANGELOG.md LICENSE NOTICE ./
COPY --from=frontend /fe/dist /app/static
# Eigener Nutzer ohne Rechte; Datenverzeichnisse (Schlüssel für Zugangsdaten,
# Sicherungen) als Volumes einbinden. Der Code selbst gehört root und ist für
# die Anwendung nur lesbar – sie kann sich nicht selbst verändern.
RUN groupadd --gid 1000 minepower \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin minepower \
    && mkdir -p /app/data /app/backups \
    && chown minepower:minepower /app/data /app/backups \
    && chmod 0755 /app/docker-entrypoint.sh
ENV HOME=/tmp

EXPOSE 8000

# Auch ohne Compose (z. B. Unraid): Docker erkennt eine hängende Anwendung.
HEALTHCHECK --interval=15s --timeout=10s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/system/ready',timeout=5).status==200 else 1)"]

# Startet als root, übergibt die Datenordner und wechselt dann auf den
# unprivilegierten Nutzer (siehe docker-entrypoint.sh).
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
