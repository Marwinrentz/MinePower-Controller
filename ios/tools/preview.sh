#!/usr/bin/env bash
# Screenshots des letzten CI-Laufs holen und in Geräterahmen anzeigen.
#
#   ios/tools/preview.sh            # letzter erfolgreicher oder fehlgeschlagener Lauf
#   ios/tools/preview.sh 1234567    # bestimmter Lauf
#   ios/tools/preview.sh --serve    # nur Server starten (vorhandene Bilder)
#
# Braucht die GitHub-CLI (`gh auth login` einmalig). Die Bilder landen in
# ios/preview/screenshots (nicht im Repo). Der Server lädt die Seite neu,
# sobald neue Bilder da sind.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST=preview/screenshots
PORT=${PORT:-8790}

if [[ "${1:-}" != "--serve" ]]; then
  RUN=${1:-$(gh run list --workflow ios.yml --limit 20 --json databaseId,status \
            --jq '[.[] | select(.status == "completed")][0].databaseId')}
  [[ -z "$RUN" ]] && { echo "Kein abgeschlossener ios-Lauf gefunden."; exit 1; }
  echo "Hole Screenshots aus Lauf $RUN …"
  rm -rf "$DEST.tmp" && mkdir -p "$DEST.tmp"
  gh run download "$RUN" -n screenshots -D "$DEST.tmp"
  rm -rf "$DEST" && mv "$DEST.tmp" "$DEST"
  echo "$(ls "$DEST" | grep -c png) Bilder."
fi

echo "Vorschau: http://127.0.0.1:$PORT"
exec python3 tools/preview-server/server.py --port "$PORT" --shots "$DEST"
