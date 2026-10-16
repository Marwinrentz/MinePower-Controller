import MinePowerKit
import SwiftUI

/// Einrichtungsassistent als Vollbild.
///
/// Oben Fortschritt und Schrittname, in der Mitte genau eine Aufgabe, unten in
/// der Daumenzone "Zurück" und "Weiter/Überspringen". Jeder Geräteschritt hat
/// seinen eigenen Test, bevor gespeichert wird.
struct SetupWizardView: View {
    @Environment(AppSession.self) private var session
    @Environment(\.dismiss) private var dismiss
    @State private var wizard: SetupWizard
    @State private var direction: Edge = .trailing
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(api: APIClient) {
        _wizard = State(initialValue: SetupWizard(api: api))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    StepHeader(flow: wizard.flow)
                    content
                        .id(wizard.step)
                        .transition(.asymmetric(insertion: .move(edge: direction).combined(with: .opacity),
                                                removal: .opacity))
                }
                .padding(.horizontal, Space.xl)
                .padding(.vertical, Space.l)
                .frame(maxWidth: 620)
                .frame(maxWidth: .infinity)
                .animation(reduceMotion ? nil : .smooth(duration: 0.35), value: wizard.step)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Color(uiColor: .systemGroupedBackground))
            .safeAreaInset(edge: .bottom) { actionBar }
            .navigationTitle("Einrichtung")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if wizard.step != .done {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Später") { dismiss() }
                            .accessibilityHint(Text("Den Assistenten schließen. Er bleibt unter Einstellungen erreichbar."))
                    }
                }
            }
        }
        .task { await wizard.start() }
        .interactiveDismissDisabled(wizard.editor?.isSaving == true)
    }

    // MARK: - Schritte

    @ViewBuilder
    private var content: some View {
        switch wizard.step {
        case .welcome:
            WelcomeStep(wizard: wizard)
        case .device(let category):
            DeviceStep(wizard: wizard, category: category)
        case .priority:
            PriorityStep(wizard: wizard)
        case .done:
            DoneStep(wizard: wizard)
        }
    }

    // MARK: - Untere Leiste

    private var actionBar: some View {
        HStack(spacing: Space.m) {
            if wizard.flow.canGoBack {
                Button {
                    go(back: true)
                } label: {
                    Label("Zurück", systemImage: "chevron.left").labelStyle(.iconOnly)
                        .frame(width: 50, height: 50)
                }
                .buttonStyle(.plain)
                .glassControl()
                .accessibilityLabel(Text("Zurück"))
            }
            primaryButton
        }
        .padding(.horizontal, Space.xl)
        .padding(.vertical, Space.m)
        .frame(maxWidth: 620)
        .frame(maxWidth: .infinity)
        .background(alignment: .top) {
            Divider().opacity(0.5)
        }
        .background(.bar)
    }

    @ViewBuilder
    private var primaryButton: some View {
        switch wizard.step {
        case .done:
            Button {
                Task {
                    await session.refreshServerInfo()
                    dismiss()
                }
            } label: {
                Text("Zur Übersicht")
            }
            .buttonStyle(.primary)
            .accessibilityIdentifier("wizardFinish")
        case .device where wizard.editor?.driver != nil && !wizard.savedInStep:
            // Formular offen: Speichern ist die Hauptaktion, Überspringen nebenan.
            HStack(spacing: Space.s) {
                Button("Überspringen") { go(back: false) }
                    .buttonStyle(.secondary)
                    .frame(maxWidth: 160)
                Button {
                    Task { await wizard.saveDevice() }
                } label: {
                    HStack {
                        if wizard.editor?.isSaving == true { ProgressView().tint(.white) }
                        Text("Speichern")
                    }
                }
                .buttonStyle(.primary)
                .accessibilityIdentifier("wizardSave")
            }
        default:
            Button {
                go(back: false)
            } label: {
                HStack {
                    if wizard.isWorking { ProgressView().tint(.white) }
                    Text(nextTitle)
                    Image(systemName: "chevron.right")
                }
            }
            .buttonStyle(.primary)
            .disabled(wizard.isWorking)
            .accessibilityIdentifier("wizardNext")
        }
    }

    private var nextTitle: LocalizedStringKey {
        switch wizard.step {
        case .welcome: "Los geht's"
        default: wizard.nextIsSkip ? "Überspringen" : "Weiter"
        }
    }

    private func go(back: Bool) {
        direction = back ? .leading : .trailing
        Task {
            if back { await wizard.back() } else { await wizard.next() }
        }
    }
}

// MARK: - Kopf

private struct StepHeader: View {
    let flow: SetupFlow
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color(uiColor: .tertiarySystemFill))
                    Capsule().fill(Brand.gradient)
                        .frame(width: max(8, geo.size.width * flow.progress))
                        .animation(reduceMotion ? nil : .smooth(duration: 0.5), value: flow.progress)
                }
            }
            .frame(height: 6)
            .accessibilityElement()
            .accessibilityLabel(Text("Fortschritt"))
            .accessibilityValue(Text("Schritt \(flow.index + 1) von \(SetupFlow.steps.count)"))
            Text("Schritt \(flow.index + 1) von \(SetupFlow.steps.count)")
                .font(Typo.caption)
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)
        }
    }
}

// MARK: - Willkommen

private struct WelcomeStep: View {
    let wizard: SetupWizard

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            BrandMark(size: 64)
            Text("Willkommen bei MinePower")
                .font(Typo.largeTitle)
                .accessibilityAddTraits(.isHeader)
            Text("In wenigen Schritten verbindest du deine Geräte. Jeder Schritt lässt sich überspringen und später in den Einstellungen nachholen.")
                .font(Typo.body)
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: Space.m) {
                ForEach(DeviceCategory.setupOrder, id: \.self) { category in
                    HStack(spacing: Space.m) {
                        DomainIcon(symbol: category.symbol, color: category.domain, size: 34)
                        Text(category.title).font(Typo.bodyMedium)
                        Spacer()
                        if SetupFlow.Step.device(category).isOptional {
                            Text("optional").font(Typo.caption).foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            Card {
                VStack(alignment: .leading, spacing: Space.m) {
                    Label("Erst einmal ausprobieren?", systemImage: "play.circle")
                        .font(Typo.headline)
                    Text("Der Server simuliert eine komplette Anlage ohne Hardware. Echte Geräte kannst du jederzeit ergänzen.")
                        .font(Typo.subheadline)
                        .foregroundStyle(.secondary)
                    Button("Demo-Modus starten") { Task { await wizard.startServerDemo() } }
                        .buttonStyle(.secondary)
                        .disabled(wizard.isWorking)
                    if let error = wizard.error { InlineError(error: error) }
                }
            }
        }
    }
}

// MARK: - Gerät

private struct DeviceStep: View {
    let wizard: SetupWizard
    let category: DeviceCategory

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            HStack(spacing: Space.m) {
                DomainIcon(symbol: category.symbol, color: category.domain, size: 48)
                VStack(alignment: .leading, spacing: 2) {
                    Text(category.title)
                        .font(Typo.title)
                        .accessibilityAddTraits(.isHeader)
                    if SetupFlow.Step.device(category).isOptional {
                        Text("Optional").font(Typo.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if let hint { Text(hint).font(Typo.body).foregroundStyle(.secondary) }
            if category == .battery {
                NoteBox(symbol: "info.circle", color: Palette.info, title: "Nur lesend reicht oft",
                        text: String(localized: "Ein Hybrid-Wechselrichter regelt seine Batterie selbst und liefert Ladestand und Leistung schon mit. Ein eigenes Batteriegerät lohnt sich nur für aktive Steuerung."))
            }
            ForEach(wizard.devicesInStep) { device in
                HStack(spacing: Space.m) {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(Palette.ok.fill)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(device.name).font(Typo.bodyMedium)
                        Text(device.driverId).font(Typo.footnote).foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                .padding(Space.m)
                .background(Color(uiColor: .secondarySystemGroupedBackground), in: .rect(cornerRadius: 16, style: .continuous))
                .accessibilityElement(children: .combine)
            }
            if wizard.savedInStep {
                HStack {
                    Label("Gespeichert", systemImage: "checkmark.seal.fill")
                        .font(Typo.headline)
                        .foregroundStyle(Palette.ok.ink)
                    Spacer()
                    if !wizard.isCategoryFull {
                        Button("Weiteres hinzufügen") { wizard.addAnother() }
                            .font(Typo.subheadlineMedium)
                    }
                }
            } else if wizard.isCategoryFull {
                Label("Höchstens \(wizard.limits.value?[category.rawValue] ?? 1) aktives Gerät dieser Art.",
                      systemImage: "info.circle")
                    .font(Typo.subheadline)
                    .foregroundStyle(.secondary)
            } else if let editor = wizard.editor {
                DeviceEditorView(editor: editor, showsSave: false)
            }
        }
    }

    private var hint: LocalizedStringKey? {
        switch category {
        case .inverter: "Hybrid-Wechselrichter liefern Netz- und Batteriewerte gleich mit."
        case .meter: "Messpunkt am Hausanschluss. Ohne Netzmessung kann MinePower nicht regeln."
        case .wallbox: "Damit das Auto mit Sonnenstrom lädt."
        case .waterHeater: "Heizstab oder Wärmepumpe, die Überschuss in warmes Wasser verwandelt."
        default: nil
        }
    }
}

// MARK: - Reihenfolge

private struct PriorityStep: View {
    @Bindable var wizard: SetupWizard

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            Text("Wer bekommt den Überschuss zuerst?")
                .font(Typo.title)
                .accessibilityAddTraits(.isHeader)
            if wizard.loads.count >= 2 {
                Text("Ziehe die Geräte in die gewünschte Reihenfolge. Oben wird zuerst versorgt.")
                    .font(Typo.body).foregroundStyle(.secondary)
                PriorityList(order: $wizard.priority, devices: wizard.loads)
                if let error = wizard.error { InlineError(error: error) }
            } else {
                Card {
                    Label("Weniger als zwei steuerbare Geräte – keine Reihenfolge nötig.", systemImage: "checkmark.circle")
                        .font(Typo.body)
                }
            }
        }
    }
}

/// Sortierbare Liste der Lasten (Ziehen oder Pfeile, auch mit VoiceOver).
struct PriorityList: View {
    @Binding var order: [Int]
    let devices: [Device]

    var body: some View {
        VStack(spacing: Space.s) {
            ForEach(Array(order.enumerated()), id: \.element) { index, id in
                if let device = devices.first(where: { $0.id == id }) {
                    HStack(spacing: Space.m) {
                        Text("\(index + 1)")
                            .font(Typo.headline).monospacedDigit()
                            .frame(width: 30, height: 30)
                            .background(Palette.accent.opacity(0.14), in: .circle)
                        DomainIcon(symbol: device.category.symbol, color: device.category.domain, size: 34)
                        Text(device.name).font(Typo.bodyMedium)
                        Spacer(minLength: Space.s)
                        VStack(spacing: 0) {
                            moveButton(index, by: -1, symbol: "chevron.up", label: "Nach oben")
                            moveButton(index, by: 1, symbol: "chevron.down", label: "Nach unten")
                        }
                    }
                    .padding(.leading, Space.m)
                    .padding(.vertical, Space.xs)
                    .background(Color(uiColor: .secondarySystemGroupedBackground), in: .rect(cornerRadius: 16, style: .continuous))
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel(Text("\(index + 1). \(device.name)"))
                    .accessibilityAction(named: Text("Nach oben")) { move(index, by: -1) }
                    .accessibilityAction(named: Text("Nach unten")) { move(index, by: 1) }
                }
            }
        }
        .sensoryFeedback(.selection, trigger: order)
        .animation(.snappy, value: order)
    }

    private func moveButton(_ index: Int, by delta: Int, symbol: String, label: LocalizedStringKey) -> some View {
        Button {
            move(index, by: delta)
        } label: {
            Image(systemName: symbol)
                .font(.footnote.weight(.bold))
                .frame(width: 44, height: 30)
        }
        .buttonStyle(.borderless)
        .disabled(!order.indices.contains(index + delta))
        .accessibilityLabel(Text(label))
    }

    private func move(_ index: Int, by delta: Int) {
        let target = index + delta
        guard order.indices.contains(index), order.indices.contains(target) else { return }
        order.swapAt(index, target)
    }
}

// MARK: - Fertig

private struct DoneStep: View {
    let wizard: SetupWizard
    @State private var appeared = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: Space.xl) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 76, weight: .semibold))
                .foregroundStyle(Palette.ok.fill)
                .symbolEffect(.bounce, value: appeared)
                .padding(.top, Space.xl)
                .accessibilityHidden(true)
            Text("Einrichtung abgeschlossen")
                .font(Typo.title)
                .multilineTextAlignment(.center)
                .accessibilityAddTraits(.isHeader)
            if wizard.hasGridMeasurement == false {
                NoteBox(symbol: "exclamationmark.triangle", color: Palette.warn, title: "Keine Netzmessung",
                        text: String(localized: "Richte einen Netzzähler oder einen Wechselrichter mit Zähler ein. Bis dahin pausiert die Regelung."))
            } else {
                Text("Die Regelung ist aktiv. Sobald die Sonne scheint, verteilt MinePower den Überschuss.")
                    .font(Typo.body)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity)
        .onAppear { if !reduceMotion { appeared = true } }
    }
}
