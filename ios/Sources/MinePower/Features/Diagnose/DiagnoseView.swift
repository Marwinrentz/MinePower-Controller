import MinePowerKit
import SwiftUI

/// Diagnose – Portierung von `frontend/src/pages/Diagnose.tsx`.
///
/// Befunde mit Ursache und konkretem Schritt, Live-Zustand der Geräte samt
/// Batteriebefehlen, Regelgüte (Totband, Wetter, Verschenkt) und das
/// Ereignisprotokoll mit Filtern und Suche.
struct DiagnoseView: View {
    enum Tab: String, CaseIterable, Identifiable {
        case findings, devices, regulation, events
        var id: String { rawValue }

        var title: String {
            switch self {
            case .findings: String(localized: "Befunde")
            case .devices: String(localized: "Geräte")
            case .regulation: String(localized: "Regelung")
            case .events: String(localized: "Ereignisse")
            }
        }

        var icon: WebIcon {
            switch self {
            case .findings: .pulse
            case .devices: .devices
            case .regulation: .auto
            case .events: .events
            }
        }
    }

    @State private var tab: Tab = .findings

    var body: some View {
        Page {
            SubNav(items: Tab.allCases.map { ($0, $0.title, $0.icon) }, selection: $tab)
            switch tab {
            case .findings: FindingsTab()
            case .devices: DevicesTab()
            case .regulation: RegulationTab()
            case .events: EventsTab()
            }
        }
        .navigationTitle("Diagnose")
        .toolbar { BrandToolbar() }
    }
}

/// `.subnav`: waagerechte Reiter als Kapseln mit Symbol.
struct SubNav<Value: Hashable>: View {
    let items: [(Value, String, WebIcon)]
    @Binding var selection: Value
    @Environment(\.horizontalSizeClass) private var sizeClass

    var body: some View {
        // Breit (iPad) umbrechend wie `.subnav` ab 1024 px im Web, sonst wischbar.
        Group {
            if sizeClass == .regular {
                FlowLayout(spacing: Space.s) { chips }
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: Space.s) { chips }
                        .padding(.vertical, 2)
                }
            }
        }
        .sensoryFeedback(.selection, trigger: selection)
    }

    private var chips: some View {
        ForEach(items, id: \.0) { value, title, icon in
            let active = value == selection
            Button {
                selection = value
            } label: {
                HStack(spacing: Space.s) {
                    Icon(icon, size: 18)
                    Text(title).font(Typo.bodyMedium)
                }
                .foregroundStyle(active ? Palette.accent : Web.text)
                .padding(.horizontal, Space.l)
                .frame(minHeight: 44)
                .background(active ? Web.accentSoft : Web.surface, in: .capsule)
                .overlay(Capsule().strokeBorder(active ? Palette.accent : Web.line, lineWidth: 1))
                .contentShape(.hoverEffect, .capsule)
                .hoverEffect(.highlight)
            }
            .buttonStyle(.plain)
            .accessibilityAddTraits(active ? .isSelected : [])
        }
    }
}

// MARK: - Befunde

private struct FindingsTab: View {
    @Environment(AppSession.self) private var session
    @State private var range = "7d"
    @State private var findings: [Finding]?
    @State private var share: ShareFile?

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            HStack {
                SmallSegment(options: [("24h", "24 h"), ("7d", String(localized: "7 Tage")), ("30d", String(localized: "30 Tage"))],
                             selection: $range)
                Spacer()
                if session.canConfigure {
                    Button {
                        Task { await report() }
                    } label: {
                        HStack(spacing: 6) { Icon(.download, size: 16); Text("Bericht") }
                    }
                    .buttonStyle(.web(.ghost, size: .small, fullWidth: false))
                    .accessibilityHint(Text("Alle Daten des Zeitraums als JSON"))
                }
            }
            if let findings {
                let problems = findings.filter { $0.level != .ok }
                let fine = findings.filter { $0.level == .ok }
                if problems.isEmpty {
                    Card { EmptyState(icon: .check, title: "Alles in Ordnung", message: "Keine Auffälligkeiten im gewählten Zeitraum.") }
                } else {
                    ForEach(problems) { FindingCard(finding: $0) }
                }
                ForEach(fine) { FindingCard(finding: $0) }
            } else {
                SkeletonCards(count: 2, height: 110)
                Text("Wird ausgewertet …").font(Typo.subheadline).foregroundStyle(Web.textDim)
            }
        }
        .sheet(item: $share) { ShareSheet(url: $0.url) }
        .task(id: range) {
            findings = nil
            do {
                findings = try await session.api.findings(range: range).findings
            } catch {
                findings = []
                session.handle(error)
            }
        }
    }

    private func report() async {
        do {
            let data = try await session.api.diagnosticReport(range: range)
            share = try ShareFile.write(data, name: "minepower_diagnose_\(range).json")
        } catch {
            session.handle(error)
        }
    }
}

/// `.finding`: Karte mit farbiger linker Kante je Stufe.
private struct FindingCard: View {
    let finding: Finding

    var body: some View {
        let tone = Self.tone(finding.level)
        VStack(alignment: .leading, spacing: Space.s) {
            HStack(spacing: Space.s) {
                Icon(finding.level == .ok ? .check : finding.level == .info ? .info : .warning, size: 20)
                    .foregroundStyle(tone.fill)
                Text(finding.title).font(Typo.headline).foregroundStyle(Web.textStrong)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if let count = finding.count, count > 1 { Badge(text: "\(count)×") }
                if finding.level != .ok { Badge(text: Self.levelName(finding.level), tone: tone) }
            }
            if finding.level == .ok {
                Text(finding.detail).font(Typo.body).foregroundStyle(Web.text)
            } else {
                (Text("Warum: ").font(Typo.headline) + Text(finding.detail).font(Typo.body))
                    .foregroundStyle(Web.text)
                if let fix = finding.fix {
                    (Text("Was tun: ").font(Typo.headline) + Text(fix).font(Typo.body))
                        .foregroundStyle(Web.textStrong)
                        .padding(Space.m)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Web.surface2, in: .rect(cornerRadius: Radius.medium, style: .continuous))
                }
            }
        }
        .padding(Space.l)
        .padding(.leading, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Web.surface, in: .rect(cornerRadius: Radius.large, style: .continuous))
        .overlay(alignment: .leading) {
            UnevenRoundedRectangle(cornerRadii: .init(topLeading: Radius.large, bottomLeading: Radius.large))
                .fill(tone.fill).frame(width: 5)
        }
        .overlay(RoundedRectangle(cornerRadius: Radius.large, style: .continuous).strokeBorder(Web.line, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }

    static func tone(_ level: Finding.Level) -> DomainColor {
        switch level {
        case .error: Palette.error
        case .warn: Palette.warn
        case .info: Palette.info
        case .ok: Palette.ok
        }
    }

    static func levelName(_ level: Finding.Level) -> String {
        switch level {
        case .error: String(localized: "Fehler")
        case .warn: String(localized: "Warnung")
        case .info: String(localized: "Hinweis")
        case .ok: String(localized: "In Ordnung")
        }
    }
}

// MARK: - Geräte

/// Diagnose-Daten alle 5 s nachladen, solange der Reiter sichtbar ist.
private struct DiagnosticsLoader<Content: View>: View {
    @Environment(AppSession.self) private var session
    @State private var data: JSONObject?
    @ViewBuilder var content: (JSONObject) -> Content

    var body: some View {
        Group {
            if let data { content(data) } else { SkeletonCards(count: 2, height: 160) }
        }
        .task {
            while !Task.isCancelled {
                if let fresh = try? await session.api.diagnostics() { data = fresh }
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }
}

private struct DevicesTab: View {
    var body: some View {
        DiagnosticsLoader { diag in
            let devices = diag["devices"]?.array?.compactMap(\.object) ?? []
            let writes = diag.object("battery_control")?["devices"]?.array?.compactMap(\.object) ?? []
            VStack(alignment: .leading, spacing: Space.l) {
                ForEach(devices.indices, id: \.self) { index in DiagDeviceCard(device: devices[index]) }
                ForEach(writes.indices, id: \.self) { index in WritesCard(battery: writes[index]) }
            }
        }
    }
}

private struct DiagDeviceCard: View {
    let device: JSONObject

    var body: some View {
        let online = device.bool("online") ?? false
        let lastError = device.string("last_error")
        let sent = device.object("sent") ?? [:]
        Card {
            VStack(alignment: .leading, spacing: Space.m) {
                HStack(spacing: Space.s) {
                    Circle().fill(online ? Palette.ok.fill : lastError != nil ? Palette.error.fill : Web.textDim).frame(width: 9, height: 9)
                    Text(device.string("name") ?? "–").font(Typo.headline).foregroundStyle(Web.textStrong)
                    Text(device.string("driver_id") ?? "").font(Typo.subheadline).foregroundStyle(Web.textDim).lineLimit(1)
                    Spacer()
                    Badge(text: online ? String(localized: "erreichbar") : String(localized: "nicht erreichbar"),
                          tone: online ? Palette.ok : Palette.error)
                }
                Grid(alignment: .leading, horizontalSpacing: Space.l, verticalSpacing: 6) {
                    row("Letzter Messwert", Fmt().ago(device.string("last_seen").flatMap(BackendDate.parse)))
                    if let lastError { row("Letzter Fehler", lastError, error: true) }
                    if let command = device.string("command_error") { row("Befehl nicht übernommen", command, error: true) }
                    if let reason = device.object("decision")?.string("reason") { row("Entscheidung", reason) }
                    if !sent.isEmpty {
                        row("Zuletzt gesendet", sent.keys.sorted().map { "\($0): \(sent[$0]?.string ?? "–")" }.joined(separator: " · "))
                    }
                }
            }
        }
    }

    private func row(_ label: LocalizedStringKey, _ value: String, error: Bool = false) -> some View {
        GridRow(alignment: .firstTextBaseline) {
            Text(label).font(Typo.subheadline).foregroundStyle(Web.textDim)
            Text(value).font(Typo.subheadline).foregroundStyle(error ? Palette.error.ink : Web.textStrong)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct WritesCard: View {
    let battery: JSONObject

    var body: some View {
        let writes = (battery["writes"]?.array?.compactMap(\.object) ?? []).reversed()
        Card {
            VStack(alignment: .leading, spacing: Space.m) {
                CardHead("Befehle an die Batterie – \(battery.string("name") ?? "")",
                         subtitle: String(localized: "Letzte Schreibzugriffe: Register, Wert, Antwort"))
                if writes.isEmpty {
                    Text("Noch keine Befehle gesendet.").font(Typo.subheadline).foregroundStyle(Web.textDim)
                } else {
                    ForEach(Array(writes.enumerated()), id: \.offset) { _, write in
                        HStack(spacing: Space.m) {
                            Text(write.string("time").flatMap(BackendDate.parse).map { Fmt().dateTime($0) } ?? "–")
                                .font(Typo.subheadline).monospacedDigit().foregroundStyle(Web.textDim)
                            Text("R \(write.int("register") ?? 0) = \(write.string("value") ?? "–") → \(write.string("readback") ?? "–")")
                                .font(Typo.subheadline).monospacedDigit().foregroundStyle(Web.textStrong)
                            Spacer()
                            Badge(text: write.bool("ok") == true ? String(localized: "übernommen") : String(localized: "abgelehnt"),
                                  tone: write.bool("ok") == true ? Palette.ok : Palette.error)
                        }
                    }
                }
            }
        }
    }
}

// MARK: - Regelung

private struct RegulationTab: View {
    var body: some View {
        DiagnosticsLoader { diag in
            let fmt = Fmt()
            let config = diag.object("regulation_config") ?? [:]
            let adaptive = config.bool("adaptive_deadband") != false
            let minimum = config.double("deadband_min_w") ?? 40
            let maximum = config.double("deadband_max_w") ?? 400
            let effective = diag.double("deadband_effective_w") ?? config.double("deadband_w") ?? 100
            let interval = config.double("interval_s") ?? 10
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 260), spacing: Space.l, alignment: .top)], spacing: Space.l) {
                BigNumberCard(title: "Totband", value: "±\(fmt.watts(effective))") {
                    if adaptive {
                        RangeMeter(minimum: minimum, maximum: maximum, value: effective)
                        Text("Totband ±\(fmt.watts(effective)), automatisch \(fmt.watts(minimum))–\(fmt.watts(maximum))")
                            .font(Typo.subheadline).foregroundStyle(Web.textDim)
                    } else {
                        Text("Fest eingestellt: ±\(fmt.watts(effective)).").font(Typo.subheadline).foregroundStyle(Web.textDim)
                    }
                }
                BigNumberCard(title: "Wetterlage", value: diag.string("weather") ?? "–") {
                    Text("Unruhe-Index \(fmt.number(diag.double("volatility_index") ?? 0, digits: 2)) (0–1)")
                        .font(Typo.subheadline).foregroundStyle(Web.textDim)
                }
                BigNumberCard(title: "Netzpunkt (geglättet)", value: fmt.watts(diag.double("grid_smoothed") ?? 0)) { EmptyView() }
                BigNumberCard(title: "Verschenkt gerade", value: fmt.watts(diag.double("waste_w") ?? 0),
                              color: (diag.double("waste_w") ?? 0) > 100 ? Palette.waste.ink : nil) {
                    if let reason = diag.string("waste_reason") {
                        Text(reason).font(Typo.subheadline).foregroundStyle(Web.textDim)
                    }
                }
                BigNumberCard(title: "Lückenfüller", value: fmt.watts(diag.double("gap_filled_w") ?? 0)) {
                    Text("Restüberschuss an modulierende Lasten").font(Typo.subheadline).foregroundStyle(Web.textDim)
                }
                BigNumberCard(title: "Regeltakt", value: "\(fmt.number(interval)) s") {
                    Text("Regeltakt \(fmt.number(interval)) s").font(Typo.subheadline).foregroundStyle(Web.textDim)
                }
            }
        }
    }
}

private struct BigNumberCard<Extra: View>: View {
    let title: LocalizedStringKey
    let value: String
    var color: Color?
    @ViewBuilder var extra: Extra

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: Space.s) {
                Text(title).font(Typo.title3).foregroundStyle(Web.textStrong)
                Text(value).font(Typo.largeTitle).monospacedDigit().foregroundStyle(color ?? Web.textStrong)
                extra
            }
        }
    }
}

/// `.range-meter`: Spanne Grün → Warnfarbe mit Punkt am aktuellen Wert.
private struct RangeMeter: View {
    let minimum: Double
    let maximum: Double
    let value: Double

    var body: some View {
        let position = maximum > minimum ? max(0, min(1, (value - minimum) / (maximum - minimum))) : 0.5
        VStack(spacing: 4) {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(LinearGradient(colors: [Palette.pv.fill, Palette.warn.fill], startPoint: .leading, endPoint: .trailing))
                        .opacity(0.5).frame(height: 8)
                    Circle().fill(Web.surface).frame(width: 22, height: 22)
                        .overlay(Circle().strokeBorder(Web.textStrong, lineWidth: 3))
                        .offset(x: geo.size.width * position - 11)
                        .animation(.smooth(duration: 0.42), value: position)
                }
            }
            .frame(height: 22)
            HStack {
                Text(Fmt().watts(minimum))
                Spacer()
                Text(Fmt().watts(maximum))
            }
            .font(Typo.caption2).foregroundStyle(Web.textDim)
        }
        .accessibilityHidden(true)
    }
}

// MARK: - Ereignisse

private struct EventsTab: View {
    @Environment(AppSession.self) private var session
    @State private var filter = "all"
    @State private var query = ""
    @State private var limit = 200
    @State private var events: [AppEvent]?

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            ScrollView(.horizontal, showsIndicators: false) {
                SmallSegment(options: [("all", String(localized: "Alle")), ("warn", String(localized: "Warnungen")),
                                       ("control", String(localized: "Regelung")), ("device", String(localized: "Geräte")),
                                       ("waste", String(localized: "Verschenkt")), ("config", String(localized: "Einstellungen"))],
                             selection: $filter)
            }
            AppTextField(title: "Suchen …", text: $query)
                .textInputAutocapitalization(.never)
            if let events {
                let groups = Self.groups(events.filter { query.isEmpty || $0.message.localizedCaseInsensitiveContains(query) })
                if groups.isEmpty {
                    Card { EmptyState(icon: .events, title: "Keine Ereignisse.") }
                } else {
                    ForEach(groups, id: \.day) { group in
                        VStack(alignment: .leading, spacing: Space.s) {
                            Text(group.day).font(Typo.subheadlineMedium).foregroundStyle(Web.textDim)
                            VStack(spacing: 0) {
                                ForEach(group.items) { event in
                                    EventRow(event: event)
                                    if event.id != group.items.last?.id { Divider().overlay(Web.line) }
                                }
                            }
                            .padding(.vertical, Space.xs)
                            .background(Web.surface, in: .rect(cornerRadius: Radius.large, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: Radius.large, style: .continuous).strokeBorder(Web.line, lineWidth: 1))
                        }
                    }
                    if events.count >= limit {
                        Button("Mehr laden") { limit += 300 }
                            .buttonStyle(.web(.ghost))
                    }
                }
            } else {
                SkeletonCards(count: 1, height: 240)
            }
        }
        .task(id: "\(filter)-\(limit)") {
            do {
                events = try await session.api.events(limit: limit,
                                                      category: filter == "all" || filter == "warn" ? nil : filter,
                                                      level: filter == "warn" ? "warning" : nil)
            } catch {
                events = []
            }
        }
    }

    static func groups(_ events: [AppEvent]) -> [(day: String, items: [AppEvent])] {
        var out: [(day: String, items: [AppEvent])] = []
        for event in events {
            let day = event.time.formatted(.dateTime.weekday(.wide).day().month(.wide))
            if out.last?.day == day { out[out.count - 1].items.append(event) } else { out.append((day, [event])) }
        }
        return out
    }
}

private struct EventRow: View {
    let event: AppEvent

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.m) {
            Text(Fmt().clock(event.time)).font(Typo.subheadline).monospacedDigit().foregroundStyle(Web.textDim)
                .frame(width: 48, alignment: .leading)
            Circle().fill(event.level == "warning" ? Palette.warn.fill : event.level == "error" ? Palette.error.fill : Web.lineStrong)
                .frame(width: 8, height: 8)
            Text(event.message).font(Typo.body).foregroundStyle(Web.text).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: Space.s)
            Text(event.category).font(Typo.caption2).foregroundStyle(Web.textDim)
        }
        .padding(.horizontal, Space.l)
        .padding(.vertical, Space.s)
        .accessibilityElement(children: .combine)
    }
}
