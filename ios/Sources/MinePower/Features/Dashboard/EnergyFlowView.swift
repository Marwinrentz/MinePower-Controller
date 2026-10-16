import MinePowerKit
import SwiftUI

/// Energiefluss als Stern um das Haus.
///
/// Jede Leitung zeigt Richtung und Stärke ohne Lesen: Punkte wandern in
/// Flussrichtung, schneller und dichter bei mehr Leistung; die Linie wird
/// breiter. Ruhige Leitungen sind dünn und gestrichelt. Mit "Bewegung
/// reduzieren" stehen die Punkte still und ein Pfeil zeigt die Richtung.
struct EnergyFlowView: View {
    let flow: EnergyFlow
    var compact = false

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isVisible = false

    var body: some View {
        GeometryReader { geo in
            let layout = FlowLayout(size: geo.size, compact: compact, nodes: flow.branches.map(\.node))
            ZStack {
                TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion || !isVisible)) { timeline in
                    Canvas { context, _ in
                        let time = timeline.date.timeIntervalSinceReferenceDate
                        for branch in flow.branches {
                            guard let point = layout.position[branch.node] else { continue }
                            draw(branch, from: point, to: layout.center, layout: layout, time: time, in: &context)
                        }
                    }
                }
                HouseNode(watts: flow.houseWatts, size: layout.houseRadius * 2)
                    .position(layout.center)
                ForEach(flow.branches) { branch in
                    if let point = layout.position[branch.node] {
                        FlowNode(branch: branch, size: layout.nodeRadius * 2, compact: compact)
                            .position(point)
                    }
                }
            }
        }
        .aspectRatio(compact ? 1.25 : 1.05, contentMode: .fit)
        .onAppear { isVisible = true }
        .onDisappear { isVisible = false }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("Energiefluss"))
        .accessibilityValue(Text(flow.accessibilitySummary()))
    }

    // MARK: - Zeichnen

    private func draw(_ branch: EnergyFlow.Branch, from node: CGPoint, to house: CGPoint, layout: FlowLayout,
                      time: TimeInterval, in context: inout GraphicsContext) {
        let dx = house.x - node.x, dy = house.y - node.y
        let length = max(1, hypot(dx, dy))
        let ux = dx / length, uy = dy / length
        // Linie zwischen den Kreisrändern.
        let a = CGPoint(x: node.x + ux * (layout.nodeRadius + 6), y: node.y + uy * (layout.nodeRadius + 6))
        let b = CGPoint(x: house.x - ux * (layout.houseRadius + 6), y: house.y - uy * (layout.houseRadius + 6))
        var line = Path()
        line.move(to: a)
        line.addLine(to: b)
        let color = branch.domain.fill

        guard branch.direction != .idle else {
            context.stroke(line, with: .color(.secondary.opacity(0.35)),
                           style: StrokeStyle(lineWidth: 1.5, lineCap: .round, dash: [3, 6]))
            return
        }

        let intensity = branch.intensity
        context.stroke(line, with: .color(color.opacity(0.28)),
                       style: StrokeStyle(lineWidth: 2 + 5 * intensity, lineCap: .round))

        let (start, end) = branch.direction == .toHouse ? (a, b) : (b, a)
        let segment = CGPoint(x: end.x - start.x, y: end.y - start.y)

        if reduceMotion {
            // Stillstand: ein Pfeil in der Mitte zeigt die Richtung.
            let mid = CGPoint(x: start.x + segment.x * 0.5, y: start.y + segment.y * 0.5)
            let angle = atan2(segment.y, segment.x)
            let size = 6 + 4 * intensity
            var arrow = Path()
            arrow.move(to: CGPoint(x: mid.x + cos(angle) * size, y: mid.y + sin(angle) * size))
            arrow.addLine(to: CGPoint(x: mid.x + cos(angle + 2.5) * size, y: mid.y + sin(angle + 2.5) * size))
            arrow.addLine(to: CGPoint(x: mid.x + cos(angle - 2.5) * size, y: mid.y + sin(angle - 2.5) * size))
            arrow.closeSubpath()
            context.fill(arrow, with: .color(color))
            return
        }

        let count = 2 + Int((intensity * 3).rounded())
        let speed = 0.25 + 0.9 * intensity
        let radius = 2.5 + 2.5 * intensity
        for i in 0..<count {
            let t = (time * speed + Double(i) / Double(count)).truncatingRemainder(dividingBy: 1)
            let point = CGPoint(x: start.x + segment.x * t, y: start.y + segment.y * t)
            // Weich ein- und ausblenden an den Enden.
            let fade = min(1, min(t, 1 - t) * 6)
            context.fill(Path(ellipseIn: CGRect(x: point.x - radius, y: point.y - radius, width: radius * 2, height: radius * 2)),
                         with: .color(color.opacity(fade)))
        }
    }
}

/// Positionen der Knoten im verfügbaren Platz.
private struct FlowLayout {
    let center: CGPoint
    let position: [EnergyFlow.Node: CGPoint]
    let nodeRadius: CGFloat
    let houseRadius: CGFloat

    init(size: CGSize, compact: Bool, nodes: [EnergyFlow.Node]) {
        let side = min(size.width, size.height * (compact ? 1.25 : 1.05))
        nodeRadius = max(22, side * 0.075)
        houseRadius = max(32, side * 0.11)
        let w = size.width, h = size.height
        let inset = nodeRadius + 4
        center = CGPoint(x: w / 2, y: h * 0.48)
        var map: [EnergyFlow.Node: CGPoint] = [:]
        let placement: [EnergyFlow.Node: CGPoint] = [
            .pv: CGPoint(x: w / 2, y: inset),
            .grid: CGPoint(x: inset + 6, y: h * 0.48),
            .battery: CGPoint(x: w - inset - 6, y: h * 0.48),
            .car: CGPoint(x: w * 0.24, y: h - inset - 14),
            .water: CGPoint(x: w * 0.76, y: h - inset - 14),
        ]
        for node in nodes { map[node] = placement[node] }
        position = map
    }
}

private struct HouseNode: View {
    let watts: Double
    let size: CGFloat

    var body: some View {
        VStack(spacing: 2) {
            Image(systemName: "house.fill")
                .font(.system(size: size * 0.3, weight: .semibold))
                .foregroundStyle(Palette.house.fill)
            Text(Fmt().watts(watts))
                .font(Typo.caption.weight(.semibold))
                .monospacedDigit()
                .contentTransition(.numericText())
                .animation(.snappy, value: watts)
        }
        .frame(width: size, height: size)
        .background(Color(uiColor: .secondarySystemGroupedBackground), in: .circle)
        .overlay(Circle().strokeBorder(Palette.house.fill.opacity(0.35), lineWidth: 1.5))
    }
}

private struct FlowNode: View {
    let branch: EnergyFlow.Branch
    let size: CGFloat
    var compact: Bool

    var body: some View {
        // Sonne steht oben: Beschriftung daneben, sonst läge sie auf der Leitung zum Haus.
        if branch.node == .pv {
            icon.overlay(alignment: .leading) {
                labels(alignment: .leading)
                    .fixedSize()
                    .offset(x: size + 8)
            }
        } else {
            VStack(spacing: 3) { icon; labels(alignment: .center) }
                .fixedSize()
        }
    }

    private var active: Bool { branch.direction != .idle }

    private var icon: some View {
        Image(systemName: branch.node.symbol)
            .font(.system(size: size * 0.42, weight: .semibold))
            .foregroundStyle(active ? branch.domain.fill : .secondary)
            .frame(width: size, height: size)
            .background(active ? branch.domain.soft : Color(uiColor: .tertiarySystemFill), in: .circle)
            .overlay(Circle().strokeBorder(active ? branch.domain.fill.opacity(0.5) : .clear, lineWidth: 1.5))
    }

    private func labels(alignment: HorizontalAlignment) -> some View {
        VStack(alignment: alignment, spacing: 1) {
            Text(active ? Fmt().watts(branch.watts) : "–")
                .font(Typo.caption.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(active ? .primary : .secondary)
                .contentTransition(.numericText())
                .animation(.snappy, value: branch.watts)
            if !compact, let caption = caption {
                Text(caption).font(Typo.caption2).foregroundStyle(.secondary)
            }
        }
    }

    private var caption: LocalizedStringKey? {
        switch (branch.node, branch.direction) {
        case (.grid, .toHouse): "Bezug"
        case (.grid, .fromHouse): "Einspeisung"
        case (.battery, .toHouse): "entlädt"
        case (.battery, .fromHouse): "lädt"
        default: nil
        }
    }
}

extension EnergyFlow.Node {
    var symbol: String {
        switch self {
        case .pv: "sun.max.fill"
        case .grid: "bolt.horizontal.fill"
        case .battery: "battery.75percent"
        case .car: "car.fill"
        case .water: "drop.fill"
        }
    }

    var domain: DomainColor {
        switch self {
        case .pv: Palette.pv
        case .grid: Palette.gridExport
        case .battery: Palette.battery
        case .car: Palette.car
        case .water: Palette.water
        }
    }
}

extension EnergyFlow.Branch {
    /// Netz: Bezug rot, Einspeisung blau – wie überall in der App.
    var domain: DomainColor {
        node == .grid && direction == .toHouse ? Palette.gridImport : node.domain
    }
}
