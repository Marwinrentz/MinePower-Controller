# Multi-Arch-fähig (amd64/arm64): python:3.12-slim existiert für beide Plattformen.
# Build für mehrere Architekturen: docker buildx build --platform linux/amd64,linux/arm64 .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
