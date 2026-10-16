import Foundation
import Observation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Kurze Meldung oben im Bild (Fehler, Erfolg, Rückgängig).
public struct Notice: Identifiable, Sendable, Equatable {
    public enum Kind: Sendable { case info, success, warning, error }
    public var id = UUID()
    public var kind: Kind
    public var title: String
    public var message: String?

    public init(_ kind: Kind, _ title: String, message: String? = nil) {
        self.kind = kind
        self.title = title
        self.message = message
    }
}

/// Anmeldung, aktiver Server und Live-Verbindung.
///
/// Ablauf beim Start: gespeicherten Server laden → Token aus dem Schlüsselbund
/// (bei aktivierter Sperre mit Face ID) → `/api/auth/me` gegenprüfen. Ein
/// nicht erreichbarer Server meldet nicht ab, sondern zeigt "offline" mit den
/// letzten bekannten Daten; erst ein ausdrückliches 401 beendet die Sitzung.
@MainActor
@Observable
public final class AppSession {
    public enum Phase: Sendable, Equatable {
        case launching
        /// Noch kein Server gespeichert.
        case needsServer
        /// Server bekannt, aber nicht angemeldet.
        case signedOut
        /// Face-ID-Sperre aktiv.
        case locked
        case signedIn
    }

    public private(set) var phase: Phase = .launching
    public private(set) var user: User?
    public private(set) var health: Health?
    public private(set) var setup: SetupState?
    /// Server nicht erreichbar, Anzeige mit letzten Werten.
    public private(set) var isOffline = false
    public var notice: Notice?

    public let directory: ServerDirectory
    public let live: LiveStore
    public private(set) var api: APIClient

    @ObservationIgnored private let secrets: any SecretStore
    @ObservationIgnored private let registry: TrustRegistry
    @ObservationIgnored private let defaults: UserDefaults
    /// Je eine Session für echte Server und für die Demo, einmal erzeugt und
    /// wiederverwendet: Eine URLSession mit Delegate lebt bis zur Invalidierung.
    @ObservationIgnored private let networkSession: URLSession
    @ObservationIgnored private lazy var demoSession: URLSession = makeSession(true)
    @ObservationIgnored private let makeSession: @Sendable (Bool) -> URLSession
    @ObservationIgnored private var urlSession: URLSession
    @ObservationIgnored private var backgroundedAt: Date?
    @ObservationIgnored private var didBeginLaunch = false

    static let lockKey = "security.biometricLock"
    static let lockAfter: TimeInterval = 5 * 60

    /// - Parameter makeSession: liefert die URLSession; `true` = Demo-Transport.
    public init(secrets: any SecretStore, defaults: UserDefaults = .standard, registry: TrustRegistry = .shared,
                makeSession: @escaping @Sendable (Bool) -> URLSession) {
        self.secrets = secrets
        self.defaults = defaults
        self.registry = registry
        self.makeSession = makeSession
        let session = makeSession(false)
        self.networkSession = session
        self.urlSession = session
        self.api = APIClient(session: session, registry: registry)
        self.directory = ServerDirectory(defaults: defaults)
        self.live = LiveStore()
        live.onAuthExpired = { [weak self] in self?.sessionExpired() }
    }

    // MARK: - Abgeleitet

    public var server: ServerProfile? { directory.active }
    public var role: User.Role? { user?.role }
    public var canControl: Bool { user?.role.canControl ?? false }
    public var canConfigure: Bool { user?.role.canConfigure ?? false }
    public var isDemo: Bool { server?.isDemo ?? false }
    public var serverVersion: String? { health?.version ?? server?.lastVersion }
    public var compatibility: Compatibility { .evaluate(serverVersion: serverVersion) }
    public func supports(_ feature: Compatibility.Feature) -> Bool {
        Compatibility.supports(feature, serverVersion: serverVersion)
    }

    /// Face ID beim Öffnen verlangen (Token nur nach Bestätigung lesbar).
    public var biometricLock: Bool { defaults.bool(forKey: Self.lockKey) }

    // MARK: - Start

    /// Liefert nur beim ersten Aufruf `true`: Mehrere Fenster (iPad) teilen
    /// sich die Sitzung und dürfen sie nicht doppelt starten.
    public func beginLaunch() -> Bool {
        guard !didBeginLaunch else { return false }
        didBeginLaunch = true
        return true
    }

    public func launch() async {
        registry.load(directory.pins)
        if directory.active == nil, let legacy = directory.migrateLegacy() {
            // Token der Vorgänger-App mitnehmen.
            if let token = try? await secrets.secret(for: "auth-token") {
                try? await secrets.setSecret(token, for: legacy.tokenKey, protected: false)
                try? await secrets.removeSecret(for: "auth-token")
            }
        }
        guard let server = directory.active else {
            phase = .needsServer
            return
        }
        await open(server)
    }

    /// Sitzung für einen gespeicherten Server aufbauen.
    private func open(_ server: ServerProfile) async {
        live.reset()
        user = nil
        health = nil
        isOffline = false
        configureTransport(demo: server.isDemo)
        await api.configure(baseURL: server.url, token: nil)

        let token: String?
        do {
            token = try await secrets.secret(for: server.tokenKey)
        } catch {
            phase = .locked
            return
        }
        guard let token else {
            phase = .signedOut
            Task { await self.refreshServerInfo() }
            return
        }
        await api.setToken(token)
        do {
            user = try await api.me()
            phase = .signedIn
            startLive(token: token)
            Task { await self.refreshServerInfo() }
            Task { await self.rollToken() }
        } catch APIError.unauthorized {
            try? await secrets.removeSecret(for: server.tokenKey)
            phase = .signedOut
            notice = Notice(.info, Loc.t("Bitte erneut anmelden", "Please sign in again"),
                            message: Loc.t("Die Anmeldung ist abgelaufen.", "Your session has expired."))
        } catch {
            // Server gerade weg (Router-Neustart, unterwegs ohne VPN): angemeldet bleiben.
            isOffline = true
            phase = .signedIn
            startLive(token: token)
        }
    }

    /// Nach Face-ID-Abbruch erneut versuchen.
    public func unlock() async {
        guard let server = directory.active else { return }
        await open(server)
    }

    // MARK: - Server finden

    /// Prüft eine eingegebene Adresse. Liefert den Einrichtungszustand gleich mit.
    public func probe(_ address: String) async throws -> ServerProbe.Result {
        guard let url = ServerAddress.normalize(address) else { throw APIError.invalidAddress }
        configureTransport(demo: false)
        let probe = ServerProbe(session: urlSession, registry: registry)
        do {
            return try await probe.check(url)
        } catch APIError.unreachable(let reason) where url.scheme == "http" && url.port == ServerAddress.defaultPort {
            // Port 8000 ist der zweite übliche Docker-Port.
            var comps = URLComponents(url: url, resolvingAgainstBaseURL: false)
            comps?.port = 8000
            if let alt = comps?.url, let result = try? await probe.check(alt) { return result }
            throw APIError.unreachable(reason)
        }
    }

    /// Prüfer für die Netzsuche (teilt Session und Zertifikatsliste).
    public var serverProbe: ServerProbe { ServerProbe(session: networkSession, registry: registry) }

    /// Selbstsigniertes Zertifikat nach Bestätigung durch den Nutzer merken.
    public func trust(host: String, fingerprint: String) {
        registry.pin(host: host, fingerprint: fingerprint)
    }

    /// Geprüften Server übernehmen und aktivieren.
    @discardableResult
    public func adopt(_ result: ServerProbe.Result, name: String? = nil) -> ServerProfile {
        let host = result.url.host ?? ""
        let pin = registry.allPins[host.lowercased()]
        let profile = directory.add(ServerProfile(
            name: name?.isEmpty == false ? name! : Self.defaultName(for: result.url),
            url: result.url, fingerprint: pin, lastVersion: result.health.version))
        health = result.health
        setup = result.setup
        phase = .signedOut
        live.reset()
        user = nil
        Task { await api.configure(baseURL: profile.url, token: nil) }
        return profile
    }

    static func defaultName(for url: URL) -> String {
        guard let host = url.host else { return "MinePower" }
        return ServerAddress.isLocalHost(host) ? Loc.t("Zuhause", "Home") : host
    }

    // MARK: - Anmelden

    public func signIn(email: String, password: String) async throws {
        guard let server = directory.active else { throw APIError.notConfigured }
        await api.configure(baseURL: server.url, token: nil)
        let response = try await api.login(email: email.trimmingCharacters(in: .whitespaces), password: password)
        try await finishSignIn(response, email: email, server: server)
    }

    /// Leerer Server: ersten Administrator anlegen.
    public func createAdministrator(name: String, email: String, password: String) async throws {
        guard let server = directory.active else { throw APIError.notConfigured }
        await api.configure(baseURL: server.url, token: nil)
        let response = try await api.createFirstAdmin(SetupRequest(
            email: email.trimmingCharacters(in: .whitespaces), password: password, name: name,
            language: Loc.isEnglish ? "en" : "de"))
        try await finishSignIn(response, email: email, server: server)
    }

    private func finishSignIn(_ response: TokenResponse, email: String, server: ServerProfile) async throws {
        try await secrets.setSecret(response.token, for: server.tokenKey, protected: biometricLock)
        await api.setToken(response.token)
        directory.update(server.id) {
            $0.email = email
            $0.lastUserName = response.user.name
        }
        user = response.user
        isOffline = false
        phase = .signedIn
        startLive(token: response.token)
        await refreshServerInfo()
    }

    public func signOut() async {
        if let server = directory.active {
            try? await secrets.removeSecret(for: server.tokenKey)
        }
        await api.setToken(nil)
        live.reset()
        user = nil
        phase = directory.active == nil ? .needsServer : .signedOut
    }

    /// Server wechseln (Liste in Einstellungen oder beim Verbinden).
    public func switchServer(to id: UUID) async {
        directory.activate(id)
        guard let server = directory.active else {
            phase = .needsServer
            return
        }
        await open(server)
    }

    public func removeServer(_ id: UUID) async {
        if let profile = directory.profiles.first(where: { $0.id == id }) {
            try? await secrets.removeSecret(for: profile.tokenKey)
            registry.unpin(host: profile.host)
        }
        let wasActive = directory.activeID == id
        directory.remove(id)
        if wasActive {
            if let next = directory.active { await open(next) } else {
                live.reset()
                user = nil
                phase = .needsServer
            }
        }
    }

    /// "Weiteren Server hinzufügen": zurück zur Suche, ohne den aktiven zu verlieren.
    public func addAnotherServer() {
        live.stop()
        phase = .needsServer
    }

    /// Suche abbrechen und zum gespeicherten Server zurück.
    public func cancelAddingServer() async {
        guard let server = directory.active else { return }
        await open(server)
    }

    // MARK: - Demo

    /// Offline-Demo ohne Server: eingebaute Beispielanlage.
    public func startDemo() async {
        let profile = directory.add(ServerProfile(name: Loc.t("Demo-Anlage", "Demo system"),
                                                  url: DemoBackend.baseURL, isDemo: true))
        try? await secrets.setSecret(DemoBackend.token, for: profile.tokenKey, protected: false)
        await open(profile)
    }

    // MARK: - Sperre

    public func setBiometricLock(_ enabled: Bool) async throws {
        guard let server = directory.active else { return }
        if let token = try await secrets.secret(for: server.tokenKey) {
            try await secrets.setSecret(token, for: server.tokenKey, protected: enabled)
        }
        defaults.set(enabled, forKey: Self.lockKey)
    }

    // MARK: - Lebenszyklus

    public func enterBackground() {
        backgroundedAt = .now
        live.stop()
    }

    public func enterForeground() async {
        let since = backgroundedAt
        backgroundedAt = nil
        guard phase == .signedIn else { return }
        if biometricLock, let since, Date.now.timeIntervalSince(since) > Self.lockAfter {
            live.reset()
            phase = .locked
            return
        }
        // Nur neu verbinden, wenn wirklich im Hintergrund (Kontrollzentrum,
        // Mitteilungen lassen die Verbindung bestehen). Das Token kommt aus dem
        // Speicher, nicht erneut aus dem Schlüsselbund: sonst fragte Face ID
        // bei jeder Rückkehr.
        guard since != nil || live.connection == .idle, let token = await api.token else { return }
        startLive(token: token)
        if isOffline { await refreshUser() }
    }

    /// Nutzer, Version und Einrichtungszustand neu laden.
    public func refreshServerInfo() async {
        if let health = try? await api.health() {
            self.health = health
            isOffline = false
            if let server = directory.active, server.lastVersion != health.version {
                directory.update(server.id) { $0.lastVersion = health.version }
            }
        }
        setup = try? await api.setupState()
    }

    public func refreshUser() async {
        do {
            user = try await api.me()
            isOffline = false
        } catch let error as APIError {
            handle(error)
        } catch {}
    }

    /// Fehler zentral behandeln: 401 meldet ab, Abbruch wird verschluckt.
    public func handle(_ error: APIError) {
        switch error {
        case .unauthorized: sessionExpired()
        case .cancelled: break
        default:
            if error.isTransient { isOffline = true }
            notice = Notice(.error, error.errorDescription ?? Loc.t("Fehler", "Error"))
        }
    }

    public func handle(_ error: any Error) {
        handle(APIClient.map(error, host: server?.host ?? ""))
    }

    public func show(_ notice: Notice) { self.notice = notice }

    // MARK: - Intern

    private func startLive(token: String) {
        guard let server = directory.active else { return }
        let socket = server.isDemo ? nil : LiveSocket(session: urlSession)
        live.start(api: api, socket: socket, baseURL: server.url, token: token,
                   pollInterval: server.isDemo ? .seconds(3) : .seconds(10))
    }

    private func configureTransport(demo: Bool) {
        urlSession = demo ? demoSession : networkSession
        api = APIClient(session: urlSession, registry: registry)
    }

    /// Token verlängern (30 Tage gültig); Fehler sind hier egal.
    private func rollToken() async {
        guard let server = directory.active, !server.isDemo,
              let fresh = try? await api.refreshToken() else { return }
        await api.setToken(fresh.token)
        try? await secrets.setSecret(fresh.token, for: server.tokenKey, protected: biometricLock)
    }

    private func sessionExpired() {
        guard phase == .signedIn else { return }
        Task {
            await signOut()
            notice = Notice(.info, Loc.t("Bitte erneut anmelden", "Please sign in again"),
                            message: Loc.t("Die Anmeldung ist abgelaufen.", "Your session has expired."))
        }
    }
}
