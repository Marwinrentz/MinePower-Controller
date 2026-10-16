import Foundation
import Testing
@testable import MinePowerKit

/// Fixtures: echte Antworten eines Demo-Backends (`ios/tools/capture_fixtures.py`).
enum Fixture {
    static func data(_ name: String) throws -> Data {
        let url = try #require(Bundle.module.url(forResource: name, withExtension: nil, subdirectory: "Fixtures"))
        return try Data(contentsOf: url)
    }

    static func decode<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        try JSONDecoder.minePower().decode(T.self, from: data(name))
    }
}

@Suite("Dekodierung echter Backend-Antworten")
struct DecodingTests {
    @Test func healthAndSetupState() throws {
        let health = try Fixture.decode(Health.self, "health.json")
        #expect(health.status == "ok")
        #expect(SemanticVersion(health.version) != nil)
        let setup = try Fixture.decode(SetupState.self, "setup-state.json")
        #expect(setup.needsAdmin == false)
    }

    @Test func liveSnapshotWithAllCategories() throws {
        let snapshot = try Fixture.decode(Snapshot.self, "live.json")
        let categories = Set(snapshot.allDevices.map(\.category))
        #expect(categories.isSuperset(of: [.inverter, .meter, .wallbox, .battery, .waterHeater]))
        #expect(snapshot.battery?.capacityKwh != nil)
        #expect(snapshot.batteryPlan?.intent == "auto")
        #expect(snapshot.time != nil)
        let wallbox = try #require(snapshot.devices(.wallbox).first)
        #expect(wallbox.lastSeen != nil)
        #expect(wallbox.presence != nil)
    }

    @Test func websocketEnvelope() throws {
        let data = try Fixture.data("ws-snapshot.json")
        let snapshot = try #require(LiveSocket.decodeSnapshot(data))
        #expect(!snapshot.allDevices.isEmpty)
    }

    @Test("Ältere Snapshots (2.x) bleiben lesbar", arguments: ["legacy-snapshot-full.json", "legacy-snapshot-demo.json",
                                                            "legacy-snapshot-safety.json"])
    func legacySnapshots(name: String) throws {
        let snapshot = try #require(LiveSocket.decodeSnapshot(Fixture.data(name)))
        #expect(snapshot.pvPower != nil)
    }

    @Test func safetySnapshotHasNoGridPower() throws {
        let snapshot = try #require(LiveSocket.decodeSnapshot(Fixture.data("legacy-snapshot-safety.json")))
        #expect(snapshot.safety != nil || snapshot.gridPower == nil)
        #expect(snapshot.overallState == .safety || snapshot.gridPower != nil)
    }

    @Test func devicesDriversAndLimits() throws {
        let devices = try Fixture.decode([Device].self, "devices.json")
        #expect(devices.count >= 5)
        let drivers = try Fixture.decode([DriverMeta].self, "drivers.json")
        #expect(drivers.count >= 70)
        #expect(drivers.contains { $0.category == .wallbox && $0.fields.contains { $0.type == .select } })
        #expect(drivers.contains { $0.fields.contains { $0.type == .password } })
        let limits = try Fixture.decode(DeviceLimits.self, "limits.json")
        #expect(limits["meter"] == 1)
    }

    @Test func accountAndUsers() throws {
        let me = try Fixture.decode(User.self, "me.json")
        #expect(me.role == .admin)
        #expect(me.role.canConfigure)
        let users = try Fixture.decode([User].self, "users.json")
        #expect(users.count == 1)
        let login = try Fixture.decode(TokenResponse.self, "login.json")
        #expect(login.user.email == me.email)
    }

    @Test func settingsAndSchedules() throws {
        let settings = try Fixture.decode(SettingsEnvelope.self, "settings.json")
        #expect(settings.section("regulation").double("grid_target_w") != nil)
        #expect(settings.section("tariff").string("provider") != nil)
        _ = try Fixture.decode([Schedule].self, "schedules.json")
    }

    @Test func batteryStatistikTarifPrognose() throws {
        let battery = try Fixture.decode(BatteryStatus.self, "battery-status.json")
        #expect(battery.devices.first?.controllable == true)
        #expect(battery.defaults?.maxMinutes ?? 0 > 0)
        _ = try Fixture.decode(Statistics.self, "statistics-24h.json")
        _ = try Fixture.decode(Statistics.self, "statistics-30d.json")
        let tariff = try Fixture.decode(TariffInfo.self, "tariff.json")
        #expect(tariff.provider == "none")
        _ = try Fixture.decode(Forecast.self, "forecast.json")
        _ = try Fixture.decode(HistorySeries.self, "history-24h.json")
        _ = try Fixture.decode([ChargeSession].self, "sessions.json")
    }

    @Test func diagnoseUndSystem() throws {
        let changelog = try Fixture.decode(Changelog.self, "changelog.json")
        #expect(changelog.entries.first?.version == changelog.version)
        let events = try Fixture.decode([AppEvent].self, "events.json")
        #expect(!events.isEmpty)
        _ = try Fixture.decode(FindingsReport.self, "findings.json")
        _ = try Fixture.decode(BackupStatus.self, "backup-status.json")
        _ = try Fixture.decode([Vehicle].self, "vehicles.json")
        let tools = try Fixture.decode([McpTool].self, "mcp-tools.json")
        #expect(tools.contains { $0.role == .admin })
        _ = try Fixture.decode([McpToken].self, "mcp-tokens.json")
    }

    @Test func unknownCategoryDoesNotBreakSnapshot() throws {
        let json = #"{"devices": [{"id": 9, "name": "X", "category": "heatpump"}], "pv_power": 1}"#
        let snapshot = try JSONDecoder.minePower().decode(Snapshot.self, from: Data(json.utf8))
        #expect(snapshot.allDevices.first?.category == .unknown)
    }

    @Test func historyPointsWithTimezoneVariants() throws {
        let json = #"{"pv_power": [{"time": "2026-10-16T20:00:00+00:00", "value": 1.5}, {"time": "2026-10-16T20:05:00Z", "value": 2}]}"#
        let series = try JSONDecoder.minePower().decode(HistorySeries.self, from: Data(json.utf8))
        #expect(series["pv_power"]?.count == 2)
    }
}

@Suite("Zeitstempel")
struct BackendDateTests {
    @Test(arguments: [
        "2026-10-16T20:50:13.379932+00:00",  // Live, Mikrosekunden
        "2026-10-16T20:50:13+00:00",         // Verlauf
        "2026-10-08T18:27:16.753887",        // Ereignisse ohne Zeitzone (UTC gemeint)
        "2026-10-16T20:50:13.949360Z",
        "2026-10-16T22:50:13+02:00",
    ])
    func parsesAllBackendForms(raw: String) throws {
        let date = try #require(BackendDate.parse(raw))
        #expect(date.timeIntervalSince1970 > 1_790_000_000)
    }

    @Test func naiveTimestampIsUTC() throws {
        let naive = try #require(BackendDate.parse("2026-10-16T20:00:00"))
        let utc = try #require(BackendDate.parse("2026-10-16T20:00:00+00:00"))
        #expect(naive == utc)
    }

    @Test func rejectsGarbage() {
        #expect(BackendDate.parse("gestern") == nil)
    }
}

@Suite("JSON-Werte")
struct JSONValueTests {
    @Test func integersStayIntegersWhenEncoded() throws {
        let data = try JSONEncoder().encode(["port": JSONValue.number(502), "scale": .number(0.1)])
        let text = String(decoding: data, as: UTF8.self)
        #expect(text.contains("\"port\":502"))
        #expect(text.contains("0.1"))
    }

    @Test func accessorsTolerateStrings() {
        let object: JSONObject = ["a": "12,5", "b": true, "c": .null]
        #expect(object.double("a") == 12.5)
        #expect(object.bool("b") == true)
        #expect(object.string("c") == nil)
    }
}
