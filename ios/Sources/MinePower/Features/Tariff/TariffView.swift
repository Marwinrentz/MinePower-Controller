import Charts
import MinePowerKit
import SwiftUI

/// Tarif – Portierung von `frontend/src/pages/Tariff.tsx`.
///
/// Preis jetzt, nächste günstige Zeit, Preiskurve, Anbieter und Aufschläge,
/// die eine gemeinsame Preisgrenze und wer davon Gebrauch macht.
struct TariffView: View {
    @Environment(AppSession.self) private var session
    @Environment(SettingsEditor.self) private var editor
    @Environment(Router.self) private var router
    @State private var info: TariffInfo?
    @State private var store: DeviceSettingsStore?

    var body: some View {
        let tariff = editor.section("tariff")
        let regulation = editor.section("regulation")
        let provider = tariff.string("provider", "none")
        let fixedLimit = tariff.number("grid_charge_limit_ct", tariff.number("cheap_limit_ct", 15))
        // Automatik an: die vom Server berechnete Grenze (Tiefstpreis der nächsten 24 h).
        let autoShown = tariff.bool("auto_limit", false) && info?.autoLimit == true && info?.autoActive == true
        let limit = autoShown ? (info?.effectiveLimitCt ?? fixedLimit) : fixedLimit
        let prices = info?.prices ?? []
        let now = session.live.snapshot?.priceCt ?? info?.currentCt
        Page(refresh: { await loadPrices(); await editor.load(force: true) }) {
            hero(now: now, limit: limit, provider: provider, prices: prices)
            if !prices.isEmpty {
                Card {
                    VStack(alignment: .leading, spacing: Space.m) {
                        CardHead("Preise der nächsten Stunden", subtitle: String(localized: "Grün: unter der Grenze"))
                        PriceCurve(prices: prices, limit: limit)
                    }
                }
            }
            if session.canConfigure {
                settingsCard(tariff: tariff, provider: provider, limit: fixedLimit, autoShown: autoShown)
            }
            usersCard(regulation: regulation)
        }
        .safeAreaInset(edge: .bottom) { SaveBar() }
        .navigationTitle("Tarif")
        .toolbar { BrandToolbar() }
        .task {
            if store == nil { store = DeviceSettingsStore(api: session.api) }
            await editor.load()
            await store?.devices.load(maxAge: 30)
            while !Task.isCancelled {
                await loadPrices()
                // Automatik an, aber noch nicht wirksam (gerade gespeichert): öfter nachfragen.
                let waiting = editor.section("tariff").bool("auto_limit", false) && info?.autoActive != true
                try? await Task.sleep(for: .seconds(waiting ? 10 : 300))
            }
        }
    }

    private func loadPrices() async {
        if let fresh = try? await session.api.tariff() { info = fresh }
    }

    // MARK: Jetzt

    private func hero(now: Double?, limit: Double, provider: String, prices: [PriceSlot]) -> some View {
        let fmt = Fmt()
        let cheapNow = now.map { $0 <= limit } ?? false
        let window = Self.nextWindow(prices, limit: limit)
        let next: String = {
            if provider == "none" || prices.isEmpty { return String(localized: "Keine Preise. Anbieter einrichten.") }
            if let window, window.now { return String(localized: "Gerade günstig – bis \(fmt.clock(window.to)).") }
            if let window {
                return String(localized: "Nächste günstige Zeit: \(fmt.clock(window.from))–\(fmt.clock(window.to)), ab \(fmt.cents(window.min))")
            }
            return String(localized: "In den nächsten Stunden nicht unter \(fmt.cents(limit)).")
        }()
        return Card {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .center, spacing: Space.xl) { priceNow(now, cheap: cheapNow); side(limit: limit, next: next) }
                VStack(alignment: .leading, spacing: Space.l) { priceNow(now, cheap: cheapNow); side(limit: limit, next: next) }
            }
        }
    }

    private func priceNow(_ now: Double?, cheap: Bool) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Jetzt").font(Typo.subheadlineMedium).foregroundStyle(Web.textDim)
            if let now {
                Text(Fmt().cents(now)).font(Typo.display).monospacedDigit()
                    .foregroundStyle(cheap ? Palette.pv.ink : Web.textStrong)
                Badge(text: cheap ? String(localized: "günstig") : String(localized: "teuer"), tone: cheap ? Palette.ok : Palette.warn)
            } else {
                Text("kein aktueller Preis").font(Typo.title3).foregroundStyle(Web.textDim)
            }
        }
    }

    private func side(limit: Double, next: String) -> some View {
        VStack(alignment: .leading, spacing: Space.s) {
            Text("Grenze \(Fmt().cents(limit))")
                .font(Typo.headline).foregroundStyle(Web.textStrong)
                .padding(.horizontal, Space.m).frame(minHeight: 36)
                .background(Web.surface2, in: .capsule)
                .overlay(Capsule().strokeBorder(Web.line, lineWidth: 1))
            Text(next).font(Typo.body).foregroundStyle(Web.text).fixedSize(horizontal: false, vertical: true)
        }
    }

    static func nextWindow(_ prices: [PriceSlot], limit: Double) -> (now: Bool, from: Date, to: Date, min: Double)? {
        guard let first = prices.firstIndex(where: { $0.priceCt <= limit }) else { return nil }
        var last = first
        var minimum = prices[first].priceCt
        while last + 1 < prices.count, prices[last + 1].priceCt <= limit {
            last += 1
            minimum = min(minimum, prices[last].priceCt)
        }
        return (first == 0, prices[first].start, prices[last].end, minimum)
    }

    // MARK: Einstellungen

    private func settingsCard(tariff: SettingsDraft, provider: String, limit: Double, autoShown: Bool) -> some View {
        let fmt = Fmt()
        let surcharge = tariff.number("grid_fees_ct", 0) + tariff.number("levies_ct", 0) + tariff.number("supplier_margin_ct", 0)
        let vat = tariff.number("vat_pct", 0)
        return Card {
            VStack(alignment: .leading, spacing: Space.l) {
                CardHead("Einstellungen")
                Field(label: "Anbieter") {
                    SegmentGrid(options: [.init(value: "none", title: String(localized: "Keiner")),
                                          .init(value: "tibber", title: "Tibber"), .init(value: "awattar", title: "aWATTar")],
                                selection: provider, minimum: 90) { value in editor.update("tariff") { $0.set("provider", value) } }
                }
                if provider == "tibber" {
                    Field(label: "Tibber-Zugangsschlüssel", help: "developer.tibber.com") {
                        AppTextField(verbatim: tariff.string("tibber_token", "") == secretMask ? String(localized: "gespeichert") : "",
                                     text: editor.secret("tariff", "tibber_token"),
                                     secure: true)
                            .textContentType(nil)
                    }
                    Callout(.ok, String(localized: "Tibber liefert den Endpreis. Aufschläge entfallen."))
                }
                if provider == "awattar" {
                    Field(label: "Region") {
                        SegmentGrid(options: [.init(value: "de", title: String(localized: "Deutschland")),
                                              .init(value: "at", title: String(localized: "Österreich"))],
                                    selection: tariff.string("awattar_region", "de"), minimum: 120) { value in
                            editor.update("tariff") { $0.set("awattar_region", value) }
                        }
                    }
                    Text("Aufschläge auf den Börsenpreis").font(Typo.headline).foregroundStyle(Web.textStrong)
                    Text("Endpreis = Börsenpreis + Aufschläge. Werte laut Stromrechnung.").font(Typo.subheadline).foregroundStyle(Web.textDim)
                    if surcharge == 0, vat == 0 {
                        Callout(.warn, String(localized: "Aufschläge 0: Vergleich mit dem Börsenpreis"))
                    }
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), spacing: Space.l, alignment: .top)], spacing: Space.l) {
                        number("Netzentgelt", key: "grid_fees_ct", unit: "ct/kWh", tariff: tariff)
                        number("Steuern & Umlagen", key: "levies_ct", unit: "ct/kWh", tariff: tariff)
                        number("Marge des Versorgers", key: "supplier_margin_ct", unit: "ct/kWh", tariff: tariff)
                        number("Umsatzsteuer", key: "vat_pct", unit: "%", tariff: tariff)
                    }
                    Text("Beispiel: \(fmt.cents(8)) an der Börse → \(fmt.cents((8 + surcharge) * (1 + vat / 100))) auf der Rechnung")
                        .font(Typo.bodyMedium).foregroundStyle(Web.textStrong)
                        .padding(.horizontal, Space.l).padding(.vertical, Space.m)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Web.surface2, in: .rect(cornerRadius: Radius.medium, style: .continuous))
                }
                let auto = tariff.bool("auto_limit", false)
                ToggleRow(label: "Automatische Grenze",
                          help: String(localized: "Die Grenze liegt immer auf dem tiefsten Preis der nächsten 24 Stunden. Geladen wird genau in diesem günstigsten Zeitraum, egal wie hoch die Preise insgesamt sind."),
                          isOn: Binding(get: { auto }, set: { value in editor.update("tariff") { $0.set("auto_limit", value) } }))
                if auto {
                    Text(autoShown
                         ? String(localized: "Automatisch: tiefster Preis \(fmt.cents(info?.autoLowestCt)), günstig \(fmt.number(info?.autoWindowH ?? 0, digits: 1)) h")
                         : info?.autoLimit != true ? String(localized: "Wird nach dem Speichern berechnet.")
                         : String(localized: "Automatik wartet auf Preise. Bis dahin gilt die feste Grenze."))
                        .font(Typo.subheadlineMedium).foregroundStyle(Web.textStrong)
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 200), spacing: Space.l, alignment: .top)], spacing: Space.l) {
                        Field(label: "Mindestens günstig für",
                              help: String(localized: "Das Fenster unter der Grenze dauert mindestens so lange. 0 = nur der absolut tiefste Preis.")) {
                            NumberField(value: Binding(get: { tariff.number("auto_limit_window_h", 2) },
                                                       set: { value in editor.update("tariff") { $0.set("auto_limit_window_h", value) } }),
                                        range: 0...12, unit: "h", digits: 1)
                        }
                        Field(label: "Toleranz", help: String(localized: "Preise bis so viel über dem Tiefstpreis zählen mit.")) {
                            NumberField(value: Binding(get: { tariff.number("auto_limit_tolerance_ct", 0) },
                                                       set: { value in editor.update("tariff") { $0.set("auto_limit_tolerance_ct", value) } }),
                                        range: 0...20, unit: "ct", digits: 1)
                        }
                    }
                }
                StepSlider(label: auto ? "Feste Grenze (Rückfall)" : "Günstig, wenn unter",
                           value: Binding(get: { limit }, set: { value in editor.update("tariff") { $0.set("grid_charge_limit_ct", value) } }),
                           range: 0...50, step: 0.5, tone: Palette.pv.fill, format: { fmt.cents($0) },
                           help: auto ? String(localized: "Gilt, wenn die Automatik aus ist oder keine Preise für die nächsten Stunden vorliegen.")
                                      : String(localized: "Darunter: Netzladen für 'Sonne + günstig' und Batterie (falls aktiviert)")) { _ in }
                ToggleRow(label: "Bei negativem Preis immer laden", help: String(localized: "Unter 0 ct: Netzladen auch über der Grenze"),
                          isOn: Binding(get: { tariff.bool("charge_on_negative", true) },
                                        set: { value in editor.update("tariff") { $0.set("charge_on_negative", value) } }))
            }
        }
    }

    private func number(_ label: LocalizedStringKey, key: String, unit: String, tariff: SettingsDraft) -> some View {
        Field(label: label) {
            NumberField(value: Binding(get: { tariff.number(key, 0) }, set: { value in editor.update("tariff") { $0.set(key, value) } }),
                        range: 0...100, unit: unit, digits: unit == "%" ? 0 : 2)
        }
    }

    // MARK: Wer lädt günstig

    private func usersCard(regulation: SettingsDraft) -> some View {
        let gridCharge = regulation.bool("battery_grid_charge_enabled", false)
        let gridSoc = regulation.number("battery_grid_charge_soc", 50)
        let users = (store?.devices.value ?? []).filter { $0.category == .wallbox || $0.category == .waterHeater }
        return Card {
            VStack(alignment: .leading, spacing: Space.m) {
                CardHead("Netzladen bei günstigem Preis", subtitle: String(localized: "Reihenfolge: Einstellungen → Reihenfolge"))
                UserRow(icon: .battery, name: String(localized: "Batterie"),
                        state: gridCharge ? String(localized: "lädt bis \(Int(gridSoc)) % aus dem Netz")
                                          : String(localized: "lädt nicht aus dem Netz (ausgeschaltet)")) {
                    if session.canConfigure {
                        Button("Batterie-Einstellungen") { router.showSettings(.battery) }
                            .buttonStyle(.web(.ghost, size: .small, fullWidth: false))
                    }
                }
                ForEach(users) { device in
                    let mode = device.settings.string("mode") ?? "pv_only"
                    let inPrice = mode == "pv_price" || mode == "price"
                    let modeLabel = device.category == .wallbox ? (WallboxMode(rawValue: mode)?.title ?? mode)
                                                                : (WaterMode(rawValue: mode)?.title ?? mode)
                    Divider().overlay(Web.line)
                    UserRow(icon: device.category == .wallbox ? .car : .water, name: device.name,
                            state: inPrice ? String(localized: "nutzt diese Grenze")
                                           : String(localized: "lädt nicht aus dem Netz (\(modeLabel))")) { EmptyView() }
                }
            }
        }
    }
}

private struct UserRow<Trailing: View>: View {
    let icon: WebIcon
    let name: String
    let state: String
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(spacing: Space.m) {
            Icon(icon, size: 20).foregroundStyle(Web.textDim)
            VStack(alignment: .leading, spacing: 1) {
                Text(name).font(Typo.bodyMedium).foregroundStyle(Web.textStrong)
                Text(state).font(Typo.subheadline).foregroundStyle(Web.textDim)
            }
            Spacer(minLength: Space.s)
            trailing
        }
        .frame(minHeight: 48)
    }
}

/// `PriceCurve`: Balken je Zeitfenster, unter der Grenze grün, Grenze gestrichelt.
struct PriceCurve: View {
    let prices: [PriceSlot]
    let limit: Double?

    var body: some View {
        Chart {
            ForEach(Array(prices.enumerated()), id: \.element.id) { index, slot in
                // Je Zeitfenster von Beginn bis Ende (15 oder 60 min), mit kleiner Lücke.
                // Mit `unit: .hour` würden Viertelstunden derselben Stunde gestapelt.
                BarMark(xStart: .value("Beginn", slot.start),
                        xEnd: .value("Ende", slot.end.addingTimeInterval(-Double(slot.minutes) * 60 * 0.18)),
                        y: .value("ct", slot.priceCt))
                    .foregroundStyle(limit.map { slot.priceCt <= $0 } == true ? Palette.pv.fill : Web.lineStrong)
                    .clipShape(UnevenRoundedRectangle(cornerRadii: .init(topLeading: 3, topTrailing: 3)))
                    .annotation(position: .overlay) {
                        if index == 0 {
                            RoundedRectangle(cornerRadius: 3).strokeBorder(Web.textStrong, lineWidth: 1.5)
                        }
                    }
            }
            if let limit {
                RuleMark(y: .value("Grenze", limit))
                    .foregroundStyle(Palette.pv.fill)
                    .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [6, 4]))
                    .annotation(position: .top, alignment: .trailing) {
                        Text(Fmt().cents(limit)).font(Typo.caption2).foregroundStyle(Web.textDim)
                    }
            }
        }
        .chartXAxis {
            AxisMarks(values: .stride(by: .hour, count: 3)) { _ in
                AxisValueLabel(format: .dateTime.hour(.twoDigits(amPM: .omitted)).minute())
                    .font(Typo.caption2).foregroundStyle(Web.textDim)
            }
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in
                AxisGridLine().foregroundStyle(Web.line)
                AxisValueLabel { if let v = value.as(Double.self) { Text("\(Fmt().number(v)) ct").font(Typo.caption2) } }
            }
        }
        .frame(height: 220)
        .accessibilityLabel(Text("Preise der nächsten Stunden"))
    }
}

/// `SaveBar`: schwebende Leiste bei ungespeicherten Einstellungen (Liquid Glass).
struct SaveBar: View {
    @Environment(SettingsEditor.self) private var editor
    @Environment(AppSession.self) private var session

    var body: some View {
        if editor.isDirty {
            HStack(spacing: Space.m) {
                Text("Ungespeicherte Änderungen").font(Typo.bodyMedium).foregroundStyle(Web.textStrong)
                    .lineLimit(2)
                Spacer(minLength: Space.s)
                Button("Verwerfen") { editor.discard() }
                    .buttonStyle(.web(.ghost, size: .small, fullWidth: false))
                Button {
                    Task {
                        if await editor.save() {
                            session.show(Notice(.success, String(localized: "Gespeichert")))
                            try? await session.live.refresh()
                        } else if let error = editor.error {
                            session.handle(error)
                        }
                    }
                } label: {
                    HStack(spacing: 6) {
                        if editor.isSaving { ProgressView().tint(Web.accentText) }
                        Text("Speichern")
                    }
                }
                .buttonStyle(.web(.primary, size: .small, fullWidth: false))
                .disabled(editor.isSaving)
            }
            .padding(.leading, Space.l)
            .padding(.trailing, Space.s)
            .padding(.vertical, Space.s)
            .glassBar(cornerRadius: Radius.card)
            .padding(.horizontal, 10)
            .padding(.bottom, 6)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }
}
