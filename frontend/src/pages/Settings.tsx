/** Einstellungen: Regelung, Prioritätskette, Tarif, Prognose, Benachrichtigungen,
    Benutzer, Fahrzeuge, Backup (Export/Import). */
import { useEffect, useRef, useState } from "react";
import { Button, Card, Field, Segment, Toggle } from "../components/ui";
import { PriorityChain } from "../components/PriorityChain";
import { useI18n } from "../i18n";
import { del, get, post, put } from "../lib/api";
import type { Device, User } from "../lib/types";
import { useAuth } from "../store/auth";
import { useTheme, type ThemeMode } from "../store/theme";

type SettingsMap = Record<string, Record<string, unknown>>;

function Num({ value, onChange }: { value: unknown; onChange: (v: number) => void }) {
  return <input className="input" type="number" step="any" value={String(value ?? "")}
                onChange={(e) => onChange(Number(e.target.value))} />;
}

export function Settings() {
  const { t, lang, setLang } = useI18n();
  const { mode, setMode } = useTheme();
  const me = useAuth((s) => s.user);
  const [values, setValues] = useState<SettingsMap>({});
  const [devices, setDevices] = useState<Device[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [newUser, setNewUser] = useState({ email: "", name: "", password: "", role: "user", rfid_tag: "" });
  const [saved, setSaved] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => {
    get<{ values: SettingsMap }>("/api/settings").then((r) => setValues(r.values)).catch(() => {});
    get<Device[]>("/api/devices").then(setDevices).catch(() => {});
    if (me?.role === "admin") get<User[]>("/api/auth/users").then(setUsers).catch(() => {});
  };
  useEffect(load, []);

  const setVal = (section: string, key: string, v: unknown) =>
    setValues((prev) => ({ ...prev, [section]: { ...(prev[section] ?? {}), [key]: v } }));

  const save = async () => {
    await put("/api/settings", { values });
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2000);
  };

  const savePriority = async (order: number[]) => {
    setVal("priority", "order", order);
    await put("/api/settings/priority", { order });
  };

  const exportConfig = async () => {
    const data = await get<unknown>("/api/system/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "minepower-config.json";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const importConfig = async (file: File) => {
    const text = await file.text();
    await post("/api/system/import", JSON.parse(text));
    load();
  };

  const addUser = async () => {
    await post("/api/auth/users", { ...newUser, rfid_tag: newUser.rfid_tag || null });
    setNewUser({ email: "", name: "", password: "", role: "user", rfid_tag: "" });
    load();
  };

  const reg = values.regulation ?? {};
  const tariff = values.tariff ?? {};
  const forecast = values.forecast ?? {};
  const notif = values.notifications ?? {};

  return (
    <div className="page">
      <header className="page-head">
        <h1 className="page-title">{t("nav.settings")}</h1>
        <div className="row tight">
          {saved && <span className="badge ok">{t("common.save")}</span>}
          <Button variant="primary" onClick={save}>{t("common.save")}</Button>
        </div>
      </header>

      <div className="grid cols-2">
        <Card>
          <div className="card-head"><h2>{t("settings.regulation")}</h2></div>
          <Field label={t("settings.intervalS")} help={t("settings.intervalHelp")}>
            <Num value={reg.interval_s} onChange={(v) => setVal("regulation", "interval_s", v)} />
          </Field>
          <Field label={t("settings.gridTarget")} help={t("settings.gridTargetHelp")}>
            <Num value={reg.grid_target_w} onChange={(v) => setVal("regulation", "grid_target_w", v)} />
          </Field>
          <Field label={t("settings.deadband")} help={t("settings.deadbandHelp")}>
            <Num value={reg.deadband_w} onChange={(v) => setVal("regulation", "deadband_w", v)} />
          </Field>
          <Field label={t("settings.houseLimit")} help={t("settings.houseLimitHelp")}>
            <Num value={reg.house_limit_a} onChange={(v) => setVal("regulation", "house_limit_a", v)} />
          </Field>
          <Field label={t("settings.batteryPriority")} help={t("settings.batteryPriorityHelp")}>
            <Toggle on={reg.battery_priority !== false}
                    onChange={(v) => setVal("regulation", "battery_priority", v)} />
          </Field>
          <Field label={t("settings.batteryDrainLimit")} help={t("settings.batteryDrainLimitHelp")}>
            <Num value={reg.battery_drain_limit_w} onChange={(v) => setVal("regulation", "battery_drain_limit_w", v)} />
          </Field>
          {reg.battery_priority === false && (
            <>
              <Field label={t("settings.batteryReserve")} help={t("settings.batteryReserveHelp")}>
                <Num value={reg.battery_reserve_soc} onChange={(v) => setVal("regulation", "battery_reserve_soc", v)} />
              </Field>
              <Field label={t("settings.evLock")} help={t("settings.evLockHelp")}>
                <Num value={reg.ev_lock_soc} onChange={(v) => setVal("regulation", "ev_lock_soc", v)} />
              </Field>
            </>
          )}

          <h3 style={{ marginTop: "var(--s5)" }}>{t("dash.wasted")}</h3>
          <Field label={t("settings.adaptiveDeadband")} help={t("settings.adaptiveDeadbandHelp")}>
            <Toggle on={reg.adaptive_deadband !== false}
                    onChange={(v) => setVal("regulation", "adaptive_deadband", v)} />
          </Field>
          {reg.adaptive_deadband !== false && (
            <div className="grid cols-2">
              <Field label={t("settings.deadbandMin")}>
                <Num value={reg.deadband_min_w} onChange={(v) => setVal("regulation", "deadband_min_w", v)} />
              </Field>
              <Field label={t("settings.deadbandMax")}>
                <Num value={reg.deadband_max_w} onChange={(v) => setVal("regulation", "deadband_max_w", v)} />
              </Field>
            </div>
          )}
          <Field label={t("settings.gapFill")} help={t("settings.gapFillHelp")}>
            <Num value={reg.gap_fill_min_w} onChange={(v) => setVal("regulation", "gap_fill_min_w", v)} />
          </Field>
          <Field label={t("settings.useForecast")} help={t("settings.useForecastHelp")}>
            <Toggle on={reg.use_forecast !== false}
                    onChange={(v) => setVal("regulation", "use_forecast", v)} />
          </Field>
        </Card>

        <Card>
          <h2>{t("settings.priority")}</h2>
          <p className="dim">{t("wizard.priorityText")}</p>
          <PriorityChain
            devices={devices}
            order={(values.priority?.order as number[]) ?? []}
            onChange={savePriority}
          />
          <div style={{ height: 20 }} />
          <h2>{t("settings.theme")} / {t("settings.language")}</h2>
          <div className="row wrap">
            <Segment<ThemeMode>
              options={[
                { value: "light", label: t("settings.themeLight") },
                { value: "dark", label: t("settings.themeDark") },
                { value: "system", label: t("settings.themeSystem") },
              ]}
              value={mode}
              onChange={setMode}
            />
            <Segment
              options={[{ value: "de", label: "Deutsch" }, { value: "en", label: "English" }]}
              value={lang}
              onChange={setLang}
            />
          </div>
        </Card>

        <Card>
          <h2>{t("settings.tariff")}</h2>
          <Field label={t("settings.tariffProvider")} help={t("settings.tariffHelp")}>
            <select className="select" value={String(tariff.provider ?? "none")}
                    onChange={(e) => setVal("tariff", "provider", e.target.value)}>
              <option value="none">–</option>
              <option value="tibber">Tibber</option>
              <option value="awattar">aWATTar / EPEX</option>
            </select>
          </Field>
          {tariff.provider === "tibber" && (
            <Field label="Tibber API-Token">
              <input className="input" type="password" value={String(tariff.tibber_token ?? "")}
                     onChange={(e) => setVal("tariff", "tibber_token", e.target.value)} />
            </Field>
          )}
          {tariff.provider === "awattar" && (
            <Field label="Region">
              <select className="select" value={String(tariff.awattar_region ?? "de")}
                      onChange={(e) => setVal("tariff", "awattar_region", e.target.value)}>
                <option value="de">Deutschland</option>
                <option value="at">Österreich</option>
              </select>
            </Field>
          )}
          <Field label={t("settings.cheapLimit")} help={t("settings.cheapLimitHelp")}>
            <Num value={tariff.cheap_limit_ct} onChange={(v) => setVal("tariff", "cheap_limit_ct", v)} />
          </Field>
          <Field label={t("settings.priceWindowBattery")} help={t("settings.priceWindowBatteryHelp")}>
            <select className="select" value={String(reg.price_window_battery ?? "hold")}
                    onChange={(e) => setVal("regulation", "price_window_battery", e.target.value)}>
              <option value="hold">{t("settings.priceWindowHold")}</option>
              <option value="charge">{t("settings.priceWindowCharge")}</option>
            </select>
          </Field>

          <h2 style={{ marginTop: 20 }}>{t("settings.forecast")}</h2>
          <Field label="Anbieter" help={t("settings.forecastHelp")}>
            <select className="select" value={String(forecast.provider ?? "none")}
                    onChange={(e) => setVal("forecast", "provider", e.target.value)}>
              <option value="none">–</option>
              <option value="forecast_solar">forecast.solar</option>
            </select>
          </Field>
          {forecast.provider === "forecast_solar" && (
            <div className="grid cols-2">
              <Field label="Breitengrad"><Num value={forecast.lat} onChange={(v) => setVal("forecast", "lat", v)} /></Field>
              <Field label="Längengrad"><Num value={forecast.lon} onChange={(v) => setVal("forecast", "lon", v)} /></Field>
              <Field label="kWp"><Num value={forecast.kwp} onChange={(v) => setVal("forecast", "kwp", v)} /></Field>
              <Field label="Neigung (°)"><Num value={forecast.declination} onChange={(v) => setVal("forecast", "declination", v)} /></Field>
            </div>
          )}
        </Card>

        <Card>
          <h2>{t("settings.notifications")}</h2>
          <Field label="ntfy-URL" help={t("settings.ntfyHelp")}>
            <input className="input" value={String(notif.ntfy_url ?? "")}
                   placeholder="https://ntfy.sh/mein-topic"
                   onChange={(e) => setVal("notifications", "ntfy_url", e.target.value)} />
          </Field>
          <div className="grid cols-2">
            <Field label="Telegram Bot-Token">
              <input className="input" type="password" value={String(notif.telegram_token ?? "")}
                     onChange={(e) => setVal("notifications", "telegram_token", e.target.value)} />
            </Field>
            <Field label="Telegram Chat-ID">
              <input className="input" value={String(notif.telegram_chat_id ?? "")}
                     onChange={(e) => setVal("notifications", "telegram_chat_id", e.target.value)} />
            </Field>
          </div>

          <h2 style={{ marginTop: 20 }}>{t("settings.backup")}</h2>
          <p className="dim">{t("settings.backupText")}</p>
          <div className="row wrap">
            <Button onClick={exportConfig}>{t("common.export")}</Button>
            <Button onClick={() => fileRef.current?.click()}>{t("common.import")}</Button>
            <input ref={fileRef} type="file" accept=".json" style={{ display: "none" }}
                   onChange={(e) => e.target.files?.[0] && importConfig(e.target.files[0])} />
          </div>
        </Card>
      </div>

      {me?.role === "admin" && (
        <Card>
          <h2>{t("settings.users")}</h2>
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr><th>E-Mail</th><th>Name</th><th>Rolle</th><th>RFID</th><th></th></tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td>{u.email}</td>
                    <td>{u.name}</td>
                    <td><span className="badge ok">{u.role}</span></td>
                    <td>{u.rfid_tag ?? "–"}</td>
                    <td>
                      {u.id !== me.id && (
                        <Button small variant="danger"
                                onClick={async () => { if (confirm(`${t("common.delete")}: ${u.email}?`)) { await del(`/api/auth/users/${u.id}`); load(); } }}>
                          {t("common.delete")}
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3 style={{ marginTop: 16 }}>+ {t("common.add")}</h3>
          <div className="grid cols-4">
            <input className="input" placeholder="E-Mail" value={newUser.email}
                   onChange={(e) => setNewUser({ ...newUser, email: e.target.value })} />
            <input className="input" placeholder="Name" value={newUser.name}
                   onChange={(e) => setNewUser({ ...newUser, name: e.target.value })} />
            <input className="input" type="password" placeholder="Passwort (min. 8)" value={newUser.password}
                   onChange={(e) => setNewUser({ ...newUser, password: e.target.value })} />
            <div className="row">
              <select className="select" value={newUser.role}
                      onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}>
                <option value="admin">admin</option>
                <option value="user">user</option>
                <option value="readonly">readonly</option>
              </select>
              <Button small variant="primary" disabled={!newUser.email || newUser.password.length < 8}
                      onClick={addUser}>+</Button>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
