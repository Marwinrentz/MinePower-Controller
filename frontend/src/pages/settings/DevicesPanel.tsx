/** Einstellungen → Geräte: alles, was MinePower misst oder steuert. */
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { DeviceForm } from "../../components/DeviceForm";
import { atLimit, useLimits } from "../../lib/useLimits";
import { Icon, CATEGORY_ICON } from "../../components/icons";
import { ActionButton, Badge, Button, Card, CardHead, Empty, StatusDot, Toggle } from "../../components/ui";
import { useI18n } from "../../i18n";
import { del, patch } from "../../lib/api";
import { fmtAgo } from "../../lib/format";
import type { Device, DeviceCategory } from "../../lib/types";
import { shortError } from "../../lib/explain";
import { useDevices } from "../../lib/useDevices";
import { useLive } from "../../store/live";
import { toast } from "../../store/toast";

const CATEGORIES: DeviceCategory[] = ["inverter", "meter", "battery", "wallbox", "water_heater"];

export function DevicesPanel() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { devices, reload } = useDevices();
  const snapshot = useLive((s) => s.snapshot);
  const [adding, setAdding] = useState<DeviceCategory | null>(null);
  const limits = useLimits();
  const [editing, setEditing] = useState<Device | null>(null);
  const live = new Map((snapshot?.devices ?? []).map((d) => [d.id, d]));
  // Direkt aus einer Meldung ('Details") zur Einrichtung eines Geräts springen
  const [params, setParams] = useSearchParams();
  useEffect(() => {
    const id = Number(params.get("edit"));
    if (!id || !devices) return;
    const dev = devices.find((d) => d.id === id);
    if (dev) {
      setEditing(dev);
      setAdding(null);
    }
    params.delete("edit");
    setParams(params, { replace: true });
  }, [params, devices, setParams]);

  const toggleEnabled = async (device: Device) => {
    try {
      await patch(`/api/devices/${device.id}`, { enabled: !device.enabled });
      reload();
    } catch (e) {
      toast.err(e instanceof Error ? e.message : String(e));
    }
  };

  const remove = async (device: Device) => {
    if (!confirm(`${t("common.delete")}: ${device.name}?`)) return;
    try {
      await del(`/api/devices/${device.id}`);
      toast.ok(`${device.name}: ${t("common.delete")}`);
      reload();
    } catch (e) {
      toast.err(e instanceof Error ? e.message : String(e));
    }
  };

  const close = () => { setAdding(null); setEditing(null); };

  return (
    <>
      <Card>
        <CardHead title={t("set.sec.devices")} sub={t("set.devices.help")}>
          <Button variant="ghost" onClick={() => navigate("/wizard")}><Icon name="flask" size={16} /> {t("set.devices.wizard")}</Button>
        </CardHead>
        <div className="chip-row">
          {CATEGORIES.map((c) => {
            const full = atLimit(limits, devices, c);
            return (
              <Button key={c} size="sm" disabled={full} title={full ? t("device.limitReached", { n: limits[c] ?? 1 }) : undefined}
                      onClick={() => { setAdding(c); setEditing(null); }}>
                <Icon name={CATEGORY_ICON[c]} size={16} /> + {t(`device.categories.${c}`)}
              </Button>
            );
          })}
        </div>
        {CATEGORIES.some((c) => atLimit(limits, devices, c)) && (
          <p className="field-help">{t("device.limitHint", {
            list: CATEGORIES.filter((c) => atLimit(limits, devices, c)).map((c) => t(`device.categories.${c}`)).join(", "),
          })}</p>
        )}
      </Card>

      {(adding || editing) && (
        <Card className="raised">
          <CardHead title={editing ? `${t("common.edit")}: ${editing.name}` : `${t("device.addTitle")}: ${t(`device.categories.${adding}`)}`}>
            <Button icon variant="ghost" onClick={close} title={t("common.close")}><Icon name="close" size={18} /></Button>
          </CardHead>
          <DeviceForm
            category={editing ? editing.category : adding!}
            existing={editing ?? undefined}
            onSaved={() => { close(); reload(); toast.ok(t("common.saved")); }}
            onCancel={close}
          />
        </Card>
      )}

      {devices === null ? <div className="skeleton" style={{ height: 200 }} />
        : devices.length === 0 ? (
          <Card><Empty icon={<Icon name="devices" size={30} />} title={t("home.noDevices")}>{t("home.noDevicesText")}</Empty></Card>
        ) : (
          <ul className="device-list">
            {devices.map((d) => {
              const l = live.get(d.id);
              const online = l?.online ?? false;
              return (
                <li key={d.id} className={`card device-row ${d.enabled ? "" : "disabled"}`}>
                  <span className="device-icon"><Icon name={CATEGORY_ICON[d.category] ?? "settings"} size={22} /></span>
                  <div className="device-main">
                    <div className="device-name">
                      <StatusDot state={!d.enabled ? "offline" : online ? "online" : l?.last_error ? "error" : "offline"} />
                      <strong>{d.name}</strong>
                      {d.category === "battery" && d.config?.allow_active_control === true && <Badge tone="accent">{t("set.battery.control")}</Badge>}
                    </div>
                    <span className="device-sub">
                      {t(`device.categories.${d.category}`)} · {d.driver_id}
                      {l?.last_seen ? ` · ${fmtAgo(l.last_seen)}` : ""}
                    </span>
                    {l?.last_error && <span className="device-err" title={l.last_error}>{shortError(l.last_error)}</span>}
                  </div>
                  <div className="device-actions">
                    <Toggle on={d.enabled} onChange={() => void toggleEnabled(d)} label={t("common.enabled")} />
                    <Button size="sm" onClick={() => { setEditing(d); setAdding(null); window.scrollTo({ top: 0, behavior: "smooth" }); }}>
                      {t("common.edit")}
                    </Button>
                    <ActionButton size="sm" variant="danger" onClick={() => remove(d)}>{t("common.delete")}</ActionButton>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
    </>
  );
}
