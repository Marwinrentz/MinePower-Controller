import Foundation

/// Ablage für Anmelde-Tokens.
///
/// Die App liefert eine Schlüsselbund-Umsetzung (`KeychainStore`), Tests und
/// die Demo eine im Speicher. Das Protokoll ist bewusst schmal: Tokens werden
/// nur gelesen, geschrieben und gelöscht, nie aufgezählt.
public protocol SecretStore: Sendable {
    /// Liest ein Geheimnis. Bei Biometrie-Schutz löst das die Systemabfrage aus.
    func secret(for key: String) async throws -> String?
    /// Legt an oder ersetzt. `protected`: nur nach Face ID / Touch ID / Code lesbar.
    func setSecret(_ value: String, for key: String, protected: Bool) async throws
    func removeSecret(for key: String) async throws
}

public enum SecretStoreError: Error, Equatable, Sendable {
    /// Der Nutzer hat die Face-ID-Abfrage abgebrochen.
    case userCancelled
    /// Gerät gesperrt oder Schlüsselbund nicht verfügbar.
    case unavailable(status: Int32)
}

/// Für Tests, Vorschauen und die Demo.
public actor InMemorySecretStore: SecretStore {
    private var values: [String: String]
    private var protectedKeys: Set<String> = []
    /// Simuliert eine abgebrochene Face-ID-Abfrage bei geschützten Einträgen.
    public var denyProtectedReads = false

    public init(_ values: [String: String] = [:]) { self.values = values }

    public func secret(for key: String) async throws -> String? {
        if denyProtectedReads, protectedKeys.contains(key) { throw SecretStoreError.userCancelled }
        return values[key]
    }

    public func setSecret(_ value: String, for key: String, protected: Bool) async throws {
        values[key] = value
        if protected { protectedKeys.insert(key) } else { protectedKeys.remove(key) }
    }

    public func removeSecret(for key: String) async throws {
        values.removeValue(forKey: key)
        protectedKeys.remove(key)
    }

    public func setDenyProtectedReads(_ deny: Bool) { denyProtectedReads = deny }
    public func isProtected(_ key: String) -> Bool { protectedKeys.contains(key) }
}
