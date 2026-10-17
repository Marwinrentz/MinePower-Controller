import Foundation
import Testing
@testable import MinePowerKit

@Suite("Wand-Ansicht")
struct WallTests {
    @Test func scaleStepsAreStable() {
        #expect(PowerScale.pick(0) == 1000)
        #expect(PowerScale.pick(800) == 1000)
        #expect(PowerScale.pick(900) == 2000)
        #expect(PowerScale.pick(-4000) == 5000)
        #expect(PowerScale.pick(100_000) == 30000)
    }

    @Test func balanceUsesDeadbandAsTargetBand() {
        var s = Snapshot()
        s.gridPower = -40
        s.deadbandW = 30
        var balance = Balance(s)
        #expect(balance.state == .balanced)
        #expect(balance.band == 50)
        s.gridPower = -2500
        balance = Balance(s)
        #expect(balance.state == .exporting)
        #expect(balance.scale == 3000)
        #expect(balance.offset(-6000) == -1)
        #expect(balance.bandShare > 0 && balance.bandShare <= 0.5)
    }

    @Test func urgentStatesWin() {
        var s = Snapshot()
        s.safety = "Hauptsicherung"
        #expect(WallState(s, fmt: .de).tone == .error)
        s.safety = nil
        s.paused = true
        #expect(WallState(s, fmt: .de).tone == .warn)
        s.paused = false
        s.wasteW = 900
        #expect(WallState(s, fmt: .de).tone == .waste)
        s.wasteW = 0
        #expect(WallState(s, fmt: .de).tone == .idle)
    }

    @Test func barsShareOneScale() {
        var s = Snapshot()
        s.pvPower = 6200
        s.housePower = 600
        s.gridPower = -3000
        s.battery = BatterySummary(soc: 50, power: 2000)
        let rows = WallBarRow.rows(s)
        #expect(rows.map(\.id) == ["pv", "house", "car", "water", "battery", "grid"])
        #expect(rows.last?.kind == .gridExport)
        #expect(rows.first { $0.kind == .battery }?.note != nil)
        #expect(WallBarRow.scale(rows) == 8000)
    }
}
