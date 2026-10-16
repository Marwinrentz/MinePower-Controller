import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Eingaben wie '192.168.1.5", 'minepower.local:8080" oder
/// 'https://energie.example.de/minepower" in eine Basis-URL verwandeln.
public enum ServerAddress {
    /// Standard-Port der Docker-Installation (`docker-compose.yml`: 8080 → 8000).
    public static let defaultPort = 8080
    public static let alternativePorts = [8080, 8000, 80, 443]

    public static func normalize(_ raw: String) -> URL? {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !text.contains(" ") else { return nil }
        if !text.contains("://") {
            // Ohne Schema: Heimnetz-Adressen per http, alles andere per https.
            let hostPart = text.split(separator: "/").first.map(String.init) ?? text
            let looksLocal = isLocalHost(hostPart.split(separator: ":").first.map(String.init) ?? hostPart)
            // Öffentliche Namen immer per https, auch mit Port (z. B. :8443 am Reverse-Proxy):
            // Klartext ins Internet würde ATS ohnehin blockieren.
            text = (looksLocal ? "http://" : "https://") + text
        }
        guard var comps = URLComponents(string: text), let scheme = comps.scheme?.lowercased(),
              ["http", "https"].contains(scheme), let host = comps.host, !host.isEmpty else { return nil }
        comps.scheme = scheme
        if comps.port == nil, scheme == "http", isLocalHost(host) { comps.port = defaultPort }
        // Pfad ohne abschließenden Schrägstrich, aber Unterpfade (Reverse-Proxy) erhalten
        var path = comps.path
        while path.hasSuffix("/") { path.removeLast() }
        comps.path = path
        comps.query = nil
        comps.fragment = nil
        return comps.url
    }

    /// Private IPv4-Bereiche, `.local`, `localhost` und Namen ohne Punkt.
    public static func isLocalHost(_ host: String) -> Bool {
        let h = host.lowercased()
        if h == "localhost" || [".local", ".lan", ".home", ".home.arpa", ".fritz.box"].contains(where: h.hasSuffix) {
            return true
        }
        let parts = h.split(separator: ".").compactMap { Int($0) }
        if parts.count == 4 {
            switch (parts[0], parts[1]) {
            case (10, _), (127, _), (192, 168), (169, 254): return true
            case (172, 16...31): return true
            case (100, 64...127): return true  // CGNAT, z. B. Tailscale
            default: return false
            }
        }
        return !h.contains(".") && !h.contains(":")
    }

    /// Ist die Verbindung unverschlüsselt und verlässt sie das Heimnetz?
    public static func isInsecureRemote(_ url: URL) -> Bool {
        url.scheme == "http" && !isLocalHost(url.host ?? "")
    }

    /// Kandidaten für die automatische Suche im /24-Netz der eigenen Adresse.
    public static func scanCandidates(ownAddress: String, ports: [Int] = [8080, 8000]) -> [URL] {
        let parts = ownAddress.split(separator: ".").compactMap { Int($0) }
        guard parts.count == 4, isLocalHost(ownAddress) else { return [] }
        var out: [URL] = []
        for host in 1...254 where host != parts[3] {
            for port in ports {
                if let url = URL(string: "http://\(parts[0]).\(parts[1]).\(parts[2]).\(host):\(port)") { out.append(url) }
            }
        }
        return out
    }
}

/// Einen Server prüfen, ohne angemeldet zu sein: `GET /api/system/health`.
public struct ServerProbe: Sendable {
    public struct Result: Sendable, Hashable {
        public var url: URL
        public var health: Health
        public var setup: SetupState?
    }

    private let session: URLSession
    private let registry: TrustRegistry
    public init(session: URLSession, registry: TrustRegistry = .shared) {
        self.session = session
        self.registry = registry
    }

    /// Antwortet unter `url` ein MinePower? Bei Erfolg auch den Einrichtungszustand.
    public func check(_ url: URL, timeout: TimeInterval = 6) async throws -> Result {
        let health: Health = try await fetch(url.appendingPathComponent("api/system/health"), timeout: timeout)
        guard health.status == "ok" || health.version != nil else { throw APIError.notMinePower }
        let setup: SetupState? = try? await fetch(url.appendingPathComponent("api/system/setup-state"), timeout: timeout)
        return Result(url: url, health: health, setup: setup)
    }

    /// Schneller Versuch ohne Fehlermeldung (für die Netzsuche).
    public func quickCheck(_ url: URL) async -> Result? {
        try? await check(url, timeout: 1.2)
    }

    private func fetch<T: Decodable>(_ url: URL, timeout: TimeInterval) async throws -> T {
        var request = URLRequest(url: url)
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw APIClient.map(error, host: url.host ?? "", registry: registry)
        }
        guard let http = response as? HTTPURLResponse else { throw APIError.notMinePower }
        guard (200..<300).contains(http.statusCode) else {
            throw http.statusCode == 404 ? APIError.notMinePower : APIError.server(status: http.statusCode, detail: nil)
        }
        do {
            return try JSONDecoder.minePower().decode(T.self, from: data)
        } catch {
            throw APIError.notMinePower
        }
    }

    /// Mehrere Kandidaten parallel prüfen, mit begrenzter Gleichzeitigkeit.
    public func scan(_ candidates: [URL], maxConcurrent: Int = 32) async -> [Result] {
        var found: [Result] = []
        var iterator = candidates.makeIterator()
        await withTaskGroup(of: Result?.self) { group in
            var running = 0
            while running < maxConcurrent, let next = iterator.next() {
                group.addTask { await quickCheck(next) }
                running += 1
            }
            for await result in group {
                if let result { found.append(result) }
                if Task.isCancelled { group.cancelAll(); break }
                if let next = iterator.next() { group.addTask { await quickCheck(next) } }
            }
        }
        return found.sorted { $0.url.absoluteString < $1.url.absoluteString }
    }
}
