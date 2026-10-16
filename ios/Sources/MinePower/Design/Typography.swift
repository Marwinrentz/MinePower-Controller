import CoreText
import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

/// Inter wie im Web, mit Dynamic Type bis AX5.
///
/// Jede Größe hängt an einem System-Textstil (`relativeTo:`), dadurch wächst
/// sie mit der Einstellung "Textgröße". Zahlen laufen mit festen Ziffernbreiten
/// (`monospacedDigit`), damit Live-Werte beim Ticken nicht springen.
enum Typo {
    enum Weight: String {
        case regular = "Inter-Regular"
        case medium = "Inter-Medium"
        case semibold = "Inter-SemiBold"
        case bold = "Inter-Bold"
    }

    static func font(_ size: CGFloat, _ weight: Weight = .regular, relativeTo style: Font.TextStyle) -> Font {
        guard FontRegistry.isAvailable else {
            return .system(style, design: .default).weight(weight.systemWeight)
        }
        return .custom(weight.rawValue, size: size, relativeTo: style)
    }

    static let largeTitle = font(34, .bold, relativeTo: .largeTitle)
    static let title = font(28, .bold, relativeTo: .title)
    static let title2 = font(22, .semibold, relativeTo: .title2)
    static let title3 = font(20, .semibold, relativeTo: .title3)
    static let headline = font(17, .semibold, relativeTo: .headline)
    static let body = font(17, .regular, relativeTo: .body)
    static let bodyMedium = font(17, .medium, relativeTo: .body)
    static let callout = font(16, .regular, relativeTo: .callout)
    static let subheadline = font(15, .regular, relativeTo: .subheadline)
    static let subheadlineMedium = font(15, .medium, relativeTo: .subheadline)
    static let footnote = font(13, .regular, relativeTo: .footnote)
    static let caption = font(12, .medium, relativeTo: .caption)
    static let caption2 = font(11, .medium, relativeTo: .caption2)

    /// Große Kennzahl (Hauptwert einer Karte).
    static let metric = font(34, .semibold, relativeTo: .largeTitle)
    /// Sehr große Kennzahl (Wandanzeige, Kopf der Übersicht).
    static let hero = font(56, .bold, relativeTo: .largeTitle)
    static let metricSmall = font(22, .semibold, relativeTo: .title2)
}

private extension Typo.Weight {
    var systemWeight: Font.Weight {
        switch self {
        case .regular: .regular
        case .medium: .medium
        case .semibold: .semibold
        case .bold: .bold
        }
    }
}

/// Registriert die eingebetteten Inter-Schnitte einmal beim Start.
///
/// Über CoreText statt `UIAppFonts`, weil dieselben Quellen auf zwei Wegen
/// gebaut werden: als SwiftPM-Ziel (Schriften im Ressourcenbündel) und im
/// Xcode-Projekt (Schriften im App-Bündel).
enum FontRegistry {
    nonisolated(unsafe) private static var registered = false
    // nonisolated(unsafe): wird nur einmal synchron in App.init (Hauptthread) gesetzt.

    static var isAvailable: Bool { registered }

    @MainActor
    static func registerFonts() {
        guard !registered else { return }
        let urls = candidateBundles.flatMap { bundle in
            (bundle.urls(forResourcesWithExtension: "ttf", subdirectory: "Fonts") ?? [])
                + (bundle.urls(forResourcesWithExtension: "ttf", subdirectory: nil) ?? [])
        }
        var count = 0
        for url in Set(urls) where url.lastPathComponent.hasPrefix("Inter-") {
            var error: Unmanaged<CFError>?
            if CTFontManagerRegisterFontsForURL(url as CFURL, .process, &error) { count += 1 }
        }
        registered = count >= 4
        #if canImport(UIKit)
        if registered { styleNavigationBars() }
        #endif
    }

    private static var candidateBundles: [Bundle] {
        #if SWIFT_PACKAGE
        [Bundle.module, .main]
        #else
        [.main]
        #endif
    }

    #if canImport(UIKit)
    /// Titel der Navigationsleisten ebenfalls in Inter (mit Dynamic Type).
    @MainActor
    private static func styleNavigationBars() {
        let metrics = (large: UIFontMetrics(forTextStyle: .largeTitle), inline: UIFontMetrics(forTextStyle: .headline))
        guard let large = UIFont(name: Typo.Weight.bold.rawValue, size: 34),
              let inline = UIFont(name: Typo.Weight.semibold.rawValue, size: 17) else { return }
        let appearance = UINavigationBar.appearance()
        appearance.largeTitleTextAttributes = [.font: metrics.large.scaledFont(for: large)]
        appearance.titleTextAttributes = [.font: metrics.inline.scaledFont(for: inline)]
    }
    #endif
}

extension View {
    /// Standardschrift der App für alles darunter.
    func appTypography() -> some View {
        font(Typo.body)
    }
}
