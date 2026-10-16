import Foundation
import Testing
@testable import MinePowerKit

@Suite("SVG-Pfade der Weboberfläche")
struct SVGPathTests {
    @Test func absoluteAndRelativeLines() {
        let path = SVGPath("M3 13h8V3H3v10Z")
        #expect(path.commands.first == .move(.init(3, 13)))
        #expect(path.commands[1] == .line(.init(11, 13)))
        #expect(path.commands[2] == .line(.init(11, 3)))
        #expect(path.commands.last == .close)
    }

    @Test func compactNumbersAndImplicitLines() {
        // "1.5.5" = 1.5 0.5; nach M folgen implizite L.
        let path = SVGPath("M1.5.5 2 2 3-3")
        #expect(path.commands == [.move(.init(1.5, 0.5)), .line(.init(2, 2)), .line(.init(3, -3))])
    }

    @Test func smoothCubicMirrorsControlPoint() {
        let path = SVGPath("M0 0C0 10 10 10 10 0S20-10 20 0")
        guard case .cubic(let c1, _, let end) = path.commands[2] else {
            Issue.record("kein Bogen")
            return
        }
        #expect(c1 == .init(10, -10))
        #expect(end == .init(20, 0))
    }

    @Test func arcsBecomeCubicsAndEndExactly() {
        // Kreis aus zwei Halbbögen (Icon "info": a9 9 0 1 0 0-18)
        let path = SVGPath("M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z")
        let cubics = path.commands.filter { if case .cubic = $0 { true } else { false } }
        #expect(cubics.count >= 4)
        let b = path.bounds
        #expect(abs(b.minX - 3) < 0.6 && abs(b.maxX - 21) < 0.6)
        #expect(abs(b.minY - 3) < 0.01 && abs(b.maxY - 21) < 0.01)
    }

    @Test func compactArcFlags() {
        // Flags ohne Trenner: "a2 2 0 1 1 2.83 2.83" und "a1 1 0 01 1 1"
        let path = SVGPath("M0 0a1 1 0 01 1 1")
        guard case .cubic(_, _, let end) = path.commands.last else {
            Issue.record("kein Bogen")
            return
        }
        #expect(abs(end.x - 1) < 1e-9 && abs(end.y - 1) < 1e-9)
    }

    @Test func garageBodyFromWeb() {
        let body = SVGPath("M94 197 L88 190 C85 184 84 174 86 166 L91 153 C112 140 152 128 198 127 C216 127 229 130 240 137 L263 152 C282 155 298 159 307 164 C313 168 316 174 316 182 L315 192 C315 195 313 197 309 197 L292 197 A25 25 0 1 0 244 197 L152 197 A25 25 0 1 0 104 197 Z")
        let b = body.bounds
        #expect(b.minX >= 84 && b.maxX <= 316.1)
        #expect(b.minY >= 126 && b.maxY < 230)
    }

    @Test func settingsIconParsesCompletely() {
        let gear = SVGPath("M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4")
        // Bis zum letzten Bogen gelesen: Endpunkt (9, 19.4).
        guard case .cubic(_, _, let end) = gear.commands.last else {
            Issue.record("unvollständig")
            return
        }
        #expect(abs(end.x - 9) < 1e-9 && abs(end.y - 19.4) < 1e-9)
        let b = gear.bounds
        #expect(b.minX > 0 && b.maxX < 24 && b.maxY <= 23.1)
    }
}
