# Multi-Arch-fähig (amd64/arm64): python:3.12-slim existiert für beide Plattformen.
# Build für mehrere Architekturen: docker buildx build --platform linux/amd64,linux/arm64 .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-compile -r requirements.txt

COPY . .
# Wie im All-in-One-Image: eigener Nutzer ohne Rechte (siehe docker-entrypoint.sh)
RUN groupadd --gid 1000 minepower \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin minepower \
    && mkdir -p /app/data /app/backups \
    && chown minepower:minepower /app/data /app/backups \
    && chmod 0755 /app/docker-entrypoint.sh
ENV HOME=/tmp

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
