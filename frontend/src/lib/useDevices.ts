/** Gespeicherte Geräte (/api/devices) – für Seiten, die Geräte-Einstellungen
 *  bearbeiten (Auto, Warmwasser, Batterie). Der Live-Snapshot kennt nur den
 *  Zustand, nicht die vollständigen Einstellungen; ein PATCH ersetzt die
 *  Einstellungen aber komplett. Deshalb wird hier immer zusammengeführt. */
import { useCallback, useEffect, useState } from "react";
import { get, patch } from "./api";
import type { Device } from "./types";
import { tr } from "../i18n";
import { toast } from "../store/toast";

export function useDevices() {
  const [devices, setDevices] = useState<Device[] | null>(null);

  const reload = useCallback(() => {
    get<Device[]>("/api/devices").then(setDevices).catch(() => setDevices([]));
  }, []);
  useEffect(reload, [reload]);

  const saveSettings = useCallback(
    async (id: number, partial: Record<string, unknown>, quiet = false): Promise<boolean> => {
      const current = devices?.find((d) => d.id === id);
      try {
        const updated = await patch<Device>(`/api/devices/${id}`, {
          settings: { ...(current?.settings ?? {}), ...partial },
        });
        setDevices((all) => (all ?? []).map((d) => (d.id === id ? updated : d)));
        if (!quiet) toast.ok(tr("common.saved"));
        return true;
      } catch (e) {
        toast.err(e instanceof Error ? e.message : String(e));
        return false;
      }
    },
    [devices],
  );

  const saveConfig = useCallback(
    async (id: number, partial: Record<string, unknown>): Promise<boolean> => {
      const current = devices?.find((d) => d.id === id);
      try {
        const updated = await patch<Device>(`/api/devices/${id}`, {
          config: { ...(current?.config ?? {}), ...partial },
        });
        setDevices((all) => (all ?? []).map((d) => (d.id === id ? updated : d)));
        toast.ok(tr("common.saved"));
        return true;
      } catch (e) {
        toast.err(e instanceof Error ? e.message : String(e));
        return false;
      }
    },
    [devices],
  );

  return { devices, reload, saveSettings, saveConfig };
}
