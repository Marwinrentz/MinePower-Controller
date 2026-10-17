import Charts
import MinePowerKit
import SwiftUI

/// Batterie – Portierung von `frontend/src/pages/Battery.tsx`.
///
/// Ladestand groß mit den Grenzen, die das Verhalten bestimmen (Reserve,
/// "zuerst bis"), Steuerung von Hand mit Dauer (endet immer von selbst) und
/// Ladestandsverlauf.
struct BatteryView: View {
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router
    @State private var settings: Resource<SettingsEnvelope>?

    var body: some View {
        Group {
            if let snapshot = session.live.snapshot {
                let device = snapshot.devices(.battery).first(where: \.enabled)
                if snapshot.battery == nil, device == nil {
                    Page {
                        Card {
                            EmptyState(icon: .battery, title: "Keine Batterie gefunden",
                                       message: "Ladestand vom Hybrid-Wechselrichter. Steuerung: Batteriegerät einrichten.")
                        }
                    }
                } else {
                    Page(refresh: { try? await session.live.refresh() }) {
                        BatteryHero(snapshot: snapshot, device: device,
                                    regulation: settings?.value?.section("regulation") ?? [:])
                        Card {
                            VStack(alignment: .leading, spacing: Space.l) {
                                CardHead("Von Hand steuern", subtitle: String(localized: "Danach Automatik"))
                                if let device {
                                    ManualControl(snapshot: snapshot, device: device,
                                                  regulation: settings?.value?.section("regulation") ?? [:])
                                } else {
                                    Callout(.info, String(localized: "Nur lesend: Werte vom Wechselrichter. Steuerung nur mit eigenem Batteriegerät."))
                                }
                            }
                        }
                        Card {
                            VStack(alignment: .leading, spacing: Space.m) {
                                CardHead(title: "Ladestand 24 h") {
                                    if session.canConfigure {
                                        Button {
                                            router.showSettings(.battery)
                                        } label: {
                                            HStack(spacing: 4) { Icon(.settings, size: 16); Text("Batterie-Einstellungen") }
                                        }
                                        .buttonStyle(.web(.ghost, size: .small, fullWidth: false))
                                    }
                                }
                                SocChart()
                            }
                        }
                    }
                }
            } else {
                Page { SkeletonCards(count: 1, height: 300) }
            }
        }
        .navigationTitle("Batterie")
        .toolbar { BrandToolbar() }
        .task {
            if settings == nil {
                let api = session.api
                settings = Resource { try await api.settings() }
            }
            await settings?.load(maxAge: 60)
        }
    }
}

// MARK: - Kopf

private struct BatteryHero: View {
    let snapshot: Snapshot
    let device: SnapshotDevice?
    let regulation: JSONObject

    var body: some View {
        let fmt = Fmt()
        let battery = snapshot.battery
        let soc = battery?.soc ?? device?.soc
        let power = battery?.power ?? device?.values.double("power") ?? 0
        let reserve = (snapshot.batteryReserveSoc ?? 20).rounded()
        let priority = snapshot.batteryPrioritySoc ?? regulation.double("battery_priority_soc") ?? 100
        let plan = snapshot.batteryPlan
        let readback = device?.values.bool("mode_known") == true ? device?.values.string("mode") : nil
        let differs = readback != nil && device?.targetMode != nil && readback != device?.targetMode
        var marks = [BatteryGaugeView.Mark(soc: reserve, label: String(localized: "Reserve \(Int(reserve)) %"), kind: .reserve)]
        if priority > 0, priority < 100 {
            marks.append(.init(soc: priority, label: String(localized: "zuerst bis \(Int(priority)) %"), kind: .priority))
        }
        let status = snapshot.batteryGridCharging == true ? String(localized: "Lädt aus dem Netz")
            : power > 30 ? String(localized: "Lädt \(fmt.watts(power))")
            : power < -30 ? String(localized: "Entlädt \(fmt.watts(-power))") : String(localized: "Ruht")
        return Card {
            VStack(alignment: .leading, spacing: Space.l) {
                HStack(alignment: .center, spacing: Space.xl) {
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text(soc.map { "\(Int($0.rounded()))" } ?? "–")
                            .font(Typo.display).monospacedDigit().foregroundStyle(Web.textStrong)
                            .contentTransition(.numericText())
                        Text("%").font(Typo.title2).foregroundStyle(Web.textDim)
                    }
                    .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: Space.s) {
                        Text(status).font(Typo.title3).foregroundStyle(Web.textStrong)
                        FlowLayout(spacing: 6) {
                            Badge(text: plan?.label ?? Explanation.batteryAction("auto"),
                                  tone: plan != nil && plan?.intent != "auto" ? DomainColor(light: 0x0F7A4D, dark: 0x3DDC97) : nil)
                            if snapshot.batteryManual != nil { Badge(text: String(localized: "von Hand"), tone: Palette.info) }
                        }
                    }
                }
                BatteryGaugeView(soc: soc, power: power, gridCharging: snapshot.batteryGridCharging == true,
                                 marks: marks, label: String(localized: "Batterie"))
                if let plan, plan.intent != "auto" {
                    Text(plan.reason).font(Typo.bodyMedium).foregroundStyle(Web.textStrong)
                        .padding(.horizontal, Space.m).padding(.vertical, Space.s)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Web.accentSoft, in: .rect(cornerRadius: Radius.medium, style: .continuous))
                }
                if let gate = snapshot.batteryGateReason, !gate.isEmpty {
                    Text(gate).font(Typo.subheadline).foregroundStyle(Web.textDim)
                }
                StatGrid(minimum: 150) {
                    Stat(label: "Leistung", value: fmt.watts(abs(power)),
                         sub: power > 30 ? String(localized: "lädt") : power < -30 ? String(localized: "entlädt") : String(localized: "Ruhe"))
                    if let eta = snapshot.batteryETA {
                        Stat(label: power > 0 ? "Voll in" : "Reserve in", value: fmt.duration(eta))
                    }
                    if let readback {
                        Stat(label: "Modus im Wechselrichter", value: Self.modeName(readback),
                             sub: differs ? (device?.modeSettling == true ? String(localized: "Wird übernommen")
                                                                         : String(localized: "Abweichung, wird erneut gesendet")) : nil)
                    }
                    if let low = device?.values.double("min_soc"), let high = device?.values.double("max_soc") {
                        let split = device?.inverterReserve.flatMap { $0 + 0.5 < reserve ? $0 : nil }
                        Stat(label: "SoC-Grenzen im Wechselrichter", value: "\(Int(low.rounded()))–\(Int(high.rounded())) %",
                             sub: split.map { String(localized: "WR \(Int($0.rounded())) %, MinePower \(Int(reserve)) %") })
                    }
                }
            }
        }
    }

    static func modeName(_ mode: String) -> String {
        switch mode {
        case "auto": String(localized: "Eigenverbrauch")
        case "force_charge": String(localized: "Zwangsladen")
        case "force_discharge": String(localized: "Zwangsentladen")
        case "hold": String(localized: "gesperrt")
        default: mode
        }
    }
}

// MARK: - Von Hand steuern

private struct ManualControl: View {
    let snapshot: Snapshot
    let device: SnapshotDevice
    let regulation: JSONObject
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router
    @State private var pick: BatteryCommand.Action = .noDischarge
    @State private var power: Double = 3000
    @State private var minutes = 60
    @State private var target: Double = 100
    @State private var busy = false
    @State private var confirm: BatteryCommand.Action?

    static let durations = [15, 30, 60, 120, 240]

    private var maxPower: Double { max(500, device.maxPowerW ?? 5000) }
    private var reserve: Double { (snapshot.batteryReserveSoc ?? 20).rounded() }
    private var manual: BatteryManual? {
        guard let m = snapshot.batteryManual, m.deviceId == nil || m.deviceId == device.id else { return nil }
        return m
    }

    var body: some View {
        let controllable = device.controlEnabled != false && device.controlCapable != false
        Group {
            if !controllable {
                Callout(tone: .info, title: String(localized: "Von Hand steuern"),
                        text: device.controlCapable == false ? String(localized: "Dieser Treiber kann den Speicher nur lesen.")
                                                             : String(localized: "Steuerung nicht freigeschaltet, nur lesend")) {
                    if device.controlCapable != false, session.canConfigure {
                        Button("Steuerung freischalten") { router.showSettings(.devices) }
                            .buttonStyle(.web(.normal, size: .small, fullWidth: false))
                    }
                }
            } else if let manual {
                running(manual)
            } else {
                chooser
            }
        }
        .disabled(!session.canControl)
        .onAppear {
            power = min(maxPower, regulation.double("battery_manual_power_w") ?? 3000)
        }
        .confirmationDialog(confirm.map { "\(Explanation.batteryAction($0.rawValue))?" } ?? "",
                            isPresented: Binding(get: { confirm != nil }, set: { if !$0 { confirm = nil } }),
                            titleVisibility: .visible) {
            if let action = confirm {
                Button(Explanation.batteryAction(action.rawValue)) { Task { await send(action) } }
            }
        } message: {
            Text("Dauer \(Fmt().duration(Double(minutes * 60))), danach Automatik")
        }
    }

    private func running(_ manual: BatteryManual) -> some View {
        let action = Self.action(manual)
        return VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: Space.s) {
                Icon(action.icon, size: 18)
                Text(Explanation.batteryAction(action.rawValue)).font(Typo.title3)
            }
            .foregroundStyle(Web.textStrong)
            HStack(spacing: 0) {
                Countdown(remaining: manual.remainingS, fallback: "")
                if let target = manual.targetSoc, action == .charge || action == .discharge {
                    Text(" · bis \(Int(target.rounded())) %").font(Typo.title).foregroundStyle(Web.textStrong)
                }
            }
            Button {
                Task { await send(.auto) }
            } label: {
                HStack(spacing: 6) {
                    if busy { ProgressView() } else { Icon(.auto, size: 18) }
                    Text("Zurück auf Automatik")
                }
            }
            .buttonStyle(.web(.normal, size: .large))
            .disabled(busy)
            .padding(.top, Space.m)
        }
        .padding(.horizontal, Space.l)
        .padding(.vertical, Space.m)
        .background(Palette.battery.fill.opacity(0.12), in: .rect(cornerRadius: Radius.large, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.large, style: .continuous).strokeBorder(Palette.battery.fill.opacity(0.4), lineWidth: 1))
    }

    private var chooser: some View {
        let fmt = Fmt()
        let maxMinutes = regulation.int("battery_manual_max_min") ?? 240
        let needsPower = pick == .charge || pick == .discharge
        return VStack(alignment: .leading, spacing: Space.l) {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: Space.s)], spacing: Space.s) {
                ForEach([BatteryCommand.Action.noDischarge, .hold, .charge, .discharge], id: \.self) { action in
                    ModeTile(action: action, active: pick == action) { pick = action }
                }
            }
            Field(label: "Dauer") {
                SegmentGrid(options: Self.durations.filter { $0 <= maxMinutes }.map {
                    .init(value: $0, title: fmt.duration(Double($0 * 60)))
                }, selection: minutes, minimum: 80) { minutes = $0 }
            }
            if needsPower {
                StepSlider(label: "Leistung", value: $power, range: 500...maxPower, step: 100, tone: Palette.battery.fill,
                           format: { fmt.watts($0) }) { _ in }
                StepSlider(label: pick == .charge ? "Laden bis" : "Entladen bis", value: $target,
                           range: pick == .charge ? 20...100 : min(reserve, 90)...95, step: 5, tone: Palette.battery.fill,
                           format: { "\(Int($0)) %" }) { _ in }
            }
            Button {
                confirm = pick
            } label: {
                HStack(spacing: 6) {
                    if busy { ProgressView().tint(Web.accentText) } else { Icon(pick.icon, size: 18) }
                    Text("\(Explanation.batteryAction(pick.rawValue)) für \(fmt.duration(Double(minutes * 60)))")
                }
            }
            .buttonStyle(.web(.primary, size: .large))
            .disabled(busy)
            if let last = snapshot.batteryManualLast {
                Text("Zuletzt beendet: \(last.reason)").font(Typo.subheadline).foregroundStyle(Web.textDim)
            }
        }
        .onChange(of: pick) { _, new in
            if new == .discharge { target = max(reserve, min(target, 95)) }
        }
    }

    private func send(_ action: BatteryCommand.Action) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        let command: BatteryCommand
        switch action {
        case .auto:
            command = BatteryCommand(action: .auto)
        case .charge, .discharge:
            command = BatteryCommand(action: action, powerW: power, minutes: Double(minutes),
                                     targetSoc: action == .charge ? target : max(reserve, target), deviceId: device.id)
        default:
            command = BatteryCommand(action: action, minutes: Double(minutes), deviceId: device.id)
        }
        do {
            _ = try await session.api.batteryCommand(command)
            try? await session.live.refresh()
            session.show(Notice(.success, action == .auto ? String(localized: "Zurück auf Automatik") : String(localized: "Befehl gesendet")))
        } catch {
            session.handle(error)
        }
    }

    static func action(_ manual: BatteryManual) -> BatteryCommand.Action {
        if let intent = manual.intent, let action = BatteryCommand.Action(rawValue: intent), action != .auto { return action }
        switch manual.mode {
        case "force_discharge": return .discharge
        case "hold": return .hold
        default: return .charge
        }
    }
}

/// `.manual-mode`: Kachel mit Symbol, Titel und Erklärung.
private struct ModeTile: View {
    let action: BatteryCommand.Action
    let active: Bool
    let select: () -> Void

    var body: some View {
        Button(action: select) {
            VStack(alignment: .leading, spacing: 4) {
                Icon(action.icon, size: 20).foregroundStyle(active ? Palette.accent : Web.textDim)
                Text(Explanation.batteryAction(action.rawValue)).font(Typo.headline).foregroundStyle(Web.textStrong)
                Text(action.help).font(Typo.subheadline).foregroundStyle(Web.textDim).multilineTextAlignment(.leading)
            }
            .padding(Space.m)
            .frame(maxWidth: .infinity, minHeight: 88, alignment: .topLeading)
            .background(active ? Web.accentSoft : Web.surface2, in: .rect(cornerRadius: Radius.large, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.large, style: .continuous)
                .strokeBorder(active ? Palette.accent : Web.line, lineWidth: 1.5))
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(active ? .isSelected : [])
    }
}

extension BatteryCommand.Action {
    var icon: WebIcon {
        switch self {
        case .auto: .auto
        case .noDischarge: .shield
        case .hold: .lock
        case .charge: .bolt
        case .discharge: .arrowDown
        }
    }

    var help: String {
        switch self {
        case .charge: String(localized: "Aus Sonne und Netz")
        case .discharge: String(localized: "Ins Haus, bis zur Reserve")
        case .hold: String(localized: "Weder laden noch entladen")
        case .noDischarge: String(localized: "Laden mit PV, keine Entladung")
        case .auto: ""
        }
    }
}

// MARK: - Verlauf

/// `SocChart`: Ladestand 24 h, Fläche in Batteriefarbe, 0–100 %.
struct SocChart: View {
    var range: HistoryRange = .day
    @Environment(AppSession.self) private var session
    @State private var points: [HistoryPoint]?

    var body: some View {
        Group {
            if let points, !points.isEmpty {
                Chart(points) { p in
                    AreaMark(x: .value("Zeit", p.time), y: .value("%", p.value))
                        .foregroundStyle(Palette.battery.fill.opacity(0.2))
                        .interpolationMethod(.catmullRom)
                    LineMark(x: .value("Zeit", p.time), y: .value("%", p.value))
                        .foregroundStyle(Palette.battery.fill)
                        .interpolationMethod(.catmullRom)
                }
                .chartYScale(domain: 0...100)
                .webAxes(unit: "%")
                .frame(height: 220)
            } else if points != nil {
                ChartEmpty(text: String(localized: "Keine Verlaufsdaten"))
            } else {
                SkeletonCards(count: 1, height: 220)
            }
        }
        .task {
            while !Task.isCancelled {
                points = (try? await session.api.history(range, fields: ["battery_soc"]))?["battery_soc"] ?? points ?? []
                try? await Task.sleep(for: .seconds(60))
            }
        }
    }
}
