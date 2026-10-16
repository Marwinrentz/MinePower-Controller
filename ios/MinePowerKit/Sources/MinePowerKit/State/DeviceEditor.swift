import Foundation
import Observation

/// Gerät anlegen oder bearbeiten: Treiber wählen, Felder, Verbindungstest, Speichern.
///
/// Wird im Einrichtungsassistenten und unter Einstellungen → Geräte genutzt.
/// Der Verbindungstest ist die einzige Stelle, an der sich vor dem Betrieb
/// prüfen lässt, ob Treiber und Firmware zusammenpassen; er liest Live-Werte
/// und auf Wunsch einen unkritischen Steuerbefehl samt Rückmeldung.
@MainActor
@Observable
public final class DeviceEditor {
    public var draft: DeviceDraft
    public let drivers: Resource<[DriverMeta]>
    public private(set) var testResult: TestResult?
    public private(set) var isTesting = false
    public private(set) var isSaving = false
    public private(set) var error: APIError?
    /// Feldschlüssel → Meldung (eigene Prüfung oder 422 vom Server).
    public private(set) var fieldErrors: [String: String] = [:]

    @ObservationIgnored private let api: APIClient

    public init(api: APIClient, category: DeviceCategory, existing: Device? = nil) {
        self.api = api
        drivers = Resource { try await api.drivers(category: category) }
        draft = DeviceDraft(category: category)
        if let existing { draft = DeviceDraft(editing: existing, driver: nil) }
        self.existing = existing
    }

    @ObservationIgnored private let existing: Device?

    public var driver: DriverMeta? {
        guard let id = draft.driverId else { return nil }
        if let known = drivers.value?.first(where: { $0.id == id }) { return known }
        // Treiber nach Server-Update unbekannt: mit den gespeicherten Feldern weiter bearbeitbar.
        guard drivers.value != nil, let existing else { return nil }
        return Self.fallbackDriver(for: existing)
    }

    nonisolated static func fallbackDriver(for device: Device) -> DriverMeta {
        let fields = device.config.keys.sorted().map { key -> ConfigField in
            let value = device.config[key]
            let type: ConfigField.FieldType = switch value {
            case .bool: .boolean
            case .number: .number
            case .string(let text) where text == secretMask: .password
            default: .text
            }
            return ConfigField(key: key, label: key, type: type, required: false)
        }
        return DriverMeta(id: device.driverId, name: device.driverId, category: device.category, fields: fields,
                          notes: Loc.t("Dieser Treiber ist dem Server nicht mehr bekannt. Name und Werte lassen sich weiter ändern.",
                                       "The server no longer knows this driver. Name and values can still be changed."))
    }

    /// Steuerbare Lasten bieten den Test mit Schreibbefehl an.
    public var canTestWrite: Bool { draft.category == .wallbox || draft.category == .waterHeater }

    public func load() async {
        await drivers.load(maxAge: 300)
        // Beim Bearbeiten die Felder erst mit bekanntem Treiber füllen (Typen!).
        if let existing, draft.text.isEmpty, draft.flags.isEmpty, let driver {
            let name = draft.name
            draft = DeviceDraft(editing: existing, driver: driver)
            draft.name = name
        }
    }

    public func select(_ driver: DriverMeta) {
        draft.select(driver, previous: self.driver)
        testResult = nil
        fieldErrors = [:]
        error = nil
    }

    public func test(includeWrite: Bool = false) async {
        guard let driver else { return }
        isTesting = true
        testResult = nil
        defer { isTesting = false }
        do {
            testResult = try await api.testConnection(draft.testRequest(driver: driver, includeWrite: includeWrite))
        } catch let apiError as APIError {
            apply(apiError)
            testResult = TestResult(ok: false, message: apiError.errorDescription ?? Loc.t("Test fehlgeschlagen", "Test failed"))
        } catch {
            testResult = TestResult(ok: false, message: error.localizedDescription)
        }
    }

    /// Prüft, speichert und liefert das Gerät; `nil` bei Fehler (Meldung in `error`/`fieldErrors`).
    public func save() async -> Device? {
        guard let driver else { return nil }
        let issues = draft.issues(for: driver)
        fieldErrors = Dictionary(issues.map { ($0.key, $0.message) }) { a, _ in a }
        error = nil
        guard issues.isEmpty else { return nil }
        isSaving = true
        defer { isSaving = false }
        do {
            if let id = draft.existingID {
                return try await api.updateDevice(id, draft.updateRequest(driver: driver))
            }
            return try await api.createDevice(draft.createRequest(driver: driver))
        } catch let apiError as APIError {
            apply(apiError)
            return nil
        } catch {
            self.error = .unreachable(error.localizedDescription)
            return nil
        }
    }

    private func apply(_ apiError: APIError) {
        if case .validation(_, let fields) = apiError, !fields.isEmpty {
            for field in fields { fieldErrors[field.key] = field.error }
        }
        error = apiError
    }
}

/// Einrichtungsassistent: Schritte, Geräte je Schritt, Reihenfolge, Abschluss.
@MainActor
@Observable
public final class SetupWizard {
    public private(set) var flow = SetupFlow()
    public let devices: Resource<[Device]>
    public let limits: Resource<DeviceLimits>
    /// Formular des aktuellen Geräteschritts (nil = gerade gespeichert oder voll).
    public private(set) var editor: DeviceEditor?
    public private(set) var savedInStep = false
    /// Reihenfolge der Lasten (Geräte-IDs), oben zuerst versorgt.
    public var priority: [Int] = []
    public private(set) var hasGridMeasurement: Bool?
    public private(set) var isWorking = false
    /// Schrittwechsel läuft (Doppeltipp darf keinen Schritt überspringen).
    public private(set) var isTransitioning = false
    public private(set) var error: APIError?

    @ObservationIgnored private let api: APIClient

    public init(api: APIClient, start: SetupFlow.Step = .welcome) {
        self.api = api
        flow = SetupFlow(start: start)
        devices = Resource { try await api.devices() }
        limits = Resource { try await api.deviceLimits() }
    }

    public var step: SetupFlow.Step { flow.step }

    public var category: DeviceCategory? {
        if case .device(let category) = flow.step { return category }
        return nil
    }

    public var devicesInStep: [Device] {
        guard let category else { return [] }
        return (devices.value ?? []).filter { $0.category == category }
    }

    public var isCategoryFull: Bool {
        guard let category, let devices = devices.value else { return false }
        return !SetupFlow.canAdd(category, devices: devices, limits: limits.value ?? [:])
    }

    /// Steuerbare Lasten für die Reihenfolge.
    public var loads: [Device] {
        (devices.value ?? []).filter { $0.enabled && ($0.category == .wallbox || $0.category == .waterHeater) }
    }

    /// "Weiter" oder "Überspringen"?
    public var nextIsSkip: Bool {
        category != nil && !savedInStep && devicesInStep.isEmpty
    }

    public func start() async {
        async let d: Void = devices.reload()
        async let l: Void = limits.load()
        _ = await (d, l)
        prepareStep()
    }

    /// Beschäftigt: Schrittwechsel, Speichern oder Server-Demo laufen.
    public var isBusy: Bool { isTransitioning || isWorking || editor?.isSaving == true }

    public func next() async {
        guard !isBusy else { return }
        isTransitioning = true
        defer { isTransitioning = false }
        error = nil
        if flow.step == .priority {
            guard await savePriority() else { return }
        }
        flow.next()
        await enterStep()
    }

    public func back() async {
        guard !isBusy else { return }
        isTransitioning = true
        defer { isTransitioning = false }
        error = nil
        flow.back()
        await enterStep()
    }

    /// Nach dem Speichern ein weiteres Gerät derselben Kategorie.
    public func addAnother() {
        savedInStep = false
        prepareStep()
    }

    /// Formular speichern; danach "Gespeichert" anzeigen.
    @discardableResult
    public func saveDevice() async -> Bool {
        guard let editor, !editor.isSaving, !isTransitioning, await editor.save() != nil else { return false }
        savedInStep = true
        self.editor = nil
        await devices.reload()
        syncPriority()
        return true
    }

    /// Serverseitige Simulation (Demo-Modus des Servers), danach direkt "Fertig".
    public func startServerDemo() async {
        isWorking = true
        defer { isWorking = false }
        do {
            try await api.enableDemo()
            await devices.reload()
            flow.jump(to: .done)
            await enterStep()
        } catch let apiError as APIError {
            error = apiError
        } catch {}
    }

    // MARK: - Intern

    private func enterStep() async {
        savedInStep = false
        if flow.step == .priority || flow.step == .done { await devices.reload() }
        if flow.step == .priority { syncPriority() }
        if flow.step == .done {
            hasGridMeasurement = (try? await api.setupState())?.hasGridMeasurement
        }
        prepareStep()
    }

    private func prepareStep() {
        guard let category, !isCategoryFull, !savedInStep else {
            editor = nil
            return
        }
        if editor?.draft.category != category { editor = DeviceEditor(api: api, category: category) }
    }

    private func syncPriority() {
        let ids = loads.map(\.id)
        priority = priority.filter(ids.contains) + ids.filter { !priority.contains($0) }
    }

    private func savePriority() async -> Bool {
        guard loads.count >= 2 else { return true }
        do {
            try await api.setPriority(priority)
            return true
        } catch let apiError as APIError {
            error = apiError
            return false
        } catch {
            return false
        }
    }
}

public extension TestResult {
    init(ok: Bool, message: String, values: JSONObject? = nil, warnings: [String]? = nil) {
        self.init(ok: ok, message: message, values: values, warnings: warnings, writeTest: nil)
    }
}
