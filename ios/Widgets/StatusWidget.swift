import AppIntents
import MinePowerKit
import SwiftUI
import WidgetKit

/// Konfiguration ohne Einstellungen (AppIntentTimelineProvider braucht eine).
struct StatusConfigurationIntent: WidgetConfigurationIntent {
    static let title: LocalizedStringResource = "MinePower"
    static let description = IntentDescription("Was die Anlage gerade tut.")
}

struct StatusEntry: TimelineEntry {
    enum Note { case signedOut, locked }

    let date: Date
    let state: WidgetState?
    var note: Note?

    static let preview = StatusEntry(date: .now, state: WidgetState(
        snapshot: DemoBackend().snapshot(), serverName: "Demo", isDemo: true))
}

/// Liest den von der App abgelegten Stand und holt, wenn erlaubt, einen frischen.
struct StatusProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> StatusEntry { .preview }

    func snapshot(for configuration: StatusConfigurationIntent, in context: Context) async -> StatusEntry {
        context.isPreview ? .preview : await load(refresh: false)
    }

    func timeline(for configuration: StatusConfigurationIntent, in context: Context) async -> Timeline<StatusEntry> {
        let entry = await load(refresh: true)
        // WidgetKit teilt das Budget zu; 15 Minuten sind der übliche Takt.
        return Timeline(entries: [entry], policy: .after(.now.addingTimeInterval(15 * 60)))
    }

    private func load(refresh: Bool) async -> StatusEntry {
        let cached = WidgetState.load()
        guard let server = SharedServer.load() else { return StatusEntry(date: .now, state: nil, note: .signedOut) }
        guard refresh else { return StatusEntry(date: .now, state: cached) }
        // Mit Face-ID-Sperre bleibt das Token unlesbar: nur den letzten Stand der App zeigen.
        if server.isProtected { return StatusEntry(date: .now, state: cached, note: cached == nil ? .locked : nil) }
        let token = server.isDemo ? nil : KeychainStore.plainSecret(for: server.tokenKey)
        guard server.isDemo || token != nil else { return StatusEntry(date: .now, state: cached) }
        do {
            let fresh = try await server.fetchState(token: token)
            fresh.save()
            return StatusEntry(date: .now, state: fresh)
        } catch {
            return StatusEntry(date: .now, state: cached)
        }
    }
}

struct StatusWidget: Widget {
    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: "de.minepower.status", intent: StatusConfigurationIntent.self, provider: StatusProvider()) { entry in
            StatusWidgetView(entry: entry)
                .containerBackground(for: .widget) { Web.surface }
                .widgetURL(URL(string: "minepower://dashboard"))
        }
        .configurationDisplayName("MinePower")
        .description("Kernaussage, PV, Netz und Batterie auf einen Blick.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryCircular, .accessoryRectangular, .accessoryInline])
    }
}

// MARK: - Ansichten

struct StatusWidgetView: View {
    let entry: StatusEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        if let state = entry.state {
            switch family {
            case .systemMedium: MediumView(state: state)
            case .accessoryCircular: CircularView(state: state)
            case .accessoryRectangular: RectangularView(state: state)
            case .accessoryInline: Text(inlineText(state))
            default: SmallView(state: state)
            }
        } else {
            switch family {
            case .accessoryInline: Text(message)
            case .accessoryCircular: Icon(.sun, size: 22)
            default: EmptyStateView(message: message)
            }
        }
    }

    private var message: String {
        entry.note == .locked ? String(localized: "Gesperrt – App öffnen") : String(localized: "In der App anmelden")
    }

    private func inlineText(_ state: WidgetState) -> String {
        let fmt = Fmt()
        return "☀ \(fmt.watts(state.pvW)) · " + GridText.short(state.gridW, fmt: fmt)
    }
}

enum GridText {
    static func short(_ grid: Double, fmt: Fmt) -> String {
        grid > 50 ? String(localized: "Bezug \(fmt.watts(grid))")
            : grid < -50 ? String(localized: "Einspeisung \(fmt.watts(-grid))") : String(localized: "Netz ausgeglichen")
    }

    static func color(_ grid: Double) -> Color {
        grid > 50 ? Palette.gridImport.fill : grid < -50 ? Palette.gridExport.fill : Web.textDim
    }
}

/// Kopfzeile: Logo, Name, Alter des Stands.
private struct Header: View {
    let state: WidgetState

    var body: some View {
        HStack(spacing: 6) {
            BrandMark(size: 18)
            Text(state.isDemo ? "Demo" : state.serverName)
                .font(Typo.font(12, .semibold, relativeTo: .caption))
                .foregroundStyle(Web.textDim)
                .lineLimit(1)
            Spacer(minLength: 0)
            if state.isStale() {
                Icon(.warning, size: 12).foregroundStyle(Palette.warn.fill)
            }
            Text(state.time, style: .time)
                .font(Typo.font(11, .semibold, relativeTo: .caption2))
                .foregroundStyle(state.isStale() ? Palette.warn.ink : Web.textDim)
                .monospacedDigit()
        }
    }
}

private struct SmallView: View {
    let state: WidgetState

    var body: some View {
        let fmt = Fmt()
        VStack(alignment: .leading, spacing: 6) {
            Header(state: state)
            Text(state.headline)
                .font(Typo.font(14, .bold, relativeTo: .headline))
                .foregroundStyle(Web.textStrong)
                .lineLimit(2)
                .minimumScaleFactor(0.85)
            Spacer(minLength: 0)
            HStack(spacing: 4) {
                Icon(.sun, size: 16).foregroundStyle(Palette.pv.fill)
                Text(fmt.watts(state.pvW))
                    .font(Typo.font(22, .extraBold, relativeTo: .title))
                    .foregroundStyle(Palette.pv.ink)
                    .monospacedDigit()
                    .minimumScaleFactor(0.7)
            }
            Text(GridText.short(state.gridW, fmt: fmt))
                .font(Typo.font(12, .semibold, relativeTo: .caption))
                .foregroundStyle(GridText.color(state.gridW))
                .lineLimit(1)
            if let soc = state.battery?.soc {
                SocLine(soc: soc)
            }
        }
    }
}

private struct MediumView: View {
    let state: WidgetState

    var body: some View {
        let fmt = Fmt()
        HStack(alignment: .top, spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                Header(state: state)
                Text(state.headline)
                    .font(Typo.font(16, .bold, relativeTo: .headline))
                    .foregroundStyle(Web.textStrong)
                    .lineLimit(3)
                Spacer(minLength: 0)
                if let car = state.car {
                    DeviceLine(icon: .car, color: Palette.car.fill, text: "\(car.name): \(car.label)")
                }
                if let water = state.water {
                    DeviceLine(icon: .water, color: Palette.water.fill,
                               text: "\(water.name): \(fmt.celsius(water.tempC))" + (water.boost ? " · Boost" : ""))
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 8) {
                GridRow {
                    Figure(icon: .sun, label: "PV", value: fmt.watts(state.pvW), color: Palette.pv.ink)
                    Figure(icon: .house, label: String(localized: "Haus"), value: fmt.watts(state.houseW), color: Web.textStrong)
                }
                GridRow {
                    Figure(icon: .grid, label: String(localized: "Netz"), value: fmt.watts(abs(state.gridW)),
                           color: state.gridW > 50 ? Palette.gridImport.ink : state.gridW < -50 ? Palette.gridExport.ink : Web.textStrong)
                    Figure(icon: .battery, label: String(localized: "Batterie"), value: state.battery?.soc.map { fmt.percent($0) } ?? "–",
                           color: state.battery != nil ? Palette.battery.ink : Web.textDim)
                }
            }
            .fixedSize()
        }
    }
}

private struct Figure: View {
    let icon: WebIcon
    let label: String
    let value: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 3) {
                Icon(icon, size: 11)
                Text(label)
            }
            .font(Typo.font(11, .semibold, relativeTo: .caption2))
            .foregroundStyle(Web.textDim)
            Text(value)
                .font(Typo.font(17, .extraBold, relativeTo: .headline))
                .foregroundStyle(color)
                .monospacedDigit()
        }
    }
}

private struct DeviceLine: View {
    let icon: WebIcon
    let color: Color
    let text: String

    var body: some View {
        HStack(spacing: 4) {
            Icon(icon, size: 12).foregroundStyle(color)
            Text(text).font(Typo.font(12, .semibold, relativeTo: .caption)).foregroundStyle(Web.text).lineLimit(1)
        }
    }
}

private struct SocLine: View {
    let soc: Double

    var body: some View {
        HStack(spacing: 6) {
            Icon(.battery, size: 12).foregroundStyle(Palette.battery.fill)
            ProgressView(value: min(1, max(0, soc / 100)))
                .tint(Palette.battery.fill)
            Text(Fmt().percent(soc))
                .font(Typo.font(11, .semibold, relativeTo: .caption2))
                .foregroundStyle(Web.textDim)
                .monospacedDigit()
        }
    }
}

private struct CircularView: View {
    let state: WidgetState

    var body: some View {
        if let soc = state.battery?.soc {
            Gauge(value: min(1, max(0, soc / 100))) {
                Icon(.battery, size: 12)
            } currentValueLabel: {
                Text("\(Int(soc.rounded()))").monospacedDigit()
            }
            .gaugeStyle(.accessoryCircular)
        } else {
            VStack(spacing: 0) {
                Icon(.sun, size: 14)
                Text(Fmt().wattsValue(state.pvW)).font(.system(size: 14, weight: .bold)).monospacedDigit()
                Text(Fmt().wattsUnit(state.pvW)).font(.system(size: 9, weight: .semibold))
            }
        }
    }
}

private struct RectangularView: View {
    let state: WidgetState

    var body: some View {
        let fmt = Fmt()
        VStack(alignment: .leading, spacing: 1) {
            Text(state.headline).font(.headline).lineLimit(1)
            Text("☀ \(fmt.watts(state.pvW))").font(.subheadline).monospacedDigit()
            Text(GridText.short(state.gridW, fmt: fmt)).font(.caption).lineLimit(1)
        }
        .widgetAccentable()
    }
}

private struct EmptyStateView: View {
    let message: String

    var body: some View {
        VStack(spacing: 8) {
            BrandMark(size: 32)
            Text(message)
                .font(Typo.font(13, .semibold, relativeTo: .footnote))
                .foregroundStyle(Web.textDim)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
