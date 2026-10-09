/** Umschalter, falls mehrere Geräte derselben Art eingerichtet sind. */
import type { SnapshotDevice } from "../lib/types";
import { Segment } from "./ui";

export function DevicePicker({ devices, active, onPick }: {
  devices: SnapshotDevice[]; active: number; onPick: (id: number) => void;
}) {
  if (devices.length < 2) return null;
  return (
    <Segment
      size="lg"
      value={String(active)}
      onChange={(v) => onPick(Number(v))}
      options={devices.map((d) => ({ value: String(d.id), label: d.name }))}
    />
  );
}
