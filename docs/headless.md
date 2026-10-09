# Headless-Konfiguration (ohne GUI-Erstlauf)

Für Docker-only-Setups kann die komplette Konfiguration als JSON-Datei
gemountet werden. Sie wird beim **ersten Start** importiert (nur wenn noch
keine Geräte konfiguriert sind – bestehende Installationen werden nie
überschrieben).

## Aktivieren

`docker-compose.yml` / `.env`:

```yaml
backend:
  environment:
    HEADLESS_CONFIG: /config/minepower.json
  volumes:
    - ./minepower.json:/config/minepower.json:ro
```

## Format

Identisch zum GUI-Export (`Einstellungen → Sicherung → Exportieren`),
plus optionalem `admin`-Block für den Erst-Benutzer:

```json
{
  "version": 1,
  "admin": { "email": "admin@example.com", "password": "sicheres-passwort", "name": "Admin" },
  "settings": {
    "regulation": { "interval_s": 3, "grid_target_w": 0, "deadband_w": 100,
                    "house_limit_a": 35, "battery_reserve_soc": 20, "ev_lock_soc": 30 },
    "priority": { "order": [] }
  },
  "devices": [
    { "_id": 1, "name": "Sungrow SH10RT", "category": "inverter",
      "driver_id": "sungrow_sh", "config": { "host": "192.0.2.50", "port": 502, "unit_id": 1 } },
    { "_id": 2, "name": "Netzzähler", "category": "meter",
      "driver_id": "sungrow_meter", "config": { "host": "192.0.2.50", "series": "sh" } },
    { "_id": 3, "name": "Wallbox", "category": "wallbox", "driver_id": "goe_charger",
      "config": { "host": "192.0.2.60", "max_current": 16 },
      "settings": { "mode": "pv_only", "min_current": 6, "max_current": 16, "phases_mode": "auto" } },
    { "_id": 4, "name": "Batterie", "category": "battery",
      "driver_id": "sungrow_battery", "config": { "host": "192.0.2.50" },
      "settings": { "reserve_soc": 20 } }
  ],
  "schedules": [],
  "vehicles": []
}
```

`_id` ist eine frei wählbare lokale Referenz, damit `priority.order` und
`schedules.device_id` auf Geräte zeigen können – beim Import werden echte
IDs vergeben und umgemappt.

Alternativ nur mit ENV starten und den **Demo-Modus** nutzen:
`DEMO_MODE=true` legt eine komplette Simulationsanlage samt Demo-Admin an
(`demo@minepower.de` / `demo1234` – sofort ändern!).
