import Foundation

/// Versionsnummer '3.1.1" vergleichbar machen.
public struct SemanticVersion: Comparable, Sendable, Hashable, CustomStringConvertible {
    public var major: Int
    public var minor: Int
    public var patch: Int

    public init(_ major: Int, _ minor: Int = 0, _ patch: Int = 0) {
        self.major = major
        self.minor = minor
        self.patch = patch
    }

    public init?(_ text: String?) {
        guard let text else { return nil }
        let core = text.trimmingCharacters(in: .whitespaces).drop(while: { $0 == "v" })
            .split(whereSeparator: { $0 == "-" || $0 == "+" }).first ?? ""
        let parts = core.split(separator: ".").map { Int($0) }
        guard let first = parts.first, let major = first, parts.allSatisfy({ $0 != nil }) else { return nil }
        self.major = major
        self.minor = parts.count > 1 ? parts[1] ?? 0 : 0
        self.patch = parts.count > 2 ? parts[2] ?? 0 : 0
    }

    public static func < (a: Self, b: Self) -> Bool {
        (a.major, a.minor, a.patch) < (b.major, b.minor, b.patch)
    }

    public var description: String { "\(major).\(minor).\(patch)" }
}

/// Wer passt zu wem? App und Server werden unabhängig aktualisiert (App Store
/// bzw. Docker). Die App muss deshalb mit einem älteren Server umgehen und
/// sagen, wenn eine Funktion erst mit einem Server-Update kommt.
public enum Compatibility: Equatable, Sendable {
    case ok
    /// Server älter als das Minimum: App nur eingeschränkt nutzbar.
    case serverTooOld(minimum: SemanticVersion, server: SemanticVersion)
    /// Server neuer als alles, was diese App kennt (große Versionssprünge).
    case appMayBeOutdated(server: SemanticVersion)
    case unknown

    /// Mindestversion des Servers für diese App (KI-Schlüssel, Gerätelimits,
    /// Changelog-Endpunkt, maskierte Zugangsdaten).
    public static let minimumServer = SemanticVersion(3, 1, 0)
    /// Höchste getestete Hauptversion des Servers.
    public static let testedServerMajor = 3

    public static func evaluate(serverVersion: String?) -> Compatibility {
        guard let server = SemanticVersion(serverVersion) else { return .unknown }
        if server < minimumServer { return .serverTooOld(minimum: minimumServer, server: server) }
        if server.major > testedServerMajor { return .appMayBeOutdated(server: server) }
        return .ok
    }

    /// Funktionen, die es erst ab einer Serverversion gibt.
    public enum Feature: Sendable {
        case changelog, mcp, deviceLimits, batteryCapacity

        public var since: SemanticVersion {
            switch self {
            case .changelog, .deviceLimits: SemanticVersion(3, 0, 0)
            case .mcp, .batteryCapacity: SemanticVersion(3, 1, 0)
            }
        }
    }

    public static func supports(_ feature: Feature, serverVersion: String?) -> Bool {
        guard let server = SemanticVersion(serverVersion) else { return true }
        return server >= feature.since
    }
}

/// Befehle für das Server-Update, je nach Installation.
public enum ServerUpdate {
    public static let image = "marwinrentz1/minepower"

    public static func dockerCompose() -> String {
        "docker compose pull && docker compose up -d"
    }

    /// Ohne Compose: neues Image holen, danach den Container mit denselben
    /// Parametern neu erstellen (Volumes bleiben erhalten).
    public static func dockerPull() -> String { "docker pull \(image):latest" }

    public static let unraidSteps = [
        Loc.t("Unraid: Reiter 'Docker' öffnen", "Unraid: open the 'Docker' tab"),
        Loc.t("Bei MinePower 'Update ready' anklicken oder 'Check for Updates'", "On MinePower click 'Update ready' or 'Check for Updates'"),
        Loc.t("'Apply Update' bestätigen", "Confirm 'Apply Update'"),
    ]
}
