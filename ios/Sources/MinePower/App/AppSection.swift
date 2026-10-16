import MinePowerKit
import SwiftUI

/// Bereiche der App, gleiche Reihenfolge wie die Weboberfläche.
///
/// iPhone: die ersten vier als Tabs plus "Mehr" (Daumenzone, höchstens fünf
/// Ziele). iPad: alle in der Seitenleiste.
enum AppSection: String, CaseIterable, Identifiable, Hashable, Codable {
    case dashboard, car, water, battery, tariff, statistics, diagnose, settings, changelog

    var id: String { rawValue }

    static let primary: [AppSection] = [.dashboard, .car, .water, .battery]
    static let more: [AppSection] = [.tariff, .statistics, .diagnose, .settings, .changelog]

    var title: LocalizedStringKey {
        switch self {
        case .dashboard: "Übersicht"
        case .car: "Auto"
        case .water: "Warmwasser"
        case .battery: "Batterie"
        case .tariff: "Tarif"
        case .statistics: "Statistik"
        case .diagnose: "Diagnose"
        case .settings: "Einstellungen"
        case .changelog: "Changelog"
        }
    }

    /// Symbole wie die Navigation der Weboberfläche (`AppShell.tsx`).
    var icon: WebIcon {
        switch self {
        case .dashboard: .dashboard
        case .car: .car
        case .water: .water
        case .battery: .battery
        case .tariff: .price
        case .statistics: .statistics
        case .diagnose: .pulse
        case .settings: .settings
        case .changelog: .events
        }
    }

    var domain: DomainColor {
        switch self {
        case .dashboard: Palette.pv
        case .car: Palette.car
        case .water: Palette.water
        case .battery: Palette.battery
        case .tariff: Palette.gridExport
        default: Palette.house
        }
    }

    /// Deep Link `minepower://bereich` (Widgets, Kurzbefehle).
    init?(url: URL) {
        guard url.scheme == "minepower", let host = url.host else { return nil }
        let aliases: [String: AppSection] = [
            "auto": .car, "vehicle": .car, "warmwasser": .water, "batterie": .battery, "tarif": .tariff,
            "statistik": .statistics, "einstellungen": .settings, "start": .dashboard, "uebersicht": .dashboard,
        ]
        guard let section = AppSection(rawValue: host) ?? aliases[host] else { return nil }
        self = section
    }
}
