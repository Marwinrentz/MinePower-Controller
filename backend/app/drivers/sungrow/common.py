"""Gemeinsame Basis der Sungrow-Treiberfamilie.

Kommunikation: Modbus TCP über WiNet-S-Dongle/LAN (Port 502) oder Modbus RTU.
Die Registerbelegung folgt der öffentlich dokumentierten Sungrow-
Kommunikationsspezifikation bzw. den etablierten Community-Registerkarten
(SG-/SH-Serie). Je nach Firmware-Stand können einzelne Register abweichen –
deshalb prüft jeder Treiber die gelesenen Werte auf Plausibilität und meldet
im Klartext, was zu tun ist, statt still einen Falschwert weiterzureichen.

WICHTIG (WiNet-S): In den WiNet-S-Einstellungen muss 'Modbus TCP' aktiviert
sein (weiße Oberfläche → Kommunikation). Standard-Unit-ID ist 1.

**Eine Verbindung für alles:** Wechselrichter, DTSU666-Zähler und Batterie
sind beim SH dasselbe physische Gerät. Alle Sungrow-Treiber teilen sich
deshalb über den Verbindungs-Pool genau eine Modbus-Session je IP – das ist
für den WiNet-S-Dongle der entscheidende Unterschied zwischen 'läuft' und
'fällt sporadisch aus'.
"""
from __future__ import annotations

from ..base import ConfigField, FieldType
from ..modbus_util import ModbusConnection, acquire_connection, release_connection

SUNGROW_COMMON_FIELDS: list[ConfigField] = [
    ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.50",
                help="IP des WiNet-S-Dongles bzw. des LAN-Anschlusses des Wechselrichters. "
                     "Am besten im Router eine feste IP vergeben – sonst geht die Anlage "
                     "nach einem DHCP-Wechsel offline."),
    ConfigField(key="port", label="Modbus-Port", type=FieldType.NUMBER, default=502,
                help="Standard: 502. Beim WiNet-S muss 'Modbus TCP' in dessen Weboberfläche "
                     "aktiviert sein, sonst bleibt der Port zu."),
    ConfigField(key="unit_id", label="Unit-ID (Slave-Adresse)", type=FieldType.NUMBER, default=1,
                help="Modbus-Geräteadresse, Standard bei Sungrow: 1. Bei WiNet-S manchmal 247 – "
                     "wenn der Verbindungstest 'Zielgerät nicht erreichbar' meldet, beides probieren."),
]


def make_connection(config: dict, name: str = "Sungrow") -> ModbusConnection:
    """Gepoolte Verbindung – alle Sungrow-Geräte derselben IP teilen sie sich."""
    return acquire_connection(
        host=str(config.get("host", "")).strip(),
        port=int(config.get("port") or 502),
        unit_id=int(config.get("unit_id") or 1),
        timeout=5.0,   # WiNet-S antwortet gelegentlich träge
        name=name,
    )


class SungrowDriverMixin:
    """Verbindungs-Lebenszyklus für alle Sungrow-Treiber."""

    conn: ModbusConnection

    async def connect(self) -> None:
        await self.conn.connect()

    async def disconnect(self) -> None:
        await release_connection(self.conn)


#: Running-State (Register 13001): Bit 1 = Batterie lädt, Bit 2 = entlädt
BIT_BAT_CHARGING = 0x0002
BIT_BAT_DISCHARGING = 0x0004
#: Unterhalb dieser Leistung (W) gilt die Batterie ohne gesetztes
#: Richtungsbit als ruhend (Messrauschen).
BATTERY_IDLE_W = 30.0


def battery_power_w(raw_register: int, running_state: int) -> float:
    """Batterieleistung aus Register 13022, + = laden, − = entladen.

    Das Register hat seine Bedeutung mit der Firmware gewechselt:

    * **bis Herbst 2024**: vorzeichenlos, immer positiv. Die Richtung steht
      im Running-State (Register 13001, Bit 1 lädt, Bit 2 entlädt).
    * **seitdem**: vorzeichenbehaftet (int16), positiv = laden, negativ =
      entladen.

    Vorzeichenlos gelesen wird aus −3000 W der Wert 62536. Mit gesetztem
    Entlade-Bit kam daraus −62 kW Entladung – der Batterie-Schutz hätte
    damit alle Lasten abgeworfen. Deshalb: als int16 lesen, den Betrag
    nehmen und die Richtung aus dem Vorzeichen (neue Firmware) oder aus den
    Zustandsbits (alte Firmware) bestimmen. Beide Wege führen bei beiden
    Firmware-Ständen zum selben Ergebnis.

    Quellen: Registerkarte mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant
    (Hinweis zu Register 13022, Issue 406), evcc-Vorlage sungrow-hybrid."""
    raw = raw_register - 0x10000 if raw_register >= 0x8000 else raw_register
    magnitude = float(abs(raw))
    if raw < 0 or running_state & BIT_BAT_DISCHARGING:
        return -magnitude
    if running_state & BIT_BAT_CHARGING:
        return magnitude
    # Kein Richtungsbit: Ruhe. Meldet neue Firmware trotzdem eine deutliche
    # positive Leistung, lädt die Batterie.
    return magnitude if magnitude >= BATTERY_IDLE_W else 0.0
