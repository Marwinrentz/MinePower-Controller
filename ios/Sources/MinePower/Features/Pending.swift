import SwiftUI

// Vorläufige Bereiche; jeder wird in seinem Meilenstein durch die echte Seite ersetzt.

struct PendingSection: View {
    let section: AppSection
    var body: some View {
        ContentUnavailableView(section.title, systemImage: section.symbol)
            .navigationTitle(section.title)
    }
}

struct ConnectView: View { var body: some View { PendingSection(section: .settings) } }
struct SignInView: View { var body: some View { PendingSection(section: .settings) } }
struct LockView: View { var body: some View { PendingSection(section: .settings) } }
struct WallView: View { var body: some View { PendingSection(section: .dashboard).accessibilityIdentifier("wall") } }
struct DashboardView: View { var body: some View { PendingSection(section: .dashboard) } }
struct CarView: View { var body: some View { PendingSection(section: .car) } }
struct WaterView: View { var body: some View { PendingSection(section: .water) } }
struct BatteryView: View { var body: some View { PendingSection(section: .battery) } }
struct TariffView: View { var body: some View { PendingSection(section: .tariff) } }
struct StatisticsView: View { var body: some View { PendingSection(section: .statistics) } }
struct DiagnoseView: View { var body: some View { PendingSection(section: .diagnose) } }
struct SettingsView: View { var body: some View { PendingSection(section: .settings) } }
struct ChangelogView: View { var body: some View { PendingSection(section: .changelog) } }
