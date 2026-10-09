"""Gemeinsame Test-Fixtures.

Setzt `DATABASE_URL` auf eine Wegwerf-SQLite-Datei, bevor irgendein
Testmodul `app.db` importiert – das Modul legt die Engine beim Import an
(`create_async_engine(get_settings().database_url)`), zu spät gesetzte
Umgebungsvariablen kämen also nicht mehr an. `conftest.py` lädt garantiert
vor jedem Testmodul im Verzeichnis, deshalb steht das hier und nicht in
einer einzelnen Testdatei.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/minepower_test.db")
os.environ.setdefault("JWT_SECRET", "test-secret")
# Schlüssel für Zugangsdaten (sonst Schlüsseldatei unter /app/data)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
