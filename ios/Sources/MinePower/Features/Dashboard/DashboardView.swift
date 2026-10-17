import MinePowerKit
import SwiftUI

/// Übersicht – gleicher Aufbau wie `Dashboard.tsx` der Weboberfläche:
/// Hinweise, Kopf ("Was passiert, und warum"), Energiefluss, Schnellkarten
/// je Gerät, Kennzahlen, Verlauf. Dazu unten die schwebende Daumenleiste
/// (Boost, Sofort laden, Auto aus) als Liquid Glass.
struct DashboardView: View {
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router
    @State private var stats: Resource<Statistics>?
    @State private var forecast: Resource<Forecast>?
    /// Breite des Seiteninhalts für `.home-grid` (wie die Breakpoints im Web).
    @State private var contentWidth: CGFloat = 0

    var body: some View {
        Group {
            if let snapshot = session.live.snapshot {
                content(snapshot)
            } else if session.isOffline || session.live.connection == .offline {
                Page { ErrorState(error: .unreachable("")) { try? await session.live.refresh() } }
            } else {
                Page {
                    SkeletonCards(count: 1, height: 180)
                    SkeletonCards(count: 1, height: 340)
                }
            }
        }
        .navigationTitle("Übersicht")
        .toolbar { BrandToolbar() }
        .task { await loadExtras() }
    }

    @ViewBuilder
    private func content(_ snapshot: Snapshot) -> some View {
        let devices = snapshot.enabledDevices
        if devices.isEmpty {
            Page {
                Card {
                    EmptyState(icon: .devices, title: "Noch keine Geräte",
                               message: "Wechselrichter und Netzzähler einrichten") {
                        if session.canConfigure {
                            Button("Assistent starten") { router.showWizard = true }
                                .buttonStyle(.web(.primary, fullWidth: false))
                        }
                    }
                }
            }
        } else {
            let explanation = Explanation(snapshot)
            Page(refresh: { await refresh() }) {
                DashboardBanners(snapshot: snapshot)
                HeroCard(snapshot: snapshot, explanation: explanation)
                    .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { contentWidth = $0 }
                HomeGrid(width: contentWidth) {
                    Card {
                        VStack(alignment: .leading, spacing: Space.s) {
                            CardHead("Energiefluss")
                            PowerFlowView(snapshot: snapshot) { router.show($0) }
                        }
                    }
                } side: { arrangement in
                    QuickCards(snapshot: snapshot, arrangement: arrangement)
                }
                Figures(snapshot: snapshot, stats: stats?.value, forecast: forecast?.value)
                HistoryCard()
            }
            .safeAreaInset(edge: .bottom) {
                if session.canControl { ActionBar(snapshot: snapshot) }
            }
        }
    }

    private func loadExtras() async {
        if stats == nil {
            let api = session.api
            stats = Resource { try await api.statistics(.day) }
            forecast = Resource { try await api.forecast() }
        }
        // Wie im Web: Tageszahlen jede Minute nachziehen, solange die Seite sichtbar ist.
        while !Task.isCancelled {
            async let a: Void = stats?.reload() ?? ()
            async let b: Void = forecast?.reload() ?? ()
            _ = await (a, b)
            try? await Task.sleep(for: .seconds(60))
        }
    }

    private func refresh() async {
        try? await session.live.refresh()
        await stats?.reload()
        await forecast?.reload()
    }
}

// MARK: - Fluss und Schnellzugriff

/// `.home-grid`: ab 1024 pt Fluss links (1,3) und Schnellkarten rechts
/// (mindestens 340 pt); darunter untereinander, ab 640 pt die Karten zweispaltig.
private struct HomeGrid<Flow: View, Side: View>: View {
    let width: CGFloat
    @ViewBuilder var flow: Flow
    @ViewBuilder var side: (QuickCards.Arrangement) -> Side

    var body: some View {
        if width >= 1024 {
            let gap = Space.xl
            let sideWidth = max(340, (width - gap) / 2.3)
            HStack(alignment: .top, spacing: gap) {
                flow.frame(width: width - gap - sideWidth)
                side(.stack).frame(width: sideWidth)
            }
        } else {
            flow
            side(width >= 640 ? .twoColumns : .stack)
        }
    }
}

// MARK: - Kopf

/// `.hero`: Überschrift in Alltagssprache, Chips, erster Grund (+ weitere).
private struct HeroCard: View {
    let snapshot: Snapshot
    let explanation: Explanation
    @Environment(AppSession.self) private var session
    @State private var showAll = false
    @State private var confirmPause = false
    @Environment(\.horizontalSizeClass) private var sizeClass

    var body: some View {
        let grid = snapshot.gridPower ?? 0
        Card(tone: explanation.tone.domain) {
            VStack(alignment: .leading, spacing: Space.l) {
                HStack(alignment: .top, spacing: Space.m) {
                    Text(explanation.headline)
                        .font(Typo.hero)
                        .foregroundStyle(Web.textStrong)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                    Spacer(minLength: 0)
                    if session.canControl {
                        Button {
                            if snapshot.isPaused { setPaused(false) } else { confirmPause = true }
                        } label: {
                            Icon(snapshot.isPaused ? .play : .pause, size: 14)
                                .frame(width: 44, height: 44)
                        }
                        .buttonStyle(.web(snapshot.isPaused ? .primary : .ghost, size: .small, fullWidth: false))
                        .accessibilityLabel(Text(snapshot.isPaused ? "Fortsetzen" : "Anhalten"))
                        .accessibilityIdentifier("pause")
                    }
                }
                FlowLayout(spacing: Space.s) {
                    if let battery = snapshot.battery, battery.soc != nil {
                        Chip(icon: .battery, tone: Palette.battery) { Text(Fmt().percent(battery.soc)).monospacedDigit() }
                    }
                    if let price = snapshot.priceCt {
                        let cheap = snapshot.priceCheap == true
                        Chip(icon: .price, tone: cheap ? Palette.pv : nil, highlighted: cheap) {
                            Text(Fmt().cents(price)).monospacedDigit()
                            Text(cheap ? "günstig" : "teuer").font(Typo.subheadlineMedium)
                                .foregroundStyle(cheap ? Palette.pv.ink : Web.textDim)
                        }
                    }
                    Chip(icon: .grid, tone: grid > 50 ? Palette.gridImport : grid < -50 ? Palette.gridExport : nil) {
                        Text(Fmt().watts(abs(grid))).monospacedDigit()
                        Text(grid > 50 ? "Bezug" : grid < -50 ? "Einspeisung" : "ausgeglichen")
                            .font(Typo.subheadlineMedium).foregroundStyle(Web.textDim)
                    }
                }
                reasons
            }
        }
        .confirmationDialog("Regelung pausieren?", isPresented: $confirmPause, titleVisibility: .visible) {
            Button("Pausieren", role: .destructive) { setPaused(true) }
        } message: {
            Text("Auto und Warmwasser werden abgeschaltet.")
        }
    }

    /// Handy: erster Grund und "+n weitere"; breit: Liste mit Überschrift "Warum?".
    @ViewBuilder
    private var reasons: some View {
        let list = explanation.reasons
        if sizeClass == .regular || showAll {
            VStack(alignment: .leading, spacing: Space.s) {
                if sizeClass == .regular {
                    Text("Warum?").font(Typo.subheadlineMedium).foregroundStyle(Web.textDim)
                }
                ForEach(list) { ReasonRow(reason: $0) }
            }
        } else if let first = list.first {
            Button {
                withAnimation(.smooth(duration: 0.25)) { showAll = true }
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                    Text(first.text).font(Typo.body).foregroundStyle(Web.text)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 0)
                    if list.count > 1 {
                        Text("+\(list.count - 1) weitere").font(Typo.subheadlineMedium).foregroundStyle(Palette.accent)
                    }
                }
            }
            .buttonStyle(.plain)
            .disabled(list.count < 2)
        }
    }

    private func setPaused(_ paused: Bool) {
        Task {
            do { try await session.live.setPaused(paused) } catch { session.handle(error) }
        }
    }
}

private struct ReasonRow: View {
    let reason: Explanation.Reason

    var body: some View {
        HStack(alignment: .top, spacing: Space.m) {
            Icon(reason.symbol.webIcon, size: 18)
                .foregroundStyle(reason.tone == .idle ? Web.textDim : reason.tone.domain.fill)
                .padding(.top, 2)
            Text(reason.text)
                .font(Typo.body)
                .foregroundStyle(reason.tone == .error ? Palette.error.ink : Web.text)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Hinweise oben

private struct DashboardBanners: View {
    let snapshot: Snapshot
    @Environment(AppSession.self) private var session
    @State private var dismissed: Set<String> = []

    var body: some View {
        let offline = snapshot.offlineDevices
        let failing = snapshot.failingDevices
        let warnings = snapshot.warnings ?? []
        // Schlüssel aus den Inhalten: Ein neues Problem erscheint wieder (wie im Web).
        let offlineKey = "off:" + offline.map { String($0.id) }.joined(separator: ",")
        let failingKey = "cmd:" + failing.map { "\($0.id)=\($0.commandError ?? "")" }.joined(separator: "|")
        let warnKey = "warn:" + warnings.joined(separator: "|")
        VStack(spacing: Space.m) {
            if snapshot.isPaused {
                Callout(tone: .warn, title: String(localized: "Regelung angehalten"),
                        text: String(localized: "Auto und Warmwasser sind aus."), icon: .pause) {
                    if session.canControl {
                        Button {
                            Task { do { try await session.live.setPaused(false) } catch { session.handle(error) } }
                        } label: {
                            HStack(spacing: 4) { Icon(.play, size: 14); Text("Fortsetzen") }
                        }
                        .buttonStyle(.web(.primary, size: .small, fullWidth: false))
                    }
                }
            }
            if let safety = snapshot.safety {
                Callout(.error, String(localized: "Sicherheitsstopp"), text: safety)
            }
            if !offline.isEmpty, !dismissed.contains(offlineKey) {
                Callout(tone: .warn, title: String(localized: "Gerät nicht erreichbar"),
                        text: offline.map(\.name).joined(separator: ", ")) { close(offlineKey) }
            }
            if !failing.isEmpty, !dismissed.contains(failingKey) {
                Callout(tone: .error, title: String(localized: "Befehl nicht übernommen"),
                        text: failing.map { "\($0.name): \($0.commandError ?? "")" }.joined(separator: "\n")) { close(failingKey) }
            }
            if !warnings.isEmpty, !dismissed.contains(warnKey) {
                Callout(tone: .warn, title: String(localized: "Hinweise"), text: warnings.joined(separator: "\n")) { close(warnKey) }
            }
        }
    }

    private func close(_ key: String) -> some View {
        Button {
            withAnimation { _ = dismissed.insert(key) }
        } label: {
            Icon(.close, size: 16).foregroundStyle(Web.textDim).frame(width: 40, height: 40)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(Text("Schließen"))
    }
}

// MARK: - Kennzahlen

/// `.figures`: Verfügbar, Verschenkt, Prognose, Strompreis.
private struct Figures: View {
    let snapshot: Snapshot
    let stats: Statistics?
    let forecast: Forecast?
    private let fmt = Fmt()

    var body: some View {
        Card(padding: 0) {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 0)], spacing: 0) {
                figure("Verfügbar", fmt.watts(max(0, snapshot.surplus ?? 0)), hint: String(localized: "für Auto und Warmwasser"),
                       color: (snapshot.surplus ?? 0) > 100 ? Palette.pv.ink : Web.textStrong)
                figure("Verschenkt", fmt.watts(snapshot.wasteW ?? 0),
                       hint: stats?.wastedKwh.map { String(localized: "\(fmt.kwh($0)) in 24 h") },
                       color: (snapshot.wasteW ?? 0) > 100 ? Palette.waste.ink : Web.textStrong)
                if let today = forecast?.todayKwh {
                    figure("Prognose heute", fmt.kwh(today), hint: nil, color: Palette.pv.ink)
                }
                if let price = snapshot.priceCt {
                    figure("Strompreis", fmt.cents(price),
                           hint: snapshot.priceLimitCt.map { String(localized: "Grenze \(fmt.cents($0))") },
                           color: snapshot.priceCheap == true ? Palette.pv.ink : Web.textStrong)
                }
            }
        }
    }

    private func figure(_ label: LocalizedStringKey, _ value: String, hint: String?, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(Typo.subheadlineMedium).foregroundStyle(Web.textDim)
            Text(value).font(Typo.title).foregroundStyle(color).monospacedDigit().contentTransition(.numericText())
            Text(hint ?? " ").font(Typo.footnote).foregroundStyle(Web.textDim)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Space.l)
        .overlay(alignment: .trailing) { Rectangle().fill(Web.line).frame(width: 1) }
        .overlay(alignment: .bottom) { Rectangle().fill(Web.line).frame(height: 1) }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Verlauf

/// Am Handy eingeklappt, damit die Seite kurz bleibt (wie im Web).
private struct HistoryCard: View {
    @Environment(\.horizontalSizeClass) private var sizeClass

    var body: some View {
        Card(padding: sizeClass == .regular ? nil : 0) {
            if sizeClass == .regular {
                VStack(alignment: .leading, spacing: Space.m) {
                    PowerHistoryChart(showsHeader: true)
                }
            } else {
                Disclosure(title: "Verlauf") {
                    PowerHistoryChart(showsHeader: false)
                }
                .padding(.horizontal, Space.l)
            }
        }
    }
}

// MARK: - Zuordnungen

extension Explanation.Tone {
    var domain: DomainColor {
        switch self {
        case .pv: Palette.pv
        case .gridImport: Palette.gridImport
        case .gridExport: Palette.waste
        case .battery: Palette.battery
        case .car: Palette.car
        case .water: Palette.water
        case .warn: Palette.warn
        case .idle: Palette.house
        case .error: Palette.error
        }
    }
}

extension Explanation.Symbol {
    var webIcon: WebIcon {
        switch self {
        case .pause: .pause
        case .warning: .warning
        case .battery: .battery
        case .price: .price
        case .car: .car
        case .water: .water
        case .export: .export
        }
    }
}
