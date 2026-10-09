/** Höchstzahl aktiver Geräte je Klasse (Backend: core/limits.py). */
import { useEffect, useState } from "react";
import { get } from "./api";
import type { Device, DeviceCategory } from "./types";

let cache: Partial<Record<DeviceCategory, number>> | null = null;

export function useLimits(): Partial<Record<DeviceCategory, number>> {
  const [limits, setLimits] = useState(cache ?? {});
  useEffect(() => {
    if (cache) return;
    get<Partial<Record<DeviceCategory, number>>>("/api/devices/limits")
      .then((l) => { cache = l; setLimits(l); })
      .catch(() => {});
  }, []);
  return limits;
}

/** Ist die Klasse voll (kein weiteres aktives Gerät erlaubt)? */
export function atLimit(limits: Partial<Record<DeviceCategory, number>>, devices: Device[] | null | undefined,
                        category: DeviceCategory): boolean {
  const max = limits[category];
  if (max == null) return false;
  return (devices ?? []).filter((d) => d.category === category && d.enabled).length >= max;
}
