import Foundation
import Observation

/// Live-Zustand der Anlage für alle Bildschirme.
///
/// Quelle ist der WebSocket. Fällt er aus, überbrückt eine REST-Abfrage alle
/// 10 s, bis er wieder steht; ohne Socket (Demo, Tests) wird nur abgefragt.
/// Jede Anzeige kann `age` lesen und so ehrlich zeigen, wie alt die Werte sind.
@MainActor
@Observable
public final class LiveStore {
    public enum Connection: Sendable, Equatable {
        case idle, connecting, live, reconnecting, offline
    }

    public private(set) var snapshot: Snapshot?
    public private(set) var connection: Connection = .idle
    public private(set) var lastUpdate: Date?
    /// Geräte mit laufendem Befehl (Knopf zeigt Fortschritt statt nichts).
    public private(set) var pending: Set<Int> = []

    /// Wird bei Schlusscode 4401 oder 401 gerufen (Token abgelaufen).
    @ObservationIgnored public var onAuthExpired: (@MainActor () -> Void)?
    /// Jede neue Momentaufnahme (für Widgets und Live Activity).
    @ObservationIgnored public var onSnapshot: (@MainActor (Snapshot) -> Void)?

    @ObservationIgnored private var api: APIClient?
    @ObservationIgnored private var socket: LiveSocket?
    @ObservationIgnored private var eventTask: Task<Void, Never>?
    @ObservationIgnored private var pollTask: Task<Void, Never>?
    @ObservationIgnored private var pollInterval: Duration = .seconds(10)

    public init(snapshot: Snapshot? = nil) {
        self.snapshot = snapshot
        if snapshot != nil { lastUpdate = .now; connection = .live }
    }

    /// Alter der angezeigten Werte in Sekunden.
    public func age(now: Date = .now) -> TimeInterval? { lastUpdate.map { now.timeIntervalSince($0) } }

    /// Werte älter als eine Minute gelten als veraltet.
    public var isStale: Bool { (age() ?? .infinity) > 60 }

    public func start(api: APIClient, socket: LiveSocket?, baseURL: URL, token: String,
                      pollInterval: Duration = .seconds(10)) {
        stop()
        self.api = api
        self.socket = socket
        self.pollInterval = pollInterval
        connection = .connecting
        if let socket {
            eventTask = Task { [weak self] in
                let stream = await socket.events()
                await socket.connect(baseURL: baseURL, token: token)
                for await event in stream {
                    guard let self else { return }
                    self.handle(event)
                }
            }
            // Erste Werte sofort per REST, nicht erst mit dem ersten Socket-Takt.
            Task { [weak self] in try? await self?.refresh() }
        } else {
            startPolling()
        }
    }

    public func stop() {
        eventTask?.cancel()
        eventTask = nil
        pollTask?.cancel()
        pollTask = nil
        if let socket { Task { await socket.disconnect() } }
        socket = nil
        connection = .idle
    }

    /// Nach Sitzungsende: alles vergessen.
    public func reset() {
        stop()
        api = nil
        snapshot = nil
        lastUpdate = nil
        pending = []
    }

    /// Rückkehr in den Vordergrund: iOS trennt Sockets beim Sperren.
    public func enterForeground() {
        guard api != nil else { return }
        if let socket {
            Task { await socket.reconnectNow() }
        }
        Task { [weak self] in try? await self?.refresh() }
    }

    /// Einmal per REST nachladen.
    public func refresh() async throws {
        guard let api else { return }
        do {
            let fresh = try await api.liveStatus()
            apply(fresh)
            if socket == nil || connection == .offline { connection = socket == nil ? .live : .reconnecting }
        } catch let error as APIError {
            if error == .unauthorized { onAuthExpired?() }
            if error.isTransient, connection != .live { connection = .offline }
            throw error
        }
    }

    /// Gerätebefehl mit Fortschrittsanzeige; danach sofort neue Werte holen.
    public func perform(_ action: DeviceAction, device id: Int) async throws {
        guard let api else { throw APIError.notConfigured }
        pending.insert(id)
        defer { pending.remove(id) }
        try await api.deviceAction(id, action)
        try? await refresh()
    }

    public func setPaused(_ paused: Bool) async throws {
        guard let api else { throw APIError.notConfigured }
        if paused { try await api.pause() } else { try await api.resume() }
        try? await refresh()
    }

    /// Nur für Vorschauen und Tests.
    public func inject(_ snapshot: Snapshot, connection: Connection = .live) {
        apply(snapshot)
        self.connection = connection
    }

    // MARK: - Intern

    private func handle(_ event: LiveEvent) {
        switch event {
        case .connected:
            connection = .live
            stopPolling()
        case .snapshot(let snapshot):
            apply(snapshot)
            connection = .live
            stopPolling()
        case .disconnected, .stale:
            connection = .reconnecting
            startPolling()
        case .authExpired:
            onAuthExpired?()
        }
    }

    private func apply(_ snapshot: Snapshot) {
        self.snapshot = snapshot
        lastUpdate = .now
        onSnapshot?(snapshot)
    }

    private func startPolling() {
        guard pollTask == nil else { return }
        let interval = pollInterval
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                // Den Abruf selbst nicht mit abbrechen: Ein später Wert schadet nicht,
                // und Foundation unter Linux verklemmt sich, wenn eine laufende
                // Anfrage per Task-Abbruch beendet wird (Tests laufen dort).
                await Task { [weak self] in try? await self?.refresh() }.value
                try? await Task.sleep(for: interval)
            }
        }
    }

    private func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }
}
