import MinePowerKit
import SwiftUI

/// Batterie als waagerechter Füllstand – Portierung von `BatteryGauge.tsx`.
///
/// Breite Zelle mit Pol rechts, Füllung bis zum Ladestand, Marken für Reserve
/// und "Batterie zuerst bis". Laden und Entladen zeigt ein langsam wanderndes
/// Streifenmuster in Flussrichtung; Netzladen färbt die Füllung blau.
struct BatteryGaugeView: View {
    struct Mark: Identifiable, Equatable {
        enum Kind { case reserve, priority }
        var soc: Double
        var label: String
        var kind: Kind
        var id: String { "\(kind)" }
    }

    var soc: Double?
    var power: Double
    var gridCharging = false
    var marks: [Mark] = []
    var label: String
    var compact = false

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.horizontalSizeClass) private var sizeClass

    private var flow: Int { power > 30 ? 1 : power < -30 ? -1 : 0 }
    private var level: Double { min(100, max(0, soc ?? 0)) }
    private var fill: Color { gridCharging ? Palette.gridExport.fill : Palette.battery.fill }
    private var height: CGFloat { compact ? 14 : sizeClass == .compact ? 48 : 56 }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                cell
                if !compact {
                    UnevenRoundedRectangle(cornerRadii: .init(bottomTrailing: 6, topTrailing: 6))
                        .fill(Web.lineStrong)
                        .frame(width: 8, height: 22)
                }
            }
            if !compact, !marks.isEmpty {
                labels.padding(.trailing, 14)
            }
        }
        .accessibilityElement()
        .accessibilityLabel(Text(label))
        .accessibilityValue(Text(soc.map { "\(Int($0.rounded())) %" } ?? "–"))
    }

    private var cell: some View {
        GeometryReader { geo in
            let inner = geo.size.width - (compact ? 0 : 8)
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: compact ? height / 2 : 11, style: .continuous)
                    .fill(fill)
                    .frame(width: max(0, inner * level / 100))
                    .overlay {
                        if flow != 0 { stripes.clipShape(RoundedRectangle(cornerRadius: compact ? height / 2 : 11, style: .continuous)) }
                    }
                    .animation(.smooth(duration: 0.42), value: level)
                ForEach(marks) { mark in
                    MarkLine(priority: mark.kind == .priority)
                        .frame(width: 2)
                        .offset(x: inner * min(100, max(0, mark.soc)) / 100 - 1)
                }
            }
            .padding(compact ? 0 : 4)
        }
        .frame(height: height)
        .background(compact ? Web.surface3 : Web.surface2,
                    in: RoundedRectangle(cornerRadius: compact ? height / 2 : 16, style: .continuous))
        .overlay {
            if !compact {
                RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(Web.lineStrong, lineWidth: 2)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: compact ? height / 2 : 16, style: .continuous))
    }

    /// `repeating-linear-gradient(115deg, weiß 22 % 0–10 px, frei 10–22 px)`, wandert 48 px in 1,8 s.
    private var stripes: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion)) { timeline in
            Canvas { context, size in
                let t = timeline.date.timeIntervalSinceReferenceDate
                let shift = reduceMotion ? 0 : (t.truncatingRemainder(dividingBy: 1.8) / 1.8) * 48 * Double(flow)
                let slant = size.height * tan(25 * .pi / 180)
                var x = -48.0 + shift.truncatingRemainder(dividingBy: 48) - slant
                while x < size.width + slant {
                    var stripe = Path()
                    stripe.move(to: CGPoint(x: x, y: size.height))
                    stripe.addLine(to: CGPoint(x: x + 10, y: size.height))
                    stripe.addLine(to: CGPoint(x: x + 10 + slant, y: 0))
                    stripe.addLine(to: CGPoint(x: x + slant, y: 0))
                    stripe.closeSubpath()
                    context.fill(stripe, with: .color(.white.opacity(0.22)))
                    x += 22
                }
            }
        }
    }

    private var labels: some View {
        GeometryReader { geo in
            let positions = spreadLabels(marks.map(\.soc), gap: 18, limit: 100)
            ForEach(Array(marks.enumerated()), id: \.element.id) { index, mark in
                let x = geo.size.width * positions[index] / 100
                Text(mark.label)
                    .font(Typo.caption)
                    .foregroundStyle(mark.kind == .priority ? Palette.accent : Web.textStrong)
                    .fixedSize()
                    .alignmentGuide(.leading) { d in
                        positions[index] < 12 ? 0 : positions[index] > 88 ? d.width : d.width / 2
                    }
                    .offset(x: x)
            }
        }
        .frame(height: 22)
    }
}

private struct MarkLine: View {
    var priority: Bool
    var body: some View {
        Line()
            .stroke(priority ? Palette.accent : Web.textStrong,
                    style: StrokeStyle(lineWidth: 2, lineCap: priority ? .round : .butt, dash: priority ? [0.1, 4] : [4, 3]))
    }

    private struct Line: Shape {
        func path(in rect: CGRect) -> Path {
            Path { $0.move(to: CGPoint(x: rect.midX, y: rect.minY)); $0.addLine(to: CGPoint(x: rect.midX, y: rect.maxY)) }
        }
    }
}
