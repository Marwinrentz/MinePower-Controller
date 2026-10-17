import ActivityKit
import MinePowerKit
import SwiftUI
import WidgetKit

/// Live-Aktivität: Boost läuft bzw. Auto lädt sofort, mit Restzeit, die ohne
/// Aktualisierung weiterzählt (`Text(timerInterval:)`).
struct MinePowerActivityWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: MinePowerActivityAttributes.self) { context in
            LockScreenActivityView(attributes: context.attributes, state: context.state, isStale: context.isStale)
                .padding(16)
                .activityBackgroundTint(Web.surface)
                .activitySystemActionForegroundColor(Web.textStrong)
                .widgetURL(Self.link(context.attributes.kind))
        } dynamicIsland: { context in
            let tone = Self.tone(context.attributes.kind)
            return DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Label {
                        Text(context.attributes.deviceName).lineLimit(1)
                    } icon: {
                        Icon(Self.icon(context.attributes.kind), size: 18).foregroundStyle(tone)
                    }
                    .font(.subheadline.weight(.semibold))
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(context.state.value).font(.title3.weight(.bold)).monospacedDigit().foregroundStyle(tone)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text(context.isStale ? Self.staleText : context.state.detail).font(.subheadline)
                            Spacer()
                            Remaining(endsAt: context.state.endsAt).font(.subheadline.weight(.semibold))
                        }
                        if let progress = context.state.progress {
                            ProgressView(value: progress).tint(tone)
                        }
                    }
                }
            } compactLeading: {
                Icon(Self.icon(context.attributes.kind), size: 16).foregroundStyle(tone)
            } compactTrailing: {
                if context.state.endsAt != nil {
                    Remaining(endsAt: context.state.endsAt).frame(maxWidth: 52)
                } else {
                    Text(context.state.value).monospacedDigit()
                }
            } minimal: {
                Icon(Self.icon(context.attributes.kind), size: 14).foregroundStyle(tone)
            }
            .widgetURL(Self.link(context.attributes.kind))
            .keylineTint(tone)
        }
    }

    /// Nach dem Ende (staleDate) ohne neue Werte: nicht mehr behaupten, dass es läuft.
    static let staleText = String(localized: "Vermutlich beendet – App öffnen")

    static func icon(_ kind: MinePowerActivityAttributes.Kind) -> WebIcon {
        kind == .boost ? .boost : .bolt
    }

    static func tone(_ kind: MinePowerActivityAttributes.Kind) -> Color {
        kind == .boost ? Palette.water.fill : Palette.car.fill
    }

    static func link(_ kind: MinePowerActivityAttributes.Kind) -> URL? {
        URL(string: kind == .boost ? "minepower://water" : "minepower://car")
    }
}

/// Restzeit als selbst laufender Countdown; ohne Ende ein Strich.
private struct Remaining: View {
    let endsAt: Date?

    var body: some View {
        if let endsAt, endsAt > .now {
            Text(timerInterval: Date.now...endsAt, countsDown: true)
                .monospacedDigit()
                .multilineTextAlignment(.trailing)
        } else {
            Text(verbatim: "–")
        }
    }
}

private struct LockScreenActivityView: View {
    let attributes: MinePowerActivityAttributes
    let state: MinePowerActivityAttributes.ContentState
    let isStale: Bool

    var body: some View {
        let tone = MinePowerActivityWidget.tone(attributes.kind)
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Icon(MinePowerActivityWidget.icon(attributes.kind), size: 22)
                    .foregroundStyle(tone)
                    .frame(width: 40, height: 40)
                    .background(tone.opacity(0.15), in: .rect(cornerRadius: 12, style: .continuous))
                VStack(alignment: .leading, spacing: 1) {
                    Text(isStale ? (attributes.kind == .boost ? String(localized: "Boost") : String(localized: "Sofort laden"))
                         : attributes.kind == .boost ? String(localized: "Boost läuft") : String(localized: "Sofort laden"))
                        .font(Typo.font(16, .bold, relativeTo: .headline))
                        .foregroundStyle(Web.textStrong)
                    Text("\(attributes.deviceName) · \(isStale ? MinePowerActivityWidget.staleText : state.detail)")
                        .font(Typo.font(13, .regular, relativeTo: .subheadline))
                        .foregroundStyle(Web.textDim)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                VStack(alignment: .trailing, spacing: 1) {
                    Text(state.value)
                        .font(Typo.font(22, .extraBold, relativeTo: .title))
                        .foregroundStyle(tone)
                        .monospacedDigit()
                    Remaining(endsAt: state.endsAt)
                        .font(Typo.font(13, .semibold, relativeTo: .subheadline))
                        .foregroundStyle(Web.textDim)
                }
            }
            if let progress = state.progress {
                ProgressView(value: progress).tint(tone)
            }
        }
    }
}
