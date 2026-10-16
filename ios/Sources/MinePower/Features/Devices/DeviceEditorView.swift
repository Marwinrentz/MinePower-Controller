import MinePowerKit
import SwiftUI

extension DeviceCategory {
    var title: LocalizedStringKey {
        switch self {
        case .inverter: "Wechselrichter"
        case .meter: "Netzzähler"
        case .wallbox: "Wallbox"
        case .battery: "Batterie"
        case .waterHeater: "Warmwasser"
        case .unknown: "Gerät"
        }
    }

    var symbol: String {
        switch self {
        case .inverter: "sun.max"
        case .meter: "gauge.with.dots.needle.33percent"
        case .wallbox: "ev.charger"
        case .battery: "battery.75percent"
        case .waterHeater: "drop.degreesign"
        case .unknown: "questionmark.square"
        }
    }

    var domain: DomainColor { Palette.category(self) }
}

/// Formular für ein Gerät: Treiber, Name, Felder aus dem Treiber, Test, Speichern.
///
/// Ohne eigene Navigation und ohne eigene Scroll-Ansicht, damit es im
/// Assistenten und in den Einstellungen gleich eingebettet werden kann.
struct DeviceEditorView: View {
    @Bindable var editor: DeviceEditor
    /// Speichern-Knopf zeigen (im Assistenten steuert die untere Leiste).
    var showsSave = true
    var onSaved: (Device) -> Void = { _ in }

    @State private var pickDriver = false

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            driverRow
            if let driver = editor.driver {
                maturity(driver)
                LabeledInput(label: String(localized: "Name"), error: editor.fieldErrors["name"]) {
                    AppTextField(title: "Name", text: $editor.draft.name, identifier: "deviceName")
                        .textInputAutocapitalization(.words)
                }
                ForEach(driver.fields) { field in
                    FieldInput(field: field, editor: editor)
                }
                if editor.draft.category == .wallbox {
                    Toggle(isOn: gridMetered) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Ladepunkt hinter dem Netzzähler").font(Typo.bodyMedium)
                            Text("Aus: Ladeleistung wird zur Netzleistung addiert.")
                                .font(Typo.footnote).foregroundStyle(.secondary)
                        }
                    }
                    .tint(Palette.accent)
                }
                testSection
                if let error = editor.error, editor.fieldErrors.isEmpty {
                    InlineError(error: error)
                }
                if showsSave {
                    Button {
                        Task { if let device = await editor.save() { onSaved(device) } }
                    } label: {
                        HStack {
                            if editor.isSaving { ProgressView().tint(.white) }
                            Text("Speichern")
                        }
                    }
                    .buttonStyle(.primary)
                    .disabled(editor.isSaving)
                }
            }
        }
        .task { await editor.load() }
        .sheet(isPresented: $pickDriver) {
            DriverPickerSheet(editor: editor)
        }
    }

    private var gridMetered: Binding<Bool> {
        Binding(get: { editor.draft.settings.bool("grid_metered") ?? true },
                set: { editor.draft.settings["grid_metered"] = .bool($0) })
    }

    // MARK: - Treiber

    @ViewBuilder
    private var driverRow: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            Text("Gerät / Treiber").font(Typo.subheadlineMedium).foregroundStyle(.secondary)
            Button {
                pickDriver = true
            } label: {
                HStack(spacing: Space.m) {
                    DomainIcon(symbol: editor.draft.category.symbol, color: editor.draft.category.domain, size: 36)
                    VStack(alignment: .leading, spacing: 2) {
                        if let driver = editor.driver {
                            Text(driver.name).font(Typo.bodyMedium).foregroundStyle(.primary)
                            if let description = driver.description {
                                Text(description).font(Typo.footnote).foregroundStyle(.secondary).lineLimit(2)
                            }
                        } else if editor.drivers.value == nil, editor.drivers.error == nil {
                            Text("Treiber werden geladen …").font(Typo.body).foregroundStyle(.secondary)
                        } else {
                            Text("Treiber wählen").font(Typo.bodyMedium).foregroundStyle(Palette.accent)
                        }
                    }
                    Spacer(minLength: 0)
                    if !editor.draft.isEditing {
                        Image(systemName: "chevron.up.chevron.down")
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(.tertiary)
                    }
                }
                .padding(Space.m)
                .frame(minHeight: 60)
                .background(Color(uiColor: .secondarySystemGroupedBackground), in: .rect(cornerRadius: 16, style: .continuous))
            }
            .buttonStyle(.plain)
            .disabled(editor.draft.isEditing || editor.drivers.value == nil)
            .accessibilityIdentifier("driver")
            if let error = editor.drivers.error {
                HStack {
                    InlineError(error: error)
                    Spacer()
                    Button("Erneut laden") { Task { await editor.drivers.reload() } }
                        .font(Typo.subheadlineMedium)
                }
            }
        }
    }

    @ViewBuilder
    private func maturity(_ driver: DriverMeta) -> some View {
        switch driver.maturity {
        case .beta:
            NoteBox(symbol: "exclamationmark.triangle", color: Palette.warn, title: "Beta",
                    text: String(localized: "An Hardware getestet, Varianten möglich. Live-Werte prüfen.") + (driver.notes.map { " " + $0 } ?? ""))
        case .experimental:
            NoteBox(symbol: "flask", color: Palette.error, title: "Experimentell",
                    text: String(localized: "Nicht an Hardware verifiziert. Live-Werte prüfen.") + (driver.notes.map { " " + $0 } ?? ""))
        default:
            if let notes = driver.notes, !notes.isEmpty {
                DisclosureGroup {
                    Text(notes).font(Typo.footnote).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                } label: {
                    Label("Hinweise zum Gerät", systemImage: "info.circle").font(Typo.subheadlineMedium)
                }
            }
        }
    }

    // MARK: - Test

    private var testSection: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            HStack(spacing: Space.s) {
                Button {
                    Task { await editor.test() }
                } label: {
                    Label(editor.isTesting ? "Teste …" : "Verbindung testen", systemImage: "antenna.radiowaves.left.and.right")
                }
                .buttonStyle(.secondary)
                .disabled(editor.isTesting)
                .accessibilityIdentifier("testConnection")
                if editor.canTestWrite {
                    Button {
                        Task { await editor.test(includeWrite: true) }
                    } label: {
                        Label("Mit Schaltbefehl", systemImage: "switch.2")
                    }
                    .buttonStyle(.secondary)
                    .disabled(editor.isTesting)
                    .accessibilityHint(Text("Sendet einen unkritischen Befehl und prüft die Rückmeldung"))
                }
            }
            if editor.isTesting {
                ProgressView().frame(maxWidth: .infinity)
            }
            if let result = editor.testResult {
                TestReport(result: result)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .animation(.smooth, value: editor.testResult)
    }
}

/// Ein Feld aus der Treiberbeschreibung.
private struct FieldInput: View {
    let field: ConfigField
    @Bindable var editor: DeviceEditor

    var body: some View {
        LabeledInput(label: label, help: field.help, error: editor.fieldErrors[field.key]) {
            switch field.type {
            case .boolean:
                Toggle(field.label, isOn: Binding(get: { editor.draft.flags[field.key] ?? false },
                                                  set: { editor.draft.flags[field.key] = $0 }))
                    .tint(Palette.accent)
                    .labelsHidden()
                    .frame(maxWidth: .infinity, alignment: .leading)
            case .select:
                Picker(field.label, selection: textBinding) {
                    // Immer ein leerer Eintrag: Ohne Standardwert gäbe es sonst keine gültige Auswahl.
                    Text(field.required ? "Bitte wählen" : "–").tag("")
                    ForEach(field.options ?? []) { option in
                        Text(option.label).tag(option.value)
                    }
                }
                .pickerStyle(.menu)
                .frame(maxWidth: .infinity, minHeight: 50, alignment: .leading)
                .padding(.horizontal, Space.s)
                .background(Color(uiColor: .secondarySystemGroupedBackground), in: .rect(cornerRadius: 14, style: .continuous))
            case .password:
                // Kein .password: Gerätezugänge sollen nicht mit eigenen Logins vorgeschlagen
                // oder im Passwörter-Programm gespeichert werden.
                AppTextField(prompt: placeholder, text: textBinding, symbol: "key", secure: true, identifier: field.key)
                    .textContentType(nil)
                    .privacySensitive()
            case .number:
                // Mit Minus (Offsets, Vorzeichen); decimalPad hätte keins.
                AppTextField(prompt: placeholder, text: textBinding, symbol: nil, secure: false, identifier: field.key)
                    .keyboardType(.numbersAndPunctuation)
            case .text:
                AppTextField(prompt: placeholder, text: textBinding, symbol: nil, secure: false, identifier: field.key)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(field.key.contains("host") || field.key.contains("ip") ? .URL : .default)
            }
        }
    }

    private var label: String {
        field.required ? field.label : field.label + " " + String(localized: "(optional)")
    }

    private var placeholder: Text {
        if field.type == .password, editor.draft.isEditing { return Text("unverändert") }
        return Text(verbatim: field.placeholder ?? "")
    }

    private var textBinding: Binding<String> {
        Binding(get: { editor.draft.text[field.key] ?? "" }, set: { editor.draft.text[field.key] = $0 })
    }
}

/// Beschriftung, Eingabe, Hilfe und Fehler eines Felds.
struct LabeledInput<Content: View>: View {
    var label: String
    var help: String?
    var error: String?
    @ViewBuilder var content: Content

    init(label: String, help: String? = nil, error: String? = nil, @ViewBuilder content: () -> Content) {
        self.label = label
        self.help = help
        self.error = error
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(Typo.subheadlineMedium).foregroundStyle(.secondary)
            content
            if let error {
                Label(error, systemImage: "exclamationmark.circle.fill")
                    .font(Typo.footnote)
                    .foregroundStyle(Palette.error.ink)
            } else if let help, !help.isEmpty {
                Text(help).font(Typo.footnote).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .contain)
    }
}

/// Hinweisfläche mit Symbol, Titel und Text (Beta, Experimentell, Batterie …).
struct NoteBox: View {
    var symbol: String
    var color: DomainColor
    var title: LocalizedStringKey
    var text: String

    var body: some View {
        HStack(alignment: .top, spacing: Space.m) {
            Image(systemName: symbol)
                .foregroundStyle(color.fill)
                .font(.title3)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(Typo.headline).foregroundStyle(color.ink)
                Text(text).font(Typo.subheadline)
            }
            Spacer(minLength: 0)
        }
        .padding(Space.m)
        .background(color.soft, in: .rect(cornerRadius: 16, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

/// Ergebnis des Verbindungstests mit gelesenen Live-Werten.
struct TestReport: View {
    let result: TestResult

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            Label {
                Text(result.message).font(Typo.bodyMedium)
            } icon: {
                Image(systemName: result.ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
                    .foregroundStyle(result.ok ? Palette.ok.fill : Palette.error.fill)
            }
            if let write = result.writeTest {
                Label(write.message, systemImage: write.ok ? "checkmark.circle" : "xmark.circle")
                    .font(Typo.subheadline)
                    .foregroundStyle(write.ok ? Palette.ok.ink : Palette.error.ink)
            }
            if let values = result.values, !values.isEmpty {
                Text("Gelesene Werte").font(Typo.caption).foregroundStyle(.secondary)
                Grid(alignment: .leading, horizontalSpacing: Space.l, verticalSpacing: 6) {
                    ForEach(values.keys.sorted(), id: \.self) { key in
                        GridRow {
                            Text(key).font(Typo.footnote).foregroundStyle(.secondary)
                            Text(display(values[key])).font(Typo.footnote.weight(.semibold)).monospacedDigit()
                                .gridColumnAlignment(.trailing)
                        }
                    }
                }
            }
            ForEach(result.warnings ?? [], id: \.self) { warning in
                Label(warning, systemImage: "exclamationmark.triangle")
                    .font(Typo.footnote)
                    .foregroundStyle(Palette.warn.ink)
            }
        }
        .padding(Space.m)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background((result.ok ? Palette.ok : Palette.error).soft, in: .rect(cornerRadius: 16, style: .continuous))
        .accessibilityElement(children: .combine)
    }

    private func display(_ value: JSONValue?) -> String {
        guard let value, !value.isNull else { return "–" }
        if case .number(let number) = value {
            return Fmt().number(number, digits: number.rounded() == number ? 0 : 2)
        }
        return value.string ?? "–"
    }
}
