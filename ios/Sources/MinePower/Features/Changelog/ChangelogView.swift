import MinePowerKit
import SwiftUI

/// Changelog (Spiegel von `Changelog.tsx`): alle Versionen des Servers,
/// darüber der Hinweis, wie man Fehler meldet.
struct ChangelogView: View {
    @Environment(AppSession.self) private var session
    @Environment(Router.self) private var router
    @State private var changelog: Resource<Changelog>?

    var body: some View {
        Page(refresh: { await changelog?.reload() }) {
            if let version = changelog?.value?.version {
                Text("Version \(version)").font(Typo.subheadline).foregroundStyle(Web.textDim)
            }
            Callout(.info, String(localized: "Fehler melden"),
                    text: String(localized: "Diagnosebericht unter Diagnose als JSON exportieren und an ein GitHub-Issue anhängen. Zugangsdaten sind geschwärzt."))
            FlowLayout(spacing: Space.s) {
                Button { router.show(.diagnose) } label: {
                    HStack(spacing: 6) { Icon(.pulse, size: 16); Text("Zur Diagnose") }
                }
                .buttonStyle(.web(.ghost, fullWidth: false))
                Link(destination: githubIssuesURL) {
                    HStack(spacing: 6) { Icon(.github, size: 16); Text("Issue auf GitHub") }
                }
                .buttonStyle(.web(.ghost, fullWidth: false))
            }
            Card {
                if let changelog {
                    ResourceContent(resource: changelog, isEmpty: { $0.entries.isEmpty }, emptyTitle: "Noch keine Einträge",
                                    emptyIcon: .events) { data in
                        ChangelogList(entries: data.entries)
                    } placeholder: {
                        SkeletonCards(count: 1, height: 240)
                    }
                } else {
                    SkeletonCards(count: 1, height: 240)
                }
            }
        }
        .navigationTitle("Changelog")
        .toolbar { BrandToolbar() }
        .task {
            if changelog == nil { changelog = Resource { [api = session.api] in try await api.changelog() } }
            await changelog?.load(maxAge: 300)
        }
    }
}

/// Neues Issue im Projekt (wie `GITHUB_ISSUES_URL` im Web).
let githubIssuesURL = githubURL.appending(path: "issues/new")

/// `ChangelogList`: Versionen mit Neu / Geändert / Behoben.
struct ChangelogList: View {
    let entries: [ChangelogEntry]

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            ForEach(entries) { entry in
                VStack(alignment: .leading, spacing: Space.s) {
                    HStack(alignment: .firstTextBaseline, spacing: Space.m) {
                        Text(verbatim: entry.version).font(Typo.title3).monospacedDigit().foregroundStyle(Web.textStrong)
                        Text(Fmt().calendarDate(entry.date)).font(Typo.subheadlineMedium).foregroundStyle(Web.textDim)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityAddTraits(.isHeader)
                    group("Neu", entry.new)
                    group("Geändert", entry.changed)
                    group("Behoben", entry.fixed)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func group(_ title: LocalizedStringKey, _ lines: [String]) -> some View {
        if !lines.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(Typo.footnote.weight(.bold)).foregroundStyle(Web.textDim)
                    .padding(.top, Space.xs)
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                        Text(verbatim: "•").foregroundStyle(Web.textDim)
                        Text(line).font(Typo.body).foregroundStyle(Web.text).fixedSize(horizontal: false, vertical: true)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
        }
    }
}

/// Nach einem Server-Update einmal die neuen Einträge zeigen (`UpdatePopup`).
/// Schließen markiert sie im Konto als gesehen, auch für die Weboberfläche.
struct UpdateSheet: ViewModifier {
    @Environment(AppSession.self) private var session
    @State private var unseen: Changelog?

    func body(content: Content) -> some View {
        content
            .task(id: session.user?.id) {
                guard session.user != nil, !session.isDemo else { return }
                if let changelog = try? await session.api.changelog(), !changelog.unseen.isEmpty {
                    unseen = changelog
                }
            }
            .sheet(item: Binding(get: { unseen.map(UnseenChangelog.init) }, set: { if $0 == nil, unseen != nil { close() } })) { item in
                NavigationStack {
                    ScrollView {
                        ChangelogList(entries: item.changelog.unseen)
                            .padding(Space.l)
                    }
                    .background(AppBackground())
                    .navigationTitle(Text("Aktualisiert auf \(item.changelog.version)"))
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .confirmationAction) {
                            Button("Schließen") { close() }
                        }
                    }
                }
                .presentationDetents([.medium, .large])
            }
    }

    private func close() {
        unseen = nil
        Task { _ = try? await session.api.markChangelogSeen() }
    }

    struct UnseenChangelog: Identifiable {
        let changelog: Changelog
        var id: String { changelog.version }
    }
}
