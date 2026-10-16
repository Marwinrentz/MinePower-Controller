import Foundation

/// SVG-Pfaddaten ("M3 13h8V3…") in absolute Zeichenbefehle.
///
/// Damit nutzt die App exakt dieselben Symbole und Grafiken wie die
/// Weboberfläche (`icons.tsx`, `Garage.tsx` …), ohne sie nachzuzeichnen.
/// Bögen (`A`) werden in kubische Bézierkurven umgerechnet, `S`/`T`
/// spiegeln den letzten Kontrollpunkt wie im Standard. Plattformunabhängig,
/// damit es unter Linux getestet wird; die App macht daraus einen SwiftUI-`Path`.
public struct SVGPath: Sendable, Equatable {
    public struct Point: Sendable, Equatable {
        public var x: Double
        public var y: Double
        public init(_ x: Double, _ y: Double) {
            self.x = x
            self.y = y
        }
    }

    public enum Command: Sendable, Equatable {
        case move(Point)
        case line(Point)
        case cubic(Point, Point, Point)
        case quad(Point, Point)
        case close
    }

    public private(set) var commands: [Command] = []

    public init(_ data: String) {
        var scanner = Tokenizer(data)
        var current = Point(0, 0)
        var start = Point(0, 0)
        var lastCubic: Point?
        var lastQuad: Point?
        var command: Character = "M"

        while let token = scanner.nextCommandOrNumber() {
            if case .command(let c) = token {
                command = c
                if c == "Z" || c == "z" {
                    commands.append(.close)
                    current = start
                    lastCubic = nil
                    lastQuad = nil
                    continue
                }
            } else {
                scanner.pushBack(token)
            }
            let relative = command.isLowercase
            func point(_ x: Double, _ y: Double) -> Point {
                relative ? Point(current.x + x, current.y + y) : Point(x, y)
            }
            switch command.uppercased().first {
            case "M":
                guard let x = scanner.number(), let y = scanner.number() else { return }
                current = point(x, y)
                start = current
                commands.append(.move(current))
                // Weitere Paare nach M sind implizite L.
                command = relative ? "l" : "L"
                lastCubic = nil
                lastQuad = nil
            case "L":
                guard let x = scanner.number(), let y = scanner.number() else { return }
                current = point(x, y)
                commands.append(.line(current))
                lastCubic = nil
                lastQuad = nil
            case "H":
                guard let x = scanner.number() else { return }
                current = Point(relative ? current.x + x : x, current.y)
                commands.append(.line(current))
                lastCubic = nil
                lastQuad = nil
            case "V":
                guard let y = scanner.number() else { return }
                current = Point(current.x, relative ? current.y + y : y)
                commands.append(.line(current))
                lastCubic = nil
                lastQuad = nil
            case "C":
                guard let x1 = scanner.number(), let y1 = scanner.number(), let x2 = scanner.number(),
                      let y2 = scanner.number(), let x = scanner.number(), let y = scanner.number() else { return }
                let c1 = point(x1, y1), c2 = point(x2, y2), end = point(x, y)
                commands.append(.cubic(c1, c2, end))
                lastCubic = c2
                lastQuad = nil
                current = end
            case "S":
                guard let x2 = scanner.number(), let y2 = scanner.number(), let x = scanner.number(),
                      let y = scanner.number() else { return }
                let c1 = lastCubic.map { Point(2 * current.x - $0.x, 2 * current.y - $0.y) } ?? current
                let c2 = point(x2, y2), end = point(x, y)
                commands.append(.cubic(c1, c2, end))
                lastCubic = c2
                lastQuad = nil
                current = end
            case "Q":
                guard let x1 = scanner.number(), let y1 = scanner.number(), let x = scanner.number(),
                      let y = scanner.number() else { return }
                let c = point(x1, y1), end = point(x, y)
                commands.append(.quad(c, end))
                lastQuad = c
                lastCubic = nil
                current = end
            case "T":
                guard let x = scanner.number(), let y = scanner.number() else { return }
                let c = lastQuad.map { Point(2 * current.x - $0.x, 2 * current.y - $0.y) } ?? current
                let end = point(x, y)
                commands.append(.quad(c, end))
                lastQuad = c
                lastCubic = nil
                current = end
            case "A":
                guard let rx = scanner.number(), let ry = scanner.number(), let rotation = scanner.number(),
                      let large = scanner.flag(), let sweep = scanner.flag(),
                      let x = scanner.number(), let y = scanner.number() else { return }
                let end = point(x, y)
                commands += Self.arc(from: current, to: end, rx: rx, ry: ry, rotation: rotation,
                                     largeArc: large, sweep: sweep)
                lastCubic = nil
                lastQuad = nil
                current = end
            default:
                return
            }
        }
    }

    /// Umgebendes Rechteck aller Punkte (inkl. Kontrollpunkte – für Tests genügt das).
    public var bounds: (minX: Double, minY: Double, maxX: Double, maxY: Double) {
        var xs: [Double] = [], ys: [Double] = []
        for command in commands {
            switch command {
            case .move(let p), .line(let p): xs.append(p.x); ys.append(p.y)
            case .cubic(let a, let b, let c): xs += [a.x, b.x, c.x]; ys += [a.y, b.y, c.y]
            case .quad(let a, let b): xs += [a.x, b.x]; ys += [a.y, b.y]
            case .close: break
            }
        }
        return (xs.min() ?? 0, ys.min() ?? 0, xs.max() ?? 0, ys.max() ?? 0)
    }

    // MARK: - Bogen → Bézier (SVG 1.1, Anhang F.6)

    static func arc(from p0: Point, to p1: Point, rx inRx: Double, ry inRy: Double, rotation: Double,
                    largeArc: Bool, sweep: Bool) -> [Command] {
        if p0 == p1 { return [] }
        var rx = abs(inRx), ry = abs(inRy)
        if rx == 0 || ry == 0 { return [.line(p1)] }
        let phi = rotation * .pi / 180
        let cosPhi = cos(phi), sinPhi = sin(phi)
        let dx = (p0.x - p1.x) / 2, dy = (p0.y - p1.y) / 2
        let x1p = cosPhi * dx + sinPhi * dy
        let y1p = -sinPhi * dx + cosPhi * dy
        let lambda = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
        if lambda > 1 {
            rx *= lambda.squareRoot()
            ry *= lambda.squareRoot()
        }
        let num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
        let den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
        var coef = den == 0 ? 0 : max(0, num / den).squareRoot()
        if largeArc == sweep { coef = -coef }
        let cxp = coef * rx * y1p / ry
        let cyp = -coef * ry * x1p / rx
        let cx = cosPhi * cxp - sinPhi * cyp + (p0.x + p1.x) / 2
        let cy = sinPhi * cxp + cosPhi * cyp + (p0.y + p1.y) / 2

        func angle(_ ux: Double, _ uy: Double, _ vx: Double, _ vy: Double) -> Double {
            let sign: Double = ux * vy - uy * vx < 0 ? -1 : 1
            let dot = (ux * vx + uy * vy) / ((ux * ux + uy * uy).squareRoot() * (vx * vx + vy * vy).squareRoot())
            return sign * acos(max(-1, min(1, dot)))
        }
        let theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
        var delta = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
        if !sweep, delta > 0 { delta -= 2 * .pi }
        if sweep, delta < 0 { delta += 2 * .pi }

        let segments = max(1, Int((abs(delta) / (.pi / 2)).rounded(.up)))
        let step = delta / Double(segments)
        let k = 4.0 / 3.0 * tan(step / 4)
        var out: [Command] = []
        var t = theta1
        func ellipsePoint(_ a: Double) -> Point {
            let x = rx * cos(a), y = ry * sin(a)
            return Point(cosPhi * x - sinPhi * y + cx, sinPhi * x + cosPhi * y + cy)
        }
        func derivative(_ a: Double) -> Point {
            let x = -rx * sin(a), y = ry * cos(a)
            return Point(cosPhi * x - sinPhi * y, sinPhi * x + cosPhi * y)
        }
        for i in 0..<segments {
            let a1 = t, a2 = t + step
            let s = ellipsePoint(a1), e = i == segments - 1 ? p1 : ellipsePoint(a2)
            let d1 = derivative(a1), d2 = derivative(a2)
            out.append(.cubic(Point(s.x + k * d1.x, s.y + k * d1.y), Point(e.x - k * d2.x, e.y - k * d2.y), e))
            t = a2
        }
        return out
    }
}

/// Zerlegt Pfaddaten in Befehlsbuchstaben und Zahlen ("1.5.5" = 1.5, 0.5; "-2-3" = -2, -3).
private struct Tokenizer {
    enum Token: Equatable {
        case command(Character)
        case number(Double)
    }

    private let chars: [Character]
    private var index = 0
    private var pending: Token?

    init(_ text: String) { chars = Array(text) }

    mutating func pushBack(_ token: Token) { pending = token }

    mutating func nextCommandOrNumber() -> Token? {
        if let pending {
            self.pending = nil
            return pending
        }
        skipSeparators()
        guard index < chars.count else { return nil }
        let c = chars[index]
        if c.isLetter, c != "e", c != "E" {
            index += 1
            return .command(c)
        }
        return readNumber().map(Token.number)
    }

    mutating func number() -> Double? {
        if let pending {
            self.pending = nil
            if case .number(let value) = pending { return value }
            self.pending = pending
            return nil
        }
        skipSeparators()
        return readNumber()
    }

    /// Bogen-Flags dürfen ohne Trenner stehen ("a1 1 0 01 1 1").
    mutating func flag() -> Bool? {
        skipSeparators()
        guard index < chars.count else { return nil }
        let c = chars[index]
        guard c == "0" || c == "1" else { return nil }
        index += 1
        return c == "1"
    }

    private mutating func skipSeparators() {
        while index < chars.count, chars[index] == " " || chars[index] == "," || chars[index].isNewline || chars[index] == "\t" {
            index += 1
        }
    }

    private mutating func readNumber() -> Double? {
        let begin = index
        var seenDot = false, seenExp = false
        if index < chars.count, chars[index] == "-" || chars[index] == "+" { index += 1 }
        while index < chars.count {
            let c = chars[index]
            if c.isNumber {
                index += 1
            } else if c == ".", !seenDot, !seenExp {
                seenDot = true
                index += 1
            } else if c == "e" || c == "E", !seenExp {
                seenExp = true
                index += 1
                if index < chars.count, chars[index] == "-" || chars[index] == "+" { index += 1 }
            } else {
                break
            }
        }
        guard index > begin else { return nil }
        return Double(String(chars[begin..<index]))
    }
}
