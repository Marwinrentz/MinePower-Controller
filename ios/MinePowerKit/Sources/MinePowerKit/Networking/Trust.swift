import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
#if canImport(Security)
import Security
#endif
#if canImport(CryptoKit)
import CryptoKit
#endif

/// Kleiner Wert hinter einem Lock.
///
/// `@unchecked Sendable` ist hier begründet: Jeder Zugriff läuft über `NSLock`.
/// `Mutex` (Synchronization) gibt es erst ab iOS 18, die App unterstützt iOS 17.
public final class Locked<Value: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Value

    public init(_ value: Value) { self.value = value }

    public func withLock<R>(_ body: (inout Value) throws -> R) rethrows -> R {
        lock.lock()
        defer { lock.unlock() }
        return try body(&value)
    }

    public var current: Value { withLock { $0 } }
}

/// Vertrauen in selbstsignierte Zertifikate (Heimserver hinter eigenem TLS).
///
/// Ablauf 'Vertrauen beim ersten Kontakt": Die Systemprüfung schlägt fehl, die
/// App zeigt dem Nutzer Host und SHA-256-Fingerabdruck des Zertifikats
/// (identisch mit `openssl x509 -fingerprint -sha256`), er bestätigt, die App
/// merkt sich den Abdruck je Host. Ändert sich das Zertifikat, wird erneut
/// gefragt. Öffentlich gültige Zertifikate laufen unverändert über die
/// Systemprüfung.
public final class TrustRegistry: Sendable {
    public static let shared = TrustRegistry()

    private let pinned = Locked<[String: String]>([:])
    private let presented = Locked<[String: String]>([:])

    public init() {}

    /// Bestätigte Abdrücke laden (z. B. aus dem Schlüsselbund).
    public func load(_ pins: [String: String]) { pinned.withLock { $0 = pins } }

    public func pin(host: String, fingerprint: String) {
        pinned.withLock { $0[host.lowercased()] = Self.normalize(fingerprint) }
    }

    public func unpin(host: String) { _ = pinned.withLock { $0.removeValue(forKey: host.lowercased()) } }

    public var allPins: [String: String] { pinned.current }

    public func isPinned(host: String, fingerprint: String) -> Bool {
        pinned.current[host.lowercased()] == Self.normalize(fingerprint)
    }

    /// Zuletzt vom Server vorgelegter (nicht vertrauter) Abdruck – für den Bestätigungsdialog.
    public func lastPresented(for host: String) -> String? { presented.current[host.lowercased()] }

    func recordPresented(host: String, fingerprint: String) {
        presented.withLock { $0[host.lowercased()] = Self.normalize(fingerprint) }
    }

    public static func normalize(_ fingerprint: String) -> String {
        fingerprint.uppercased().filter { $0.isHexDigit }
    }

    /// 'AB:CD:…" in Zweiergruppen für die Anzeige.
    public static func display(_ fingerprint: String) -> String {
        let hex = Array(normalize(fingerprint))
        return stride(from: 0, to: hex.count, by: 2).map { String(hex[$0..<min($0 + 2, hex.count)]) }
            .joined(separator: ":")
    }
}

#if canImport(Security) && canImport(CryptoKit)
/// URLSession-Delegate: Systemprüfung zuerst, danach bestätigte Abdrücke.
public final class PinningDelegate: NSObject, URLSessionDelegate, Sendable {
    private let registry: TrustRegistry

    public init(registry: TrustRegistry = .shared) { self.registry = registry }

    public func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge)
        async -> (URLSession.AuthChallengeDisposition, URLCredential?)
    {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust else {
            return (.performDefaultHandling, nil)
        }
        let host = challenge.protectionSpace.host
        if SecTrustEvaluateWithError(trust, nil) {
            return (.useCredential, URLCredential(trust: trust))
        }
        guard let fingerprint = Self.leafFingerprint(trust) else {
            return (.cancelAuthenticationChallenge, nil)
        }
        if registry.isPinned(host: host, fingerprint: fingerprint) {
            return (.useCredential, URLCredential(trust: trust))
        }
        registry.recordPresented(host: host, fingerprint: fingerprint)
        // Standardbehandlung statt Abbruch: Das System lehnt dann mit einem
        // Zertifikatsfehler ab (nicht mit "abgebrochen"), und die App kann den
        // Abdruck zur Bestätigung anzeigen.
        return (.performDefaultHandling, nil)
    }

    static func leafFingerprint(_ trust: SecTrust) -> String? {
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let leaf = chain.first else {
            return nil
        }
        let der = SecCertificateCopyData(leaf) as Data
        return SHA256.hash(data: der).map { String(format: "%02X", $0) }.joined()
    }
}
#endif

public extension URLSession {
    /// Session für MinePower: kurze Wartezeiten, kein Cache, Zertifikatsprüfung mit Bestätigung.
    static func minePower(registry: TrustRegistry = .shared) -> URLSession {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 120
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.urlCache = nil
        #if !canImport(FoundationNetworking)
        config.waitsForConnectivity = false
        #endif
        #if canImport(Security) && canImport(CryptoKit)
        return URLSession(configuration: config, delegate: PinningDelegate(registry: registry), delegateQueue: nil)
        #else
        return URLSession(configuration: config)
        #endif
    }
}
