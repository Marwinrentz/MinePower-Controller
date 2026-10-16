import Foundation

// MARK: - Changelog

public struct ChangelogEntry: Codable, Sendable, Hashable, Identifiable {
    public var version: String
    public var date: String
    public var new: [String]
    public var changed: [String]
    public var fixed: [String]
    public var id: String { version }

    public init(version: String, date: String, new: [String] = [], changed: [String] = [], fixed: [String] = []) {
        self.version = version
        self.date = date
        self.new = new
        self.changed = changed
        self.fixed = fixed
    }

    public var isEmpty: Bool { new.isEmpty && changed.isEmpty && fixed.isEmpty }
}

/// `GET /api/system/changelog`: alle Versionen und die seit dem letzten Besuch neuen.
public struct Changelog: Codable, Sendable, Hashable {
    public var version: String
    public var entries: [ChangelogEntry]
    public var unseen: [ChangelogEntry]
}

// MARK: - Diagnose

public struct Finding: Codable, Sendable, Hashable, Identifiable {
    public enum Level: String, Codable, Sendable, Hashable {
        case error, warn, info, ok
        public init(from decoder: any Decoder) throws {
            let raw = try decoder.singleValueContainer().decode(String.self)
            self = Level(rawValue: raw) ?? .info
        }
        public var rank: Int {
            switch self {
            case .error: 0
            case .warn: 1
            case .info: 2
            case .ok: 3
            }
        }
    }

    public var level: Level
    public var title: String
    public var detail: String
    public var fix: String?
    public var count: Int?
    public var kwh: Double?
    public var id: String { level.rawValue + title }
}

public struct FindingsReport: Codable, Sendable, Hashable {
    public var generatedAt: Date?
    public var range: String?
    public var findings: [Finding]

    enum CodingKeys: String, CodingKey {
        case range, findings
        case generatedAt = "generated_at"
    }
}

public struct AppEvent: Codable, Sendable, Hashable, Identifiable {
    public var id: Int
    public var time: Date
    public var level: String
    public var category: String
    public var message: String
    public var data: JSONObject?

    public var isWarning: Bool { level == "warning" || level == "error" }
}

public struct BackupStatus: Codable, Sendable, Hashable {
    public var count: Int
    public var latest: String?
    public var dir: String?
}

// MARK: - Einstellungen

/// `GET /api/settings`: Abschnitte als freie Objekte (regulation, tariff,
/// notifications, forecast, ui, priority …). Geheimnisse kommen als "•••".
public struct SettingsEnvelope: Codable, Sendable, Hashable {
    public var values: [String: JSONValue]

    public init(values: [String: JSONValue]) { self.values = values }

    public func section(_ name: String) -> JSONObject { values[name]?.object ?? [:] }
}

/// Wert, den das Backend für ein gesetztes Geheimnis zurückgibt. Zurückgeschickt
/// bedeutet er 'unverändert lassen".
public let secretMask = "•••"

public struct Schedule: Codable, Sendable, Hashable, Identifiable {
    public var id: Int
    public var deviceId: Int
    public var daysMask: Int
    public var startTime: String
    public var endTime: String
    public var enabled: Bool

    enum CodingKeys: String, CodingKey {
        case id, enabled
        case deviceId = "device_id"
        case daysMask = "days_mask"
        case startTime = "start_time"
        case endTime = "end_time"
    }
}

public struct ScheduleCreate: Encodable, Sendable {
    public var deviceId: Int
    public var daysMask: Int
    public var startTime: String
    public var endTime: String
    public var enabled: Bool

    public init(deviceId: Int, daysMask: Int, startTime: String, endTime: String, enabled: Bool = true) {
        self.deviceId = deviceId
        self.daysMask = daysMask
        self.startTime = startTime
        self.endTime = endTime
        self.enabled = enabled
    }

    enum CodingKeys: String, CodingKey {
        case enabled
        case deviceId = "device_id"
        case daysMask = "days_mask"
        case startTime = "start_time"
        case endTime = "end_time"
    }
}

/// Wochentage als Bitmaske (Bit 0 = Montag … Bit 6 = Sonntag), wie im Backend.
public struct Weekdays: OptionSet, Sendable, Hashable {
    public let rawValue: Int
    public init(rawValue: Int) { self.rawValue = rawValue & 127 }

    public static let monday = Weekdays(rawValue: 1)
    public static let tuesday = Weekdays(rawValue: 2)
    public static let wednesday = Weekdays(rawValue: 4)
    public static let thursday = Weekdays(rawValue: 8)
    public static let friday = Weekdays(rawValue: 16)
    public static let saturday = Weekdays(rawValue: 32)
    public static let sunday = Weekdays(rawValue: 64)
    public static let workdays: Weekdays = [.monday, .tuesday, .wednesday, .thursday, .friday]
    public static let weekend: Weekdays = [.saturday, .sunday]
    public static let all = Weekdays(rawValue: 127)

    /// Index 0 = Montag.
    public static func day(_ index: Int) -> Weekdays { Weekdays(rawValue: 1 << index) }

    public func label(short names: [String] = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
                      daily: String = "täglich", workdays: String = "Mo–Fr", weekend: String = "Sa, So") -> String {
        if self == .all { return daily }
        if self == .workdays { return workdays }
        if self == .weekend { return weekend }
        return (0..<7).filter { contains(.day($0)) }.map { names[$0] }.joined(separator: ", ")
    }
}

/// Zeit 'HH:MM" prüfen.
public enum ClockTime {
    public static func isValid(_ text: String) -> Bool {
        let parts = text.split(separator: ":")
        guard parts.count == 2, parts[0].count == 2, parts[1].count == 2,
              let h = Int(parts[0]), let m = Int(parts[1]) else { return false }
        return (0..<24).contains(h) && (0..<60).contains(m)
    }

    public static func string(hour: Int, minute: Int) -> String {
        String(format: "%02d:%02d", hour, minute)
    }
}

public struct PriorityUpdate: Encodable, Sendable {
    public var order: [Int]
    public init(order: [Int]) { self.order = order }
}

public struct MessageResponse: Decodable, Sendable {
    public var ok: Bool?
    public var message: String?
}
