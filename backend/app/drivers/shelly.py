"""Shelly-Geräte: 3EM/Pro 3EM als Netzzähler, Relais (Plug/1PM) für Heizstab.

Gen1 (3EM) spricht `/status`, Gen2+ (Pro 3EM) die RPC-API. Die Generation
wird konfiguriert, kann aber beim Verbindungstest automatisch erkannt werden –
die falsche Generation ist der häufigste Einrichtungsfehler bei Shelly.
"""
from __future__ import annotations

import asyncio

from .base import (
    ConfigField,
    DeviceCategory,
    DriverMeta,
    FieldType,
    MeterData,
    MeterDriver,
    WaterHeaterData,
    WaterHeaterDriver,
)
from .http_util import HttpDevice
from .registry import register
from .validation import CommandNotApplied, DriverError, checked, sanitized

GENERATION_FIELD = ConfigField(
    key="generation", label="Geräte-Generation", type=FieldType.SELECT, default="gen2",
    options=[{"value": "gen2", "label": "Gen2+ (Pro-Serie, RPC-API)"},
             {"value": "gen1", "label": "Gen1 (ältere Modelle, /status)"}],
    help="Bestimmt die verwendete API. Falsch gewählt meldet der Verbindungstest HTTP 404 – "
         "er schlägt dann die richtige Generation vor.",
)


@register
class Shelly3EM(MeterDriver):
    meta = DriverMeta(
        id="shelly_3em",
        name="Shelly 3EM / Pro 3EM (Netzzähler)",
        category=DeviceCategory.METER,
        description="Shelly 3-Phasen-Energiemesser am Hausanschluss. Gen1 (3EM) über /status, "
                    "Gen2+ (Pro 3EM) über RPC. Positive Leistung = Netzbezug "
                    "(Wandler-Richtung beachten).",
        fields=[
            ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.40",
                        help="IP des Shelly im lokalen Netz. Feste IP im Router vergeben – "
                             "sonst geht die Netzmessung nach einem DHCP-Wechsel verloren."),
            GENERATION_FIELD,
            ConfigField(key="invert", label="Richtung invertieren", type=FieldType.BOOLEAN, default=False,
                        required=False,
                        help="Aktivieren, falls Einspeisung fälschlich als Bezug angezeigt wird "
                             "(Stromwandler seitenverkehrt montiert). Der Verbindungstest zeigt "
                             "die aktuelle Richtung im Klartext."),
        ],
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice(f"http://{str(config.get('host', '')).strip()}", name="Shelly 3EM")
        self.gen2 = config.get("generation", "gen2") != "gen1"
        self.sign = -1.0 if config.get("invert") else 1.0

    async def connect(self) -> None:
        await self.http.connect()

    async def disconnect(self) -> None:
        await self.http.close()

    async def _read_gen2(self) -> tuple[float, list[float | None]]:
        """Rohwerte (ungedreht) – Vorzeichen wird zentral in read_data angewandt."""
        d = await self.http.get_json("/rpc/EM.GetStatus", {"id": 0})
        total = d.get("total_act_power")
        if total is None:
            raise DriverError(
                "Shelly antwortet, liefert aber kein Feld 'total_act_power'. Ist das Gerät "
                "wirklich ein Pro 3EM (Energiemesser) und kein Schaltaktor?"
            )
        phases = [sanitized("grid_power", _f(d.get(k))) for k in ("a_act_power", "b_act_power", "c_act_power")]
        return float(total), phases

    async def _read_gen1(self) -> tuple[float, list[float | None]]:
        d = await self.http.get_json("/status")
        emeters = d.get("emeters") or []
        if not emeters:
            raise DriverError(
                "Shelly antwortet, liefert aber keine 'emeters'. Für Gen2-Geräte (Pro 3EM) "
                "bitte die Generation auf 'Gen2+' umstellen."
            )
        powers = [_f(e.get("power")) for e in emeters[:3]]
        phases: list[float | None] = list(powers) + [None] * (3 - len(powers))
        return sum(powers), phases

    async def read_data(self) -> MeterData:
        total, phases = await (self._read_gen2() if self.gen2 else self._read_gen1())
        signed = [None if p is None else self.sign * p for p in phases]
        return MeterData(
            grid_power=self.sign * checked("grid_power", total, source="Shelly Gesamtwirkleistung"),
            power_l1=signed[0],
            power_l2=signed[1],
            power_l3=signed[2],
        )

    async def _probe(self) -> dict:
        """Erkennt beim Test selbst, ob die eingestellte Generation stimmt."""
        try:
            return await super()._probe()
        except DriverError as first:
            try:
                total, _ = await (self._read_gen1() if self.gen2 else self._read_gen2())
            except DriverError:
                raise first from None
            correct = "Gen1" if self.gen2 else "Gen2+"
            return {
                "⚠ Geräte-Generation": (
                    f"Die eingestellte Generation passt nicht zu diesem Shelly. Bitte auf "
                    f"'{correct}' umstellen – damit antwortet das Gerät sauber."
                ),
                "Netzleistung (Gegenprobe)": f"{self.sign * total:.0f} W",
            }


@register
class ShellyRelayHeater(WaterHeaterDriver):
    meta = DriverMeta(
        id="shelly_relay_heater",
        name="Heizstab über Shelly-Relais",
        category=DeviceCategory.WATER_HEATER,
        description="Ein-/Aus-Steuerung eines Heizstabs über ein Shelly-Relais (Plug S, 1PM, Pro 1PM). "
                    "Keine Modulation – der Regler schaltet bei ausreichend Überschuss ein. "
                    "Für stufenloses Nachführen kleiner Überschüsse ist ein modulierendes Gerät "
                    "(z. B. my-PV) deutlich effizienter.",
        capabilities={"relay", "write_test"},
        fields=[
            ConfigField(key="host", label="IP-Adresse", placeholder="192.0.2.45",
                        help="IP des Shelly-Relais."),
            GENERATION_FIELD,
            ConfigField(key="rated_power", label="Heizstab-Leistung (W)", type=FieldType.NUMBER, default=3000,
                        help="Nennleistung der geschalteten Last – wird für die Überschussrechnung "
                             "verwendet, solange das Relais keine eigene Messung liefert."),
        ],
    )

    modulating = False

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.http = HttpDevice(f"http://{str(config.get('host', '')).strip()}", name="Shelly Relais")
        self.gen2 = config.get("generation", "gen2") != "gen1"
        self.rated_power = float(config.get("rated_power") or 3000)

    async def connect(self) -> None:
        await self.http.connect()

    async def disconnect(self) -> None:
        await self.http.close()

    async def read_data(self) -> WaterHeaterData:
        if self.gen2:
            d = await self.http.get_json("/rpc/Switch.GetStatus", {"id": 0})
            if "output" not in d:
                raise DriverError(
                    "Shelly liefert keinen Schaltzustand ('output'). Für Gen1-Geräte bitte die "
                    "Generation auf 'Gen1' umstellen."
                )
            on = bool(d.get("output"))
            measured = d.get("apower")
        else:
            d = await self.http.get_json("/status")
            relays = d.get("relays") or []
            if not relays:
                raise DriverError(
                    "Shelly liefert keine 'relays'. Für Gen2-Geräte bitte die Generation "
                    "auf 'Gen2+' umstellen."
                )
            on = bool(relays[0].get("ison"))
            meters = d.get("meters") or []
            measured = meters[0].get("power") if meters else None
        power = _f(measured) if measured is not None else (self.rated_power if on else 0.0)
        return WaterHeaterData(
            power=checked("heater_power", power, source="Shelly Relais-Leistung"),
            is_on=on,
        )

    async def set_power(self, watts: float) -> None:
        """Relais: >50 % Nennleistung = EIN. Mit Readback, weil ein Shelly
        einen Schaltbefehl auch dann quittiert, wenn ein aktiver Zeitplan
        oder eine Sperre ihn direkt wieder zurücknimmt."""
        turn_on = watts >= self.rated_power * 0.5
        if self.gen2:
            await self.http.get_json("/rpc/Switch.Set", {"id": 0, "on": turn_on})
        else:
            await self.http.get_text("/relay/0", {"turn": "on" if turn_on else "off"})
        await asyncio.sleep(0.4)
        state = await self.read_data()
        if state.is_on != turn_on:
            raise CommandNotApplied(
                f"Shelly-Relais hat den Schaltbefehl nicht übernommen "
                f"(gewünscht: {'ein' if turn_on else 'aus'}, gemeldet: "
                f"{'ein' if state.is_on else 'aus'}). Prüfen, ob im Shelly ein Zeitplan, "
                f"eine Einschaltsperre oder ein Überhitzungsschutz aktiv ist."
            )

    async def test_command(self) -> dict | None:
        """Aus-Befehl mit Readback – schaltet den Heizstab nur ab, nie ein."""
        try:
            await self.set_power(0.0)
        except CommandNotApplied as exc:
            return {"ok": False, "message": str(exc), "sent": "aus", "readback": "ein"}
        return {"ok": True, "message": "Aus-Befehl gesendet und vom Relais bestätigt.",
                "sent": "aus", "readback": "aus"}


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
