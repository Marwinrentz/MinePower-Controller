#!/usr/bin/env python3
"""Projektregel: keine typografischen Anführungszeichen im Code (sie haben schon
einmal einen Build gebrochen). Ersetzt sie durch ' und meldet die Dateien.

    python3 ios/tools/no-typographic-quotes.py          # bereinigen
    python3 ios/tools/no-typographic-quotes.py --check  # nur prüfen, Exit 1 bei Fund
"""
import sys
from pathlib import Path

QUOTES = "".join(map(chr, (0x201E, 0x201C, 0x201D, 0x201A, 0x2018, 0x2019))) # als Codes, damit sich das Skript nicht selbst ändert
SUFFIXES = {".swift", ".yml", ".yaml", ".plist", ".sh", ".py", ".ts", ".tsx"}
SKIP = {".build", "xtool", "node_modules", ".git", "DerivedData"}

root = Path(__file__).resolve().parents[1]
check = "--check" in sys.argv
found = []
for path in root.rglob("*"):
    if not path.is_file() or path.suffix not in SUFFIXES or SKIP & set(path.parts):
        continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    if any(q in text for q in QUOTES):
        found.append(path.relative_to(root))
        if not check:
            for q in QUOTES:
                text = text.replace(q, "'")
            path.write_text(text, encoding="utf-8")
for f in found:
    print(("Fund: " if check else "bereinigt: ") + str(f))
sys.exit(1 if check and found else 0)
