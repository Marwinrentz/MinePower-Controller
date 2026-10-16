import MinePowerKit
import SwiftUI

/// Anmelden am gewählten Server – oder auf leerem Server den ersten
/// Administrator anlegen.
struct SignInView: View {
    @Environment(AppSession.self) private var session
    @State private var confirmRemoval = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xxl) {
                    ServerHeader()
                    CompatibilityNotice()
                    if session.setup?.needsAdmin == true {
                        CreateAdminForm()
                    } else {
                        SignInForm()
                    }
                }
                .padding(.horizontal, Space.xl)
                .padding(.vertical, Space.xxl)
                .frame(maxWidth: 520)
                .frame(maxWidth: .infinity)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Color(uiColor: .systemGroupedBackground))
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button {
                            session.addAnotherServer()
                        } label: {
                            Label("Anderer Server", web: .display)
                        }
                        if session.server != nil {
                            Button(role: .destructive) {
                                confirmRemoval = true
                            } label: {
                                Label("Server entfernen", web: .close)
                            }
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                            .frame(width: 44, height: 44)
                    }
                    .accessibilityLabel(Text("Server"))
                }
            }
        }
        .task { await session.refreshServerInfo() }
        .confirmationDialog("Server entfernen?", isPresented: $confirmRemoval, titleVisibility: .visible) {
            Button("Entfernen", role: .destructive) {
                if let id = session.server?.id { Task { await session.removeServer(id) } }
            }
        } message: {
            Text("Gespeicherte Anmeldung und bestätigtes Zertifikat werden gelöscht. Auf dem Server selbst ändert sich nichts.")
        }
    }
}

private struct ServerHeader: View {
    @Environment(AppSession.self) private var session

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            BrandMark(size: 56)
            Text(session.setup?.needsAdmin == true ? "Server einrichten" : "Anmelden")
                .font(Typo.largeTitle)
                .accessibilityAddTraits(.isHeader)
            if let server = session.server {
                Label {
                    Text(server.displayAddress)
                } icon: {
                    Image(systemName: server.url.scheme == "https" ? "lock.fill" : "house.fill")
                }
                .font(Typo.subheadline)
                .foregroundStyle(.secondary)
                .accessibilityLabel(Text("Server \(server.displayAddress)"))
            }
        }
    }
}

/// Hinweis, wenn App und Server-Version nicht zusammenpassen.
struct CompatibilityNotice: View {
    @Environment(AppSession.self) private var session

    var body: some View {
        switch session.compatibility {
        case .serverTooOld(let minimum, let server):
            Card {
                VStack(alignment: .leading, spacing: Space.s) {
                    Label("Server-Update nötig", web: .download)
                        .font(Typo.headline)
                        .foregroundStyle(Palette.warn.ink)
                    Text("Dein Server hat Version \(server.description), die App braucht mindestens \(minimum.description). Einige Bereiche funktionieren sonst nicht.")
                        .font(Typo.subheadline)
                    NavigationLink("So aktualisierst du den Server") { ServerUpdateGuide() }
                        .font(Typo.subheadlineMedium)
                }
            }
        case .appMayBeOutdated(let server):
            Card {
                Label("Server \(server.description) ist neuer als diese App. Bitte die App aktualisieren.",
                      systemImage: "info.circle")
                    .font(Typo.subheadline)
            }
        case .ok, .unknown:
            EmptyView()
        }
    }
}

private struct SignInForm: View {
    @Environment(AppSession.self) private var session
    @State private var email = ""
    @State private var password = ""
    @State private var isWorking = false
    @State private var error: APIError?
    @FocusState private var focus: Field?

    enum Field { case email, password }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            AppTextField(title: "E-Mail", text: $email, symbol: "envelope", identifier: "email")
                .textContentType(.username)
                .keyboardType(.emailAddress)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.next)
                .focused($focus, equals: .email)
                .onSubmit { focus = .password }
            AppTextField(title: "Passwort", text: $password, symbol: "key", secure: true, identifier: "password")
                .textContentType(.password)
                .submitLabel(.go)
                .focused($focus, equals: .password)
                .onSubmit { Task { await signIn() } }
            if let error { InlineError(error: error) }
            Button {
                Task { await signIn() }
            } label: {
                HStack {
                    if isWorking { ProgressView().tint(.white) }
                    Text("Anmelden")
                }
            }
            .buttonStyle(.primary)
            .disabled(email.isEmpty || password.isEmpty || isWorking)
            .accessibilityIdentifier("signIn")
            Text("Dieselben Zugangsdaten wie in der Weboberfläche. Dein Passwort bleibt auf dem Gerät nicht gespeichert, nur ein Anmelde-Schlüssel im Schlüsselbund.")
                .font(Typo.footnote)
                .foregroundStyle(.secondary)
        }
        .onAppear {
            if email.isEmpty { email = session.server?.email ?? "" }
            focus = email.isEmpty ? .email : .password
        }
    }

    private func signIn() async {
        guard !email.isEmpty, !password.isEmpty else { return }
        focus = nil
        error = nil
        isWorking = true
        defer { isWorking = false }
        do {
            try await session.signIn(email: email, password: password)
        } catch APIError.unauthorized {
            error = .validation(message: String(localized: "E-Mail oder Passwort stimmt nicht."), fields: [])
            password = ""
        } catch let apiError as APIError {
            error = apiError
        } catch {
            self.error = .unreachable(error.localizedDescription)
        }
    }
}

/// Leerer Server: erster Administrator.
private struct CreateAdminForm: View {
    @Environment(AppSession.self) private var session
    @State private var name = ""
    @State private var email = ""
    @State private var password = ""
    @State private var repeated = ""
    @State private var isWorking = false
    @State private var error: APIError?

    private var problem: LocalizedStringKey? {
        if !email.isEmpty, !email.contains("@") { return "Bitte eine gültige E-Mail-Adresse eingeben." }
        if !password.isEmpty, password.count < 8 { return "Das Passwort braucht mindestens 8 Zeichen." }
        if !repeated.isEmpty, repeated != password { return "Die Passwörter stimmen nicht überein." }
        return nil
    }

    private var isComplete: Bool {
        !name.isEmpty && email.contains("@") && password.count >= 8 && repeated == password
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            Text("Der Server ist neu. Lege das erste Konto an – es bekommt alle Rechte und kann später weitere Personen einladen.")
                .font(Typo.body)
                .foregroundStyle(.secondary)
            AppTextField(title: "Name", text: $name, symbol: "person")
                .textContentType(.name)
            AppTextField(title: "E-Mail", text: $email, symbol: "envelope")
                .textContentType(.username)
                .keyboardType(.emailAddress)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            AppTextField(title: "Passwort (mind. 8 Zeichen)", text: $password, symbol: "key", secure: true)
                .textContentType(.newPassword)
            AppTextField(title: "Passwort wiederholen", text: $repeated, symbol: "key", secure: true)
                .textContentType(.newPassword)
            if let problem {
                Label(problem, systemImage: "exclamationmark.circle")
                    .font(Typo.footnote)
                    .foregroundStyle(Palette.warn.ink)
            }
            if let error { InlineError(error: error) }
            Button {
                Task { await create() }
            } label: {
                HStack {
                    if isWorking { ProgressView().tint(.white) }
                    Text("Konto anlegen")
                }
            }
            .buttonStyle(.primary)
            .disabled(!isComplete || isWorking)
        }
    }

    private func create() async {
        error = nil
        isWorking = true
        defer { isWorking = false }
        do {
            try await session.createAdministrator(name: name, email: email, password: password)
        } catch let apiError as APIError {
            error = apiError
        } catch {
            self.error = .unreachable(error.localizedDescription)
        }
    }
}

/// Anleitung: MinePower-Server aktualisieren (Docker, Unraid).
struct ServerUpdateGuide: View {
    var body: some View {
        List {
            Section {
                Text("Deine Daten (Geräte, Einstellungen, Verlauf) bleiben erhalten. Beim Start übernimmt der Server sie automatisch in das neue Format.")
            }
            Section("Docker Compose") {
                CommandText(ServerUpdate.dockerCompose())
            }
            Section("Einzelner Container") {
                CommandText(ServerUpdate.dockerPull())
                Text("Danach den Container mit denselben Volumes neu erstellen.")
                    .font(Typo.footnote).foregroundStyle(.secondary)
            }
            Section("Unraid") {
                ForEach(Array(ServerUpdate.unraidSteps.enumerated()), id: \.offset) { index, step in
                    Label {
                        Text(step)
                    } icon: {
                        Text("\(index + 1)").font(Typo.caption).monospacedDigit()
                            .frame(width: 22, height: 22)
                            .background(Palette.accent.opacity(0.15), in: .circle)
                    }
                }
            }
            Section {
                Text("Wichtig: Das Datenverzeichnis /app/data muss als Volume eingebunden sein, sonst gehen gespeicherte Zugangsdaten (z. B. Tibber-Token) beim Update verloren.")
                    .font(Typo.footnote)
            }
        }
        .navigationTitle("Server aktualisieren")
    }
}

struct CommandText: View {
    let command: String
    init(_ command: String) { self.command = command }

    var body: some View {
        HStack(alignment: .top) {
            Text(verbatim: command)
                .font(.system(.footnote, design: .monospaced))
                .textSelection(.enabled)
            Spacer(minLength: 0)
            Button {
                UIPasteboard.general.string = command
            } label: {
                Image(systemName: "doc.on.doc")
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.borderless)
            .accessibilityLabel(Text("Befehl kopieren"))
        }
    }
}
