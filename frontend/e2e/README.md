# Oberflächentests (Playwright)

Touch-, Layout- und Screenshot-Tests für die Handy-Bedienung. Das Backend
wird vollständig gemockt (`mock.py`). Es werden nie echte Geräte
angesprochen.

## Einrichten

```bash
python -m venv .venv-e2e && . .venv-e2e/bin/activate
pip install playwright pytest pillow
python -m playwright install webkit chromium
```

Unter Fedora fehlen der WebKit-Build-Datei zwei Ubuntu-Bibliotheken
(`libicu74`, `libjpeg-turbo8`). Lege die `.so`-Dateien aus den Ubuntu-Paketen
nach `~/.cache/ms-playwright/webkit-*/minibrowser-wpe/sys/lib/`.

## Ausführen

Im Ordner `frontend/`:

```bash
npm run dev                      # Vite auf :5173, im Hintergrund lassen
pytest e2e -q                    # Chromium + WebKit
E2E_ENGINES=webkit pytest e2e -q
```

## Screenshots

```bash
python e2e/shots.py mobile  out/ webkit   # Handy-Profile hoch/quer
python e2e/shots.py sheets  out/ webkit   # Werte-Blatt und Bestätigung
python e2e/shots.py states  out/ webkit   # Übersicht in allen Mock-Zuständen
python e2e/shots.py wall    out/          # Wand 1920×1080 und 3840×2160
python e2e/shots.py desktop out/          # Desktop-Seiten
python e2e/compare.py vorher/ nachher/    # Pixelvergleich (Desktop unverändert?)
```

## Mock-Daten neu mitschneiden

`python e2e/capture.py` braucht die Demo-Instanz (`DEMO_MODE=true`, :8000)
und Vite. Das Skript schreibt `fixtures/api.json` und `fixtures/snapshot.json`.
Anmeldedaten werden nicht gespeichert.
