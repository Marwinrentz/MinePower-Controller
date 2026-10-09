/** Dynamisches Geräteformular: Treiberwahl → Felder aus DriverMeta →
 *  'Verbindung testen' mit Live-Readback → Speichern.
 *
 *  Der Verbindungstest ist hier bewusst prominent: Er ist die einzige Stelle,
 *  an der sich vor dem Produktivbetrieb prüfen lässt, ob eine Registerkarte
 *  zum Firmware-Stand passt. Er zeigt deshalb alle relevanten Live-Werte in
 *  Klartext, Hinweise zum Reifegrad des Treibers – und auf Wunsch einen
 *  unkritischen Steuerbefehl samt Readback, damit 'liest' nicht mit
 *  'steuert' verwechselt wird.
 */
import { Icon } from "./icons";
import { useEffect, useMemo, useState } from "react";
import { useI18n } from "../i18n";
import { get, patch, post } from "../lib/api";
import type { ConfigField, Device, DeviceCategory, DriverMeta, TestResult } from "../lib/types";
import { Badge, Banner, Button, Field, Toggle } from "./ui";

function FieldInput({ field, value, onChange }: {
  field: ConfigField; value: unknown; onChange: (v: unknown) => void;
}) {
  if (field.type === "boolean") {
    return <Toggle on={value === true} onChange={onChange} />;
  }
  if (field.type === "select" && field.options) {
    return (
      <select className="select" value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
        {field.options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    );
  }
  return (
    <input
      className="input"
      type={field.type === "password" ? "password" : field.type === "number" ? "number" : "text"}
      step="any"
      value={String(value ?? "")}
      placeholder={field.placeholder ?? ""}
      onChange={(e) => onChange(
        field.type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value,
      )}
    />
  );
}

function MaturityBadge({ driver }: { driver: DriverMeta }) {
  const { t } = useI18n();
  if (driver.maturity === "stable") return null;
  const beta = driver.maturity === "beta";
  return (
    <Badge tone={beta ? "warn" : "err"}>
      {beta ? t("device.maturityBeta") : t("device.maturityExperimental")}
    </Badge>
  );
}

export function DeviceForm({ category, existing, onSaved, onCancel }: {
  category: DeviceCategory;
  existing?: Device;
  onSaved: (device: Device) => void;
  onCancel?: () => void;
}) {
  const { t } = useI18n();
  const [drivers, setDrivers] = useState<DriverMeta[]>([]);
  const [driverId, setDriverId] = useState(existing?.driver_id ?? "");
  const [name, setName] = useState(existing?.name ?? "");
  const [config, setConfig] = useState<Record<string, unknown>>(existing?.config ?? {});
  const [settings, setSettings] = useState<Record<string, unknown>>(existing?.settings ?? {});
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    get<DriverMeta[]>(`/api/drivers?category=${category}`).then(setDrivers).catch(() => {});
  }, [category]);

  const driver = useMemo(() => drivers.find((d) => d.id === driverId), [drivers, driverId]);
  const controllable = category === "wallbox" || category === "water_heater";

  useEffect(() => {
    if (!driver || existing) return;
    // Defaults des Treibers vorbelegen
    const defaults: Record<string, unknown> = {};
    for (const f of driver.fields) if (f.default !== null && f.default !== undefined) defaults[f.key] = f.default;
    setConfig(defaults);
    if (!name) setName(driver.name);
    setTestResult(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [driverId, drivers]);

  const runTest = async (includeWrite: boolean) => {
    if (!driverId) return;
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await post<TestResult>("/api/devices/test", {
        driver_id: driverId, config, include_write: includeWrite, device_id: existing?.id ?? null,
      }));
    } catch (e) {
      setTestResult({
        ok: false, message: String((e as Error).message), values: {}, warnings: [], write_test: null,
      });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const device = existing
        ? await patch<Device>(`/api/devices/${existing.id}`, {
            name, config, settings: { ...existing.settings, ...settings },
          })
        : await post<Device>("/api/devices", {
            name, category, driver_id: driverId, config,
            settings: { ...defaultSettings(category), ...settings },
          });
      onSaved(device);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="col">
      <Field label={t("device.driver")}>
        <select className="select" value={driverId} disabled={!!existing}
                onChange={(e) => setDriverId(e.target.value)}>
          <option value="">–</option>
          {drivers.map((d) => (
            <option key={d.id} value={d.id}>{d.name}</option>
          ))}
        </select>
      </Field>

      {driver && (
        <>
          <div className="row wrap tight">
            <MaturityBadge driver={driver} />
            <small>{driver.description}</small>
          </div>

          {driver.maturity !== "stable" && (
            <Banner tone={driver.maturity === "beta" ? "warn" : "err"}
                    title={driver.maturity === "beta"
                      ? t("device.maturityBeta") : t("device.maturityExperimental")}>
              {driver.maturity === "beta" ? t("device.maturityBetaHelp") : t("device.maturityExperimentalHelp")}
              {driver.notes ? ` ${driver.notes}` : ""}
            </Banner>
          )}

          {driver.maturity === "stable" && driver.notes && (
            <details className="device-notes">
              <summary><Icon name="info" size={16} /> {t("device.notes")}</summary>
              {driver.notes.split("\n\n").map((para, i) => <p key={i}>{para}</p>)}
            </details>
          )}

          <Field label={t("device.name")}>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>

          {driver.fields.map((f) => (
            <Field key={f.key} label={f.required ? f.label : `${f.label} (${t("common.optional")})`} help={f.help}>
              <FieldInput field={f} value={config[f.key]} onChange={(v) => setConfig({ ...config, [f.key]: v })} />
            </Field>
          ))}

          {category === "wallbox" && (
            <Field label={t("device.gridMetered")} help={t("device.gridMeteredHelp")}>
              <Toggle
                on={settings.grid_metered !== false}
                onChange={(v) => setSettings({ ...settings, grid_metered: v })}
              />
            </Field>
          )}

          <div className="row wrap">
            <Button onClick={() => runTest(false)} disabled={testing}>
              {testing ? t("common.testing") : t("common.test")}
            </Button>
            {controllable && (
              <Button onClick={() => runTest(true)} disabled={testing} title={t("device.writeTestHelp")}>
                {t("common.testWithWrite")}
              </Button>
            )}
            <div className="spacer" />
            {onCancel && <Button onClick={onCancel}>{t("common.cancel")}</Button>}
            <Button variant="primary" onClick={save} disabled={saving || !name}>{t("common.save")}</Button>
          </div>

          {testResult && <TestReport result={testResult} />}
          {error && <Banner tone="err" title={t("common.error")}>{error}</Banner>}
        </>
      )}
    </div>
  );
}

function TestReport({ result }: { result: TestResult }) {
  const { t } = useI18n();
  const values = Object.entries(result.values);
  return (
    <div className="col" style={{ gap: "var(--s2)" }}>
      <Banner tone={result.ok ? "ok" : "err"} title={result.message} />

      {values.length > 0 && (
        <div className="panel">
          <span className="section-label">{t("device.liveValues")}</span>
          <div className="grid cols-2" style={{ gap: "var(--s1) var(--s4)", marginTop: "var(--s2)" }}>
            {values.map(([k, v]) => (
              <div key={k} className="row between" style={{ gap: "var(--s2)" }}>
                <span className="dim">{k}</span>
                <strong className="num" style={{ color: "var(--text-strong)", textAlign: "right" }}>
                  {v === null || v === undefined ? "–" : String(v)}
                </strong>
              </div>
            ))}
          </div>
        </div>
      )}

      {result.write_test && (
        <Banner tone={result.write_test.ok ? "ok" : "err"}
                title={`${t("device.writeTest")}: ${
                  result.write_test.ok ? t("device.writeTestOk") : t("device.writeTestFailed")}`}>
          {result.write_test.message}
        </Banner>
      )}

      {(result.warnings ?? []).map((w, i) => (
        <Banner key={i} tone="info" title={t("common.details")}>{w}</Banner>
      ))}
    </div>
  );
}

/** Sinnvolle Regelungs-Defaults je Kategorie beim Anlegen. */
function defaultSettings(category: DeviceCategory): Record<string, unknown> {
  switch (category) {
    case "wallbox":
      return { mode: "pv_only", min_current: 6, max_current: 16, phases_mode: "fixed1",
               start_threshold_w: 1400, start_delay_s: 60, stop_delay_s: 180, grid_metered: true };
    case "water_heater":
      return { mode: "pv_only", max_power_w: 3000, target_temp_c: 60, modulating: true,
               price_limit_ct: 15, boost_end_mode: "both", boost_duration_min: 60, boost_temp_c: 65 };
    case "battery":
      // Bewusst ohne manage_reserve: Hybrid-Wechselrichter regeln ihre
      // Batterie selbst; aktive Steuerung bleibt eine bewusste Entscheidung.
      // Kein price_window_mode mehr: Der Wert war nirgends editierbar und
      // überstimmte die globale Einstellung 'Strompreis & Optimierung".
      return { reserve_soc: 20, manage_reserve: false };
    default:
      return {};
  }
}
