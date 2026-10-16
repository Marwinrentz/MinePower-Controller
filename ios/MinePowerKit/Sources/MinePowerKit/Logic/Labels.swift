import Foundation

/// Bezeichnungen wie in der Weboberfläche (`i18n` → `modes`), für App, Widgets und Kurzbefehle.
public extension WallboxMode {
    var title: String {
        switch self {
        case .pvOnly: Loc.t("Nur Sonne", "Solar only")
        case .minPv: Loc.t("Sonne + Minimum", "Solar + minimum")
        case .pvPrice: Loc.t("Sonne + günstig", "Solar + cheap")
        case .target: Loc.t("Zielladung", "Target charge")
        }
    }

    var help: String {
        switch self {
        case .pvOnly: Loc.t("Nur PV-Überschuss", "PV surplus only")
        case .minPv: Loc.t("Mindeststrom immer, Rest aus PV-Überschuss", "Minimum current always, the rest from PV surplus")
        case .pvPrice: Loc.t("PV-Überschuss, unter Preisgrenze auch Netz", "PV surplus, grid too below the price limit")
        case .target: Loc.t("Ziel-Ladestand bis Abfahrt, bei Bedarf mit Netz", "Target charge by departure, using the grid if needed")
        }
    }

    var symbol: String {
        switch self {
        case .pvOnly: "sun.max"
        case .minPv: "sun.min"
        case .pvPrice: "eurosign.circle"
        case .target: "flag.checkered"
        }
    }
}

public extension WaterMode {
    var title: String {
        switch self {
        case .off: Loc.t("Aus", "Off")
        case .pvOnly: Loc.t("Nur Sonne", "Solar only")
        case .pvPrice: Loc.t("Sonne + günstig", "Solar + cheap")
        case .schedule: Loc.t("Zeitplan", "Schedule")
        }
    }

    var help: String {
        switch self {
        case .off: Loc.t("Aus. Laufender Boost endet.", "Off. A running boost ends.")
        case .pvOnly: Loc.t("Nur PV-Überschuss", "PV surplus only")
        case .pvPrice: Loc.t("PV-Überschuss, unter Preisgrenze auch Netz", "PV surplus, grid too below the price limit")
        case .schedule: Loc.t("Zeitfenster: volle Leistung bis Zieltemperatur. Sonst PV-Überschuss.",
                              "Time window: full power up to the target temperature. Otherwise PV surplus.")
        }
    }

    var symbol: String {
        switch self {
        case .off: "power"
        case .pvOnly: "sun.max"
        case .pvPrice: "eurosign.circle"
        case .schedule: "calendar.badge.clock"
        }
    }
}

public extension SnapshotDevice {
    /// Plan des Warmwasser-Boosts ("1 h · bis 65 °C").
    func boostPlan(fmt: Fmt = Fmt()) -> String {
        let time = fmt.duration((boostDurationMin ?? 60) * 60)
        let temp = fmt.celsius(boostTempC)
        switch boostEndMode ?? "both" {
        case "time": return Loc.t("für \(time)", "for \(time)")
        case "temp": return Loc.t("bis \(temp)", "up to \(temp)")
        default: return Loc.t("\(time) · bis \(temp)", "\(time) · up to \(temp)")
        }
    }

    var wallboxMode: WallboxMode? { mode.flatMap(WallboxMode.init(rawValue:)) }
    var waterMode: WaterMode? { mode.flatMap(WaterMode.init(rawValue:)) }
}

public extension Snapshot {
    /// Zeit bis voll (lädt) bzw. bis zur Reserve (entlädt), in Sekunden.
    var batteryETA: TimeInterval? {
        guard let battery, let soc = battery.soc, let power = battery.power, abs(power) >= 100,
              let capacity = battery.capacityKwh, capacity > 0 else { return nil }
        let reserve = batteryReserveSoc ?? 0
        let percent = power > 0 ? max(0, 100 - soc) : max(0, soc - reserve)
        guard percent > 0 else { return nil }
        return capacity * 1000 * percent / 100 / abs(power) * 3600
    }
}
