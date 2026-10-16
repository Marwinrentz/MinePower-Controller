import Charts
import MinePowerKit
import SwiftUI

/// Diagramme wie `frontend/src/components/charts.tsx`, mit Swift Charts.
///
/// Gleiche Reihen, Farben und Glättung; Achsen und Raster in Linienfarbe.

/// Leistungsverlauf: PV (Fläche), Haus, Netz, Batterie.
struct PowerHistoryChart: View {
    var showsHeader = true
    @Environment(AppSession.self) private var session
    @State private var range: HistoryRange = .day
    @State private var data: Resource<HistorySeries>?

    static let ranges: [HistoryRange] = [.sixHours, .day, .week]

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            HStack {
                if showsHeader { Text("Verlauf").font(Typo.title3).foregroundStyle(Web.textStrong) }
                Spacer()
                SmallSegment(options: Self.ranges.map { ($0, $0.rawValue) }, selection: $range)
            }
            chart
                .frame(height: 300)
        }
        .task(id: range) {
            let api = session.api
            let range = range
            let resource = Resource { try await api.history(range, fields: ["pv_power", "house_power", "grid_power", "battery_power"]) }
            data = resource
            while !Task.isCancelled {
                await resource.reload()
                try? await Task.sleep(for: .seconds(60))
            }
        }
    }

    @ViewBuilder
    private var chart: some View {
        if let series = data?.value, !series.isEmpty {
            Chart {
                ForEach(series["pv_power"] ?? []) { point in
                    AreaMark(x: .value("Zeit", point.time), y: .value("W", point.value), series: .value("Reihe", "PV"))
                        .foregroundStyle(Palette.pv.fill.opacity(0.18))
                        .interpolationMethod(.catmullRom)
                }
                line(series["pv_power"], name: "PV", color: Palette.pv.fill, width: 2)
                line(series["house_power"], name: String(localized: "Haus"), color: Palette.house.fill, width: 1.6)
                line(series["grid_power"], name: String(localized: "Netz"), color: Palette.gridImport.fill, width: 1.6)
                line(series["battery_power"], name: String(localized: "Batterie"), color: Palette.battery.fill, width: 1.6)
            }
            .chartForegroundStyleScale([
                "PV": Palette.pv.fill, String(localized: "Haus"): Palette.house.fill,
                String(localized: "Netz"): Palette.gridImport.fill, String(localized: "Batterie"): Palette.battery.fill,
            ])
            .chartLegend(position: .top, alignment: .leading)
            .webAxes(unit: "W")
        } else if let error = data?.error {
            ChartEmpty(text: error.errorDescription ?? "")
        } else if data?.value != nil {
            ChartEmpty(text: String(localized: "Noch keine Werte in diesem Zeitraum"))
        } else {
            SkeletonCards(count: 1, height: 300)
        }
    }

    @ChartContentBuilder
    private func line(_ points: [HistoryPoint]?, name: String, color: Color, width: CGFloat) -> some ChartContent {
        ForEach(points ?? []) { point in
            LineMark(x: .value("Zeit", point.time), y: .value("W", point.value), series: .value("Reihe", name))
                .foregroundStyle(by: .value("Reihe", name))
                .lineStyle(StrokeStyle(lineWidth: width))
                .interpolationMethod(.catmullRom)
        }
    }
}

/// `.chart-empty`
struct ChartEmpty: View {
    let text: String
    var body: some View {
        Text(text)
            .font(Typo.subheadline)
            .foregroundStyle(Web.textDim)
            .multilineTextAlignment(.center)
            .frame(maxWidth: 340)
            .frame(maxWidth: .infinity, minHeight: 200)
    }
}

/// `.segment` (klein): kompakte Auswahl wie die Zeitraum-Schalter im Web.
struct SmallSegment<Value: Hashable>: View {
    let options: [(Value, String)]
    @Binding var selection: Value

    var body: some View {
        HStack(spacing: 3) {
            ForEach(options, id: \.0) { value, label in
                let active = value == selection
                Button {
                    selection = value
                } label: {
                    Text(label)
                        .font(Typo.subheadlineMedium)
                        .foregroundStyle(active ? Web.textStrong : Web.textDim)
                        .padding(.horizontal, Space.m)
                        .frame(minHeight: 36)
                        .background {
                            if active {
                                RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Web.surface)
                                    .shadow(color: Web.shadow, radius: 1, y: 1)
                            }
                        }
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(active ? .isSelected : [])
            }
        }
        .padding(3)
        .background(Web.surface3, in: .rect(cornerRadius: Radius.medium, style: .continuous))
        .sensoryFeedback(.selection, trigger: selection)
    }
}

extension View {
    /// Achsen wie im Web: Zeitachse mit Linienfarbe, Werteachse mit Rasterlinien.
    func webAxes(unit: String) -> some View {
        chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 5)) { _ in
                AxisValueLabel().font(Typo.caption2).foregroundStyle(Web.textDim)
            }
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 1)).foregroundStyle(Web.line)
                AxisValueLabel {
                    if let v = value.as(Double.self) {
                        Text(unit == "W" ? Fmt().watts(v) : "\(Fmt().number(v)) \(unit)")
                            .font(Typo.caption2).foregroundStyle(Web.textDim)
                    }
                }
            }
        }
    }
}
