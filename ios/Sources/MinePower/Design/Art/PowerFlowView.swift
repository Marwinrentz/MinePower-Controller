import MinePowerKit
import SwiftUI

/// Energiefluss – Portierung von `frontend/src/components/PowerFlow.tsx`.
///
/// Sechs Knoten im 3×3-Raster um das Haus: Sonne und Netz oben, Batterie,
/// Auto und Warmwasser unten. Leitungen leuchten nur, wenn Strom fließt; die
/// Punkte laufen in Flussrichtung, je mehr Leistung, desto schneller.
/// "Bewegung reduzieren": Punkte stehen, die Richtung zeigt die Unterzeile.
struct PowerFlowView: View {
    let snapshot: Snapshot
    var onSelect: ((AppSection) -> Void)?

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var visible = false

    enum Key: CaseIterable { case pv, grid, battery, car, water }

    struct Node: Identifiable {
        var key: Key
        var watts: Double
        /// +1 zum Haus, -1 vom Haus weg, 0 ruhig
        var direction: Int
        var value: String
        var sub: String
        var sub2: String?
        var absent = false
        var alert = false
        var id: Key { key }
    }

    var body: some View {
        let nodes = Self.nodes(snapshot)
        GeometryReader { geo in
            let size = geo.size
            ZStack {
                TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion || !visible)) { timeline in
                    Canvas { context, _ in
                        let t = timeline.date.timeIntervalSinceReferenceDate
                        let hub = Self.center(col: 2, row: 2, in: size)
                        for node in nodes {
                            let c = Self.center(of: node.key, in: size)
                            var line = Path()
                            line.move(to: c)
                            line.addLine(to: hub)
                            let tone = Self.tone(node)
                            if node.direction == 0 {
                                var track = context
                                track.opacity = 0.6
                                track.stroke(line, with: .color(Web.lineStrong), style: StrokeStyle(lineWidth: 3, lineCap: .round))
                            } else {
                                var track = context
                                track.opacity = 0.35
                                track.stroke(line, with: .color(tone.fill), style: StrokeStyle(lineWidth: 3, lineCap: .round))
                                // Punkte: Strich 2, Lücke 14, Breite 6; 32 je Durchlauf.
                                let duration = Self.duration(node.watts)
                                let progress = reduceMotion ? 0 : (t.truncatingRemainder(dividingBy: duration) / duration) * 32
                                // Pfad läuft vom Knoten zum Haus; "zum Haus" läuft vorwärts.
                                let phase = node.direction > 0 ? 32 - progress : progress
                                context.stroke(line, with: .color(tone.fill),
                                               style: StrokeStyle(lineWidth: 6, lineCap: .round, dash: [2, 14], dashPhase: phase))
                            }
                        }
                    }
                }
                ForEach(nodes) { node in
                    let pos = Self.center(of: node.key, in: size)
                    NodeCard(node: node, tone: Self.tone(node), width: min(size.width * 0.31, 150))
                        .position(pos)
                        .onTapGesture {
                            if let target = Self.link(node.key), !node.absent { onSelect?(target) }
                        }
                        .accessibilityAddTraits(Self.link(node.key) != nil && !node.absent ? .isButton : [])
                }
                Hub(watts: snapshot.housePower ?? 0, width: min(size.width * 0.28, 130))
                    .position(Self.center(col: 2, row: 2, in: size))
            }
        }
        .aspectRatio(sizeClass == .regular ? 16 / 11 : 1, contentMode: .fit)
        .onAppear { visible = true }
        .onDisappear { visible = false }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text("Energiefluss"))
    }

    // MARK: - Daten (wie PowerFlow.tsx)

    static func nodes(_ s: Snapshot) -> [Node] {
        let fmt = Fmt()
        let grid = s.gridPower ?? 0
        let battery = s.battery?.power ?? 0
        let car = s.allDevices.first { $0.category == .wallbox && $0.enabled }
        let water = s.allDevices.first { $0.category == .waterHeater && $0.enabled }
        let carW = s.wallboxPower ?? 0, waterW = s.waterPower ?? 0
        return [
            Node(key: .pv, watts: s.pv, direction: s.pv > 20 ? 1 : 0, value: fmt.watts(s.pv), sub: Loc.t("Sonne", "Solar")),
            Node(key: .grid, watts: abs(grid), direction: grid > 20 ? 1 : grid < -20 ? -1 : 0, value: fmt.watts(abs(grid)),
                 sub: grid > 20 ? Loc.t("Bezug", "Import") : grid < -20 ? Loc.t("Einspeisung", "Export") : Loc.t("Netz", "Grid"),
                 alert: (s.wasteW ?? 0) > 150),
            Node(key: .battery, watts: abs(battery), direction: battery > 20 ? -1 : battery < -20 ? 1 : 0,
                 value: s.battery != nil ? fmt.percent(s.battery?.soc) : "–",
                 sub: s.battery == nil ? Loc.t("Batterie", "Battery")
                     : battery > 20 ? Loc.t("lädt", "charging") : battery < -20 ? Loc.t("entlädt", "discharging") : Loc.t("Ruhe", "idle"),
                 sub2: abs(battery) > 20 && s.battery != nil ? fmt.watts(abs(battery)) : nil,
                 absent: s.battery == nil),
            Node(key: .car, watts: carW, direction: carW > 20 ? -1 : 0, value: car != nil ? fmt.watts(carW) : "–",
                 sub: Loc.t("Auto", "Car"), sub2: car?.soc.map { fmt.percent($0) }, absent: car == nil),
            Node(key: .water, watts: waterW, direction: waterW > 20 ? -1 : 0, value: water != nil ? fmt.watts(waterW) : "–",
                 sub: Loc.t("Warmwasser", "Hot water"), sub2: water?.temperatureC.map { fmt.celsius($0) }, absent: water == nil),
        ]
    }

    /// 1 kW ≈ 2,4 s je Durchlauf, 10 kW ≈ 0,7 s.
    static func duration(_ watts: Double) -> Double {
        max(0.6, min(3.2, 3.2 - log10(max(watts, 50) / 50) * 1.15))
    }

    static func tone(_ node: Node) -> DomainColor {
        switch node.key {
        case .pv: Palette.pv
        case .grid: node.direction < 0 ? Palette.gridExport : Palette.gridImport
        case .battery: Palette.battery
        case .car: Palette.car
        case .water: Palette.water
        }
    }

    static func link(_ key: Key) -> AppSection? {
        switch key {
        case .car: .car
        case .water: .water
        case .battery: .battery
        case .grid: .tariff
        case .pv: nil
        }
    }

    static func center(of key: Key, in size: CGSize) -> CGPoint {
        switch key {
        case .pv: center(col: 1, row: 1, in: size)
        case .grid: center(col: 3, row: 1, in: size)
        case .battery: center(col: 1, row: 3, in: size)
        case .car: center(col: 2, row: 3, in: size)
        case .water: center(col: 3, row: 3, in: size)
        }
    }

    static func center(col: Int, row: Int, in size: CGSize) -> CGPoint {
        CGPoint(x: (Double(col) - 0.5) / 3 * size.width, y: (Double(row) - 0.5) / 3 * size.height)
    }
}

// MARK: - Knoten

private struct NodeCard: View {
    let node: PowerFlowView.Node
    let tone: DomainColor
    let width: CGFloat

    var body: some View {
        let active = node.direction != 0
        VStack(spacing: 1) {
            Icon(icon, size: 20).foregroundStyle(tone.fill)
            Text(node.value)
                .font(Typo.metricSmall)
                .monospacedDigit()
                .foregroundStyle(Web.textStrong)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
                .contentTransition(.numericText())
            Text(node.sub).font(Typo.caption2).foregroundStyle(Web.textDim).lineLimit(1)
            if let sub2 = node.sub2 {
                Text(sub2).font(Typo.caption2).foregroundStyle(Web.textDim).lineLimit(1)
            }
        }
        .padding(Space.s)
        .frame(width: width)
        .background(Web.surface, in: .rect(cornerRadius: Radius.large, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: Radius.large, style: .continuous)
                .strokeBorder(node.alert ? Palette.waste.fill : active ? tone.fill : Web.line,
                              style: StrokeStyle(lineWidth: 1.5, dash: node.absent ? [5, 4] : []))
        }
        .background {
            if node.alert {
                RoundedRectangle(cornerRadius: Radius.large + 3, style: .continuous)
                    .fill(Palette.waste.fill.opacity(0.25))
                    .padding(-3)
            }
        }
        .shadow(color: Web.shadow, radius: 1, y: 1)
        .opacity(node.absent ? 0.45 : 1)
        .dynamicTypeSize(...DynamicTypeSize.xLarge)
        .accessibilityElement(children: .combine)
    }

    private var icon: WebIcon {
        switch node.key {
        case .pv: .sun
        case .grid: .grid
        case .battery: .battery
        case .car: .car
        case .water: .water
        }
    }
}

private struct Hub: View {
    let watts: Double
    let width: CGFloat

    var body: some View {
        VStack(spacing: 1) {
            Icon(.house, size: 22).foregroundStyle(Web.textDim)
            Text(Fmt().watts(watts))
                .font(Typo.metricSmall).monospacedDigit().foregroundStyle(Web.textStrong)
                .lineLimit(1).minimumScaleFactor(0.7)
                .contentTransition(.numericText())
            Text("Haus").font(Typo.caption2).foregroundStyle(Web.textDim)
        }
        .padding(Space.s)
        .frame(width: width, height: width)
        .background(Web.surface, in: .circle)
        .overlay(Circle().strokeBorder(Web.line, lineWidth: 1.5))
        .shadow(color: Web.shadow, radius: 1, y: 1)
        .dynamicTypeSize(...DynamicTypeSize.xLarge)
        .accessibilityElement(children: .combine)
    }
}
