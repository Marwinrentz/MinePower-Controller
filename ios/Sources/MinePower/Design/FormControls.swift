import MinePowerKit
import SwiftUI

/// `.field`: Beschriftung, Eingabe, Hilfe – wie im Web-Formular.
struct Field<Content: View>: View {
    var label: LocalizedStringKey
    var help: String?
    var error: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(Typo.subheadlineMedium).foregroundStyle(Web.textStrong)
            content
            if let error {
                Text(error).font(Typo.subheadlineMedium).foregroundStyle(Palette.error.ink)
            } else if let help, !help.isEmpty {
                Text(help).font(Typo.subheadline).foregroundStyle(Web.textDim).fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .contain)
    }
}

/// `NumberInput` mit Einheit rechts im Feld. Übernimmt erst beim Verlassen
/// oder "Fertig" – kein Springen beim Tippen von "0,".
struct NumberField: View {
    @Binding var value: Double
    var range: ClosedRange<Double>? = nil
    var unit: String = ""
    var digits = 0

    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: Space.s) {
            TextField("", text: $text)
                .keyboardType(.numbersAndPunctuation)
                .submitLabel(.done)
                .focused($focused)
                .onSubmit(commit)
                .font(.custom(Typo.Weight.regular.rawValue, size: 16, relativeTo: .body))
                .monospacedDigit()
                .foregroundStyle(Web.textStrong)
            if !unit.isEmpty {
                Text(unit).font(Typo.subheadlineMedium).foregroundStyle(Web.textDim)
            }
        }
        .padding(.horizontal, Space.m)
        .frame(minHeight: 48)
        .background(Web.surface, in: .rect(cornerRadius: Radius.medium, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.medium, style: .continuous)
            .strokeBorder(focused ? Palette.accent : Web.lineStrong, lineWidth: 1))
        .onAppear { text = Fmt().number(value, digits: digits) }
        .onChange(of: value) { _, new in if !focused { text = Fmt().number(new, digits: digits) } }
        .onChange(of: focused) { _, isFocused in if !isFocused { commit() } }
    }

    private func commit() {
        guard var parsed = Fmt.parseNumber(text) else {
            text = Fmt().number(value, digits: digits)
            return
        }
        if let range { parsed = min(range.upperBound, max(range.lowerBound, parsed)) }
        value = parsed
        text = Fmt().number(parsed, digits: digits)
    }
}

/// `.toggle-row`: Schalter mit Beschriftung und Hilfe.
struct ToggleRow: View {
    var label: LocalizedStringKey
    var help: String?
    @Binding var isOn: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Toggle(isOn: $isOn) {
                Text(label).font(Typo.bodyMedium).foregroundStyle(Web.textStrong)
            }
            .tint(Palette.accent)
            .frame(minHeight: 48)
            if let help, !help.isEmpty {
                Text(help).font(Typo.subheadline).foregroundStyle(Web.textDim)
            }
        }
    }
}

/// Uhrzeit "HH:MM" mit Systemauswahl.
struct TimeField: View {
    @Binding var value: String

    var body: some View {
        DatePicker("", selection: Binding(get: { Self.date(value) }, set: { value = Self.text($0) }),
                   displayedComponents: .hourAndMinute)
            .labelsHidden()
            .environment(\.locale, Locale(identifier: "de_DE"))
            .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
    }

    static func date(_ text: String) -> Date {
        let parts = text.split(separator: ":").compactMap { Int($0) }
        var comps = DateComponents()
        comps.hour = parts.first ?? 7
        comps.minute = parts.count > 1 ? parts[1] : 0
        return Calendar.current.date(from: comps) ?? .now
    }

    static func text(_ date: Date) -> String {
        let c = Calendar.current.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", c.hour ?? 0, c.minute ?? 0)
    }
}

/// `.form-actions`: Verwerfen und Speichern (nur bei Änderungen aktiv).
struct FormActions: View {
    var dirty: Bool
    var saving: Bool
    var onDiscard: () -> Void
    var onSave: () -> Void

    var body: some View {
        HStack(spacing: Space.s) {
            Spacer()
            if dirty {
                Button("Verwerfen", action: onDiscard)
                    .buttonStyle(.web(.ghost, fullWidth: false))
            }
            Button(action: onSave) {
                HStack(spacing: 6) {
                    if saving { ProgressView().tint(Web.accentText) }
                    Text("Speichern")
                }
            }
            .buttonStyle(.web(.primary, fullWidth: false))
            .disabled(!dirty || saving)
        }
    }
}

/// `DevicePicker`: Auswahl, wenn mehrere Geräte einer Art eingerichtet sind.
struct DevicePicker: View {
    let devices: [SnapshotDevice]
    @Binding var selection: Int?

    var body: some View {
        if devices.count > 1 {
            ScrollView(.horizontal, showsIndicators: false) {
                SmallSegment(options: devices.map { ($0.id, $0.name) },
                             selection: Binding(get: { selection ?? devices.first?.id ?? 0 }, set: { selection = $0 }))
            }
        }
    }
}

/// Gespeicherte Geräte (für Regelungseinstellungen) samt Speichern.
@MainActor
@Observable
final class DeviceSettingsStore {
    let devices: Resource<[Device]>
    private(set) var saving: Set<Int> = []
    @ObservationIgnored private let api: APIClient

    init(api: APIClient) {
        self.api = api
        devices = Resource { try await api.devices() }
    }

    func settings(for id: Int) -> JSONObject? { devices.value?.first { $0.id == id }?.settings }

    /// Wie `saveSettings` im Web: bestehende Einstellungen plus Änderungen.
    func save(_ id: Int, _ settings: JSONObject, session: AppSession) async -> Bool {
        saving.insert(id)
        defer { saving.remove(id) }
        let merged = (self.settings(for: id) ?? [:]).merging(settings) { $1 }
        do {
            let updated = try await api.updateDevice(id, DeviceUpdate(settings: merged))
            devices.update { list in
                if let i = list.firstIndex(where: { $0.id == id }) { list[i] = updated }
            }
            try? await session.live.refresh()
            session.show(Notice(.success, String(localized: "Gespeichert")))
            return true
        } catch {
            session.handle(error)
            return false
        }
    }
}
