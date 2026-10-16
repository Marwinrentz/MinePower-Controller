import MinePowerKit
import SwiftUI

/// Je Gerät eine Karte: Grafik, Kernzahl, ein Satz, die eine wichtige Aktion.
///
/// Aktionen mit großer Wirkung (Sofortladen, Boost) fragen kurz nach: Ein
/// Tipp am Rand beim Scrollen soll nie volle Leistung aus dem Netz auslösen.
struct DeviceCards: View {
    let snapshot: Snapshot

    var body: some View {
        let cars = snapshot.devices(.wallbox).filter(\.enabled)
        let heaters = snapshot.devices(.waterHeater).filter(\.enabled)
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 300), spacing: Space.l)], spacing: Space.l) {
            ForEach(cars) { CarCard(device: $0) }
            ForEach(heaters) { WaterCard(device: $0, named: heaters.count > 1) }
            if snapshot.battery?.soc != nil {
                BatteryCard(snapshot: snapshot)
            }
        }
    }
}

// MARK: - Auto

struct CarCard: View {
    let device: SnapshotDevice
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router
    @State private var confirmFast = false

    var body: some View {
        let state = CarState(device)
        let sentence = Explanation.carSentence(device)
        let pending = session.live.pending.contains(device.id)
        Card {
            VStack(alignment: .leading, spacing: Space.m) {
                CardHeader(title: device.name, symbol: "car.fill", color: Palette.car) { router.show(.car) }
                HStack(alignment: .center, spacing: Space.l) {
                    CarArt(soc: device.soc, state: state, limit: device.chargeLimitSoc)
                        .frame(maxWidth: 190)
                    VStack(alignment: .leading, spacing: 4) {
                        if let soc = device.soc {
                            Metric(value: Fmt().number(soc), unit: "%", font: Typo.metric)
                        }
                        StatusBadge(text: state.label, symbol: state.symbol, color: state == .charging ? Palette.car : Palette.house)
                        if state == .charging {
                            Metric(watts: device.values.double("power"), font: Typo.subheadlineMedium, color: Palette.car.ink)
                        }
                    }
                }
                Text(sentence.text)
                    .font(Typo.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if session.canControl {
                    HStack(spacing: Space.s) {
                        if device.override == "fast" {
                            ActionChip(title: "Sofortladen beenden", symbol: "bolt.slash", pending: pending) { send(.auto) }
                        } else {
                            ActionChip(title: "Sofort laden", symbol: "bolt.fill", pending: pending, prominent: true) {
                                confirmFast = true
                            }
                            .disabled(!device.online || !state.isPlugged)
                        }
                        if device.override == "stop" {
                            ActionChip(title: "Laden fortsetzen", symbol: "play.fill", pending: pending) { send(.auto) }
                        } else if device.override != "fast" {
                            ActionChip(title: "Laden aus", symbol: "stop.fill", pending: pending) { send(.stop) }
                                .disabled(!device.online)
                        }
                    }
                }
            }
        }
        .confirmationDialog("Sofort laden?", isPresented: $confirmFast, titleVisibility: .visible) {
            Button("Sofort voll laden") { send(.fast) }
        } message: {
            Text("Volle Leistung aus Sonne und Netz. Endet bei voll, beim Abstecken oder nach Zeitablauf.")
        }
        .sensoryFeedback(.success, trigger: device.override)
    }

    private func send(_ action: DeviceAction) {
        Task {
            do { try await session.live.perform(action, device: device.id) } catch { session.handle(error) }
        }
    }
}

extension CarState {
    var symbol: String {
        switch self {
        case .charging: "bolt.fill"
        case .waiting: "powerplug.fill"
        case .complete: "checkmark.circle.fill"
        case .asleep: "moon.zzz.fill"
        case .away: "location.fill"
        case .unplugged: "powerplug"
        case .offline, .proxy: "wifi.slash"
        case .unknown, .none: "questionmark.circle"
        }
    }
}

// MARK: - Warmwasser

struct WaterCard: View {
    let device: SnapshotDevice
    var named = false
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router
    @State private var confirmBoost = false

    var body: some View {
        let power = device.values.double("power") ?? 0
        let sentence = Explanation.waterSentence(device)
        let pending = session.live.pending.contains(device.id)
        Card {
            VStack(alignment: .leading, spacing: Space.m) {
                CardHeader(title: named ? device.name : String(localized: "Warmwasser"), symbol: "drop.fill", color: Palette.water) {
                    router.show(.water)
                }
                HStack(alignment: .center, spacing: Space.l) {
                    BoilerArt(temperature: device.temperatureC, target: device.targetTempC,
                              heating: power > 50, boost: device.boost == true)
                        .frame(height: 120)
                    VStack(alignment: .leading, spacing: 4) {
                        Metric(value: device.temperatureC.map { Fmt().number($0) } ?? "–", unit: "°C", font: Typo.metric)
                        if let target = device.targetTempC {
                            Text("Ziel \(Fmt().celsius(target))").font(Typo.caption).foregroundStyle(.secondary)
                        }
                        if power > 50 {
                            Metric(watts: power, font: Typo.subheadlineMedium, color: Palette.water.ink)
                        }
                    }
                    Spacer(minLength: 0)
                }
                Text(sentence.text)
                    .font(Typo.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if session.canControl {
                    if device.boost == true {
                        ActionChip(title: "Boost beenden", symbol: "flame", pending: pending) { send(.boostOff) }
                    } else {
                        ActionChip(title: "Boost", symbol: "flame.fill", pending: pending, prominent: true) { confirmBoost = true }
                            .disabled(!device.online || device.boostWouldBePointless)
                    }
                }
            }
        }
        .confirmationDialog("Boost starten?", isPresented: $confirmBoost, titleVisibility: .visible) {
            Button("Boost starten") { send(.boost) }
        } message: {
            Text("Volle Leistung, \(device.boostPlan()).")
        }
        .sensoryFeedback(.success, trigger: device.boost)
    }

    private func send(_ action: DeviceAction) {
        Task {
            do { try await session.live.perform(action, device: device.id) } catch { session.handle(error) }
        }
    }
}

// MARK: - Batterie

struct BatteryCard: View {
    let snapshot: Snapshot
    @Environment(Router.self) private var router

    var body: some View {
        let battery = snapshot.battery
        let power = battery?.power ?? 0
        Card {
            VStack(alignment: .leading, spacing: Space.m) {
                CardHeader(title: String(localized: "Batterie"), symbol: "battery.75percent", color: Palette.battery) {
                    router.show(.battery)
                }
                HStack(spacing: Space.l) {
                    BatteryArt(soc: battery?.soc, power: power, reserve: snapshot.batteryReserveSoc)
                        .frame(height: 120)
                    VStack(alignment: .leading, spacing: 4) {
                        Metric(value: battery?.soc.map { Fmt().number($0) } ?? "–", unit: "%", font: Typo.metric)
                        Text(direction(power)).font(Typo.subheadlineMedium)
                            .foregroundStyle(abs(power) < EnergyFlow.threshold ? .secondary : Palette.battery.ink)
                        if let eta = snapshot.batteryETA {
                            Text(power > 0 ? "voll in \(Fmt().duration(eta))" : "Reserve in \(Fmt().duration(eta))")
                                .font(Typo.caption).foregroundStyle(.secondary)
                        }
                        if let capacity = battery?.capacityKwh {
                            Text("\(Fmt().kwh(capacity)) Kapazität").font(Typo.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer(minLength: 0)
                }
            }
        }
    }

    private func direction(_ power: Double) -> String {
        let fmt = Fmt()
        if power >= EnergyFlow.threshold { return String(localized: "lädt \(fmt.watts(power))") }
        if power <= -EnergyFlow.threshold { return String(localized: "entlädt \(fmt.watts(-power))") }
        return String(localized: "ruht")
    }
}

// MARK: - Strompreis

struct PriceCard: View {
    let tariff: TariffInfo
    let current: Double?
    let cheap: Bool
    @Environment(Router.self) private var router

    var body: some View {
        let upcoming = tariff.prices.filter { $0.end > .now }.prefix(12)
        let maxPrice = upcoming.map(\.priceCt).max() ?? 1
        let minPrice = min(0, upcoming.map(\.priceCt).min() ?? 0)
        Card {
            VStack(alignment: .leading, spacing: Space.m) {
                CardHeader(title: String(localized: "Strompreis"), symbol: "eurosign.circle.fill", color: Palette.gridExport) {
                    router.show(.tariff)
                }
                HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                    Metric(value: current.map { Fmt().number($0, digits: 1) } ?? "–", unit: "ct/kWh", font: Typo.metric)
                    if cheap { StatusBadge(text: String(localized: "günstig"), symbol: "arrow.down", color: Palette.ok) }
                }
                HStack(alignment: .bottom, spacing: 3) {
                    ForEach(Array(upcoming.enumerated()), id: \.element.id) { index, slot in
                        let fraction = (slot.priceCt - minPrice) / max(0.1, maxPrice - minPrice)
                        RoundedRectangle(cornerRadius: 3)
                            .fill(slot.priceCt <= (tariff.cheapLimitCt ?? -.infinity) ? Palette.ok.fill : Palette.gridExport.fill.opacity(index == 0 ? 1 : 0.55))
                            .frame(height: max(4, 54 * fraction))
                    }
                }
                .frame(height: 56, alignment: .bottom)
                .accessibilityElement()
                .accessibilityLabel(Text("Preise der nächsten Stunden"))
                .accessibilityValue(Text(upcoming.map { "\(Fmt().clock($0.start)) \(Fmt().cents($0.priceCt))" }.joined(separator: ", ")))
                HStack {
                    Text("jetzt").font(Typo.caption2).foregroundStyle(.secondary)
                    Spacer()
                    if let last = upcoming.last {
                        Text(Fmt().clock(last.end)).font(Typo.caption2).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }
}

// MARK: - Bausteine

/// Kartenkopf mit Symbol, Titel und Sprung zur Detailseite.
struct CardHeader: View {
    let title: String
    let symbol: String
    let color: DomainColor
    var action: (() -> Void)?

    var body: some View {
        HStack(spacing: Space.s) {
            DomainIcon(symbol: symbol, color: color, size: 30)
            Text(title).font(Typo.headline).lineLimit(2)
            Spacer(minLength: Space.s)
            if let action {
                Button(action: action) {
                    HStack(spacing: 2) {
                        Text("Details")
                        Image(systemName: "chevron.right").font(.caption.weight(.semibold))
                    }
                    .font(Typo.subheadlineMedium)
                    .frame(minHeight: 44)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Palette.accent)
                .accessibilityLabel(Text("\(title) – Details"))
            }
        }
        .accessibilityElement(children: .contain)
    }
}

/// Aktion auf einer Karte: groß genug für den Daumen (≥ 44 pt), mit Ladezustand.
struct ActionChip: View {
    let title: LocalizedStringKey
    let symbol: String
    var pending = false
    var prominent = false
    let action: () -> Void
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if pending {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: symbol)
                }
                Text(title).lineLimit(1).minimumScaleFactor(0.85)
            }
            .font(Typo.subheadlineMedium)
            .padding(.horizontal, Space.m)
            .frame(maxWidth: .infinity, minHeight: 44)
            .foregroundStyle(prominent ? .white : Palette.accent)
            .background(prominent ? Palette.accent.opacity(isEnabled ? 1 : 0.35) : Palette.accent.opacity(0.12),
                        in: .rect(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(.plain)
        .disabled(pending)
        .opacity(isEnabled ? 1 : 0.6)
    }
}
