import Foundation
import Testing
@testable import MinePowerKit

@Suite("Geräteformular")
struct DeviceDraftTests {
    let fields = [
        ConfigField(key: "host", label: "IP", type: .text),
        ConfigField(key: "port", label: "Port", type: .number, required: false, defaultValue: 502),
        ConfigField(key: "password", label: "Passwort", type: .password),
        ConfigField(key: "three_phase", label: "3-phasig", type: .boolean, required: false, defaultValue: true),
    ]

    var driver: DriverMeta {
        DriverMeta(id: "x_wallbox", name: "X Wallbox", category: .wallbox, fields: fields)
    }

    @Test func selectingDriverFillsDefaultsAndName() {
        var draft = DeviceDraft(category: .wallbox)
        draft.select(driver)
        #expect(draft.name == "X Wallbox")
        #expect(draft.text["port"] == "502")
        #expect(draft.flags["three_phase"] == true)
        // Eigener Name bleibt beim Treiberwechsel erhalten.
        draft.name = "Carport"
        draft.select(DriverMeta(id: "y", name: "Y", category: .wallbox, fields: []), previous: driver)
        #expect(draft.name == "Carport")
    }

    @Test func typingKeepsTextUntilConversion() {
        var draft = DeviceDraft(category: .wallbox)
        draft.select(driver)
        draft.text["port"] = "50,"
        #expect(draft.text["port"] == "50,")
        draft.text["port"] = "502"
        draft.text["host"] = "192.0.2.7"
        draft.text["password"] = "geheim"
        let config = draft.config(for: driver)
        #expect(config["port"] == .number(502))
        #expect(config["three_phase"] == .bool(true))
        #expect(draft.issues(for: driver).isEmpty)
        let create = draft.createRequest(driver: driver)
        #expect(create.settings["grid_metered"] == .bool(true))
        #expect(create.settings["mode"] == .string("pv_only"))
    }

    @Test func editingKeepsMaskedPassword() {
        let device = Device(id: 7, name: "Garage", category: .wallbox, driverId: "x_wallbox",
                            config: ["host": "192.0.2.7", "port": 502, "password": .string(secretMask), "three_phase": false])
        let draft = DeviceDraft(editing: device, driver: driver)
        #expect(draft.text["password"] == "")
        #expect(draft.flags["three_phase"] == false)
        #expect(draft.issues(for: driver).isEmpty)
        #expect(draft.config(for: driver)["password"] == .string(secretMask))
        #expect(draft.testRequest(driver: driver, includeWrite: true).deviceId == 7)
    }

    @Test func unreadableOptionalNumberIsReported() {
        var draft = DeviceDraft(category: .wallbox)
        draft.select(driver)
        draft.text["host"] = "192.0.2.7"
        draft.text["password"] = "x"
        draft.text["port"] = "50x"
        #expect(draft.issues(for: driver).map(\.key) == ["port"])
    }

    @Test func unknownDriverStaysEditable() {
        let device = Device(id: 3, name: "Alt", category: .meter, driverId: "entfernt",
                            config: ["host": "192.0.2.9", "port": 502, "token": .string(secretMask), "invert": true])
        let fallback = DeviceEditor.fallbackDriver(for: device)
        #expect(fallback.fields.map(\.type) == [.text, .boolean, .number, .password])
        var draft = DeviceDraft(editing: device, driver: fallback)
        draft.name = "Neu"
        #expect(draft.issues(for: fallback).isEmpty)
        #expect(draft.updateRequest(driver: fallback).config?["token"] == .string(secretMask))
    }

    @Test func missingRequiredFieldsAndName() {
        var draft = DeviceDraft(category: .wallbox)
        draft.select(driver)
        draft.name = " "
        let keys = draft.issues(for: driver).map(\.key)
        #expect(keys == ["name", "host", "password"])
    }

    @Test func driverGroupsAndSearch() throws {
        let drivers = try Fixture.decode([DriverMeta].self, "drivers.json")
        let wallboxes = drivers.filter { $0.category == .wallbox }
        #expect(wallboxes.contains { $0.group == .vendor })
        #expect(wallboxes.contains { $0.group == .simulation })
        #expect(wallboxes.filter { $0.matches("sim") }.allSatisfy { $0.id.contains("sim") || $0.name.lowercased().contains("sim") || ($0.description?.lowercased().contains("sim") ?? false) })
    }
}

extension DemoBacked {
    @Suite("Einrichtungsassistent mit Demo-Server")
    @MainActor
    struct SetupWizardTests {
        let api = APIClient(session: .demo(), baseURL: DemoBackend.baseURL, token: DemoBackend.token)

        init() { DemoBackend.shared.reset(scenario: .fresh) }

        @Test func freshServerNeedsDevices() async throws {
            let setup = try await api.setupState()
            #expect(setup.needsDevices)
            #expect(setup.hasGridMeasurement == false)
        }

        @Test func walkThroughAddingInverterAndSkippingRest() async throws {
            let wizard = SetupWizard(api: api)
            await wizard.start()
            #expect(wizard.step == .welcome)
            await wizard.next()
            #expect(wizard.step == .device(.inverter))
            let editor = try #require(wizard.editor)
            await editor.load()
            let driver = try #require(editor.drivers.value?.first { $0.isSimulation })
            editor.select(driver)
            await editor.test()
            #expect(editor.testResult?.ok == true)
            #expect(await wizard.saveDevice())
            #expect(wizard.savedInStep)
            #expect(wizard.devicesInStep.count == 1)
            #expect(!wizard.nextIsSkip)
            // Netzzähler: noch leer → "Überspringen"
            await wizard.next()
            #expect(wizard.step == .device(.meter))
            #expect(wizard.nextIsSkip)
            for _ in 0..<4 { await wizard.next() }
            #expect(wizard.step == .priority)
            await wizard.next()
            #expect(wizard.step == .done)
            #expect(wizard.hasGridMeasurement == true)
        }

        @Test func saveWithoutRequiredFieldsShowsFieldErrors() async throws {
            let wizard = SetupWizard(api: api)
            await wizard.start()
            await wizard.next()
            let editor = try #require(wizard.editor)
            await editor.load()
            let driver = try #require(editor.drivers.value?.first { !$0.fields.filter(\.required).isEmpty })
            editor.select(driver)
            for field in driver.fields where field.required { editor.draft.text[field.key] = "" }
            #expect(await wizard.saveDevice() == false)
            #expect(!editor.fieldErrors.isEmpty)
        }

        @Test func serverDemoJumpsToDone() async {
            let wizard = SetupWizard(api: api)
            await wizard.start()
            await wizard.startServerDemo()
            #expect(wizard.step == .done)
            #expect((wizard.devices.value ?? []).count >= 5)
        }

        @Test func priorityKeepsUserOrderAndAddsNewLoads() async throws {
            DemoBackend.shared.reset(scenario: .sunny)
            let wizard = SetupWizard(api: api, start: .device(.waterHeater))
            await wizard.start()
            await wizard.next()
            #expect(wizard.step == .priority)
            #expect(Set(wizard.priority) == Set(wizard.loads.map(\.id)))
            wizard.priority.reverse()
            let chosen = wizard.priority
            await wizard.next()
            #expect(wizard.step == .done)
            let settings = try await api.settings()
            #expect(settings.section("priority")["order"]?.array?.compactMap(\.int) == chosen)
        }
    }

    @Suite("Einstellungen bearbeiten")
    @MainActor
    struct SettingsEditorTests {
        let api = APIClient(session: .demo(), baseURL: DemoBackend.baseURL, token: DemoBackend.token)

        init() { DemoBackend.shared.reset() }

        @Test func editSaveAndDiscard() async {
            let editor = SettingsEditor(api: api)
            await editor.load()
            #expect(editor.isLoaded)
            #expect(!editor.isDirty)
            editor.update("tariff") { $0.set("grid_charge_limit_ct", 21.5) }
            #expect(editor.isDirty)
            #expect(await editor.save())
            #expect(!editor.isDirty)
            #expect(editor.section("tariff").number("grid_charge_limit_ct", 0) == 21.5)
            let reloaded = SettingsEditor(api: api)
            await reloaded.load()
            #expect(reloaded.section("tariff").number("grid_charge_limit_ct", 0) == 21.5)
            reloaded.update("regulation") { $0.set("grid_target_w", 100.0) }
            reloaded.discard()
            #expect(!reloaded.isDirty)
        }

        @Test func editsDuringSaveSurvive() async {
            let editor = SettingsEditor(api: api)
            await editor.load()
            editor.update("tariff") { $0.set("grid_charge_limit_ct", 18.0) }
            let saving = Task { await editor.save() }
            await Task.yield()
            editor.update("tariff") { $0.set("vat_pct", 19.0) }
            #expect(await saving.value)
            #expect(editor.section("tariff").number("grid_charge_limit_ct", 0) == 18)
            #expect(editor.section("tariff").number("vat_pct", 0) == 19)
            // Die zweite Änderung kam nach dem Absenden: gespeichert ist sie nicht, sichtbar bleibt sie.
            #expect(editor.section("tariff").saved.double("vat_pct") != 19)
            #expect(editor.isDirty)
        }
    }
}
