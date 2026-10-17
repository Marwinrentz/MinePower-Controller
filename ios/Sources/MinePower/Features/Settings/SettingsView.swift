import MinePowerKit
import SwiftUI

/// Einstellungen – Portierung von `frontend/src/pages/Settings.tsx` mit
/// denselben Bereichen, dazu "Konto & Server" für das, was nur die App hat
/// (Server wechseln, Face ID, Abmelden). Ungespeicherte Änderungen zeigt die
/// Speicherleiste unten (Liquid Glass).
struct SettingsView: View {
    typealias Tab = SettingsTab

    @Environment(AppSession.self) private var session
    @Environment(SettingsEditor.self) private var editor
    @Environment(Router.self) private var router
    @State private var tab: Tab = .regulation

    var body: some View {
        let tabs = Tab.allCases.filter { !$0.adminOnly || session.canConfigure }
        Page(refresh: { await editor.load(force: true) }) {
            SubNav(items: tabs.map { ($0, $0.title, $0.icon) }, selection: $tab)
            Group {
                if !editor.isLoaded, ![Tab.devices, .account, .info, .appearance, .users, .mcp, .backup].contains(tab) {
                    if let error = editor.error {
                        Card { ErrorState(error: error) { await editor.load(force: true) } }
                    } else {
                        SkeletonCards(count: 1, height: 300)
                    }
                } else {
                    switch tab {
                    case .regulation: RegulationSettings()
                    case .battery: BatterySettings()
                    case .priority: PrioritySettings()
                    case .devices: DevicesSettings()
                    case .notifications: NotificationSettings()
                    case .users: UsersSettings()
                    case .appearance: AppearanceSettings()
                    case .backup: BackupSettings()
                    case .mcp: McpSettings()
                    case .account: AccountSettings()
                    case .info: InfoSettings()
                    }
                }
            }
            // Nur der Inhalt: Die Reiterleiste bleibt für alle Rollen bedienbar.
            .disabled(!session.canConfigure && ![Tab.appearance, .account, .info, .mcp].contains(tab))
        }
        .safeAreaInset(edge: .bottom) { SaveBar() }
        .navigationTitle("Einstellungen")
        .toolbar { BrandToolbar() }
        .task { await editor.load() }
        .onChange(of: router.settingsTab, initial: true) { _, requested in
            guard let requested else { return }
            router.settingsTab = nil
            if !requested.adminOnly || session.canConfigure { tab = requested }
        }
    }
}

/// Bereiche der Einstellungen (wie `TABS` in `Settings.tsx`, dazu "Konto & Server").
enum SettingsTab: String, CaseIterable, Identifiable {
    case regulation, battery, priority, devices, notifications, users, appearance, backup, mcp, account, info
    var id: String { rawValue }

    var title: String {
        switch self {
        case .regulation: String(localized: "Regelung")
        case .battery: String(localized: "Batterie")
        case .priority: String(localized: "Reihenfolge")
        case .devices: String(localized: "Geräte")
        case .notifications: String(localized: "Benachrichtigungen")
        case .users: String(localized: "Benutzer")
        case .appearance: String(localized: "Darstellung")
        case .backup: String(localized: "Sicherung")
        case .mcp: String(localized: "KI-Assistent")
        case .account: String(localized: "Konto & Server")
        case .info: String(localized: "Info")
        }
    }

    var icon: WebIcon {
        switch self {
        case .regulation: .auto
        case .battery: .battery
        case .priority: .events
        case .devices: .devices
        case .notifications: .info
        case .users: .lock
        case .appearance: .display
        case .backup: .download
        case .mcp: .ai
        case .account: .shield
        case .info: .info
        }
    }

    var adminOnly: Bool { self == .users || self == .backup }
}

/// Bindung an einen Wert eines Einstellungsabschnitts.
@MainActor
extension SettingsEditor {
    func number(_ section: String, _ key: String, _ fallback: Double) -> Binding<Double> {
        Binding(get: { self.section(section).number(key, fallback) },
                set: { value in self.update(section) { $0.set(key, value) } })
    }

    func flag(_ section: String, _ key: String, _ fallback: Bool) -> Binding<Bool> {
        Binding(get: { self.section(section).bool(key, fallback) },
                set: { value in self.update(section) { $0.set(key, value) } })
    }

    func text(_ section: String, _ key: String, _ fallback: String = "") -> Binding<String> {
        Binding(get: { self.section(section).string(key, fallback) },
                set: { value in self.update(section) { $0.set(key, value) } })
    }

    /// Geheimnis: maskiert ("•••") gilt als gesetzt. Ein leeres Feld lässt den
    /// gespeicherten Stand unverändert (sonst meldete die Speicherleiste eine
    /// Änderung, die keine ist).
    func secret(_ section: String, _ key: String) -> Binding<String> {
        Binding(get: { let v = self.section(section).string(key, ""); return v == secretMask ? "" : v },
                set: { value in
                    self.update(section) { draft in
                        if value.isEmpty { draft.restore(key) } else { draft.set(key, value) }
                    }
                })
    }
}

// MARK: - Regelung

private struct RegulationSettings: View {
    @Environment(SettingsEditor.self) private var editor
    @Environment(AppSession.self) private var session

    var body: some View {
        let fmt = Fmt()
        let interval = String(Int(editor.section("regulation").number("interval_s", 10)))
        let forecastProvider = editor.section("forecast").string("provider", "none")
        Card {
            VStack(alignment: .leading, spacing: Space.l) {
                CardHead("Grundeinstellungen")
                Field(label: "Regeltakt") {
                    SegmentGrid(options: ["5", "10", "20"].map { .init(value: $0, title: "\($0) s", hint: $0 == "10" ? String(localized: "empfohlen") : nil) },
                                selection: ["5", "10", "20"].contains(interval) ? interval : "10", minimum: 90) { value in
                        editor.update("regulation") { $0.set("interval_s", Double(value) ?? 10) }
                    }
                }
                Field(label: "Hauptsicherung je Phase") {
                    NumberField(value: editor.number("regulation", "house_limit_a", 35), range: 6...250, unit: "A")
                }
                Field(label: "Phasen Hausanschluss") {
                    SmallSegment(options: [("1", "1"), ("3", "3")],
                                 selection: Binding(get: { editor.section("regulation").number("house_phases", 3) == 1 ? "1" : "3" },
                                                    set: { value in editor.update("regulation") { $0.set("house_phases", Double(value) ?? 3) } }))
                }
                Text("Totband ±\(fmt.watts(session.live.snapshot?.deadbandW ?? 100)), automatisch (Wetter: \(session.live.snapshot?.weather ?? "–"))")
                    .font(Typo.subheadline).foregroundStyle(Web.textDim)
            }
        }
        Card {
            VStack(alignment: .leading, spacing: Space.l) {
                CardHead("Sonne")
                ToggleRow(label: "Solarprognose nutzen", isOn: editor.flag("regulation", "use_forecast", true))
                Field(label: "Prognose-Dienst") {
                    SegmentGrid(options: [.init(value: "none", title: String(localized: "keiner")),
                                          .init(value: "forecast_solar", title: "forecast.solar")],
                                selection: forecastProvider, minimum: 120) { value in editor.update("forecast") { $0.set("provider", value) } }
                }
                if forecastProvider == "forecast_solar" {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: Space.l, alignment: .top)], spacing: Space.l) {
                        Field(label: "Breitengrad") { NumberField(value: editor.number("forecast", "lat", 0), range: -90...90, unit: "°", digits: 4) }
                        Field(label: "Längengrad") { NumberField(value: editor.number("forecast", "lon", 0), range: -180...180, unit: "°", digits: 4) }
                        Field(label: "Anlagengröße") { NumberField(value: editor.number("forecast", "kwp", 0), range: 0...1000, unit: "kWp", digits: 1) }
                        Field(label: "Dachneigung") { NumberField(value: editor.number("forecast", "declination", 30), range: 0...90, unit: "°") }
                    }
                }
            }
        }
        Card {
            Disclosure(title: "Experte", summary: String(localized: "Netz-Sollwert, Wecken, Ende von Eingriffen")) {
                Field(label: "Ziel am Netzpunkt", help: String(localized: "0 W = Nulleinspeisung")) {
                    NumberField(value: editor.number("regulation", "grid_target_w", 0), range: -20_000...20_000, unit: "W")
                }
                ToggleRow(label: "Schlafendes Auto wecken", help: String(localized: "Bei Überschuss oder günstigem Preis, höchstens alle 30 min"),
                          isOn: editor.flag("regulation", "vehicle_wake_enabled", true))
                Field(label: "'Aus' am Auto endet nach") {
                    NumberField(value: editor.number("regulation", "override_stop_h", 4), range: 0.5...48, unit: "h", digits: 1)
                }
                Field(label: "'Sofort laden' endet nach") {
                    NumberField(value: editor.number("regulation", "override_fast_h", 12), range: 0.5...48, unit: "h", digits: 1)
                }
            }
        }
    }
}

// MARK: - Batterie

private struct BatterySettings: View {
    @Environment(SettingsEditor.self) private var editor
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router
    @State private var store: DeviceSettingsStore?

    var body: some View {
        let fmt = Fmt()
        let regulation = editor.section("regulation")
        let live = (session.live.snapshot?.devices(.battery) ?? []).filter(\.enabled)
        let stored = (store?.devices.value ?? []).filter { $0.category == .battery }
        let controllable = live.contains { $0.controlEnabled != false && $0.controlCapable != false }
        let reserveMax = live.compactMap(\.reserveMax).first
        let reserve = regulation.number("battery_reserve_soc", 20)
        let gridCharge = regulation.bool("battery_grid_charge_enabled", false)
        let houseLimit = regulation.number("house_limit_a", 35)
        let phases = regulation.number("house_phases", 3) == 1 ? 1.0 : 3.0
        let manageReserve = stored.isEmpty || stored.allSatisfy { $0.settings.bool("manage_reserve") != false }

        if !live.isEmpty, !controllable {
            Callout(tone: .warn, title: String(localized: "Nur lesend. Reserve, Sperren und Netzladen erfordern Freischaltung.")) {
                Button("Unter Geräte freischalten") { router.showSettings(.devices) }
                    .buttonStyle(.web(.normal, size: .small, fullWidth: false))
            }
        }
        Card {
            VStack(alignment: .leading, spacing: Space.xl) {
                CardHead("Grundeinstellungen")
                StepSlider(label: "Reserve", value: editor.number("regulation", "battery_reserve_soc", 20), range: 0...80, step: 5,
                           tone: Palette.battery.fill, format: { "\(Int($0)) %" },
                           help: String(localized: "Untergrenze für die Entladung, wird in den Wechselrichter geschrieben")) { _ in }
                if let reserveMax, reserve > reserveMax {
                    Text("Wechselrichter max. \(Int(reserveMax)) %, \(Int(reserveMax))–\(Int(reserve)) % hält MinePower")
                        .font(Typo.subheadlineMedium).foregroundStyle(Web.text)
                }
                StepSlider(label: "Batterie zuerst bis", value: editor.number("regulation", "battery_priority_soc", 100), range: 0...100,
                           step: 5, tone: Palette.battery.fill,
                           format: { $0 >= 100 ? String(localized: "immer zuerst") : $0 <= 0 ? String(localized: "immer zuletzt") : "\(Int($0)) %" },
                           help: String(localized: "Bis hier Vorrang vor Auto und Warmwasser")) { _ in }
            }
        }
        Card {
            VStack(alignment: .leading, spacing: Space.l) {
                CardHead("Günstig aus dem Netz laden")
                ToggleRow(label: "Bei günstigem Strom aus dem Netz laden", help: String(localized: "Unter der Tarif-Grenze. Aus: nie aus dem Netz."),
                          isOn: editor.flag("regulation", "battery_grid_charge_enabled", false))
                if gridCharge, !controllable { Callout(.error, String(localized: "Kein steuerbares Batteriegerät")) }
                if gridCharge {
                    StepSlider(label: "Aus dem Netz laden bis", value: editor.number("regulation", "battery_grid_charge_soc", 50),
                               range: 20...100, step: 5, tone: Palette.battery.fill, format: { "\(Int($0)) %" }) { _ in }
                    ToggleRow(label: "Nur bei schlechter Prognose", isOn: editor.flag("regulation", "battery_grid_charge_forecast", true))
                }
                Text("Netzladen bei günstigem Preis wird nie aus der Batterie gedeckt.").font(Typo.subheadline).foregroundStyle(Web.textDim)
            }
        }
        Card {
            Disclosure(title: "Experte", summary: String(localized: "Reserve im Wechselrichter, Sperren, Grenzen, Vorgaben")) {
                ToggleRow(label: "Reserve in den Wechselrichter schreiben", help: String(localized: "Empfohlen. Gilt auch ohne MinePower."),
                          isOn: Binding(get: { manageReserve }, set: { value in Task { await setManageReserve(value, stored) } }))
                    .disabled(!controllable || stored.isEmpty)
                ToggleRow(label: "Speicher auch bei Sofort laden, Boost und Geräteprogramm sperren",
                          help: String(localized: "Aus: Speicher deckt diese Lasten bei vollem Netzpreis"),
                          isOn: editor.flag("regulation", "battery_protect_other_loads", false))
                StepSlider(label: "Speicher unterstützt Auto und Warmwasser ab", value: editor.number("regulation", "battery_ev_support_soc", 0),
                           range: 0...100, step: 5, tone: Palette.battery.fill,
                           format: { $0 <= 0 ? String(localized: "nie") : String(localized: "ab \(Int($0)) %") }) { _ in }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 170), spacing: Space.l, alignment: .top)], spacing: Space.l) {
                    Field(label: "Höchster Netzbezug beim Netzladen",
                          help: String(localized: "0 = aus dem Hausanschluss (\(fmt.watts(houseLimit * 230 * phases)))")) {
                        NumberField(value: editor.number("regulation", "grid_charge_max_w", 0), range: 0...100_000, unit: "W")
                    }
                    Field(label: "Überschuss-Lasten abschalten ab", help: String(localized: "Entladeleistung, ab der Überschuss-Lasten abschalten")) {
                        NumberField(value: editor.number("regulation", "battery_drain_limit_w", 250), range: 50...20_000, unit: "W")
                    }
                    Field(label: "Batteriekapazität", help: String(localized: "Nur wenn das Gerät sie nicht meldet")) {
                        NumberField(value: editor.number("regulation", "battery_capacity_kwh", 0), range: 0...500, unit: "kWh", digits: 1)
                    }
                    Field(label: "Leistung von Hand (Vorgabe)") {
                        NumberField(value: editor.number("regulation", "battery_manual_power_w", 3000), range: 100...50_000, unit: "W")
                    }
                    Field(label: "Längste Dauer von Hand") {
                        NumberField(value: editor.number("regulation", "battery_manual_max_min", 240), range: 5...720, unit: "min")
                    }
                }
            }
        }
        .task {
            if store == nil { store = DeviceSettingsStore(api: session.api) }
            await store?.devices.load(maxAge: 30)
        }
    }

    /// Je Speicher still speichern, danach eine einzige Rückmeldung.
    private func setManageReserve(_ on: Bool, _ stored: [Device]) async {
        var ok = true
        for device in stored {
            do {
                _ = try await session.api.updateDevice(device.id, DeviceUpdate(settings: device.settings.merging(["manage_reserve": .bool(on)]) { $1 }))
            } catch {
                ok = false
                session.handle(error)
            }
        }
        await store?.devices.reload()
        if ok { session.show(Notice(.success, String(localized: "Gespeichert"))) }
    }
}

// MARK: - Reihenfolge

private struct PrioritySettings: View {
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router
    @Environment(SettingsEditor.self) private var editor
    @State private var devices: [Device]?
    @State private var order: [Int] = []
    /// Speichern nacheinander, damit schnelles Umsortieren nicht in falscher Reihenfolge ankommt.
    @State private var saving: Task<Void, Never>?

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: Space.l) {
                CardHead(title: "Reihenfolge", subtitle: String(localized: "Oben = zuerst. Batterie: Einstellung 'Batterie zuerst bis'.")) {
                    Button { router.showSettings(.battery) } label: {
                        HStack(spacing: 4) { Icon(.battery, size: 16); Text("Batterie-Einstellungen") }
                    }
                    .buttonStyle(.web(.ghost, size: .small, fullWidth: false))
                }
                if let devices {
                    let loads = devices.filter { $0.category == .wallbox || $0.category == .waterHeater }
                    if loads.count < 2 {
                        Text("Weniger als zwei Lasten, keine Reihenfolge nötig").font(Typo.body).foregroundStyle(Web.textDim)
                    } else {
                        PriorityList(order: $order, devices: loads)
                            .onChange(of: order) { old, new in
                                guard !old.isEmpty, old != new else { return }
                                let previous = saving
                                saving = Task {
                                    await previous?.value
                                    await save(new)
                                }
                            }
                    }
                } else {
                    SkeletonCards(count: 1, height: 160)
                }
            }
        }
        .task {
            let list = (try? await session.api.devices()) ?? []
            let loads = list.filter { $0.category == .wallbox || $0.category == .waterHeater }.map(\.id)
            let saved = editor.section("priority").draft["order"]?.array?.compactMap(\.int) ?? []
            order = saved.filter(loads.contains) + loads.filter { !saved.contains($0) }
            devices = list
        }
    }

    private func save(_ next: [Int]) async {
        do {
            try await session.api.setPriority(next)
            session.show(Notice(.success, String(localized: "Gespeichert")))
            await editor.load(force: true)
        } catch {
            session.handle(error)
        }
    }
}

// MARK: - Benachrichtigungen

private struct NotificationSettings: View {
    @Environment(SettingsEditor.self) private var editor

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: Space.l) {
                CardHead("Benachrichtigungen")
                Field(label: "ntfy-Adresse", help: "z. B. https://ntfy.sh/<thema>") {
                    AppTextField(verbatim: "https://ntfy.sh/…", text: editor.text("notifications", "ntfy_url"))
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                }
                Field(label: "Telegram-Bot-Token") {
                    AppTextField(verbatim: editor.section("notifications").string("telegram_token", "") == secretMask ? String(localized: "gespeichert") : "",
                                 text: editor.secret("notifications", "telegram_token"), secure: true)
                        .textContentType(nil)
                }
                Field(label: "Telegram-Chat-ID") {
                    AppTextField(verbatim: "", text: editor.text("notifications", "telegram_chat_id"))
                        .keyboardType(.numbersAndPunctuation)
                }
            }
        }
    }
}

// MARK: - Darstellung

private struct AppearanceSettings: View {
    @AppStorage(Appearance.storageKey) private var appearance = Appearance.system
    @Environment(Router.self) private var router

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: Space.l) {
                CardHead("Darstellung")
                Field(label: "Farbschema") {
                    SegmentGrid(options: [.init(value: Appearance.light, title: String(localized: "Hell"), icon: .sun),
                                          .init(value: Appearance.dark, title: String(localized: "Dunkel"), icon: .moon),
                                          .init(value: Appearance.system, title: String(localized: "System"), icon: .display)],
                                selection: appearance, minimum: 90) { appearance = $0 }
                }
                Field(label: "Sprache", help: String(localized: "Die App folgt der Sprache in den iOS-Einstellungen.")) {
                    Button {
                        if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                    } label: {
                        HStack(spacing: 6) { Icon(.settings, size: 16); Text("In den iOS-Einstellungen ändern") }
                    }
                    .buttonStyle(.web(.normal, fullWidth: false))
                }
                Field(label: "Wand-Ansicht") {
                    Button {
                        router.showWall = true
                    } label: {
                        HStack(spacing: 6) { Icon(.fullscreen, size: 16); Text("Wand-Ansicht öffnen") }
                    }
                    .buttonStyle(.web(.normal, fullWidth: false))
                }
            }
        }
    }
}

// MARK: - Info

private struct InfoSettings: View {
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: Space.l) {
                CardHead("Info")
                Grid(alignment: .leading, horizontalSpacing: Space.xl, verticalSpacing: Space.s) {
                    GridRow {
                        Text("Version (Server)").foregroundStyle(Web.textDim)
                        Text(session.serverVersion ?? "–").monospacedDigit().foregroundStyle(Web.textStrong)
                    }
                    GridRow {
                        Text("Version (App)").foregroundStyle(Web.textDim)
                        Text(AppInfo.version).monospacedDigit().foregroundStyle(Web.textStrong)
                    }
                }
                .font(Typo.body)
                CompatibilityNotice()
                FlowLayout(spacing: Space.s) {
                    Button { router.show(.changelog) } label: {
                        HStack(spacing: 6) { Icon(.events, size: 16); Text("Changelog") }
                    }
                    .buttonStyle(.web(.normal, fullWidth: false))
                    Link(destination: githubURL) {
                        HStack(spacing: 6) { Icon(.github, size: 16); Text("GitHub") }
                    }
                    .buttonStyle(.web(.normal, fullWidth: false))
                }
                Text("Fehler gefunden? Den Diagnosebericht (Diagnose → Bericht) an ein GitHub-Issue anhängen hilft, die App zu verbessern.")
                    .font(Typo.subheadline).foregroundStyle(Web.textDim)
                Text("Schrift: Inter (SIL Open Font License 1.1). Lizenz der App: MIT.")
                    .font(Typo.caption2).foregroundStyle(Web.textDim)
            }
        }
    }
}

/// Version der App aus dem Bündel.
enum AppInfo {
    static var version: String {
        let short = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0.0"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "1"
        return "\(short) (\(build))"
    }
}
