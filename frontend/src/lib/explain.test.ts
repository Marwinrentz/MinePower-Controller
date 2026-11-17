import { describe, expect, it } from "vitest";
import { tr } from "../i18n";
import { carState, explain, shortError } from "./explain";
import type { Snapshot, SnapshotDevice } from "./types";

function snapshot(over: Partial<Snapshot> = {}): Snapshot {
  return {
    time: "2026-05-01T12:00:00Z", pv_power: 0, grid_power: 0, house_power: 300, wallbox_power: 0, water_power: 0,
    battery: null, surplus: 0, price_ct: null, price_cheap: null, safety: null, paused: false, waste_w: 0,
    waste_reason: null, gap_filled_w: 0, deadband_w: 100, weather: null, devices: [], ...over,
  };
}

function device(over: Partial<SnapshotDevice>): SnapshotDevice {
  return { id: 1, name: "Gerät", category: "wallbox", driver_id: "sim_wallbox", enabled: true, online: true,
           last_error: null, command_error: null, decision: {}, ...over };
}

describe("Überschrift der Übersicht", () => {
  it("nennt Pause und Sicherheitsstopp vor allem anderen", () => {
    expect(explain(snapshot({ paused: true, wallbox_power: 7000 }), tr).tone).toBe("warn");
    expect(explain(snapshot({ safety: "Netzmessung ausgefallen", wallbox_power: 7000 }), tr).tone).toBe("err");
  });

  it("unterscheidet Laden aus Sonne und aus dem Netz", () => {
    const solar = explain(snapshot({ wallbox_power: 3600, pv_power: 5000, grid_power: -200 }), tr);
    const grid = explain(snapshot({ wallbox_power: 3600, pv_power: 0, grid_power: 3900 }), tr);
    expect(solar.tone).toBe("car");
    expect(grid.tone).toBe("import");
    expect(solar.headline).not.toBe(grid.headline);
  });

  it("kommt ohne Netzmessung zurecht", () => {
    expect(() => explain(snapshot({ grid_power: null }), tr)).not.toThrow();
  });
});

describe("Zustand des Fahrzeugs", () => {
  it("meldet ein fehlendes Gerät nicht als Fehler", () => {
    expect(() => carState(undefined)).not.toThrow();
  });

  it("erkennt Laden am Gerätezustand", () => {
    const d = device({ data: { state: "charging", power: 3600 }, presence: "charging" });
    expect(carState(d)).toBe("charging");
  });
});

describe("Fehlermeldungen kürzen", () => {
  it("lässt den Klassennamen der Ausnahme weg", () => {
    expect(shortError("ConnectionProblem: Gerät 192.0.2.1 antwortet nicht")).toBe("Gerät 192.0.2.1 antwortet nicht");
    expect(shortError(null)).toBe("");
  });
});
