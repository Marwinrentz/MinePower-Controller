import MinePowerKit
import SwiftUI

/// Gerätegrafiken: Der Zustand steckt in der Form, nicht in einer Zahl daneben.
///
/// Eigene, schlichte Vektorformen statt Herstellerbilder: gezeichnet ist die
/// Gattung (Auto, Speicher, Boiler), nicht ein Modell. Alles skaliert
/// verlustfrei, folgt Hell/Dunkel und bleibt bei "Bewegung reduzieren" still.

// MARK: - Auto

/// Seitenansicht; der Ladestand füllt die Karosserie von hinten nach vorn.
struct CarArt: View {
    /// Ladestand 0…100 oder `nil`, wenn das Auto ihn nicht meldet.
    var soc: Double?
    var state: CarState
    /// Ladegrenze (Strich auf der Füllung).
    var limit: Double?

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var present: Bool { state != .away && state != .none }
    private var charging: Bool { state == .charging }

    var body: some View {
        GeometryReader { geo in
            let rect = CGRect(origin: .zero, size: geo.size)
            ZStack {
                // Bodenschatten
                Ellipse()
                    .fill(.primary.opacity(0.08))
                    .frame(width: rect.width * 0.82, height: rect.height * 0.07)
                    .position(x: rect.midX, y: rect.height * 0.86)

                if state.isPlugged { CableShape().stroke(cableStyle, style: StrokeStyle(lineWidth: 2.5, lineCap: .round)) }

                CarBody()
                    .fill(Color(uiColor: .tertiarySystemFill))
                if let soc, present {
                    CarFill(fraction: soc / 100, charging: charging && !reduceMotion)
                        .mask(CarBody())
                    if let limit, limit < 100 {
                        LimitMark(fraction: limit / 100).mask(CarBody())
                    }
                }
                CarWindows()
                    .fill(Color(uiColor: .systemBackground).opacity(0.75))
                CarBody()
                    .stroke(present ? Palette.car.fill : .secondary,
                            style: StrokeStyle(lineWidth: 2, lineJoin: .round, dash: present ? [] : [6, 5]))
                Wheels()
                    .fill(present ? Color.primary.opacity(0.75) : Color.secondary.opacity(0.4))
                if charging {
                    Image(systemName: "bolt.fill")
                        .font(.system(size: rect.height * 0.2, weight: .bold))
                        .foregroundStyle(.white)
                        .shadow(color: Palette.car.fill.opacity(0.6), radius: 4)
                        .position(x: rect.width * 0.47, y: rect.height * 0.52)
                        .symbolEffect(.pulse, options: .repeating, isActive: !reduceMotion)
                }
            }
            .opacity(present ? 1 : 0.55)
        }
        .aspectRatio(2, contentMode: .fit)
        .accessibilityHidden(true)
    }

    private var cableStyle: Color { charging ? Palette.car.fill : Color.secondary.opacity(0.6) }
}

/// Maße des Fahrzeugs (480 × 240), damit Rad, Radlauf und Schweller zusammenpassen.
private enum CarGeometry {
    static let size = CGSize(width: 480, height: 240)
    static let arch: CGFloat = 46

    static func transform(_ rect: CGRect) -> (CGFloat, CGFloat, CGFloat) {
        let s = min(rect.width / size.width, rect.height / size.height)
        return (s, (rect.width - size.width * s) / 2, (rect.height - size.height * s) / 2)
    }
}

private struct CarBody: Shape {
    func path(in rect: CGRect) -> Path {
        let (s, dx, dy) = CarGeometry.transform(rect)
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: rect.minX + dx + x * s, y: rect.minY + dy + y * s) }
        var path = Path()
        path.move(to: p(24, 162))
        path.addCurve(to: p(52, 118), control1: p(24, 140), control2: p(32, 126))
        path.addLine(to: p(114, 102))
        path.addCurve(to: p(142, 86), control1: p(126, 99), control2: p(134, 94))
        path.addLine(to: p(178, 56))
        path.addCurve(to: p(210, 46), control1: p(187, 49), control2: p(197, 46))
        path.addLine(to: p(288, 46))
        path.addCurve(to: p(341, 64), control1: p(313, 46), control2: p(329, 52))
        path.addLine(to: p(381, 100))
        path.addLine(to: p(429, 110))
        path.addCurve(to: p(456, 146), control1: p(449, 115), control2: p(456, 128))
        path.addLine(to: p(456, 164))
        path.addCurve(to: p(447, 172), control1: p(456, 169), control2: p(452, 172))
        path.addLine(to: p(398, 172))
        path.addArc(center: p(352, 172), radius: CarGeometry.arch * s, startAngle: .degrees(0), endAngle: .degrees(180), clockwise: true)
        path.addLine(to: p(174, 172))
        path.addArc(center: p(128, 172), radius: CarGeometry.arch * s, startAngle: .degrees(0), endAngle: .degrees(180), clockwise: true)
        path.addLine(to: p(33, 172))
        path.addCurve(to: p(24, 163), control1: p(27, 172), control2: p(24, 169))
        path.closeSubpath()
        return path
    }
}

private struct CarWindows: Shape {
    func path(in rect: CGRect) -> Path {
        let (s, dx, dy) = CarGeometry.transform(rect)
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: rect.minX + dx + x * s, y: rect.minY + dy + y * s) }
        var path = Path()
        // Vordere Scheibe … Heckscheibe, getrennt durch die B-Säule.
        path.move(to: p(158, 96))
        path.addLine(to: p(188, 64))
        path.addCurve(to: p(212, 57), control1: p(194, 59), control2: p(202, 57))
        path.addLine(to: p(246, 57))
        path.addLine(to: p(246, 96))
        path.closeSubpath()
        path.move(to: p(258, 96))
        path.addLine(to: p(258, 57))
        path.addLine(to: p(286, 57))
        path.addCurve(to: p(330, 72), control1: p(306, 57), control2: p(320, 62))
        path.addLine(to: p(358, 96))
        path.closeSubpath()
        return path
    }
}

private struct Wheels: Shape {
    func path(in rect: CGRect) -> Path {
        let (s, dx, dy) = CarGeometry.transform(rect)
        var path = Path()
        for x in [128.0, 352.0] {
            let c = CGPoint(x: rect.minX + dx + x * s, y: rect.minY + dy + 172 * s)
            path.addEllipse(in: CGRect(x: c.x - 34 * s, y: c.y - 34 * s, width: 68 * s, height: 68 * s))
        }
        return path
    }
}

/// Ladekabel vom Heck zur (gedachten) Wallbox links.
private struct CableShape: Shape {
    func path(in rect: CGRect) -> Path {
        let (s, dx, dy) = CarGeometry.transform(rect)
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: rect.minX + dx + x * s, y: rect.minY + dy + y * s) }
        var path = Path()
        path.move(to: p(30, 132))
        path.addCurve(to: p(-10, 120), control1: p(6, 200), control2: p(-14, 170))
        return path
    }
}

/// Füllung der Karosserie; beim Laden wandert ein heller Schimmer durch.
private struct CarFill: View {
    var fraction: Double
    var charging: Bool

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width * min(1, max(0, fraction))
            ZStack(alignment: .leading) {
                Rectangle()
                    .fill(LinearGradient(colors: [Palette.car.fill.opacity(0.55), Palette.car.fill],
                                         startPoint: .bottom, endPoint: .top))
                    .frame(width: width)
                if charging {
                    TimelineView(.animation(minimumInterval: 1 / 30)) { timeline in
                        let t = timeline.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 2.2) / 2.2
                        LinearGradient(colors: [.clear, .white.opacity(0.45), .clear], startPoint: .leading, endPoint: .trailing)
                            .frame(width: geo.size.width * 0.18)
                            .offset(x: -geo.size.width * 0.18 + (width + geo.size.width * 0.18) * t)
                    }
                    .frame(width: width, alignment: .leading)
                    .clipped()
                }
            }
            .animation(.smooth(duration: 0.8), value: fraction)
        }
    }
}

private struct LimitMark: View {
    var fraction: Double
    var body: some View {
        GeometryReader { geo in
            Rectangle()
                .fill(.primary.opacity(0.55))
                .frame(width: 2)
                .offset(x: geo.size.width * fraction)
        }
    }
}

// MARK: - Warmwasser

/// Speicher mit "Wärmepegel": Höhe und Farbe folgen der Temperatur
/// (kalt blau → warm violett → heiß rot), gestrichelte Linie = Ziel.
struct BoilerArt: View {
    var temperature: Double?
    var target: Double?
    var heating: Bool
    var boost: Bool = false

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static let range: ClosedRange<Double> = 15...75

    private func level(_ celsius: Double?) -> Double {
        guard let celsius else { return 0 }
        return min(1, max(0.04, (celsius - Self.range.lowerBound) / (Self.range.upperBound - Self.range.lowerBound)))
    }

    var body: some View {
        GeometryReader { geo in
            let shape = RoundedRectangle(cornerRadius: geo.size.width * 0.36, style: .continuous)
            ZStack(alignment: .bottom) {
                shape.fill(Color(uiColor: .tertiarySystemFill))
                if temperature != nil {
                    Rectangle()
                        .fill(LinearGradient(colors: [Self.color(for: (temperature ?? 40) - 12), Self.color(for: temperature ?? 40)],
                                             startPoint: .bottom, endPoint: .top))
                        .frame(height: geo.size.height * level(temperature))
                        .animation(.smooth(duration: 0.8), value: temperature)
                    if heating && !reduceMotion {
                        Bubbles().frame(height: geo.size.height * level(temperature))
                    }
                }
                if let target {
                    Rectangle()
                        .fill(.clear)
                        .frame(height: 1.5)
                        .overlay(Line().stroke(.primary.opacity(0.5), style: StrokeStyle(lineWidth: 1.5, dash: [4, 4])))
                        .padding(.bottom, geo.size.height * level(target))
                }
                if heating || boost {
                    Image(systemName: boost ? "flame.fill" : "bolt.fill")
                        .font(.system(size: geo.size.width * 0.28, weight: .bold))
                        .foregroundStyle(.white)
                        .padding(.bottom, geo.size.height * 0.08)
                        .symbolEffect(.pulse, options: .repeating, isActive: !reduceMotion)
                }
            }
            .clipShape(shape)
            .overlay(shape.strokeBorder(Palette.water.fill.opacity(0.6), lineWidth: 2))
        }
        .aspectRatio(0.56, contentMode: .fit)
        .accessibilityHidden(true)
    }

    /// Farbe nach Temperatur wie im Web (--water-cold/-warm/-hot).
    static func color(for celsius: Double) -> Color {
        switch celsius {
        case ..<35: Color(light: 0x3B82F6, dark: 0x60A5FA)
        case ..<52: Color(light: 0xA855F7, dark: 0xC084FC)
        default: Color(light: 0xE5484D, dark: 0xF87171)
        }
    }
}

private struct Line: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX + 6, y: rect.midY))
        path.addLine(to: CGPoint(x: rect.maxX - 6, y: rect.midY))
        return path
    }
}

/// Aufsteigende Bläschen beim Heizen.
private struct Bubbles: View {
    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 24)) { timeline in
            Canvas { context, size in
                let time = timeline.date.timeIntervalSinceReferenceDate
                for i in 0..<7 {
                    let seed = Double(i) * 0.137
                    let t = (time * (0.22 + seed * 0.3) + seed * 3).truncatingRemainder(dividingBy: 1)
                    let x = size.width * (0.2 + 0.6 * ((seed * 7).truncatingRemainder(dividingBy: 1)))
                    let y = size.height * (1 - t)
                    let r = 1.5 + 2 * (1 - t)
                    context.fill(Path(ellipseIn: CGRect(x: x - r, y: y - r, width: r * 2, height: r * 2)),
                                 with: .color(.white.opacity(0.55 * (1 - t))))
                }
            }
        }
    }
}

// MARK: - Batterie

/// Stehender Speicher; Füllung = Ladestand, Pfeil = Richtung.
struct BatteryArt: View {
    var soc: Double?
    /// > 0 lädt, < 0 entlädt (W).
    var power: Double
    var reserve: Double?

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var fillColor: Color {
        guard let soc else { return .secondary }
        return soc < 15 ? Palette.error.fill : Palette.battery.fill
    }

    var body: some View {
        GeometryReader { geo in
            let cap = CGSize(width: geo.size.width * 0.38, height: geo.size.height * 0.06)
            let bodyHeight = geo.size.height - cap.height
            let shape = RoundedRectangle(cornerRadius: geo.size.width * 0.18, style: .continuous)
            VStack(spacing: 0) {
                RoundedRectangle(cornerRadius: 3).fill(Palette.battery.fill.opacity(0.6))
                    .frame(width: cap.width, height: cap.height)
                ZStack(alignment: .bottom) {
                    shape.fill(Color(uiColor: .tertiarySystemFill))
                    Rectangle()
                        .fill(LinearGradient(colors: [fillColor.opacity(0.75), fillColor], startPoint: .bottom, endPoint: .top))
                        .frame(height: bodyHeight * min(1, max(0, (soc ?? 0) / 100)))
                        .animation(.smooth(duration: 0.8), value: soc)
                    if let reserve, reserve > 0 {
                        Rectangle().fill(.primary.opacity(0.45)).frame(height: 1.5)
                            .padding(.horizontal, geo.size.width * 0.12)
                            .padding(.bottom, bodyHeight * reserve / 100)
                    }
                    if abs(power) >= EnergyFlow.threshold {
                        Image(systemName: power > 0 ? "arrow.up" : "arrow.down")
                            .font(.system(size: geo.size.width * 0.34, weight: .heavy))
                            .foregroundStyle(.white)
                            .shadow(color: .black.opacity(0.2), radius: 2)
                            .frame(maxHeight: .infinity)
                            .symbolEffect(.pulse, options: .repeating, isActive: !reduceMotion)
                    }
                }
                .clipShape(shape)
                .overlay(shape.strokeBorder(Palette.battery.fill.opacity(0.7), lineWidth: 2))
                .frame(height: bodyHeight)
            }
        }
        .aspectRatio(0.5, contentMode: .fit)
        .accessibilityHidden(true)
    }
}
