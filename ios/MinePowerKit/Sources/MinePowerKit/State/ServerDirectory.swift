import Foundation
import Observation

/// Ein gespeicherter MinePower-Server.
///
/// Enthält nichts Geheimes: Adresse, Anzeigename, bestätigter
/// Zertifikats-Fingerabdruck und zuletzt gesehene Version. Das Token liegt
/// getrennt im Schlüsselbund unter `tokenKey`.
public struct ServerProfile: Codable, Sendable, Hashable, Identifiable {
    public var id: UUID
    public var name: String
    public var url: URL
    /// SHA-256 des vom Nutzer bestätigten, selbstsignierten Zertifikats.
    public var fingerprint: String?
    public var email: String?
    public var lastVersion: String?
    public var lastUserName: String?
    public var isDemo: Bool
    public var addedAt: Date

    public init(id: UUID = UUID(), name: String, url: URL, fingerprint: String? = nil, email: String? = nil,
                lastVersion: String? = nil, isDemo: Bool = false, addedAt: Date = .now) {
        self.id = id
        self.name = name
        self.url = url
        self.fingerprint = fingerprint
        self.email = email
        self.lastVersion = lastVersion
        self.isDemo = isDemo
        self.addedAt = addedAt
    }

    public var tokenKey: String { "token.\(id.uuidString)" }
    public var host: String { url.host ?? url.absoluteString }

    /// Kurzform für Listen: Host und, falls nicht Standard, Port.
    public var displayAddress: String {
        var text = url.host ?? url.absoluteString
        if let port = url.port, port != 80, port != 443 { text += ":\(port)" }
        if !url.path.isEmpty, url.path != "/" { text += url.path }
        return text
    }
}

/// Liste der Server, zwischen denen die App wechseln kann.
@MainActor
@Observable
public final class ServerDirectory {
    public private(set) var profiles: [ServerProfile] = []
    public private(set) var activeID: UUID?

    @ObservationIgnored private let defaults: UserDefaults
    static let storageKey = "servers.v1"
    static let activeKey = "servers.active"
    /// Vorgänger-App (1.x) speicherte genau eine Adresse.
    static let legacyURLKey = "server-url"

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        load()
    }

    public var active: ServerProfile? { profiles.first { $0.id == activeID } }

    /// Fügt hinzu oder aktualisiert einen vorhandenen Eintrag mit derselben Adresse.
    @discardableResult
    public func add(_ profile: ServerProfile, activate: Bool = true) -> ServerProfile {
        var stored = profile
        if let index = profiles.firstIndex(where: { $0.url == profile.url && $0.isDemo == profile.isDemo }) {
            stored.id = profiles[index].id
            stored.addedAt = profiles[index].addedAt
            stored.email = profile.email ?? profiles[index].email
            stored.fingerprint = profile.fingerprint ?? profiles[index].fingerprint
            profiles[index] = stored
        } else {
            profiles.append(stored)
        }
        if activate { activeID = stored.id }
        save()
        return stored
    }

    public func update(_ id: UUID, _ change: (inout ServerProfile) -> Void) {
        guard let index = profiles.firstIndex(where: { $0.id == id }) else { return }
        change(&profiles[index])
        save()
    }

    public func remove(_ id: UUID) {
        profiles.removeAll { $0.id == id }
        if activeID == id { activeID = profiles.first { !$0.isDemo }?.id ?? profiles.first?.id }
        save()
    }

    public func activate(_ id: UUID?) {
        activeID = id.flatMap { id in profiles.contains { $0.id == id } ? id : nil }
        save()
    }

    /// Bestätigte Zertifikate aller Server (Host → Abdruck).
    public var pins: [String: String] {
        Dictionary(profiles.compactMap { p in p.fingerprint.map { (p.host.lowercased(), $0) } }) { a, _ in a }
    }

    /// Alte Einzeladresse übernehmen, damit niemand nach dem Update neu suchen muss.
    public func migrateLegacy() -> ServerProfile? {
        guard profiles.isEmpty, let raw = defaults.string(forKey: Self.legacyURLKey),
              let url = ServerAddress.normalize(raw) else { return nil }
        defaults.removeObject(forKey: Self.legacyURLKey)
        return add(ServerProfile(name: url.host ?? "MinePower", url: url))
    }

    private func load() {
        if let data = defaults.data(forKey: Self.storageKey),
           let list = try? JSONDecoder().decode([ServerProfile].self, from: data) {
            profiles = list
        }
        if let raw = defaults.string(forKey: Self.activeKey), let id = UUID(uuidString: raw),
           profiles.contains(where: { $0.id == id }) {
            activeID = id
        } else {
            activeID = profiles.first?.id
        }
    }

    private func save() {
        if let data = try? JSONEncoder().encode(profiles) { defaults.set(data, forKey: Self.storageKey) }
        defaults.set(activeID?.uuidString, forKey: Self.activeKey)
    }
}
