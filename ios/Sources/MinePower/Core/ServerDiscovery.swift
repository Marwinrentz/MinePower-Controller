import Foundation
import MinePowerKit
import Network
import Observation

/// Sucht MinePower-Server im Heimnetz.
///
/// Zwei Wege parallel:
/// 1. **Bonjour** (`_minepower._tcp`): sofort, wenn der Server angekündigt
///    wird (z. B. per Avahi-Dienstdatei auf dem NAS, siehe ios/README.md).
///    Steht im TXT-Eintrag `url=…`, wird genau diese Adresse genommen
///    (Name statt IP, übersteht neue DHCP-Adressen).
/// 2. **Netzsuche**: das eigene /24-Netz auf Port 8080 und 8000 abfragen,
///    32 Adressen gleichzeitig, je 1,2 s. Findet jede Docker-Installation
///    ohne Zusatzeinrichtung, braucht aber bis zu ~20 s.
///
/// Der erste Zugriff löst die iOS-Abfrage "Lokales Netzwerk" aus. Solange sie
/// offen ist, scheitert jede Anfrage; sobald der Browser danach bereit
/// meldet, startet die Netzsuche automatisch noch einmal.
@MainActor
@Observable
final class ServerDiscovery {
    struct Found: Identifiable, Hashable {
        var result: ServerProbe.Result
        var via: Source
        var id: URL { result.url }
    }

    enum Source { case bonjour, scan }

    private(set) var found: [Found] = []
    private(set) var isScanning = false
    private(set) var progress: Double = 0
    private(set) var ownAddress: String?

    @ObservationIgnored private var browser: NWBrowser?
    @ObservationIgnored private var scanTask: Task<Void, Never>?
    @ObservationIgnored private var probe: ServerProbe?
    /// Jeder Start bekommt eine neue Nummer; ältere Läufe dürfen nichts mehr ändern.
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var resolved: Set<NWEndpoint> = []
    @ObservationIgnored private var waitingForPermission = false

    func start(using probe: ServerProbe) {
        guard !isScanning else { return }
        stop()
        generation += 1
        self.probe = probe
        found = []
        resolved = []
        startBonjour()
        startScan()
    }

    func stop() {
        generation += 1
        browser?.cancel()
        browser = nil
        scanTask?.cancel()
        scanTask = nil
        isScanning = false
    }

    // MARK: - Netzsuche

    private func startScan() {
        ownAddress = LocalNetwork.ownIPv4()
        guard let own = ownAddress, let probe else { return }
        let candidates = ServerAddress.scanCandidates(ownAddress: own)
        guard !candidates.isEmpty else { return }
        let run = generation
        isScanning = true
        progress = 0
        scanTask = Task {
            // In Blöcken, damit Fortschritt und Treffer laufend erscheinen.
            let chunks = stride(from: 0, to: candidates.count, by: 64).map {
                Array(candidates[$0..<min($0 + 64, candidates.count)])
            }
            for (index, chunk) in chunks.enumerated() {
                let results = await probe.scan(chunk, maxConcurrent: 32)
                guard run == generation, !Task.isCancelled else { return }
                for result in results { add(result, via: .scan) }
                progress = Double(index + 1) / Double(chunks.count)
            }
            if run == generation { isScanning = false }
        }
    }

    // MARK: - Bonjour

    private func startBonjour() {
        let browser = NWBrowser(for: .bonjourWithTXTRecord(type: "_minepower._tcp", domain: nil), using: .tcp)
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            let items = results.map { result -> (NWEndpoint, String?) in
                var url: String?
                if case .bonjour(let txt) = result.metadata { url = txt["url"] }
                return (result.endpoint, url)
            }
            Task { @MainActor in self?.handle(items) }
        }
        browser.stateUpdateHandler = { [weak self] state in
            Task { @MainActor in self?.browserState(state) }
        }
        browser.start(queue: .main)
        self.browser = browser
    }

    private func handle(_ items: [(NWEndpoint, String?)]) {
        for (endpoint, advertised) in items where !resolved.contains(endpoint) {
            resolved.insert(endpoint)
            let run = generation
            Task {
                let url: URL?
                if let advertised, let parsed = ServerAddress.normalize(advertised) {
                    url = parsed
                } else {
                    url = await Self.resolve(endpoint)
                }
                guard let url, let probe, run == generation,
                      let result = try? await probe.check(url, timeout: 3), run == generation else { return }
                add(result, via: .bonjour)
            }
        }
    }

    /// Erlaubnis "Lokales Netzwerk" erteilt: Netzsuche erneut, falls sie vorher ins Leere lief.
    private func browserState(_ state: NWBrowser.State) {
        switch state {
        case .waiting:
            waitingForPermission = true
        case .ready where waitingForPermission:
            waitingForPermission = false
            if found.isEmpty, let probe {
                scanTask?.cancel()
                isScanning = false
                generation += 1
                self.probe = probe
                startScan()
            }
        default:
            break
        }
    }

    /// Dienst → Adresse: kurz per IPv4 verbinden und die Gegenstelle ablesen.
    /// Höchstens 5 s; hängt die Verbindung (`.waiting`), wird abgebrochen.
    private static func resolve(_ endpoint: NWEndpoint) async -> URL? {
        let parameters = NWParameters.tcp
        if let ip = parameters.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options {
            // IPv6 link-local bräuchte die Schnittstellenkennung in der URL.
            ip.version = .v4
        }
        let connection = NWConnection(to: endpoint, using: parameters)
        let once = OnceFlag()
        return await withCheckedContinuation { continuation in
            let finish: @Sendable (URL?) -> Void = { url in
                guard once.claim() else { return }
                connection.cancel()
                continuation.resume(returning: url)
            }
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    if case .hostPort(let host, let port) = connection.currentPath?.remoteEndpoint {
                        let text = "\(host)".split(separator: "%").first.map(String.init) ?? "\(host)"
                        finish(URL(string: "http://\(text):\(port.rawValue)"))
                    } else {
                        finish(nil)
                    }
                case .waiting, .failed, .cancelled:
                    finish(nil)
                default:
                    break
                }
            }
            connection.start(queue: .global(qos: .userInitiated))
            DispatchQueue.global().asyncAfter(deadline: .now() + 5) { finish(nil) }
        }
    }

    private func add(_ result: ServerProbe.Result, via source: Source) {
        guard !found.contains(where: { $0.result.url == result.url }) else { return }
        found.append(Found(result: result, via: source))
    }
}

/// Einmal-Schalter für Rückrufe, die mehrfach feuern können.
private final class OnceFlag: Sendable {
    private let fired = Locked(false)
    func claim() -> Bool {
        fired.withLock { done in
            defer { done = true }
            return !done
        }
    }
}

enum LocalNetwork {
    /// IPv4-Adresse im WLAN bzw. am Kabel (`en*`), sonst `nil`.
    static func ownIPv4() -> String? {
        var pointer: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&pointer) == 0, let first = pointer else { return nil }
        defer { freeifaddrs(pointer) }
        var candidate: String?
        for entry in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let ifa = entry.pointee
            guard let addr = ifa.ifa_addr, addr.pointee.sa_family == UInt8(AF_INET),
                  (ifa.ifa_flags & UInt32(IFF_UP)) != 0, (ifa.ifa_flags & UInt32(IFF_LOOPBACK)) == 0 else { continue }
            let name = String(cString: ifa.ifa_name)
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            guard getnameinfo(addr, socklen_t(addr.pointee.sa_len), &host, socklen_t(host.count), nil, 0,
                              NI_NUMERICHOST) == 0 else { continue }
            let ip = String(decoding: host.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
            if name.hasPrefix("en") { return ip }
            if candidate == nil, ServerAddress.isLocalHost(ip) { candidate = ip }
        }
        return candidate
    }
}
