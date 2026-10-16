import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Was die Live-Verbindung meldet.
public enum LiveEvent: Sendable {
    case connected
    case snapshot(Snapshot)
    /// Verbindung weg, wird automatisch neu aufgebaut.
    case disconnected
    /// Verbindung offen, aber es kommt nichts mehr an.
    case stale
    /// Token abgelaufen (Schlusscode 4401) – hier hilft kein Reconnect.
    case authExpired
}

/// Live-Snapshots über WebSocket (`/api/ws?token=…`).
///
/// Eine offene Verbindung ist keine Zusage, dass auch etwas ankommt. Ein
/// schlafendes Handy, ein Router-Neustart oder ein abgelaufenes Token
/// hinterlassen einen Socket, der offen aussieht und nie wieder liefert:
///
/// 1. **Schlusscode 4401** → abgelaufenes Token: abmelden statt endlos verbinden.
/// 2. **Rückkehr in den Vordergrund** → sofort neu verbinden (`reconnectNow`).
/// 3. **Totmann-Schaltung** → 45 s ohne Nachricht: neu aufbauen.
public actor LiveSocket {
    static let staleAfter: Duration = .seconds(45)
    static let maxBackoff: Duration = .seconds(30)

    private var task: URLSessionWebSocketTask?
    private var receiveLoop: Task<Void, Never>?
    private var watchdog: Task<Void, Never>?
    private var continuation: AsyncStream<LiveEvent>.Continuation?

    private var baseURL: URL?
    private var token: String?
    private var backoff: Duration = .seconds(1)
    private var lastMessage: ContinuousClock.Instant = .now
    private var wantsConnection = false

    private let session: URLSession
    private let decoder = JSONDecoder.minePower()

    public init(session: URLSession) { self.session = session }

    /// Ereignisstrom; ein Abonnent je Verbindung, über Reconnects hinweg.
    public func events() -> AsyncStream<LiveEvent> {
        continuation?.finish()
        let (stream, continuation) = AsyncStream<LiveEvent>.makeStream(bufferingPolicy: .bufferingNewest(8))
        self.continuation = continuation
        return stream
    }

    public func connect(baseURL: URL, token: String) {
        self.baseURL = baseURL
        self.token = token
        wantsConnection = true
        backoff = .seconds(1)
        teardown()
        openSocket()
    }

    public func disconnect() {
        wantsConnection = false
        teardown()
        continuation?.finish()
        continuation = nil
    }

    public func updateToken(_ token: String) { self.token = token }

    /// Beim Wechsel in den Vordergrund: iOS trennt Sockets beim Sperren.
    public func reconnectNow() {
        guard wantsConnection else { return }
        backoff = .seconds(1)
        teardown()
        openSocket()
    }

    /// `http(s)://host/pfad` → `ws(s)://host/pfad/api/ws?token=…`
    ///
    /// Das Token steht im Query-Parameter, weil `URLSessionWebSocketTask` beim
    /// Handshake keine eigenen Header setzt und das Backend deshalb `?token=`
    /// auswertet. Außerhalb des Heimnetzes daher nur über TLS verbinden.
    public static func socketURL(base: URL, token: String) -> URL? {
        guard var comps = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return nil }
        switch comps.scheme {
        case "https": comps.scheme = "wss"
        case "http": comps.scheme = "ws"
        default: break
        }
        let root = comps.path.hasSuffix("/") ? String(comps.path.dropLast()) : comps.path
        comps.path = root + "/api/ws"
        comps.queryItems = [URLQueryItem(name: "token", value: token)]
        return comps.url
    }

    /// Nachricht dekodieren (öffentlich für Tests).
    public static func decodeSnapshot(_ data: Data, decoder: JSONDecoder = .minePower()) -> Snapshot? {
        guard let envelope = try? decoder.decode(LiveEnvelope.self, from: data), envelope.type == "snapshot" else {
            return nil
        }
        return envelope.data
    }

    // MARK: - Intern

    private func openSocket() {
        guard wantsConnection, let baseURL, let token,
              let url = Self.socketURL(base: baseURL, token: token) else { return }
        let task = session.webSocketTask(with: url)
        self.task = task
        lastMessage = .now
        task.resume()
        receiveLoop = Task { [weak self] in await self?.receiveMessages(on: task) }
        watchdog = Task { [weak self] in await self?.runWatchdog() }
    }

    private func receiveMessages(on task: URLSessionWebSocketTask) async {
        while !Task.isCancelled {
            do {
                let message = try await task.receive()
                lastMessage = .now
                backoff = .seconds(1)
                handle(message)
            } catch {
                guard self.task === task else { return }  // längst ersetzt
                if task.closeCode.rawValue == 4401 {
                    continuation?.yield(.authExpired)
                    wantsConnection = false
                    teardown()
                    return
                }
                continuation?.yield(.disconnected)
                // Nicht `teardown()`: das bräche diesen Task selbst ab, die
                // Wartezeit vor dem Neuverbinden entfiele, und ein
                // ausgefallener Server erzeugte eine Schleife ohne Pause.
                watchdog?.cancel()
                watchdog = nil
                receiveLoop = nil
                self.task = nil
                await scheduleReconnect()
                return
            }
        }
    }

    private func handle(_ message: URLSessionWebSocketTask.Message) {
        let data: Data
        switch message {
        case .string(let text): data = Data(text.utf8)
        case .data(let raw): data = raw
        @unknown default: return
        }
        guard let snapshot = Self.decodeSnapshot(data, decoder: decoder) else { return }
        continuation?.yield(.connected)
        continuation?.yield(.snapshot(snapshot))
    }

    private func runWatchdog() async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(5))
            guard !Task.isCancelled, wantsConnection else { return }
            if ContinuousClock.now - lastMessage > Self.staleAfter {
                continuation?.yield(.stale)
                watchdog = nil  // nicht sich selbst abbrechen
                receiveLoop?.cancel()
                receiveLoop = nil
                task?.cancel(with: .goingAway, reason: nil)
                task = nil
                await scheduleReconnect()
                return
            }
        }
    }

    private func scheduleReconnect() async {
        guard wantsConnection else { return }
        let delay = backoff
        backoff = min(backoff * 2, Self.maxBackoff)
        try? await Task.sleep(for: delay)
        guard wantsConnection, task == nil else { return }
        openSocket()
    }

    private func teardown() {
        receiveLoop?.cancel()
        watchdog?.cancel()
        receiveLoop = nil
        watchdog = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
    }
}

struct LiveEnvelope: Decodable {
    var type: String
    var data: Snapshot
}
