"""Zentrale Anwendungs-Konfiguration (ENV-getrieben).

Alle laufzeitveränderlichen Einstellungen (Prioritäten, Schwellen, …) liegen
in der Datenbank (settings-Tabelle) und werden über das GUI gepflegt.
Hier stehen nur Bootstrap-Werte: DB-Verbindung, Secrets, Logging.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):
    database_url: str = "postgresql+asyncpg://minepower:minepower@localhost:5432/minepower"

    jwt_secret: str = "insecure-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    #: Gültigkeitsdauer eines Anmelde-Tokens.
    #:
    #: 12 Stunden waren für eine Anlagensteuerung im eigenen Netz zu kurz: Wer
    #: morgens nachsieht und abends noch einmal, musste sich jedes Mal neu
    #: anmelden. Dazu kommt, dass die Oberfläche das Token nur im Browser
    #: hält – ein abgelaufenes bedeutet also nicht 'unsicher", sondern nur
    #: 'lästig". 30 Tage, und solange die Oberfläche offen ist, wird das Token
    #: laufend erneuert (siehe /api/auth/refresh).
    jwt_expire_minutes: int = 30 * 24 * 60

    log_level: str = "INFO"
    control_interval_s: float = 3.0

    # Erststart-Verhalten
    demo_mode: bool = False
    headless_config: str = ""  # Pfad zu gemounteter JSON-Config (optional)

    cors_origins: str = "*"

    # Optionale MQTT-Integration (mqtt://host:port oder leer = deaktiviert)
    mqtt_url: str = ""
    mqtt_base_topic: str = "minepower"

    # Automatische lokale Sicherung (siehe services/backup.py). Schützt vor
    # Stromausfällen, die die Postgres-Datendatei beschädigen: eigenes
    # Volume, eigenes Format, unabhängig vom manuellen Export im GUI.
    backup_dir: str = "/app/backups"
    #: Persistente Daten der Anwendung (Schlüssel für Zugangsdaten). Eigenes
    #: Volume, nicht in der Datenbank – ein DB-Dump enthält keinen Schlüssel.
    data_dir: str = "/app/data"
    backup_interval_h: float = 6.0
    backup_keep: int = 30

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
