import ActivityKit
import MinePowerKit
import WidgetKit

/// Verbindet den geteilten Stand der Sitzung mit Widgets und Live-Aktivitäten.
@MainActor
enum SharedBridge {
    private static var lastReload = Date.distantPast
    private static var lastHeadline: String?

    static func handle(_ state: WidgetState?) {
        LiveActivities.sync(state)
        // Widgets haben ein Budget: neu laden bei neuer Kernaussage, sonst höchstens alle 5 min.
        let changed = state?.headline != lastHeadline
        guard state == nil || changed || Date.now.timeIntervalSince(lastReload) > 300 else { return }
        lastHeadline = state?.headline
        lastReload = .now
        WidgetCenter.shared.reloadAllTimelines()
    }
}

/// Startet, aktualisiert und beendet die Live-Aktivitäten aus dem geteilten Stand.
@MainActor
enum LiveActivities {
    private typealias Attributes = MinePowerActivityAttributes
    private static var lastUpdate: [Attributes.Kind: Date] = [:]

    static func sync(_ state: WidgetState?) {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { return }
        let fmt = Fmt()
        if let water = state?.water, water.boost {
            let progress = water.tempC.flatMap { temp in water.targetC.map { min(1, max(0, temp / max($0, 1))) } }
            let detail = water.powerW > 50 ? String(localized: "Heizt mit \(fmt.watts(water.powerW))") : String(localized: "Boost läuft")
            upsert(.boost, name: water.name,
                   Attributes.ContentState(value: fmt.celsius(water.tempC), detail: detail, progress: progress, endsAt: water.boostEnds))
        } else {
            end(.boost)
        }
        if let car = state?.car, car.override == "fast" {
            let detail = car.powerW > 50 ? String(localized: "Lädt mit \(fmt.watts(car.powerW))") : car.label
            upsert(.fastCharge, name: car.name,
                   Attributes.ContentState(value: car.soc.map { fmt.percent($0) } ?? fmt.watts(car.powerW), detail: detail,
                                           progress: car.soc.map { $0 / 100 }, endsAt: car.overrideUntil))
        } else {
            end(.fastCharge)
        }
    }

    private static func upsert(_ kind: Attributes.Kind, name: String, _ state: Attributes.ContentState) {
        let content = ActivityContent(state: state, staleDate: state.endsAt)
        if let activity = Activity<Attributes>.activities.first(where: { $0.attributes.kind == kind }) {
            // Höchstens alle 20 s, außer das Ende hat sich verschoben.
            let due = Date.now.timeIntervalSince(lastUpdate[kind] ?? .distantPast) > 20
            guard due || activity.content.state.endsAt != state.endsAt else { return }
            lastUpdate[kind] = .now
            let id = activity.id
            Task.detached { await update(id: id, content) }
        } else {
            // Starten geht nur im Vordergrund; sonst beim nächsten Öffnen.
            if (try? Activity.request(attributes: Attributes(kind: kind, deviceName: name), content: content)) != nil {
                lastUpdate[kind] = .now
            }
        }
    }

    private static func end(_ kind: Attributes.Kind) {
        guard Activity<Attributes>.activities.contains(where: { $0.attributes.kind == kind }) else { return }
        lastUpdate[kind] = nil
        Task.detached { await endAll(kind) }
    }

    // Activity ist nicht Sendable: außerhalb des Hauptakteurs nachschlagen und dort verwenden.
    private nonisolated static func update(id: String, _ content: ActivityContent<Attributes.ContentState>) async {
        await Activity<Attributes>.activities.first { $0.id == id }?.update(content)
    }

    private nonisolated static func endAll(_ kind: Attributes.Kind) async {
        for activity in Activity<Attributes>.activities where activity.attributes.kind == kind {
            await activity.end(nil, dismissalPolicy: .immediate)
        }
    }
}
