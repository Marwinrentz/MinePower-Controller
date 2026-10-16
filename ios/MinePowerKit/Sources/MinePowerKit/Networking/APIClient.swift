import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Fehler, die der Nutzer lesen können muss.
///
/// Klartext statt roher `URLError`-Codes: Die Meldungen landen direkt auf dem
/// Schirm. Das Backend formuliert seine Begründungen (`detail`) selbst im
/// Klartext; die werden durchgereicht.
public enum APIError: LocalizedError, Sendable, Equatable {
    case notConfigured
    case invalidAddress
    case unauthorized
    case forbidden
    case notFound
    case conflict(String)
    case rateLimited
    case validation(message: String, fields: [FieldError])
    case server(status: Int, detail: String?)
    case unreachable(String)
    case timedOut
    case offline
    case untrustedCertificate(host: String, fingerprint: String?)
    /// http zu einer Adresse außerhalb des Heimnetzes (ATS blockiert das).
    case insecureRemote
    case notMinePower
    case decoding(String)
    case cancelled

    public struct FieldError: Sendable, Equatable, Hashable {
        public var key: String
        public var label: String
        public var error: String
    }

    public var errorDescription: String? {
        switch self {
        case .notConfigured: return Loc.t("Keine Serveradresse hinterlegt.", "No server address configured.")
        case .invalidAddress: return Loc.t("Ungültige Adresse.", "Invalid address.")
        case .unauthorized: return Loc.t("Anmeldung abgelaufen oder ungültig.", "Sign-in expired or invalid.")
        case .forbidden: return Loc.t("Keine Berechtigung für diese Aktion.", "Not permitted for this account.")
        case .notFound: return Loc.t("Nicht gefunden.", "Not found.")
        case .conflict(let message): return message
        case .rateLimited: return Loc.t("Zu viele Fehlversuche. Kurz warten.", "Too many failed attempts. Wait a moment.")
        case .validation(let message, _): return message
        case .server(let status, let detail):
            return detail ?? Loc.t("Serverfehler \(status).", "Server error \(status).")
        case .unreachable: return Loc.t("Server nicht erreichbar.", "Server not reachable.")
        case .timedOut: return Loc.t("Zeitüberschreitung. Server antwortet nicht.", "Timed out. Server not responding.")
        case .offline: return Loc.t("Keine Netzverbindung.", "No network connection.")
        case .untrustedCertificate(let host, _):
            return Loc.t("Zertifikat von \(host) nicht vertrauenswürdig.", "Certificate of \(host) is not trusted.")
        case .insecureRemote:
            return Loc.t("Ohne https verbindet sich die App nur im Heimnetz. Nutze https (z. B. Reverse-Proxy) oder ein VPN.",
                         "Without https the app only connects inside your home network. Use https (e.g. a reverse proxy) or a VPN.")
        case .notMinePower: return Loc.t("Unter dieser Adresse läuft kein MinePower.", "No MinePower server at this address.")
        case .decoding(let what): return Loc.t("Unerwartete Antwort vom Server (\(what)).", "Unexpected server response (\(what)).")
        case .cancelled: return nil
        }
    }

    /// Lohnt ein automatischer neuer Versuch?
    public var isTransient: Bool {
        switch self {
        case .unreachable, .timedOut, .offline: return true
        case .server(let status, _): return status >= 500
        default: return false
        }
    }

    public var isAuthFailure: Bool { self == .unauthorized }
}

/// HTTP-Methode der REST-API.
public enum HTTPMethod: String, Sendable {
    case get = "GET", post = "POST", put = "PUT", patch = "PATCH", delete = "DELETE"
}

/// REST-Zugriff auf ein MinePower-Backend.
///
/// Ein Aktor, weil Basis-URL und Token von mehreren Stellen gleichzeitig gelesen
/// und geschrieben werden (Anmeldung, Auffrischen, laufende Abrufe). Die Session
/// wird injiziert: In Tests ein `URLProtocol`-Mock, in der App eine Session mit
/// Zertifikats-Prüfung für selbstsignierte Zertifikate.
public actor APIClient {
    public private(set) var baseURL: URL?
    public private(set) var token: String?
    private let session: URLSession
    private let registry: TrustRegistry
    private let decoder = JSONDecoder.minePower()
    private let encoder = JSONEncoder.minePower()

    public init(session: URLSession, baseURL: URL? = nil, token: String? = nil, registry: TrustRegistry = .shared) {
        self.session = session
        self.baseURL = baseURL
        self.token = token
        self.registry = registry
    }

    public func configure(baseURL: URL?, token: String?) {
        self.baseURL = baseURL
        self.token = token
    }

    public func setToken(_ token: String?) { self.token = token }

    // MARK: - Aufrufe

    public func get<T: Decodable & Sendable>(_ path: String, query: [String: String] = [:],
                                             as type: T.Type = T.self) async throws -> T {
        let data = try await send(.get, path, query: query, body: Optional<Never>.none)
        return try decode(data, as: T.self)
    }

    @discardableResult
    public func send<Body: Encodable & Sendable, T: Decodable & Sendable>(
        _ method: HTTPMethod, _ path: String, body: Body?, query: [String: String] = [:], as type: T.Type = T.self
    ) async throws -> T {
        let data = try await send(method, path, query: query, body: body)
        return try decode(data, as: T.self)
    }

    /// Ohne Antwortkörper (204, `{"ok": true}` …).
    public func perform<Body: Encodable & Sendable>(_ method: HTTPMethod, _ path: String, body: Body?) async throws {
        _ = try await send(method, path, query: [:], body: body)
    }

    public func perform(_ method: HTTPMethod, _ path: String) async throws {
        _ = try await send(method, path, query: [:], body: Optional<Never>.none)
    }

    /// Rohtext (CSV-Export, Diagnosebericht, Sicherung).
    public func text(_ path: String, query: [String: String] = [:]) async throws -> String {
        let data = try await send(.get, path, query: query, body: Optional<Never>.none)
        return String(decoding: data, as: UTF8.self)
    }

    public func rawData(_ path: String, query: [String: String] = [:]) async throws -> Data {
        try await send(.get, path, query: query, body: Optional<Never>.none)
    }

    /// JSON-Rohkörper senden (Import einer Sicherung).
    public func sendRaw(_ method: HTTPMethod, _ path: String, json: Data) async throws -> Data {
        var request = try makeRequest(method, path, query: [:])
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = json
        return try await execute(request)
    }

    // MARK: - Intern

    private func send<Body: Encodable>(_ method: HTTPMethod, _ path: String, query: [String: String],
                                       body: Body?) async throws -> Data {
        var request = try makeRequest(method, path, query: query)
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try encoder.encode(body)
        }
        return try await execute(request)
    }

    private func makeRequest(_ method: HTTPMethod, _ path: String, query: [String: String]) throws -> URLRequest {
        guard let baseURL else { throw APIError.notConfigured }
        let trimmed = path.hasPrefix("/") ? String(path.dropFirst()) : path
        guard var components = URLComponents(url: baseURL.appendingPathComponent(trimmed),
                                             resolvingAgainstBaseURL: false) else {
            throw APIError.invalidAddress
        }
        if !query.isEmpty {
            components.queryItems = query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        guard let url = components.url else { throw APIError.invalidAddress }
        var request = URLRequest(url: url)
        request.httpMethod = method.rawValue
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        // Heimnetz antwortet in Millisekunden; ein schlafendes Auto über einen
        // BLE-Proxy braucht beim Verbindungstest aber bis zu 20 s.
        request.timeoutInterval = 30
        return request
    }

    private func execute(_ request: URLRequest) async throws -> Data {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw Self.map(error, host: request.url?.host ?? "", registry: registry)
        }
        guard let http = response as? HTTPURLResponse else { throw APIError.decoding("kein HTTP") }
        switch http.statusCode {
        case 200..<300: return data
        case 401: throw APIError.unauthorized
        case 403: throw APIError.forbidden
        case 404: throw Self.detail(data).map { APIError.server(status: 404, detail: $0.message) } ?? .notFound
        case 409: throw APIError.conflict(Self.detail(data)?.message ?? "Konflikt")
        case 422:
            let detail = Self.detail(data)
            throw APIError.validation(message: detail?.message ?? "Eingabe ungültig", fields: detail?.fields ?? [])
        case 429: throw APIError.rateLimited
        default: throw APIError.server(status: http.statusCode, detail: Self.detail(data)?.message)
        }
    }

    private func decode<T: Decodable>(_ data: Data, as type: T.Type) throws -> T {
        if T.self == EmptyResponse.self, let empty = EmptyResponse() as? T { return empty }
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decoding(String(describing: T.self))
        }
    }

    static func map(_ error: any Error, host: String, registry: TrustRegistry = .shared) -> APIError {
        if error is CancellationError { return .cancelled }
        if let api = error as? APIError { return api }
        guard let url = error as? URLError else { return .unreachable(error.localizedDescription) }
        switch url.code {
        case .cancelled: return .cancelled
        case .timedOut: return .timedOut
        case .notConnectedToInternet, .dataNotAllowed, .internationalRoamingOff: return .offline
        case .serverCertificateUntrusted, .serverCertificateHasUnknownRoot, .serverCertificateNotYetValid,
             .serverCertificateHasBadDate, .secureConnectionFailed, .clientCertificateRejected:
            return .untrustedCertificate(host: host, fingerprint: registry.lastPresented(for: host))
        default: return .unreachable(url.localizedDescription)
        }
    }

    /// FastAPI: `{"detail": "…"}` oder `{"detail": {"code", "message", "fields": [...]}}`
    /// oder Pydantic-Fehlerliste `{"detail": [{"loc", "msg"}]}`.
    struct Detail {
        var message: String
        var fields: [APIError.FieldError]
    }

    static func detail(_ data: Data) -> Detail? {
        guard let object = try? JSONDecoder().decode([String: JSONValue].self, from: data),
              let detail = object["detail"] else { return nil }
        switch detail {
        case .string(let text):
            return Detail(message: text, fields: [])
        case .object(let obj):
            let fields = (obj["fields"]?.array ?? []).compactMap { item -> APIError.FieldError? in
                guard let f = item.object else { return nil }
                return .init(key: f.string("key") ?? "", label: f.string("label") ?? f.string("key") ?? "",
                             error: f.string("error") ?? "")
            }
            return Detail(message: obj.string("message") ?? obj.string("code") ?? "Fehler", fields: fields)
        case .array(let list):
            let fields = list.compactMap { item -> APIError.FieldError? in
                guard let f = item.object else { return nil }
                let key = f["loc"]?.array?.last?.string ?? ""
                return .init(key: key, label: key, error: f.string("msg") ?? "")
            }
            let message = fields.map { "\($0.label): \($0.error)" }.joined(separator: "; ")
            return Detail(message: message.isEmpty ? "Eingabe ungültig" : message, fields: fields)
        default:
            return nil
        }
    }
}

public struct EmptyResponse: Decodable, Sendable {
    public init() {}
}
