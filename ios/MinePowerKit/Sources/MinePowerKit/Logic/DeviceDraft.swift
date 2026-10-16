import Foundation

/// Formularzustand eines Geräts (neu oder bearbeitet), unabhängig von der Oberfläche.
///
/// Eingaben bleiben Text, bis gespeichert oder getestet wird: Wer "0," tippt,
/// soll nicht sofort "0" zurückbekommen. Erst `config(for:)` wandelt nach
/// Feldtyp um. Gespeicherte Passwörter kommen maskiert ("•••") vom Server;
/// bleibt das Feld beim Bearbeiten leer, wird die Maske gesendet und der
/// Server behält den alten Wert.
public struct DeviceDraft: Sendable, Equatable {
    public var category: DeviceCategory
    public private(set) var driverId: String?
    public var name: String
    /// Text-, Zahl-, Passwort- und Auswahlfelder als Eingabetext.
    public var text: [String: String] = [:]
    /// Ja/Nein-Felder.
    public var flags: [String: Bool] = [:]
    /// Regelungseinstellungen (`settings`), z. B. `grid_metered` der Wallbox.
    public var settings: JSONObject
    public private(set) var existingID: Int?

    public init(category: DeviceCategory) {
        self.category = category
        name = ""
        settings = [:]
    }

    public init(editing device: Device, driver: DriverMeta?) {
        category = device.category
        driverId = device.driverId
        name = device.name
        settings = device.settings
        existingID = device.id
        for field in driver?.fields ?? [] {
            let value = device.config[field.key]
            switch field.type {
            case .boolean: flags[field.key] = value?.bool ?? false
            case .password: text[field.key] = ""
            default: text[field.key] = DriverForm.text(value)
            }
        }
    }

    public var isEditing: Bool { existingID != nil }

    /// Treiber wählen: Standardwerte übernehmen, Namen vorschlagen.
    public mutating func select(_ driver: DriverMeta, previous: DriverMeta? = nil) {
        guard !isEditing else { return }
        driverId = driver.id
        text = [:]
        flags = [:]
        for field in driver.fields {
            let value = field.defaultValue
            if field.type == .boolean {
                flags[field.key] = value?.bool ?? false
            } else {
                text[field.key] = DriverForm.text(value)
            }
        }
        if name.isEmpty || name == previous?.name { name = driver.name }
    }

    /// Konfiguration für Test und Speichern.
    public func config(for driver: DriverMeta) -> JSONObject {
        var out: JSONObject = [:]
        for field in driver.fields {
            switch field.type {
            case .boolean:
                out[field.key] = .bool(flags[field.key] ?? false)
            case .password where isEditing && (text[field.key] ?? "").isEmpty:
                out[field.key] = .string(secretMask)
            default:
                if let value = DriverForm.value(text[field.key] ?? "", for: field) { out[field.key] = value }
            }
        }
        return out
    }

    /// Vor dem Senden prüfen (Pflichtfelder, Zahlen, Auswahl, Name).
    public func issues(for driver: DriverMeta) -> [DriverForm.Issue] {
        var list = DriverForm.validate(config(for: driver).filter { $0.value.string != secretMask },
                                       fields: driver.fields, editing: isEditing)
        // Unlesbare Zahl nicht stillschweigend verwerfen (fiele sonst aus der Konfiguration).
        for field in driver.fields where field.type == .number {
            let raw = (text[field.key] ?? "").trimmingCharacters(in: .whitespaces)
            if !raw.isEmpty, DriverForm.value(raw, for: field) == nil, !list.contains(where: { $0.key == field.key }) {
                list.append(.init(key: field.key, message: Loc.t("Zahl erwartet", "Number expected")))
            }
        }
        if name.trimmingCharacters(in: .whitespaces).isEmpty {
            list.insert(.init(key: "name", message: Loc.t("Bitte einen Namen eingeben", "Please enter a name")), at: 0)
        }
        return list
    }

    public func createRequest(driver: DriverMeta) -> DeviceCreate {
        var merged = Self.defaultSettings(category)
        merged.merge(settings) { $1 }
        return DeviceCreate(name: name.trimmingCharacters(in: .whitespaces), category: category, driverId: driver.id,
                            config: config(for: driver), settings: merged, enabled: true)
    }

    public func updateRequest(driver: DriverMeta) -> DeviceUpdate {
        DeviceUpdate(name: name.trimmingCharacters(in: .whitespaces), config: config(for: driver), settings: settings)
    }

    public func testRequest(driver: DriverMeta, includeWrite: Bool) -> TestConnectionRequest {
        TestConnectionRequest(driverId: driver.id, config: config(for: driver), deviceId: existingID,
                              includeWrite: includeWrite)
    }

    /// Startwerte der Regelung je Kategorie (wie `DeviceForm.tsx` im Web).
    public static func defaultSettings(_ category: DeviceCategory) -> JSONObject {
        switch category {
        case .wallbox:
            ["mode": "pv_only", "min_current": 6, "max_current": 16, "phases_mode": "fixed1",
             "start_threshold_w": 1400, "start_delay_s": 60, "stop_delay_s": 180, "grid_metered": true]
        case .waterHeater:
            ["mode": "pv_only", "max_power_w": 3000, "target_temp_c": 60, "modulating": true,
             "price_limit_ct": 15, "boost_end_mode": "both", "boost_duration_min": 60, "boost_temp_c": 65]
        case .battery:
            // Ohne aktive Steuerung: Hybrid-Wechselrichter regeln ihre Batterie selbst.
            ["reserve_soc": 20, "manage_reserve": false]
        default:
            [:]
        }
    }
}

public extension DriverMeta {
    /// Gruppen für die Auswahl: Hersteller zuerst, dann universelle Treiber, dann Simulation.
    enum Group: Int, Sendable, CaseIterable, Comparable {
        case vendor, generic, simulation
        public static func < (a: Self, b: Self) -> Bool { a.rawValue < b.rawValue }
    }

    var group: Group { isSimulation ? .simulation : isGeneric ? .generic : .vendor }

    /// Treiber-Suche über Name, Beschreibung und Kennung.
    func matches(_ query: String) -> Bool {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return true }
        return name.lowercased().contains(q) || id.lowercased().contains(q)
            || (description?.lowercased().contains(q) ?? false)
    }
}
