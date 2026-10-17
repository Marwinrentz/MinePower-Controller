import MinePowerKit
import SwiftUI

/// Tastaturbefehle (iPad mit Tastatur, Befehlstaste gedrückt halten zeigt sie):
/// ⌘1–⌘9 für die Bereiche in Reihenfolge der Navigation, ⌘R aktualisiert die
/// sichtbare Seite, ⌃⌘F öffnet die Wand-Ansicht (Vollbild wie am Mac).
/// Wirkt auf das Fenster im Vordergrund.
struct AppCommands: Commands {
    let session: AppSession
    @FocusedValue(\.router) private var router

    var body: some Commands {
        CommandMenu("Bereiche") {
            // Höchstens neun: Ziffern 1–9 (eine zweistellige Taste gibt es nicht).
            ForEach(Array(AppSection.allCases.prefix(9).enumerated()), id: \.element) { index, section in
                Button(section.title) { router?.show(section) }
                    .keyboardShortcut(KeyEquivalent(Character(String(index + 1))), modifiers: .command)
            }
            .disabled(router == nil || session.phase != .signedIn)
            Divider()
            Button("Wand-Ansicht") { router?.showWall = true }
                .keyboardShortcut("f", modifiers: [.command, .control])
                .disabled(router == nil || session.phase != .signedIn)
        }
        CommandGroup(after: .sidebar) {
            Button("Aktualisieren") {
                if let router { router.requestRefresh() } else { Task { try? await session.live.refresh() } }
            }
            .keyboardShortcut("r", modifiers: .command)
            .disabled(session.phase != .signedIn)
        }
    }
}

/// Router des Fensters im Vordergrund (für die Tastaturbefehle).
struct RouterFocusKey: FocusedValueKey {
    typealias Value = Router
}

extension FocusedValues {
    var router: Router? {
        get { self[RouterFocusKey.self] }
        set { self[RouterFocusKey.self] = newValue }
    }
}
