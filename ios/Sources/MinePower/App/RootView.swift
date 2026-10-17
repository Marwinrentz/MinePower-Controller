import MinePowerKit
import SwiftUI

/// Wählt nach Sitzungszustand: Verbinden, Anmelden, Entsperren oder App.
struct RootView: View {
    @Environment(AppSession.self) private var session
    @State private var router = Router()

    var body: some View {
        @Bindable var session = session
        ZStack {
            switch session.phase {
            case .launching:
                LaunchView()
            case .needsServer:
                ConnectView()
                    .transition(.opacity)
            case .signedOut:
                SignInView()
                    .transition(.opacity)
            case .locked:
                LockView()
                    .transition(.opacity)
            case .signedIn:
                MainShell()
                    .id(session.server?.id)
                    .transition(.opacity)
            }
        }
        .animation(.smooth(duration: 0.35), value: session.phase)
        .overlay(alignment: .top) {
            if let notice = session.notice {
                NoticeBanner(notice: notice) { session.notice = nil }
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .padding(.top, Space.xs)
            }
        }
        .animation(.spring(duration: 0.4), value: session.notice)
        .environment(router)
        .task {
            guard session.beginLaunch() else { return }
            if LaunchOptions.current.demoScenario != nil {
                await session.startDemo()
                if let section = LaunchOptions.current.section { router.show(section) }
                router.showWall = LaunchOptions.current.wallMode
            } else {
                await session.launch()
            }
        }
        .onOpenURL { url in router.open(url) }
        .focusedSceneValue(\.router, router)
    }
}

/// Navigationszustand eines Fensters (iPad: mehrere Fenster, je eigener Zustand).
@MainActor
@Observable
final class Router {
    /// Sprungauftrag (Deep Link, Widget, Kurzbefehl). Eigene Kennung, damit
    /// auch ein Sprung zum zuletzt gewählten Bereich ausgeführt wird.
    struct Request: Equatable {
        let section: AppSection
        let id = UUID()
    }

    /// Aktueller Bereich, von der Navigation selbst gepflegt.
    var selection: AppSection = .dashboard
    private(set) var request: Request?
    /// Wandanzeige (Vollbild, ohne Bedienung).
    var showWall = false
    /// Einrichtungsassistent (Vollbild).
    var showWizard = false
    /// Automatisch höchstens einmal je Sitzung anbieten.
    var wizardOffered = false
    /// Bereich der Einstellungen, in den ein Sprung führt (wie `?tab=` im Web);
    /// die Einstellungen übernehmen ihn und setzen ihn zurück.
    var settingsTab: SettingsTab?
    /// Gerät, dessen Formular sich öffnen soll (wie `?edit=` im Web).
    var editDevice: Int?
    /// ⌘R: Die sichtbare Seite lädt neu (wie Ziehen zum Aktualisieren).
    private(set) var refreshRequest = 0

    func requestRefresh() { refreshRequest += 1 }

    func show(_ section: AppSection) {
        showWall = false
        selection = section
        request = Request(section: section)
    }

    /// Einstellungen in einem bestimmten Bereich öffnen.
    func showSettings(_ tab: SettingsTab, editDevice: Int? = nil) {
        settingsTab = tab
        self.editDevice = editDevice
        show(.settings)
    }

    func open(_ url: URL) {
        if url.host == "wall" || url.host == "wand" {
            showWall = true
        } else if let section = AppSection(url: url) {
            let tab = URLComponents(url: url, resolvingAgainstBaseURL: false)?
                .queryItems?.first { $0.name == "tab" }?.value.flatMap(SettingsTab.init(rawValue:))
            if section == .settings, let tab {
                showSettings(tab)
            } else {
                show(section)
            }
        }
    }
}

/// Kurzer Startbildschirm, solange die Sitzung geprüft wird (meist Millisekunden).
private struct LaunchView: View {
    var body: some View {
        BrandMark(size: 72)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(uiColor: .systemGroupedBackground))
            .accessibilityLabel(Text("MinePower wird gestartet"))
    }
}

