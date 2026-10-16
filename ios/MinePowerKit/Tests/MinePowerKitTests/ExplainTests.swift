import Foundation
import Testing
@testable import MinePowerKit

@Suite("Erklärung und Energiefluss")
struct ExplainTests {
    func device(_ category: DeviceCategory, _ json: String = "{}") throws -> SnapshotDevice {
        var object = try JSONDecoder().decode(JSONObject.self, from: Data(json.utf8))
        object["id"] = 9
        object["name"] = .string(category == .wallbox ? "Wallbox" : "Heizstab")
        object["category"] = .string(category.rawValue)
        object["enabled"] = true
        object["online"] = object["online"] ?? true
        return try JSONDecoder.minePower().decode(SnapshotDevice.self, from: JSONEncoder().encode(object))
    }

    @Test func sunnyDemoHeadline() {
        let snapshot = DemoBackend().snapshot()
        let explanation = Explanation(snapshot, fmt: .de)
        #expect(!explanation.headline.isEmpty)
        #expect(explanation.reasons.contains { $0.id == "car" })
        #expect(explanation.reasons.contains { $0.id == "water" })
    }

    @Test func pausedWins() {
        var snapshot = Snapshot()
        snapshot.paused = true
        snapshot.wallboxPower = 3000
        let explanation = Explanation(snapshot, fmt: .de)
        #expect(explanation.tone == .warn)
        #expect(explanation.reasons.first?.id == "paused")
    }

    @Test func carWaitingForSurplusShowsThresholdAndFree() throws {
        let car = try device(.wallbox, #"{"presence": "plugged", "start_threshold_w": 1400, "decision": {"reason": "warte auf Überschuss (620 W)"}}"#)
        let sentence = Explanation.carSentence(car, fmt: .de)
        #expect(sentence.text.contains("1,40 kW"))
        #expect(sentence.text.contains("620 W"))
        #expect(sentence.tone == .idle)
    }

    @Test(arguments: [("Überschuss reicht (4067 W) – Start in 40 s", 40), ("nichts", nil)] as [(String, Int?)])
    func startDelayParsing(reason: String, expected: Int?) {
        #expect(Explanation.startIn(reason) == expected)
    }

    @Test func legacyCarStateWithoutPresence() throws {
        #expect(CarState(try device(.wallbox, #"{"data": {"state": "charging", "power": 3200}}"#)) == .charging)
        #expect(CarState(try device(.wallbox, #"{"data": {"state": "idle"}}"#)) == .unplugged)
        #expect(CarState(try device(.wallbox, #"{"online": false, "last_error": "Proxy http://10.0.0.5:8080 down"}"#)) == .proxy)
        #expect(CarState(nil) == .none)
    }

    @Test func waterBoostAndOwnProgram() throws {
        let boosting = try device(.waterHeater, #"{"boost": true, "boost_state": {"remaining_s": 1800, "end_mode": "time"}}"#)
        #expect(Explanation.waterSentence(boosting, fmt: .de).text.contains("30 min"))
        let own = try device(.waterHeater, #"{"data": {"device_mode": "legionella", "power": 2000}}"#)
        #expect(Explanation.waterSentence(own, fmt: .de).tone == .warn)
    }

    @Test func wasteCausesSkipDevicesAlreadyNamed() {
        let causes = Explanation.wasteCauses("Batterie lädt mit 3957 W – sie hat Vorrang; Wallbox: warte auf Überschuss (4067 W); Heizstab: Zieltemperatur erreicht",
                                             skip: ["Wallbox"])
        #expect(causes.count == 2)
        #expect(causes.last?.contains("Heizstab") == true)
    }

    @Test func flowDirectionsFromDemo() {
        let flow = EnergyFlow(DemoBackend().snapshot())
        #expect(flow.branch(.pv)?.direction == .toHouse)
        #expect(flow.branch(.car)?.direction == .fromHouse)
        #expect(flow.branch(.battery)?.direction == .fromHouse)
        #expect((flow.branch(.pv)?.intensity ?? 0) > (flow.branch(.water)?.intensity ?? 1))
        #expect(flow.accessibilitySummary(fmt: .de).split(separator: ",").count >= 6)
    }

    @Test func nightFlowBatteryFeedsHouse() {
        let demo = DemoBackend()
        demo.scenario = .night
        let flow = EnergyFlow(demo.snapshot())
        #expect(flow.branch(.battery)?.direction == .toHouse)
        #expect(flow.branch(.pv)?.direction == .idle)
        #expect(flow.branch(.car)?.direction == .idle)
    }

    @Test func intensityIsBoundedAndMonotonic() {
        let small = EnergyFlow.Branch(node: .car, watts: 100, direction: .fromHouse).intensity
        let large = EnergyFlow.Branch(node: .car, watts: 11_000, direction: .fromHouse).intensity
        #expect(small >= 0.12 && small < large && large <= 1)
        #expect(EnergyFlow.Branch(node: .car, watts: 5000, direction: .idle).intensity == 0)
    }
}
