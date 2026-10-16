import MinePowerKit
import SwiftUI

/// Daumenleiste der Übersicht (`ActionBar` im Web): Boost, Sofort laden, Auto aus.
///
/// Die drei Handgriffe, für die man die App am Handy öffnet, unten im
/// Daumenbereich über der Tab-Leiste – hier als schwebende Liquid-Glass-Leiste.
/// Boost und Sofortladen fragen kurz nach; jeder Knopf zeigt, was läuft.
struct ActionBar: View {
    let snapshot: Snapshot
    @Environment(AppSession.self) private var session
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var confirmBoost = false
    @State private var confirmFast = false
    /// Nur eigene, erfolgreiche Befehle geben Rückmeldung (nicht Änderungen von anderswo).
    @State private var feedback = 0

    private var water: SnapshotDevice? { snapshot.enabledDevices.first { $0.category == .waterHeater } }
    private var car: SnapshotDevice? { snapshot.enabledDevices.first { $0.category == .wallbox } }

    var body: some View {
        if sizeClass == .compact, water != nil || car != nil {
            GlassGroup(spacing: 6) {
                HStack(spacing: 6) {
                    if let water { boostButton(water) }
                    if let car {
                        fastButton(car)
                        stopButton(car)
                    }
                }
                .padding(6)
                .glassBar(cornerRadius: Radius.card)
            }
            .padding(.horizontal, 10)
            .padding(.bottom, 6)
            .dynamicTypeSize(...DynamicTypeSize.accessibility1)
            .sensoryFeedback(.impact(weight: .light), trigger: feedback)
            .confirmationDialog("Boost starten?", isPresented: $confirmBoost, titleVisibility: .visible) {
                if let water { Button("Boost starten") { send(.boost, water) } }
            } message: {
                if let water { Text("Volle Leistung, \(water.boostPlan())") }
            }
            .confirmationDialog("Sofort laden?", isPresented: $confirmFast, titleVisibility: .visible) {
                if let car { Button("Sofort voll") { send(.fast, car) } }
            } message: {
                Text("Volle Leistung aus PV und Netz. Endet bei voll, beim Abstecken oder nach Zeitablauf.")
            }
        }
    }

    private func boostButton(_ water: SnapshotDevice) -> some View {
        let on = water.boost == true
        let fmt = Fmt()
        return ActionTile(icon: .boost, title: on ? String(localized: "Boost läuft") : String(localized: "Boost"),
                          detail: on ? water.boostState?.remainingS.map { fmt.duration($0) } ?? fmt.celsius(water.boostTempC)
                                     : fmt.celsius(water.temperatureC),
                          on: on, busy: session.live.pending.contains(water.id)) {
            if on { send(.boostOff, water) } else { confirmBoost = true }
        }
        .disabled(!water.online || (!on && water.boostWouldBePointless))
    }

    private func fastButton(_ car: SnapshotDevice) -> some View {
        let on = car.override == "fast"
        let state = CarState(car)
        return ActionTile(icon: .bolt, title: on ? String(localized: "Lädt sofort") : String(localized: "Sofort laden"),
                          detail: state.label, on: on, busy: session.live.pending.contains(car.id)) {
            if on { send(.auto, car) } else { confirmFast = true }
        }
        .disabled(!car.online || (!state.isPlugged && !on))
    }

    private func stopButton(_ car: SnapshotDevice) -> some View {
        let on = car.override == "stop"
        let until = car.overrideState?.until
        return ActionTile(icon: on ? .play : .stop, title: on ? String(localized: "Auto wieder an") : String(localized: "Auto aus"),
                          detail: on && until != nil ? String(localized: "bis \(Fmt().clock(until!))") : String(localized: "bis Abstecken"),
                          on: on, busy: session.live.pending.contains(car.id)) {
            send(on ? .auto : .stop, car)
        }
        .disabled(!car.online)
    }

    private func send(_ action: DeviceAction, _ device: SnapshotDevice) {
        Task {
            do {
                try await session.live.perform(action, device: device.id)
                feedback += 1
            } catch {
                session.handle(error)
            }
        }
    }
}

/// `.action-bar .btn.action`: Symbol, Titel, kleine Zeile; "an" mit Akzentfläche.
private struct ActionTile: View {
    let icon: WebIcon
    let title: String
    let detail: String
    let on: Bool
    let busy: Bool
    let action: () -> Void
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button(action: action) {
            VStack(spacing: 2) {
                if busy {
                    ProgressView().controlSize(.small).frame(height: 20)
                } else {
                    Icon(icon, size: 20)
                }
                Text(title).font(.custom(Typo.Weight.bold.rawValue, size: 13, relativeTo: .footnote)).lineLimit(1)
                Text(detail).font(Typo.caption2).lineLimit(1)
                    .foregroundStyle(on ? Palette.accent : Web.textDim)
            }
            .foregroundStyle(on ? Palette.accent : Web.textStrong)
            .frame(maxWidth: .infinity, minHeight: 64)
            .padding(.horizontal, 4)
            .background(on ? Web.accentSoft : Web.surface.opacity(0.75), in: .rect(cornerRadius: Radius.large, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.large, style: .continuous)
                .strokeBorder(on ? Palette.accent : Web.lineStrong, lineWidth: 1))
            .opacity(isEnabled ? 1 : 0.5)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}
