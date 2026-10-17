import MinePowerKit
import SwiftUI

/// Einstellungen → KI-Assistent (Spiegel von `McpSettings.tsx`):
/// Zugangsschlüssel, Anleitung je KI-Anwendung, Funktionsliste.
struct McpSettings: View {
    @Environment(AppSession.self) private var session
    @AppStorage("mcp.publicBase") private var publicBase = ""
    @State private var tokens: [McpToken]?
    @State private var tools: [McpTool] = []
    @State private var name = ""
    @State private var role: User.Role = .readonly
    @State private var fresh: String?
    @State private var client = "claude_desktop"
    @State private var creating = false
    @State private var revoking: McpToken?

    private var url: String {
        guard let base = session.server?.url.absoluteString else { return "/mcp" }
        return base.replacing(/\/+$/, with: "") + "/mcp"
    }

    private var insecure: Bool {
        guard let server = session.server?.url, !session.isDemo else { return false }
        return server.scheme == "http" && !["localhost", "127.0.0.1"].contains(server.host ?? "")
    }

    var body: some View {
        let userRole = session.user?.role ?? .readonly
        let roles = User.Role.allCases.filter { $0 <= userRole }
        let clients = McpClient.all(url: url, token: fresh ?? String(localized: "<SCHLÜSSEL>"),
                                    publicBase: publicBase.trimmingCharacters(in: .whitespaces).isEmpty
                                        ? String(localized: "https://<öffentliche-adresse>") : publicBase.trimmingCharacters(in: .whitespaces))
        let active = clients.first { $0.id == client } ?? clients[0]

        Card {
            VStack(alignment: .leading, spacing: Space.l) {
                CardHead("KI-Assistent (MCP)",
                         subtitle: String(localized: "Lesen, Steuern und Einrichten per KI über das Model Context Protocol"))
                Field(label: "Server-Adresse") { CopyBlock(text: url) }
                if insecure {
                    Callout(.warn, String(localized: "Unverschlüsselt (HTTP). Nur im Heimnetz nutzen; von außen über VPN oder HTTPS-Proxy."))
                }
                GroupTitle("Zugangsschlüssel")
                if let tokens {
                    if tokens.isEmpty {
                        Text("Kein Schlüssel angelegt").font(Typo.body).foregroundStyle(Web.textDim)
                    } else {
                        VStack(spacing: 0) {
                            ForEach(tokens) { token in
                                tokenRow(token)
                                if token.id != tokens.last?.id { Divider().overlay(Web.line) }
                            }
                        }
                    }
                } else {
                    SkeletonCards(count: 1, height: 80)
                }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), spacing: Space.l, alignment: .top)], spacing: Space.l) {
                    Field(label: "Bezeichnung") {
                        AppTextField(verbatim: "Claude Desktop", text: Binding(get: { name }, set: { name = String($0.prefix(80)) }))
                    }
                    Field(label: "Rechte", help: role.mcpHelp) {
                        SegmentGrid(options: roles.map { .init(value: $0, title: $0.mcpTitle) }, selection: role, minimum: 90) { role = $0 }
                    }
                }
                HStack {
                    Spacer()
                    Button {
                        Task { await create() }
                    } label: {
                        HStack(spacing: 6) {
                            if creating { ProgressView().tint(Web.accentText) }
                            Icon(.plus, size: 16)
                            Text("Schlüssel erzeugen")
                        }
                    }
                    .buttonStyle(.web(.primary, fullWidth: false))
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty || creating)
                }
                if let fresh {
                    VStack(alignment: .leading, spacing: Space.s) {
                        Callout(.ok, String(localized: "Neuer Schlüssel"), text: String(localized: "Nur jetzt sichtbar. In der KI-Anwendung eintragen."))
                        CopyBlock(text: fresh)
                    }
                }
            }
        }

        Card {
            VStack(alignment: .leading, spacing: Space.l) {
                CardHead("Anleitung", subtitle: String(localized: "Konfiguration je Anwendung"))
                FlowLayout(spacing: Space.s) {
                    ForEach(clients) { item in
                        let on = item.id == active.id
                        Button { client = item.id } label: {
                            Text(item.name)
                                .font(Typo.subheadlineMedium)
                                .foregroundStyle(on ? Palette.accent : Web.text)
                                .padding(.horizontal, Space.m)
                                .frame(minHeight: 36)
                                .background(on ? Web.accentSoft : Web.surface, in: .capsule)
                                .overlay(Capsule().strokeBorder(on ? Palette.accent : Web.line, lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                        .accessibilityAddTraits(on ? .isSelected : [])
                    }
                }
                if active.urlOnly {
                    Field(label: "Öffentliche Adresse (HTTPS)",
                          help: String(localized: "z. B. über Cloudflare Tunnel, Tailscale Funnel oder Reverse-Proxy")) {
                        AppTextField(verbatim: "https://minepower.example.com", text: $publicBase)
                            .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    }
                }
                VStack(alignment: .leading, spacing: Space.s) {
                    ForEach(Array(active.steps.enumerated()), id: \.offset) { index, step in
                        HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                            Text("\(index + 1).").font(Typo.bodyMedium).monospacedDigit().foregroundStyle(Web.textDim)
                            Text(step).font(Typo.body).foregroundStyle(Web.text).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                CopyBlock(text: active.code)
                if fresh == nil {
                    Text("Platzhalter <SCHLÜSSEL> durch einen erzeugten Schlüssel ersetzen.")
                        .font(Typo.subheadline).foregroundStyle(Web.textDim)
                }
                if active.urlOnly {
                    Callout(.warn, String(localized: "Die Adresse enthält den Schlüssel. Wer sie kennt, hat dessen Rechte. Eigenen Schlüssel je Dienst anlegen, möglichst nur 'Lesen' oder 'Steuern'."))
                }
            }
        }

        Card {
            VStack(alignment: .leading, spacing: Space.m) {
                CardHead("Funktionen", subtitle: String(localized: "Verfügbar je nach Rechten des Schlüssels"))
                ForEach([User.Role.readonly, .user, .admin], id: \.self) { group in
                    let list = tools.filter { $0.role == group }
                    if !list.isEmpty {
                        GroupTitle(LocalizedStringKey(group.mcpTitle))
                        VStack(alignment: .leading, spacing: Space.s) {
                            ForEach(list) { tool in
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(tool.name).font(.system(.footnote, design: .monospaced)).foregroundStyle(Web.textStrong)
                                    Text(tool.title).font(Typo.subheadline).foregroundStyle(Web.textDim)
                                }
                                .accessibilityElement(children: .combine)
                            }
                        }
                    }
                }
                Link(destination: URL(string: "https://modelcontextprotocol.io")!) {
                    HStack(spacing: 6) { Icon(.info, size: 16); Text(verbatim: "modelcontextprotocol.io") }
                }
                .buttonStyle(.web(.normal, fullWidth: false))
            }
        }
        .task {
            await load()
            tools = (try? await session.api.mcpTools()) ?? []
        }
        .confirmationDialog(Text("\(revoking?.name ?? "") widerrufen?"), isPresented: Binding(
            get: { revoking != nil }, set: { if !$0 { revoking = nil } }), titleVisibility: .visible, presenting: revoking) { token in
            Button("Widerrufen", role: .destructive) { Task { await revoke(token) } }
            Button("Abbrechen", role: .cancel) {}
        } message: { _ in
            Text("KI-Anwendungen mit diesem Schlüssel verlieren sofort den Zugriff.")
        }
    }

    private func tokenRow(_ token: McpToken) -> some View {
        HStack(spacing: Space.m) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: Space.s) {
                    Text(token.name).font(Typo.bodyMedium).foregroundStyle(Web.textStrong)
                    Text(verbatim: "\(token.prefix)…").font(Typo.footnote).monospacedDigit().foregroundStyle(Web.textDim)
                }
                HStack(spacing: Space.s) {
                    Badge(text: token.role.mcpTitle,
                          tone: token.role == .admin ? Palette.warn : token.role == .user ? Palette.accentDomain : nil)
                    Text(token.lastUsedAt.map { String(localized: "Zuletzt \(Fmt().ago($0))") } ?? String(localized: "Nie benutzt"))
                        .font(Typo.footnote).foregroundStyle(Web.textDim)
                }
            }
            Spacer(minLength: Space.s)
            Button { revoking = token } label: {
                HStack(spacing: 4) { Icon(.close, size: 16); Text("Widerrufen") }
            }
            .buttonStyle(.web(.ghost, size: .small, fullWidth: false))
        }
        .padding(.vertical, Space.s)
    }

    private func load() async {
        do { tokens = try await session.api.mcpTokens() } catch { tokens = tokens ?? []; session.handle(error) }
    }

    private func create() async {
        creating = true
        defer { creating = false }
        do {
            let token = try await session.api.createMcpToken(name: name.trimmingCharacters(in: .whitespaces), role: role)
            fresh = token.token
            name = ""
            await load()
        } catch {
            session.handle(error)
        }
    }

    private func revoke(_ token: McpToken) async {
        do {
            try await session.api.revokeMcpToken(token.id)
            await load()
        } catch {
            session.handle(error)
        }
    }
}

extension User.Role {
    /// Rechte eines KI-Schlüssels (`mcp.role.*`).
    var mcpTitle: String {
        switch self {
        case .readonly: String(localized: "Lesen")
        case .user: String(localized: "Steuern")
        case .admin: String(localized: "Einrichten")
        }
    }

    var mcpHelp: String {
        switch self {
        case .readonly: String(localized: "Werte, Verlauf, Ladeliste, Diagnose")
        case .user: String(localized: "zusätzlich Betriebsarten, Boost, Batterie, Pause")
        case .admin: String(localized: "zusätzlich Geräte, Zeitfenster, Reihenfolge, Einstellungen")
        }
    }
}

/// `.copy-block`: Text in Festbreitenschrift mit "Kopieren".
struct CopyBlock: View {
    let text: String
    @Environment(AppSession.self) private var session
    @State private var copied = 0

    var body: some View {
        VStack(alignment: .trailing, spacing: 0) {
            ScrollView(.horizontal, showsIndicators: false) {
                Text(verbatim: text)
                    .font(.system(.footnote, design: .monospaced))
                    .foregroundStyle(Web.textStrong)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: true, vertical: true)
                    .padding(Space.m)
            }
            Button {
                UIPasteboard.general.string = text
                copied += 1
                session.show(Notice(.success, String(localized: "Kopiert")))
            } label: {
                HStack(spacing: 4) { Icon(.check, size: 14); Text("Kopieren") }
            }
            .buttonStyle(.web(.ghost, size: .small, fullWidth: false))
            .padding([.trailing, .bottom], Space.xs)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Web.surface2, in: .rect(cornerRadius: Radius.medium, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.medium, style: .continuous).strokeBorder(Web.line, lineWidth: 1))
        .sensoryFeedback(.success, trigger: copied)
    }
}
