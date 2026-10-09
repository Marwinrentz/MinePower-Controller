/** Geführter Einrichtungs-Assistent:
 *  Willkommen/Demo → Wechselrichter → Netzzähler → Wallbox → Batterie →
 *  Warmwasser → Prioritäten → Fertig.
 *
 *  Wichtige inhaltliche Entscheidung: Der Batterie-Schritt wird **nicht** als
 *  Pflicht dargestellt. Wer einen Hybrid-Wechselrichter hat, braucht kein
 *  separates Batteriegerät – der Wechselrichter regelt seine Batterie selbst
 *  und liefert Ladestand und Leistung bereits mit. Ein eigenes Gerät lohnt
 *  sich nur, wenn die Batterie in der Prioritätskette einsortiert oder
 *  bewusst aktiv gesteuert werden soll.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { DeviceForm } from "../components/DeviceForm";
import { PriorityChain } from "../components/PriorityChain";
import { Badge, Banner, Button, Card } from "../components/ui";
import { useI18n } from "../i18n";
import { get, post, put } from "../lib/api";
import { atLimit, useLimits } from "../lib/useLimits";
import type { Device, DeviceCategory } from "../lib/types";

type Step = { key: string; category?: DeviceCategory; optional?: boolean };

const STEPS: Step[] = [
  { key: "welcome" },
  { key: "stepInverter", category: "inverter" },
  { key: "stepMeter", category: "meter" },
  { key: "stepWallbox", category: "wallbox", optional: true },
  { key: "stepBattery", category: "battery", optional: true },
  { key: "stepWater", category: "water_heater", optional: true },
  { key: "stepPriority" },
  { key: "stepDone" },
];

export function Wizard() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [stepIndex, setStepIndex] = useState(0);
  const [devices, setDevices] = useState<Device[]>([]);
  const [order, setOrder] = useState<number[]>([]);
  const [savedInStep, setSavedInStep] = useState(false);

  const step = STEPS[stepIndex];

  const loadDevices = () => get<Device[]>("/api/devices").then(setDevices).catch(() => {});
  useEffect(() => { loadDevices(); }, [stepIndex]);
  const [gridOk, setGridOk] = useState<boolean | null>(null);
  useEffect(() => {
    if (STEPS[stepIndex].key !== "stepDone") return;
    get<{ has_grid_measurement: boolean }>("/api/system/setup-state")
      .then((s) => setGridOk(s.has_grid_measurement)).catch(() => setGridOk(null));
  }, [stepIndex]);

  const next = () => { setSavedInStep(false); setStepIndex((i) => Math.min(i + 1, STEPS.length - 1)); };
  const back = () => { setSavedInStep(false); setStepIndex((i) => Math.max(i - 1, 0)); };

  const startDemo = async () => {
    await post("/api/system/demo");
    setStepIndex(STEPS.length - 1);
  };

  const savePriority = async () => {
    // Die Kette ordnet nur Lasten; die Batterie hat ihre eigene Einstellung.
    const controllable = devices.filter((d) => ["wallbox", "water_heater"].includes(d.category));
    const ids = order.length ? order : controllable.map((d) => d.id);
    await put("/api/settings/priority", { order: ids });
    next();
  };

  const inCategory = step.category ? devices.filter((d) => d.category === step.category) : [];
  const loads = devices.filter((d) => ["wallbox", "water_heater"].includes(d.category));
  const limits = useLimits();
  const full = step.category ? atLimit(limits, devices, step.category) : false;
  const progress = Math.round((stepIndex / (STEPS.length - 1)) * 100);

  return (
    <div className="wizard-shell">
      <h1 style={{ textAlign: "center" }}>{t("wizard.title")}</h1>

      <div className="wizard-progress">
        <div className="wizard-progress-fill" style={{ width: `${progress}%` }} />
      </div>
      <div className="wizard-steps" style={{ justifyContent: "center" }}>
        {STEPS.map((s, i) => (
          <span key={s.key} className={`wizard-step ${i === stepIndex ? "current" : i < stepIndex ? "done" : ""}`}>
            {t(`wizard.${s.key === "welcome" ? "welcome" : s.key}`)}
          </span>
        ))}
      </div>

      <Card className="raised">
        {step.key === "welcome" && (
          <div className="col">
            <h2>{t("wizard.welcome")}</h2>
            <p>{t("wizard.welcomeText")}</p>
            <div className="panel">
              <p style={{ margin: "0 0 var(--s3)" }}>{t("wizard.demoText")}</p>
              <Button onClick={startDemo}>{t("wizard.demoButton")}</Button>
            </div>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <Button variant="primary" onClick={next}>{t("common.next")} →</Button>
            </div>
          </div>
        )}

        {step.category && (
          <div className="col">
            <div className="card-head">
              <h2>{t(`device.categories.${step.category}`)}</h2>
              {step.optional && <Badge>{t("common.optional")}</Badge>}
            </div>

            {step.category === "meter" && <p className="dim">{t("wizard.meterHint")}</p>}
            {step.category === "inverter" && <p className="dim">{t("wizard.inverterHint")}</p>}
            {step.category === "battery" && (
              <Banner tone="info" title={t("device.batteryPassive")}
                      action={<Button small onClick={next}>{t("wizard.batterySkip")}</Button>}>
                {t("wizard.batteryHint")}
              </Banner>
            )}

            {inCategory.map((d) => (
              <div key={d.id} className="panel row between">
                <strong style={{ color: "var(--text-strong)" }}>{d.name}</strong>
                <small>{d.driver_id}</small>
              </div>
            ))}

            {!savedInStep && !full && (
              <DeviceForm category={step.category} onSaved={() => { setSavedInStep(true); loadDevices(); }} />
            )}
            {full && !savedInStep && <p className="dim">{t("device.limitReached", { n: limits[step.category] ?? 1 })}</p>}
            {savedInStep && <Badge tone="ok">{t("common.saved")}</Badge>}

            <div className="row between">
              <Button onClick={back}>← {t("common.back")}</Button>
              <div className="row tight">
                {savedInStep && !full && <Button small onClick={() => setSavedInStep(false)}>+ {t("common.add")}</Button>}
                <Button variant="primary" onClick={next}>
                  {savedInStep || inCategory.length > 0 ? `${t("common.next")} →` : `${t("common.skip")} →`}
                </Button>
              </div>
            </div>
          </div>
        )}

        {step.key === "stepPriority" && (
          <div className="col">
            <h2>{t("settings.priority")}</h2>
            {loads.length > 1 ? (
              <>
                <p className="dim">{t("wizard.priorityText")}</p>
                <PriorityChain devices={devices} order={order} onChange={setOrder} />
              </>
            ) : (
              <p className="dim">{t("wizard.priorityNone")}</p>
            )}
            <div className="row between">
              <Button onClick={back}>← {t("common.back")}</Button>
              <Button variant="primary" onClick={savePriority}>{t("common.next")} →</Button>
            </div>
          </div>
        )}

        {step.key === "stepDone" && (
          <div className="col" style={{ textAlign: "center", alignItems: "center" }}>
            <h2>{t("wizard.stepDone")}</h2>
            {gridOk === false && <Banner tone="warn" title={t("wizard.noGridTitle")}>{t("wizard.noGridText")}</Banner>}
            <p>{t("wizard.doneText")}</p>
            <Button variant="primary" onClick={() => navigate("/")}>{t("wizard.toDashboard")} →</Button>
          </div>
        )}
      </Card>
    </div>
  );
}
