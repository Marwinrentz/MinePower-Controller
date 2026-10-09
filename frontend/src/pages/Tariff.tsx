/** Tarif – Strompreis, Preisgrenze und wer davon Gebrauch macht.
 *
 *  Eine Grenze für alles: Batterie, Auto und Warmwasser laden aus dem Netz,
 *  wenn der Endpreis darunter liegt. Ein Gerät kann eine eigene Grenze haben;
 *  dann steht das hier ausdrücklich, mit einem Knopf zurück zur gemeinsamen.
 *  (Im Diagnosebericht hatte das Warmwasser 15 ct, die Tarif-Grenze 24 ct –
 *  die 15 ct sahen wirksam aus, waren es aber nicht.)
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { PriceCurve } from "../components/charts";
import { Icon } from "../components/icons";
import { SaveBar } from "../components/SaveBar";
import {
  ActionButton, Badge, Callout, Card, CardHead, Field, NumberInput, PageHead, Segment, Slider, ToggleRow,
} from "../components/ui";
import { useI18n } from "../i18n";
import { get } from "../lib/api";
import { fmtClock, fmtCt } from "../lib/format";
import type { PriceSlot } from "../lib/types";
import { useDevices } from "../lib/useDevices";
import { useLive } from "../store/live";
import { useSection, useSettings } from "../store/settings";

type TariffInfo = { provider: string; current_ct: number | null; cheap_limit_ct: number; prices: PriceSlot[] };

function nextWindow(prices: PriceSlot[], limit: number) {
  const end = (p: PriceSlot) => new Date(new Date(p.start).getTime() + p.minutes * 60000);
  let i = prices.findIndex((p) => p.price_ct <= limit);
  if (i < 0) return null;
  const start = prices[i];
  let j = i;
  let min = start.price_ct;
  while (j + 1 < prices.length && prices[j + 1].price_ct <= limit) {
    j += 1;
    min = Math.min(min, prices[j].price_ct);
  }
  return { now: i === 0, from: new Date(start.start), to: end(prices[j]), min };
}

export function Tariff() {
  const { t } = useI18n();
  const snapshot = useLive((s) => s.snapshot);
  const loaded = useSettings((s) => s.loaded);
  const load = useSettings((s) => s.load);
  const tariff = useSection("tariff");
  const reg = useSection("regulation");
  const { devices, saveSettings } = useDevices();
  const [info, setInfo] = useState<TariffInfo | null>(null);

  useEffect(() => { if (!loaded) void load(); }, [loaded, load]);
  useEffect(() => {
    let alive = true;
    const fetchPrices = () => get<TariffInfo>("/api/statistics/tariff").then((r) => alive && setInfo(r)).catch(() => {});
    fetchPrices();
    const timer = window.setInterval(fetchPrices, 300000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  const provider = tariff.str("provider", "none");
  const limit = tariff.num("grid_charge_limit_ct", tariff.num("cheap_limit_ct", 15));
  const prices = info?.prices ?? [];
  const now = snapshot?.price_ct ?? info?.current_ct ?? null;
  const cheapNow = now != null && now <= limit;
  const win = useMemo(() => nextWindow(prices, limit), [prices, limit]);

  const surcharge = tariff.num("grid_fees_ct", 0) + tariff.num("levies_ct", 0) + tariff.num("supplier_margin_ct", 0);
  const vat = tariff.num("vat_pct", 0);
  const exampleSpot = 8;
  const exampleTotal = (exampleSpot + surcharge) * (1 + vat / 100);

  const users = (devices ?? []).filter((d) => d.category === "wallbox" || d.category === "water_heater");
  const gridCharge = reg.bool("battery_grid_charge_enabled", false);
  const gridSoc = reg.num("battery_grid_charge_soc", 50);

  return (
    <div className="page">
      <PageHead title={t("tariff.title")} sub={t("tariff.sub")} />

      <Card className="price-hero">
        <div className="price-now">
          <span className="price-now-label">{t("tariff.now")}</span>
          {now != null
            ? <span className={`price-now-value num ${cheapNow ? "t-pv" : ""}`}>{fmtCt(now)}</span>
            : <span className="price-now-none">{t("tariff.noPriceNow")}</span>}
          {now != null && <Badge tone={cheapNow ? "ok" : "warn"}>{cheapNow ? t("tariff.cheap") : t("tariff.expensive")}</Badge>}
        </div>
        <div className="price-now-side">
          <span className="price-limit-chip">{t("tariff.limit", { limit: fmtCt(limit) })}</span>
          <p className="price-next">
            {provider === "none" || prices.length === 0 ? t("tariff.noPrices")
              : win?.now ? t("tariff.cheapNow", { to: fmtClock(win.to) })
              : win ? t("tariff.nextCheap", { from: fmtClock(win.from), to: fmtClock(win.to), price: fmtCt(win.min) })
              : t("tariff.noCheap", { limit: fmtCt(limit) })}
          </p>
        </div>
      </Card>

      {prices.length > 0 && (
        <Card>
          <CardHead title={t("tariff.curve")} sub={t("tariff.curveHelp")} />
          <PriceCurve prices={prices} limit={limit} />
        </Card>
      )}

      <Card>
        <CardHead title={t("tariff.settings")} />
        <Field label={t("tariff.provider")}>
          <Segment size="lg" value={provider} onChange={(v) => tariff.set("provider", v)}
                   options={[
                     { value: "none", label: t("tariff.providerNone") },
                     { value: "tibber", label: "Tibber" },
                     { value: "awattar", label: "aWATTar" },
                   ]} />
        </Field>

        {provider === "tibber" && (
          <>
            <Field label={t("tariff.tibberToken")} help={t("tariff.tibberHelp")}>
              <input className="input" type="password" autoComplete="off" value={tariff.str("tibber_token", "")}
                     onChange={(e) => tariff.set("tibber_token", e.target.value)} />
            </Field>
            <Callout tone="ok">{t("tariff.tibberNoSurcharge")}</Callout>
          </>
        )}

        {provider === "awattar" && (
          <>
            <Field label={t("tariff.region")}>
              <Segment size="lg" value={tariff.str("awattar_region", "de")} onChange={(v) => tariff.set("awattar_region", v)}
                       options={[{ value: "de", label: t("tariff.regionDe") }, { value: "at", label: t("tariff.regionAt") }]} />
            </Field>
            <h3 className="group-title">{t("tariff.surcharges")}</h3>
            <p className="field-help">{t("tariff.surchargesHelp")}</p>
            {surcharge === 0 && vat === 0 && <Callout tone="warn">{t("tariff.surchargesZero")}</Callout>}
            <div className="form-grid">
              <Field label={t("tariff.gridFees")}>
                <NumberInput value={tariff.num("grid_fees_ct", 0)} onChange={(v) => tariff.set("grid_fees_ct", v ?? 0)} step={0.1} unit="ct/kWh" />
              </Field>
              <Field label={t("tariff.levies")}>
                <NumberInput value={tariff.num("levies_ct", 0)} onChange={(v) => tariff.set("levies_ct", v ?? 0)} step={0.1} unit="ct/kWh" />
              </Field>
              <Field label={t("tariff.margin")}>
                <NumberInput value={tariff.num("supplier_margin_ct", 0)} onChange={(v) => tariff.set("supplier_margin_ct", v ?? 0)} step={0.1} unit="ct/kWh" />
              </Field>
              <Field label={t("tariff.vat")}>
                <NumberInput value={tariff.num("vat_pct", 0)} onChange={(v) => tariff.set("vat_pct", v ?? 0)} step={1} unit="%" />
              </Field>
            </div>
            <p className="example">{t("tariff.example", { spot: fmtCt(exampleSpot), total: fmtCt(exampleTotal) })}</p>
          </>
        )}

        <Slider
          label={t("tariff.threshold")}
          help={t("tariff.thresholdHelp")}
          value={limit} min={0} max={50} step={0.5} tone="pv"
          format={(v) => fmtCt(v)}
          onChange={(v) => tariff.set("grid_charge_limit_ct", v)}
          marks={[{ value: 0, label: "0" }, { value: 25, label: "25" }, { value: 50, label: "50 ct" }]}
        />
        <ToggleRow label={t("tariff.negative")} help={t("tariff.negativeHelp")}
                   on={tariff.bool("charge_on_negative", true)} onChange={(v) => tariff.set("charge_on_negative", v)} />
      </Card>

      <Card>
        <CardHead title={t("tariff.users")} sub={t("tariff.usersHelp")} />
        <ul className="user-list">
          <li className="user-row">
            <Icon name="battery" size={20} />
            <div className="user-text">
              <span className="user-name">{t("bat.title")}</span>
              <span className="user-state">
                {!gridCharge ? t("tariff.batteryOff") : t("tariff.batteryOn", { soc: gridSoc })}
              </span>
            </div>
            <Link to="/einstellungen?tab=battery" className="btn ghost btn-sm">{t("tariff.toBatterySettings")}</Link>
          </li>
          {users.map((d) => {
            const s = d.settings ?? {};
            const mode = String(s.mode ?? "pv_only");
            const inPrice = mode === "pv_price" || mode === "price";
            const modeLabel = t(d.category === "wallbox" ? `modes.car.${mode}` : `modes.water.${mode}`);
            return (
              <li key={d.id} className="user-row">
                <Icon name={d.category === "wallbox" ? "car" : "water"} size={20} />
                <div className="user-text">
                  <span className="user-name">{d.name}</span>
                  <span className="user-state">
                    {!inPrice ? t("tariff.notInPriceMode", { mode: modeLabel })
                      : t("tariff.usesGlobal")}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      </Card>

      <SaveBar />
    </div>
  );
}
