import MinePowerKit
import SwiftUI

/// Umbrechende Zeile (`flex-wrap`): Chips, Knöpfe, Badges.
struct FlowLayout: Layout {
    var spacing: CGFloat = 8
    var lineSpacing: CGFloat?

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        let rows = arrange(subviews, width: width)
        let height = rows.reduce(0) { $0 + $1.height } + CGFloat(max(0, rows.count - 1)) * (lineSpacing ?? spacing)
        let used = rows.map(\.width).max() ?? 0
        return CGSize(width: proposal.width ?? used, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(subviews, width: bounds.width) {
            var x = bounds.minX
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                let fitted = CGSize(width: min(size.width, bounds.width), height: size.height)
                subviews[index].place(at: CGPoint(x: x, y: y + (row.height - fitted.height) / 2),
                                      proposal: ProposedViewSize(fitted))
                x += fitted.width + spacing
            }
            y += row.height + (lineSpacing ?? spacing)
        }
    }

    private struct Row {
        var indices: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func arrange(_ subviews: Subviews, width: CGFloat) -> [Row] {
        var rows: [Row] = []
        var current = Row()
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            let w = min(size.width, width)
            if !current.indices.isEmpty, current.width + spacing + w > width {
                rows.append(current)
                current = Row()
            }
            current.width += (current.indices.isEmpty ? 0 : spacing) + w
            current.height = max(current.height, size.height)
            current.indices.append(index)
        }
        if !current.indices.isEmpty { rows.append(current) }
        return rows
    }
}

/// Kopfzeile wie die Web-`topbar`: Logo links, Live-Zustand rechts (als Glas).
struct BrandToolbar: ToolbarContent {
    var body: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            BrandMark(size: 28)
                .accessibilityLabel(Text("MinePower"))
        }
        ToolbarItem(placement: .topBarTrailing) {
            LiveIndicator()
        }
    }
}

/// `.live`: grüner Punkt und "Live", sonst "verbinde …" oder das Alter der Werte.
struct LiveIndicator: View {
    @Environment(AppSession.self) private var session

    var body: some View {
        TimelineView(.periodic(from: .now, by: 5)) { context in
            let connection = session.live.connection
            let age = session.live.lastUpdate.map { context.date.timeIntervalSince($0) }
            let live = connection == .live && (age ?? .infinity) < 60
            HStack(spacing: 6) {
                Circle()
                    .fill(live ? Palette.ok.fill : Palette.warn.fill)
                    .frame(width: 9, height: 9)
                    .overlay {
                        if live { PulseRing(color: Palette.ok.fill) }
                    }
                Text(live ? String(localized: "Live") : label(connection, age: age))
                    .font(Typo.subheadlineMedium)
                    .foregroundStyle(live ? Web.textDim : Palette.warn.ink)
            }
            .padding(.horizontal, 4)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.updatesFrequently)
        }
    }

    private func label(_ connection: LiveStore.Connection, age: TimeInterval?) -> String {
        if connection == .connecting || connection == .reconnecting { return String(localized: "verbinde …") }
        if let age, age.isFinite { return Fmt().ago(Date.now.addingTimeInterval(-age)) }
        return String(localized: "offline")
    }
}

/// `.dot.online::after`: weich pulsierender Ring.
private struct PulseRing: View {
    let color: Color
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        if reduceMotion {
            EmptyView()
        } else {
            Circle()
                .fill(color)
                .phaseAnimator([0.0, 1.0]) { view, phase in
                    view.scaleEffect(0.7 + phase).opacity(0.45 * (1 - phase))
                } animation: { _ in .easeOut(duration: 2.4) }
        }
    }
}
