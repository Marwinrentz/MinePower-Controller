/** Geräteverwaltung: Live-Zustand, Hinzufügen/Bearbeiten/Löschen.
 *
 *  Verwendet bewusst dieselbe Kachel wie das Dashboard – zwei getrennte
 *  Gerätedarstellungen wären genau der Stilbruch, den es zu vermeiden gilt.
 */
import { useCallback, useEffect, useState } from "react";
import { DeviceForm } from "../components/DeviceForm";
import { DeviceTile } from "../components/DeviceTile";
import { Button, Card, Empty, Toggle } from "../components/ui";
import { useI18n } from "../i18n";
import { del, get, patch } from "../lib/api";
import type { Device, DeviceCategory, SnapshotDevice } from "../lib/types";
import { useLive } from "../store/live";
import { Icon } from "../components/icons";

const CATEGORIES: DeviceCategory[] = ["inverter", "meter", "wallbox", "water_heater", "battery"];

/** Gruppen der Geräteseite. `wide` gibt steuerbaren Lasten mehr Breite, weil
 *  ihre Karten Modusleiste, Eingriffstasten und Regler tragen — in einer
 *  Drittelspalte bricht die Modusleiste sonst auf zwei Zeilen um. */
const GROUPS: { key: string; categories: DeviceCategory[]; wide?: boolean }[] = [
  { key: "measure", categories: ["inverter", "meter"] },
  { key: "control", categories: ["wallbox", "water_heater"], wide: true },
  { key: "storage", categories: ["battery"] },
];

/** Ersatzkachel, solange der Regelkreis ein Gerät noch nicht kennt
 *  (frisch angelegt oder deaktiviert). */
function placeholder(device: Device): SnapshotDevice {
  return {
    id: device.id,
    name: device.name,
    category: device.category,
    driver_id: device.driver_id,
    enabled: device.enabled,
    online: false,
    last_error: device.last_error,
    command_error: null,
    decision: {},
  };
}

export function Devices() {
  const { t } = useI18n();
  const snapshot = useLive((s) => s.snapshot);
  const [devices, setDevices] = useState<Device[]>([]);
  const [adding, setAdding] = useState<DeviceCategory | null>(null);
  const [editing, setEditing] = useState<Device | null>(null);

  const load = useCallback(() => {
    get<Device[]>("/api/devices").then(setDevices).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const liveById = new Map<number, SnapshotDevice>(
    (snapshot?.devices ?? []).map((d) => [d.id, d] as [number, SnapshotDevice]),
  );

  const toggleEnabled = async (device: Device) => {
    await patch(`/api/devices/${device.id}`, { enabled: !device.enabled });
    load();
  };

  const remove = async (device: Device) => {
    if (!confirm(`${t("common.delete")}: ${device.name}?`)) return;
    await del(`/api/devices/${device.id}`);
    load();
  };

  const closeForm = () => { setAdding(null); setEditing(null); };

  return (
    <div className="page">
      <header className="page-head">
        <h1 className="page-title">{t("nav.devices")}</h1>
        <div className="row wrap tight">
          {CATEGORIES.map((c) => (
            <Button key={c} small onClick={() => { setAdding(c); setEditing(null); }}>
              + {t(`device.categories.${c}`)}
            </Button>
          ))}
        </div>
      </header>

      {(adding || editing) && (
        <Card className="raised">
          <div className="card-head">
            <h2>
              {editing
                ? `${t("common.edit")}: ${editing.name}`
                : `${t("device.addTitle")}: ${t(`device.categories.${adding}`)}`}
            </h2>
            <Button small variant="ghost" onClick={closeForm} title={t("common.close")}><Icon name="close" size={14} /></Button>
          </div>
          <DeviceForm
            category={editing ? editing.category : adding!}
            existing={editing ?? undefined}
            onSaved={() => { closeForm(); load(); }}
            onCancel={closeForm}
          />
        </Card>
      )}

      {/* Nach Rolle gruppiert statt in ein gemeinsames Raster geworfen.
          Vorher standen Messgeräte und steuerbare Lasten in denselben
          Rasterzeilen: Eine hohe Wallbox-Karte mit Modusleiste und
          Ladegrenze bestimmte die Zeilenhöhe, und neben den flachen
          Zählerkarten klaffte ein Loch, in das die nächste Karte erst weit
          unten rutschte. Innerhalb einer Gruppe sind die Karten gleich
          aufgebaut, damit tritt das nicht mehr auf — und die Seite erklärt
          nebenbei, was misst und was gesteuert wird. */}
      {GROUPS.map((group) => {
        const rows = devices.filter((d) => group.categories.includes(d.category));
        if (rows.length === 0) return null;
        return (
          <section key={group.key} className="dev-group">
            <div className="dev-group-head">
              <h2>{t(`devices.group.${group.key}`)}</h2>
              <span className="dim">{t(`devices.group.${group.key}Sub`)}</span>
            </div>
            <div className={`grid ${group.wide ? "cols-2" : "cols-3"}`}>
              {rows.map((device, i) => (
                <div key={device.id} className="col list-enter"
                     style={{ gap: "var(--s2)", animationDelay: `${i * 35}ms` }}>
                  <DeviceTile device={liveById.get(device.id) ?? placeholder(device)} />
                  <div className="row wrap tight" style={{ paddingLeft: "var(--s1)" }}>
                    <Toggle on={device.enabled} onChange={() => toggleEnabled(device)} label={t("common.enabled")} />
                    <div className="spacer" />
                    <Button small onClick={() => { setEditing(device); setAdding(null); }}>{t("common.edit")}</Button>
                    <Button small variant="danger" onClick={() => remove(device)}>{t("common.delete")}</Button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        );
      })}

      {devices.length === 0 && !adding && (
        <Card>
          <Empty icon={<Icon name="devices" size={30} />} title={t("dash.noDevices")}>{t("wizard.welcomeText")}</Empty>
        </Card>
      )}
    </div>
  );
}
