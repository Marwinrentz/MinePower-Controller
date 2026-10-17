import Foundation

/// Kernaussage und Rahmenfarbe der Wand-Ansicht (Spiegel von `overallState()`
/// in `Wall.tsx`). Reihenfolge = Dringlichkeit; ohne Störung dieselbe Aussage
/// wie die Übersicht, damit Wand und Handy nie zwei Geschichten erzählen.
public struct WallState: Sendable, Equatable {
    public enum Tone: String, Sendable {
        case ok, waste, warn, error, idle
    }

    public var tone: Tone
    public var headline: String
    public var sub: String

    public init(_ s: Snapshot, fmt: Fmt = Fmt()) {
        let offline = s.offlineDevices
        let failed = s.allDevices.filter { $0.commandError != nil }
        if let safety = s.safety {
            (tone, headline, sub) = (.error, Loc.t("Sicherheits-Stopp aktiv", "Safety stop active"), safety)
        } else if s.isPaused {
            (tone, headline, sub) = (.warn, Loc.t("Regelung pausiert", "Control paused"), Loc.t("Lasten gestoppt", "Loads stopped"))
        } else if !failed.isEmpty {
            (tone, headline, sub) = (.error, Loc.t("Befehl wurde nicht übernommen", "Command not applied"),
                                     failed.map(\.name).joined(separator: ", "))
        } else if !offline.isEmpty {
            (tone, headline, sub) = (.warn, Loc.t("Gerät nicht erreichbar", "Device unreachable"),
                                     offline.map(\.name).joined(separator: ", "))
        } else if (s.wasteW ?? 0) > 150 {
            // Kurz: nur der wichtigste Grund (Details: Diagnose).
            let cause = s.wasteReason.flatMap { Explanation.wasteCauses($0, skip: []).first }
            (tone, headline, sub) = (.waste, Loc.t("Überschuss geht ins Netz", "Surplus going to the grid"),
                                     cause ?? Loc.t("Keine Last verfügbar", "No load available"))
        } else {
            let explanation = Explanation(s, fmt: fmt)
            let busy = (s.wallboxPower ?? 0) > 50 || (s.waterPower ?? 0) > 50
            (tone, headline, sub) = (busy ? .ok : .idle, explanation.headline, explanation.reasons.first?.text ?? "")
        }
    }
}

/// Feste Skalenstufen für Waage und Balken (`pickScale` im Web): Die Skala
/// soll nicht bei jedem Messwert wechseln, sonst "atmet" die Anzeige.
public enum PowerScale {
    public static let steps: [Double] = [1000, 2000, 3000, 5000, 8000, 12000, 20000, 30000]

    public static func pick(_ watts: Double) -> Double {
        let need = abs(watts) * 1.2
        return steps.first { $0 >= need } ?? steps[steps.count - 1]
    }
}

/// Netzpunkt-Waage (`BalanceMeter`): Ausschlag aus der Mitte, Zielband = Totband.
public struct Balance: Sendable, Equatable {
    public enum State: String, Sendable { case balanced, importing, exporting }

    public var state: State
    public var scale: Double
    public var band: Double

    public init(_ s: Snapshot) {
        let grid = s.gridPower ?? 0
        band = max(s.deadbandW ?? 0, 50)
        scale = PowerScale.pick(grid)
        state = abs(grid) <= band ? .balanced : grid > band ? .importing : .exporting
    }

    /// Ausschlag −1 … 1 (negativ = Einspeisung) für einen (animierten) Wert.
    public func offset(_ watts: Double) -> Double { max(-1, min(1, watts / scale)) }
    /// Breite des Zielbands als Anteil der ganzen Spur (höchstens die Hälfte).
    public var bandShare: Double { min(0.5, band / scale) }
}

/// Eine Zeile der Wand-Balken (`WallBars`): je Energiefluss, gemeinsame Skala.
public struct WallBarRow: Sendable, Equatable, Identifiable {
    public enum Kind: String, Sendable { case pv, house, car, water, battery, gridImport, gridExport }

    public var kind: Kind
    public var watts: Double
    public var note: String?
    public var id: String { kind == .gridImport || kind == .gridExport ? "grid" : kind.rawValue }

    public static func rows(_ s: Snapshot) -> [WallBarRow] {
        let grid = s.gridPower ?? 0
        var rows = [
            WallBarRow(kind: .pv, watts: s.pv),
            WallBarRow(kind: .house, watts: s.house),
            WallBarRow(kind: .car, watts: s.wallboxPower ?? 0),
            WallBarRow(kind: .water, watts: s.waterPower ?? 0),
        ]
        if let battery = s.battery {
            let power = battery.power ?? 0
            rows.append(WallBarRow(kind: .battery, watts: abs(power),
                                   note: power > 30 ? Loc.t("lädt", "charging") : power < -30 ? Loc.t("entlädt", "discharging") : nil))
        }
        rows.append(WallBarRow(kind: grid > 0 ? .gridImport : .gridExport, watts: abs(grid),
                               note: grid > 50 ? Loc.t("Bezug", "Import") : grid < -50 ? Loc.t("Einspeisung", "Export") : nil))
        return rows
    }

    /// Gemeinsame Skala aller Zeilen (mindestens 1 kW).
    public static func scale(_ rows: [WallBarRow]) -> Double {
        PowerScale.pick(max(rows.map(\.watts).max() ?? 0, 1000) / 1.2)
    }
}
