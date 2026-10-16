import MinePowerKit
import SwiftUI

/// Warmwasserspeicher – Portierung von `frontend/src/components/Boiler.tsx`.
///
/// Zylinder im Anschnitt (280 × 300): Die Wassersäule steigt mit der
/// Temperatur (15 °C leer … 80 °C voll), der Farbverlauf hängt an der Skala.
/// Links die Temperaturskala, rechts Ziel-, Boost- und Puffermarken. Beim
/// Heizen glüht der Heizstab, die Oberfläche wogt und Bläschen steigen auf –
/// je mehr Leistung, desto mehr. Eigenes Geräteprogramm: Stab in Warnfarbe.
struct BoilerView: View {
    struct Mark: Identifiable, Equatable {
        enum Kind { case target, boost, buffer }
        var temp: Double
        var label: String
        var kind: Kind
        var id: String { "\(kind)" }
    }

    var temp: Double?
    var power: Double
    var maxPower: Double
    var marks: [Mark]
    var own = false
    var label: String

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static let viewBox = CGSize(width: 280, height: 300)
    static let tMin = 15.0, tMax = 80.0
    static let innerTop = 38.0, innerBottom = 262.0, innerX = 58.0, innerW = 100.0

    static func y(_ temp: Double) -> Double {
        let f = max(0, min(1, (temp - tMin) / (tMax - tMin)))
        return innerBottom - f * (innerBottom - innerTop)
    }

    private var heating: Bool { power > 50 }

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion || !heating)) { timeline in
            Canvas { context, size in
                let scale = min(size.width / Self.viewBox.width, size.height / Self.viewBox.height)
                context.translateBy(x: (size.width - Self.viewBox.width * scale) / 2,
                                    y: (size.height - Self.viewBox.height * scale) / 2)
                context.scaleBy(x: scale, y: scale)
                draw(in: &context, time: timeline.date.timeIntervalSinceReferenceDate)
            }
        }
        .aspectRatio(Self.viewBox.width / Self.viewBox.height, contentMode: .fit)
        .frame(maxWidth: 300, maxHeight: 340)
        .accessibilityElement()
        .accessibilityLabel(Text(label))
        .accessibilityValue(Text(temp.map { Fmt().celsius($0) } ?? "–"))
        .accessibilityAddTraits(.isImage)
    }

    private func draw(in ctx: inout GraphicsContext, time t: Double) {
        let level = temp.map(Self.y) ?? Self.innerBottom
        let inner = CGRect(x: Self.innerX, y: Self.innerTop, width: Self.innerW, height: Self.innerBottom - Self.innerTop)
        let innerPath = Path(roundedRect: inner, cornerRadius: 30)

        // Temperaturskala
        for v in [20.0, 40, 60, 80] {
            let y = Self.y(v)
            ctx.stroke(Path { $0.move(to: CGPoint(x: 32, y: y)); $0.addLine(to: CGPoint(x: 40, y: y)) },
                       with: .color(Web.lineStrong), style: StrokeStyle(lineWidth: 2, lineCap: .round))
            ctx.draw(Text("\(Int(v))°").font(.custom(Typo.Weight.semibold.rawValue, size: 11)).foregroundStyle(Web.textDim),
                     at: CGPoint(x: 28, y: y), anchor: .trailing)
        }

        // Anschlüsse
        ctx.stroke(line(84, 26, 84, 10), with: .color(Web.waterCold), style: StrokeStyle(lineWidth: 7, lineCap: .round))
        ctx.stroke(line(132, 26, 132, 10), with: .color(Web.waterHot), style: StrokeStyle(lineWidth: 7, lineCap: .round))

        // Füße, Mantel, Sichtfenster
        var feet = Path()
        feet.move(to: CGPoint(x: 66, y: 274)); feet.addLine(to: CGPoint(x: 66, y: 286))
        feet.move(to: CGPoint(x: 150, y: 274)); feet.addLine(to: CGPoint(x: 150, y: 286))
        ctx.stroke(feet, with: .color(Web.artLine), style: StrokeStyle(lineWidth: 7, lineCap: .round))
        let shell = Path(roundedRect: CGRect(x: 46, y: 24, width: 124, height: 252), cornerRadius: 42)
        ctx.fill(shell, with: .linearGradient(
            Gradient(stops: [.init(color: Web.boilerShade, location: 0), .init(color: Web.boilerHi, location: 0.38),
                             .init(color: Web.boilerShade, location: 1)]),
            startPoint: CGPoint(x: 46, y: 0), endPoint: CGPoint(x: 170, y: 0)))
        ctx.stroke(shell, with: .color(Web.artLine), lineWidth: 2)
        ctx.fill(innerPath, with: .color(Web.boilerEmpty))
        var innerEdge = ctx
        innerEdge.opacity = 0.5
        innerEdge.stroke(innerPath, with: .color(Web.artLine), lineWidth: 1.5)

        // Wasser, Glühen, Bläschen, Glanz – im Sichtfenster
        var water = ctx
        water.clip(to: innerPath)
        if temp != nil {
            let shift = heating && !reduceMotion ? (t.truncatingRemainder(dividingBy: 2.4) / 2.4) * 50 : 0
            water.fill(waterPath(level: level, shift: shift), with: .linearGradient(
                Gradient(stops: [
                    .init(color: Web.waterCold, location: 0),
                    .init(color: Web.waterCold, location: Self.offset(32)),
                    .init(color: Web.waterWarm, location: Self.offset(46)),
                    .init(color: Web.waterHot, location: Self.offset(62)),
                    .init(color: Web.waterHot, location: 1),
                ]),
                startPoint: CGPoint(x: 0, y: Self.innerBottom), endPoint: CGPoint(x: 0, y: Self.innerTop)))
        }
        if heating {
            var glow = water
            glow.opacity = reduceMotion ? 0.8 : 0.65 + 0.35 * cos(t * 2 * .pi / 1.6)
            glow.fill(Path(ellipseIn: CGRect(x: 38, y: 214, width: 124, height: 44)),
                      with: .radialGradient(Gradient(colors: [Color(rgb: 0xFF8A3D, opacity: 0.75), Color(rgb: 0xFF8A3D, opacity: 0)]),
                                            center: CGPoint(x: 100, y: 236), startRadius: 0, endRadius: 62))
            let count = max(2, min(7, Int((power / max(maxPower, 1) * 7).rounded())))
            let rise = max(36, Self.innerBottom - level - 16)
            for i in 0..<count where !reduceMotion {
                let delay = Double(i) * 0.41
                let p = ((t + 2.2 - delay.truncatingRemainder(dividingBy: 2.2)).truncatingRemainder(dividingBy: 2.2)) / 2.2
                let opacity = p < 0.15 ? 0.9 * p / 0.15 : 0.9 * (1 - (p - 0.15) / 0.85)
                let cx = 72 + Double((i * 19) % 76)
                let cy = Self.innerBottom - 30 - rise * p * p
                let r = 2.2 + Double(i % 3) * 0.9
                water.fill(Path(ellipseIn: CGRect(x: cx - r, y: cy - r, width: r * 2, height: r * 2)),
                           with: .color(.white.opacity(0.8 * opacity)))
            }
        }
        water.fill(Path(inner), with: .linearGradient(
            Gradient(stops: [.init(color: .white.opacity(0), location: 0), .init(color: .white.opacity(0.22), location: 0.25),
                             .init(color: .white.opacity(0), location: 0.45)]),
            startPoint: CGPoint(x: inner.minX, y: 0), endPoint: CGPoint(x: inner.maxX, y: 0)))

        // Heizstab
        let rod = SVGShape("M44 242 H128 C138 242 138 228 128 228 H70",
                           viewBox: CGRect(origin: .zero, size: Self.viewBox), fit: false)
            .path(in: CGRect(origin: .zero, size: Self.viewBox))
        var rodContext = ctx
        let rodColor: Color
        if own {
            rodColor = Palette.warn.fill
            rodContext.addFilter(.shadow(color: Palette.warn.fill, radius: 4))
        } else if heating {
            let pulse = reduceMotion ? 0 : (1 - cos(t * 2 * .pi / 1.4)) / 2
            rodColor = Color(rgb: pulse > 0.5 ? 0xFB923C : 0xF97316)
            rodContext.addFilter(.shadow(color: Color(rgb: 0xF97316), radius: 4 + 4 * pulse))
        } else {
            rodColor = Web.artLine
        }
        rodContext.stroke(rod, with: .color(rodColor), style: StrokeStyle(lineWidth: 5, lineCap: .round, lineJoin: .round))
        let flange = Path(roundedRect: CGRect(x: 34, y: 230, width: 14, height: 20), cornerRadius: 3)
        ctx.fill(flange, with: .color(Web.artMetal))
        ctx.stroke(flange, with: .color(Web.artLine), lineWidth: 1.5)

        // Marken rechts
        let ys = marks.map { Self.y($0.temp) }
        let textY = spreadLabels(ys, gap: 16)
        for (i, mark) in marks.enumerated() {
            let color: Color = switch mark.kind {
            case .target: Web.textStrong
            case .boost: Palette.water.fill
            case .buffer: Palette.pv.fill
            }
            var p = Path()
            p.move(to: CGPoint(x: Self.innerX + Self.innerW - 8, y: ys[i]))
            p.addLine(to: CGPoint(x: 176, y: ys[i]))
            p.addLine(to: CGPoint(x: 182, y: textY[i]))
            p.addLine(to: CGPoint(x: 186, y: textY[i]))
            ctx.stroke(p, with: .color(color),
                       style: StrokeStyle(lineWidth: mark.kind == .target ? 2 : 1.5, dash: mark.kind == .target ? [] : [4, 3]))
            ctx.draw(Text(mark.label).font(.custom(Typo.Weight.bold.rawValue, size: 12)).foregroundStyle(color),
                     at: CGPoint(x: 190, y: textY[i]), anchor: .leading)
        }

        // Temperatur als Plakette
        let pill = Path(roundedRect: CGRect(x: 64, y: 128, width: 88, height: 38), cornerRadius: 19)
        ctx.fill(pill, with: .color(Web.surface.opacity(0.94)))
        ctx.stroke(pill, with: .color(Web.line), lineWidth: 1)
        ctx.draw(Text(temp.map { Fmt().celsius($0) } ?? "–").font(.custom(Typo.Weight.extraBold.rawValue, size: 21))
                    .foregroundStyle(Web.textStrong),
                 at: CGPoint(x: 108, y: 147))
    }

    static func offset(_ temp: Double) -> Double { (temp - tMin) / (tMax - tMin) }

    /// Wasserkörper mit welliger Oberfläche, breiter als das Fenster (eine Periode = 50).
    private func waterPath(level: Double, shift: Double) -> Path {
        var path = Path()
        var x = Self.innerX - 50 + shift
        path.move(to: CGPoint(x: x, y: level))
        for i in 0..<8 {
            let up = i % 2 == 0
            path.addQuadCurve(to: CGPoint(x: x + 25, y: level), control: CGPoint(x: x + 12.5, y: level + (up ? -3.5 : 3.5)))
            x += 25
        }
        path.addLine(to: CGPoint(x: x, y: Self.innerBottom + 4))
        path.addLine(to: CGPoint(x: Self.innerX - 50 + shift, y: Self.innerBottom + 4))
        path.closeSubpath()
        return path
    }

    private func line(_ x1: Double, _ y1: Double, _ x2: Double, _ y2: Double) -> Path {
        Path { $0.move(to: CGPoint(x: x1, y: y1)); $0.addLine(to: CGPoint(x: x2, y: y2)) }
    }
}
