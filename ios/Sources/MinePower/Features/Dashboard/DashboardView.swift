import MinePowerKit
import SwiftUI

/// Übersicht: Was passiert gerade, wohin fließt die Energie, was tun die Geräte.
///
/// Reihenfolge nach Blickhäufigkeit: oben eine Aussage in Alltagssprache mit
/// den drei Kernzahlen, darunter das Flussbild, dann je Gerät eine Karte mit
/// Grafik und der einen wichtigen Aktion. Zahlen ticken weich mit
/// (`numericText`), damit Live-Werte nicht springen.
struct DashboardView: View {
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router
    @State private var stats: Resource<Statistics>?
    @State private var forecast: Resource<Forecast>?
    @State private var tariff: Resource<TariffInfo>?
    @State private var confirmPause = false

    var body: some View {
        Group {
            if let snapshot = session.live.snapshot {
                content(snapshot)
            } else if session.isOffline || session.live.connection == .offline {
                ErrorState(error: .unreachable("")) { try? await session.live.refresh() }
            } else {
                Page { SkeletonCards(count: 4) }
            }
        }
        .navigationTitle("Übersicht")
        .toolbar { toolbar }
        .task { await loadExtras() }
        .confirmationDialog("Regelung anhalten?", isPresented: $confirmPause, titleVisibility: .visible) {
            Button("Anhalten", role: .destructive) { setPaused(true) }
        } message: {
            Text("Auto und Warmwasser werden ausgeschaltet, bis du die Regelung fortsetzt.")
        }
    }

    // MARK: - Inhalt

    @ViewBuilder
    private func content(_ snapshot: Snapshot) -> some View {
        let devices = snapshot.enabledDevices
        if devices.isEmpty {
            ContentUnavailableView {
                Label("Noch keine Geräte", systemImage: "square.grid.2x2")
            } description: {
                Text("Richte Wechselrichter und Netzzähler ein, damit MinePower regeln kann.")
            } actions: {
                if session.canConfigure {
                    Button("Einrichtung starten") { router.showWizard = true }
                        .buttonStyle(.borderedProminent)
                        .tint(Palette.accent)
                }
            }
        } else {
            Page(refresh: { await refreshAll() }) {
                StatusBanners(snapshot: snapshot, onResume: { setPaused(false) })
                HeroCard(snapshot: snapshot, age: session.live.age())
                Card(padding: Space.m) {
                    EnergyFlowView(flow: EnergyFlow(snapshot))
                        .padding(.vertical, Space.s)
                }
                ReasonsCard(explanation: Explanation(snapshot))
                DeviceCards(snapshot: snapshot)
                FiguresRow(snapshot: snapshot, stats: stats?.value, forecast: forecast?.value)
                if let tariff = tariff?.value, tariff.provider != "none", !tariff.prices.isEmpty {
                    PriceCard(tariff: tariff, current: snapshot.priceCt, cheap: snapshot.priceCheap == true)
                }
            }
        }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        if session.canControl, let snapshot = session.live.snapshot, !snapshot.enabledDevices.isEmpty {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    if snapshot.isPaused { setPaused(false) } else { confirmPause = true }
                } label: {
                    Label(snapshot.isPaused ? "Fortsetzen" : "Anhalten",
                          systemImage: snapshot.isPaused ? "play.fill" : "pause.fill")
                }
                .accessibilityIdentifier("pause")
            }
        }
    }

    // MARK: - Laden

    private func loadExtras() async {
        if stats == nil {
            let api = session.api
            stats = Resource { try await api.statistics(.day) }
            forecast = Resource { try await api.forecast() }
            tariff = Resource { try await api.tariff() }
        }
        // Zahlen des Tages jede Minute nachziehen, solange die Seite sichtbar ist.
        while !Task.isCancelled {
            await refreshExtras()
            try? await Task.sleep(for: .seconds(60))
        }
    }

    private func refreshExtras() async {
        async let a: Void = stats?.reload() ?? ()
        async let b: Void = forecast?.reload() ?? ()
        async let c: Void = tariff?.reload() ?? ()
        _ = await (a, b, c)
    }

    private func refreshAll() async {
        try? await session.live.refresh()
        await refreshExtras()
    }

    private func setPaused(_ paused: Bool) {
        Task {
            do {
                try await session.live.setPaused(paused)
                session.show(Notice(.success, paused ? String(localized: "Regelung angehalten")
                                                     : String(localized: "Regelung läuft wieder")))
            } catch {
                session.handle(error)
            }
        }
    }
}

// MARK: - Kopf

private struct HeroCard: View {
    let snapshot: Snapshot
    let age: TimeInterval?

    var body: some View {
        let explanation = Explanation(snapshot)
        let grid = snapshot.gridPower ?? 0
        Card {
            VStack(alignment: .leading, spacing: Space.l) {
                HStack(alignment: .top, spacing: Space.m) {
                    Image(systemName: explanation.tone.symbol)
                        .font(.title2.weight(.semibold))
                        .foregroundStyle(explanation.tone.domain.fill)
                        .frame(width: 44, height: 44)
                        .background(explanation.tone.domain.soft, in: .circle)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(explanation.headline)
                            .font(Typo.title3)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityAddTraits(.isHeader)
                        LiveAge(age: age)
                    }
                }
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: Space.l) { figures(grid: grid) }
                    VStack(alignment: .leading, spacing: Space.m) { figures(grid: grid) }
                }
            }
        }
    }

    @ViewBuilder
    private func figures(grid: Double) -> some View {
        HeroFigure(label: "Sonne", watts: snapshot.pv, color: Palette.pv)
        HeroFigure(label: "Haus", watts: snapshot.house, color: Palette.house)
        HeroFigure(label: grid > 50 ? "Netzbezug" : grid < -50 ? "Einspeisung" : "Netz",
                   watts: abs(grid), color: grid > 50 ? Palette.gridImport : Palette.gridExport)
    }
}

private struct HeroFigure: View {
    let label: LocalizedStringKey
    let watts: Double
    let color: DomainColor

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 5) {
                Circle().fill(color.fill).frame(width: 8, height: 8)
                Text(label).font(Typo.caption).foregroundStyle(.secondary)
            }
            Metric(watts: watts, font: Typo.metricSmall)
                .animation(.snappy, value: watts)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

/// "Live" mit Punkt oder Alter der Werte.
struct LiveAge: View {
    let age: TimeInterval?

    var body: some View {
        TimelineView(.periodic(from: .now, by: 5)) { _ in
            let fresh = (age ?? .infinity) < 30
            HStack(spacing: 5) {
                Circle().fill(fresh ? Palette.ok.fill : Palette.warn.fill).frame(width: 7, height: 7)
                Text(fresh ? String(localized: "Live") : ageText)
                    .font(Typo.caption)
                    .foregroundStyle(.secondary)
            }
            .accessibilityElement(children: .combine)
        }
    }

    private var ageText: String {
        guard let age, age.isFinite else { return String(localized: "Keine Werte") }
        return Fmt().ago(Date.now.addingTimeInterval(-age))
    }
}

// MARK: - Hinweise

private struct StatusBanners: View {
    let snapshot: Snapshot
    var onResume: () -> Void
    @State private var dismissed: Set<String> = []

    var body: some View {
        VStack(spacing: Space.s) {
            if snapshot.isPaused {
                BannerRow(symbol: "pause.circle.fill", color: Palette.warn, title: "Regelung angehalten",
                          text: String(localized: "Auto und Warmwasser sind aus.")) {
                    Button("Fortsetzen", action: onResume).buttonStyle(.borderedProminent).tint(Palette.accent)
                }
            }
            if let safety = snapshot.safety {
                BannerRow(symbol: "exclamationmark.octagon.fill", color: Palette.error, title: "Sicherheitsstopp", text: safety) { EmptyView() }
            }
            let offline = snapshot.offlineDevices
            if !offline.isEmpty, !dismissed.contains("offline") {
                BannerRow(symbol: "wifi.exclamationmark", color: Palette.warn, title: "Gerät nicht erreichbar",
                          text: offline.map(\.name).joined(separator: ", ")) { close("offline") }
            }
            let failing = snapshot.failingDevices
            if !failing.isEmpty, !dismissed.contains("failing") {
                BannerRow(symbol: "exclamationmark.triangle.fill", color: Palette.error, title: "Befehl nicht übernommen",
                          text: failing.map { "\($0.name): \($0.commandError ?? "")" }.joined(separator: "\n")) { close("failing") }
            }
            let warnings = snapshot.warnings ?? []
            if !warnings.isEmpty, !dismissed.contains("warnings") {
                BannerRow(symbol: "exclamationmark.bubble.fill", color: Palette.warn, title: "Hinweise",
                          text: warnings.joined(separator: "\n")) { close("warnings") }
            }
        }
    }

    private func close(_ key: String) -> some View {
        Button {
            withAnimation { _ = dismissed.insert(key) }
        } label: {
            Image(systemName: "xmark").font(.footnote.weight(.semibold)).frame(width: 44, height: 44)
        }
        .buttonStyle(.plain)
        .foregroundStyle(.secondary)
        .accessibilityLabel(Text("Hinweis schließen"))
    }
}

struct BannerRow<Trailing: View>: View {
    let symbol: String
    let color: DomainColor
    let title: LocalizedStringKey
    let text: String
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(alignment: .top, spacing: Space.m) {
            Image(systemName: symbol).font(.title3).foregroundStyle(color.fill).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(Typo.headline).foregroundStyle(color.ink)
                Text(text).font(Typo.subheadline).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            trailing
        }
        .padding(.leading, Space.m)
        .padding(.vertical, Space.s)
        .padding(.trailing, Space.xs)
        .background(color.soft, in: .rect(cornerRadius: 18, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Warum

private struct ReasonsCard: View {
    let explanation: Explanation
    @State private var expanded = false
    @Environment(\.horizontalSizeClass) private var sizeClass

    var body: some View {
        let reasons = explanation.reasons
        if !reasons.isEmpty {
            let visible = expanded || sizeClass == .regular ? reasons : Array(reasons.prefix(2))
            Card {
                VStack(alignment: .leading, spacing: Space.m) {
                    Text("Warum?").font(Typo.headline).accessibilityAddTraits(.isHeader)
                    ForEach(visible) { reason in
                        Label {
                            Text(reason.text).font(Typo.subheadline).fixedSize(horizontal: false, vertical: true)
                        } icon: {
                            Image(systemName: reason.symbol.systemName)
                                .foregroundStyle(reason.tone.domain.fill)
                        }
                    }
                    if reasons.count > visible.count {
                        Button("\(reasons.count - visible.count) weitere") { withAnimation(.smooth) { expanded = true } }
                            .font(Typo.subheadlineMedium)
                    }
                }
            }
        }
    }
}

// MARK: - Kennzahlen

private struct FiguresRow: View {
    let snapshot: Snapshot
    let stats: Statistics?
    let forecast: Forecast?
    private let fmt = Fmt()

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: Space.m)], spacing: Space.m) {
            FigureTile(title: "Überschuss jetzt", value: fmt.watts(max(0, snapshot.surplus ?? 0)),
                       hint: "für Auto und Warmwasser", color: Palette.pv)
            if let stats {
                FigureTile(title: "Sonne heute", value: fmt.kwh(stats.pvKwh), hint: "letzte 24 h", color: Palette.pv)
                FigureTile(title: "Autarkie", value: fmt.percent(stats.autarkyPct), hint: "letzte 24 h", color: Palette.ok)
            }
            if let forecast, forecast.active == true, let today = forecast.todayKwh {
                FigureTile(title: "Prognose heute", value: fmt.kwh(today),
                           hint: forecast.tomorrowKwh.map { "morgen \(fmt.kwh($0))" } ?? "", color: Palette.pv)
            }
        }
    }
}

private struct FigureTile: View {
    let title: LocalizedStringKey
    let value: String
    let hint: String
    let color: DomainColor

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(Typo.caption).foregroundStyle(.secondary)
            Text(value).font(Typo.metricSmall).monospacedDigit().contentTransition(.numericText())
            if !hint.isEmpty { Text(hint).font(Typo.caption2).foregroundStyle(.secondary) }
        }
        .frame(maxWidth: .infinity, minHeight: 72, alignment: .leading)
        .padding(Space.m)
        .background(Color(uiColor: .secondarySystemGroupedBackground), in: .rect(cornerRadius: Radius.card, style: .continuous))
        .overlay(alignment: .topTrailing) {
            Circle().fill(color.fill).frame(width: 8, height: 8).padding(Space.m)
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Ton → Farbe/Symbol

extension Explanation.Tone {
    var domain: DomainColor {
        switch self {
        case .pv: Palette.pv
        case .gridImport: Palette.gridImport
        case .gridExport: Palette.gridExport
        case .battery: Palette.battery
        case .car: Palette.car
        case .water: Palette.water
        case .warn: Palette.warn
        case .idle: Palette.house
        case .error: Palette.error
        }
    }

    var symbol: String {
        switch self {
        case .pv: "sun.max.fill"
        case .gridImport: "arrow.down.to.line"
        case .gridExport: "arrow.up.to.line"
        case .battery: "battery.75percent"
        case .car: "car.fill"
        case .water: "drop.fill"
        case .warn: "pause.circle.fill"
        case .idle: "moon.stars.fill"
        case .error: "exclamationmark.octagon.fill"
        }
    }
}

extension Explanation.Symbol {
    var systemName: String {
        switch self {
        case .pause: "pause.circle"
        case .warning: "exclamationmark.triangle"
        case .battery: "battery.75percent"
        case .price: "eurosign.circle"
        case .car: "car"
        case .water: "drop"
        case .export: "arrow.up.to.line"
        }
    }
}
