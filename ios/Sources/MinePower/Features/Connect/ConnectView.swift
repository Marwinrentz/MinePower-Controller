import MinePowerKit
import SwiftUI

/// Erster Bildschirm: Server im Heimnetz finden, Adresse eingeben oder Demo.
///
/// Aufbau von oben nach unten nach Wahrscheinlichkeit: Die meisten finden
/// ihren Server automatisch; wer von unterwegs per https verbindet, tippt die
/// Adresse; wer nur schauen will, nimmt die Demo.
struct ConnectView: View {
    @Environment(AppSession.self) private var session
    @State private var discovery = ServerDiscovery()
    @State private var address = ""
    @State private var isChecking = false
    @State private var error: APIError?
    @State private var trustRequest: TrustRequest?
    @State private var showHelp = false
    @FocusState private var addressFocused: Bool

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xxl) {
                    header
                    discoveredSection
                    manualSection
                    demoSection
                    if !session.directory.profiles.isEmpty { savedSection }
                }
                .padding(.horizontal, Space.xl)
                .padding(.vertical, Space.xxl)
                .frame(maxWidth: 560)
                .frame(maxWidth: .infinity)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Color(uiColor: .systemGroupedBackground))
            .toolbar {
                if session.directory.active != nil {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Abbrechen") { Task { await session.cancelAddingServer() } }
                    }
                }
            }
            .sheet(item: $trustRequest) { request in
                TrustCertificateSheet(request: request) {
                    session.trust(host: request.host, fingerprint: request.fingerprint)
                    trustRequest = nil
                    Task { await connect(request.address) }
                }
            }
            .sheet(isPresented: $showHelp) { AddressHelpSheet() }
        }
        .task { discovery.start(using: session.serverProbe) }
        .onDisappear { discovery.stop() }
    }

    // MARK: - Abschnitte

    private var header: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            BrandMark(size: 56)
            Text("Mit MinePower verbinden")
                .font(Typo.largeTitle)
                .accessibilityAddTraits(.isHeader)
            Text(Brand.claim)
                .font(Typo.body)
                .foregroundStyle(.secondary)
        }
    }

    private var discoveredSection: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            HStack {
                Text("Im Heimnetz gefunden").font(Typo.headline)
                Spacer()
                if discovery.isScanning {
                    ProgressView(value: discovery.progress)
                        .frame(width: 64)
                        .accessibilityLabel(Text("Suche läuft"))
                } else {
                    Button("Erneut suchen") { discovery.start(using: session.serverProbe) }
                        .font(Typo.subheadlineMedium)
                }
            }
            if discovery.found.isEmpty {
                Card {
                    HStack(spacing: Space.m) {
                        Image(systemName: discovery.isScanning ? "antenna.radiowaves.left.and.right" : "magnifyingglass")
                            .font(.title2)
                            .foregroundStyle(.secondary)
                            .symbolEffect(.variableColor.iterative, isActive: discovery.isScanning)
                            .accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(discovery.isScanning ? "Suche läuft …" : "Kein Server gefunden")
                                .font(Typo.bodyMedium)
                            Text(emptyHint)
                                .font(Typo.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            } else {
                VStack(spacing: Space.s) {
                    ForEach(discovery.found) { item in
                        FoundServerRow(item: item) { Task { await connect(item.result.url.absoluteString) } }
                    }
                }
            }
        }
    }

    private var emptyHint: LocalizedStringKey {
        if discovery.isScanning { return "MinePower antwortet meist auf Port 8080." }
        if discovery.ownAddress == nil { return "Nicht im WLAN? Gib die Adresse unten ein." }
        return "Gib die Adresse unten ein oder prüfe, ob der Server läuft."
    }

    private var manualSection: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            HStack {
                Text("Adresse eingeben").font(Typo.headline)
                Spacer()
                Button {
                    showHelp = true
                } label: {
                    Label("Wo finde ich die Adresse?", web: .info)
                        .labelStyle(.iconOnly)
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel(Text("Wo finde ich die Adresse?"))
            }
            AppTextField(title: "z. B. 192.168.178.20:8080", text: $address, symbol: "network", identifier: "address")
                .textContentType(.URL)
                .keyboardType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.go)
                .focused($addressFocused)
                .onSubmit { Task { await connect(address) } }
            if let error {
                InlineError(error: error)
            }
            Button {
                Task { await connect(address) }
            } label: {
                HStack {
                    if isChecking { ProgressView().tint(.white) }
                    Text(isChecking ? "Prüfe …" : "Verbinden")
                }
            }
            .buttonStyle(.primary)
            .disabled(address.trimmingCharacters(in: .whitespaces).isEmpty || isChecking)
            .accessibilityIdentifier("connect")
        }
    }

    private var demoSection: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            Button {
                Task { await session.startDemo() }
            } label: {
                Label("Demo ansehen", web: .play)
            }
            .buttonStyle(.secondary)
            .accessibilityIdentifier("demo")
            Text("Eine Beispielanlage mit Sonne, Auto und Warmwasser – ohne eigenen Server. Nichts wird gesteuert.")
                .font(Typo.footnote)
                .foregroundStyle(.secondary)
        }
    }

    private var savedSection: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            Text("Gespeicherte Server").font(Typo.headline)
            VStack(spacing: Space.s) {
                ForEach(session.directory.profiles) { profile in
                    Button {
                        Task { await session.switchServer(to: profile.id) }
                    } label: {
                        ServerRow(name: profile.name, address: profile.displayAddress,
                                  detail: profile.lastVersion.map { "MinePower \($0)" },
                                  icon: profile.isDemo ? .play : .display)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    // MARK: - Verbinden

    private func connect(_ text: String) async {
        addressFocused = false
        error = nil
        isChecking = true
        defer { isChecking = false }
        do {
            let result = try await session.probe(text)
            discovery.stop()
            await session.adopt(result)
        } catch let APIError.untrustedCertificate(host, fingerprint) {
            if let fingerprint {
                trustRequest = TrustRequest(address: text, host: host, fingerprint: fingerprint)
            } else {
                error = .untrustedCertificate(host: host, fingerprint: nil)
            }
        } catch let apiError as APIError {
            if apiError != .cancelled { error = apiError }
        } catch {
            self.error = .unreachable(error.localizedDescription)
        }
    }
}

// MARK: - Bausteine

struct ServerRow: View {
    var name: String
    var address: String
    var detail: String?
    var icon: WebIcon = .display

    var body: some View {
        HStack(spacing: Space.m) {
            DomainIcon(icon: icon, color: Palette.pv, size: 40)
            VStack(alignment: .leading, spacing: 2) {
                Text(name).font(Typo.bodyMedium).foregroundStyle(.primary)
                Text(address).font(Typo.footnote).foregroundStyle(.secondary)
                if let detail {
                    Text(detail).font(Typo.caption).foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: Space.s)
            Icon(.chevron, size: 16)
                .foregroundStyle(Web.textDim)
        }
        .padding(Space.m)
        .frame(minHeight: 64)
        .background(Color(uiColor: .secondarySystemGroupedBackground), in: .rect(cornerRadius: 18, style: .continuous))
        .contentShape(.rect)
        .accessibilityElement(children: .combine)
    }
}

private struct FoundServerRow: View {
    let item: ServerDiscovery.Found
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            ServerRow(name: item.result.setup?.needsAdmin == true ? String(localized: "Neuer MinePower-Server") : "MinePower",
                      address: ServerProfile(name: "", url: item.result.url).displayAddress,
                      detail: detail)
        }
        .buttonStyle(.plain)
        .accessibilityHint(Text("Verbindet mit diesem Server"))
    }

    private var detail: String? {
        var parts: [String] = []
        if let version = item.result.health.version { parts.append("Version \(version)") }
        if item.result.setup?.needsAdmin == true { parts.append(String(localized: "noch nicht eingerichtet")) }
        if item.via == .bonjour { parts.append("Bonjour") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

/// Eingabefeld im App-Stil: mindestens 16 pt (kein Zoom), 50 pt hoch.
struct AppTextField: View {
    var prompt: Text
    @Binding var text: String
    var symbol: String?
    var secure = false
    /// Kennung für UI-Tests, direkt am Eingabefeld (nicht am Rahmen).
    var identifier: String?

    init(title: LocalizedStringKey, text: Binding<String>, symbol: String? = nil, secure: Bool = false,
         identifier: String? = nil) {
        self.init(prompt: Text(title), text: text, symbol: symbol, secure: secure, identifier: identifier)
    }

    /// Platzhalter wörtlich (z. B. vom Server), ohne Übersetzung und Markdown.
    init(verbatim: String, text: Binding<String>, symbol: String? = nil, secure: Bool = false,
         identifier: String? = nil) {
        self.init(prompt: Text(verbatim: verbatim), text: text, symbol: symbol, secure: secure, identifier: identifier)
    }

    init(prompt: Text, text: Binding<String>, symbol: String?, secure: Bool, identifier: String?) {
        self.prompt = prompt
        _text = text
        self.symbol = symbol
        self.secure = secure
        self.identifier = identifier
    }

    @FocusState private var focused: Bool

    /// `.input`: Fläche, kräftige Kante, Akzentrand mit Schein beim Bearbeiten.
    var body: some View {
        Group {
            if secure {
                SecureField(text: $text, prompt: prompt) { prompt }
            } else {
                TextField(text: $text, prompt: prompt) { prompt }
            }
        }
        .font(.custom(Typo.Weight.regular.rawValue, size: 16, relativeTo: .body))
        .foregroundStyle(Web.textStrong)
        .focused($focused)
        .accessibilityIdentifier(identifier ?? "")
        .padding(.horizontal, Space.m)
        .frame(minHeight: 48)
        .background(Web.surface, in: .rect(cornerRadius: Radius.medium, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.medium, style: .continuous)
            .strokeBorder(focused ? Palette.accent : Web.lineStrong, lineWidth: 1))
        .background(RoundedRectangle(cornerRadius: Radius.medium + 3, style: .continuous)
            .fill(focused ? Web.accentSoft : .clear).padding(-3))
        .animation(.snappy(duration: 0.12), value: focused)
    }
}

/// Fehler direkt unter dem Eingabefeld, mit Symbol (nicht nur Farbe).
struct InlineError: View {
    var error: APIError

    var body: some View {
        Label {
            Text(error.errorDescription ?? "")
        } icon: {
            Image(systemName: "exclamationmark.circle.fill")
        }
        .font(Typo.footnote)
        .foregroundStyle(Palette.error.ink)
        .accessibilityElement(children: .combine)
    }
}

/// Hilfe zur Server-Adresse (Docker, Unraid, Fernzugriff).
private struct AddressHelpSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section("Im Heimnetz") {
                    Text("Die Adresse ist die IP deines Servers mit Port 8080, z. B. 192.168.178.20:8080. Du findest sie in der Geräteliste deines Routers oder in der Unraid-Übersicht.")
                }
                Section("Docker") {
                    Text("Läuft MinePower per Docker Compose, gilt der Port aus `ports:` (Standard 8080). Die Weboberfläche im Browser hat dieselbe Adresse.")
                }
                Section("Von unterwegs") {
                    Text("Nutze VPN (z. B. WireGuard, Tailscale) oder einen Reverse-Proxy mit https. Ohne Verschlüsselung verbindet sich die App nur im Heimnetz.")
                }
                Section("Automatisch finden") {
                    Text("Die App durchsucht dein WLAN. Schneller geht es, wenn dein Server den Dienst _minepower._tcp per Bonjour ankündigt (Anleitung in der Dokumentation).")
                }
            }
            .navigationTitle("Adresse finden")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Fertig") { dismiss() } }
            }
        }
        .presentationDetents([.medium, .large])
    }
}
