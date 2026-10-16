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
/// Ein eigener Aktor, damit `SecItem*` nie auf dem Hauptthread blockiert.
actor KeychainStore: SecretStore {
    private let service: String

    init(service: String = "de.minepower.app") {
        self.service = service
    }

    func secret(for key: String) async throws -> String? {
        var query = identity(key)
        query[kSecReturnData] = true
        query[kSecMatchLimit] = kSecMatchLimitOne
        let context = LAContext()
        context.localizedReason = String(localized: "Zugang zu deiner Anlage entsperren")
        query[kSecUseAuthenticationContext] = context

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

    func setSecret(_ value: String, for key: String, protected: Bool) async throws {
        let data = Data(value.utf8)
        // Wechsel des Schutzes lässt sich nicht per Update ändern: nur dann neu anlegen.
        if protected != isProtected(key) {
            try removeSecret(key)
        }
        var add = identity(key)
        add[kSecValueData] = data
        if protected {
            var error: Unmanaged<CFError>?
            guard let access = SecAccessControlCreateWithFlags(
                nil, kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly, .userPresence, &error) else {
                throw SecretStoreError.unavailable(status: errSecParam)
            }
            add[kSecAttrAccessControl] = access
        } else {
            add[kSecAttrAccessible] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        }

        switch SecItemAdd(add as CFDictionary, nil) {
        case errSecSuccess:
            return
        case errSecDuplicateItem:
            let status = SecItemUpdate(identity(key) as CFDictionary, [kSecValueData: data] as CFDictionary)
            guard status == errSecSuccess else { throw SecretStoreError.unavailable(status: status) }
        case let status:
            throw SecretStoreError.unavailable(status: status)
        }
    }

    func removeSecret(for key: String) async throws {
        try removeSecret(key)
    }

    // MARK: - Intern

    private func removeSecret(_ key: String) throws {
        let status = SecItemDelete(identity(key) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw SecretStoreError.unavailable(status: status)
        }
    }

    /// Ist der Eintrag mit Zugriffskontrolle angelegt? (Ohne Face-ID-Abfrage.)
    private func isProtected(_ key: String) -> Bool {
        var query = identity(key)
        query[kSecReturnAttributes] = true
        query[kSecMatchLimit] = kSecMatchLimitOne
        let context = LAContext()
        context.interactionNotAllowed = true
        query[kSecUseAuthenticationContext] = context
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        // Geschützte Einträge melden ohne Interaktion "nicht erlaubt".
        return status == errSecInteractionNotAllowed
    }

    private func identity(_ key: String) -> [CFString: Any] {
        [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: key,
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
