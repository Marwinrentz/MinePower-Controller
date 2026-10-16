import Foundation

/// Der Live-Zustand der Anlage, wie ihn der Regelkreis je Takt sendet
/// (WebSocket `/api/ws` und `GET /api/status/live`).
///
/// Spiegel von `ControlLoop._publish` bzw. `Snapshot` in `frontend/src/lib/types.ts`.
/// Fast alles ist optional: Der Snapshot kommt auch, wenn die Netzmessung
/// ausgefallen ist oder kein Gerät antwortet. Ein fehlendes Feld darf nie den
/// ganzen Snapshot verwerfen.
public struct Snapshot: Codable, Sendable, Hashable {
    public var time: Date?
    public var pvPower: Double?
    /// `nil` ohne verlässliche Netzmessung (Sicherheits-Stopp). + Bezug, − Einspeisung.
    public var gridPower: Double?
    public var housePower: Double?
    public var wallboxPower: Double?
    public var waterPower: Double?
    public var battery: BatterySummary?
    public var surplus: Double?
    public var priceCt: Double?
    public var priceCheap: Bool?
    public var priceLimitCt: Double?
    public var priceReason: String?
    public var gridPriceWindow: Bool?
    public var batteryLocked: Bool?
    public var batteryGateReason: String?
    public var batteryReleased: Bool?
    public var batteryGridCharging: Bool?
    public var batteryManual: BatteryManual?
    public var batteryManualLast: BatteryManualLast?
    public var batteryReserveSoc: Double?
    public var batteryPlan: BatteryPlan?
    public var batteryFirst: Bool?
    public var batteryPrioritySoc: Double?
    public var gridLoads: [String]?
    public var gridLimitW: Double?
    public var gridBudgetLeftW: Double?
    public var warnings: [String]?
    public var safety: String?
    public var paused: Bool?
    public var wasteW: Double?
    public var wasteReason: String?
    public var gapFilledW: Double?
    public var deadbandW: Double?
    public var intervalS: Double?
    public var weather: String?
    public var devices: [SnapshotDevice]?

    public init() {}

    enum CodingKeys: String, CodingKey {
        case time
        case pvPower = "pv_power"
        case gridPower = "grid_power"
        case housePower = "house_power"
        case wallboxPower = "wallbox_power"
        case waterPower = "water_power"
        case battery, surplus
        case priceCt = "price_ct"
        case priceCheap = "price_cheap"
        case priceLimitCt = "price_limit_ct"
        case priceReason = "price_reason"
        case gridPriceWindow = "grid_price_window"
        case batteryLocked = "battery_locked"
        case batteryGateReason = "battery_gate_reason"
        case batteryReleased = "battery_released"
        case batteryGridCharging = "battery_grid_charging"
        case batteryManual = "battery_manual"
        case batteryManualLast = "battery_manual_last"
        case batteryReserveSoc = "battery_reserve_soc"
        case batteryPlan = "battery_plan"
        case batteryFirst = "battery_first"
        case batteryPrioritySoc = "battery_priority_soc"
        case gridLoads = "grid_loads"
        case gridLimitW = "grid_limit_w"
        case gridBudgetLeftW = "grid_budget_left_w"
        case warnings, safety, paused
        case wasteW = "waste_w"
        case wasteReason = "waste_reason"
        case gapFilledW = "gap_filled_w"
        case deadbandW = "deadband_w"
        case intervalS = "interval_s"
        case weather, devices
    }

    // MARK: - Abgeleitet

    public var pv: Double { pvPower ?? 0 }
    public var house: Double { housePower ?? 0 }
    public var isPaused: Bool { paused ?? false }
    public var allDevices: [SnapshotDevice] { devices ?? [] }
    public var enabledDevices: [SnapshotDevice] { allDevices.filter(\.enabled) }
    public var offlineDevices: [SnapshotDevice] { enabledDevices.filter { !$0.online } }
    public var failingDevices: [SnapshotDevice] { enabledDevices.filter { $0.online && $0.commandError != nil } }

    public func devices(_ category: DeviceCategory) -> [SnapshotDevice] {
        enabledDevices.filter { $0.category == category }
    }

    /// Steht der Netzpunkt im Totband?
    public var isBalanced: Bool { abs(gridPower ?? 0) <= max(deadbandW ?? 50, 50) }
    public var isImporting: Bool { (gridPower ?? 0) > max(deadbandW ?? 50, 50) }
    public var isExporting: Bool { (gridPower ?? 0) < -max(deadbandW ?? 50, 50) }

    /// Gesamtzustand für die Übersicht (wie 'dash.state*" im Web).
    public var overallState: OverallState {
        if safety != nil { return .safety }
        if isPaused { return .paused }
        if !failingDevices.isEmpty || !offlineDevices.isEmpty { return .attention }
        if (wasteW ?? 0) >= 200 { return .exporting }
        if isImporting { return .importing }
        if pv > 50 { return .solar }
        return .quiet
    }

    public enum OverallState: String, Sendable {
        case safety, paused, attention, exporting, importing, solar, quiet
    }
}

public struct BatterySummary: Codable, Sendable, Hashable {
    public var soc: Double?
    /// + Laden, − Entladen
    public var power: Double?
    public var capacityKwh: Double?

    public init(soc: Double? = nil, power: Double? = nil, capacityKwh: Double? = nil) {
        self.soc = soc
        self.power = power
        self.capacityKwh = capacityKwh
    }

    enum CodingKeys: String, CodingKey {
        case soc, power
        case capacityKwh = "capacity_kwh"
    }
}

public struct BatteryPlan: Codable, Sendable, Hashable {
    public var intent: String
    public var label: String
    public var reason: String
    public var source: String?
}

public struct BatteryManual: Codable, Sendable, Hashable {
    public var intent: String?
    public var mode: String
    public var powerW: Double?
    public var minutes: Double?
    public var started: Date?
    public var until: Date?
    public var remainingS: Double?
    public var targetSoc: Double?
    public var deviceId: Int?

    enum CodingKeys: String, CodingKey {
        case intent, mode, minutes, started, until
        case powerW = "power_w"
        case remainingS = "remaining_s"
        case targetSoc = "target_soc"
        case deviceId = "device_id"
    }
}

public struct BatteryManualLast: Codable, Sendable, Hashable {
    public var mode: String
    public var reason: String
    public var at: Date?
}

/// Kategorie eines Geräts. Unbekannte Werte kippen nie den ganzen Snapshot.
public enum DeviceCategory: String, Codable, Sendable, Hashable, CaseIterable, Identifiable {
    case inverter, meter, wallbox
    case waterHeater = "water_heater"
    case battery
    case unknown

    public var id: String { rawValue }

    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = DeviceCategory(rawValue: raw) ?? .unknown
    }

    /// Echte Kategorien in der Reihenfolge des Einrichtungsassistenten.
    public static let setupOrder: [DeviceCategory] = [.inverter, .meter, .wallbox, .battery, .waterHeater]

    public var sortOrder: Int {
        switch self {
        case .wallbox: 0
        case .waterHeater: 1
        case .battery: 2
        case .inverter: 3
        case .meter: 4
        case .unknown: 5
        }
    }
}

/// Wo ist das Auto? (seit 2.16)
public enum Presence: String, Codable, Sendable, Hashable {
    case charging, plugged, complete, unplugged
    case asleepPlugged = "asleep_plugged"
    case away
    case proxyOffline = "proxy_offline"
    case offline, unknown

    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = Presence(rawValue: raw) ?? .unknown
    }
}

public struct OverrideState: Codable, Sendable, Hashable {
    public var kind: String
    public var since: Date?
    public var until: Date?
    public var remainingS: Double?
    public var endsOn: String?

    enum CodingKeys: String, CodingKey {
        case kind, since, until
        case remainingS = "remaining_s"
        case endsOn = "ends_on"
    }
}

public struct BoostState: Codable, Sendable, Hashable {
    public var started: Date?
    public var until: Date?
    public var remainingS: Double?
    public var endMode: String?
    public var targetTempC: Double?

    enum CodingKeys: String, CodingKey {
        case started, until
        case remainingS = "remaining_s"
        case endMode = "end_mode"
        case targetTempC = "target_temp_c"
    }
}

public struct ChargeSessionLive: Codable, Sendable, Hashable {
    public var active: Bool
    public var energyKwh: Double?
    public var solarKwh: Double?
    public var costEur: Double?

    enum CodingKeys: String, CodingKey {
        case active
        case energyKwh = "energy_kwh"
        case solarKwh = "solar_kwh"
        case costEur = "cost_eur"
    }
}

/// Ein Gerät im Live-Snapshot.
public struct SnapshotDevice: Codable, Sendable, Hashable, Identifiable {
    public var id: Int
    public var name: String
    public var category: DeviceCategory
    public var driverId: String?
    public var enabledRaw: Bool?
    public var onlineRaw: Bool?
    public var lastError: String?
    public var commandError: String?
    public var uncontrolled: Bool?
    public var decision: JSONObject?
    public var lastSeen: Date?
    public var capabilities: [String]?
    public var data: JSONObject?
    // Wallbox / Fahrzeug
    public var mode: String?
    public var override: String?
    public var overrideState: OverrideState?
    public var presence: Presence?
    public var phases: Int?
    public var chargeLimitSoc: Double?
    public var phasesMode: String?
    public var phasesDetected: Int?
    public var minPowerW: Double?
    public var startThresholdW: Double?
    public var session: ChargeSessionLive?
    public var wakeNote: String?
    // Warmwasser
    public var boost: Bool?
    public var targetTempC: Double?
    public var boostTempC: Double?
    public var boostState: BoostState?
    public var boostEndMode: String?
    public var boostDurationMin: Double?
    public var maxPowerW: Double?
    public var surplusTempC: Double?
    public var selfModeSinceS: Double?
    // Batterie
    public var controlEnabled: Bool?
    public var controlCapable: Bool?
    public var targetMode: String?
    public var targetPowerW: Double?
    public var modeSettling: Bool?
    public var inverterReserve: Double?
    public var reserveMax: Double?
    public var guardMode: String?

    public init(id: Int, name: String, category: DeviceCategory) {
        self.id = id
        self.name = name
        self.category = category
    }

    enum CodingKeys: String, CodingKey {
        case id, name, category
        case driverId = "driver_id"
        case enabledRaw = "enabled"
        case onlineRaw = "online"
        case lastError = "last_error"
        case commandError = "command_error"
        case uncontrolled, decision
        case lastSeen = "last_seen"
        case capabilities, data, mode, override
        case overrideState = "override_state"
        case presence, phases
        case chargeLimitSoc = "charge_limit_soc"
        case phasesMode = "phases_mode"
        case phasesDetected = "phases_detected"
        case minPowerW = "min_power_w"
        case startThresholdW = "start_threshold_w"
        case session
        case wakeNote = "wake_note"
        case boost
        case targetTempC = "target_temp_c"
        case boostTempC = "boost_temp_c"
        case boostState = "boost_state"
        case boostEndMode = "boost_end_mode"
        case boostDurationMin = "boost_duration_min"
        case maxPowerW = "max_power_w"
        case surplusTempC = "surplus_temp_c"
        case selfModeSinceS = "self_mode_since_s"
        case controlEnabled = "control_enabled"
        case controlCapable = "control_capable"
        case targetMode = "target_mode"
        case targetPowerW = "target_power_w"
        case modeSettling = "mode_settling"
        case inverterReserve = "inverter_reserve"
        case reserveMax = "reserve_max"
        case guardMode = "guard_mode"
    }

    // MARK: - Abgeleitet

    public var enabled: Bool { enabledRaw ?? true }
    public var online: Bool { onlineRaw ?? false }
    public var values: JSONObject { data ?? [:] }
    public var reason: String? { decision?.string("reason") }
    public func can(_ capability: String) -> Bool { capabilities?.contains(capability) ?? false }

    /// Anzuzeigende Leistung. Wechselrichter und Zähler melden unter eigenen Schlüsseln.
    public var displayPower: Double {
        switch category {
        case .inverter: values.double("pv_power") ?? 0
        case .meter: values.double("grid_power") ?? 0
        default: values.double("power") ?? 0
        }
    }

    public var soc: Double? { values.double("soc") }
    public var temperatureC: Double? { values.double("temperature_c") }
    public var chargingState: WallboxState { WallboxState(rawValue: values.string("state") ?? "") ?? .unknown }
    public var isControllable: Bool { category == .wallbox || category == .waterHeater }
    public var hasChargeLimit: Bool { can("charge_limit") }
    public var canWake: Bool { can("wake") }

    /// Boost wäre sofort wieder aus, wenn das Wasser schon wärmer ist als die Boost-Grenze.
    public var boostWouldBePointless: Bool {
        guard let temp = temperatureC, let limit = boostTempC else { return false }
        return temp >= limit && (boostEndMode ?? "both") != "time"
    }
}

public enum WallboxState: String, Sendable, Hashable {
    case idle, connected, charging, complete, error, unknown
}
