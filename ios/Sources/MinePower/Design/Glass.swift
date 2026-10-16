import SwiftUI

/// Liquid Glass, an einer Stelle gekapselt.
///
/// Glas gehört nur auf die **Bedienebene**: Leisten, schwebende Knöpfe,
/// Umschalter über Inhalt. Karten, Listen und Diagramme bleiben normale
/// Flächen – sonst verliert Glas seine Aufgabe, "darüber liegt Bedienung"
/// zu signalisieren. Deshalb gibt es hier keinen Glaskarten-Modifier.
///
/// iOS 26: echtes Liquid Glass. iOS 17/18: `.ultraThinMaterial` mit feiner
/// Kante; bei "Transparenz reduzieren" eine deckende Fläche.
///
/// `#if compiler(>=6.2)` schneidet die iOS-26-Aufrufe weg, wenn ein älteres
/// SDK baut; `#available` entscheidet zur Laufzeit.
extension View {
    /// Schwebendes Bedienelement in Kapselform.
    func glassControl(tint: Color? = nil, interactive: Bool = true) -> some View {
        modifier(GlassSurface(tint: tint, interactive: interactive, shape: .capsule))
    }

    /// Leiste oder Block der Bedienebene mit abgerundeten Ecken.
    func glassBar(cornerRadius: CGFloat = Radius.card, tint: Color? = nil) -> some View {
        modifier(GlassSurface(tint: tint, interactive: false, shape: .rect(cornerRadius)))
    }

    /// Weiche Kante, wo Inhalt unter Leisten verschwindet (iOS 26).
    @ViewBuilder
    func softScrollEdges() -> some View {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) {
            scrollEdgeEffectStyle(.soft, for: .all)
        } else {
            self
        }
        #else
        self
        #endif
    }
}

/// Mehrere Glaselemente, die beim Annähern ineinanderfließen statt sich zu überlappen.
struct GlassGroup<Content: View>: View {
    var spacing: CGFloat = 12
    @ViewBuilder var content: Content

    var body: some View {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) {
            GlassEffectContainer(spacing: spacing) { content }
        } else {
            content
        }
        #else
        content
        #endif
    }
}

private struct GlassSurface: ViewModifier {
    enum Shape {
        case capsule
        case rect(CGFloat)
    }

    var tint: Color?
    var interactive: Bool
    var shape: Shape
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    func body(content: Content) -> some View {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) {
            glass(content)
        } else {
            fallback(content)
        }
        #else
        fallback(content)
        #endif
    }

    #if compiler(>=6.2)
    @available(iOS 26.0, *)
    @ViewBuilder
    private func glass(_ content: Content) -> some View {
        let base = tint.map { Glass.regular.tint($0) } ?? .regular
        let glass = interactive ? base.interactive() : base
        switch shape {
        case .capsule: content.glassEffect(glass, in: .capsule)
        case .rect(let radius): content.glassEffect(glass, in: .rect(cornerRadius: radius))
        }
    }
    #endif

    @ViewBuilder
    private func fallback(_ content: Content) -> some View {
        switch shape {
        case .capsule:
            content
                .background { fallbackFill(Capsule()) }
                .overlay(Capsule().strokeBorder(.primary.opacity(0.08), lineWidth: 0.5))
        case .rect(let radius):
            let rect = RoundedRectangle(cornerRadius: radius, style: .continuous)
            content
                .background { fallbackFill(rect) }
                .overlay(rect.strokeBorder(.primary.opacity(0.08), lineWidth: 0.5))
        }
    }

    @ViewBuilder
    private func fallbackFill(_ shape: some InsettableShape) -> some View {
        if reduceTransparency {
            shape.fill(Color(uiColor: .secondarySystemBackground))
        } else if let tint {
            shape.fill(tint.opacity(0.9))
        } else {
            shape.fill(.ultraThinMaterial)
        }
    }
}

/// Hauptaktion eines Bildschirms (genau eine je Kontext).
struct PrimaryButtonStyle: ButtonStyle {
    var tint: Color = Palette.accent
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Typo.headline)
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity, minHeight: 50)
            .padding(.horizontal, Space.l)
            .background(tint.opacity(isEnabled ? 1 : 0.4), in: .rect(cornerRadius: 16, style: .continuous))
            .opacity(configuration.isPressed ? 0.8 : 1)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.snappy(duration: 0.18), value: configuration.isPressed)
    }
}

/// Zweitrangige Aktion als ruhige Fläche.
struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Typo.headline)
            .foregroundStyle(Palette.accent)
            .frame(maxWidth: .infinity, minHeight: 50)
            .padding(.horizontal, Space.l)
            .background(Palette.accent.opacity(0.12), in: .rect(cornerRadius: 16, style: .continuous))
            .opacity(configuration.isPressed ? 0.7 : 1)
            .animation(.snappy(duration: 0.18), value: configuration.isPressed)
    }
}

/// Schwebender Glasknopf (Pausieren, Boost, Wandmodus verlassen).
struct GlassButtonStyle: ButtonStyle {
    var tint: Color?
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Typo.subheadlineMedium)
            .padding(.horizontal, Space.l)
            .frame(minHeight: 44)
            .glassControl(tint: tint)
            .opacity(configuration.isPressed ? 0.75 : 1)
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.snappy(duration: 0.18), value: configuration.isPressed)
    }
}

extension ButtonStyle where Self == PrimaryButtonStyle {
    static var primary: PrimaryButtonStyle { PrimaryButtonStyle() }
    static func primary(_ tint: Color) -> PrimaryButtonStyle { PrimaryButtonStyle(tint: tint) }
}

extension ButtonStyle where Self == SecondaryButtonStyle {
    static var secondary: SecondaryButtonStyle { SecondaryButtonStyle() }
}

extension ButtonStyle where Self == GlassButtonStyle {
    static var floatingGlass: GlassButtonStyle { GlassButtonStyle() }
    static func floatingGlass(_ tint: Color) -> GlassButtonStyle { GlassButtonStyle(tint: tint) }
}
