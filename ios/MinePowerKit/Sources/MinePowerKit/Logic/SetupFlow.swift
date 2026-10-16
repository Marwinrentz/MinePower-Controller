import Foundation

/// Ablauf des Einrichtungsassistenten (Spiegel von `frontend/src/pages/Wizard.tsx`).
///
/// Server verbinden → (leerer Server: Administrator anlegen) → Willkommen mit
/// Demo-Option → Wechselrichter → Netzzähler → Wallbox → Batterie →
/// Warmwasser → Reihenfolge → Fertig. Jeder Geräteschritt ist überspringbar;
/// ohne Netzmessung warnt der letzte Schritt, weil die Regelung dann pausiert.
public struct SetupFlow: Sendable, Equatable {
    public enum Step: Hashable, Sendable {
        case welcome
        case device(DeviceCategory)
        case priority
        case done

        public var isOptional: Bool {
            switch self {
            case .device(let category): category != .inverter && category != .meter
            default: false
            }
        }
    }

    public static let steps: [Step] = [.welcome] + DeviceCategory.setupOrder.map(Step.device) + [.priority, .done]

    public private(set) var index = 0

    public init(start: Step = .welcome) {
        index = Self.steps.firstIndex(of: start) ?? 0
    }

    public var step: Step { Self.steps[index] }
    public var progress: Double { Double(index) / Double(Self.steps.count - 1) }
    public var canGoBack: Bool { index > 0 && step != .done }
    public var isFinished: Bool { step == .done }

    public mutating func next() { index = min(index + 1, Self.steps.count - 1) }
    public mutating func back() { index = max(index - 1, 0) }
    public mutating func jump(to step: Step) { index = Self.steps.firstIndex(of: step) ?? index }

    /// Darf in dieser Kategorie noch ein Gerät angelegt werden? (Netzzähler, Batterie: höchstens 1 aktiv)
    public static func canAdd(_ category: DeviceCategory, devices: [Device], limits: DeviceLimits) -> Bool {
        guard let max = limits[category.rawValue] else { return true }
        return devices.filter { $0.category == category && $0.enabled }.count < max
    }

    /// Reihenfolge-Schritt ist nur sinnvoll ab zwei steuerbaren Lasten.
    public static func needsPriority(devices: [Device]) -> Bool {
        devices.filter { $0.enabled && ($0.category == .wallbox || $0.category == .waterHeater) }.count >= 2
    }

    /// Netzmessung vorhanden: Netzzähler oder Wechselrichter mit Zähler.
    public static func hasGridMeasurement(devices: [Device], drivers: [DriverMeta]) -> Bool {
        let gridDrivers = Set(drivers.filter { $0.capabilities?.contains("grid_meter") == true }.map(\.id))
        return devices.contains { $0.enabled && ($0.category == .meter || gridDrivers.contains($0.driverId)) }
    }
}

/// Werte eines Treiberformulars prüfen und in die Konfiguration umwandeln.
public enum DriverForm {
    public struct Issue: Hashable, Sendable {
        public var key: String
        public var message: String
    }

    /// Text aus dem Formular → JSON-Wert passend zum Feldtyp.
    public static func value(_ text: String, for field: ConfigField) -> JSONValue? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        switch field.type {
        case .number:
            if trimmed.isEmpty { return nil }
            return Double(trimmed.replacingOccurrences(of: ",", with: ".")).map(JSONValue.number)
        case .boolean:
            return .bool(["true", "1", "ja", "on"].contains(trimmed.lowercased()))
        default:
            return trimmed.isEmpty ? nil : .string(trimmed)
        }
    }

    /// Formularinhalt als Text (für Eingabefelder).
    public static func text(_ value: JSONValue?) -> String {
        guard let value, !value.isNull else { return "" }
        return value.string ?? ""
    }

    /// Vor dem Senden: Pflichtfelder, Zahlen, Auswahl. Das Backend prüft
    /// zusätzlich und genauer (Port-Bereich, Hostname …) und meldet Feld und Ursache.
    public static func validate(_ config: JSONObject, fields: [ConfigField], editing: Bool = false) -> [Issue] {
        var issues: [Issue] = []
        for field in fields {
            let value = config[field.key]
            let empty = value == nil || value?.isNull == true || value?.string?.isEmpty == true
            if field.required, empty, !(editing && field.type == .password) {
                issues.append(Issue(key: field.key, message: Loc.t("Pflichtfeld", "Required")))
                continue
            }
            if field.type == .number, let value, !empty, value.double == nil {
                issues.append(Issue(key: field.key, message: Loc.t("Zahl erwartet", "Number expected")))
            }
            if field.type == .select, let options = field.options, let selected = value?.string, !empty,
               !options.contains(where: { $0.value == selected }) {
                issues.append(Issue(key: field.key, message: Loc.t("Ungültige Auswahl", "Invalid choice")))
            }
        }
        return issues
    }
}
