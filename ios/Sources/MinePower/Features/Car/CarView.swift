import MinePowerKit
import SwiftUI

/// Auto – Portierung von `frontend/src/pages/Car.tsx`.
///
/// Garage mit Zustand, Kennzahlen, Betriebsart (sechs Modi), Ladegrenze,
/// Experteneinstellungen und letzte Ladevorgänge.
struct CarView: View {
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router
    @State private var picked: Int?
    @State private var store: DeviceSettingsStore?

    var body: some View {
        Group {
            if let snapshot = session.live.snapshot {
                let cars = snapshot.devices(.wallbox).filter(\.enabled)
                if let car = cars.first(where: { $0.id == picked }) ?? cars.first {
                    Page(refresh: { try? await session.live.refresh(); await store?.devices.reload() }) {
                        DevicePicker(devices: cars, selection: $picked)
                        CarDetail(device: car, store: store)
                            .id(car.id)
                    }
                } else {
                    Page {
                        Card {
                            EmptyState(icon: .car, title: "Kein Auto eingerichtet", message: "Einstellungen → Geräte") {
                                if session.canConfigure {
                                    Button("Gerät anlegen") { router.show(.settings) }
                                        .buttonStyle(.web(.primary, fullWidth: false))
                                }
                            }
                        }
                    }
                }
            } else {
                Page { SkeletonCards(count: 1, height: 300) }
            }
        }
        .navigationTitle("Auto")
        .toolbar { BrandToolbar() }
        .task {
            if store == nil { store = DeviceSettingsStore(api: session.api) }
            await store?.devices.load(maxAge: 30)
        }
    }
}

/// Betriebsarten wie im Web (`MODES` in Car.tsx).
enum CarMode: String, CaseIterable, Identifiable {
    case pvOnly = "pv_only", minPv = "min_pv", pvPrice = "pv_price", target, fast, off
    var id: String { rawValue }

    var title: String {
        switch self {
        case .fast: String(localized: "Sofort voll")
        case .off: String(localized: "Aus")
        default: WallboxMode(rawValue: rawValue)?.title ?? rawValue
        }
    }

    var help: String {
        switch self {
        case .fast: String(localized: "Volle Leistung. Endet bei voll, beim Abstecken oder nach Zeitablauf.")
        case .off: String(localized: "Kein Laden bis Abstecken oder Zeitablauf")
        default: WallboxMode(rawValue: rawValue)?.help ?? ""
        }
    }

    var icon: WebIcon {
        switch self {
        case .off: .stop
        case .pvOnly: .sun
        case .minPv: .plug
        case .pvPrice: .price
        case .target: .clock
        case .fast: .bolt
        }
    }
}

private struct CarDetail: View {
    let device: SnapshotDevice
    let store: DeviceSettingsStore?
    @Environment(AppSession.self) private var session
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var confirmFast = false
    @State private var limit: Double = 80

    var body: some View {
        let state = CarState(device)
        let mode = currentMode
        hero(state)
        Card {
            VStack(alignment: .leading, spacing: Space.m) {
                CardHead("Betriebsart")
                SegmentGrid(options: CarMode.allCases.map { .init(value: $0, title: $0.title, icon: $0.icon) },
                            selection: mode, minimum: 128,
                            pending: session.live.pending.contains(device.id) ? mode : nil,
                            disabled: !session.canControl || !device.online) { choose($0) }
                Text(mode.help)
                    .font(Typo.body)
                    .foregroundStyle(Web.text)
                    .padding(.horizontal, Space.l)
                    .padding(.vertical, Space.m)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Web.surface2, in: .rect(cornerRadius: Radius.medium, style: .continuous))
                    .accessibilityAddTraits(.updatesFrequently)
                if device.canWake, session.canControl {
                    Button {
                        Task {
                            do {
                                try await session.live.perform(.wake, device: device.id)
                                session.show(Notice(.success, String(localized: "Weckbefehl gesendet")))
                            } catch { session.handle(error) }
                        }
                    } label: {
                        HStack(spacing: 6) { Icon(.power, size: 16); Text("Auto wecken") }
                    }
                    .buttonStyle(.web(.ghost, fullWidth: false))
                }
            }
        }
        .confirmationDialog("Sofort laden?", isPresented: $confirmFast, titleVisibility: .visible) {
            Button("Sofort voll") { send(.fast) }
        } message: {
            Text("Volle Leistung aus PV und Netz. Endet bei voll, beim Abstecken oder nach Zeitablauf.")
        }

        if device.hasChargeLimit {
            Card {
                StepSlider(label: "Ladegrenze", value: $limit, range: 50...100, step: 1, tone: Palette.car.fill,
                           format: { "\(Int($0)) %" }) { value in
                    send(.setChargeLimit(Int(value)))
                }
                .disabled(!session.canControl)
            }
            .onAppear { limit = device.chargeLimitSoc ?? 80 }
            .onChange(of: device.chargeLimitSoc) { _, new in if let new { limit = new } }
        }

        if session.canConfigure {
            Card {
                CarAdvanced(device: device, store: store, globalLimit: session.live.snapshot?.priceLimitCt)
            }
        }

        Cycles(deviceID: device.id)
    }

    // MARK: Kopf mit Garage

    @ViewBuilder
    private func hero(_ state: CarState) -> some View {
        Card {
            let layout = sizeClass == .regular ? AnyLayout(HStackLayout(alignment: .center, spacing: Space.xl))
                                               : AnyLayout(VStackLayout(alignment: .leading, spacing: Space.l))
            layout {
                GarageView(look: GarageView.Look(state), soc: device.soc, label: String(localized: "Garage mit Auto"))
                    .frame(maxWidth: .infinity)
                info(state)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func info(_ state: CarState) -> some View {
        let fmt = Fmt()
        let power = device.values.double("power") ?? 0
        let current = device.values.double("current_set")
        let phases = device.values.int("phases_active") ?? device.phasesDetected
        let session = device.session
        let charged = device.values.double("energy_session_kwh") ?? session?.energyKwh
        let solarShare: Double? = {
            guard let s = session, let energy = s.energyKwh, energy > 0.01, let solar = s.solarKwh else { return nil }
            return solar / energy
        }()
        return VStack(alignment: .leading, spacing: Space.m) {
            FlowLayout(spacing: Space.s) {
                Badge(text: state.label, tone: state.badgeTone)
                if let override = device.overrideState {
                    let until = override.until.map { fmt.clock($0) } ?? "–"
                    Badge(text: override.kind == "stop" ? String(localized: "Aus bis \(until)") : String(localized: "Sofort bis \(until)"),
                          tone: DomainColor(light: 0x0F7A4D, dark: 0x3DDC97))
                }
                if device.uncontrolled == true { Badge(text: String(localized: "Befehl nicht übernommen"), tone: Palette.warn) }
            }
            Text(device.name).font(Typo.title).foregroundStyle(Web.textStrong)
            Text(hint(state)).font(Typo.body).foregroundStyle(state == .offline || state == .proxy ? Palette.warn.ink : Web.text)
                .fixedSize(horizontal: false, vertical: true)
            if let note = device.wakeNote, state == .asleep {
                Text(note).font(Typo.subheadline).foregroundStyle(Web.textDim)
            }
            StatGrid {
                Stat(label: "Leistung", value: fmt.watts(power), color: state == .charging ? Palette.car.ink : Web.textStrong)
                Stat(label: "Ladestand", value: device.soc.map { fmt.percent($0) } ?? "–")
                Stat(label: "Strom", value: current != nil && state == .charging ? "\(fmt.number(current!)) A" : "–")
                Stat(label: "Phasen", value: phases.map(String.init) ?? "–")
                Stat(label: "Geladen", value: charged.map { fmt.kwh($0) } ?? "–", sub: String(localized: "seit Ladebeginn"))
                Stat(label: "Solaranteil", value: solarShare.map { fmt.percent($0 * 100) } ?? "–",
                     color: solarShare != nil ? Palette.pv.ink : Web.textStrong)
            }
        }
    }

    /// Kurzer Hinweis zum Zustand (wie `hint` in Car.tsx).
    private func hint(_ state: CarState) -> String {
        let fmt = Fmt()
        let reason = device.reason ?? ""
        switch state {
        case .proxy:
            let addr = device.lastError?.firstMatch(of: /https?:\/\/([\w.-]+(?::\d+)?)/).map { String($0.1) } ?? "–"
            return String(localized: "Gateway \(addr) antwortet nicht")
        case .offline: return String(localized: "Keine Verbindung: \(device.lastError ?? "–")")
        case .asleep: return String(localized: "Schläft, angesteckt. Wecken bei Überschuss.")
        case .away: return String(localized: "Nicht vor Ort")
        case .unknown: return String(localized: "Keine Antwort seit Start")
        case .unplugged: return String(localized: "Nicht angesteckt")
        case .complete: return String(localized: "Ladegrenze erreicht.")
        case .waiting:
            if device.override == "stop" { return String(localized: "Aus bis Abstecken oder Zeitablauf") }
            if reason.contains("nicht ladebereit") || reason.contains("keine Leistung") {
                return String(localized: "Nimmt keinen Strom an (Ladeplan im Fahrzeug)")
            }
            if let match = reason.firstMatch(of: /Start in (\d+) s/) { return String(localized: "Start in \(String(match.1)) s") }
            if reason.contains("Vorrang") { return String(localized: "Auto wartet: Batterie hat Vorrang") }
            return String(localized: "Wartet auf \(fmt.watts(device.startThresholdW ?? device.minPowerW ?? 1400)) Überschuss")
        default:
            return Explanation.carSentence(device).text
        }
    }

    // MARK: Befehle

    private var currentMode: CarMode {
        if device.override == "stop" { return .off }
        if device.override == "fast" { return .fast }
        return CarMode(rawValue: device.mode ?? "pv_only") ?? .pvOnly
    }

    private func choose(_ mode: CarMode) {
        switch mode {
        case .off: send(.stop)
        case .fast: confirmFast = true
        default:
            Task {
                do {
                    if device.override != nil { try await session.live.perform(.auto, device: device.id) }
                    if device.mode != mode.rawValue { try await session.live.perform(.setMode(mode.rawValue), device: device.id) }
                } catch { session.handle(error) }
            }
        }
    }

    private func send(_ action: DeviceAction) {
        Task { do { try await session.live.perform(action, device: device.id) } catch { session.handle(error) } }
    }
}

extension CarState {
    /// Badge-Ton wie im Web: lädt ok, Verbindung weg Fehler, schläft Warnung, sonst Info.
    var badgeTone: DomainColor {
        switch self {
        case .charging: Palette.ok
        case .offline, .proxy: Palette.error
        case .asleep: Palette.warn
        default: Palette.info
        }
    }
}

// MARK: - Experte

private struct CarAdvanced: View {
    let device: SnapshotDevice
    let store: DeviceSettingsStore?
    let globalLimit: Double?
    @Environment(AppSession.self) private var session
    @State private var draft = SettingsDraft()
    @State private var loaded = false

    var body: some View {
        let fmt = Fmt()
        let detected = device.phasesDetected
        let phasesMode = draft.string("phases_mode", "fixed1")
        let setN: Int? = phasesMode == "fixed3" ? 3 : phasesMode == "fixed1" ? 1 : nil
        let minA = draft.number("min_current", 6)
        let minPower = Double(detected ?? setN ?? 1) * minA * 230
        Disclosure(title: "Experte", summary: String(localized: "Ströme, Phasen, Schwellen")) {
            pair {
                Field(label: "Min. Ladestrom") { NumberField(value: binding("min_current", 6), range: 1...32, unit: "A") }
                Field(label: "Max. Ladestrom") { NumberField(value: binding("max_current", 16), range: 1...63, unit: "A") }
            }
            Field(label: "Phasen") {
                SegmentGrid(options: [.init(value: "fixed1", title: String(localized: "1-phasig")),
                                      .init(value: "fixed3", title: String(localized: "3-phasig"))]
                                + (device.can("phase_switch") ? [.init(value: "auto", title: String(localized: "Automatisch"))] : []),
                            selection: phasesMode, minimum: 100) { draft.set("phases_mode", $0) }
            }
            if let detected, let setN, detected != setN {
                Callout(tone: .warn, title: String(localized: "Eingestellt \(setN)-phasig, erkannt \(detected)-phasig")) {
                    Button(detected == 3 ? "3-phasig" : "1-phasig") {
                        draft.set("phases_mode", detected == 3 ? "fixed3" : "fixed1")
                    }
                    .buttonStyle(.web(.normal, size: .small, fullWidth: false))
                }
            } else if let detected {
                Text("Erkannt: \(detected)-phasig, min. \(fmt.watts(Double(detected) * minA * 230))")
                    .font(Typo.subheadline).foregroundStyle(Web.textDim)
            }
            pair {
                Field(label: "Startschwelle", help: draft.number("start_threshold_w", 1400) < minPower ? "= \(fmt.watts(minPower))" : nil) {
                    NumberField(value: binding("start_threshold_w", 1400), range: 0...50_000, unit: "W")
                }
                Field(label: "Startverzögerung") { NumberField(value: binding("start_delay_s", 60), range: 0...3600, unit: "s") }
            }
            pair {
                Field(label: "Stoppverzögerung") { NumberField(value: binding("stop_delay_s", 180), range: 0...3600, unit: "s") }
                Field(label: "Ziel-Ladestand", help: String(localized: "Nur Zielladung")) {
                    NumberField(value: binding("target_soc", 80), range: 10...100, unit: "%")
                }
            }
            Field(label: "Abfahrt um") {
                TimeField(value: Binding(get: { draft.string("target_time", "07:00") }, set: { draft.set("target_time", $0) }))
            }
            Text("Preisgrenze \(globalLimit.map { fmt.cents($0) } ?? "–") (Tarif)")
                .font(Typo.subheadline).foregroundStyle(Web.textDim)
            ToggleRow(label: "Ladepunkt hinter dem Netzzähler", help: String(localized: "Aus: Ladeleistung wird zur Netzleistung addiert"),
                      isOn: Binding(get: { draft.bool("grid_metered", true) }, set: { draft.set("grid_metered", $0) }))
            FormActions(dirty: draft.isDirty, saving: store?.saving.contains(device.id) == true,
                        onDiscard: { draft.reset() }) {
                Task {
                    if await store?.save(device.id, draft.draft, session: session) == true,
                       let saved = store?.settings(for: device.id) { draft = SettingsDraft(saved) }
                }
            }
        }
        .onChange(of: store?.settings(for: device.id), initial: true) { _, saved in
            guard let saved else { return }
            if !loaded {
                draft = SettingsDraft(saved)
                loaded = true
            } else {
                draft.rebase(saved)
            }
        }
    }

    @ViewBuilder
    private func pair<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: Space.l) { content() }
            VStack(alignment: .leading, spacing: Space.l) { content() }
        }
    }

    private func binding(_ key: String, _ fallback: Double) -> Binding<Double> {
        Binding(get: { draft.number(key, fallback) }, set: { draft.set(key, $0) })
    }
}

// MARK: - Ladevorgänge

private struct Cycles: View {
    let deviceID: Int
    @Environment(AppSession.self) private var session
    @State private var rows: [ChargeSession]?

    var body: some View {
        Group {
            if let rows, !rows.isEmpty {
                let maxEnergy = max(1, rows.map(\.energyKwh).max() ?? 1)
                Card {
                    VStack(alignment: .leading, spacing: Space.m) {
                        CardHead("Ladevorgänge", subtitle: String(localized: "Grün: Solaranteil"))
                        ForEach(rows) { row in
                            CycleRow(row: row, maxEnergy: maxEnergy)
                        }
                    }
                }
            }
        }
        .task {
            let all = try? await session.api.sessions(limit: 40)
            rows = all?.filter { $0.deviceId == deviceID } ?? []
        }
    }
}

private struct CycleRow: View {
    let row: ChargeSession
    let maxEnergy: Double

    var body: some View {
        let fmt = Fmt()
        let solar = row.solarShare
        HStack(spacing: Space.m) {
            VStack(alignment: .leading, spacing: 0) {
                Text(row.startedAt.formatted(.dateTime.day(.twoDigits).month(.twoDigits)))
                    .font(Typo.subheadlineMedium).foregroundStyle(Web.textStrong)
                Text(fmt.clock(row.startedAt)).font(Typo.subheadline).foregroundStyle(Web.textDim)
            }
            .frame(width: 64, alignment: .leading)
            GeometryReader { geo in
                let width = geo.size.width * row.energyKwh / maxEnergy
                ZStack(alignment: .leading) {
                    Capsule().fill(Web.surface3)
                    Capsule().fill(Palette.car.fill.opacity(0.35)).frame(width: width)
                        .overlay(alignment: .leading) {
                            Capsule().fill(Palette.pv.fill).frame(width: width * solar)
                        }
                }
            }
            .frame(height: 12)
            VStack(alignment: .trailing, spacing: 0) {
                Text(fmt.kwh(row.energyKwh)).font(Typo.subheadlineMedium).foregroundStyle(Web.textStrong).monospacedDigit()
                Text("\(Int((solar * 100).rounded())) % ☀").font(Typo.caption2).foregroundStyle(Web.textDim).monospacedDigit()
            }
        }
        .frame(minHeight: 44)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("\(fmt.dateTime(row.startedAt)): \(fmt.kwh(row.energyKwh)), \(Int((solar * 100).rounded())) % Sonne"))
    }
}
