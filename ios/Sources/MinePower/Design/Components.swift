import MinePowerKit
import SwiftUI

// Bausteine im Aussehen der Weboberfläche (theme.css): Karte, Panel, Chip,
// Badge, Segmente, Regler, Kennzahlen, Hinweise. Liquid Glass kommt nur auf
// der Bedienebene dazu (Leisten, schwebende Aktionen, Meldungen).

// MARK: - Flächen

/// `.card`: Fläche mit feiner Kante und leichtem Schatten, Radius 20.
struct Card<Content: View>: View {
    var padding: CGFloat?
    var tone: DomainColor?
    @ViewBuilder var content: Content
    @Environment(\.horizontalSizeClass) private var sizeClass

    var body: some View {
        content
            .padding(padding ?? (sizeClass == .regular ? Space.xl : Space.l))
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                let shape = RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                ZStack {
                    shape.fill(Web.surface)
                    if let tone {
                        // `.hero::before`: Farbwolke oben links in der Tonfarbe.
                        RadialGradient(colors: [tone.fill.opacity(0.22), .clear], center: .topLeading,
                                       startRadius: 0, endRadius: 420)
                            .clipShape(shape)
                    }
                }
                .shadow(color: Web.shadow, radius: 1, y: 1)
            }
            .overlay(RoundedRectangle(cornerRadius: Radius.card, style: .continuous).strokeBorder(Web.line, lineWidth: 1))
    }
}

/// `.panel`: zweite Ebene innerhalb einer Karte.
struct Panel<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(.horizontal, Space.l)
            .padding(.vertical, Space.m)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Web.surface2, in: .rect(cornerRadius: Radius.medium, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.medium, style: .continuous).strokeBorder(Web.line, lineWidth: 1))
    }
}

/// Seite mit Web-Hintergrund, Lesebreite und Ziehen zum Aktualisieren.
struct Page<Content: View>: View {
    var maxWidth: CGFloat = 900
    var refresh: (@MainActor () async -> Void)?
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Space.xl) {
                content
            }
            .padding(.horizontal, Space.l)
            .padding(.top, Space.s)
            .padding(.bottom, Space.section)
            .frame(maxWidth: maxWidth)
            .frame(maxWidth: .infinity)
        }
        .background { AppBackground() }
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

/// Kartenkopf (`.card-head`): Titel, Unterzeile, Aktion rechts.
struct CardHead<Trailing: View>: View {
    var title: LocalizedStringKey
    var subtitle: String?
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.m) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(Typo.title3).foregroundStyle(Web.textStrong)
                    .accessibilityAddTraits(.isHeader)
                if let subtitle { Text(subtitle).font(Typo.subheadline).foregroundStyle(Web.textDim) }
            }
            Spacer(minLength: Space.s)
            trailing
        }
    }
}

extension CardHead where Trailing == EmptyView {
    init(_ title: LocalizedStringKey, subtitle: String? = nil) {
        self.init(title: title, subtitle: subtitle) { EmptyView() }
    }
}

/// `.group-title`: Zwischenüberschrift innerhalb einer Karte.
struct GroupTitle: View {
    var title: LocalizedStringKey

    init(_ title: LocalizedStringKey) { self.title = title }

    var body: some View {
        Text(title)
            .font(Typo.headline)
            .foregroundStyle(Web.textStrong)
            .padding(.top, Space.s)
            .accessibilityAddTraits(.isHeader)
    }
}

/// Überschrift einer Gruppe außerhalb von Karten.
struct SectionHeader<Trailing: View>: View {
    var title: LocalizedStringKey
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(Typo.title3).foregroundStyle(Web.textStrong).accessibilityAddTraits(.isHeader)
            Spacer(minLength: Space.s)
            trailing
        }
        .padding(.top, Space.s)
    }
}

extension SectionHeader where Trailing == EmptyView {
    init(_ title: LocalizedStringKey) { self.init(title: title) { EmptyView() } }
}

// MARK: - Kleine Elemente

/// `.quick-icon`: Symbol auf getönter, abgerundeter Fläche (44 × 44).
struct DomainIcon: View {
    var icon: WebIcon
    var color: DomainColor?
    var size: CGFloat = 44

    var body: some View {
        Icon(icon, size: size * 0.46)
            .foregroundStyle(color?.fill ?? Web.textDim)
            .frame(width: size, height: size)
            .background(color?.soft ?? Web.surface3, in: .rect(cornerRadius: size * 0.27, style: .continuous))
            .accessibilityHidden(true)
    }
}

/// `.badge`: kleines Etikett, neutral oder getönt.
struct Badge: View {
    var text: String
    var tone: DomainColor?
    var icon: WebIcon?

    var body: some View {
        HStack(spacing: 4) {
            if let icon { Icon(icon, size: 12, strokeWidth: 2) }
            Text(text).lineLimit(1)
        }
        .font(Typo.caption)
        .foregroundStyle(tone?.ink ?? Web.textDim)
        .padding(.horizontal, 10)
        .padding(.vertical, 4)
        .background(tone?.soft ?? Web.surface3, in: .capsule)
        .accessibilityElement(children: .combine)
    }
}

/// `.chip`: Kennzahl mit Symbol im Kopf der Übersicht.
struct Chip<Content: View>: View {
    var icon: WebIcon
    var tone: DomainColor?
    var highlighted = false
    @ViewBuilder var content: Content

    var body: some View {
        HStack(spacing: 6) {
            Icon(icon, size: 16)
                .foregroundStyle(tone?.fill ?? Web.textDim)
            content
        }
        .font(Typo.headline)
        .foregroundStyle(Web.textStrong)
        .padding(.horizontal, Space.m)
        .frame(minHeight: 36)
        .background(Web.surface2, in: .capsule)
        .overlay(Capsule().strokeBorder(highlighted ? (tone?.fill ?? Web.line).opacity(0.5) : Web.line, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}

/// Zahl mit Einheit (`.num`): feste Ziffernbreite, Einheit kleiner und gedämpft.
struct Metric: View {
    var value: String
    var unit: String
    var font: Font = Typo.metric
    var color: Color = Web.textStrong

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text(value)
                .font(font)
                .monospacedDigit()
                .foregroundStyle(color)
                .contentTransition(.numericText())
            if !unit.isEmpty {
                Text(unit).font(Typo.subheadlineMedium).foregroundStyle(Web.textDim)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

extension Metric {
    /// Leistung in W/kW nach Web-Regeln.
    init(watts: Double?, font: Font = Typo.metric, color: Color = Web.textStrong) {
        let fmt = Fmt()
        if let watts {
            self.init(value: fmt.wattsValue(watts), unit: fmt.wattsUnit(watts), font: font, color: color)
        } else {
            self.init(value: "–", unit: "", font: font, color: color)
        }
    }
}

/// `.stats`/`.stat`: Raster aus Kennzahlen mit Beschriftung.
struct StatGrid<Content: View>: View {
    var minimum: CGFloat = 128
    @ViewBuilder var content: Content

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: minimum), spacing: Space.l, alignment: .topLeading)],
                  alignment: .leading, spacing: Space.m) {
            content
        }
    }
}

struct Stat: View {
    var label: LocalizedStringKey
    var value: String
    var sub: String?
    var color: Color = Web.textStrong

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(Typo.subheadlineMedium).foregroundStyle(Web.textDim)
            Text(value).font(Typo.stat).foregroundStyle(color).monospacedDigit()
                .contentTransition(.numericText())
                .lineLimit(2).minimumScaleFactor(0.8)
            if let sub { Text(sub).font(Typo.caption2).foregroundStyle(Web.textDim) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Knöpfe

/// `.btn` in seinen Varianten.
struct WebButtonStyle: ButtonStyle {
    enum Kind { case normal, primary, soft, ghost, danger }
    enum Size { case small, regular, large, xl }

    var kind: Kind = .normal
    var size: Size = .regular
    var fullWidth = true
    var tint: Color?
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(size == .small ? Typo.subheadlineMedium : size == .xl ? Typo.title3 : Typo.bodyMedium)
            .multilineTextAlignment(.center)
            .foregroundStyle(foreground)
            .padding(.horizontal, size == .small ? Space.m : Space.xl)
            .padding(.vertical, size == .xl ? Space.s : 0)
            .frame(maxWidth: fullWidth ? .infinity : nil, minHeight: height)
            .background(background, in: .rect(cornerRadius: radius, style: .continuous))
            .overlay {
                if let border {
                    RoundedRectangle(cornerRadius: radius, style: .continuous).strokeBorder(border, lineWidth: 1)
                }
            }
            .opacity(isEnabled ? 1 : 0.5)
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.snappy(duration: 0.12), value: configuration.isPressed)
            .contentShape(.rect(cornerRadius: radius))
    }

    private var height: CGFloat {
        switch size {
        case .small: 44 // Web 40, auf Touch 44
        case .regular: 48
        case .large: 56
        case .xl: 76
        }
    }

    private var radius: CGFloat {
        switch size {
        case .xl: Radius.card
        case .large: Radius.large
        default: Radius.medium
        }
    }

    private var foreground: Color {
        switch kind {
        case .primary: Web.accentText
        case .soft: tint ?? Palette.accent
        case .danger: Palette.error.ink
        default: Web.textStrong
        }
    }

    private var background: Color {
        switch kind {
        case .primary: tint ?? Palette.accent
        case .soft: (tint ?? Palette.accent).opacity(0.14)
        case .ghost: .clear
        case .danger: Palette.error.soft
        case .normal: Web.surface
        }
    }

    private var border: Color? {
        switch kind {
        case .normal: Web.lineStrong
        case .danger: Palette.error.fill.opacity(0.45)
        default: nil
        }
    }
}

extension ButtonStyle where Self == WebButtonStyle {
    static var web: WebButtonStyle { WebButtonStyle() }
    static var primary: WebButtonStyle { WebButtonStyle(kind: .primary) }
    static func primary(_ tint: Color) -> WebButtonStyle { WebButtonStyle(kind: .primary, tint: tint) }
    static var secondary: WebButtonStyle { WebButtonStyle(kind: .soft) }
    static var webGhost: WebButtonStyle { WebButtonStyle(kind: .ghost, size: .small, fullWidth: false) }
    static var webDanger: WebButtonStyle { WebButtonStyle(kind: .danger) }
    static func web(_ kind: WebButtonStyle.Kind, size: WebButtonStyle.Size = .regular, fullWidth: Bool = true) -> WebButtonStyle {
        WebButtonStyle(kind: kind, size: size, fullWidth: fullWidth)
    }
}

/// Web-Knopf mit Symbol und zweiter Zeile (`.btn-text-2`), z. B. "Boost starten / 1 h · bis 65 °C".
struct TwoLineLabel: View {
    var icon: WebIcon
    var title: String
    var subtitle: String?

    var body: some View {
        HStack(spacing: Space.m) {
            Icon(icon, size: 20)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(Typo.title3)
                if let subtitle { Text(subtitle).font(Typo.subheadline).opacity(0.85) }
            }
            Spacer(minLength: 0)
        }
    }
}

// MARK: - Auswahl

/// `.segment-lg`: Auswahlraster mit Symbol, aktives Feld mit Akzentrand.
struct SegmentGrid<Value: Hashable>: View {
    struct Option: Identifiable {
        var value: Value
        var title: String
        var icon: WebIcon?
        var hint: String?
        var id: Value { value }
    }

    var options: [Option]
    var selection: Value?
    var minimum: CGFloat = 128
    /// Laufender Befehl für diese Option (Punkt oben rechts).
    var pending: Value?
    var disabled = false
    var onSelect: (Value) -> Void

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: minimum), spacing: 4)], spacing: 4) {
            ForEach(options) { option in
                let active = option.value == selection
                Button {
                    onSelect(option.value)
                } label: {
                    VStack(spacing: 2) {
                        HStack(spacing: 6) {
                            if let icon = option.icon { Icon(icon, size: 16) }
                            Text(option.title).font(Typo.bodyMedium).multilineTextAlignment(.center)
                        }
                        if let hint = option.hint {
                            Text(hint).font(Typo.caption2).foregroundStyle(Web.textDim)
                        }
                    }
                    .foregroundStyle(active ? Palette.accent : Web.textDim)
                    .padding(.horizontal, Space.m)
                    .padding(.vertical, Space.s)
                    .frame(maxWidth: .infinity, minHeight: 48)
                    .background {
                        if active {
                            RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Web.surface)
                                .shadow(color: Web.shadow, radius: 1, y: 1)
                        }
                    }
                    .overlay {
                        if active {
                            RoundedRectangle(cornerRadius: 9, style: .continuous).strokeBorder(Palette.accent, lineWidth: 1.5)
                        }
                    }
                    .overlay(alignment: .topTrailing) {
                        if pending == option.value {
                            Circle().fill(.foreground).frame(width: 6, height: 6).padding(8)
                                .phaseAnimator([1.0, 0.25]) { view, phase in view.opacity(phase) }
                        }
                    }
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(active ? .isSelected : [])
            }
        }
        .padding(4)
        .background(Web.surface3, in: .rect(cornerRadius: Radius.medium, style: .continuous))
        .disabled(disabled)
        .sensoryFeedback(.selection, trigger: selection)
    }
}

/// `.slider` mit −/+: Wert groß rechts, Spur in Tonfarbe, Griff mit Rand.
///
/// Gesendet wird erst beim Loslassen (oder per −/+), damit ein Wischen über
/// den Regler beim Scrollen keinen Befehl auslöst.
struct StepSlider: View {
    var label: LocalizedStringKey
    @Binding var value: Double
    var range: ClosedRange<Double>
    var step: Double = 1
    var tone: Color = Palette.accent
    var format: (Double) -> String
    var help: String?
    var onCommit: (Double) -> Void

    @State private var dragging = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(label).font(Typo.subheadlineMedium).foregroundStyle(Web.textStrong)
                Spacer()
                Text(format(value))
                    .font(Typo.title).monospacedDigit()
                    .foregroundStyle(dragging ? tone : Web.textStrong)
                    .contentTransition(.numericText())
            }
            HStack(spacing: Space.m) {
                roundButton(.minus, enabled: value > range.lowerBound) { nudge(-step) }
                Slider(value: $value, in: range, step: step) { editing in
                    dragging = editing
                    if !editing { onCommit(value) }
                }
                .tint(tone)
                .accessibilityLabel(Text(label))
                .accessibilityValue(Text(format(value)))
                roundButton(.plus, enabled: value < range.upperBound) { nudge(step) }
            }
            if let help { Text(help).font(Typo.subheadline).foregroundStyle(Web.textDim) }
        }
    }

    private func nudge(_ delta: Double) {
        value = min(range.upperBound, max(range.lowerBound, value + delta))
        onCommit(value)
    }

    private func roundButton(_ icon: WebIcon, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Icon(icon, size: 18)
                .foregroundStyle(Web.textStrong)
                .frame(width: 44, height: 44)
                .background(Web.surface, in: .circle)
                .overlay(Circle().strokeBorder(Web.lineStrong, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.4)
        .accessibilityLabel(Text(icon == .minus ? "Weniger" : "Mehr"))
    }
}

// MARK: - Hinweise

/// `.banner` / `.callout`: getönte Fläche mit Rand, Symbol, Titel, Text, Aktion.
struct Callout<Actions: View>: View {
    enum Tone { case info, warn, error, ok }

    var tone: Tone
    var title: String
    var text: String?
    var icon: WebIcon?
    @ViewBuilder var actions: Actions

    var body: some View {
        let color = domain
        HStack(alignment: .top, spacing: Space.m) {
            Icon(icon ?? defaultIcon, size: 18)
                .foregroundStyle(color.fill)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(Typo.headline).foregroundStyle(Web.textStrong)
                if let text { Text(text).font(Typo.subheadline).foregroundStyle(Web.text).fixedSize(horizontal: false, vertical: true) }
            }
            Spacer(minLength: 0)
            actions
        }
        .padding(.horizontal, Space.l)
        .padding(.vertical, Space.m)
        .background(tone == .ok ? Web.accentSoft : color.fill.opacity(0.1), in: .rect(cornerRadius: Radius.large, style: .continuous))
        .background(Web.surface, in: .rect(cornerRadius: Radius.large, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.large, style: .continuous).strokeBorder(color.fill.opacity(0.45), lineWidth: 1))
        .accessibilityElement(children: .combine)
    }

    private var domain: DomainColor {
        switch tone {
        case .info: Palette.info
        case .warn: Palette.warn
        case .error: Palette.error
        case .ok: Palette.ok
        }
    }

    private var defaultIcon: WebIcon {
        switch tone {
        case .info: .info
        case .warn, .error: .warning
        case .ok: .check
        }
    }
}

extension Callout where Actions == EmptyView {
    init(_ tone: Tone, _ title: String, text: String? = nil, icon: WebIcon? = nil) {
        self.init(tone: tone, title: title, text: text, icon: icon) { EmptyView() }
    }
}

/// `.disclosure`: aufklappbare Zeile mit Pfeil.
struct Disclosure<Content: View>: View {
    var title: LocalizedStringKey
    var summary: String?
    @State var isOpen = false
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.smooth(duration: 0.25)) { isOpen.toggle() }
            } label: {
                HStack(spacing: Space.m) {
                    Text(title).font(Typo.headline).foregroundStyle(Web.textStrong)
                    if let summary {
                        Text(summary).font(Typo.subheadline).foregroundStyle(Web.textDim).lineLimit(1)
                    }
                    Spacer(minLength: Space.s)
                    Icon(.chevron, size: 16)
                        .foregroundStyle(Web.textDim)
                        .rotationEffect(.degrees(isOpen ? 90 : 0))
                }
                .frame(minHeight: 56)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityAddTraits(.isButton)
            .accessibilityValue(Text(isOpen ? "geöffnet" : "geschlossen"))
            if isOpen {
                VStack(alignment: .leading, spacing: Space.l) { content }
                    .padding(.bottom, Space.l)
                    .transition(.opacity)
            }
        }
    }
}

// MARK: - Zustände

/// Lade-, Fehler- und Leerzustand für geladene Inhalte.
struct ResourceContent<Value: Sendable, Content: View, Placeholder: View>: View {
    let resource: Resource<Value>
    var isEmpty: (Value) -> Bool = { _ in false }
    var emptyTitle: LocalizedStringKey = "Noch keine Daten"
    var emptyIcon: WebIcon = .events
    var emptyMessage: LocalizedStringKey?
    @ViewBuilder var content: (Value) -> Content
    @ViewBuilder var placeholder: () -> Placeholder

    var body: some View {
        if let value = resource.value {
            if isEmpty(value) {
                EmptyState(icon: emptyIcon, title: emptyTitle, message: emptyMessage)
            } else {
                content(value)
            }
        } else if let error = resource.error, !resource.isLoading {
            ErrorState(error: error) { await resource.reload() }
        } else {
            placeholder()
                .accessibilityLabel(Text("Wird geladen"))
        }
    }
}

extension ResourceContent where Placeholder == SkeletonCards {
    init(_ resource: Resource<Value>, isEmpty: @escaping (Value) -> Bool = { _ in false },
         emptyTitle: LocalizedStringKey = "Noch keine Daten", emptyIcon: WebIcon = .events,
         emptyMessage: LocalizedStringKey? = nil, @ViewBuilder content: @escaping (Value) -> Content) {
        self.init(resource: resource, isEmpty: isEmpty, emptyTitle: emptyTitle, emptyIcon: emptyIcon,
                  emptyMessage: emptyMessage, content: content, placeholder: { SkeletonCards() })
    }
}

/// `.empty`: Symbol, Titel, Text, Aktion.
struct EmptyState<Actions: View>: View {
    var icon: WebIcon
    var title: LocalizedStringKey
    var message: LocalizedStringKey?
    @ViewBuilder var actions: Actions

    var body: some View {
        VStack(spacing: Space.m) {
            Icon(icon, size: 32).foregroundStyle(Web.textDim).opacity(0.7)
            Text(title).font(Typo.title3).foregroundStyle(Web.textStrong)
            if let message {
                Text(message).font(Typo.body).foregroundStyle(Web.textDim).multilineTextAlignment(.center)
                    .frame(maxWidth: 440)
            }
            actions
        }
        .padding(.vertical, Space.section)
        .padding(.horizontal, Space.l)
        .frame(maxWidth: .infinity)
    }
}

extension EmptyState where Actions == EmptyView {
    init(icon: WebIcon, title: LocalizedStringKey, message: LocalizedStringKey? = nil) {
        self.init(icon: icon, title: title, message: message) { EmptyView() }
    }
}

/// `.skeleton`: schimmernde Platzhalter während des ersten Ladens.
struct SkeletonCards: View {
    var count = 3
    var height: CGFloat = 120
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: Space.l) {
            ForEach(0..<count, id: \.self) { _ in
                RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                    .fill(Web.surface2)
                    .overlay {
                        if !reduceMotion {
                            TimelineView(.animation(minimumInterval: 1 / 30)) { timeline in
                                let t = timeline.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 1.4) / 1.4
                                GeometryReader { geo in
                                    LinearGradient(colors: [.clear, Web.surface3, .clear], startPoint: .leading, endPoint: .trailing)
                                        .frame(width: geo.size.width)
                                        .offset(x: (t * 2 - 1) * geo.size.width)
                                }
                            }
                            .clipShape(RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
                        }
                    }
                    .frame(height: height)
            }
        }
        .accessibilityHidden(true)
    }
}

/// Fehler mit verständlichem Text und "Erneut versuchen".
struct ErrorState: View {
    var error: APIError
    var retry: (@MainActor () async -> Void)?

    var body: some View {
        EmptyState(icon: icon, title: title, message: LocalizedStringKey(error.errorDescription ?? "")) {
            if let retry {
                Button("Erneut versuchen") { Task { await retry() } }
                    .buttonStyle(.web(.primary, fullWidth: false))
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

    private var icon: WebIcon {
        switch error {
        case .forbidden, .unauthorized: .lock
        case .offline, .unreachable, .timedOut: .refresh
        default: .warning
        }
    }
}

/// Meldung oben im Bild (Liquid Glass); verschwindet nach einigen Sekunden.
struct NoticeBanner: View {
    let notice: Notice
    var dismiss: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: Space.m) {
            Icon(icon, size: 20)
                .foregroundStyle(color.fill)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 2) {
                Text(notice.title).font(Typo.headline).foregroundStyle(Web.textStrong)
                if let message = notice.message {
                    Text(message).font(Typo.subheadline).foregroundStyle(Web.text)
                }
            }
            Spacer(minLength: 0)
            Button {
                dismiss()
            } label: {
                Icon(.close, size: 16)
                    .foregroundStyle(Web.textDim)
                    .frame(width: 44, height: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
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

    private var icon: WebIcon {
        switch notice.kind {
        case .info: .info
        case .success: .check
        case .warning, .error: .warning
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
