import MinePowerKit
import SwiftUI
import UniformTypeIdentifiers

// MARK: - Benutzer

/// Einstellungen → Benutzer (nur Admin), wie `UsersSettings` im Web.
struct UsersSettings: View {
    @Environment(AppSession.self) private var session
    @State private var users: [User]?
    @State private var email = ""
    @State private var name = ""
    @State private var password = ""
    @State private var role: User.Role = .user
    @State private var isAdding = false
    @State private var deleting: User?

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: Space.l) {
                CardHead("Benutzer", subtitle: String(localized: "Admin: alles. Benutzer: bedienen. Nur lesen: anzeigen."))
                if let users {
                    VStack(spacing: 0) {
                        ForEach(users) { user in
                            row(user)
                            if user.id != users.last?.id { Divider().overlay(Web.line) }
                        }
                    }
                } else {
                    SkeletonCards(count: 1, height: 120)
                }
                GroupTitle("Hinzufügen")
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), spacing: Space.l, alignment: .top)], spacing: Space.l) {
                    Field(label: "E-Mail") {
                        AppTextField(verbatim: "", text: $email)
                            .keyboardType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled()
                            .textContentType(nil)
                    }
                    Field(label: "Name") {
                        AppTextField(verbatim: "", text: $name).textInputAutocapitalization(.words)
                    }
                    Field(label: "Passwort", help: "min. 8") {
                        AppTextField(verbatim: "", text: $password, secure: true).textContentType(.newPassword)
                    }
                }
                Field(label: "Rolle") {
                    SegmentGrid(options: User.Role.allCases.reversed().map { .init(value: $0, title: $0.title) },
                                selection: role, minimum: 100) { role = $0 }
                }
                HStack {
                    Spacer()
                    Button {
                        Task { await add() }
                    } label: {
                        HStack(spacing: 6) {
                            if isAdding { ProgressView().tint(Web.accentText) }
                            Icon(.plus, size: 16)
                            Text("Hinzufügen")
                        }
                    }
                    .buttonStyle(.web(.primary, fullWidth: false))
                    .disabled(email.trimmingCharacters(in: .whitespaces).isEmpty || password.count < 8 || isAdding)
                }
            }
        }
        .task { await load() }
        .confirmationDialog(Text("Löschen: \(deleting?.email ?? "")?"), isPresented: Binding(
            get: { deleting != nil }, set: { if !$0 { deleting = nil } }), titleVisibility: .visible, presenting: deleting) { user in
            Button("Löschen", role: .destructive) { Task { await delete(user) } }
            Button("Abbrechen", role: .cancel) {}
        }
    }

    private func row(_ user: User) -> some View {
        HStack(spacing: Space.m) {
            Icon(.lock, size: 18).foregroundStyle(Web.textDim)
            VStack(alignment: .leading, spacing: 1) {
                Text(user.name.isEmpty ? user.email : user.name).font(Typo.bodyMedium).foregroundStyle(Web.textStrong)
                Text(user.email).font(Typo.footnote).foregroundStyle(Web.textDim)
            }
            Spacer(minLength: Space.s)
            Badge(text: user.role.title, tone: user.role == .admin ? Palette.accentDomain : nil)
            if user.id != session.user?.id {
                Button("Löschen") { deleting = user }
                    .buttonStyle(.web(.danger, size: .small, fullWidth: false))
            }
        }
        .padding(.vertical, Space.s)
    }

    private func load() async {
        do { users = try await session.api.users() } catch { users = users ?? []; session.handle(error) }
    }

    private func add() async {
        isAdding = true
        defer { isAdding = false }
        do {
            _ = try await session.api.createUser(UserCreate(email: email.trimmingCharacters(in: .whitespaces),
                                                            name: name.trimmingCharacters(in: .whitespaces),
                                                            password: password, role: role))
            email = ""
            name = ""
            password = ""
            role = .user
            session.show(Notice(.success, String(localized: "Gespeichert")))
            await load()
        } catch {
            session.handle(error)
        }
    }

    private func delete(_ user: User) async {
        do {
            try await session.api.deleteUser(user.id)
            await load()
        } catch {
            session.handle(error)
        }
    }
}

extension User.Role {
    /// Bezeichnung wie `ROLE_LABEL` im Web.
    var title: String {
        switch self {
        case .admin: String(localized: "Admin")
        case .user: String(localized: "Benutzer")
        case .readonly: String(localized: "Nur lesen")
        }
    }
}

// MARK: - Sicherung

/// Einstellungen → Sicherung (nur Admin): Export teilen, Import aus Dateien,
/// Stand der automatischen Sicherung.
struct BackupSettings: View {
    @Environment(AppSession.self) private var session
    @Environment(SettingsEditor.self) private var editor
    @State private var status: BackupStatus?
    @State private var share: ShareFile?
    @State private var picking = false
    @State private var pending: Data?
    @State private var working = false

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: Space.l) {
                CardHead("Sicherung", subtitle: String(localized: "Geräte und Einstellungen, ohne Zugangsdaten"))
                FlowLayout(spacing: Space.s) {
                    Button {
                        Task { await export() }
                    } label: {
                        HStack(spacing: 6) { Icon(.download, size: 16); Text("Exportieren") }
                    }
                    .buttonStyle(.web(.normal, fullWidth: false))
                    Button {
                        picking = true
                    } label: {
                        HStack(spacing: 6) { Icon(.export, size: 16); Text("Importieren") }
                    }
                    .buttonStyle(.web(.normal, fullWidth: false))
                }
                .disabled(working)
                GroupTitle("Automatische Sicherung")
                Text("Lokal, automatisch").font(Typo.subheadline).foregroundStyle(Web.textDim)
                if let status {
                    Text(statusLine(status)).font(Typo.subheadlineMedium).foregroundStyle(Web.text)
                }
            }
        }
        .task { status = try? await session.api.backupStatus() }
        .sheet(item: $share) { file in ShareSheet(url: file.url) }
        .fileImporter(isPresented: $picking, allowedContentTypes: [.json]) { result in
            switch result {
            case .success(let url):
                let access = url.startAccessingSecurityScopedResource()
                defer { if access { url.stopAccessingSecurityScopedResource() } }
                do { pending = try Data(contentsOf: url) } catch {
                    session.show(Notice(.error, String(localized: "Datei nicht lesbar"), message: error.localizedDescription))
                }
            case .failure(let error):
                session.show(Notice(.error, String(localized: "Datei nicht lesbar"), message: error.localizedDescription))
            }
        }
        .confirmationDialog(Text("Import ersetzt alle Geräte und Einstellungen. Fortfahren?"), isPresented: Binding(
            get: { pending != nil }, set: { if !$0 { pending = nil } }), titleVisibility: .visible, presenting: pending) { data in
            Button("Importieren", role: .destructive) { Task { await importConfiguration(data) } }
            Button("Abbrechen", role: .cancel) {}
        }
    }

    private func statusLine(_ status: BackupStatus) -> String {
        let latest = status.latest.flatMap(BackendDate.parse).map { String(localized: "Letzte Sicherung: \(Fmt().dateTime($0))") } ?? "–"
        return latest + " · " + String(localized: "\(status.count) Sicherungen vorhanden")
    }

    private func export() async {
        working = true
        defer { working = false }
        do {
            let data = try await session.api.exportConfiguration()
            share = try ShareFile.write(data, name: "minepower-config.json")
        } catch {
            session.handle(error)
        }
    }

    private func importConfiguration(_ data: Data) async {
        working = true
        defer { working = false }
        do {
            try await session.api.importConfiguration(data)
            session.show(Notice(.success, String(localized: "Gespeichert")))
            await editor.load(force: true)
            try? await session.live.refresh()
        } catch {
            session.handle(error)
        }
    }
}

// MARK: - Konto & Server

/// Nur in der App: angemeldetes Konto, gespeicherte Server, Sperre, Abmelden.
struct AccountSettings: View {
    @Environment(AppSession.self) private var session
    @State private var confirmSignOut = false
    @State private var removing: ServerProfile?
    @State private var lockError: String?

    var body: some View {
        if let user = session.user {
            Card {
                VStack(alignment: .leading, spacing: Space.l) {
                    CardHead("Konto")
                    HStack(spacing: Space.m) {
                        DomainIcon(icon: .lock, color: nil, size: 44)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(user.name.isEmpty ? user.email : user.name).font(Typo.headline).foregroundStyle(Web.textStrong)
                            Text(user.email).font(Typo.footnote).foregroundStyle(Web.textDim)
                        }
                        Spacer(minLength: Space.s)
                        Badge(text: user.role.title, tone: user.role == .admin ? Palette.accentDomain : nil)
                    }
                    .accessibilityElement(children: .combine)
                    if Biometrics.kind != .none, !session.isDemo {
                        ToggleRow(label: "App mit \(Biometrics.label) sperren",
                                  help: lockError ?? String(localized: "Beim Öffnen und nach einigen Minuten im Hintergrund."),
                                  isOn: Binding(get: { session.biometricLock }, set: { value in Task { await setLock(value) } }))
                    }
                    Button {
                        confirmSignOut = true
                    } label: {
                        HStack(spacing: 6) { Icon(.power, size: 16); Text(session.isDemo ? "Demo beenden" : "Abmelden") }
                    }
                    .buttonStyle(.web(.danger, fullWidth: false))
                }
            }
            .confirmationDialog(Text(session.isDemo ? "Demo beenden?" : "Abmelden?"), isPresented: $confirmSignOut,
                                titleVisibility: .visible) {
                Button(session.isDemo ? "Demo beenden" : "Abmelden", role: .destructive) {
                    Task {
                        if session.isDemo, let id = session.server?.id {
                            await session.removeServer(id)
                        } else {
                            await session.signOut()
                        }
                    }
                }
                Button("Abbrechen", role: .cancel) {}
            } message: {
                if !session.isDemo { Text("Die Anmeldung auf diesem Gerät wird gelöscht.") }
            }
        }
        Card {
            VStack(alignment: .leading, spacing: Space.l) {
                CardHead("Server", subtitle: String(localized: "Gespeicherte MinePower-Server auf diesem Gerät"))
                VStack(spacing: 0) {
                    ForEach(session.directory.profiles) { profile in
                        serverRow(profile)
                        if profile.id != session.directory.profiles.last?.id { Divider().overlay(Web.line) }
                    }
                }
                Button {
                    session.addAnotherServer()
                } label: {
                    HStack(spacing: 6) { Icon(.plus, size: 16); Text("Server hinzufügen") }
                }
                .buttonStyle(.web(.normal, fullWidth: false))
            }
        }
        .confirmationDialog(Text("\(removing?.name ?? "") entfernen?"), isPresented: Binding(
            get: { removing != nil }, set: { if !$0 { removing = nil } }), titleVisibility: .visible, presenting: removing) { profile in
            Button("Entfernen", role: .destructive) { Task { await session.removeServer(profile.id) } }
            Button("Abbrechen", role: .cancel) {}
        } message: { _ in
            Text("Adresse, Zertifikat und Anmeldung werden von diesem Gerät gelöscht. Der Server selbst bleibt unverändert.")
        }
    }

    private func serverRow(_ profile: ServerProfile) -> some View {
        let active = profile.id == session.server?.id
        return HStack(spacing: Space.m) {
            BrandMark(size: 32)
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: Space.s) {
                    Text(profile.name).font(Typo.bodyMedium).foregroundStyle(Web.textStrong)
                    if active { Badge(text: String(localized: "Aktiv"), tone: Palette.accentDomain) }
                }
                Text(profile.isDemo ? String(localized: "Eingebaute Beispielanlage") : profile.displayAddress)
                    .font(Typo.footnote).foregroundStyle(Web.textDim)
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: Space.s)
            if !active {
                Button("Wechseln") { Task { await session.switchServer(to: profile.id) } }
                    .buttonStyle(.web(.normal, size: .small, fullWidth: false))
            }
            Menu {
                Button(role: .destructive) { removing = profile } label: { Label("Entfernen", systemImage: "trash") }
            } label: {
                Icon(.more, size: 18).foregroundStyle(Web.textDim).frame(width: 36, height: 44).contentShape(.rect)
            }
            .accessibilityLabel(Text("Weitere Aktionen für \(profile.name)"))
        }
        .padding(.vertical, Space.s)
    }

    private func setLock(_ enabled: Bool) async {
        do {
            try await session.setBiometricLock(enabled)
            lockError = nil
        } catch {
            lockError = String(localized: "Sperre nicht geändert: \(error.localizedDescription)")
        }
    }
}
