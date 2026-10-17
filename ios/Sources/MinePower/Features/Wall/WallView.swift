import MinePowerKit
import SwiftUI

/// Wand-Ansicht (Spiegel von `Wall.tsx`): für ein iPad im Flur, im
/// Vorbeigehen gelesen. Oben eine Aussage, links die Netzpunkt-Waage mit
/// Kennzahlen, rechts die Balken je Energiefluss und die Geräte. Der Zustand
/// färbt den Schein am oberen Rand, nachts dunkelt die Ansicht ab, der
/// Bildschirm bleibt an. Schrift wächst mit der Breite (wie `clamp()` im Web).
struct WallView: View {
    @Environment(AppSession.self) private var session
    @Environment(\.dismiss) private var dismiss
    @State private var now = Date()
    /// Nachts nach Tippen kurz in voller Helligkeit (wie `:hover` im Web).
    @State private var awakeUntil: Date?

    var body: some View {
        GeometryReader { geo in
            let size = Fluid(size: geo.size)
            let snapshot = session.live.snapshot
            let state = snapshot.map { WallState($0) }
            ZStack(alignment: .top) {
                Web.bg.ignoresSafeArea()
                RadialGradient(colors: [glow(state?.tone).opacity(0.3), .clear], center: .top,
                               startRadius: 0, endRadius: max(geo.size.width * 0.62, 200))
                    .frame(height: min(max(geo.size.height * 0.16, 120), 260))
                    .frame(maxWidth: .infinity)
                    .ignoresSafeArea()
                    .animation(.smooth(duration: 0.6), value: state?.tone)
                if let snapshot, let state {
                    content(snapshot, state: state, size: size)
                } else {
                    Text("Lädt …")
                        .font(Typo.font(size.fluid(22, 3.6, 60), .bold, relativeTo: .largeTitle))
                        .foregroundStyle(Web.textStrong)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .overlay {
                if isDimmed {
                    Color.black.opacity(0.55).ignoresSafeArea().allowsHitTesting(false)
                }
            }
            .animation(.smooth(duration: 0.6), value: isDimmed)
        }
        .contentShape(.rect)
        .onTapGesture { awakeUntil = .now.addingTimeInterval(30) }
        .accessibilityIdentifier("wall")
        .statusBarHidden()
        .persistentSystemOverlays(.hidden)
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        .onDisappear { UIApplication.shared.isIdleTimerDisabled = false }
        .onKeyPress(.escape) {
            dismiss()
            return .handled
        }
        .task {
            while !Task.isCancelled {
                now = .now
                try? await Task.sleep(for: .seconds(10))
            }
        }
    }

    /// Nachts (22–6 Uhr) dunkler, außer kurz nach einer Berührung.
    private var isDimmed: Bool {
        let hour = Calendar.current.component(.hour, from: now)
        guard hour >= 22 || hour < 6 else { return false }
        return (awakeUntil ?? .distantPast) < now
    }

    private func glow(_ tone: WallState.Tone?) -> Color {
        switch tone {
        case .ok, nil: Palette.pv.fill
        case .waste: Palette.waste.fill
        case .warn: Palette.warn.fill
        case .error: Palette.error.fill
        case .idle: Web.lineStrong
        }
    }

    @ViewBuilder
    private func content(_ snapshot: Snapshot, state: WallState, size: Fluid) -> some View {
        let gap = size.fluid(10, 1.4, 22)
        let wide = size.width > 780 && size.width / max(size.height, 1) > 0.8
        let main = VStack(alignment: .leading, spacing: gap) {
            header(state, size: size)
            if wide {
                HStack(alignment: .top, spacing: gap) {
                    leftColumn(snapshot, size: size, gap: gap)
                        .frame(maxWidth: .infinity)
                    rightColumn(snapshot, size: size, gap: gap, narrow: false)
                        .frame(maxWidth: .infinity)
                }
            } else {
                leftColumn(snapshot, size: size, gap: gap)
                rightColumn(snapshot, size: size, gap: gap, narrow: true)
            }
        }
        .padding(size.fluid(14, 1.8, 34))
        if wide {
            main.frame(maxHeight: .infinity, alignment: .top)
        } else {
            ScrollView { main }.scrollIndicators(.hidden)
        }
    }

    private func header(_ state: WallState, size: Fluid) -> some View {
        HStack(alignment: .top, spacing: Space.l) {
            VStack(alignment: .leading, spacing: 2) {
                Text(state.headline)
                    .font(Typo.font(size.fluid(22, 3.6, 60), .bold, relativeTo: .largeTitle))
                    .foregroundStyle(Web.textStrong)
                    .contentTransition(.opacity)
                Text(state.sub)
                    .font(Typo.font(size.fluid(12, 1.35, 22), .semibold, relativeTo: .title3))
                    .foregroundStyle(Web.textDim)
                    .lineLimit(1)
            }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.updatesFrequently)
            Spacer(minLength: Space.s)
            VStack(alignment: .trailing, spacing: Space.s) {
                Text(Fmt().clock(now))
                    .font(Typo.font(size.fluid(16, 2.1, 36), .bold, relativeTo: .title))
                    .monospacedDigit()
                    .foregroundStyle(Web.textDim)
                HStack(spacing: Space.s) {
                    if session.live.connection != .live {
                        HStack(spacing: 4) {
                            Icon(.warning, size: 15)
                            Text("Keine Live-Verbindung")
                        }
                        .font(Typo.subheadlineMedium)
                        .foregroundStyle(Palette.warn.ink)
                    }
                    Button { dismiss() } label: {
                        Icon(.close, size: 17)
                            .foregroundStyle(Web.text)
                            .frame(width: 44, height: 44)
                            .contentShape(.circle)
                    }
                    .buttonStyle(.plain)
                    .glassControl()
                    .accessibilityLabel(Text("Verlassen"))
                    .accessibilityIdentifier("wallExit")
                }
            }
        }
    }

    private func leftColumn(_ s: Snapshot, size: Fluid, gap: CGFloat) -> some View {
        let fmt = Fmt()
        let surplus = s.surplus ?? 0
        let waste = s.wasteW ?? 0
        return VStack(alignment: .leading, spacing: gap) {
            BalanceMeterView(snapshot: s, size: size)
            LazyVGrid(columns: [GridItem(.flexible(), spacing: size.fluid(8, 1.1, 18)),
                                GridItem(.flexible(), spacing: size.fluid(8, 1.1, 18))],
                      spacing: size.fluid(8, 1.1, 18)) {
                WallKpi(value: fmt.watts(s.pv), label: String(localized: "PV"), color: Palette.pv.fill, size: size)
                WallKpi(value: fmt.watts(surplus), label: String(localized: "Überschuss"),
                        color: surplus > 100 ? Palette.pv.fill : Web.textDim, size: size)
                WallKpi(value: fmt.watts(waste), label: String(localized: "Verschenkt"),
                        color: waste > 100 ? Palette.waste.fill : Web.textDim, size: size)
                WallKpi(value: s.battery?.soc.map { "\(Int($0.rounded())) %" } ?? "–", label: String(localized: "Batterie"),
                        color: s.battery != nil ? Palette.battery.fill : Web.textDim, size: size)
                // Nur bei dynamischem Tarif, sonst stünde dauerhaft ein Strich da.
                if let price = s.priceCt {
                    let cheap = s.priceCheap
                    WallKpi(value: fmt.cents(price),
                            label: String(localized: "Strompreis") + (cheap.map { $0 ? " · " + String(localized: "günstig") : " · " + String(localized: "normal") } ?? ""),
                            color: cheap == true ? Palette.pv.fill : Web.textDim, size: size)
                }
            }
        }
    }

    private func rightColumn(_ s: Snapshot, size: Fluid, gap: CGFloat, narrow: Bool) -> some View {
        let tiles = s.enabledDevices.filter { [.wallbox, .waterHeater, .battery].contains($0.category) }.prefix(4)
        let tileGap = size.fluid(8, 1.1, 18)
        return VStack(alignment: .leading, spacing: gap) {
            WallBarsView(snapshot: s, size: size)
                .padding(size.fluid(12, 1.6, 26))
                .frame(maxWidth: .infinity)
                .background(Web.surface, in: .rect(cornerRadius: Radius.card, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.card, style: .continuous).strokeBorder(Web.line, lineWidth: 1))
                .shadow(color: Web.shadow, radius: 12, y: 4)
            if !tiles.isEmpty {
                LazyVGrid(columns: narrow
                          ? [GridItem(.flexible(), spacing: tileGap), GridItem(.flexible(), spacing: tileGap)]
                          : [GridItem(.adaptive(minimum: 170), spacing: tileGap)],
                          spacing: tileGap) {
                    ForEach(Array(tiles)) { WallTile(device: $0, size: size) }
                }
            }
        }
    }
}

/// Schriftgrößen, die mit der Fläche wachsen (`clamp(min, vw, max)` im Web).
struct Fluid {
    let width: CGFloat
    let height: CGFloat

    init(size: CGSize) {
        width = size.width
        height = size.height
    }

    func fluid(_ lower: CGFloat, _ vw: CGFloat, _ upper: CGFloat) -> CGFloat {
        min(max(width * vw / 100, lower), upper)
    }

    func fluidHeight(_ lower: CGFloat, _ vh: CGFloat, _ upper: CGFloat) -> CGFloat {
        min(max(height * vh / 100, lower), upper)
    }
}

// MARK: - Netzpunkt-Waage

/// `BalanceMeter` (groß): Steht der Netzpunkt auf null? Fester Nullpunkt in
/// der Mitte, sichtbares Zielband (Totband), Ausschlag nach links (Einspeisung)
/// oder rechts (Bezug).
struct BalanceMeterView: View {
    let snapshot: Snapshot
    let size: Fluid
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let fmt = Fmt()
        let grid = snapshot.gridPower ?? 0
        let balance = Balance(snapshot)
        let tone: Color = switch balance.state {
        case .balanced: Palette.ok.fill
        case .importing: Palette.gridImport.fill
        case .exporting: Palette.waste.fill
        }
        let label: String = switch balance.state {
        case .balanced: String(localized: "Ausgeglichen")
        case .importing: String(localized: "Bezug")
        case .exporting: String(localized: "Einspeisung")
        }
        let trackHeight = size.fluid(26, 3.4, 54)
        let offset = balance.offset(grid)
        VStack(alignment: .leading, spacing: Space.s) {
            HStack(alignment: .firstTextBaseline) {
                Text("Netzpunkt")
                    .font(Typo.font(size.fluid(10, 0.95, 16) + 2, .semibold, relativeTo: .subheadline))
                    .foregroundStyle(Web.textDim)
                Spacer()
                Text(label)
                    .font(Typo.font(size.fluid(13, 1.5, 26), .bold, relativeTo: .headline))
                    .foregroundStyle(tone)
            }
            HStack(alignment: .firstTextBaseline, spacing: 5) {
                Text(balance.state == .balanced ? "0" : fmt.watts(abs(grid)))
                    .font(Typo.font(size.fluid(34, 6, 92), .extraBold, relativeTo: .largeTitle))
                    .monospacedDigit()
                    .foregroundStyle(tone)
                    .contentTransition(.numericText(value: grid))
                if balance.state == .balanced {
                    Text(verbatim: "W")
                        .font(Typo.font(size.fluid(16, 2, 32), .bold, relativeTo: .title3))
                        .foregroundStyle(Web.textDim)
                }
            }
            GeometryReader { geo in
                let w = geo.size.width
                ZStack(alignment: .leading) {
                    Capsule().fill(Web.surface3)
                    Capsule().fill(Palette.ok.fill.opacity(0.26))
                        .frame(width: w * balance.bandShare)
                        .offset(x: w * (0.5 - balance.bandShare / 2))
                    Capsule().fill(tone)
                        .frame(width: w * abs(offset) / 2, height: max(trackHeight - 4, 0))
                        .offset(x: offset >= 0 ? w / 2 : w / 2 - w * abs(offset) / 2)
                    Rectangle().fill(Web.textDim.opacity(0.7))
                        .frame(width: 2, height: trackHeight + 2)
                        .offset(x: w / 2 - 1)
                }
                .frame(height: trackHeight)
                .clipShape(.capsule)
                .overlay {
                    if balance.state == .balanced {
                        Capsule().strokeBorder(Palette.ok.fill.opacity(0.3), lineWidth: 2).padding(-2)
                    }
                }
                .animation(reduceMotion ? nil : .smooth(duration: 0.6), value: offset)
                .animation(reduceMotion ? nil : .smooth(duration: 0.6), value: balance.bandShare)
            }
            .frame(height: trackHeight)
            HStack {
                Text("Einspeisung")
                Spacer()
                Text(verbatim: "0").foregroundStyle(Web.text).fontWeight(.bold)
                Spacer()
                Text("Bezug")
            }
            .font(Typo.font(size.fluid(10, 1, 17), .semibold, relativeTo: .caption))
            .foregroundStyle(Web.textDim)
        }
        .padding(size.fluid(14, 1.8, 30))
        .background(Web.surface2, in: .rect(cornerRadius: Radius.card, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
            .strokeBorder(balance.state == .balanced ? Palette.ok.fill.opacity(0.4) : Web.line, lineWidth: 1))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("Netzpunkt"))
        .accessibilityValue(Text("\(label): \(fmt.watts(abs(grid)))"))
        .accessibilityAddTraits(.updatesFrequently)
    }
}

// MARK: - Balken je Energiefluss

/// `WallBars`: ein Balken je Fluss, alle auf derselben festen Skala.
private struct WallBarsView: View {
    let snapshot: Snapshot
    let size: Fluid
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let fmt = Fmt()
        let rows = WallBarRow.rows(snapshot)
        let scale = WallBarRow.scale(rows)
        let trackHeight = size.fluidHeight(18, 2.6, 36)
        Grid(alignment: .leading, horizontalSpacing: size.fluid(12, 1.4, 28), verticalSpacing: size.fluid(8, 1, 16)) {
            ForEach(rows) { row in
                let idle = row.watts < 50
                let color = Self.color(row.kind)
                GridRow {
                    HStack(spacing: 10) {
                        Icon(Self.icon(row.kind), size: 26)
                        Text(Self.title(row.kind))
                    }
                    .font(Typo.font(size.fluid(16, 1.5, 30), .bold, relativeTo: .title3))
                    .foregroundStyle(idle ? Web.textDim : Web.textStrong)
                    .lineLimit(1)
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            Capsule().fill(Web.surface3)
                            Capsule().fill(color)
                                .frame(width: geo.size.width * max(0, min(1, row.watts / scale)))
                            Path { path in
                                path.move(to: CGPoint(x: geo.size.width / 2, y: 0))
                                path.addLine(to: CGPoint(x: geo.size.width / 2, y: geo.size.height))
                            }
                            .stroke(Web.lineStrong.opacity(0.6), style: StrokeStyle(lineWidth: 2, dash: [4, 3]))
                        }
                        .clipShape(.capsule)
                        .animation(reduceMotion ? nil : .easeOut(duration: 0.6), value: row.watts)
                    }
                    .frame(height: trackHeight)
                    .gridColumnAlignment(.leading)
                    VStack(alignment: .trailing, spacing: 0) {
                        Text(fmt.watts(row.watts))
                            .font(Typo.font(size.fluid(20, 2, 40), .extraBold, relativeTo: .title))
                            .monospacedDigit()
                            .foregroundStyle(idle ? Web.textDim : color)
                            .contentTransition(.numericText(value: row.watts))
                        if let note = row.note {
                            Text(note)
                                .font(Typo.font(size.fluid(12, 0.9, 18), .semibold, relativeTo: .caption))
                                .foregroundStyle(Web.textDim)
                        }
                    }
                    .lineLimit(1)
                    .frame(minWidth: size.fluid(20, 2, 40) * 4.4, alignment: .trailing)
                    .gridColumnAlignment(.trailing)
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(Text(Self.title(row.kind)))
                .accessibilityValue(Text([fmt.watts(row.watts), row.note].compactMap { $0 }.joined(separator: ", ")))
            }
            GridRow {
                Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
                HStack {
                    Text(verbatim: "0")
                    Spacer()
                    Text(fmt.watts(scale / 2))
                    Spacer()
                    Text(fmt.watts(scale))
                }
                .font(Typo.font(size.fluid(12, 0.9, 16), .semibold, relativeTo: .caption))
                .foregroundStyle(Web.textDim)
                .accessibilityHidden(true)
                Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
            }
        }
    }

    static func title(_ kind: WallBarRow.Kind) -> String {
        switch kind {
        case .pv: String(localized: "PV")
        case .house: String(localized: "Haus")
        case .car: String(localized: "Auto")
        case .water: String(localized: "Warmwasser")
        case .battery: String(localized: "Batterie")
        case .gridImport, .gridExport: String(localized: "Netz")
        }
    }

    static func icon(_ kind: WallBarRow.Kind) -> WebIcon {
        switch kind {
        case .pv: .sun
        case .house: .house
        case .car: .car
        case .water: .water
        case .battery: .battery
        case .gridImport, .gridExport: .grid
        }
    }

    static func color(_ kind: WallBarRow.Kind) -> Color {
        switch kind {
        case .pv: Palette.pv.fill
        case .house: Palette.house.fill
        case .car: Palette.car.fill
        case .water: Palette.water.fill
        case .battery: Palette.battery.fill
        case .gridImport: Palette.gridImport.fill
        case .gridExport: Palette.gridExport.fill
        }
    }
}

// MARK: - Kacheln

private struct WallKpi: View {
    let value: String
    let label: String
    let color: Color
    let size: Fluid

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value)
                .font(Typo.font(size.fluid(19, 2.6, 44), .bold, relativeTo: .title))
                .monospacedDigit()
                .foregroundStyle(color)
                .contentTransition(.numericText())
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            Text(label)
                .font(Typo.font(size.fluid(10, 1, 17), .semibold, relativeTo: .caption))
                .foregroundStyle(Web.textDim)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(size.fluid(9, 1.2, 20))
        .background(Web.surface, in: .rect(cornerRadius: Radius.large, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.large, style: .continuous).strokeBorder(Web.line, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}

private struct WallTile: View {
    let device: SnapshotDevice
    let size: Fluid

    var body: some View {
        let fmt = Fmt()
        let power = device.displayPower
        let active = abs(power) > 50
        let color = Palette.category(device.category).fill
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: Space.s) {
                Icon(device.category.webIcon, size: size.fluid(15, 1.7, 27))
                    .foregroundStyle(color)
                Text(device.name)
                    .font(Typo.font(size.fluid(11, 1.1, 18), .semibold, relativeTo: .subheadline))
                    .foregroundStyle(Web.textDim)
                    .lineLimit(1)
            }
            if device.category == .battery, let soc = device.soc {
                Text("\(Int(soc.rounded())) %")
                    .font(Typo.font(size.fluid(21, 2.8, 46), .bold, relativeTo: .title))
                    .monospacedDigit()
                    .foregroundStyle(color)
                    .contentTransition(.numericText(value: soc))
                GeometryReader { geo in
                    Capsule().fill(Web.surface3)
                        .overlay(alignment: .leading) {
                            Capsule().fill(color).frame(width: geo.size.width * max(0, min(1, soc / 100)))
                        }
                }
                .frame(height: size.fluid(5, 0.6, 10))
            } else {
                Text(fmt.watts(power))
                    .font(Typo.font(size.fluid(21, 2.8, 46), .bold, relativeTo: .title))
                    .monospacedDigit()
                    .foregroundStyle(color)
                    .contentTransition(.numericText(value: power))
            }
            Text(subtitle(fmt))
                .font(Typo.font(size.fluid(10, 1, 16), .semibold, relativeTo: .caption))
                .foregroundStyle(Web.textDim)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(size.fluid(10, 1.3, 22))
        .background(Web.surface, in: .rect(cornerRadius: Radius.large, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.large, style: .continuous)
            .strokeBorder(active ? Palette.accent.opacity(0.5) : Web.line,
                          style: StrokeStyle(lineWidth: 1, dash: device.online ? [] : [5, 4])))
        .opacity(device.online ? 1 : 0.5)
        .accessibilityElement(children: .combine)
    }

    private func subtitle(_ fmt: Fmt) -> String {
        if !device.online { return String(localized: "offline") }
        switch device.category {
        case .wallbox:
            guard let soc = device.soc else { return " " }
            return "\(Int(soc.rounded())) % · \(stateLabel(device.values.string("state") ?? "idle"))"
        case .waterHeater:
            return device.temperatureC.map { fmt.celsius($0) } ?? " "
        case .battery:
            let power = device.values.double("power") ?? 0
            return power > 30 ? String(localized: "lädt") : power < -30 ? String(localized: "entlädt") : String(localized: "Ruhe")
        default:
            return " "
        }
    }

    private func stateLabel(_ raw: String) -> String {
        switch raw {
        case "charging": String(localized: "lädt")
        case "complete": String(localized: "fertig")
        case "connected": String(localized: "verbunden")
        case "error": String(localized: "Fehler")
        default: String(localized: "kein Fahrzeug")
        }
    }
}
