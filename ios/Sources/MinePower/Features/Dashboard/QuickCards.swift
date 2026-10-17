import MinePowerKit
import SwiftUI

/// Schnellkarten der Übersicht (`QuickWater`, `QuickCar`, `QuickBattery` im Web).
struct QuickCards: View {
    let snapshot: Snapshot
    @Environment(\.horizontalSizeClass) private var sizeClass

    var body: some View {
        let devices = snapshot.enabledDevices
        let heaters = devices.filter { $0.category == .waterHeater }
        let cars = devices.filter { $0.category == .wallbox }
        LazyVGrid(columns: sizeClass == .regular ? [GridItem(.flexible(), spacing: Space.l), GridItem(.flexible(), spacing: Space.l)]
                                                 : [GridItem(.flexible())],
                  alignment: .leading, spacing: Space.l) {
            ForEach(heaters) { QuickWater(device: $0, named: heaters.count > 1) }
            ForEach(cars) { QuickCar(device: $0) }
            if snapshot.battery?.soc != nil { QuickBattery(snapshot: snapshot) }
        }
    }
}

/// `.quick-head`: Symbolkachel, Titel, Unterzeile, "Details".
struct QuickHead: View {
    let icon: WebIcon
    let tone: DomainColor
    let title: String
    let meta: String
    let section: AppSection
    @Environment(Router.self) private var router

    var body: some View {
        HStack(spacing: Space.m) {
            DomainIcon(icon: icon, color: tone)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(Typo.headline).foregroundStyle(Web.textStrong).lineLimit(1)
                Text(meta).font(Typo.subheadlineMedium).foregroundStyle(Web.textDim).monospacedDigit().lineLimit(2)
            }
            Spacer(minLength: Space.s)
            Button {
                router.show(section)
            } label: {
                HStack(spacing: 4) {
                    Text("Details")
                    Icon(.chevron, size: 16)
                }
            }
            .buttonStyle(.web(.ghost, size: .small, fullWidth: false))
            .accessibilityLabel(Text("\(title): Details"))
        }
    }
}

// MARK: - Warmwasser

struct QuickWater: View {
    let device: SnapshotDevice
    var named = false
    @Environment(AppSession.self) private var session
    @State private var confirmBoost = false

    var body: some View {
        let fmt = Fmt()
        let power = device.values.double("power") ?? 0
        let sentence = Explanation.waterSentence(device)
        let busy = session.live.pending.contains(device.id)
        Card {
            VStack(alignment: .leading, spacing: Space.m) {
                QuickHead(icon: .water, tone: Palette.water, title: named ? device.name : String(localized: "Warmwasser"),
                          meta: "\(fmt.celsius(device.temperatureC)) · \(fmt.watts(power))", section: .water)
                Text(sentence.text).font(Typo.body).foregroundStyle(sentence.tone.textColor)
                    .fixedSize(horizontal: false, vertical: true)
                if session.canControl {
                    if device.boost == true {
                        BoostRunning(device: device, busy: busy) { send(.boostOff) }
                    } else {
                        Button {
                            confirmBoost = true
                        } label: {
                            TwoLineLabel(icon: .boost, title: String(localized: "Boost starten"), subtitle: device.boostPlan())
                        }
                        .buttonStyle(.web(.primary, size: .large))
                        .disabled(!device.online || device.boostWouldBePointless || busy)
                        if device.boostWouldBePointless {
                            Text("Wasser wärmer als Boost-Temperatur (\(fmt.celsius(device.boostTempC)))")
                                .font(Typo.subheadline).foregroundStyle(Web.textDim)
                        }
                    }
                }
            }
        }
        .confirmationDialog("Boost starten?", isPresented: $confirmBoost, titleVisibility: .visible) {
            Button("Boost starten") { send(.boost) }
        } message: {
            Text("Volle Leistung, \(device.boostPlan())")
        }
    }

    private func send(_ action: DeviceAction) {
        Task { do { try await session.live.perform(action, device: device.id) } catch { session.handle(error) } }
    }
}

/// `.boost-running`: läuft noch …, mit "Boost beenden".
struct BoostRunning: View {
    let device: SnapshotDevice
    let busy: Bool
    let stop: () -> Void

    var body: some View {
        let fmt = Fmt()
        let endMode = device.boostEndMode ?? "both"
        ViewThatFits(in: .horizontal) {
            HStack(spacing: Space.m) { text; Spacer(minLength: 0); stopButton(fullWidth: false) }
            VStack(alignment: .leading, spacing: Space.m) { text; stopButton(fullWidth: true) }
        }
        .padding(.horizontal, Space.l)
        .padding(.vertical, Space.m)
        .background(Palette.water.fill.opacity(0.12), in: .rect(cornerRadius: Radius.large, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.large, style: .continuous).strokeBorder(Palette.water.fill.opacity(0.4), lineWidth: 1))
        .accessibilityElement(children: .contain)
        .modifier(RemainingTicker(remaining: device.boostState?.remainingS) { remaining in
            if let remaining, endMode != "temp" {
                return String(localized: "noch \(fmt.duration(remaining))")
            }
            return String(localized: "bis \(fmt.celsius(device.boostState?.targetTempC ?? device.boostTempC))")
        })
    }

    private var text: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Icon(.boost, size: 18)
                Text("Boost läuft").font(Typo.headline)
            }
            .foregroundStyle(Palette.water.ink)
            RemainingText()
        }
    }

    private func stopButton(fullWidth: Bool) -> some View {
        Button(action: stop) {
            HStack(spacing: 6) { Icon(.stop, size: 16); Text("Boost beenden") }
        }
        .buttonStyle(.web(.danger, size: .large, fullWidth: fullWidth))
        .disabled(busy)
    }
}

// MARK: - Auto

struct QuickCar: View {
    let device: SnapshotDevice
    @Environment(AppSession.self) private var session
    @State private var confirmFast = false

    var body: some View {
        let fmt = Fmt()
        let state = CarState(device)
        let power = device.values.double("power") ?? 0
        let sentence = Explanation.carSentence(device)
        let override = device.override
        let value = override == "fast" ? "fast" : override == "stop" ? "stop" : "auto"
        var meta = state.label
        if state == .charging { meta += " · \(fmt.watts(power))" }
        if let soc = device.soc { meta += " · \(fmt.percent(soc))" }
        if let until = device.overrideState?.until {
            meta += " · " + (device.overrideState?.kind == "stop"
                ? String(localized: "Aus bis \(fmt.clock(until))") : String(localized: "Sofort bis \(fmt.clock(until))"))
        }
        let busy = session.live.pending.contains(device.id)
        return Card {
            VStack(alignment: .leading, spacing: Space.m) {
                QuickHead(icon: .car, tone: Palette.car, title: device.name, meta: meta, section: .car)
                Text(sentence.text).font(Typo.body).foregroundStyle(sentence.tone.textColor)
                    .fixedSize(horizontal: false, vertical: true)
                if session.canControl {
                    SegmentGrid(options: [
                        .init(value: "auto", title: device.wallboxMode?.title ?? WallboxMode.pvOnly.title, icon: .sun),
                        .init(value: "fast", title: String(localized: "Sofort voll"), icon: .bolt),
                        .init(value: "stop", title: String(localized: "Aus"), icon: .stop),
                    ], selection: value, minimum: 90, pending: busy ? value : nil, disabled: !device.online) { choice in
                        switch choice {
                        case "fast": confirmFast = true
                        case "stop": send(.stop)
                        default: send(.auto)
                        }
                    }
                }
            }
        }
        .confirmationDialog("Sofort laden?", isPresented: $confirmFast, titleVisibility: .visible) {
            Button("Sofort voll") { send(.fast) }
        } message: {
            Text("Volle Leistung aus PV und Netz. Endet bei voll, beim Abstecken oder nach Zeitablauf.")
        }
    }

    private func send(_ action: DeviceAction) {
        Task { do { try await session.live.perform(action, device: device.id) } catch { session.handle(error) } }
    }
}

// MARK: - Batterie

struct QuickBattery: View {
    let snapshot: Snapshot
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router

    var body: some View {
        let fmt = Fmt()
        let battery = snapshot.battery
        let power = battery?.power ?? 0
        Card {
            VStack(alignment: .leading, spacing: Space.m) {
                QuickHead(icon: .battery, tone: Palette.battery, title: String(localized: "Batterie"),
                          meta: fmt.percent(battery?.soc), section: .battery)
                SocBar(soc: battery?.soc ?? 0, power: power)
                Text(status).font(Typo.body).foregroundStyle(Web.text)
                if session.canControl {
                    if snapshot.batteryManual != nil {
                        Button {
                            Task {
                                do {
                                    _ = try await session.api.batteryCommand(BatteryCommand(action: .auto))
                                    try? await session.live.refresh()
                                    session.show(Notice(.success, String(localized: "Zurück auf Automatik")))
                                } catch { session.handle(error) }
                            }
                        } label: {
                            HStack(spacing: 6) { Icon(.auto, size: 16); Text("Automatik") }
                        }
                        .buttonStyle(.web)
                    } else {
                        Button {
                            router.show(.battery)
                        } label: {
                            HStack(spacing: 6) { Icon(.bolt, size: 16); Text("Von Hand steuern") }
                        }
                        .buttonStyle(.web(.ghost))
                    }
                }
            }
        }
    }

    private var status: String {
        let fmt = Fmt()
        let power = snapshot.battery?.power ?? 0
        if let manual = snapshot.batteryManual {
            let what = Explanation.batteryAction(manual.action.rawValue)
            return "\(what) · " + String(localized: "noch \(fmt.duration(manual.remainingS))")
        }
        if let plan = snapshot.batteryPlan, plan.intent != "auto" { return plan.reason }
        if power > 30 { return String(localized: "Lädt \(fmt.watts(power))") }
        if power < -30 { return String(localized: "Entlädt \(fmt.watts(-power))") }
        return String(localized: "Ruht")
    }
}

/// `.soc-bar`: schmaler Ladestand mit laufendem Glanz beim Laden/Entladen.
struct SocBar: View {
    let soc: Double
    let power: Double
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width * max(0.02, min(1, soc / 100))
            ZStack(alignment: .leading) {
                Capsule().fill(Web.surface3)
                Capsule().fill(Palette.battery.fill)
                    .frame(width: width)
                    .overlay {
                        if abs(power) > 30, !reduceMotion {
                            TimelineView(.animation(minimumInterval: 1 / 30)) { timeline in
                                let p = timeline.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 1.6) / 1.6
                                let x = power > 0 ? p : 1 - p
                                LinearGradient(stops: [.init(color: .clear, location: 0), .init(color: .white.opacity(0.45), location: 0.5),
                                                       .init(color: .clear, location: 1)], startPoint: .leading, endPoint: .trailing)
                                    .frame(width: width * 0.5)
                                    .offset(x: (x * 1.5 - 0.5) * width)
                            }
                            .clipShape(Capsule())
                        }
                    }
                    .animation(.smooth(duration: 0.42), value: soc)
            }
        }
        .frame(height: 14)
        .accessibilityElement()
        .accessibilityLabel(Text("Batterie"))
        .accessibilityValue(Text("\(Int(soc.rounded())) %"))
    }
}

// MARK: - Restzeit

/// Restzeit, die zwischen zwei Momentaufnahmen sekündlich herunterzählt (`useCountdown`).
private struct RemainingTicker: ViewModifier {
    let remaining: Double?
    let format: (Double?) -> String
    @State private var anchor = Date.now

    func body(content: Content) -> some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let left = remaining.map { max(0, $0 - context.date.timeIntervalSince(anchor)) }
            content.environment(\.remainingText, format(left))
        }
        .onChange(of: remaining) { anchor = .now }
    }
}

private struct RemainingText: View {
    @Environment(\.remainingText) private var text
    var body: some View {
        Text(text).font(Typo.subheadline).foregroundStyle(Web.textStrong).monospacedDigit()
    }
}

private extension EnvironmentValues {
    @Entry var remainingText: String = ""
}

extension Explanation.Tone {
    /// `.quick-status.tone-*`: Sonne grün, Warnung/Fehler farbig, sonst normal.
    var textColor: Color {
        switch self {
        case .pv: Palette.pv.ink
        case .warn: Palette.warn.ink
        case .error: Palette.error.ink
        default: Web.text
        }
    }
}
