import Foundation

/// Bearbeitungsstand eines Einstellungsblocks (wie `useDraft` im Web).
///
/// Hält den gespeicherten Stand und die Änderungen getrennt: "Verwerfen"
/// stellt den Stand wieder her, "Speichern" ist nur aktiv, wenn sich etwas
/// geändert hat. Leere Felder fallen auf den Standardwert der Oberfläche zurück.
public struct SettingsDraft: Sendable, Equatable {
    public private(set) var saved: JSONObject
    public private(set) var draft: JSONObject

    public init(_ saved: JSONObject = [:]) {
        self.saved = saved
        draft = saved
    }

    public var isDirty: Bool { draft != saved }

    public func number(_ key: String, _ fallback: Double) -> Double { draft.double(key) ?? fallback }
    public func string(_ key: String, _ fallback: String) -> String { draft.string(key) ?? fallback }
    public func bool(_ key: String, _ fallback: Bool) -> Bool { draft.bool(key) ?? fallback }

    public mutating func set(_ key: String, _ value: JSONValue) { draft[key] = value }
    public mutating func set(_ key: String, _ value: Double) { draft[key] = .number(value) }
    public mutating func set(_ key: String, _ value: String) { draft[key] = .string(value) }
    public mutating func set(_ key: String, _ value: Bool) { draft[key] = .bool(value) }

    public mutating func reset() { draft = saved }

    /// Einen Schlüssel auf den gespeicherten Stand zurücksetzen (auch "nicht gesetzt").
    public mutating func restore(_ key: String) { draft[key] = saved[key] }

    /// Nach erfolgreichem Speichern bzw. neuen Serverdaten: Stand übernehmen,
    /// eigene ungespeicherte Änderungen bleiben erhalten.
    public mutating func rebase(_ newSaved: JSONObject) {
        let changed = draft.filter { saved[$0.key] != $0.value }
        saved = newSaved
        draft = newSaved.merging(changed) { $1 }
    }

    /// Nur die geänderten Schlüssel (für sparsame Updates).
    public var changes: JSONObject { draft.filter { saved[$0.key] != $0.value } }
}
