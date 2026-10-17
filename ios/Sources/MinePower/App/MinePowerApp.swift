import MinePowerKit
import SwiftUI

@main
struct MinePowerApp: App {
    @State private var session: AppSession
    @Environment(\.scenePhase) private var scenePhase
    @AppStorage(Appearance.storageKey) private var appearance = Appearance.system

    init() {
        FontRegistry.registerFonts()
        let launch = LaunchOptions.current
        if let scenario = launch.demoScenario {
            DemoBackend.shared.reset(scenario: scenario)
        }
        _session = State(initialValue: AppSession(
            secrets: launch.isUITest ? InMemorySecretStore() : KeychainStore(),
            defaults: launch.defaults,
            makeSession: { demo in demo ? .demo() : .minePower() }))
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .appTypography()
                .tint(Palette.accent)
                .preferredColorScheme(LaunchOptions.current.colorScheme ?? appearance.colorScheme)
                .dynamicTypeSizeOverride(LaunchOptions.current.textSize)
        }
        .commands { AppCommands(session: session) }

        // Wand-Ansicht als eigenes Fenster (iPad: neben der App oder auf einem zweiten Bildschirm).
        WindowGroup("Wand-Ansicht", id: WallWindow.id) {
            WallWindow()
                .environment(session)
                .appTypography()
                .preferredColorScheme(appearance.colorScheme)
        }
        .onChange(of: scenePhase) { _, phase in
            // iOS trennt Sockets beim Sperren; beim Zurückkommen sofort neu verbinden.
            switch phase {
            case .active: Task { await session.enterForeground() }
            case .background: session.enterBackground()
            default: break
            }
        }
    }
}

/// Startparameter für UI-Tests und Screenshots (`-UITestDemo sunny`, `-UITestSection car`).
@MainActor
struct LaunchOptions {
    var isUITest = false
    var demoScenario: DemoBackend.Scenario?
    var section: AppSection?
    var wallMode = false
    var colorScheme: ColorScheme?
    var textSize: DynamicTypeSize?
    var defaults: UserDefaults = .standard

    static let current: LaunchOptions = {
        let args = ProcessInfo.processInfo.arguments
        func value(_ flag: String) -> String? {
            args.firstIndex(of: flag).flatMap { args.indices.contains($0 + 1) ? args[$0 + 1] : nil }
        }
        var options = LaunchOptions()
        guard args.contains("-UITest") || value("-UITestDemo") != nil else { return options }
        options.isUITest = true
        options.demoScenario = value("-UITestDemo").flatMap(DemoBackend.Scenario.init(rawValue:))
        options.section = value("-UITestSection").flatMap(AppSection.init(rawValue:))
        options.wallMode = args.contains("-UITestWall")
        options.colorScheme = value("-UITestAppearance") == "dark" ? .dark : nil
        options.textSize = value("-UITestTextSize") == "large" ? .accessibility2 : nil
        // Eigene, leere Ablage: Tests hängen nie von vorherigen Läufen ab.
        let suite = "uitest"
        UserDefaults().removePersistentDomain(forName: suite)
        options.defaults = UserDefaults(suiteName: suite) ?? .standard
        return options
    }()
}

/// Darstellung wie im Web: System, Hell oder Dunkel.
enum Appearance: String, CaseIterable, Identifiable {
    case system, light, dark

    static let storageKey = "ui.appearance"
    var id: String { rawValue }

    var colorScheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }

    var title: LocalizedStringKey {
        switch self {
        case .system: "System"
        case .light: "Hell"
        case .dark: "Dunkel"
        }
    }
}

private extension View {
    @ViewBuilder
    func dynamicTypeSizeOverride(_ size: DynamicTypeSize?) -> some View {
        if let size { dynamicTypeSize(size) } else { self }
    }
}
