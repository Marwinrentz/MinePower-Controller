#!/usr/bin/env bash
# Startet ein MinePower-Backend im Demo-Modus (SQLite, simulierte Geräte).
# Für Fixture-Mitschnitt, UI-Tests in CI und Vorschau. Keine echten Geräte.
#
#   ios/tools/demo-server.sh [PORT]          # Standard 18080
#
# Anmeldung: demo@minepower.de / demo1234 (nur Demo)
set -euo pipefail
PORT="${1:-18080}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="${MP_DEMO_DIR:-$(mktemp -d)}"
PY="${PYTHON:-python3}"
cd "$ROOT/backend"
export DATABASE_URL="sqlite+aiosqlite:///$DATA/demo.db"
export DATA_DIR="$DATA" BACKUP_DIR="$DATA/backups"
export JWT_SECRET="demo-only-secret-0123456789abcdef0123456789"
export DEMO_MODE=true
exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
