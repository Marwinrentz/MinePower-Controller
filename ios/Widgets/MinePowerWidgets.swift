import SwiftUI
import WidgetKit

/// Widget-Erweiterung: Status auf Home- und Sperrbildschirm, Live-Aktivität
/// für Boost und Sofortladen. Gleiche Farben, Schrift und Symbole wie die App
/// (Theme, Typography, WebIcon werden mit übersetzt).
@main
struct MinePowerWidgets: WidgetBundle {
    init() {
        // Die Erweiterung startet auf dem Hauptthread; Inter wie in der App laden.
        if Thread.isMainThread {
            MainActor.assumeIsolated { FontRegistry.registerFonts() }
        }
    }

    var body: some Widget {
        StatusWidget()
        MinePowerActivityWidget()
    }
}
