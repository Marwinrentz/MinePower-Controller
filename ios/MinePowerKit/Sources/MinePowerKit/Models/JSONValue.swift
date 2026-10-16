import Foundation

/// Ein JSON-Wert unbekannter Form.
///
/// Das Backend liefert `data`, `decision`, Geräte-`config` und -`settings`
/// sowie Einstellungsabschnitte als freie Objekte: Welche Schlüssel darin
/// stehen, hängt vom Treiber bzw. Abschnitt ab. Ein eigener Typ statt
/// `[String: Any]`, weil die App beides braucht: `Codable` (Netz, Cache für
/// Widgets) und `Sendable` (Zugriff über Aktorgrenzen).
public enum JSONValue: Codable, Hashable, Sendable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    public init(from decoder: any Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            // Bool vor Double: sonst wird `true` zu 1.
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unbekannter JSON-Wert")
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .number(let value):
            // Ganze Zahlen ohne ".0" zurückschreiben: Das Backend prüft manche
            // Felder (Port, Unit-ID) als Ganzzahl.
            if value.rounded() == value, abs(value) < 1e15 {
                try container.encode(Int64(value))
            } else {
                try container.encode(value)
            }
        case .bool(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }

    // MARK: - Zugriff

    public var double: Double? {
        switch self {
        case .number(let value): return value
        // Manche Treiber liefern Zahlen als Zeichenkette.
        case .string(let value): return Double(value.replacingOccurrences(of: ",", with: "."))
        case .bool(let value): return value ? 1 : 0
        default: return nil
        }
    }

    public var int: Int? { double.map { Int($0.rounded()) } }

    public var string: String? {
        switch self {
        case .string(let value): return value
        case .number(let value):
            return value.rounded() == value ? String(Int64(value)) : String(value)
        case .bool(let value): return value ? "true" : "false"
        default: return nil
        }
    }

    public var bool: Bool? {
        switch self {
        case .bool(let value): return value
        case .number(let value): return value != 0
        case .string(let value): return ["true", "1", "on", "ja"].contains(value.lowercased())
        default: return nil
        }
    }

    public var object: [String: JSONValue]? {
        if case .object(let value) = self { return value }
        return nil
    }

    public var array: [JSONValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    public var isNull: Bool {
        if case .null = self { return true }
        return false
    }
}

extension JSONValue: ExpressibleByStringLiteral, ExpressibleByFloatLiteral, ExpressibleByIntegerLiteral,
    ExpressibleByBooleanLiteral, ExpressibleByNilLiteral
{
    public init(stringLiteral value: String) { self = .string(value) }
    public init(floatLiteral value: Double) { self = .number(value) }
    public init(integerLiteral value: Int) { self = .number(Double(value)) }
    public init(booleanLiteral value: Bool) { self = .bool(value) }
    public init(nilLiteral: ()) { self = .null }
}

public typealias JSONObject = [String: JSONValue]

public extension Dictionary where Key == String, Value == JSONValue {
    func double(_ key: String) -> Double? { self[key]?.double }
    func int(_ key: String) -> Int? { self[key]?.int }
    func string(_ key: String) -> String? {
        guard let value = self[key], !value.isNull else { return nil }
        return value.string
    }
    func bool(_ key: String) -> Bool? { self[key]?.bool }
    func object(_ key: String) -> JSONObject? { self[key]?.object }
}
