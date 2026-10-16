import Foundation
import Testing
@testable import MinePowerKit

@Suite("Formatierung wie im Web")
struct FormattingTests {
    let fmt = Fmt.de

    @Test(arguments: [(950.4, "950 W"), (3450, "3,45 kW"), (12_345, "12,3 kW"), (-2100, "-2,10 kW")])
    func watts(value: Double, expected: String) {
        #expect(fmt.watts(value) == expected)
    }

    @Test func missingValuesShowDash() {
        #expect(fmt.watts(nil) == "–")
        #expect(fmt.kwh(.nan) == "–")
        #expect(fmt.percent(nil) == "–")
    }

    @Test func units() {
        #expect(fmt.kwh(18.4) == "18,4 kWh")
        #expect(fmt.cents(24.84) == "24,8 ct")
        #expect(fmt.percent(64.6) == "65 %")
        #expect(fmt.celsius(52) == "52 °C")
        #expect(fmt.duration(65 * 60) == "1 h 05 min")
        #expect(fmt.duration(12 * 60) == "12 min")
    }

    @Test func ago() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        #expect(fmt.ago(now.addingTimeInterval(-40), now: now) == "vor 40 s")
        #expect(fmt.ago(now.addingTimeInterval(-180), now: now) == "vor 3 min")
        #expect(fmt.ago(now.addingTimeInterval(-7200), now: now) == "vor 2 h")
    }

    @Test func calendarDateHasNoTimezoneShift() {
        #expect(fmt.calendarDate("2026-10-15") == "15.10.2026")
    }
}

@Suite("Serveradresse")
struct ServerAddressTests {
    @Test(arguments: [
        ("192.168.1.5", "http://192.168.1.5:8080"),
        ("192.168.1.5:8000", "http://192.168.1.5:8000"),
        ("minepower.local", "http://minepower.local:8080"),
        ("unraid", "http://unraid:8080"),
        ("https://energie.example.de/minepower/", "https://energie.example.de/minepower"),
        ("energie.example.de", "https://energie.example.de"),
        ("energie.example.de:8443", "https://energie.example.de:8443"),
        ("minepower.fritz.box", "http://minepower.fritz.box:8080"),
        ("  http://10.0.0.2:8080/  ", "http://10.0.0.2:8080"),
    ])
    func normalizes(input: String, expected: String) {
        #expect(ServerAddress.normalize(input)?.absoluteString == expected)
    }

    @Test(arguments: ["", "ftp://x", "a b", "http://"])
    func rejects(input: String) {
        #expect(ServerAddress.normalize(input) == nil)
    }

    @Test func localDetection() {
        #expect(ServerAddress.isLocalHost("172.20.1.1"))
        #expect(ServerAddress.isLocalHost("100.101.1.2"))
        #expect(!ServerAddress.isLocalHost("8.8.8.8"))
        #expect(!ServerAddress.isLocalHost("energie.example.de"))
        #expect(ServerAddress.isInsecureRemote(URL(string: "http://energie.example.de")!))
        #expect(!ServerAddress.isInsecureRemote(URL(string: "http://192.168.1.2:8080")!))
    }

    @Test func scanCandidatesSkipOwnAddress() {
        let list = ServerAddress.scanCandidates(ownAddress: "192.168.178.20", ports: [8080])
        #expect(list.count == 253)
        #expect(!list.contains { $0.host == "192.168.178.20" })
        #expect(ServerAddress.scanCandidates(ownAddress: "8.8.8.8").isEmpty)
    }

    @Test func websocketURL() {
        let url = LiveSocket.socketURL(base: URL(string: "https://e.example.de/mp")!, token: "abc")
        #expect(url?.absoluteString == "wss://e.example.de/mp/api/ws?token=abc")
    }
}

@Suite("Kompatibilität App ↔ Server")
struct CompatibilityTests {
    @Test func versions() {
        #expect(SemanticVersion("3.1.1")! > SemanticVersion("3.1.0")!)
        #expect(SemanticVersion("v2.17")! < SemanticVersion("3.0.0")!)
        #expect(SemanticVersion("3.1.0-beta")?.patch == 0)
        #expect(SemanticVersion("unbekannt") == nil)
    }

    @Test func matrix() {
        #expect(Compatibility.evaluate(serverVersion: "3.1.1") == .ok)
        #expect(Compatibility.evaluate(serverVersion: "2.17.0") == .serverTooOld(minimum: .init(3, 1, 0), server: .init(2, 17, 0)))
        #expect(Compatibility.evaluate(serverVersion: "4.0.0") == .appMayBeOutdated(server: .init(4, 0, 0)))
        #expect(Compatibility.evaluate(serverVersion: nil) == .unknown)
        #expect(!Compatibility.supports(.mcp, serverVersion: "3.0.0"))
        #expect(Compatibility.supports(.mcp, serverVersion: "3.1.1"))
    }
}

@Suite("Einrichtungsassistent")
struct SetupFlowTests {
    @Test func walksAllSteps() {
        var flow = SetupFlow()
        #expect(flow.step == .welcome)
        flow.next()
        #expect(flow.step == .device(.inverter))
        #expect(!flow.step.isOptional)
        flow.next()
        flow.next()
        #expect(flow.step == .device(.wallbox))
        #expect(flow.step.isOptional)
        for _ in 0..<10 { flow.next() }
        #expect(flow.isFinished)
        #expect(flow.progress == 1)
        #expect(!flow.canGoBack)
    }

    @Test func limitsAndGridMeasurement() throws {
        let devices = try Fixture.decode([Device].self, "devices.json")
        let drivers = try Fixture.decode([DriverMeta].self, "drivers.json")
        #expect(!SetupFlow.canAdd(.meter, devices: devices, limits: ["meter": 1]))
        #expect(SetupFlow.canAdd(.wallbox, devices: devices, limits: ["meter": 1]))
        #expect(SetupFlow.hasGridMeasurement(devices: devices, drivers: drivers))
        #expect(!SetupFlow.hasGridMeasurement(devices: [], drivers: drivers))
    }

    @Test func formValidation() {
        let fields = [
            ConfigField(key: "host", label: "IP", type: .text),
            ConfigField(key: "port", label: "Port", type: .number, required: false),
            ConfigField(key: "password", label: "Passwort", type: .password),
            ConfigField(key: "phases", label: "Phasen", type: .select, options: [.init(value: "1", label: "1"),
                                                                                 .init(value: "3", label: "3")]),
        ]
        let issues = DriverForm.validate(["port": "abc", "phases": "2"], fields: fields)
        #expect(Set(issues.map(\.key)) == ["host", "port", "password", "phases"])
        // Beim Bearbeiten darf das Passwort leer bleiben (bleibt serverseitig erhalten)
        let editing = DriverForm.validate(["host": "192.0.2.1", "phases": "3"], fields: fields, editing: true)
        #expect(editing.isEmpty)
        #expect(DriverForm.value("1,5", for: fields[1]) == .number(1.5))
        #expect(DriverForm.value("", for: fields[0]) == nil)
    }

    @Test func weekdays() {
        #expect(Weekdays.workdays.rawValue == 31)
        #expect(Weekdays.weekend.rawValue == 96)
        #expect(Weekdays(rawValue: 127).label() == "täglich")
        #expect(Weekdays([.monday, .wednesday]).label() == "Mo, Mi")
        #expect(ClockTime.isValid("06:30"))
        #expect(!ClockTime.isValid("24:00"))
        #expect(!ClockTime.isValid("6:30"))
    }
}

@Suite("Ladeliste (CSV)")
struct ChargeListTests {
    @Test func parsesExportWithQuotedFields() throws {
        let csv = String(decoding: try Fixture.data("sessions-sample.csv"), as: UTF8.self)
        let list = ChargeList(csv: csv)
        #expect(list.rows.count == 2)
        #expect(list.rows[1].device == "Wallbox; Carport")
        #expect(list.rows[1].end == nil)
        #expect(abs(list.totalEnergy - 22.4) < 0.001)
        #expect(abs(list.solarShare - 15.7 / 22.4) < 0.001)
        #expect(abs(list.totalCost - 2.52) < 0.001)
    }

    @Test func emptyExportFromDemo() throws {
        let csv = String(decoding: try Fixture.data("sessions-export.csv"), as: UTF8.self)
        #expect(ChargeList(csv: csv).rows.isEmpty)
    }
}

@Suite("Beschriftungen verteilen")
struct SpreadTests {
    @Test func keepsOrderAndGap() {
        #expect(spreadLabels([100, 104, 50], gap: 16) == [100, 116, 50])
        #expect(spreadLabels([95, 90], gap: 18, limit: 100) == [100, 90])
    }
}

@Suite("Einstellungs-Entwurf")
struct SettingsDraftTests {
    @Test func dirtyResetAndRebase() {
        var draft = SettingsDraft(["min_current": 6, "mode": "pv_only"])
        #expect(!draft.isDirty)
        draft.set("min_current", 8.0)
        #expect(draft.isDirty)
        #expect(draft.changes == ["min_current": 8])
        #expect(draft.number("max_current", 16) == 16)
        draft.rebase(["min_current": 6, "mode": "min_pv"])
        #expect(draft.string("mode", "") == "min_pv")
        #expect(draft.number("min_current", 0) == 8)
        draft.reset()
        #expect(!draft.isDirty)
    }
}

@Suite("Zahlen aus Eingaben")
struct ParseNumberTests {
    @Test(arguments: [("1.400", 1400.0), ("0,5", 0.5), ("0.5", 0.5), ("-12", -12.0), ("1 400", 1400.0),
                      ("1.234,5", 1234.5), ("2.25", 2.25)] as [(String, Double)])
    func parses(raw: String, expected: Double) {
        #expect(Fmt.parseNumber(raw) == expected)
    }

    @Test func rejectsGarbage() {
        #expect(Fmt.parseNumber("") == nil)
        #expect(Fmt.parseNumber("abc") == nil)
    }
}
