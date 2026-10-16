import MinePowerKit
import SwiftUI

/// Hauptnavigation: iPhone (kompakt) mit Tab-Leiste, iPad (regulär) mit Seitenleiste.
///
/// Entscheidet nach Größenklasse, nicht nach Gerät: Ein iPad in Slide Over
/// oder schmalem Stage-Manager-Fenster bekommt die iPhone-Navigation.
struct MainShell: View {
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(Router.self) private var router
    @Environment(AppSession.self) private var session

    var body: some View {
        @Bindable var router = router
        Group {
            if sizeClass == .regular {
                SplitShell()
            } else {
                TabShell()
            }
        }
        // Leerer Server: Assistent einmal je Sitzung von selbst anbieten.
        .task(id: session.setup?.needsDevices) {
            guard session.setup?.needsDevices == true, session.canConfigure, !router.wizardOffered else { return }
            router.wizardOffered = true
            router.showWizard = true
        }
        .fullScreenCover(isPresented: $router.showWizard) {
            SetupWizardView(api: session.api)
                .environment(session)
        }
        .overlay(alignment: .top) { ConnectionBanner() }
        .fullScreenCover(isPresented: $router.showWall) {
            WallView()
        }
    }
}

// MARK: - iPhone

private struct TabShell: View {
    @Environment(Router.self) private var router
    @State private var tab: Tab = .section(.dashboard)
    @State private var morePath: [AppSection] = []

    enum Tab: Hashable {
        case section(AppSection)
        case more
    }

    var body: some View {
        TabView(selection: $tab) {
            ForEach(AppSection.primary) { section in
                NavigationStack {
                    SectionView(section: section)
                }
                .tabItem { Label(section.title, systemImage: section.symbol) }
                .tag(Tab.section(section))
            }
            NavigationStack(path: $morePath) {
                MoreList()
                    .navigationDestination(for: AppSection.self) { SectionView(section: $0) }
            }
            .tabItem { Label("Mehr", systemImage: "ellipsis.circle") }
            .tag(Tab.more)
        }
        .minimizingTabBar()
        .onChange(of: router.request, initial: true) { _, request in
            guard let section = request?.section else { return }
            if AppSection.primary.contains(section) {
                tab = .section(section)
            } else {
                tab = .more
                morePath = [section]
            }
        }
        .onChange(of: tab) { _, tab in
            if case .section(let section) = tab { router.selection = section }
        }
        .onChange(of: morePath) { _, path in
            if let section = path.last { router.selection = section }
        }
    }
}

/// "Mehr": alle weiteren Bereiche plus Wandanzeige.
private struct MoreList: View {
    @Environment(Router.self) private var router
    @Environment(AppSession.self) private var session

    var body: some View {
        List {
            Section {
                ForEach(AppSection.more) { section in
                    NavigationLink(value: section) {
                        Label { Text(section.title) } icon: { DomainIcon(symbol: section.symbol, color: section.domain, size: 30) }
                    }
                }
            }
            Section {
                Button {
                    router.showWall = true
                } label: {
                    Label { Text("Wandanzeige") } icon: { DomainIcon(symbol: "rectangle.on.rectangle", color: Palette.house, size: 30) }
                }
                .foregroundStyle(.primary)
            } footer: {
                Text("Vollbild für ein festes Display, gut lesbar aus der Entfernung.")
            }
        }
        .navigationTitle("Mehr")
    }
}

// MARK: - iPad

private struct SplitShell: View {
    @Environment(Router.self) private var router
    @Environment(AppSession.self) private var session
    @State private var visibility: NavigationSplitViewVisibility = .all

    var body: some View {
        @Bindable var router = router
        NavigationSplitView(columnVisibility: $visibility) {
            List(selection: Binding<AppSection?>(get: { router.selection }, set: { if let s = $0 { router.selection = s } })) {
                Section {
                    ForEach(AppSection.allCases) { section in
                        Label(section.title, systemImage: section.symbol)
                            .tag(section)
                    }
                }
                Section {
                    Button {
                        router.showWall = true
                    } label: {
                        Label("Wandanzeige", systemImage: "rectangle.on.rectangle")
                    }
                }
            }
            .navigationTitle("MinePower")
            .navigationSplitViewColumnWidth(min: 220, ideal: 260, max: 320)
            .safeAreaInset(edge: .bottom) { SidebarFooter() }
        } detail: {
            NavigationStack {
                SectionView(section: router.selection)
            }
            .id(router.selection)
        }
        .navigationSplitViewStyle(.balanced)
    }
}

/// Server und Konto unten in der Seitenleiste.
private struct SidebarFooter: View {
    @Environment(AppSession.self) private var session

    var body: some View {
        if let server = session.server {
            HStack(spacing: Space.s) {
                Image(systemName: session.isDemo ? "play.rectangle" : "server.rack")
                    .foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 0) {
                    Text(server.name).font(Typo.subheadlineMedium)
                    Text(session.user?.name ?? server.displayAddress)
                        .font(Typo.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(Space.m)
            .accessibilityElement(children: .combine)
        }
    }
}

// MARK: - Inhalt je Bereich

struct SectionView: View {
    let section: AppSection

    var body: some View {
        switch section {
        case .dashboard: DashboardView()
        case .car: CarView()
        case .water: WaterView()
        case .battery: BatteryView()
        case .tariff: TariffView()
        case .statistics: StatisticsView()
        case .diagnose: DiagnoseView()
        case .settings: SettingsView()
        case .changelog: ChangelogView()
        }
    }
}

// MARK: - Verbindung

/// Hinweis, wenn die Werte nicht live sind. Bei bestehender Verbindung unsichtbar.
private struct ConnectionBanner: View {
    @Environment(AppSession.self) private var session

    var body: some View {
        let connection = session.live.connection
        if session.isOffline || connection == .offline || connection == .reconnecting {
            Label(text(connection), systemImage: connection == .reconnecting ? "arrow.triangle.2.circlepath" : "wifi.slash")
                .font(Typo.caption)
                .padding(.horizontal, Space.m)
                .padding(.vertical, 6)
                .glassControl(interactive: false)
                .padding(.top, 2)
                .transition(.move(edge: .top).combined(with: .opacity))
                .accessibilityAddTraits(.updatesFrequently)
        }
    }

    private func text(_ connection: LiveStore.Connection) -> LocalizedStringKey {
        if connection == .reconnecting { return "Verbindung wird wiederhergestellt …" }
        if let age = session.live.age(), age.isFinite {
            return "Offline – Werte von vor \(Fmt().duration(age))"
        }
        return "Server nicht erreichbar"
    }
}

private extension View {
    /// Tab-Leiste zieht sich beim Scrollen zusammen (iOS 26, nur iPhone).
    @ViewBuilder
    func minimizingTabBar() -> some View {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) {
            tabBarMinimizeBehavior(.onScrollDown)
        } else {
            self
        }
        #else
        self
        #endif
    }
}
