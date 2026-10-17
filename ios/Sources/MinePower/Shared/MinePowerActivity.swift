#if canImport(ActivityKit)
import ActivityKit
import Foundation

/// Live-Aktivität für Boost und Sofortladen (Sperrbildschirm, Dynamic Island).
///
/// Der Server schickt keine Push-Mitteilungen: Die Restzeit läuft deshalb als
/// Timer von selbst weiter (`endsAt`), Werte aktualisiert die App, solange
/// sie offen ist. Gemeinsam von App und Widget-Erweiterung übersetzt.
struct MinePowerActivityAttributes: ActivityAttributes {
    enum Kind: String, Codable, Hashable, Sendable {
        case boost, fastCharge
    }

    struct ContentState: Codable, Hashable, Sendable {
        /// Große Zahl: Temperatur bzw. Ladestand.
        var value: String
        /// Kurzer Zustand ("Heizt mit 3,0 kW").
        var detail: String
        /// 0…1 für den Fortschrittsbalken (Temperatur zum Ziel, Ladestand).
        var progress: Double?
        /// Ende des Boosts bzw. des Sofortladens, wenn bekannt.
        var endsAt: Date?
    }

    var kind: Kind
    var deviceName: String
}
#endif
