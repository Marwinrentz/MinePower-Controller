/** API-Typen (Spiegel der Backend-Schemas). */

export type User = {
  id: number;
  email: string;
  name: string;
  role: "admin" | "user" | "readonly";
  rfid_tag: string | null;
  language: string;
  disabled: boolean;
};

export type ConfigField = {
  key: string;
  label: string;
  type: "text" | "number" | "password" | "boolean" | "select";
  required: boolean;
  default: unknown;
  options: { value: string; label: string }[] | null;
  placeholder: string | null;
  help: string | null;
};

export type Maturity = "stable" | "beta" | "experimental";

export type DriverMeta = {
  id: string;
  name: string;
  category: DeviceCategory;
  description: string;
  fields: ConfigField[];
  capabilities: string[];
  /** Reifegrad – „beta“/„experimental“ wird im GUI deutlich gekennzeichnet,
   *  damit sich niemand blind auf ungeprüfte Registerkarten verlässt. */
  maturity: Maturity;
  notes: string | null;
};

export type DeviceCategory = "inverter" | "meter" | "wallbox" | "water_heater" | "battery";

export type Device = {
  id: number;
  name: string;
  category: DeviceCategory;
  driver_id: string;
  config: Record<string, unknown>;
  settings: Record<string, unknown>;
  enabled: boolean;
  status: string;
  last_error: string | null;
  last_seen: string | null;
};

export type WallboxLiveData = {
  state: "idle" | "connected" | "charging" | "complete" | "error";
  power: number;
  current_set: number | null;
  phases_active: number | null;
  energy_session_kwh: number | null;
  soc: number | null;
  rfid_tag: string | null;
};

export type SnapshotDevice = {
  id: number;
  name: string;
  category: DeviceCategory;
  driver_id: string;
  enabled: boolean;
  online: boolean;
  last_error: string | null;
  /** Gerät antwortet, hat den letzten Stellbefehl aber nicht übernommen
   *  (Readback-Gegenprüfung im Regelkreis). */
  command_error: string | null;
  decision: Record<string, unknown>;
  /** Treiber-Fähigkeiten (z. B. "charge_limit", "phase_switch") – die
   *  Oberfläche zeigt nur Bedienelemente, die das Gerät wirklich kann. */
  capabilities?: string[];
  data?: Record<string, unknown>;
  mode?: string;
  override?: string | null;
  boost?: boolean;
  phases?: number;
  /** Im Fahrzeug hinterlegte Ladegrenze in % (nur Fahrzeug-Treiber). */
  charge_limit_soc?: number | null;
};

export type Snapshot = {
  time: string | null;
  pv_power: number;
  grid_power: number | null;
  house_power: number;
  wallbox_power: number;
  water_power: number;
  battery: { soc: number; power: number } | null;
  surplus: number;
  price_ct: number | null;
  /** Liegt der aktuelle Preis unter dem eingestellten Günstig-Grenzwert?
   *  Kommt aus dem Backend, damit die Oberfläche den Grenzwert nicht raten muss. */
  price_cheap: boolean | null;
  safety: string | null;
  paused: boolean;
  /** Verschenkter Solarstrom: Einspeisung über dem Netz-Sollwert, die keine
   *  steuerbare Last aufgenommen hat. */
  waste_w: number;
  waste_reason: string | null;
  /** Was der Lückenfüller in diesem Tick an Restüberschuss gerettet hat. */
  gap_filled_w: number;
  /** Aktuell wirksames Totband (adaptiv) und die Wetterlage dazu. */
  deadband_w: number;
  weather: string | null;
  devices: SnapshotDevice[];
};

export type TestResult = {
  ok: boolean;
  message: string;
  values: Record<string, unknown>;
  warnings: string[];
  write_test: { ok: boolean; message: string; sent?: unknown; readback?: unknown } | null;
};

export type Statistics = {
  range: string;
  pv_kwh: number;
  grid_import_kwh: number;
  grid_export_kwh: number;
  consumption_kwh: number;
  autarky_pct: number;
  self_consumption_pct: number;
  charged_kwh: number;
  charged_solar_kwh: number;
  charged_solar_pct: number;
  savings_eur: number;
  co2_saved_kg: number;
  session_count: number;
  /** Verschenkter Solarstrom über den Zeitraum – die Kennzahl, an der sich
   *  das Regelziel „keinen Watt verschenken“ messen lässt. */
  wasted_kwh: number;
  wasted_pct: number;
  wasted_value_eur: number;
};

export type ChargeSession = {
  id: number;
  device_id: number;
  started_at: string;
  ended_at: string | null;
  energy_kwh: number;
  solar_kwh: number;
  cost_eur: number;
  rfid_tag: string | null;
  user_id: number | null;
};

export type AppEvent = {
  id: number;
  time: string;
  level: string;
  category: string;
  message: string;
  data: Record<string, unknown>;
};
