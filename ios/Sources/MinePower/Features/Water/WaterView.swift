import Charts
import MinePowerKit
import SwiftUI

/// Warmwasser – Portierung von `frontend/src/pages/Water.tsx`.
///
/// Speicher als Grafik, großer Boost-Knopf in der Mitte, Betriebsart mit
/// Zeitfenstern, Regler für Zieltemperatur, Höchstleistung und Wärmepuffer
/// (gespeichert beim Loslassen), Tagesverlauf und Boost-/Experteneinstellungen.
struct WaterView: View {
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router
    @State private var picked: Int?
    @State private var store: DeviceSettingsStore?

    var body: some View {
        Group {
            if let snapshot = session.live.snapshot {
                let heaters = snapshot.devices(.waterHeater).filter(\.enabled)
                if let heater = heaters.first(where: { $0.id == picked }) ?? heaters.first {
                    Page(refresh: { try? await session.live.refresh(); await store?.devices.reload() }) {
                        DevicePicker(devices: heaters, selection: $picked)
                        WaterDetail(device: heater, store: store)
                            .id(heater.id)
                    }
                } else {
                    Page {
                        Card {
                            EmptyState(icon: .water, title: "Kein Warmwassergerät eingerichtet", message: "Einstellungen → Geräte") {
                                if session.canConfigure {
                                    Button("Gerät anlegen") { router.showSettings(.devices) }
                                        .buttonStyle(.web(.primary, fullWidth: false))
                                }
                            }
                        }
                    }
                }
            } else {
                Page { SkeletonCards(count: 1, height: 320) }
            }
        }
        .navigationTitle("Warmwasser")
        .toolbar { BrandToolbar() }
        .task {
            if store == nil { store = DeviceSettingsStore(api: session.api) }
            await store?.devices.load(maxAge: 30)
        }
    }
}

private struct WaterDetail: View {
    let device: SnapshotDevice
    let store: DeviceSettingsStore?
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var confirmBoost = false
    @State private var target: Double = 60
    @State private var maxPower: Double = 3000
    @State private var buffer: Double = 60

    private var temp: Double? { device.temperatureC }
    private var power: Double { device.values.double("power") ?? 0 }
    private var own: String? { device.values.string("device_mode").flatMap { $0.isEmpty ? nil : $0 } }
    private var mode: WaterMode { device.waterMode ?? .pvOnly }
    private var boost: Bool { device.boost == true }
    private var rated: Double {
        let stored = store?.devices.value?.first { $0.id == device.id }?.config.double("rated_power") ?? 0
        return stored > 0 ? stored : max(device.maxPowerW ?? 3000, 3000)
    }

    var body: some View {
        hero
        if let own {
            HStack(spacing: Space.m) {
                Icon(.info, size: 18).foregroundStyle(Palette.warn.fill)
                Text("Geräteprogramm (\(Fmt().watts(power)))").font(Typo.body).foregroundStyle(Web.text)
                Text(own).font(Typo.subheadline).foregroundStyle(Web.textDim)
                Spacer(minLength: 0)
                if session.canConfigure {
                    Button("Details") { router.showSettings(.devices, editDevice: device.id) }
                        .font(Typo.subheadlineMedium)
                        .foregroundStyle(Palette.accent)
                }
            }
            .padding(.horizontal, Space.s)
        }
        modeCard
        sliders
        Card {
            VStack(alignment: .leading, spacing: Space.m) {
                CardHead("24 h", subtitle: String(localized: "Fläche: Leistung, Linie: Temperatur"))
                WaterDayChart(deviceID: device.id)
            }
        }
        if session.canConfigure {
            Card {
                VStack(spacing: 0) {
                    BoostSettings(device: device, store: store)
                    Divider().overlay(Web.line)
                    WaterAdvanced(device: device, store: store, globalLimit: session.live.snapshot?.priceLimitCt)
                }
            }
        }
    }

    // MARK: Kopf mit Speicher und Boost

    private var hero: some View {
        let layout = sizeClass == .regular ? AnyLayout(HStackLayout(alignment: .center, spacing: Space.xl))
                                           : AnyLayout(VStackLayout(alignment: .leading, spacing: Space.l))
        return Card {
            layout {
                BoilerView(temp: temp, power: power, maxPower: device.maxPowerW ?? 3000, marks: marks,
                           own: own != nil, label: String(localized: "Warmwasserspeicher"))
                    .frame(maxWidth: .infinity)
                VStack(alignment: .leading, spacing: Space.l) {
                    HStack(spacing: Space.m) {
                        Badge(text: statusText, tone: statusTone)
                        Text(Fmt().watts(power)).font(Typo.title).foregroundStyle(Web.textStrong).monospacedDigit()
                    }
                    if !boost {
                        let sentence = Explanation.waterSentence(device)
                        Text(sentence.text).font(Typo.title3).foregroundStyle(sentence.tone.textColor)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    boostBlock
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .confirmationDialog("Boost starten?", isPresented: $confirmBoost, titleVisibility: .visible) {
            Button("Boost starten") { send(.boost) }
        } message: {
            Text("Volle Leistung, \(device.boostPlan())")
        }
    }

    @ViewBuilder
    private var boostBlock: some View {
        let busy = session.live.pending.contains(device.id)
        let pointless = !boost && device.boostWouldBePointless
        VStack(alignment: .leading, spacing: Space.m) {
            if boost {
                let endMode = device.boostEndMode ?? "both"
                HStack(spacing: Space.m) {
                    Icon(.boost, size: 26).foregroundStyle(Palette.water.fill)
                    VStack(alignment: .leading, spacing: 0) {
                        Text("Boost läuft").font(Typo.title3).foregroundStyle(Palette.water.ink)
                        Countdown(remaining: endMode != "temp" ? device.boostState?.remainingS : nil,
                                  fallback: String(localized: "bis \(Fmt().celsius(device.boostState?.targetTempC ?? device.boostTempC))"))
                    }
                }
                Button {
                    send(.boostOff)
                } label: {
                    HStack(spacing: 6) { Icon(.stop, size: 20); Text("Boost beenden") }
                }
                .buttonStyle(.web(.danger, size: .xl))
                .disabled(busy || !session.canControl)
            } else {
                Button {
                    confirmBoost = true
                } label: {
                    TwoLineLabel(icon: .boost, title: String(localized: "Boost starten"), subtitle: device.boostPlan())
                }
                .buttonStyle(.web(.primary, size: .xl))
                .disabled(!device.online || pointless || busy || !session.canControl)
                if pointless {
                    Text("Wasser wärmer als Boost-Temperatur (\(Fmt().celsius(device.boostTempC)))")
                        .font(Typo.subheadline).foregroundStyle(Web.textDim)
                }
            }
        }
        .padding(boost ? Space.l : 0)
        .background {
            if boost {
                RoundedRectangle(cornerRadius: Radius.card, style: .continuous).fill(Palette.water.fill.opacity(0.10))
                RoundedRectangle(cornerRadius: Radius.card, style: .continuous).strokeBorder(Palette.water.fill.opacity(0.35), lineWidth: 1)
            }
        }
    }

    private var marks: [BoilerView.Mark] {
        let fmt = Fmt()
        let targetTemp = device.targetTempC ?? 60
        let boostTemp = device.boostTempC ?? 65
        var list = [BoilerView.Mark(temp: targetTemp, label: String(localized: "Ziel \(fmt.celsius(targetTemp))"), kind: .target)]
        if boost || (device.boostEndMode ?? "both") != "time" {
            list.append(.init(temp: boostTemp, label: "Boost \(fmt.celsius(boostTemp))", kind: .boost))
        }
        if let surplus = device.surplusTempC {
            list.append(.init(temp: surplus, label: "☀ \(fmt.celsius(surplus))", kind: .buffer))
        }
        return list
    }

    private var status: String {
        if own != nil { return "own" }
        if !device.online { return "offline" }
        if boost { return "boost" }
        if power > 50 { return "heating" }
        if mode == .off { return "off" }
        if let temp, temp >= (device.targetTempC ?? 60) { return "hot" }
        return "standby"
    }

    private var statusText: String {
        switch status {
        case "boost": "Boost"
        case "heating": String(localized: "Heizt")
        case "hot": String(localized: "Warm")
        case "off": String(localized: "Aus")
        case "offline": String(localized: "Keine Verbindung")
        case "own": String(localized: "Geräteprogramm")
        default: String(localized: "Bereit")
        }
    }

    private var statusTone: DomainColor {
        switch status {
        case "heating", "boost": Palette.ok
        case "own": Palette.warn
        case "offline": Palette.error
        default: Palette.info
        }
    }

    // MARK: Betriebsart

    private var modeCard: some View {
        Card {
            VStack(alignment: .leading, spacing: Space.m) {
                CardHead("Betriebsart")
                SegmentGrid(options: [WaterMode.pvOnly, .pvPrice, .schedule, .off].map {
                    .init(value: $0, title: $0.title, icon: $0.webIcon)
                }, selection: mode, minimum: 128, pending: session.live.pending.contains(device.id) ? mode : nil,
                   disabled: !session.canControl || !device.online) { send(.setMode($0.rawValue)) }
                Text(mode.help)
                    .font(Typo.body).foregroundStyle(Web.text)
                    .padding(.horizontal, Space.l).padding(.vertical, Space.m)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Web.surface2, in: .rect(cornerRadius: Radius.medium, style: .continuous))
                if mode == .schedule {
                    ScheduleEditor(deviceID: device.id)
                }
            }
        }
    }

    // MARK: Regler

    private var sliders: some View {
        let fmt = Fmt()
        return Card {
            VStack(alignment: .leading, spacing: Space.xl) {
                StepSlider(label: "Zieltemperatur", value: $target, range: 35...75, step: 1, tone: Palette.water.fill,
                           format: { fmt.celsius($0) }) { save("target_temp_c", .number($0)) }
                Divider().overlay(Web.line)
                StepSlider(label: "Höchstleistung", value: $maxPower, range: 300...max(301, rated), step: 100,
                           tone: Palette.water.fill, format: { fmt.watts($0) }) { save("max_power_w", .number($0)) }
                Divider().overlay(Web.line)
                StepSlider(label: "Wärmepuffer bis", value: $buffer, range: min(device.targetTempC ?? 60, 79)...80, step: 1,
                           tone: Palette.pv.fill,
                           format: { $0 <= (device.targetTempC ?? 60) ? String(localized: "aus") : fmt.celsius($0) },
                           help: String(localized: "Nur mit Überschuss. Maximaltemperatur im Gerät beachten.")) { value in
                    save("surplus_temp_c", value <= (device.targetTempC ?? 60) ? .null : .number(value))
                }
            }
            .disabled(!session.canConfigure)
        }
        .onAppear(perform: syncSliders)
        .onChange(of: device.targetTempC) { syncSliders() }
        .onChange(of: device.maxPowerW) { syncSliders() }
        .onChange(of: device.surplusTempC) { syncSliders() }
    }

    private func syncSliders() {
        target = device.targetTempC ?? 60
        maxPower = min(device.maxPowerW ?? 3000, rated)
        buffer = device.surplusTempC ?? (device.targetTempC ?? 60)
    }

    private func save(_ key: String, _ value: JSONValue) {
        Task { _ = await store?.save(device.id, [key: value], session: session) }
    }

    private func send(_ action: DeviceAction) {
        Task { do { try await session.live.perform(action, device: device.id) } catch { session.handle(error) } }
    }
}

extension WaterMode {
    var webIcon: WebIcon {
        switch self {
        case .pvOnly: .sun
        case .pvPrice: .price
        case .schedule: .clock
        case .off: .stop
        }
    }
}

/// Restzeit, die sekündlich herunterzählt (`useCountdown`).
struct Countdown: View {
    let remaining: Double?
    let fallback: String
    @State private var anchor = Date.now

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let left = remaining.map { max(0, $0 - context.date.timeIntervalSince(anchor)) }
            Text(left.map { String(localized: "noch \(Fmt().duration($0))") } ?? fallback)
                .font(Typo.title).foregroundStyle(Web.textStrong).monospacedDigit()
        }
        .onChange(of: remaining) { anchor = .now }
    }
}

// MARK: - Boost-Einstellungen und Experte

private struct BoostSettings: View {
    let device: SnapshotDevice
    let store: DeviceSettingsStore?
    @Environment(AppSession.self) private var session
    @State private var draft = SettingsDraft()

    var body: some View {
        Disclosure(title: "Boost-Einstellungen", summary: String(localized: "Ende des Boosts")) {
            Field(label: "Boost endet") {
                SegmentGrid(options: [.init(value: "time", title: String(localized: "nach Zeit")),
                                      .init(value: "temp", title: String(localized: "bei Temperatur")),
                                      .init(value: "both", title: String(localized: "was zuerst kommt"))],
                            selection: draft.string("boost_end_mode", "both"), minimum: 100) { draft.set("boost_end_mode", $0) }
            }
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: Space.l) { durationField; tempField }
                VStack(alignment: .leading, spacing: Space.l) { durationField; tempField }
            }
            Text("Ohne Temperaturwert Ende nach Dauer. Maximal 80 °C.").font(Typo.subheadline).foregroundStyle(Web.textDim)
            FormActions(dirty: draft.isDirty, saving: store?.saving.contains(device.id) == true,
                        onDiscard: { draft.reset() }) {
                Task {
                    let keys = ["boost_end_mode", "boost_duration_min", "boost_temp_c"]
                    let values = draft.draft.filter { keys.contains($0.key) }
                    if await store?.save(device.id, values, session: session) == true, let saved = store?.settings(for: device.id) {
                        draft = SettingsDraft(saved)
                    }
                }
            }
        }
        .onChange(of: store?.settings(for: device.id), initial: true) { _, saved in
            if let saved { draft.rebase(saved) }
        }
    }

    private var durationField: some View {
        Field(label: "Dauer") {
            NumberField(value: Binding(get: { draft.number("boost_duration_min", 60) }, set: { draft.set("boost_duration_min", $0) }),
                        range: 5...360, unit: "min")
        }
    }

    private var tempField: some View {
        Field(label: "Boost-Temperatur") {
            NumberField(value: Binding(get: { draft.number("boost_temp_c", 65) }, set: { draft.set("boost_temp_c", $0) }),
                        range: 30...80, unit: "°C")
                .disabled(draft.string("boost_end_mode", "both") == "time")
        }
    }
}

private struct WaterAdvanced: View {
    let device: SnapshotDevice
    let store: DeviceSettingsStore?
    let globalLimit: Double?
    @Environment(AppSession.self) private var session
    @State private var draft = SettingsDraft()

    var body: some View {
        Disclosure(title: "Experte", summary: String(localized: "Startschwelle")) {
            Field(label: "Startschwelle") {
                NumberField(value: Binding(get: { draft.number("start_threshold_w", 200) }, set: { draft.set("start_threshold_w", $0) }),
                            range: 0...20_000, unit: "W")
            }
            Text("Preisgrenze \(globalLimit.map { Fmt().cents($0) } ?? "–") (Tarif)")
                .font(Typo.subheadline).foregroundStyle(Web.textDim)
            FormActions(dirty: draft.isDirty, saving: store?.saving.contains(device.id) == true,
                        onDiscard: { draft.reset() }) {
                Task {
                    if await store?.save(device.id, draft.draft, session: session) == true, let saved = store?.settings(for: device.id) {
                        draft = SettingsDraft(saved)
                    }
                }
            }
        }
        .onChange(of: store?.settings(for: device.id), initial: true) { _, saved in
            if let saved { draft.rebase(saved) }
        }
    }
}

// MARK: - Zeitfenster

/// `ScheduleEditor`: Fenster des Geräts, darunter eine Zeile zum Anlegen.
struct ScheduleEditor: View {
    let deviceID: Int
    @Environment(AppSession.self) private var session
    @State private var items: [Schedule]?
    @State private var mask = Weekdays(rawValue: 127)
    @State private var start = "06:00"
    @State private var end = "08:00"
    @State private var busy = false

    private static let dayNames = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            if let items, items.isEmpty {
                Text("Kein Zeitfenster. Bis dahin nur PV-Überschuss.").font(Typo.subheadline).foregroundStyle(Web.textDim)
            }
            ForEach(items ?? []) { item in
                HStack(spacing: Space.m) {
                    Icon(.clock, size: 18).foregroundStyle(Web.textDim)
                    Text("\(item.startTime)–\(item.endTime)").font(Typo.bodyMedium).monospacedDigit().foregroundStyle(Web.textStrong)
                    Text(Weekdays(rawValue: item.daysMask).label()).font(Typo.subheadline).foregroundStyle(Web.textDim)
                    Spacer()
                    if session.canConfigure {
                        Button {
                            Task { await remove(item) }
                        } label: {
                            Icon(.close, size: 18).foregroundStyle(Web.textDim).frame(width: 44, height: 44)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(Text("Zeitfenster \(item.startTime)–\(item.endTime) löschen"))
                    }
                }
                .padding(.leading, Space.m)
                .background(Web.surface2, in: .rect(cornerRadius: Radius.medium, style: .continuous))
            }
            if session.canConfigure { addRow }
        }
        .task { await load() }
    }

    private var addRow: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            Text("Neues Zeitfenster").font(Typo.subheadlineMedium).foregroundStyle(Web.textStrong)
            FlowLayout(spacing: 6) {
                ForEach(Array(Self.dayNames.enumerated()), id: \.offset) { index, name in
                    let day = Weekdays(rawValue: 1 << index)
                    let on = mask.contains(day)
                    Button {
                        if on { mask.remove(day) } else { mask.insert(day) }
                    } label: {
                        Text(name).font(Typo.subheadlineMedium)
                            .foregroundStyle(on ? Palette.accent : Web.textDim)
                            .frame(minWidth: 44, minHeight: 40)
                            .background(on ? Web.accentSoft : Web.surface2, in: .capsule)
                            .overlay(Capsule().strokeBorder(on ? Palette.accent : Web.line, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: Space.m) { times; addButton }
                VStack(alignment: .leading, spacing: Space.m) { times; addButton }
            }
        }
        .padding(Space.m)
        .background(Web.surface2.opacity(0.5), in: .rect(cornerRadius: Radius.medium, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.medium, style: .continuous).strokeBorder(Web.line, lineWidth: 1))
    }

    private var times: some View {
        HStack(spacing: Space.m) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Von").font(Typo.caption2).foregroundStyle(Web.textDim)
                TimeField(value: $start)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text("Bis").font(Typo.caption2).foregroundStyle(Web.textDim)
                TimeField(value: $end)
            }
        }
    }

    private var addButton: some View {
        Button {
            Task { await add() }
        } label: {
            HStack(spacing: 6) {
                if busy { ProgressView().tint(Web.accentText) } else { Icon(.check, size: 18) }
                Text("Hinzufügen")
            }
        }
        .buttonStyle(.web(.primary, fullWidth: false))
        .disabled(busy)
    }

    private func load() async {
        do {
            items = try await session.api.schedules().filter { $0.deviceId == deviceID }
        } catch {
            session.handle(error)
            items = []
        }
    }

    private func add() async {
        guard !mask.isEmpty else {
            session.show(Notice(.error, String(localized: "Mindestens einen Wochentag wählen")))
            return
        }
        guard start != end else {
            session.show(Notice(.error, String(localized: "Beginn und Ende gleich")))
            return
        }
        busy = true
        defer { busy = false }
        do {
            _ = try await session.api.createSchedule(ScheduleCreate(deviceId: deviceID, daysMask: mask.rawValue,
                                                                  startTime: start, endTime: end))
            session.show(Notice(.success, String(localized: "Zeitfenster gespeichert")))
            await load()
        } catch {
            session.handle(error)
        }
    }

    private func remove(_ item: Schedule) async {
        do {
            try await session.api.deleteSchedule(item.id)
            await load()
        } catch {
            session.handle(error)
        }
    }
}

// MARK: - Tagesverlauf

/// `WaterDayChart`: Fläche Leistung (links), Linie Temperatur (rechts, 10–80 °C).
struct WaterDayChart: View {
    let deviceID: Int
    @Environment(AppSession.self) private var session
    @State private var data: HistorySeries?
    @State private var failed = false

    var body: some View {
        Group {
            if let data, !data.isEmpty {
                let power = data["water_power"] ?? []
                let temps = data["water_temp"] ?? []
                let top = max(1000, (power.map(\.value).max() ?? 0) * 1.1)
                let scale = { (t: Double) in (t - 10) / 70 * top }
                Chart {
                    ForEach(power) { p in
                        AreaMark(x: .value("Zeit", p.time), y: .value("W", p.value))
                            .foregroundStyle(Palette.water.fill.opacity(0.25))
                            .interpolationMethod(.catmullRom)
                        LineMark(x: .value("Zeit", p.time), y: .value("W", p.value), series: .value("Reihe", "Leistung"))
                            .foregroundStyle(Palette.water.fill)
                            .lineStyle(StrokeStyle(lineWidth: 1.2))
                            .interpolationMethod(.catmullRom)
                    }
                    ForEach(temps) { p in
                        LineMark(x: .value("Zeit", p.time), y: .value("W", scale(p.value)), series: .value("Reihe", "Temperatur"))
                            .foregroundStyle(Web.waterHot)
                            .lineStyle(StrokeStyle(lineWidth: 2))
                            .interpolationMethod(.catmullRom)
                    }
                }
                .chartYScale(domain: 0...top)
                .chartXAxis {
                    AxisMarks(values: .automatic(desiredCount: 5)) { _ in
                        AxisValueLabel().font(Typo.caption2).foregroundStyle(Web.textDim)
                    }
                }
                .chartYAxis {
                    AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in
                        AxisGridLine().foregroundStyle(Web.line)
                        AxisValueLabel { if let v = value.as(Double.self) { Text(Fmt().watts(v)).font(Typo.caption2) } }
                    }
                    AxisMarks(position: .trailing, values: [20, 40, 60, 80].map(scale)) { value in
                        AxisValueLabel {
                            if let v = value.as(Double.self) { Text("\(Int((v / top * 70 + 10).rounded())) °C").font(Typo.caption2) }
                        }
                    }
                }
                .frame(height: 220)
            } else if failed || data != nil {
                ChartEmpty(text: String(localized: "Keine Verlaufsdaten"))
            } else {
                SkeletonCards(count: 1, height: 220)
            }
        }
        .task {
            while !Task.isCancelled {
                do {
                    data = try await session.api.history(.day, fields: ["water_power", "water_temp"], source: "device:\(deviceID)")
                } catch {
                    failed = true
                }
                try? await Task.sleep(for: .seconds(120))
            }
        }
    }
}
