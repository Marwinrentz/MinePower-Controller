import MinePowerKit
import SwiftUI

/// Tastaturbefehle (iPad mit Tastatur, Befehlstaste gedrückt halten zeigt sie):
/// ⌘1–⌘9 für die Bereiche in Reihenfolge der Navigation, ⌘R aktualisiert,
/// ⇧⌘W öffnet die Wand-Ansicht. Wirkt auf das Fenster im Vordergrund.
struct AppCommands: Commands {
    let session: AppSession
    @FocusedValue(\.router) private var router

    var body: some Commands {
        CommandMenu("Bereiche") {
            ForEach(Array(AppSection.allCases.enumerated()), id: \.element) { index, section in
                Button(section.title) { router?.show(section) }
                    .keyboardShortcut(KeyEquivalent(Character(String(index + 1))), modifiers: .command)
            }
            .disabled(router == nil || session.phase != .signedIn)
            Divider()
            Button("Wand-Ansicht") { router?.showWall = true }
                .keyboardShortcut("w", modifiers: [.command, .shift])
                .disabled(router == nil || session.phase != .signedIn)
        }
        CommandGroup(after: .sidebar) {
            Button("Aktualisieren") {
                Task { try? await session.live.refresh() }
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
