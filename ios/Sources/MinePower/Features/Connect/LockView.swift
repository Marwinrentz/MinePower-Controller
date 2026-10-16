import MinePowerKit
import SwiftUI

/// App-Sperre: Das Token im Schlüsselbund ist nur nach Face ID lesbar.
struct LockView: View {
    @Environment(AppSession.self) private var session
    @State private var isUnlocking = false
    @State private var didAutoStart = false

    var body: some View {
        VStack(spacing: Space.xl) {
            Spacer()
            BrandMark(size: 72)
            VStack(spacing: Space.s) {
                Text("MinePower ist gesperrt")
                    .font(Typo.title2)
                    .accessibilityAddTraits(.isHeader)
                if let server = session.server {
                    Text(server.name).font(Typo.subheadline).foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button {
                Task { await unlock() }
            } label: {
                Label("Mit \(Biometrics.label) entsperren", systemImage: Biometrics.symbol)
            }
            .buttonStyle(.primary)
            .disabled(isUnlocking)
            .frame(maxWidth: 420)
            Button("Abmelden") { Task { await session.signOut() } }
                .font(Typo.subheadlineMedium)
                .frame(minHeight: 44)
        }
        .padding(Space.xl)
        .frame(maxWidth: .infinity)
        .background(Color(uiColor: .systemGroupedBackground))
        .task {
            // Einmal automatisch fragen; nach Abbruch nur noch auf Knopfdruck.
            guard !didAutoStart else { return }
            didAutoStart = true
            await unlock()
        }
    }

    private func unlock() async {
        isUnlocking = true
        defer { isUnlocking = false }
        await session.unlock()
    }
}
