import Charts
import MinePowerKit
import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

/// Statistik – Portierung von `frontend/src/pages/Statistics.tsx`.
///
/// Leitzahlen Autarkie und Verschenkt, darunter sechs weitere in einem Raster
/// mit Haarlinien; Energiebilanz je Tag, verschenkte Leistung, Ladevorgänge
/// mit CSV-Export und (für Admins) der Diagnosebericht.
struct StatisticsView: View {
    @Environment(AppSession.self) private var session
    @State private var range: StatsRange = .month
    @State private var stats: Statistics?
    /// Zeitraum, zu dem `stats` gehört (nach einem Fehler beim Wechsel nicht die alten Zahlen zeigen).
    @State private var statsRange: StatsRange?
    @State private var sessions: [ChargeSession]?
    @State private var loadError: APIError?
    @State private var share: ShareFile?
    @State private var busy = false

    var body: some View {
        Page(refresh: { await load() }) {
            ScrollView(.horizontal, showsIndicators: false) {
                SmallSegment(options: StatsRange.allCases.map { ($0, $0.title) }, selection: $range)
            }
            if let stats, statsRange == range {
                Figures(stats: stats)
            } else if let loadError {
                Card { ErrorState(error: loadError) { await load() } }
            } else {
                SkeletonCards(count: 1, height: 220)
            }
            sectionLabel("Verlauf")
            let columns = [GridItem(.adaptive(minimum: 340), spacing: Space.l, alignment: .top)]
            LazyVGrid(columns: columns, spacing: Space.l) {
                Card {
                    VStack(alignment: .leading, spacing: Space.m) {
                        Text("kWh").font(Typo.title3).foregroundStyle(Web.textStrong)
                        EnergyBalanceChart(range: range == .day ? .week : range)
                    }
                }
                Card {
                    VStack(alignment: .leading, spacing: Space.m) {
                        CardHead("Verschenkt", subtitle: String(localized: "Einspeisung über dem Sollwert ohne steuerbare Last"))
                        WasteChart(range: range.historyRange)
                    }
                }
            }
            HStack {
                sectionLabel("Ladevorgänge")
                Spacer()
                Button {
                    Task { await exportCSV() }
                } label: {
                    HStack(spacing: 6) { Icon(.download, size: 16); Text("CSV herunterladen") }
                }
                .buttonStyle(.web(.normal, size: .small, fullWidth: false))
                .disabled(busy)
            }
            SessionsCard(sessions: sessions)
        }
        .navigationTitle("Statistik")
        .toolbar {
            BrandToolbar()
            if session.canConfigure {
                ToolbarItem(placement: .secondaryAction) {
                    Button {
                        Task { await exportReport() }
                    } label: {
                        Label("Diagnosebericht (JSON)", web: .download)
                    }
                }
            }
        }
        .sheet(item: $share) { file in ShareSheet(url: file.url) }
        .task(id: range) { await load() }
    }

    private func sectionLabel(_ text: LocalizedStringKey) -> some View {
        Text(text).font(Typo.subheadlineMedium).foregroundStyle(Web.textDim)
    }

    private func load() async {
        let requested = range
        do {
            async let s = session.api.statistics(requested)
            async let list = session.api.sessions(limit: 50)
            stats = try await s
            statsRange = requested
            sessions = (try? await list) ?? sessions ?? []
            loadError = nil
        } catch let error as APIError {
            loadError = error
        } catch {}
    }

    private func exportCSV() async {
        busy = true
        defer { busy = false }
        do {
            let csv = try await session.api.sessionsCSV(range)
            share = try ShareFile.write(Data(csv.utf8), name: "minepower_sessions_\(range.rawValue).csv")
        } catch {
            session.handle(error)
        }
    }

    private func exportReport() async {
        busy = true
        defer { busy = false }
        do {
            let data = try await session.api.diagnosticReport(range: range.rawValue)
            share = try ShareFile.write(data, name: "minepower_diagnose_\(range.rawValue).json")
        } catch {
            session.handle(error)
        }
    }
}

extension StatsRange {
    var title: String {
        switch self {
        case .day: String(localized: "24 Stunden")
        case .week: String(localized: "7 Tage")
        case .month: String(localized: "30 Tage")
        case .year: String(localized: "1 Jahr")
        }
    }

    var historyRange: HistoryRange {
        switch self {
        case .day: .day
        case .week: .week
        case .month: .month
        case .year: .year
        }
    }
}

// MARK: - Kennzahlen

/// `.figures`: Raster mit Haarlinien; Autarkie und Verschenkt sind die Leitzahlen.
private struct Figures: View {
    let stats: Statistics

    var body: some View {
        let fmt = Fmt()
        Card(padding: 0) {
            Grid(horizontalSpacing: 0, verticalSpacing: 0) {
                GridRow {
                    FigureCell(label: "Autarkiegrad", value: "\(fmt.number(stats.autarkyPct)) %", lead: true,
                               hint: String(localized: "Eigenverbrauch \(fmt.number(stats.selfConsumptionPct)) %"))
                    FigureCell(label: "Verschenkt", value: fmt.kwh(stats.wastedKwh ?? 0), lead: true,
                               hint: "\(fmt.number(stats.wastedPct ?? 0)) % · " + String(localized: "entspricht \(fmt.euro(stats.wastedValueEur ?? 0))"),
                               color: (stats.wastedKwh ?? 0) > 0 ? Palette.waste.ink : nil)
                }
                GridRow {
                    FigureCell(label: "Ersparnis", value: fmt.euro(stats.savingsEur))
                    FigureCell(label: "PV-Ertrag", value: fmt.kwh(stats.pvKwh), color: Palette.pv.ink)
                }
                GridRow {
                    FigureCell(label: "Netzbezug", value: fmt.kwh(stats.gridImportKwh), color: Palette.gridImport.ink)
                    FigureCell(label: "Einspeisung", value: fmt.kwh(stats.gridExportKwh), color: Palette.gridExport.ink)
                }
                GridRow {
                    FigureCell(label: "Geladen (Auto)", value: fmt.kwh(stats.chargedKwh),
                               hint: String(localized: "Solaranteil: \(fmt.number(stats.chargedSolarPct)) %"), color: Palette.car.ink)
                    FigureCell(label: "CO₂ vermieden", value: fmt.kilograms(stats.co2SavedKg))
                }
            }
        }
    }
}

private struct FigureCell: View {
    let label: LocalizedStringKey
    let value: String
    var lead = false
    var hint: String?
    var color: Color?

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(Typo.subheadlineMedium).foregroundStyle(Web.textDim)
            Text(value).font(lead ? Typo.largeTitle : Typo.title).foregroundStyle(color ?? Web.textStrong)
                .monospacedDigit().lineLimit(1).minimumScaleFactor(0.7)
            if let hint { Text(hint).font(Typo.caption2).foregroundStyle(Web.textDim) }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .padding(Space.l)
        .overlay(alignment: .trailing) { Rectangle().fill(Web.line).frame(width: 1) }
        .overlay(alignment: .bottom) { Rectangle().fill(Web.line).frame(height: 1) }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Ladevorgänge

private struct SessionsCard: View {
    let sessions: [ChargeSession]?

    var body: some View {
        Card(padding: sessions?.isEmpty == false ? Space.s : nil) {
            if let sessions {
                if sessions.isEmpty {
                    EmptyState(icon: .car, title: "Noch keine Ladevorgänge aufgezeichnet.")
                } else {
                    VStack(spacing: 0) {
                        ForEach(sessions) { row in
                            SessionRow(row: row)
                            if row.id != sessions.last?.id { Divider().overlay(Web.line) }
                        }
                    }
                }
            } else {
                SkeletonCards(count: 2, height: 56)
            }
        }
    }
}

private struct SessionRow: View {
    let row: ChargeSession

    var body: some View {
        let fmt = Fmt()
        HStack(spacing: Space.m) {
            VStack(alignment: .leading, spacing: 1) {
                Text(fmt.dateTime(row.startedAt)).font(Typo.bodyMedium).foregroundStyle(Web.textStrong)
                Text(row.endedAt.map { String(localized: "bis \(fmt.clock($0))") } ?? String(localized: "läuft …"))
                    .font(Typo.subheadline).foregroundStyle(Web.textDim)
            }
            Spacer(minLength: Space.s)
            VStack(alignment: .trailing, spacing: 1) {
                Text(fmt.kwh(row.energyKwh)).font(Typo.bodyMedium).foregroundStyle(Web.textStrong).monospacedDigit()
                HStack(spacing: Space.s) {
                    Text("\(Int((row.solarShare * 100).rounded())) % ☀").foregroundStyle(Palette.pv.ink)
                    Text(fmt.euro(row.costEur)).foregroundStyle(Web.textDim)
                    if let rfid = row.rfidTag { Text(rfid).foregroundStyle(Web.textDim) }
                }
                .font(Typo.subheadline).monospacedDigit()
            }
        }
        .padding(.horizontal, Space.m)
        .frame(minHeight: 56)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Diagramme

/// `EnergyBalanceChart`: kWh je Tag, Balken PV/Bezug/Einspeisung nebeneinander.
struct EnergyBalanceChart: View {
    let range: StatsRange
    @Environment(AppSession.self) private var session
    @State private var data: [String: [String: Double]]?

    var body: some View {
        Group {
            if let data, !data.isEmpty {
                let days = data.keys.sorted()
                Chart {
                    ForEach(days, id: \.self) { day in
                        let label = String(day.dropFirst(5))
                        ForEach([("pv_power", "PV"), ("grid_import", String(localized: "Bezug")),
                                 ("grid_export", String(localized: "Einspeisung"))], id: \.0) { key, name in
                            BarMark(x: .value("Tag", label), y: .value("kWh", data[day]?[key] ?? 0))
                                .foregroundStyle(by: .value("Reihe", name))
                                .position(by: .value("Reihe", name))
                                .clipShape(UnevenRoundedRectangle(cornerRadii: .init(topLeading: 3, topTrailing: 3)))
                        }
                    }
                }
                .chartForegroundStyleScale(["PV": Palette.pv.fill, String(localized: "Bezug"): Palette.gridImport.fill,
                                            String(localized: "Einspeisung"): Palette.gridExport.fill])
                .chartLegend(position: .top, alignment: .leading)
                .chartYAxis {
                    AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in
                        AxisGridLine().foregroundStyle(Web.line)
                        AxisValueLabel { if let v = value.as(Double.self) { Text(Fmt().number(v)).font(Typo.caption2) } }
                    }
                }
                .chartXAxis {
                    AxisMarks(values: .automatic(desiredCount: 6)) { _ in
                        AxisValueLabel().font(Typo.caption2).foregroundStyle(Web.textDim)
                    }
                }
                .frame(height: 260)
            } else if data != nil {
                ChartEmpty(text: String(localized: "Keine Verlaufsdaten"))
            } else {
                SkeletonCards(count: 1, height: 260)
            }
        }
        .task(id: range) {
            data = (try? await session.api.energyBalance(range)) ?? [:]
        }
    }
}

/// `WasteChart`: verschenkte Leistung (Fläche) und Überschuss (gestrichelt).
struct WasteChart: View {
    let range: HistoryRange
    @Environment(AppSession.self) private var session
    @State private var data: HistorySeries?

    var body: some View {
        Group {
            if let data, !data.isEmpty {
                Chart {
                    ForEach(data["waste_power"] ?? []) { p in
                        AreaMark(x: .value("Zeit", p.time), y: .value("W", p.value))
                            .foregroundStyle(Palette.waste.fill.opacity(0.3))
                            .interpolationMethod(.catmullRom)
                        LineMark(x: .value("Zeit", p.time), y: .value("W", p.value), series: .value("Reihe", "Verschenkt"))
                            .foregroundStyle(Palette.waste.fill)
                            .lineStyle(StrokeStyle(lineWidth: 1.8))
                            .interpolationMethod(.catmullRom)
                    }
                    ForEach(data["surplus"] ?? []) { p in
                        LineMark(x: .value("Zeit", p.time), y: .value("W", p.value), series: .value("Reihe", "Überschuss"))
                            .foregroundStyle(Palette.pv.fill)
                            .lineStyle(StrokeStyle(lineWidth: 1.4, dash: [5, 4]))
                            .interpolationMethod(.catmullRom)
                    }
                }
                .webAxes(unit: "W")
                .frame(height: 260)
            } else if data != nil {
                ChartEmpty(text: String(localized: "Keine Verlaufsdaten"))
            } else {
                SkeletonCards(count: 1, height: 260)
            }
        }
        .task(id: range) {
            data = (try? await session.api.history(range, fields: ["waste_power", "surplus"])) ?? [:]
        }
    }
}

// MARK: - Teilen

/// Datei zum Teilen (CSV, Diagnosebericht, Konfiguration).
struct ShareFile: Identifiable {
    let url: URL
    var id: URL { url }

    static func write(_ data: Data, name: String) throws -> ShareFile {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(name)
        try data.write(to: url, options: [.atomic, .completeFileProtection])
        return ShareFile(url: url)
    }
}

#if canImport(UIKit)
/// Systemdialog "Teilen" (Dateien sichern, AirDrop, Mail …).
struct ShareSheet: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
#endif
