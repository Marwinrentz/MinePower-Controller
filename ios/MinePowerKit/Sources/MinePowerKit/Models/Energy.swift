import Foundation

/// Kennzahlen für einen Zeitraum (`GET /api/statistics?range=`).
public struct Statistics: Codable, Sendable, Hashable {
    public var range: String
    public var pvKwh: Double
    public var gridImportKwh: Double
    public var gridExportKwh: Double
    public var consumptionKwh: Double
    public var autarkyPct: Double
    public var selfConsumptionPct: Double
    public var chargedKwh: Double
    public var chargedSolarKwh: Double
    public var chargedSolarPct: Double
    public var savingsEur: Double
    public var co2SavedKg: Double
    public var sessionCount: Int
    public var wastedKwh: Double?
    public var wastedPct: Double?
    public var wastedValueEur: Double?

    enum CodingKeys: String, CodingKey {
        case range
        case pvKwh = "pv_kwh"
        case gridImportKwh = "grid_import_kwh"
        case gridExportKwh = "grid_export_kwh"
        case consumptionKwh = "consumption_kwh"
        case autarkyPct = "autarky_pct"
        case selfConsumptionPct = "self_consumption_pct"
        case chargedKwh = "charged_kwh"
        case chargedSolarKwh = "charged_solar_kwh"
        case chargedSolarPct = "charged_solar_pct"
        case savingsEur = "savings_eur"
        case co2SavedKg = "co2_saved_kg"
        case sessionCount = "session_count"
        case wastedKwh = "wasted_kwh"
        case wastedPct = "wasted_pct"
        case wastedValueEur = "wasted_value_eur"
    }
}

public enum StatsRange: String, CaseIterable, Sendable, Identifiable {
    case day = "24h", week = "7d", month = "30d", year = "365d"
    public var id: String { rawValue }
}

public enum HistoryRange: String, CaseIterable, Sendable, Identifiable {
    case hour = "1h", sixHours = "6h", day = "24h", week = "7d", month = "30d"
    public var id: String { rawValue }
}

public struct HistoryPoint: Codable, Sendable, Hashable, Identifiable {
    public var time: Date
    public var value: Double
    public var id: Date { time }
    public init(time: Date, value: Double) {
        self.time = time
        self.value = value
    }
}

/// `GET /api/history` liefert je angefragtem Feld eine Punktliste.
public typealias HistorySeries = [String: [HistoryPoint]]

public struct ChargeSession: Codable, Sendable, Hashable, Identifiable {
    public var id: Int
    public var deviceId: Int
    public var vehicleId: Int?
    public var userId: Int?
    public var rfidTag: String?
    public var startedAt: Date
    public var endedAt: Date?
    public var energyKwh: Double
    public var solarKwh: Double
    public var costEur: Double

    public var solarShare: Double { energyKwh > 0 ? solarKwh / energyKwh : 0 }

    enum CodingKeys: String, CodingKey {
        case id
        case deviceId = "device_id"
        case vehicleId = "vehicle_id"
        case userId = "user_id"
        case rfidTag = "rfid_tag"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case energyKwh = "energy_kwh"
        case solarKwh = "solar_kwh"
        case costEur = "cost_eur"
    }
}

public struct PriceSlot: Codable, Sendable, Hashable, Identifiable {
    public var start: Date
    public var minutes: Int
    public var spotCt: Double
    public var priceCt: Double
    public var id: Date { start }
    public var end: Date { start.addingTimeInterval(Double(minutes) * 60) }

    enum CodingKeys: String, CodingKey {
        case start, minutes
        case spotCt = "spot_ct"
        case priceCt = "price_ct"
    }
}

/// `GET /api/statistics/tariff`
public struct TariffInfo: Codable, Sendable, Hashable {
    public var provider: String
    public var currentCt: Double?
    public var cheapLimitCt: Double?
    public var prices: [PriceSlot]

    enum CodingKeys: String, CodingKey {
        case provider, prices
        case currentCt = "current_ct"
        case cheapLimitCt = "cheap_limit_ct"
    }

    public init(provider: String, currentCt: Double? = nil, cheapLimitCt: Double? = nil, prices: [PriceSlot] = []) {
        self.provider = provider
        self.currentCt = currentCt
        self.cheapLimitCt = cheapLimitCt
        self.prices = prices
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        provider = try c.decodeIfPresent(String.self, forKey: .provider) ?? "none"
        currentCt = try c.decodeIfPresent(Double.self, forKey: .currentCt)
        cheapLimitCt = try c.decodeIfPresent(Double.self, forKey: .cheapLimitCt)
        prices = try c.decodeIfPresent([PriceSlot].self, forKey: .prices) ?? []
    }
}

/// `GET /api/statistics/forecast`
public struct Forecast: Codable, Sendable, Hashable {
    public var provider: String?
    public var todayKwh: Double?
    public var tomorrowKwh: Double?
    public var dailyKwh: [String: Double]?
    public var updatedAt: Date?
    public var active: Bool?
    public var nowW: Double?

    enum CodingKeys: String, CodingKey {
        case provider, active
        case todayKwh = "today_kwh"
        case tomorrowKwh = "tomorrow_kwh"
        case dailyKwh = "daily_kwh"
        case updatedAt = "updated_at"
        case nowW = "now_w"
    }
}

// MARK: - Batterie

/// `GET /api/battery/status`
public struct BatteryStatus: Codable, Sendable, Hashable {
    public var manual: BatteryManual?
    public var manualLast: BatteryManualLast?
    public var gridCharging: Bool?
    public var defaults: Defaults?
    public var devices: [BatteryDevice]

    enum CodingKeys: String, CodingKey {
        case manual, defaults, devices
        case manualLast = "manual_last"
        case gridCharging = "grid_charging"
    }

    public struct Defaults: Codable, Sendable, Hashable {
        public var powerW: Double
        public var maxMinutes: Double
        public var reserveSoc: Double

        enum CodingKeys: String, CodingKey {
            case powerW = "power_w"
            case maxMinutes = "max_minutes"
            case reserveSoc = "reserve_soc"
        }
    }

    public struct BatteryDevice: Codable, Sendable, Hashable, Identifiable {
        public var id: Int
        public var name: String
        public var online: Bool
        public var controllable: Bool
        public var controlEnabled: Bool
        public var soc: Double?
        public var power: Double?
        public var mode: String?
        public var maxSoc: Double?
        public var minSoc: Double?
        public var targetMode: String?
        public var targetPowerW: Double?
        public var commandError: String?

        enum CodingKeys: String, CodingKey {
            case id, name, online, controllable, soc, power, mode
            case controlEnabled = "control_enabled"
            case maxSoc = "max_soc"
            case minSoc = "min_soc"
            case targetMode = "target_mode"
            case targetPowerW = "target_power_w"
            case commandError = "command_error"
        }
    }
}

/// `POST /api/battery/manual`
public struct BatteryCommand: Encodable, Sendable {
    public enum Action: String, Sendable, CaseIterable, Identifiable {
        case auto, charge, discharge, noDischarge = "no_discharge", hold
        public var id: String { rawValue }
        public var needsPower: Bool { self == .charge || self == .discharge }
    }

    public var action: Action
    public var powerW: Double?
    public var minutes: Double?
    public var targetSoc: Double?
    public var deviceId: Int?

    public init(action: Action, powerW: Double? = nil, minutes: Double? = nil, targetSoc: Double? = nil,
                deviceId: Int? = nil) {
        self.action = action
        self.powerW = powerW
        self.minutes = minutes
        self.targetSoc = targetSoc
        self.deviceId = deviceId
    }

    enum CodingKeys: String, CodingKey {
        case action, minutes
        case powerW = "power_w"
        case targetSoc = "target_soc"
        case deviceId = "device_id"
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(action.rawValue, forKey: .action)
        try c.encodeIfPresent(powerW, forKey: .powerW)
        try c.encodeIfPresent(minutes, forKey: .minutes)
        try c.encodeIfPresent(targetSoc, forKey: .targetSoc)
        try c.encodeIfPresent(deviceId, forKey: .deviceId)
    }
}
