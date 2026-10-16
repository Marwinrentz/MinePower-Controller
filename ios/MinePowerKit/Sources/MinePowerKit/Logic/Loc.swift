import Foundation

/// Zweisprachige Texte der plattformunabhängigen Schicht (Deutsch ist Leitsprache).
///
/// Die App selbst übersetzt über ihren String Catalog. Hier, unterhalb von
/// SwiftUI und auch unter Linux, genügt eine Auswahl nach bevorzugter Sprache.
public enum Loc {
    public static var isEnglish: Bool {
        guard let first = Locale.preferredLanguages.first else { return false }
        return first.hasPrefix("en")
    }

    public static func t(_ de: String, _ en: String) -> String { isEnglish ? en : de }
}
