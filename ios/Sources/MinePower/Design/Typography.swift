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
        case extraBold = "Inter-ExtraBold"
    }

    static func font(_ size: CGFloat, _ weight: Weight = .regular, relativeTo style: Font.TextStyle) -> Font {
        guard FontRegistry.isAvailable else {
            return .system(style, design: .default).weight(weight.systemWeight)
        }
        return .custom(weight.rawValue, size: size, relativeTo: style)
    }

    // Größen wie im Web (--text-xs … --text-3xl), Gewichte 650 → SemiBold,
    // 680–760 → Bold, 780+ → ExtraBold.
    static let largeTitle = font(28, .bold, relativeTo: .largeTitle)          // .page-title
    static let hero = font(24, .extraBold, relativeTo: .title)                 // .hero-title
    static let title = font(22, .bold, relativeTo: .title)                     // --text-xl
    static let title2 = font(20, .bold, relativeTo: .title2)
    static let title3 = font(18, .bold, relativeTo: .title3)                   // h2
    static let headline = font(15.5, .bold, relativeTo: .headline)             // h3, strong
    static let body = font(15.5, .regular, relativeTo: .body)                  // --text-md
    static let bodyMedium = font(15.5, .semibold, relativeTo: .body)           // Knöpfe (650)
    static let callout = font(15.5, .regular, relativeTo: .callout)
    static let subheadline = font(14, .regular, relativeTo: .subheadline)      // --text-sm
    static let subheadlineMedium = font(14, .semibold, relativeTo: .subheadline)
    static let footnote = font(13, .regular, relativeTo: .footnote)
    static let caption = font(12.5, .bold, relativeTo: .caption)               // .badge (720)
    static let caption2 = font(11.5, .semibold, relativeTo: .caption2)

    /// `.stat-value` (740)
    static let stat = font(22, .bold, relativeTo: .title2)
    /// Große Kennzahl einer Karte.
    static let metric = font(34, .extraBold, relativeTo: .largeTitle)
    /// `.bat-soc` – sehr große Zahl (Ladestand, Wandanzeige).
    static let display = font(56, .extraBold, relativeTo: .largeTitle)
    static let metricSmall = font(18, .extraBold, relativeTo: .headline)       // .pflow-value (780)
}

private extension Typo.Weight {
    var systemWeight: Font.Weight {
        switch self {
        case .regular: .regular
        case .medium: .medium
        case .semibold: .semibold
        case .bold: .bold
        case .extraBold: .heavy
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
        registered = count >= 5
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
        // Seitentitel wie `.page-title` im Web (760 → Bold), etwas größer für die iOS-Leiste.
        guard let large = UIFont(name: Typo.Weight.bold.rawValue, size: 31),
              let inline = UIFont(name: Typo.Weight.bold.rawValue, size: 17) else { return }
        let appearance = UINavigationBar.appearance()
        appearance.largeTitleTextAttributes = [.font: metrics.large.scaledFont(for: large)]
        appearance.titleTextAttributes = [.font: metrics.inline.scaledFont(for: inline)]
        let tab = UITabBarItem.appearance()
        if let label = UIFont(name: Typo.Weight.semibold.rawValue, size: 11) {
            tab.setTitleTextAttributes([.font: label], for: .normal)
        }
    }
    #endif
}

extension View {
    /// Standardschrift der App für alles darunter.
    func appTypography() -> some View {
        font(Typo.body)
    }
}
