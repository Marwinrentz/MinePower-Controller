import Foundation

public struct User: Codable, Sendable, Hashable, Identifiable {
    public var id: Int
    public var email: String
    public var name: String
    public var role: Role
    public var rfidTag: String?
    public var language: String?
    public var disabled: Bool?
    public var lastSeenVersion: String?

    public init(id: Int, email: String, name: String, role: Role) {
        self.id = id
        self.email = email
        self.name = name
        self.role = role
    }

    enum CodingKeys: String, CodingKey {
        case id, email, name, role, language, disabled
        case rfidTag = "rfid_tag"
        case lastSeenVersion = "last_seen_version"
    }

    public enum Role: String, Codable, Sendable, Hashable, CaseIterable, Comparable {
        case readonly, user, admin

        public init(from decoder: any Decoder) throws {
            let raw = try decoder.singleValueContainer().decode(String.self)
            self = Role(rawValue: raw) ?? .readonly
        }

        var rank: Int {
            switch self {
            case .readonly: 0
            case .user: 1
            case .admin: 2
            }
        }

        public static func < (lhs: Role, rhs: Role) -> Bool { lhs.rank < rhs.rank }

        /// Darf schalten (Betriebsart, Boost, Batterie, Pause).
        public var canControl: Bool { self >= .user }
        /// Darf einrichten (Geräte, Einstellungen, Benutzer, Sicherung).
        public var canConfigure: Bool { self == .admin }
    }
}

public struct LoginRequest: Encodable, Sendable {
    public var email: String
    public var password: String
    public init(email: String, password: String) {
        self.email = email
        self.password = password
    }
}

/// Erster Administrator auf einem leeren Server (`POST /api/auth/setup`).
public struct SetupRequest: Encodable, Sendable {
    public var email: String
    public var password: String
    public var name: String
    public var language: String
    public init(email: String, password: String, name: String, language: String = "de") {
        self.email = email
        self.password = password
        self.name = name
        self.language = language
    }
}

public struct TokenResponse: Codable, Sendable {
    public var token: String
    public var user: User
}

public struct UserCreate: Encodable, Sendable {
    public var email: String
    public var name: String
    public var password: String
    public var role: User.Role
    public init(email: String, name: String, password: String, role: User.Role) {
        self.email = email
        self.name = name
        self.password = password
        self.role = role
    }
}

public struct UserUpdate: Encodable, Sendable {
    public var name: String?
    public var role: User.Role?
    public var password: String?
    public var disabled: Bool?
    public var rfidTag: String?
    public init(name: String? = nil, role: User.Role? = nil, password: String? = nil, disabled: Bool? = nil,
                rfidTag: String? = nil) {
        self.name = name
        self.role = role
        self.password = password
        self.disabled = disabled
        self.rfidTag = rfidTag
    }

    enum CodingKeys: String, CodingKey {
        case name, role, password, disabled
        case rfidTag = "rfid_tag"
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(name, forKey: .name)
        try c.encodeIfPresent(role, forKey: .role)
        try c.encodeIfPresent(password, forKey: .password)
        try c.encodeIfPresent(disabled, forKey: .disabled)
        try c.encodeIfPresent(rfidTag, forKey: .rfidTag)
    }
}

/// `GET /api/system/setup-state` (ohne Anmeldung): Erst-Setup oder Login?
public struct SetupState: Codable, Sendable, Hashable {
    public var needsAdmin: Bool
    public var needsDevices: Bool
    public var hasGridMeasurement: Bool?

    public init(needsAdmin: Bool, needsDevices: Bool, hasGridMeasurement: Bool? = nil) {
        self.needsAdmin = needsAdmin
        self.needsDevices = needsDevices
        self.hasGridMeasurement = hasGridMeasurement
    }

    enum CodingKeys: String, CodingKey {
        case needsAdmin = "needs_admin"
        case needsDevices = "needs_devices"
        case hasGridMeasurement = "has_grid_measurement"
    }
}

/// `GET /api/system/health` (ohne Anmeldung).
public struct Health: Codable, Sendable, Hashable {
    public var status: String
    public var controlLoop: String?
    public var version: String?
    public var time: Date?

    public init(status: String, controlLoop: String? = nil, version: String? = nil, time: Date? = nil) {
        self.status = status
        self.controlLoop = controlLoop
        self.version = version
        self.time = time
    }

    enum CodingKeys: String, CodingKey {
        case status, version, time
        case controlLoop = "control_loop"
    }
}

/// MCP-Zugangsschlüssel (Einstellungen → KI-Assistent).
public struct McpToken: Codable, Sendable, Hashable, Identifiable {
    public var id: Int
    public var name: String
    public var prefix: String
    public var role: User.Role
    public var createdAt: Date?
    public var lastUsedAt: Date?
    /// Nur direkt nach dem Anlegen gesetzt.
    public var token: String?

    enum CodingKeys: String, CodingKey {
        case id, name, prefix, role, token
        case createdAt = "created_at"
        case lastUsedAt = "last_used_at"
    }
}

public struct McpTool: Codable, Sendable, Hashable, Identifiable {
    public var name: String
    public var title: String
    public var role: User.Role
    public var description: String
    public var id: String { name }
}
