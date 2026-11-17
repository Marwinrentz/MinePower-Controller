import { describe, expect, it } from "vitest";
import { fmtCt, fmtKwh, fmtPct, fmtTemp, fmtW, setFormatLocale } from "./format";

describe("Werte formatieren", () => {
  it("zeigt fehlende Werte als Strich, nie als 0", () => {
    for (const fmt of [fmtW, fmtKwh, fmtCt, fmtPct, fmtTemp]) {
      expect(fmt(null)).toBe("–");
      expect(fmt(undefined)).toBe("–");
      expect(fmt(Number.NaN)).toBe("–");
    }
  });

  it("wechselt bei der Leistung ab 1 kW die Einheit", () => {
    setFormatLocale("de");
    expect(fmtW(0)).toBe("0 W");
    expect(fmtW(999.4)).toBe("999 W");
    expect(fmtW(1000)).toBe("1,00 kW");
    expect(fmtW(-2500)).toBe("-2,50 kW");
    expect(fmtW(11040)).toBe("11,0 kW");
  });

  it("richtet sich nach der Sprache", () => {
    setFormatLocale("en");
    expect(fmtW(1500)).toBe("1.50 kW");
    expect(fmtKwh(12.34)).toBe("12.3 kWh");
    setFormatLocale("de");
    expect(fmtKwh(12.34)).toBe("12,3 kWh");
  });

  it("trägt immer die Einheit", () => {
    expect(fmtPct(73.4)).toBe("73 %");
    expect(fmtTemp(58.6)).toBe("59 °C");
    expect(fmtCt(28.44)).toBe("28,4 ct");
  });
});
