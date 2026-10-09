"""Treiber-Registry mit Plugin-Discovery.

Alle Module unter app/drivers/ (inkl. Unterpakete) werden beim Start importiert;
Treiber registrieren sich selbst über den @register-Dekorator. Externe Plugins
können zusätzlich über den Ordner /app/plugins nachgeladen werden (gemountetes
Volume) – gleiche Struktur, gleicher Dekorator.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path

from .base import BaseDriver, DeviceCategory, DriverMeta

log = logging.getLogger(__name__)

_REGISTRY: dict[str, type[BaseDriver]] = {}


def register(cls: type[BaseDriver]) -> type[BaseDriver]:
    meta = cls.meta
    if meta.id in _REGISTRY:
        raise ValueError(f"Treiber-ID doppelt registriert: {meta.id}")
    _REGISTRY[meta.id] = cls
    return cls


def discover() -> None:
    """Importiert alle Treiber-Module (einmalig beim App-Start)."""
    import app.drivers as pkg

    for mod in pkgutil.walk_packages(pkg.__path__, prefix="app.drivers."):
        if mod.name.endswith((".base", ".registry")):
            continue
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # noqa: BLE001 – ein defekter Treiber darf den Start nicht verhindern
            log.error("Treiber-Modul %s konnte nicht geladen werden: %s", mod.name, exc)

    plugin_dir = Path("/app/plugins")
    if plugin_dir.is_dir():
        import sys

        sys.path.insert(0, str(plugin_dir))
        for file in plugin_dir.glob("*.py"):
            try:
                importlib.import_module(file.stem)
            except Exception as exc:  # noqa: BLE001
                log.error("Plugin %s konnte nicht geladen werden: %s", file.name, exc)


def all_metas() -> list[DriverMeta]:
    return [cls.meta for cls in _REGISTRY.values()]


def _order(meta: DriverMeta) -> tuple[int, str]:
    """Auswahlliste: Hersteller alphabetisch, dann generische Treiber, zuletzt Simulation."""
    if meta.id.startswith("sim_"):
        group = 2
    elif "generisch" in meta.name.lower() or meta.id.startswith(("ha_", "generic_")):
        group = 1
    else:
        group = 0
    return group, meta.name.lower()


def metas_for(category: DeviceCategory | str) -> list[DriverMeta]:
    cat = DeviceCategory(category)
    return sorted((m for m in all_metas() if m.category == cat), key=_order)


def get_driver_class(driver_id: str) -> type[BaseDriver]:
    if driver_id not in _REGISTRY:
        raise KeyError(f"Unbekannter Treiber: {driver_id}")
    return _REGISTRY[driver_id]


def create_driver(driver_id: str, config: dict) -> BaseDriver:
    return get_driver_class(driver_id)(config)
