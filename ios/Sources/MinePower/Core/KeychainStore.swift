import Foundation
import LocalAuthentication
import MinePowerKit
import Security

/// Tokens im Schlüsselbund.
///
/// - Ohne Sperre: `AfterFirstUnlockThisDeviceOnly` – Widgets und
///   Hintergrundabrufe können nach dem ersten Entsperren lesen, nichts wandert
///   in Backups oder auf andere Geräte.
/// - Mit Sperre: Zugriffskontrolle `.userPresence` (Face ID, Touch ID oder
///   Gerätecode). Der Schlüsselbund selbst verlangt die Bestätigung; ein
///   bloßes `LAContext`-Ja würde das Token nicht schützen.
///
/// Geschützte und ungeschützte Einträge liegen unter getrennten Kontonamen
/// (`<schlüssel>` und `<schlüssel>#geschützt`). Der Schutz einer bestehenden
/// Zugriffskontrolle lässt sich per Update nicht ändern und ohne
/// Face-ID-Abfrage auch nicht sicher erkennen; mit zwei Namen wird beim
/// Umschalten erst der neue Eintrag geschrieben, dann der alte gelöscht –
/// es gibt keinen Moment ohne Token.
///
/// Ein eigener Aktor, damit `SecItem*` nie auf dem Hauptthread blockiert.
actor KeychainStore: SecretStore {
    private let service: String
    /// Zuletzt bestätigter Face-ID-Kontext: Ein direkt folgendes Schreiben
    /// (Token-Verlängerung nach dem Entsperren) fragt nicht ein zweites Mal.
    private var authenticatedContext: LAContext?

    init(service: String = "de.minepower.app") {
        self.service = service
    }

    func secret(for key: String) async throws -> String? {
        if let plain = try read(account: key, context: nil) { return plain }
        let context = LAContext()
        context.localizedReason = String(localized: "Zugang zu deiner Anlage entsperren")
        let value = try read(account: Self.protectedAccount(key), context: context)
        if value != nil { authenticatedContext = context }
        return value
    }

    func setSecret(_ value: String, for key: String, protected: Bool) async throws {
        let target = protected ? Self.protectedAccount(key) : key
        let other = protected ? key : Self.protectedAccount(key)
        try write(Data(value.utf8), account: target, protected: protected)
        try delete(account: other)
    }

    func removeSecret(for key: String) async throws {
        try delete(account: key)
        try delete(account: Self.protectedAccount(key))
        authenticatedContext = nil
    }

    // MARK: - Intern

    private static func protectedAccount(_ key: String) -> String { key + "#geschützt" }

    private func read(account: String, context: LAContext?) throws -> String? {
        var query = identity(account)
        query[kSecReturnData] = true
        query[kSecMatchLimit] = kSecMatchLimitOne
        if let context { query[kSecUseAuthenticationContext] = context }
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        switch status {
        case errSecSuccess:
            guard let data = item as? Data else { return nil }
            return String(decoding: data, as: UTF8.self)
        case errSecItemNotFound:
            return nil
        case errSecUserCanceled, errSecAuthFailed:
            throw SecretStoreError.userCancelled
        default:
            throw SecretStoreError.unavailable(status: status)
        }
    }

    /// Anlegen oder ersetzen (kein Löschen-und-neu im Normalfall).
    private func write(_ data: Data, account: String, protected: Bool) throws {
        var add = identity(account)
        add[kSecValueData] = data
        if protected {
            var error: Unmanaged<CFError>?
            guard let access = SecAccessControlCreateWithFlags(
                nil, kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly, .userPresence, &error) else {
                throw SecretStoreError.unavailable(status: errSecParam)
            }
            add[kSecAttrAccessControl] = access
            if let authenticatedContext { add[kSecUseAuthenticationContext] = authenticatedContext }
        } else {
            add[kSecAttrAccessible] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        }

        switch SecItemAdd(add as CFDictionary, nil) {
        case errSecSuccess:
            return
        case errSecDuplicateItem:
            var query = identity(account)
            if protected, let authenticatedContext { query[kSecUseAuthenticationContext] = authenticatedContext }
            let status = SecItemUpdate(query as CFDictionary, [kSecValueData: data] as CFDictionary)
            switch status {
            case errSecSuccess: return
            case errSecUserCanceled, errSecAuthFailed: throw SecretStoreError.userCancelled
            default: throw SecretStoreError.unavailable(status: status)
            }
        case let status:
            throw SecretStoreError.unavailable(status: status)
        }
    }

    private func delete(account: String) throws {
        let status = SecItemDelete(identity(account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw SecretStoreError.unavailable(status: status)
        }
    }

    private func identity(_ account: String) -> [CFString: Any] {
        [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
        ]
    }
}

/// Ob das Gerät Face ID / Touch ID oder wenigstens einen Code hat.
enum Biometrics {
    enum Kind { case faceID, touchID, opticID, passcode, none }

    static var kind: Kind {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else { return .none }
        _ = context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: nil)
        switch context.biometryType {
        case .faceID: return .faceID
        case .touchID: return .touchID
        case .opticID: return .opticID
        default: return .passcode
        }
    }

    static var label: String {
        switch kind {
        case .faceID: "Face ID"
        case .touchID: "Touch ID"
        case .opticID: "Optic ID"
        case .passcode, .none: String(localized: "Gerätecode")
        }
    }

    static var symbol: String {
        switch kind {
        case .faceID: "faceid"
        case .touchID: "touchid"
        case .opticID: "opticid"
        case .passcode, .none: "lock"
        }
    }
}
