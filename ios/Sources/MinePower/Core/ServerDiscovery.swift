import Foundation
import MinePowerKit
import Network
import Observation

/// Sucht MinePower-Server im Heimnetz.
///
/// Zwei Wege parallel:
/// 1. **Bonjour** (`_minepower._tcp`): sofort, wenn der Server angekündigt
///    wird (z. B. per Avahi-Dienstdatei auf dem NAS, siehe ios/README.md).
/// 2. **Netzsuche**: das eigene /24-Netz auf Port 8080 und 8000 abfragen,
///    32 Adressen gleichzeitig, je 1,2 s. Findet jede Docker-Installation
///    ohne Zusatzeinrichtung, braucht aber bis zu ~20 s.
///
/// Der erste Zugriff löst die iOS-Abfrage "Lokales Netzwerk" aus; der Text
/// dazu steht in Info.plist (`NSLocalNetworkUsageDescription`).
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

    func start(using probe: ServerProbe) {
        guard !isScanning else { return }
        stop()
        self.probe = probe
        found = []
        startBonjour()
        startScan()
    }

    func stop() {
        browser?.cancel()
        browser = nil
        scanTask?.cancel()
        scanTask = nil
        isScanning = false
    }

    // MARK: - Netzsuche

    private func startScan() {
        ownAddress = LocalNetwork.ownIPv4()
        guard let own = ownAddress else { return }
        let candidates = ServerAddress.scanCandidates(ownAddress: own)
        guard !candidates.isEmpty, let probe else { return }
        isScanning = true
        progress = 0
        scanTask = Task { [probe] in
            // In Blöcken, damit Fortschritt und Treffer laufend erscheinen.
            let chunks = stride(from: 0, to: candidates.count, by: 64).map {
                Array(candidates[$0..<min($0 + 64, candidates.count)])
            }
            for (index, chunk) in chunks.enumerated() {
                if Task.isCancelled { break }
                let results = await probe.scan(chunk, maxConcurrent: 32)
                for result in results { add(result, via: .scan) }
                progress = Double(index + 1) / Double(chunks.count)
            }
            isScanning = false
        }
    }

    // MARK: - Bonjour

    private func startBonjour() {
        let browser = NWBrowser(for: .bonjour(type: "_minepower._tcp", domain: nil), using: .tcp)
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            for result in results {
                Task { @MainActor in await self?.resolve(result.endpoint) }
            }
        }
        browser.start(queue: .main)
        self.browser = browser
    }

    /// Dienst → Adresse: kurz verbinden und die Gegenstelle ablesen.
    private func resolve(_ endpoint: NWEndpoint) async {
        let address: URL? = await withCheckedContinuation { continuation in
            let connection = NWConnection(to: endpoint, using: .tcp)
            let once = OnceFlag()
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    var url: URL?
                    if case .hostPort(let host, let port) = connection.currentPath?.remoteEndpoint {
                        let raw = "\(host)".split(separator: "%").first.map(String.init) ?? "\(host)"
                        let hostText = raw.contains(":") ? "[\(raw)]" : raw
                        url = URL(string: "http://\(hostText):\(port.rawValue)")
                    }
                    connection.cancel()
                    if once.claim() { continuation.resume(returning: url) }
                case .failed, .cancelled:
                    if once.claim() { continuation.resume(returning: nil) }
                default:
                    break
                }
            }
            connection.start(queue: .global(qos: .userInitiated))
        }
        guard let address, let probe, let result = try? await probe.check(address, timeout: 3) else { return }
        add(result, via: .bonjour)
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
