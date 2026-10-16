import Foundation

/// Ein eingerichtetes Gerät (`GET /api/devices`). Zugangsdaten kommen maskiert ("•••").
public struct Device: Codable, Sendable, Hashable, Identifiable {
    public var id: Int
    public var name: String
    public var category: DeviceCategory
    public var driverId: String
    public var config: JSONObject
    public var settings: JSONObject
    public var enabled: Bool
    public var status: String?
    public var lastError: String?
    public var lastSeen: Date?

    enum CodingKeys: String, CodingKey {
        case id, name, category, config, settings, enabled, status
        case driverId = "driver_id"
        case lastError = "last_error"
        case lastSeen = "last_seen"
    }

    public init(id: Int, name: String, category: DeviceCategory, driverId: String, config: JSONObject = [:],
                settings: JSONObject = [:], enabled: Bool = true, status: String? = nil, lastError: String? = nil,
                lastSeen: Date? = nil) {
        self.id = id
        self.name = name
        self.category = category
        self.driverId = driverId
        self.config = config
        self.settings = settings
        self.enabled = enabled
        self.status = status
        self.lastError = lastError
        self.lastSeen = lastSeen
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        category = try c.decode(DeviceCategory.self, forKey: .category)
        driverId = try c.decode(String.self, forKey: .driverId)
        config = try c.decodeIfPresent(JSONObject.self, forKey: .config) ?? [:]
        settings = try c.decodeIfPresent(JSONObject.self, forKey: .settings) ?? [:]
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled) ?? true
        status = try c.decodeIfPresent(String.self, forKey: .status)
        lastError = try c.decodeIfPresent(String.self, forKey: .lastError)
        lastSeen = try c.decodeIfPresent(Date.self, forKey: .lastSeen)
    }
}

public struct DeviceCreate: Encodable, Sendable {
    public var name: String
    public var category: DeviceCategory
    public var driverId: String
    public var config: JSONObject
    public var settings: JSONObject
    public var enabled: Bool

    public init(name: String, category: DeviceCategory, driverId: String, config: JSONObject,
                settings: JSONObject = [:], enabled: Bool = true) {
        self.name = name
        self.category = category
        self.driverId = driverId
        self.config = config
        self.settings = settings
        self.enabled = enabled
    }

    enum CodingKeys: String, CodingKey {
        case name, category, config, settings, enabled
        case driverId = "driver_id"
    }
}

public struct DeviceUpdate: Encodable, Sendable {
    public var name: String?
    public var config: JSONObject?
    public var settings: JSONObject?
    public var enabled: Bool?

    public init(name: String? = nil, config: JSONObject? = nil, settings: JSONObject? = nil, enabled: Bool? = nil) {
        self.name = name
        self.config = config
        self.settings = settings
        self.enabled = enabled
    }

    enum CodingKeys: String, CodingKey { case name, config, settings, enabled }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(name, forKey: .name)
        try c.encodeIfPresent(config, forKey: .config)
        try c.encodeIfPresent(settings, forKey: .settings)
        try c.encodeIfPresent(enabled, forKey: .enabled)
    }
}

/// Manueller Eingriff an einem Gerät (`POST /api/devices/{id}/action`).
public struct DeviceAction: Encodable, Sendable, Hashable {
    public var action: String
    public var value: JSONValue?

    public init(_ action: String, value: JSONValue? = nil) {
        self.action = action
        self.value = value
    }

    public static func setMode(_ mode: String) -> DeviceAction { .init("set_mode", value: .string(mode)) }
    public static func setChargeLimit(_ percent: Int) -> DeviceAction { .init("set_charge_limit", value: .number(Double(percent))) }
    public static let fast = DeviceAction("fast")
    public static let stop = DeviceAction("stop")
    public static let auto = DeviceAction("auto")
    public static let wake = DeviceAction("wake")
    public static let boost = DeviceAction("boost")
    public static let boostOff = DeviceAction("boost_off")

    enum CodingKeys: String, CodingKey { case action, value }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(action, forKey: .action)
        try c.encode(value ?? .null, forKey: .value)
    }
}

/// Betriebsarten, wie sie das Backend akzeptiert (`api/devices.py`).
public enum WallboxMode: String, CaseIterable, Sendable, Identifiable {
    case pvOnly = "pv_only", minPv = "min_pv", pvPrice = "pv_price", target
    public var id: String { rawValue }
}

public enum WaterMode: String, CaseIterable, Sendable, Identifiable {
    case off, pvOnly = "pv_only", pvPrice = "pv_price", schedule
    public var id: String { rawValue }
}

// MARK: - Treiber

public struct DriverMeta: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    public var name: String
    public var category: DeviceCategory
    public var description: String?
    public var fields: [ConfigField]
    public var capabilities: [String]?
    public var maturity: Maturity?
    public var notes: String?
    public var offlineHint: String?
    public var ownProgramHint: String?

    enum CodingKeys: String, CodingKey {
        case id, name, category, description, fields, capabilities, maturity, notes
        case offlineHint = "offline_hint"
        case ownProgramHint = "own_program_hint"
    }

    public enum Maturity: String, Codable, Sendable, Hashable {
        case stable, beta, experimental
        public init(from decoder: any Decoder) throws {
            let raw = try decoder.singleValueContainer().decode(String.self)
            self = Maturity(rawValue: raw) ?? .experimental
        }
    }

    /// Generische Treiber (HTTP, MQTT, Home Assistant, Modbus-Profil) und Simulation
    /// erkennt man am Namen; die Auswahl zeigt Hersteller zuerst.
    public var isGeneric: Bool {
        name.lowercased().contains("generisch") || id.hasPrefix("ha_") || id.hasPrefix("generic_")
    }

    public var isSimulation: Bool { id.hasPrefix("sim_") }

    /// Startkonfiguration mit Standardwerten der Felder.
    public func defaultConfig() -> JSONObject {
        var out: JSONObject = [:]
        for field in fields {
            if let value = field.defaultValue, !value.isNull { out[field.key] = value }
        }
        return out
    }
}

public struct ConfigField: Codable, Sendable, Hashable, Identifiable {
    public var key: String
    public var label: String
    public var type: FieldType
    public var required: Bool
    public var defaultValue: JSONValue?
    public var options: [Option]?
    public var placeholder: String?
    public var help: String?

    public var id: String { key }

    public init(key: String, label: String, type: FieldType, required: Bool = true, defaultValue: JSONValue? = nil,
                options: [Option]? = nil, placeholder: String? = nil, help: String? = nil) {
        self.key = key
        self.label = label
        self.type = type
        self.required = required
        self.defaultValue = defaultValue
        self.options = options
        self.placeholder = placeholder
        self.help = help
    }

    enum CodingKeys: String, CodingKey {
        case key, label, type, required, options, placeholder, help
        case defaultValue = "default"
    }

    public enum FieldType: String, Codable, Sendable, Hashable {
        case text, number, password, boolean, select
        public init(from decoder: any Decoder) throws {
            let raw = try decoder.singleValueContainer().decode(String.self)
            self = FieldType(rawValue: raw) ?? .text
        }
    }

    public struct Option: Codable, Sendable, Hashable, Identifiable {
        public var value: String
        public var label: String
        public var id: String { value }
        public init(value: String, label: String) {
            self.value = value
            self.label = label
        }
    }
}

public struct TestConnectionRequest: Encodable, Sendable {
    public var driverId: String
    public var config: JSONObject
    public var deviceId: Int?
    public var includeWrite: Bool

    public init(driverId: String, config: JSONObject, deviceId: Int? = nil, includeWrite: Bool = false) {
        self.driverId = driverId
        self.config = config
        self.deviceId = deviceId
        self.includeWrite = includeWrite
    }

    enum CodingKeys: String, CodingKey {
        case config
        case driverId = "driver_id"
        case deviceId = "device_id"
        case includeWrite = "include_write"
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(driverId, forKey: .driverId)
        try c.encode(config, forKey: .config)
        try c.encode(deviceId, forKey: .deviceId)
        try c.encode(includeWrite, forKey: .includeWrite)
    }
}

public struct TestResult: Codable, Sendable, Hashable {
    public var ok: Bool
    public var message: String
    public var values: JSONObject?
    public var warnings: [String]?
    public var writeTest: WriteTest?

    enum CodingKeys: String, CodingKey {
        case ok, message, values, warnings
        case writeTest = "write_test"
    }

    public struct WriteTest: Codable, Sendable, Hashable {
        public var ok: Bool
        public var message: String
    }
}

public typealias DeviceLimits = [String: Int]

// MARK: - Fahrzeuge

public struct Vehicle: Codable, Sendable, Hashable, Identifiable {
    public var id: Int
    public var name: String
    public var capacityKwh: Double
    public var wallboxDeviceId: Int?
    public var socSource: String
    public var defaultTargetSoc: Int

    enum CodingKeys: String, CodingKey {
        case id, name
        case capacityKwh = "capacity_kwh"
        case wallboxDeviceId = "wallbox_device_id"
        case socSource = "soc_source"
        case defaultTargetSoc = "default_target_soc"
    }
}

public struct VehicleCreate: Encodable, Sendable {
    public var name: String
    public var capacityKwh: Double
    public var wallboxDeviceId: Int?
    public var socSource: String
    public var defaultTargetSoc: Int

    public init(name: String, capacityKwh: Double = 60, wallboxDeviceId: Int? = nil, socSource: String = "none",
                defaultTargetSoc: Int = 80) {
        self.name = name
        self.capacityKwh = capacityKwh
        self.wallboxDeviceId = wallboxDeviceId
        self.socSource = socSource
        self.defaultTargetSoc = defaultTargetSoc
    }

    enum CodingKeys: String, CodingKey {
        case name
        case capacityKwh = "capacity_kwh"
        case wallboxDeviceId = "wallbox_device_id"
        case socSource = "soc_source"
        case defaultTargetSoc = "default_target_soc"
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(name, forKey: .name)
        try c.encode(capacityKwh, forKey: .capacityKwh)
        try c.encode(wallboxDeviceId, forKey: .wallboxDeviceId)
        try c.encode(socSource, forKey: .socSource)
        try c.encode(defaultTargetSoc, forKey: .defaultTargetSoc)
    }
}
