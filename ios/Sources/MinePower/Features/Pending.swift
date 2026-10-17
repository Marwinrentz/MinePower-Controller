import SwiftUI

// Vorläufige Bereiche; jeder wird in seinem Meilenstein durch die echte Seite ersetzt.

struct PendingSection: View {
    let section: AppSection
    var body: some View {
        Page { Card { EmptyState(icon: section.icon, title: section.title, message: "Folgt im nächsten Schritt.") } }
            .navigationTitle(section.title)
    }
}

struct WallView: View { var body: some View { PendingSection(section: .dashboard).accessibilityIdentifier("wall") } }
struct ChangelogView: View { var body: some View { PendingSection(section: .changelog) } }
