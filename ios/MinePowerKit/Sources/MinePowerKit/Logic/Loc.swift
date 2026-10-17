import Foundation

/// Zweisprachige Texte der plattformunabhängigen Schicht (Deutsch ist Leitsprache).
///
/// Die App selbst übersetzt über ihren String Catalog. Hier, unterhalb von
/// SwiftUI und auch unter Linux, genügt eine Auswahl nach bevorzugter Sprache.
public enum Loc {
    /// Englisch, wenn iOS für die App Englisch gewählt hat (gleiche Wahl wie der
    /// String Catalog, auch bei z. B. "Französisch, dann Englisch" in den
    /// Einstellungen); ohne App-Bündel (Linux, Tests) die bevorzugte Sprache.
    public static var isEnglish: Bool {
        let chosen = Bundle.main.preferredLocalizations.first { $0 != "Base" }
        guard let language = chosen ?? Locale.preferredLanguages.first else { return false }
        return language.hasPrefix("en")
    }

    public static func t(_ de: String, _ en: String) -> String { isEnglish ? en : de }
}
