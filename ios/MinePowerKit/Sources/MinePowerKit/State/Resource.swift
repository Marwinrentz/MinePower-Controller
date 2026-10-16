import Foundation
import Observation

/// Ein vom Server geladener Wert mit Lade-, Fehler- und Leerzustand.
///
/// Jede Seite zeigt damit dieselben drei Zustände gleich an. Beim Neuladen
/// bleibt der alte Wert sichtbar; ein Fehler ersetzt ihn nicht, sondern wird
/// zusätzlich gemeldet ("zuletzt geladen um …").
@MainActor
@Observable
public final class Resource<Value: Sendable> {
    public enum Phase: Equatable {
        case loading, loaded, failed
    }

    public private(set) var value: Value?
    public private(set) var error: APIError?
    public private(set) var isLoading = false
    public private(set) var loadedAt: Date?

    @ObservationIgnored private let fetch: @Sendable () async throws -> Value
    @ObservationIgnored private var inFlight: Task<Void, Never>?

    public init(_ value: Value? = nil, fetch: @escaping @Sendable () async throws -> Value) {
        self.value = value
        self.fetch = fetch
        if value != nil { loadedAt = .now }
    }

    public var phase: Phase {
        if value != nil { return .loaded }
        if error != nil, !isLoading { return .failed }
        return .loading
    }

    /// Lädt, außer es liegt ein frischer Wert vor (`maxAge`).
    public func load(maxAge: TimeInterval = 0) async {
        if let loadedAt, value != nil, Date.now.timeIntervalSince(loadedAt) < maxAge { return }
        await reload()
    }

    public func reload() async {
        if let inFlight {
            await inFlight.value
            return
        }
        isLoading = true
        let task = Task { @MainActor in
            do {
                let fresh = try await fetch()
                value = fresh
                error = nil
                loadedAt = .now
            } catch {
                let mapped = APIClient.map(error, host: "")
                if mapped != .cancelled { self.error = mapped }
            }
            isLoading = false
        }
        inFlight = task
        await task.value
        inFlight = nil
    }

    /// Lokale Änderung nach erfolgreichem Speichern (spart einen Abruf).
    public func replace(_ value: Value) {
        self.value = value
        error = nil
        loadedAt = .now
    }

    public func update(_ change: (inout Value) -> Void) {
        guard var current = value else { return }
        change(&current)
        value = current
    }
}
