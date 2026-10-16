import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Eingebaute Beispielanlage: beantwortet alle REST-Aufrufe ohne Netz.
///
/// Wofür: App-Prüfung durch Apple (ohne eigenen Server testbar), Screenshots
/// und UI-Tests in der CI und Interessenten, die die App vor dem Kauf von
/// Hardware ansehen wollen. Grundlage sind echte Antworten eines
/// Demo-Backends (`Resources/Demo`), die Live-Werte entstehen aus einem
/// kleinen Modell eines sonnigen Mittags bzw. einer Nacht.
public final class DemoBackend: Sendable {
    public static let baseURL = URL(string: "https://demo.minepower.invalid")!
    public static let token = "demo-token"
    public static let shared = DemoBackend()

    public enum Scenario: String, Sendable, CaseIterable {
        /// Mittag, Sonne, Auto lädt aus Überschuss, Warmwasser heizt.
        case sunny
        /// Nacht: Batterie versorgt das Haus, Auto unterwegs.
        case night
        /// Wolken: Netzbezug, Gerät offline, Hinweis aktiv.
        case attention
        /// Frisch installierter Server ohne Geräte (Einrichtungsassistent).
        case fresh
    }

    struct State: Sendable {
        var scenario: Scenario = .sunny
        var paused = false
        var wallboxMode = "pv_only"
        var override: String?
        var chargeLimit: Double = 80
        var waterMode = "pv_only"
        var waterBoost = false
        var batteryManual: BatteryCommand.Action?
        var settings: JSONObject = [:]
        var schedules: [Schedule] = []
        var vehicles: [Vehicle] = []
        var devices: [Device] = []
        var users: [User] = []
        var mcpTokens: [McpToken] = []
        var nextID = 100
        var started = Date.now
    }

    private let state = Locked(State())
    private let encoder = JSONEncoder.minePower()
    /// Einmal geladene Vorlagen (Live-Momentaufnahme, Treiberliste).
    private let liveTemplate: Snapshot
    private let drivers: [JSONValue]

    public init() {
        liveTemplate = Self.load(Snapshot.self, "live") ?? Snapshot()
        drivers = Self.load([JSONValue].self, "drivers") ?? []
        state.withLock { $0 = Self.initialState() }
    }

    static func initialState() -> State {
        var s = State()
        s.settings = load(SettingsEnvelope.self, "settings")?.values ?? [:]
        s.devices = load([Device].self, "devices") ?? []
        s.users = load([User].self, "users") ?? []
        s.vehicles = [Vehicle(id: 1, name: Loc.t("Familienauto", "Family car"), capacityKwh: 64,
                              wallboxDeviceId: 3, socSource: "wallbox", defaultTargetSoc: 80)]
        s.schedules = [Schedule(id: 1, deviceId: 4, daysMask: Weekdays.workdays.rawValue, startTime: "05:30",
                                endTime: "06:30", enabled: true)]
        return s
    }

    public var scenario: Scenario {
        get { state.current.scenario }
        set { state.withLock { $0.scenario = newValue } }
    }

    /// Auf Ausgangszustand zurück (UI-Tests).
    public func reset(scenario: Scenario = .sunny) {
        let fresh = Self.initialState()
        state.withLock { s in
            s = fresh
            s.scenario = scenario
            if scenario == .fresh {
                s.devices = []
                s.vehicles = []
                s.schedules = []
            }
        }
    }

    // MARK: - Anfrage beantworten

    public struct Reply: Sendable {
        public var status: Int
        public var body: Data
        public var contentType = "application/json"
    }

    public func reply(to request: URLRequest) -> Reply {
        guard let url = request.url else { return Reply(status: 400, body: Data()) }
        let method = request.httpMethod ?? "GET"
        var path = url.path
        if path.hasPrefix("/") { path.removeFirst() }
        let query = Dictionary(URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?
            .map { ($0.name, $0.value ?? "") } ?? []) { a, _ in a }
        let body = request.httpBody ?? Self.read(request.httpBodyStream)
        let json = body.flatMap { try? JSONDecoder().decode(JSONValue.self, from: $0) }
        let open = ["api/system/health", "api/system/setup-state", "api/auth/login"]
        if !open.contains(path), request.value(forHTTPHeaderField: "Authorization") != "Bearer \(Self.token)" {
            return error(401, "Nicht angemeldet")
        }
        return route(method, path, query, json)
    }

    private func route(_ method: String, _ path: String, _ query: [String: String], _ json: JSONValue?) -> Reply {
        let parts = path.split(separator: "/").map(String.init)
        let id = parts.dropFirst(2).lazy.compactMap { Int($0) }.first
        switch (method, path) {
        case ("GET", "api/system/health"):
            return ok(Health(status: "ok", controlLoop: "running", version: "3.1.1", time: .now))
        case ("GET", "api/system/setup-state"):
            let devices = state.current.devices
            return ok(SetupState(needsAdmin: false, needsDevices: devices.isEmpty,
                                 hasGridMeasurement: devices.contains { $0.category == .meter || $0.category == .inverter }))
        case ("POST", "api/auth/login"), ("POST", "api/auth/refresh"):
            return ok(TokenResponse(token: Self.token, user: me))
        case ("GET", "api/auth/me"), ("POST", "api/auth/me/seen"):
            return ok(me)
        case ("GET", "api/auth/users"):
            return ok(state.current.users)
        case ("POST", "api/auth/users"):
            let object = json?.object ?? [:]
            let user = state.withLock { s -> User in
                s.nextID += 1
                var user = User(id: s.nextID, email: object.string("email") ?? "", name: object.string("name") ?? "",
                                role: User.Role(rawValue: object.string("role") ?? "") ?? .user)
                user.disabled = false
                s.users.append(user)
                return user
            }
            return ok(user)
        case ("GET", "api/status/live"):
            return ok(snapshot())
        case ("POST", "api/status/pause"), ("POST", "api/status/resume"):
            state.withLock { $0.paused = path.hasSuffix("pause") }
            return okMessage()
        case ("GET", "api/devices"):
            return ok(state.current.devices)
        case ("GET", "api/devices/limits"):
            return raw("limits")
        case ("GET", "api/drivers"):
            guard let category = query["category"] else { return ok(drivers) }
            return ok(drivers.filter { $0.object?.string("category") == category })
        case ("POST", "api/devices/test"):
            return ok(TestResult(ok: true, message: Loc.t("Verbindung erfolgreich (Demo)", "Connection OK (demo)"),
                                 values: ["power": 1234], warnings: nil, writeTest: nil))
        case ("POST", "api/devices"):
            let object = json?.object ?? [:]
            let device = state.withLock { s -> Device in
                s.nextID += 1
                let device = Device(id: s.nextID, name: object.string("name") ?? "Gerät",
                                    category: DeviceCategory(rawValue: object.string("category") ?? "") ?? .unknown,
                                    driverId: object.string("driver_id") ?? "", config: object.object("config") ?? [:],
                                    settings: object.object("settings") ?? [:], enabled: object.bool("enabled") ?? true)
                s.devices.append(device)
                return device
            }
            return ok(device)
        case ("GET", "api/battery/status"):
            return raw("battery-status")
        case ("POST", "api/battery/manual"):
            let action = BatteryCommand.Action(rawValue: json?.object?.string("action") ?? "auto")
            state.withLock { $0.batteryManual = action == .auto ? nil : action }
            return raw("battery-status")
        case ("GET", "api/settings"):
            return ok(SettingsEnvelope(values: state.current.settings))
        case ("PUT", "api/settings"):
            if let values = json?.object?.object("values") {
                state.withLock { s in
                    for (key, value) in values {
                        var section = s.settings[key]?.object ?? [:]
                        for (k, v) in value.object ?? [:] where v.string != secretMask { section[k] = v }
                        s.settings[key] = .object(section)
                    }
                }
            }
            return okMessage()
        case ("GET", "api/settings/schedules"):
            return ok(state.current.schedules)
        case ("POST", "api/settings/schedules"):
            let o = json?.object ?? [:]
            let schedule = state.withLock { s -> Schedule in
                s.nextID += 1
                let schedule = Schedule(id: s.nextID, deviceId: o.int("device_id") ?? 0, daysMask: o.int("days_mask") ?? 127,
                                        startTime: o.string("start_time") ?? "00:00",
                                        endTime: o.string("end_time") ?? "01:00", enabled: o.bool("enabled") ?? true)
                s.schedules.append(schedule)
                return schedule
            }
            return ok(schedule)
        case ("PUT", "api/settings/priority"):
            let order = json?.object?["order"]?.array?.compactMap(\.int) ?? []
            state.withLock { $0.settings["priority"] = .object(["order": .array(order.map { .number(Double($0)) })]) }
            return okMessage()
        case ("GET", "api/vehicles"):
            return ok(state.current.vehicles)
        case ("POST", "api/vehicles"):
            let o = json?.object ?? [:]
            let vehicle = state.withLock { s -> Vehicle in
                s.nextID += 1
                let v = Vehicle(id: s.nextID, name: o.string("name") ?? "Auto", capacityKwh: o.double("capacity_kwh") ?? 60,
                                wallboxDeviceId: o.int("wallbox_device_id"), socSource: o.string("soc_source") ?? "none",
                                defaultTargetSoc: o.int("default_target_soc") ?? 80)
                s.vehicles.append(v)
                return v
            }
            return ok(vehicle)
        case ("GET", "api/statistics"):
            return ok(statistics(StatsRange(rawValue: query["range"] ?? "24h") ?? .day))
        case ("GET", "api/history"):
            let fields = (query["fields"] ?? "pv_power").split(separator: ",").map(String.init)
            return ok(history(HistoryRange(rawValue: query["range"] ?? "24h") ?? .day, fields: fields))
        case ("GET", "api/sessions"):
            return ok(sessions())
        case ("GET", "api/statistics/export.csv"):
            return Reply(status: 200, body: Data(csv().utf8), contentType: "text/csv")
        case ("GET", "api/statistics/tariff"):
            return ok(tariff())
        case ("GET", "api/statistics/forecast"):
            return ok(Forecast(provider: "forecast_solar", todayKwh: 38.4, tomorrowKwh: 29.1,
                               dailyKwh: nil, updatedAt: .now, active: true, nowW: 6200))
        case ("GET", "api/events"):
            return ok(events())
        case ("GET", "api/system/findings"):
            return ok(findings())
        case ("GET", "api/system/diagnostic-report"):
            return raw("diagnostics")
        case ("GET", "api/system/changelog"):
            return raw("changelog")
        case ("GET", "api/system/backup-status"):
            return ok(BackupStatus(count: 7, latest: "minepower-2026-10-16.json", dir: "/app/data/backups"))
        case ("GET", "api/system/export"):
            let export: JSONObject = ["version": "3.1.1", "settings": .object(state.current.settings)]
            return ok(export)
        case ("POST", "api/system/import"):
            return okMessage()
        case ("POST", "api/system/demo"):
            // Server-Demo: Simulationsgeräte anlegen (wie das Backend).
            state.withLock { s in
                if s.devices.isEmpty { s.devices = Self.initialState().devices }
                if s.scenario == .fresh { s.scenario = .sunny }
            }
            return okMessage()
        case ("GET", "api/mcp/tokens"):
            return ok(state.current.mcpTokens)
        case ("POST", "api/mcp/tokens"):
            let o = json?.object ?? [:]
            let token = state.withLock { s -> McpToken in
                s.nextID += 1
                let secret = "mp_demo_" + String(UUID().uuidString.prefix(8)).lowercased()
                let token = McpToken(id: s.nextID, name: o.string("name") ?? "KI", prefix: String(secret.prefix(10)),
                                     role: User.Role(rawValue: o.string("role") ?? "") ?? .readonly,
                                     createdAt: .now, lastUsedAt: nil, token: secret)
                var stored = token
                stored.token = nil
                s.mcpTokens.append(stored)
                return token
            }
            return ok(token)
        case ("GET", "api/mcp/tools"):
            return raw("mcp-tools")
        default:
            return routeWithID(method, parts, id, json)
        }
    }

    private func routeWithID(_ method: String, _ parts: [String], _ id: Int?, _ json: JSONValue?) -> Reply {
        guard let id else { return error(404, "Not Found") }
        let area = parts.count > 1 ? parts[1] : ""
        switch (method, area, parts.last) {
        case ("POST", "devices", "action"):
            apply(action: json?.object ?? [:], device: id)
            return okMessage()
        case ("PATCH", "devices", _):
            let o = json?.object ?? [:]
            let device = state.withLock { s -> Device? in
                guard let i = s.devices.firstIndex(where: { $0.id == id }) else { return nil }
                if let name = o.string("name") { s.devices[i].name = name }
                if let enabled = o.bool("enabled") { s.devices[i].enabled = enabled }
                if let config = o.object("config") { s.devices[i].config.merge(config) { $1 } }
                if let settings = o.object("settings") { s.devices[i].settings.merge(settings) { $1 } }
                return s.devices[i]
            }
            return device.map(ok) ?? error(404, "Gerät nicht gefunden")
        case ("DELETE", "devices", _):
            state.withLock { $0.devices.removeAll { $0.id == id } }
            return okMessage()
        case ("DELETE", "settings", _):
            state.withLock { $0.schedules.removeAll { $0.id == id } }
            return okMessage()
        case ("PATCH", "vehicles", _):
            let o = json?.object ?? [:]
            let vehicle = state.withLock { s -> Vehicle? in
                guard let i = s.vehicles.firstIndex(where: { $0.id == id }) else { return nil }
                if let name = o.string("name") { s.vehicles[i].name = name }
                if let cap = o.double("capacity_kwh") { s.vehicles[i].capacityKwh = cap }
                if let target = o.int("default_target_soc") { s.vehicles[i].defaultTargetSoc = target }
                return s.vehicles[i]
            }
            return vehicle.map(ok) ?? error(404, "Fahrzeug nicht gefunden")
        case ("DELETE", "vehicles", _):
            state.withLock { $0.vehicles.removeAll { $0.id == id } }
            return okMessage()
        case ("PATCH", "auth", _):
            let o = json?.object ?? [:]
            let user = state.withLock { s -> User? in
                guard let i = s.users.firstIndex(where: { $0.id == id }) else { return nil }
                if let name = o.string("name") { s.users[i].name = name }
                if let role = o.string("role").flatMap(User.Role.init(rawValue:)) { s.users[i].role = role }
                if let disabled = o.bool("disabled") { s.users[i].disabled = disabled }
                return s.users[i]
            }
            return user.map(ok) ?? error(404, "Konto nicht gefunden")
        case ("DELETE", "auth", _):
            if id == 1 { return error(409, Loc.t("Das eigene Konto kann nicht gelöscht werden", "You cannot delete your own account")) }
            state.withLock { $0.users.removeAll { $0.id == id } }
            return okMessage()
        case ("DELETE", "mcp", _):
            state.withLock { $0.mcpTokens.removeAll { $0.id == id } }
            return okMessage()
        default:
            return error(404, "Not Found")
        }
    }

    private func apply(action object: JSONObject, device id: Int) {
        let action = object.string("action") ?? ""
        let value = object["value"]
        state.withLock { s in
            switch (id, action) {
            case (3, "set_mode"): s.wallboxMode = value?.string ?? s.wallboxMode
            case (3, "set_charge_limit"): s.chargeLimit = value?.double ?? s.chargeLimit
            case (3, "fast"), (3, "stop"): s.override = action
            case (3, "auto"): s.override = nil
            case (4, "set_mode"): s.waterMode = value?.string ?? s.waterMode
            case (4, "boost"): s.waterBoost = true
            case (4, "boost_off"), (4, "auto"): s.waterBoost = false
            default: break
            }
        }
    }

    // MARK: - Live-Modell

    /// Momentaufnahme nach Szenario. Kleine Schwankungen, damit der Fluss lebt.
    public func snapshot(at now: Date = .now) -> Snapshot {
        let s = state.current
        var snap = liveTemplate
        let t = now.timeIntervalSince1970
        let wobble = { (period: Double, amount: Double) in sin(t / period) * amount }
        let elapsed = now.timeIntervalSince(s.started)

        var pv = 0.0, house = 420 + wobble(23, 60), wallbox = 0.0, water = 0.0, battery = 0.0
        var carSoc = 42 + min(elapsed / 60, 30), waterTemp = 51 + min(elapsed / 120, 8), batterySoc = 72.0
        var presence = Presence.charging
        var warnings: [String] = []
        let charging = s.override != "stop" && s.wallboxMode != "off" && carSoc < s.chargeLimit

        switch s.scenario {
        case .sunny:
            pv = 7200 + wobble(37, 380)
            wallbox = charging ? (s.override == "fast" ? 11000 : 3450 + wobble(19, 120)) : 0
            water = waterTemp < 60 || s.waterBoost ? 1500 : 0
            battery = 900 + wobble(29, 150)
            batterySoc = 72 + min(elapsed / 300, 20)
        case .night:
            pv = 0
            house = 380 + wobble(31, 40)
            presence = .away
            battery = -house
            batterySoc = 46
            carSoc = 68
            waterTemp = 54
        case .fresh:
            pv = 0
            house = 0
            presence = .away
        case .attention:
            pv = 1900 + wobble(11, 600)
            house = 1150 + wobble(17, 200)
            wallbox = charging ? 4140 : 0
            battery = 0
            batterySoc = 21
            warnings = [Loc.t("Netzbezug für das Auto (Sofortladen aktiv)", "Grid import for the car (fast charge active)")]
        }
        if s.paused { wallbox = 0; water = 0 }
        if s.batteryManual == .noDischarge || s.batteryManual == .hold, battery < 0 { battery = 0 }
        if presence != .charging { wallbox = 0 }
        let grid = house + wallbox + water + battery - pv

        snap.time = now
        snap.pvPower = pv.rounded()
        snap.housePower = house.rounded()
        snap.wallboxPower = wallbox.rounded()
        snap.waterPower = water
        snap.gridPower = grid.rounded()
        snap.surplus = (-grid).rounded()
        snap.battery = BatterySummary(soc: batterySoc.rounded(), power: battery.rounded(), capacityKwh: 9.6)
        snap.paused = s.paused
        snap.priceCt = s.scenario == .night ? 24.1 : 18.7
        snap.priceCheap = s.scenario == .night
        snap.priceReason = nil
        snap.warnings = warnings
        snap.batteryManual = s.batteryManual.map { BatteryManual(intent: $0.rawValue, mode: $0.rawValue) }
        snap.weather = s.scenario == .attention ? "wechselhaft" : "stabil"

        let configured = Set(s.devices.map(\.id))
        snap.devices = snap.devices?.filter { configured.contains($0.id) }.map { device in
            var d = device
            d.lastSeen = now
            switch d.category {
            case .inverter:
                d.data?["pv_power"] = .number(pv.rounded())
                d.data?["daily_yield_kwh"] = .number(s.scenario == .night ? 31.2 : 18.6)
            case .meter:
                d.data?["grid_power"] = .number(grid.rounded())
                if s.scenario == .attention { d.onlineRaw = false; d.lastError = Loc.t("Keine Antwort (Timeout)", "No response (timeout)") }
            case .wallbox:
                d.mode = s.wallboxMode
                d.override = s.override
                d.presence = presence
                d.chargeLimitSoc = s.chargeLimit
                d.data?["power"] = .number(wallbox.rounded())
                d.data?["soc"] = .number(carSoc.rounded())
                d.data?["state"] = .string(presence == .away ? "disconnected" : wallbox > 0 ? "charging" : "connected")
                d.data?["energy_session_kwh"] = .number((elapsed / 3600 * wallbox / 1000 + 6.4).rounded(toPlaces: 1))
                d.decision?["reason"] = .string(wallbox > 0 ? Loc.t("PV-Überschuss", "PV surplus")
                                                : Loc.t("wartet auf Überschuss", "waiting for surplus"))
                d.session = ChargeSessionLive(active: wallbox > 0, energyKwh: 6.4, solarKwh: 5.9, costEur: 0.41)
            case .waterHeater:
                d.mode = s.waterMode
                d.boost = s.waterBoost
                d.data?["power"] = .number(water)
                d.data?["temperature_c"] = .number(waterTemp.rounded())
                d.data?["is_on"] = .bool(water > 0)
            case .battery:
                d.data?["soc"] = .number(batterySoc.rounded())
                d.data?["power"] = .number(battery.rounded())
            default: break
            }
            return d
        }
        return snap
    }

    // MARK: - Auswertungen

    private func statistics(_ range: StatsRange) -> Statistics {
        let days: Double = switch range {
        case .day: 1
        case .week: 7
        case .month: 30
        case .year: 365
        }
        let pv = 31.2 * days, consumption = 22.8 * days
        return Statistics(range: range.rawValue, pvKwh: pv, gridImportKwh: 4.1 * days, gridExportKwh: 12.5 * days,
                          consumptionKwh: consumption, autarkyPct: 82, selfConsumptionPct: 60, chargedKwh: 9.8 * days,
                          chargedSolarKwh: 8.7 * days, chargedSolarPct: 89, savingsEur: 4.9 * days,
                          co2SavedKg: 7.4 * days, sessionCount: Int(max(1, days * 0.8)), wastedKwh: 0.6 * days,
                          wastedPct: 2, wastedValueEur: 0.05 * days)
    }

    private func history(_ range: HistoryRange, fields: [String], now: Date = .now) -> HistorySeries {
        let (span, step): (TimeInterval, TimeInterval) = switch range {
        case .hour: (3600, 60)
        case .sixHours: (6 * 3600, 300)
        case .day: (86_400, 600)
        case .week: (7 * 86_400, 3600)
        case .month: (30 * 86_400, 4 * 3600)
        }
        let calendar = Calendar(identifier: .gregorian)
        let start = now.addingTimeInterval(-span)
        var series: HistorySeries = [:]
        for field in fields {
            var points: [HistoryPoint] = []
            var time = start
            while time <= now {
                let comps = calendar.dateComponents([.hour, .minute], from: time)
                let hour = Double(comps.hour ?? 0) + Double(comps.minute ?? 0) / 60
                let sun = max(0, sin((hour - 6.5) / 13 * .pi))
                let pv = 7400 * pow(sun, 1.3) * (0.92 + 0.08 * sin(time.timeIntervalSince1970 / 1700))
                let house = 380 + (hour > 17 && hour < 22 ? 700 : 150) + 90 * sin(time.timeIntervalSince1970 / 900)
                let car = (hour > 10 && hour < 15) ? 3400.0 : 0
                let battery = pv > house + car ? min(1500, pv - house - car) : -min(house, 1200)
                let value: Double = switch field {
                case "pv_power": pv
                case "house_power": house
                case "wallbox_power": car
                case "battery_power": battery
                case "grid_power": house + car + battery - pv
                case "battery_soc": 30 + 60 * sun
                case "water_power": (hour > 11 && hour < 13.5) ? 1500 : 0
                case "water_temp": 48 + 12 * sun
                case "surplus": max(0, pv - house - car)
                case "waste_power": 0
                default: 0
                }
                points.append(HistoryPoint(time: time, value: value.rounded()))
                time = time.addingTimeInterval(step)
            }
            series[field] = points
        }
        return series
    }

    private func sessions(now: Date = .now) -> [ChargeSession] {
        (0..<12).map { i in
            let start = Calendar(identifier: .gregorian).startOfDay(for: now)
                .addingTimeInterval(Double(-i) * 86_400 * 1.6 + 10.5 * 3600)
            let energy = [18.2, 9.4, 22.8, 6.1, 14.9, 11.3][i % 6]
            let solar = energy * [0.94, 0.71, 0.88, 1.0, 0.62, 0.97][i % 6]
            return ChargeSession(id: 200 - i, deviceId: 3, vehicleId: 1, userId: 1, rfidTag: nil, startedAt: start,
                                 endedAt: start.addingTimeInterval(energy / 3.4 * 3600), energyKwh: energy,
                                 solarKwh: solar.rounded(toPlaces: 1),
                                 costEur: ((energy - solar) * 0.31 + solar * 0.08).rounded(toPlaces: 2))
        }
    }

    private func csv() -> String {
        var lines = ["ID;Start;Ende;Energie (kWh);Solar (kWh);Solaranteil (%);Kosten (EUR);Geraet;Nutzer;RFID"]
        for s in sessions() {
            let num = { (v: Double) in String(format: "%.2f", v).replacingOccurrences(of: ".", with: ",") }
            lines.append([String(s.id), BackendDate.format(s.startedAt), s.endedAt.map(BackendDate.format) ?? "",
                          num(s.energyKwh), num(s.solarKwh), num(s.solarShare * 100), num(s.costEur),
                          "Wallbox Garage", "Demo", ""].joined(separator: ";"))
        }
        return lines.joined(separator: "\n")
    }

    private func tariff(now: Date = .now) -> TariffInfo {
        let calendar = Calendar(identifier: .gregorian)
        let start = calendar.date(bySettingHour: calendar.component(.hour, from: now), minute: 0, second: 0, of: now) ?? now
        let slots = (0..<24).map { i -> PriceSlot in
            let time = start.addingTimeInterval(Double(i) * 3600)
            let hour = Double(calendar.component(.hour, from: time))
            let spot = 9 + 7 * cos((hour - 19) / 24 * 2 * .pi) - 6 * max(0, sin((hour - 7) / 12 * .pi))
            return PriceSlot(start: time, minutes: 60, spotCt: spot.rounded(toPlaces: 2),
                             priceCt: (spot * 1.19 + 14.2).rounded(toPlaces: 2))
        }
        return TariffInfo(provider: "awattar", currentCt: slots.first?.priceCt, cheapLimitCt: 22, prices: slots)
    }

    private func events(now: Date = .now) -> [AppEvent] {
        let items: [(TimeInterval, String, String, String)] = [
            (-120, "info", "control", Loc.t("Auto lädt mit PV-Überschuss (3,4 kW)", "Car charging from PV surplus (3.4 kW)")),
            (-900, "info", "device", Loc.t("Warmwasser: Ziel 60 °C erreicht", "Hot water: target 60 °C reached")),
            (-3600, "warning", "device", Loc.t("Netzzähler: kurz keine Antwort", "Grid meter: briefly unresponsive")),
            (-7200, "info", "battery", Loc.t("Batterie voll geladen", "Battery fully charged")),
            (-18_000, "info", "auth", "Login: demo@minepower.de"),
        ]
        return items.enumerated().map { i, item in
            AppEvent(id: 50 - i, time: now.addingTimeInterval(item.0), level: item.1, category: item.2,
                     message: item.3, data: nil)
        }
    }

    private func findings() -> FindingsReport {
        let attention = state.current.scenario == .attention
        let list: [Finding] = attention
            ? [Finding(level: .warn, title: Loc.t("Netzzähler zeitweise offline", "Grid meter intermittently offline"),
                       detail: Loc.t("12 Aussetzer in 7 Tagen", "12 dropouts in 7 days"),
                       fix: Loc.t("WLAN-Empfang des Zählers prüfen", "Check the meter's Wi-Fi reception"),
                       count: 12, kwh: nil)]
            : [Finding(level: .ok, title: Loc.t("Regelung stabil", "Control loop stable"),
                       detail: Loc.t("Keine Auffälligkeiten in 7 Tagen", "No issues in 7 days"), fix: nil, count: nil, kwh: nil)]
        return FindingsReport(generatedAt: .now, range: "7d", findings: list)
    }

    // MARK: - Hilfen

    private var me: User { state.current.users.first ?? User(id: 1, email: "demo@minepower.de", name: "Demo", role: .admin) }

    private func ok(_ value: some Encodable) -> Reply {
        Reply(status: 200, body: (try? encoder.encode(value)) ?? Data("{}".utf8))
    }

    private func okMessage() -> Reply { Reply(status: 200, body: Data(#"{"ok": true}"#.utf8)) }

    private func error(_ status: Int, _ message: String) -> Reply {
        Reply(status: status, body: (try? JSONEncoder().encode(["detail": message])) ?? Data())
    }

    private func raw(_ name: String) -> Reply {
        Reply(status: 200, body: Self.data(name) ?? Data("{}".utf8))
    }

    static func data(_ name: String) -> Data? {
        guard let url = Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Demo") else { return nil }
        return try? Data(contentsOf: url)
    }

    static func load<T: Decodable>(_ type: T.Type, _ name: String) -> T? {
        data(name).flatMap { try? JSONDecoder.minePower().decode(T.self, from: $0) }
    }

    static func read(_ stream: InputStream?) -> Data? {
        guard let stream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return data
    }
}

/// Transport, der statt ins Netz an `DemoBackend.shared` geht.
public final class DemoURLProtocol: URLProtocol {
    override public class func canInit(with request: URLRequest) -> Bool {
        request.url?.host == DemoBackend.baseURL.host
    }

    override public class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override public func startLoading() {
        let reply = DemoBackend.shared.reply(to: request)
        guard let url = request.url,
              let response = HTTPURLResponse(url: url, statusCode: reply.status, httpVersion: "HTTP/1.1",
                                             headerFields: ["Content-Type": reply.contentType]) else {
            client?.urlProtocol(self, didFailWithError: URLError(.badURL))
            return
        }
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: reply.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override public func stopLoading() {}
}

public extension URLSession {
    /// Session für die eingebaute Demo-Anlage.
    static func demo() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [DemoURLProtocol.self]
        return URLSession(configuration: config)
    }
}

extension Double {
    func rounded(toPlaces places: Int) -> Double {
        let factor = pow(10, Double(places))
        return (self * factor).rounded() / factor
    }
}
