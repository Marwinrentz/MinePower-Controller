import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Gemeinsamer Ablageort von App und Widget-Erweiterung (App Group).
///
/// Ohne die Berechtigung (z. B. beim Bauen mit xtool ohne App Group) liefert
/// iOS trotzdem eine Ablage, die dann nur die App selbst sieht: Das Widget
/// zeigt in dem Fall "App öffnen", nichts bricht.
public enum AppGroup {
    public static let id = "group.de.minepower.app"

    public static var defaults: UserDefaults { UserDefaults(suiteName: id) ?? .standard }
}

/// Kompakter Stand der Anlage für Widgets, Live-Aktivität und Kurzbefehle.
///
/// Die App schreibt ihn bei neuen Live-Werten; das Widget liest ihn und holt
/// sich, wenn es darf, selbst einen frischen. Nur Anzeigewerte, keine
/// Zugangsdaten.
public struct WidgetState: Codable, Sendable, Equatable {
    public struct Battery: Codable, Sendable, Equatable {
        public var soc: Double?
        public var powerW: Double
    }

    public struct Car: Codable, Sendable, Equatable {
        public var name: String
        public var state: String
        public var label: String
        public var powerW: Double
        public var soc: Double?
        /// "fast" oder "stop", solange ein Eingriff läuft.
        public var override: String?
        public var overrideUntil: Date?

        public var carState: CarState { CarState(rawValue: state) ?? .unknown }
    }

    public struct Water: Codable, Sendable, Equatable {
        public var name: String
        public var tempC: Double?
        public var targetC: Double?
        public var powerW: Double
        public var boost: Bool
        public var boostEnds: Date?
    }

    public var time: Date
    public var serverName: String
    public var isDemo: Bool
    public var headline: String
    public var tone: String
    public var pvW: Double
    public var gridW: Double
    public var houseW: Double
    public var wasteW: Double
    public var battery: Battery?
    public var car: Car?
    public var water: Water?
    public var priceCt: Double?
    public var priceCheap: Bool?

    public init(snapshot s: Snapshot, serverName: String, isDemo: Bool, now: Date = .now, fmt: Fmt = Fmt()) {
        let explanation = Explanation(s, fmt: fmt)
        time = s.time ?? now
        self.serverName = serverName
        self.isDemo = isDemo
        headline = explanation.headline
        tone = explanation.tone.rawValue
        pvW = s.pv
        gridW = s.gridPower ?? 0
        houseW = s.house
        wasteW = s.wasteW ?? 0
        battery = s.battery.map { Battery(soc: $0.soc, powerW: $0.power ?? 0) }
        if let d = s.enabledDevices.first(where: { $0.category == .wallbox }) {
            let state = CarState(d)
            car = Car(name: d.name, state: state.rawValue, label: state.label, powerW: d.values.double("power") ?? 0,
                      soc: d.soc, override: d.override, overrideUntil: d.overrideState?.until)
        }
        if let d = s.enabledDevices.first(where: { $0.category == .waterHeater }) {
            let boost = d.boost == true
            water = Water(name: d.name, tempC: d.temperatureC, targetC: d.targetTempC, powerW: d.values.double("power") ?? 0,
                          boost: boost, boostEnds: boost ? d.boostState?.remainingS.map { now.addingTimeInterval($0) } : nil)
        }
        priceCt = s.priceCt
        priceCheap = s.priceCheap
    }

    /// Älter als 30 Minuten: Das Widget zeigt den Stand dann ausdrücklich als alt.
    public func isStale(now: Date = .now) -> Bool { now.timeIntervalSince(time) > 30 * 60 }

    // MARK: Ablage

    static let key = "widget.state.v1"

    public static func load(from defaults: UserDefaults = AppGroup.defaults) -> WidgetState? {
        guard let data = defaults.data(forKey: key) else { return nil }
        // Standardkodierung: Zeitpunkte bleiben auf den Bruchteil genau erhalten.
        return try? JSONDecoder().decode(WidgetState.self, from: data)
    }

    public func save(to defaults: UserDefaults = AppGroup.defaults) {
        guard let data = try? JSONEncoder().encode(self) else { return }
        defaults.set(data, forKey: Self.key)
    }

    public static func clear(in defaults: UserDefaults = AppGroup.defaults) {
        defaults.removeObject(forKey: key)
    }
}

/// Aktiver Server für Widget und Kurzbefehle: Adresse, Zertifikat und wo das
/// Token liegt. Kein Token selbst – das bleibt im Schlüsselbund.
public struct SharedServer: Codable, Sendable, Equatable {
    public var name: String
    public var url: URL
    public var fingerprint: String?
    public var tokenKey: String
    public var isDemo: Bool
    /// Token nur mit Face ID lesbar: Widget und Hintergrund dürfen es nicht.
    public var isProtected: Bool

    public init(_ profile: ServerProfile, isProtected: Bool) {
        name = profile.name
        url = profile.url
        fingerprint = profile.fingerprint
        tokenKey = profile.tokenKey
        isDemo = profile.isDemo
        self.isProtected = isProtected
    }

    static let key = "widget.server.v1"

    public static func load(from defaults: UserDefaults = AppGroup.defaults) -> SharedServer? {
        guard let data = defaults.data(forKey: key) else { return nil }
        return try? JSONDecoder().decode(SharedServer.self, from: data)
    }

    public func save(to defaults: UserDefaults = AppGroup.defaults) {
        guard let data = try? JSONEncoder().encode(self) else { return }
        defaults.set(data, forKey: Self.key)
    }

    public static func clear(in defaults: UserDefaults = AppGroup.defaults) {
        defaults.removeObject(forKey: key)
    }

    /// API-Zugang mit eigenem Vertrauensspeicher (nur dieses Zertifikat).
    public func client(token: String?) -> APIClient {
        if isDemo {
            return APIClient(session: .demo(), baseURL: url, token: DemoBackend.token)
        }
        let registry = TrustRegistry()
        if let fingerprint, let host = url.host { registry.pin(host: host, fingerprint: fingerprint) }
        return APIClient(session: .minePower(registry: registry), baseURL: url, token: token, registry: registry)
    }

    /// Frischen Stand holen (Widget, Kurzbefehl "Status").
    public func fetchState(token: String?, now: Date = .now) async throws -> WidgetState {
        let snapshot = try await client(token: token).liveStatus()
        return WidgetState(snapshot: snapshot, serverName: name, isDemo: isDemo, now: now)
    }
}
