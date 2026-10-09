"""MinePower-Backend."""
from pathlib import Path


def _read_version() -> str:
    """Versionsnummer aus der Datei VERSION – einzige Quelle für Backend,
    Frontend-Build und Docker-Label. Im Image liegt sie unter /app/VERSION,
    im Repository eine Ebene über backend/."""
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "VERSION", here.parent.parent / "VERSION"):
        try:
            return candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return "0.0.0"


__version__ = _read_version()
