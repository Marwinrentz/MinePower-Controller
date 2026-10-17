import MinePowerKit
import SwiftUI

/// Einstellungen → Geräte (Spiegel von `DevicesPanel.tsx`): alles, was
/// MinePower misst oder steuert. Anlegen und Bearbeiten öffnen ein Blatt mit
/// dem Geräteformular statt einer Karte über der Liste.
struct DevicesSettings: View {
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router
    @State private var devices: Resource<[Device]>?
    @State private var limits: DeviceLimits = [:]
    @State private var form: FormTarget?
    @State private var deleting: Device?

    static let categories: [DeviceCategory] = [.inverter, .meter, .battery, .wallbox, .waterHeater]

    /// Was das Blatt bearbeitet: neues Gerät einer Kategorie oder ein bestehendes.
    struct FormTarget: Identifiable {
        let editor: DeviceEditor
        let title: String
        var id: ObjectIdentifier { ObjectIdentifier(editor) }
    }

    var body: some View {
        let list = devices?.value ?? []
        let full = Self.categories.filter { !SetupFlow.canAdd($0, devices: list, limits: limits) }
        VStack(alignment: .leading, spacing: Space.xl) {
            Card {
                VStack(alignment: .leading, spacing: Space.l) {
                    CardHead(title: "Geräte") {
                        Button { router.showWizard = true } label: {
                            HStack(spacing: 4) { Icon(.flask, size: 16); Text("Assistent") }
                        }
                        .buttonStyle(.web(.ghost, size: .small, fullWidth: false))
                    }
                    FlowLayout(spacing: Space.s) {
                        ForEach(Self.categories, id: \.self) { category in
                            Button {
                                open(DeviceEditor(api: session.api, category: category),
                                     title: String(localized: "Gerät hinzufügen: \(category.label)"))
                            } label: {
                                HStack(spacing: 6) { Icon(category.webIcon, size: 16); Text("+ \(category.label)") }
                            }
                            .buttonStyle(.web(.normal, size: .small, fullWidth: false))
                            .disabled(full.contains(category))
                            .accessibilityHint(full.contains(category)
                                ? Text("Höchstens \(limits[category.rawValue] ?? 1) aktives Gerät") : Text(""))
                        }
                    }
                    if !full.isEmpty {
                        Text("Limit erreicht: \(full.map(\.label).joined(separator: ", "))")
                            .font(Typo.subheadline).foregroundStyle(Web.textDim)
                    }
                }
            }
            if let devices {
                ResourceContent(resource: devices, isEmpty: \.isEmpty, emptyTitle: "Noch keine Geräte", emptyIcon: .devices,
                                emptyMessage: "Wechselrichter und Netzzähler einrichten") { list in
                    VStack(spacing: Space.m) {
                        ForEach(list) { device in
                            DeviceRow(device: device, live: session.live.snapshot?.allDevices.first { $0.id == device.id },
                                      onToggle: { await toggle(device) },
                                      onEdit: { edit(device) },
                                      onDelete: { deleting = device })
                        }
                    }
                } placeholder: {
                    SkeletonCards(count: 1, height: 200)
                }
            } else {
                SkeletonCards(count: 1, height: 200)
            }
        }
        .task {
            if devices == nil { devices = Resource { [api = session.api] in try await api.devices() } }
            await devices?.load(maxAge: 5)
            limits = (try? await session.api.deviceLimits()) ?? [:]
        }
        // Sprung aus einer Meldung ("Details"): Formular öffnen, sobald die Liste da ist.
        .onChange(of: router.editDevice, initial: true) { openRequestedDevice() }
        .onChange(of: devices?.value?.count) { openRequestedDevice() }
        .sheet(item: $form) { target in
            DeviceFormSheet(target: target) {
                form = nil
                session.show(Notice(.success, String(localized: "Gespeichert")))
                Task { await reload() }
            }
        }
        .confirmationDialog(Text("Löschen: \(deleting?.name ?? "")?"), isPresented: Binding(
            get: { deleting != nil }, set: { if !$0 { deleting = nil } }), titleVisibility: .visible, presenting: deleting) { device in
            Button("Löschen", role: .destructive) { Task { await delete(device) } }
            Button("Abbrechen", role: .cancel) {}
        } message: { device in
            // Das Backend löscht Ladevorgänge und Zeitfenster des Geräts mit (Fremdschlüssel mit CASCADE).
            if device.category == .wallbox || device.category == .waterHeater {
                Text("Ladevorgänge und Zeitfenster dieses Geräts werden mitgelöscht. Das lässt sich nicht rückgängig machen.")
            } else {
                Text("Das lässt sich nicht rückgängig machen.")
            }
        }
    }

    private func open(_ editor: DeviceEditor, title: String) {
        form = FormTarget(editor: editor, title: title)
    }

    private func edit(_ device: Device) {
        open(DeviceEditor(api: session.api, category: device.category, existing: device),
             title: String(localized: "Bearbeiten: \(device.name)"))
    }

    private func openRequestedDevice() {
        guard let id = router.editDevice, let device = devices?.value?.first(where: { $0.id == id }) else { return }
        router.editDevice = nil
        edit(device)
    }

    private func reload() async {
        await devices?.reload()
        limits = (try? await session.api.deviceLimits()) ?? limits
        try? await session.live.refresh()
    }

    private func toggle(_ device: Device) async {
        do {
            _ = try await session.api.updateDevice(device.id, DeviceUpdate(enabled: !device.enabled))
            await reload()
        } catch {
            session.handle(error)
        }
    }

    private func delete(_ device: Device) async {
        do {
            try await session.api.deleteDevice(device.id)
            session.show(Notice(.success, String(localized: "\(device.name): gelöscht")))
            await reload()
        } catch {
            session.handle(error)
        }
    }
}

extension DeviceCategory {
    /// Bezeichnung als Text (für zusammengesetzte Sätze).
    var label: String {
        switch self {
        case .inverter: String(localized: "Wechselrichter")
        case .meter: String(localized: "Netzzähler")
        case .wallbox: String(localized: "Wallbox")
        case .battery: String(localized: "Batterie")
        case .waterHeater: String(localized: "Warmwasser")
        case .unknown: String(localized: "Gerät")
        }
    }
}

/// `.device-row`: Symbol, Status, Name, Kategorie · Treiber · zuletzt gesehen, Aktionen.
private struct DeviceRow: View {
    let device: Device
    let live: SnapshotDevice?
    var onToggle: () async -> Void
    var onEdit: () -> Void
    var onDelete: () -> Void

    @State private var toggling = false

    var body: some View {
        let fmt = Fmt()
        let online = live?.online ?? false
        let error = live?.lastError
        let state: Color = !device.enabled ? Web.textDim : online ? Palette.ok.fill : error != nil ? Palette.error.fill : Web.textDim
        Card {
            VStack(alignment: .leading, spacing: Space.m) {
                HStack(alignment: .top, spacing: Space.m) {
                    DomainIcon(icon: device.category.webIcon, color: device.category.domain, size: 44)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: Space.s) {
                            Circle().fill(state).frame(width: 9, height: 9)
                                .accessibilityHidden(true)
                            Text(device.name).font(Typo.headline).foregroundStyle(Web.textStrong)
                            if device.category == .battery, device.config.bool("allow_active_control") == true {
                                Badge(text: String(localized: "Aktive Steuerung"), tone: Palette.accentDomain)
                            }
                        }
                        Text(subtitle(fmt)).font(Typo.footnote).foregroundStyle(Web.textDim)
                        if let error, !error.isEmpty {
                            Text(shortError(error)).font(Typo.footnote).foregroundStyle(Palette.error.ink)
                                .lineLimit(2)
                        }
                    }
                    Spacer(minLength: 0)
                }
                .accessibilityElement(children: .combine)
                .accessibilityValue(Text(!device.enabled ? "deaktiviert" : online ? "erreichbar" : "nicht erreichbar"))
                HStack(spacing: Space.s) {
                    Toggle(isOn: Binding(get: { device.enabled }, set: { _ in
                        toggling = true
                        Task { await onToggle(); toggling = false }
                    })) {
                        Text("Aktiv").font(Typo.bodyMedium).foregroundStyle(Web.text)
                    }
                    .tint(Palette.accent)
                    .fixedSize()
                    .disabled(toggling)
                    Spacer(minLength: Space.s)
                    Button("Bearbeiten", action: onEdit)
                        .buttonStyle(.web(.normal, size: .small, fullWidth: false))
                    Button("Löschen", action: onDelete)
                        .buttonStyle(.web(.danger, size: .small, fullWidth: false))
                }
            }
        }
        .opacity(device.enabled ? 1 : 0.7)
    }

    private func subtitle(_ fmt: Fmt) -> String {
        var parts = [device.category.label, device.driverId]
        if let seen = live?.lastSeen { parts.append(fmt.ago(seen)) }
        return parts.joined(separator: " · ")
    }
}

/// Blatt mit dem Geräteformular (Treiber, Felder, Verbindungstest, Speichern).
private struct DeviceFormSheet: View {
    let target: DevicesSettings.FormTarget
    var onSaved: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                DeviceEditorView(editor: target.editor) { _ in onSaved() }
                    .padding(Space.l)
                    .frame(maxWidth: 640)
                    .frame(maxWidth: .infinity)
            }
            .background(AppBackground())
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle(target.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Abbrechen") { dismiss() }
                }
            }
        }
        .interactiveDismissDisabled(target.editor.isSaving)
    }
}
