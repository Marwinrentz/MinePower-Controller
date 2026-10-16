import MinePowerKit
import SwiftUI

// MARK: - Flächen

/// Inhaltskarte: ruhige Fläche ohne Glas, gruppiert auf dem Seitenhintergrund.
struct Card<Content: View>: View {
    var padding: CGFloat = Space.l
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(uiColor: .secondarySystemGroupedBackground),
                        in: .rect(cornerRadius: Radius.card, style: .continuous))
    }
}

/// Seite mit gruppiertem Hintergrund, angenehmer Lesebreite und Pull-to-Refresh.
struct Page<Content: View>: View {
    var maxWidth: CGFloat = 760
    var refresh: (@MainActor () async -> Void)?
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.l) {
                content
            }
            .padding(.horizontal, Space.l)
            .padding(.vertical, Space.s)
            .frame(maxWidth: maxWidth)
            .frame(maxWidth: .infinity)
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .softScrollEdges()
        .refreshableIf(refresh)
    }
}

private extension View {
    /// Ziehen zum Aktualisieren nur, wenn die Seite etwas nachladen kann.
    @ViewBuilder
    func refreshableIf(_ action: (@MainActor () async -> Void)?) -> some View {
        if let action { refreshable { await action() } } else { self }
    }
}

/// Überschrift einer Gruppe, optional mit Aktion rechts.
struct SectionHeader<Trailing: View>: View {
    var title: LocalizedStringKey
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
                .font(Typo.title3)
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: Space.s)
            trailing
        }
        .padding(.top, Space.s)
    }
}

extension SectionHeader where Trailing == EmptyView {
    init(_ title: LocalizedStringKey) {
        self.init(title: title) { EmptyView() }
    }
}

// MARK: - Werte

/// Zahl mit Einheit; Einheit kleiner und ruhiger, Ziffern fest breit.
struct Metric: View {
    var value: String
    var unit: String
    var font: Font = Typo.metric
    var color: Color = .primary

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text(value)
                .font(font)
                .monospacedDigit()
                .foregroundStyle(color)
                .contentTransition(.numericText())
            if !unit.isEmpty {
                Text(unit)
                    .font(Typo.subheadlineMedium)
                    .foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

extension Metric {
    /// Leistung in W/kW nach Web-Regeln.
    init(watts: Double?, font: Font = Typo.metric, color: Color = .primary) {
        let fmt = Fmt()
        if let watts {
            self.init(value: fmt.wattsValue(watts), unit: fmt.wattsUnit(watts), font: font, color: color)
        } else {
            self.init(value: "–", unit: "", font: font, color: color)
        }
    }
}

/// Kleiner Zustandshinweis: Form (Symbol) und Text, nie nur Farbe.
struct StatusBadge: View {
    var text: String
    var symbol: String
    var color: DomainColor

    var body: some View {
        Label(text, systemImage: symbol)
            .font(Typo.caption)
            .foregroundStyle(color.ink)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(color.soft, in: .capsule)
            .labelStyle(.titleAndIcon)
    }
}

/// Rundes Symbol in Domänenfarbe (Kartenkopf).
struct DomainIcon: View {
    var symbol: String
    var color: DomainColor
    var size: CGFloat = 36

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: size * 0.46, weight: .semibold))
            .foregroundStyle(color.fill)
            .frame(width: size, height: size)
            .background(color.soft, in: .circle)
            .accessibilityHidden(true)
    }
}

// MARK: - Zustände

/// Lade-, Fehler- und Leerzustand für geladene Inhalte.
struct ResourceContent<Value: Sendable, Content: View, Placeholder: View>: View {
    let resource: Resource<Value>
    var isEmpty: (Value) -> Bool = { _ in false }
    var emptyTitle: LocalizedStringKey = "Noch keine Daten"
    var emptySymbol = "tray"
    var emptyMessage: LocalizedStringKey?
    @ViewBuilder var content: (Value) -> Content
    @ViewBuilder var placeholder: () -> Placeholder

    var body: some View {
        if let value = resource.value {
            if isEmpty(value) {
                ContentUnavailableView {
                    Label(emptyTitle, systemImage: emptySymbol)
                } description: {
                    if let emptyMessage { Text(emptyMessage) }
                }
            } else {
                content(value)
            }
        } else if let error = resource.error, !resource.isLoading {
            ErrorState(error: error) { await resource.reload() }
        } else {
            placeholder()
                .redacted(reason: .placeholder)
                .accessibilityLabel(Text("Wird geladen"))
        }
    }
}

extension ResourceContent where Placeholder == SkeletonCards {
    init(_ resource: Resource<Value>, isEmpty: @escaping (Value) -> Bool = { _ in false },
         emptyTitle: LocalizedStringKey = "Noch keine Daten", emptySymbol: String = "tray",
         emptyMessage: LocalizedStringKey? = nil, @ViewBuilder content: @escaping (Value) -> Content) {
        self.init(resource: resource, isEmpty: isEmpty, emptyTitle: emptyTitle, emptySymbol: emptySymbol,
                  emptyMessage: emptyMessage, content: content, placeholder: { SkeletonCards() })
    }
}

/// Platzhalter während des ersten Ladens.
struct SkeletonCards: View {
    var count = 3
    @State private var pulse = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: Space.l) {
            ForEach(0..<count, id: \.self) { _ in
                RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                    .fill(Color(uiColor: .secondarySystemGroupedBackground))
                    .frame(height: 110)
            }
        }
        .opacity(pulse ? 0.55 : 1)
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { pulse = true }
        }
    }
}

/// Fehler mit verständlichem Text und "Erneut versuchen".
struct ErrorState: View {
    var error: APIError
    var retry: (@MainActor () async -> Void)?

    var body: some View {
        ContentUnavailableView {
            Label(title, systemImage: symbol)
        } description: {
            Text(error.errorDescription ?? "")
        } actions: {
            if let retry {
                Button("Erneut versuchen") { Task { await retry() } }
                    .buttonStyle(.borderedProminent)
                    .tint(Palette.accent)
            }
        }
    }

    private var title: LocalizedStringKey {
        switch error {
        case .offline, .unreachable, .timedOut: "Server nicht erreichbar"
        case .forbidden: "Keine Berechtigung"
        case .unauthorized: "Anmeldung abgelaufen"
        default: "Laden fehlgeschlagen"
        }
    }

    private var symbol: String {
        switch error {
        case .offline: "wifi.slash"
        case .unreachable, .timedOut: "server.rack"
        case .forbidden: "lock"
        default: "exclamationmark.triangle"
        }
    }
}

/// Meldung oben im Bild; verschwindet nach einigen Sekunden.
struct NoticeBanner: View {
    let notice: Notice
    var dismiss: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: Space.m) {
            Image(systemName: symbol)
                .font(.title3)
                .foregroundStyle(color.fill)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(notice.title).font(Typo.headline)
                if let message = notice.message {
                    Text(message).font(Typo.subheadline).foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.footnote.weight(.semibold))
                    .frame(width: 44, height: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .accessibilityLabel(Text("Schließen"))
        }
        .padding(.leading, Space.l)
        .padding(.vertical, Space.xs)
        .glassBar(cornerRadius: 22)
        .padding(.horizontal, Space.l)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isStaticText)
        .task(id: notice.id) {
            // Abgebrochen (neue Meldung, Ansicht weg): nichts schließen.
            do {
                try await Task.sleep(for: .seconds(notice.kind == .error ? 6 : 4))
            } catch {
                return
            }
            dismiss()
        }
    }

    private var symbol: String {
        switch notice.kind {
        case .info: "info.circle.fill"
        case .success: "checkmark.circle.fill"
        case .warning: "exclamationmark.triangle.fill"
        case .error: "xmark.octagon.fill"
        }
    }

    private var color: DomainColor {
        switch notice.kind {
        case .info: Palette.info
        case .success: Palette.ok
        case .warning: Palette.warn
        case .error: Palette.error
        }
    }
}
