import Foundation

/// Zweisprachige Texte der plattformunabhängigen Schicht (Deutsch ist Leitsprache).
///
/// Die App selbst übersetzt über ihren String Catalog. Hier, unterhalb von
/// SwiftUI und auch unter Linux, genügt eine Auswahl nach bevorzugter Sprache.
public enum Loc {
    /// Englisch, wenn iOS für die App Englisch gewählt hat (gleiche Wahl wie der
    /// String Catalog, auch bei z. B. "Französisch, dann Englisch" in den
    /// Einstellungen); ohne App-Bündel (Linux, Tests) die bevorzugte Sprache.
    /// Einmal ermittelt: Die Sprache einer App ändert sich nur mit einem Neustart.
    public static let isEnglish: Bool = {
        let chosen = Bundle.main.preferredLocalizations.first { $0 != "Base" }
        guard let language = chosen ?? Locale.preferredLanguages.first else { return false }
        return language.hasPrefix("en")
    }()

    /// Sprache für das Konto auf dem Server: wie die App, bei Builds ohne
    /// übersetzte Texte (xtool) aber die Sprache des Geräts.
    public static var accountLanguage: String {
        let translated = Bundle.main.localizations.filter { $0 != "Base" }.count > 1
        let english = translated ? isEnglish : Locale.preferredLanguages.first?.hasPrefix("en") == true
        return english ? "en" : "de"
    }

    public static func t(_ de: String, _ en: String) -> String { isEnglish ? en : de }
}
