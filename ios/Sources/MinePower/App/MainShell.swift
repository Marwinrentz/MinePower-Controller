import MinePowerKit
import SwiftUI

/// Hauptnavigation wie die Weboberfläche, in iOS-Struktur.
///
/// iPhone (kompakt): Tab-Leiste unten mit den Web-Symbolen – Übersicht,
/// Auto, Warmwasser, Batterie und "Mehr" (wie `AppShell.tsx`), auf iOS 26 als
/// Liquid Glass. iPad (regulär): Seitenleiste mit allen Bereichen. Entschieden
/// wird nach Größenklasse: Ein iPad in Slide Over bekommt die Tab-Leiste.
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
        .fullScreenCover(isPresented: $router.showWall) {
            WallView()
        }
        .modifier(UpdateSheet())
        .environment(session.settings)
    }
}

/// Projektseite (wie `GITHUB_URL` in `frontend/src/lib/links.ts`).
let githubURL = URL(string: "https://github.com/Marwinrentz/MinePower-Controller")!

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
                .tabItem { Label(section.title, web: section.icon) }
                .tag(Tab.section(section))
            }
            NavigationStack(path: $morePath) {
                MoreList()
                    .navigationDestination(for: AppSection.self) { SectionView(section: $0) }
            }
            .tabItem { Label("Mehr", web: .more) }
            .tag(Tab.more)
        }
        .tint(Palette.accent)
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

/// "Mehr" wie das Blatt im Web: weitere Bereiche, Wand-Ansicht, GitHub, Konto.
private struct MoreList: View {
    @Environment(Router.self) private var router
    @Environment(AppSession.self) private var session

    var body: some View {
        Page {
            Card(padding: Space.s) {
                VStack(spacing: 0) {
                    ForEach(AppSection.more) { section in
                        NavigationLink(value: section) {
                            MoreRow(icon: section.icon, title: section.title, tone: section.domain)
                        }
                        .buttonStyle(.plain)
                        if section != AppSection.more.last { Divider().overlay(Web.line).padding(.leading, 60) }
                    }
                }
            }
            Card(padding: Space.s) {
                VStack(spacing: 0) {
                    Button { router.showWall = true } label: {
                        MoreRow(icon: .fullscreen, title: "Wand-Ansicht", tone: nil)
                    }
                    .buttonStyle(.plain)
                    Divider().overlay(Web.line).padding(.leading, 60)
                    Link(destination: githubURL) {
                        MoreRow(icon: .github, title: "GitHub", tone: nil, external: true)
                    }
                    .buttonStyle(.plain)
                }
            }
            if let user = session.user {
                Text("Angemeldet als \(user.name) · \(session.server?.name ?? "")")
                    .font(Typo.subheadline)
                    .foregroundStyle(Web.textDim)
                    .padding(.horizontal, Space.s)
            }
        }
        .navigationTitle("Mehr")
        .toolbar { BrandToolbar() }
    }
}

private struct MoreRow: View {
    let icon: WebIcon
    let title: LocalizedStringKey
    let tone: DomainColor?
    var external = false

    var body: some View {
        HStack(spacing: Space.m) {
            DomainIcon(icon: icon, color: tone, size: 40)
            Text(title).font(Typo.bodyMedium).foregroundStyle(Web.textStrong)
            Spacer()
            Icon(external ? .export : .chevron, size: 16)
                .foregroundStyle(Web.textDim)
                .rotationEffect(.degrees(external ? 45 : 0))
        }
        .padding(.horizontal, Space.s)
        .frame(minHeight: 56)
        .contentShape(.rect)
    }
}

// MARK: - iPad

private struct SplitShell: View {
    @Environment(Router.self) private var router
    @Environment(\.supportsMultipleWindows) private var supportsMultipleWindows
    @Environment(\.openWindow) private var openWindow
    @State private var visibility: NavigationSplitViewVisibility = .all

    var body: some View {
        NavigationSplitView(columnVisibility: $visibility) {
            List(selection: Binding<AppSection?>(get: { router.selection }, set: { if let s = $0 { router.selection = s } })) {
                Section {
                    ForEach(AppSection.allCases) { section in
                        Label(section.title, web: section.icon)
                            .font(Typo.bodyMedium)
                            .tag(section)
                    }
                }
                Section {
                    Button {
                        router.showWall = true
                    } label: {
                        Label("Wand-Ansicht", web: .fullscreen).font(Typo.bodyMedium)
                    }
                    .contextMenu {
                        if supportsMultipleWindows {
                            Button {
                                openWindow(id: WallWindow.id)
                            } label: {
                                Label("In neuem Fenster öffnen", web: .fullscreen)
                            }
                        }
                    }
                    Link(destination: githubURL) {
                        Label("GitHub", web: .github).font(Typo.bodyMedium)
                    }
                }
            }
            .tint(Palette.accent)
            .scrollContentBackground(.hidden)
            .background(Web.surface)
            .navigationTitle("MinePower")
            .navigationSplitViewColumnWidth(min: 220, ideal: 248, max: 300)
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
                BrandMark(size: 28)
                VStack(alignment: .leading, spacing: 0) {
                    Text(server.name).font(Typo.subheadlineMedium).foregroundStyle(Web.textStrong)
                    Text(session.user?.name ?? server.displayAddress).font(Typo.caption2).foregroundStyle(Web.textDim)
                }
                Spacer()
                LiveIndicator()
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

/// Fenster nur mit der Wand-Ansicht; ohne Anmeldung ein Hinweis.
struct WallWindow: View {
    static let id = "wall"
    @Environment(AppSession.self) private var session
    @Environment(\.dismissWindow) private var dismissWindow

    var body: some View {
        Group {
            if session.phase == .signedIn {
                WallView { dismissWindow(id: Self.id) }
            } else {
                VStack(spacing: Space.l) {
                    BrandMark(size: 64)
                    Text(session.phase == .launching ? "MinePower wird gestartet" : "Zuerst in MinePower anmelden.")
                        .font(Typo.title3).foregroundStyle(Web.textStrong)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Web.bg)
            }
        }
        // Wird nur dieses Fenster wiederhergestellt, startet es die Sitzung selbst
        // (beginLaunch verhindert einen doppelten Start neben dem Hauptfenster).
        .task {
            guard session.beginLaunch() else { return }
            await session.launch()
        }
    }
}
