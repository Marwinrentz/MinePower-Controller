"""CHANGELOG.md lesen und Versionen vergleichen.

Format je Version::

    ## 3.0.0 (2026-10-09)

    ### Neu
    - Eintrag

    ### Geändert
    - Eintrag

    ### Behoben
    - Eintrag
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

SECTIONS = {"neu": "new", "geändert": "changed", "behoben": "fixed"}
_HEAD = re.compile(r"^##\s+(\d+\.\d+\.\d+)\s*\((\d{4}-\d{2}-\d{2})\)\s*$")


def version_key(version: str | None) -> tuple[int, int, int]:
    parts = re.findall(r"\d+", version or "")[:3]
    nums = [int(p) for p in parts] + [0] * (3 - len(parts))
    return nums[0], nums[1], nums[2]


def _path() -> Path | None:
    here = Path(__file__).resolve().parents[2]
    for candidate in (here / "CHANGELOG.md", here.parent / "CHANGELOG.md"):
        if candidate.exists():
            return candidate
    return None


def parse(text: str) -> list[dict]:
    out: list[dict] = []
    current: dict | None = None
    section: str | None = None
    for line in text.splitlines():
        head = _HEAD.match(line.strip())
        if head:
            current = {"version": head.group(1), "date": head.group(2), "new": [], "changed": [], "fixed": []}
            out.append(current)
            section = None
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("### "):
            section = SECTIONS.get(stripped[4:].strip().lower())
        elif stripped.startswith("- ") and section:
            current[section].append(stripped[2:].strip())
    return out


@lru_cache
def entries() -> list[dict]:
    path = _path()
    return parse(path.read_text(encoding="utf-8")) if path else []


def since(version: str | None) -> list[dict]:
    """Alle Versionen neuer als `version`."""
    base = version_key(version)
    return [e for e in entries() if version_key(e["version"]) > base]
