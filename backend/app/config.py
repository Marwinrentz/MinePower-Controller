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
    jwt_expire_minutes: int = 12 * 60

    log_level: str = "INFO"
    control_interval_s: float = 3.0

    # Erststart-Verhalten
    demo_mode: bool = False
    headless_config: str = ""  # Pfad zu gemounteter JSON-Config (optional)

    cors_origins: str = "*"

    # Optionale MQTT-Integration (mqtt://host:port oder leer = deaktiviert)
    mqtt_url: str = ""
    mqtt_base_topic: str = "minepower"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
