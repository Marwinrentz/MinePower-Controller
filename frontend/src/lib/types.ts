/** API-Typen (Spiegel der Backend-Schemas). */

export type User = {
  id: number;
  email: string;
  name: string;
  role: "admin" | "user" | "readonly";
  rfid_tag: string | null;
  language: string;
  disabled: boolean;
  /** Zuletzt gesehene Version (Hinweisfenster nach Updates) */
  last_seen_version: string | null;
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
  /** Reifegrad – 'beta'/'experimental' wird im GUI deutlich gekennzeichnet,
   *  damit sich niemand blind auf ungeprüfte Registerkarten verlässt. */
  maturity: Maturity;
  notes: string | null;
  /** Abhilfe bei Offline (Diagnose) */
  offline_hint: string | null;
  /** Abhilfe bei Geräteprogramm (Capability "own_program") */
  own_program_hint: string | null;
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
  /** Läuft gerade gegen den Befehl (selbst gestartet, Watchdog …). */
  uncontrolled?: boolean;
  decision: Record<string, unknown>;
  /** Zeitpunkt des letzten erfolgreichen Messwerts (ISO). */
  last_seen?: string | null;
  /** Treiber-Fähigkeiten (z. B. "charge_limit", "wake") – die Oberfläche
   *  zeigt nur Bedienelemente, die das Gerät wirklich kann. */
  capabilities?: string[];
  data?: Record<string, unknown>;
  mode?: string;
  override?: string | null;
  /** Manueller Eingriff am Auto mit Ende (seit 2.16) */
  override_state?: OverrideState | null;
  /** Wo ist das Auto? (seit 2.16) */
  presence?: Presence;
  boost?: boolean;
  phases?: number;
  /** Fahrzeug/Wallbox */
  charge_limit_soc?: number | null;
  phases_mode?: "fixed1" | "fixed3" | "auto";
  phases_detected?: number | null;
  min_power_w?: number;
  start_threshold_w?: number;
  session?: { active: boolean; energy_kwh: number; solar_kwh: number; cost_eur: number };
  wake_note?: string | null;
  /** Warmwasser */
  target_temp_c?: number;
  boost_temp_c?: number;
  boost_state?: BoostState | null;
  boost_end_mode?: "time" | "temp" | "both";
  boost_duration_min?: number;
  max_power_w?: number;
  surplus_temp_c?: number | null;
  /** Sekunden seit Beginn eines Eigenprogramms des Geräts (my-PV 'Boost"). */
  self_mode_since_s?: number | null;
  /** Batterie: darf MinePower schreiben, kann der Treiber es, und welcher
   *  Sollmodus wurde zuletzt gesendet? */
  control_enabled?: boolean;
  control_capable?: boolean;
  target_mode?: BatteryMode | null;
  target_power_w?: number | null;
  mode_settling?: boolean;
  /** Reserve, die der Wechselrichter bestätigt hält (%), und sein Höchstwert */
  inverter_reserve?: number | null;
  reserve_max?: number | null;
  guard_mode?: "auto" | "hold" | "house";
};

export type Presence =
  | "charging" | "plugged" | "complete" | "unplugged"
  | "asleep_plugged" | "away" | "proxy_offline" | "offline" | "unknown";

export type OverrideState = {
  kind: "fast" | "stop";
  since: string | null;
  until: string | null;
  remaining_s: number | null;
  ends_on: "unplug" | "complete";
};

export type BatteryIntent = "auto" | "no_discharge" | "hold" | "charge" | "discharge";

export type BatteryPlan = {
  intent: BatteryIntent;
  label: string;
  reason: string;
  source: "manual" | "grid_charge" | "grid_loads" | "other_loads" | "foreign_loads" | "reserve" | "auto";
};

export type BatteryMode = "auto" | "force_charge" | "force_discharge" | "hold";

export type BoostState = {
  started: string | null;
  until: string | null;
  remaining_s: number | null;
  end_mode: "time" | "temp" | "both";
  target_temp_c: number;
};

export type BatteryManual = {
  intent?: BatteryIntent | null;
  mode: BatteryMode;
  power_w: number;
  minutes: number;
  started: string;
  until: string;
  remaining_s: number;
  target_soc: number | null;
  device_id: number | null;
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
  /** Liegt der aktuelle Preis unter der Grenze aus 'Tarif"? */
  price_cheap: boolean | null;
  price_limit_ct?: number | null;
  /** Läuft gerade ein Netzladefenster, und ist der Speicher dafür gesperrt? */
  grid_price_window?: boolean;
  battery_locked?: boolean;
  /** Warum gerade so geregelt wird – im Klartext. */
  price_reason?: string | null;
  battery_gate_reason?: string | null;
  battery_released?: boolean;
  /** Lädt der Speicher gerade aktiv aus dem Netz (nicht nur gesperrt)? */
  battery_grid_charging?: boolean;
  battery_manual?: BatteryManual | null;
  battery_manual_last?: { mode: BatteryMode; reason: string; at: string } | null;
  battery_reserve_soc?: number;
  /** Was der Speicher gerade tut und warum (Entscheidungstabelle) */
  battery_plan?: BatteryPlan;
  battery_first?: boolean;
  battery_priority_soc?: number;
  /** Lasten, die gerade absichtlich Netzstrom ziehen */
  grid_loads?: string[];
  /** Netzanschluss-Grenze und nach der Verteilung freier Rest (W). */
  grid_limit_w?: number;
  grid_budget_left_w?: number | null;
  /** Klartext-Hinweise (doppelte Geräte, Speicher im fremden Zwangsmodus …). */
  warnings?: string[];
  safety: string | null;
  paused: boolean;
  /** Verschenkter Solarstrom: Einspeisung über dem Netz-Sollwert, die keine
   *  steuerbare Last aufgenommen hat – und warum. */
  waste_w: number;
  waste_reason: string | null;
  /** Was der Lückenfüller in diesem Takt an Restüberschuss gerettet hat. */
  gap_filled_w: number;
  /** Totband: aktuell wirksam, Spanne und ob adaptiv. */
  deadband_w: number;
  deadband_min_w?: number;
  deadband_max_w?: number;
  adaptive_deadband?: boolean;
  interval_s?: number;
  weather: string | null;
  devices: SnapshotDevice[];
};

export type Finding = {
  level: "error" | "warn" | "info" | "ok";
  title: string;
  detail: string;
  fix?: string;
  count?: number;
  kwh?: number;
};

export type PriceSlot = { start: string; minutes: number; spot_ct: number; price_ct: number };

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
   *  das Regelziel 'keinen Watt verschenken' messen lässt. */
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
