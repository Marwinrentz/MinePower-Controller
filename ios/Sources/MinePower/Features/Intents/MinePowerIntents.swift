import AppIntents
import MinePowerKit

/// Kurzbefehle und Siri: Boost, Sofort laden, Auto aus/Automatik, Status.
///
/// Laufen im Prozess der App (auch ohne sichtbares Fenster). Ist die App mit
/// Face ID gesperrt, öffnet der Befehl die App statt das Token zu lesen.
@MainActor
enum IntentRuntime {
    static var session: AppSession?

    /// Angemeldete Sitzung, notfalls erst starten (Kurzbefehl bei geschlossener App).
    static func signedIn() async throws -> AppSession {
        guard let session else { throw MinePowerIntentError.notSignedIn }
        if session.phase == .launching {
            if session.beginLaunch() {
                await session.launch()
            } else {
                // Das Fenster startet die Sitzung gerade: kurz darauf warten statt abzulehnen.
                for _ in 0..<50 where session.phase == .launching {
                    try? await Task.sleep(for: .milliseconds(100))
                }
            }
        }
        switch session.phase {
        case .signedIn: return session
        case .locked: throw MinePowerIntentError.locked
        default: throw MinePowerIntentError.notSignedIn
        }
    }

    /// Erstes aktives Gerät der Kategorie; Befehl senden (Server prüft die Rechte).
    static func send(_ action: DeviceAction, to category: DeviceCategory) async throws -> String {
        let session = try await signedIn()
        // Offline gestartet: Rolle unbekannt, der Server ist das Problem, nicht die Rechte.
        if session.isOffline || session.user == nil { throw MinePowerIntentError.offline }
        guard session.canControl else { throw MinePowerIntentError.notAllowed }
        let snapshot: Snapshot
        if let current = session.live.snapshot {
            snapshot = current
        } else {
            snapshot = try await session.api.liveStatus()
        }
        guard let device = snapshot.enabledDevices.first(where: { $0.category == category }) else {
            throw MinePowerIntentError.noDevice
        }
        try await session.api.deviceAction(device.id, action)
        try? await session.live.refresh()
        return device.name
    }
}

enum MinePowerIntentError: Error, CustomLocalizedStringResourceConvertible {
    case notSignedIn, locked, notAllowed, noDevice, offline

    var localizedStringResource: LocalizedStringResource {
        switch self {
        case .notSignedIn: "In MinePower ist kein Server angemeldet."
        case .locked: "MinePower ist gesperrt. Bitte die App öffnen und entsperren."
        case .notAllowed: "Dein Konto darf nur lesen."
        case .noDevice: "Kein passendes Gerät eingerichtet."
        case .offline: "Der MinePower-Server ist gerade nicht erreichbar."
        }
    }
}

struct StartBoostIntent: AppIntent {
    static let title: LocalizedStringResource = "Warmwasser-Boost starten"
    static let description = IntentDescription("Heizt das Warmwasser mit voller Leistung, bis Temperatur oder Dauer erreicht sind.")

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let name = try await IntentRuntime.send(.boost, to: .waterHeater)
        return .result(dialog: "Boost für \(name) gestartet.")
    }
}

struct StopBoostIntent: AppIntent {
    static let title: LocalizedStringResource = "Warmwasser-Boost beenden"

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let name = try await IntentRuntime.send(.boostOff, to: .waterHeater)
        return .result(dialog: "Boost für \(name) beendet.")
    }
}

struct FastChargeIntent: AppIntent {
    static let title: LocalizedStringResource = "Auto sofort laden"
    static let description = IntentDescription("Volle Leistung aus PV und Netz, bis das Auto voll ist, abgesteckt wird oder die Zeit abläuft.")

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let name = try await IntentRuntime.send(.fast, to: .wallbox)
        return .result(dialog: "\(name) lädt jetzt sofort.")
    }
}

struct CarAutomaticIntent: AppIntent {
    static let title: LocalizedStringResource = "Auto auf Automatik"
    static let description = IntentDescription("Beendet Sofortladen oder Aus und kehrt zur eingestellten Betriebsart zurück.")

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let name = try await IntentRuntime.send(.auto, to: .wallbox)
        return .result(dialog: "\(name) lädt wieder automatisch.")
    }
}

struct CarStopIntent: AppIntent {
    static let title: LocalizedStringResource = "Auto nicht laden"
    static let description = IntentDescription("Kein Laden bis zum Abstecken oder Zeitablauf.")

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let name = try await IntentRuntime.send(.stop, to: .wallbox)
        return .result(dialog: "\(name) lädt vorerst nicht.")
    }
}

struct StatusIntent: AppIntent {
    static let title: LocalizedStringResource = "Status der Anlage"
    static let description = IntentDescription("Was die Anlage gerade tut: Kernaussage, PV, Netz und Batterie.")

    @MainActor
    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let session = try await IntentRuntime.signedIn()
        let snapshot: Snapshot
        if let current = session.live.snapshot {
            snapshot = current
        } else {
            snapshot = try await session.api.liveStatus()
        }
        let fmt = Fmt()
        var parts = [Explanation(snapshot).headline + ".", String(localized: "PV \(fmt.watts(snapshot.pv))")]
        let grid = snapshot.gridPower ?? 0
        parts.append(grid > 50 ? String(localized: "Bezug \(fmt.watts(grid))")
                     : grid < -50 ? String(localized: "Einspeisung \(fmt.watts(-grid))") : String(localized: "Netz ausgeglichen"))
        if let soc = snapshot.battery?.soc { parts.append(String(localized: "Batterie \(fmt.percent(soc))")) }
        let text = parts.joined(separator: " ")
        return .result(value: text, dialog: "\(text)")
    }
}

/// Fertige Kurzbefehle mit deutschen Sätzen für Siri und Spotlight.
struct MinePowerShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: StatusIntent(), phrases: [
            "Status von \(.applicationName)",
            "Was macht \(.applicationName)",
        ], shortTitle: "Status", systemImageName: "sun.max")
        AppShortcut(intent: StartBoostIntent(), phrases: [
            "Boost in \(.applicationName) starten",
            "Warmwasser-Boost mit \(.applicationName)",
        ], shortTitle: "Boost", systemImageName: "flame")
        AppShortcut(intent: FastChargeIntent(), phrases: [
            "Auto sofort laden mit \(.applicationName)",
            "Sofort laden in \(.applicationName)",
        ], shortTitle: "Sofort laden", systemImageName: "bolt.car")
        AppShortcut(intent: CarAutomaticIntent(), phrases: [
            "Auto auf Automatik in \(.applicationName)",
        ], shortTitle: "Automatik", systemImageName: "sun.max")
        AppShortcut(intent: CarStopIntent(), phrases: [
            "Auto nicht laden mit \(.applicationName)",
        ], shortTitle: "Auto aus", systemImageName: "stop.circle")
    }
}
