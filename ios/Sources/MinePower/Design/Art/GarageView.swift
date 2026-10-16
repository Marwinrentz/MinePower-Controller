import MinePowerKit
import SwiftUI

/// Garage mit Auto – Portierung von `frontend/src/components/Garage.tsx`.
///
/// Gleiche Pfade (400 × 236), gleiche Farben, gleiche Bewegung:
/// - lädt: Kabel gesteckt, Energie läuft durchs Kabel, Ring an der Ladeklappe
///   pulsiert, die Wallbox-LED leuchtet grün.
/// - wartet: Auto steht ruhig, Kabel gesteckt, LED und Klappe in Autofarbe.
/// - schläft: Auto gedimmt.
/// - weg/unbekannt: Das Auto fährt beim Wechsel durchs offene Tor hinaus,
///   danach bleibt ein gestrichelter Umriss (bei "unbekannt" mit Fragezeichen).
/// "Bewegung reduzieren": kein Fahren, kein Fluss, Zustand bleibt erkennbar.
struct GarageView: View {
    enum Look: Equatable { case charging, waiting, asleep, away, unknown }

    var look: Look
    var soc: Double?
    var label: String

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var motion: Motion?
    @State private var shownPresent: Bool?

    private struct Motion: Equatable {
        var leaving: Bool
        var start: Date
    }

    static let viewBox = CGSize(width: 400, height: 236)
    static let driveDuration: TimeInterval = 1.6

    private var present: Bool { look == .charging || look == .waiting || look == .asleep }

    var body: some View {
        let animating = !reduceMotion && (look == .charging || look == .waiting || motion != nil)
        TimelineView(.animation(minimumInterval: 1 / 30, paused: !animating)) { timeline in
            Canvas { context, size in
                let scale = min(size.width / Self.viewBox.width, size.height / Self.viewBox.height)
                context.translateBy(x: (size.width - Self.viewBox.width * scale) / 2,
                                    y: (size.height - Self.viewBox.height * scale) / 2)
                context.scaleBy(x: scale, y: scale)
                draw(in: &context, time: timeline.date)
            }
        }
        .aspectRatio(Self.viewBox.width / Self.viewBox.height, contentMode: .fit)
        .frame(maxHeight: 300)
        .onAppear { shownPresent = present }
        .onChange(of: present) { _, now in
            guard shownPresent != nil, shownPresent != now else { return }
            shownPresent = now
            guard !reduceMotion else { return }
            motion = Motion(leaving: !now, start: .now)
            Task {
                try? await Task.sleep(for: .seconds(Self.driveDuration))
                motion = nil
            }
        }
        .accessibilityElement()
        .accessibilityLabel(Text(label))
        .accessibilityAddTraits(.isImage)
    }

    // MARK: - Zeichnen

    private func draw(in ctx: inout GraphicsContext, time: Date) {
        let t = time.timeIntervalSinceReferenceDate
        let charging = look == .charging
        let waiting = look == .waiting
        let asleep = look == .asleep

        // Bewegung des Autos (drive-in / drive-out)
        var carOffset: Double = 0
        var moving = false
        if let motion {
            let p = min(1, time.timeIntervalSince(motion.start) / Self.driveDuration)
            moving = p < 1
            if motion.leaving {
                // 0 → kurz zurück (-5) → hinaus (330), Kurve wie --ease
                carOffset = p < 0.12 ? -5 * (p / 0.12) : -5 + 335 * Self.ease((p - 0.12) / 0.88)
            } else {
                carOffset = 330 * (1 - Self.easeOut(p))
            }
        }
        let carVisible = motion != nil || present
        let plugged = present && motion == nil

        // Garage: Innenraum, Lichtkegel, Lampe, Dach, Wände, Boden
        ctx.fill(path("M30 98 L200 40 L370 98 V208 H30 Z"),
                 with: .linearGradient(Gradient(colors: [Web.garageBg.opacity(0.55), Web.garageBg]),
                                       startPoint: CGPoint(x: 0, y: 40), endPoint: CGPoint(x: 0, y: 208)))
        var cone = ctx
        cone.opacity = look == .away || look == .unknown ? 0.35 : asleep ? 0.25 : 0.9
        cone.fill(path("M192 66 L208 66 L262 208 L138 208 Z"),
                  with: .linearGradient(Gradient(colors: [Web.garageLight, Web.garageLight.opacity(0)]),
                                        startPoint: CGPoint(x: 0, y: 66), endPoint: CGPoint(x: 0, y: 208)))
        ctx.fill(path("M188 60 h24 l-4 7 h-16 Z"), with: .color(Web.artLine))
        ctx.stroke(path("M18 104 L200 40 L382 104"), with: .color(Web.artLine),
                   style: StrokeStyle(lineWidth: 7, lineCap: .round, lineJoin: .round))
        var wall = ctx
        wall.opacity = 0.55
        wall.stroke(path("M30 102 V208 M370 102 V122"), with: .color(Web.artLine), style: StrokeStyle(lineWidth: 4, lineCap: .round))
        var floor = ctx
        floor.opacity = 0.8
        floor.fill(Path(roundedRect: CGRect(x: 8, y: 207, width: 384, height: 5), cornerRadius: 2.5), with: .color(Web.artLine))

        // Freier Stellplatz
        if !carVisible {
            let ghost = StrokeStyle(lineWidth: 2, dash: [5, 6])
            ctx.stroke(path(Self.body), with: .color(Web.lineStrong), style: ghost)
            for x in Self.wheels {
                ctx.stroke(Path(ellipseIn: CGRect(x: x - 19, y: 171, width: 38, height: 38)), with: .color(Web.lineStrong), style: ghost)
            }
            if look == .unknown {
                var q = ctx
                q.opacity = 0.6
                q.draw(Text("?").font(.system(size: 56, weight: .heavy)).foregroundStyle(Web.textDim),
                       at: CGPoint(x: 200, y: 150))
            }
        }

        // Wallbox mit Lichtleiste
        ctx.fill(Path(roundedRect: CGRect(x: 40, y: 120, width: 26, height: 46), cornerRadius: 9), with: .color(Web.surface))
        ctx.stroke(Path(roundedRect: CGRect(x: 40, y: 120, width: 26, height: 46), cornerRadius: 9),
                   with: .color(Web.artLine), lineWidth: 2)
        var led = ctx
        let ledColor: Color
        if charging {
            ledColor = Palette.pv.fill
            led.opacity = reduceMotion ? 1 : 0.7 + 0.3 * cos(t * 2 * .pi / 1.6)
            led.addFilter(.shadow(color: Palette.pv.fill, radius: 3))
        } else if waiting {
            ledColor = Palette.car.fill
            led.opacity = 0.7
        } else {
            ledColor = Web.lineStrong
        }
        led.fill(Path(roundedRect: CGRect(x: 51, y: 128, width: 4, height: 22), cornerRadius: 2), with: .color(ledColor))

        // Kabel
        if plugged {
            let cable = path("M53 166 C 52 206, 92 210, 97 168")
            ctx.stroke(cable, with: .color(Web.artTyre), style: StrokeStyle(lineWidth: 4.5, lineCap: .round))
            if charging {
                let phase = reduceMotion ? 0 : 24 - (t.truncatingRemainder(dividingBy: 0.9) / 0.9) * 24
                ctx.stroke(cable, with: .color(Palette.pv.fill),
                           style: StrokeStyle(lineWidth: 3, lineCap: .round, dash: [3, 9], dashPhase: phase))
            }
        }

        // Auto
        guard carVisible else { return }
        var car = ctx
        // Erst am Garagenrand beschneiden, dann verschieben (wie clipPath auf der äußeren Gruppe).
        car.clip(to: Path(CGRect(origin: .zero, size: Self.viewBox)))
        car.translateBy(x: carOffset, y: 0)
        if asleep { car.opacity = 0.55 }
        if let motion, motion.leaving, reduceMotion { car.opacity = 0 }

        car.fill(Path(ellipseIn: CGRect(x: 72, y: 201, width: 252, height: 14)),
                 with: .radialGradient(Gradient(colors: [.black.opacity(0.32), .black.opacity(0)]),
                                       center: CGPoint(x: 198, y: 208), startRadius: 0, endRadius: 126))
        let bodyPath = path(Self.body)
        car.fill(bodyPath, with: .linearGradient(
            Gradient(stops: [.init(color: Web.carBodyHi, location: 0), .init(color: Web.carBody, location: 0.55),
                             .init(color: Web.carBody, location: 1)]),
            startPoint: CGPoint(x: 0, y: 127), endPoint: CGPoint(x: 0, y: 222)))
        car.stroke(bodyPath, with: .color(Web.artLine), style: StrokeStyle(lineWidth: 2, lineJoin: .round))
        let glass = path(Self.glass)
        car.fill(glass, with: .linearGradient(Gradient(colors: [Web.artGlassA, Web.artGlassB]),
                                              startPoint: CGPoint(x: 107, y: 134), endPoint: CGPoint(x: 251, y: 156)))
        car.stroke(glass, with: .color(Web.artLine), style: StrokeStyle(lineWidth: 1.5, lineJoin: .round))
        car.stroke(path("M190 134 L187 156"), with: .color(Web.carBody), style: StrokeStyle(lineWidth: 5, lineCap: .round))
        var seam = car
        seam.opacity = 0.45
        seam.stroke(path("M238 157 L237 196 M187 157 L187 197"), with: .color(Web.artLine), lineWidth: 1.2)
        var handle = car
        handle.opacity = 0.7
        handle.stroke(path("M224 163 h9 M174 163 h9"), with: .color(Web.artLine), style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
        let mirror = path("M247 150 l10 -2 l2 6 l-10 1.5 Z")
        car.fill(mirror, with: .color(Web.carBody))
        car.stroke(mirror, with: .color(Web.artLine), style: StrokeStyle(lineWidth: 1.5, lineJoin: .round))
        var sill = car
        sill.opacity = 0.55
        sill.stroke(path("M152 197 H244"), with: .color(Web.artTyre), lineWidth: 3)
        car.stroke(path("M87 162 L97 159"), with: .color(Color(rgb: 0xE5484D)), style: StrokeStyle(lineWidth: 4, lineCap: .round))
        car.stroke(path("M297 162 L312 168"), with: .color(Color(rgb: 0xF6D365)), style: StrokeStyle(lineWidth: 3.5, lineCap: .round))

        // Ladeklappe und Leuchtring
        let portColor = charging ? Palette.pv.fill : waiting ? Palette.car.fill : Web.artLine
        car.fill(Path(ellipseIn: CGRect(x: 95.6, y: 164.6, width: 6.8, height: 6.8)), with: .color(portColor))
        if plugged && (charging || waiting) {
            var glow = car
            glow.opacity = waiting || reduceMotion ? 0.4 : 0.65 + 0.35 * cos(t * 2 * .pi / 1.8)
            glow.fill(Path(ellipseIn: CGRect(x: 83, y: 152, width: 32, height: 32)),
                      with: .radialGradient(Gradient(colors: [Palette.car.fill.opacity(0.9), Palette.car.fill.opacity(0)]),
                                            center: CGPoint(x: 99, y: 168), startRadius: 0, endRadius: 16))
        }

        // Räder (drehen beim Fahren)
        for x in Self.wheels {
            var wheel = car
            if moving {
                let angle = Angle.degrees((t.truncatingRemainder(dividingBy: 0.45) / 0.45) * 360)
                wheel.translateBy(x: x, y: 190)
                wheel.rotate(by: angle)
                wheel.translateBy(x: -x, y: -190)
            }
            wheel.fill(Path(ellipseIn: CGRect(x: x - 19, y: 171, width: 38, height: 38)), with: .color(Web.artTyre))
            let rim = Path(ellipseIn: CGRect(x: x - 13, y: 177, width: 26, height: 26))
            wheel.fill(rim, with: .color(Web.artMetal))
            wheel.stroke(rim, with: .color(Web.artLine), lineWidth: 1)
            wheel.stroke(Self.spokes(x, 190), with: .color(Web.artLine), style: StrokeStyle(lineWidth: 2.4, lineCap: .round))
            wheel.fill(Path(ellipseIn: CGRect(x: x - 3.5, y: 186.5, width: 7, height: 7)), with: .color(Web.artLine))
        }

        // Ladestand-Plakette
        if let soc, motion == nil, present {
            let pill = Path(roundedRect: CGRect(x: 172, y: 98, width: 56, height: 22), cornerRadius: 11)
            car.fill(pill, with: .color(Web.surface))
            car.stroke(pill, with: .color(charging ? Palette.pv.fill : Palette.car.fill), lineWidth: 1.5)
            car.draw(Text("\(Int(soc.rounded())) %").font(.custom(Typo.Weight.extraBold.rawValue, size: 12.5))
                        .foregroundStyle(Web.textStrong),
                     at: CGPoint(x: 200, y: 109))
        }
    }

    // MARK: - Geometrie aus Garage.tsx

    static let body = "M94 197 L88 190 C85 184 84 174 86 166 L91 153 C112 140 152 128 198 127 C216 127 229 130 240 137 L263 152 C282 155 298 159 307 164 C313 168 316 174 316 182 L315 192 C315 195 313 197 309 197 L292 197 A25 25 0 1 0 244 197 L152 197 A25 25 0 1 0 104 197 Z"
    static let glass = "M107 152 C128 141 160 134 197 134 C213 134 225 136 234 142 L251 155 L114 156 Z"
    static let wheels: [Double] = [128, 268]

    static func spokes(_ cx: Double, _ cy: Double) -> Path {
        var p = Path()
        for i in 0..<5 {
            let a = Double(i * 72 - 90) * .pi / 180
            p.move(to: CGPoint(x: cx + cos(a) * 5.5, y: cy + sin(a) * 5.5))
            p.addLine(to: CGPoint(x: cx + cos(a) * 11.5, y: cy + sin(a) * 11.5))
        }
        return p
    }

    /// Pfad im Zeichenraster (ohne Skalierung; skaliert wird der Kontext).
    private func path(_ data: String) -> Path {
        SVGShape(data, viewBox: CGRect(origin: .zero, size: Self.viewBox), fit: false)
            .path(in: CGRect(origin: .zero, size: Self.viewBox))
    }

    /// `--ease: cubic-bezier(0.32, 0.72, 0, 1)` und `--ease-out` grob nachgebildet.
    static func ease(_ x: Double) -> Double { 1 - pow(1 - min(1, max(0, x)), 3) }
    static func easeOut(_ x: Double) -> Double { 1 - pow(1 - min(1, max(0, x)), 4) }
}

extension GarageView.Look {
    /// Wie `Car.tsx`: unterwegs und nicht angesteckt = weg, keine Verbindung = unbekannt.
    init(_ state: CarState) {
        switch state {
        case .charging: self = .charging
        case .asleep: self = .asleep
        case .waiting, .complete: self = .waiting
        case .away, .unplugged: self = .away
        default: self = .unknown
        }
    }
}
