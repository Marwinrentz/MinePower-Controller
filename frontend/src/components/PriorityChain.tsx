/** Prioritätskette – wer bekommt den Überschuss zuerst?
 *
 *  Sortierbar per Drag & Drop *und* über Pfeil-Schaltflächen. Letztere sind
 *  kein Fallback für Puristen: Auf dem Handy ist Drag & Drop in einer
 *  scrollenden Liste unzuverlässig, und mit Tastatur ist es gar nicht
 *  bedienbar.
 */
import { useState } from "react";
import { useI18n } from "../i18n";
import type { Device } from "../lib/types";
import { Icon, CATEGORY_ICON } from "./icons";


export function PriorityChain({ devices, order, onChange }: {
  devices: Device[];
  order: number[];
  onChange: (order: number[]) => void;
}) {
  const { t } = useI18n();
  const [dragId, setDragId] = useState<number | null>(null);
  const [overId, setOverId] = useState<number | null>(null);

  // Seit 2.16 nur Lasten: Der Platz der Batterie ist 'Batterie zuerst bis …"
  const controllable = devices.filter((d) => ["wallbox", "water_heater"].includes(d.category));
  const sorted = [
    ...order.map((id) => controllable.find((d) => d.id === id)).filter(Boolean) as Device[],
    ...controllable.filter((d) => !order.includes(d.id)),
  ];

  const moveTo = (targetId: number, sourceId: number) => {
    if (sourceId === targetId) return;
    const ids = sorted.map((d) => d.id);
    const from = ids.indexOf(sourceId);
    const to = ids.indexOf(targetId);
    if (from < 0 || to < 0) return;
    ids.splice(from, 1);
    ids.splice(to, 0, sourceId);
    onChange(ids);
  };

  const shift = (index: number, delta: number) => {
    const ids = sorted.map((d) => d.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    onChange(ids);
  };

  if (sorted.length === 0) {
    return <p className="dim">{t("wizard.priorityText")}</p>;
  }

  return (
    <div className="col" style={{ gap: "var(--s2)" }}>
      {sorted.map((d, i) => (
        <div
          key={d.id}
          className={`prio-item ${dragId === d.id ? "dragging" : ""} ${overId === d.id ? "drop-target" : ""}`}
          draggable
          onDragStart={() => setDragId(d.id)}
          onDragEnd={() => { setDragId(null); setOverId(null); }}
          onDragOver={(e) => { e.preventDefault(); setOverId(d.id); }}
          onDragLeave={() => setOverId((cur) => (cur === d.id ? null : cur))}
          onDrop={(e) => {
            e.preventDefault();
            if (dragId !== null) moveTo(d.id, dragId);
            setDragId(null);
            setOverId(null);
          }}
        >
          <span className="prio-grip" aria-hidden="true">⠿</span>
          <span className="prio-rank">{i + 1}</span>
          <Icon name={CATEGORY_ICON[d.category] ?? "settings"} size={17} />
          <div className="col" style={{ gap: 0, minWidth: 0 }}>
            <strong style={{ color: "var(--text-strong)" }}>{d.name}</strong>
            <small>{t(`device.categories.${d.category}`)}</small>
          </div>
          <div className="spacer" />
          <div className="prio-move">
            <button onClick={() => shift(i, -1)} disabled={i === 0}
                    title={t("common.back")} aria-label={`${d.name} ${t("common.back")}`}>&#9650;</button>
            <button onClick={() => shift(i, 1)} disabled={i === sorted.length - 1}
                    title={t("common.next")} aria-label={`${d.name} ${t("common.next")}`}>&#9660;</button>
          </div>
        </div>
      ))}
    </div>
  );
}
