import Foundation
import Observation

/// Globale Einstellungen bearbeiten (`/api/settings`), abschnittsweise.
///
/// Wie `useSettings`/`useSection` im Web: Mehrere Seiten (Tarif, Regelung,
/// Batterie) bearbeiten Abschnitte, eine Speicherleiste zeigt, ob etwas
/// ungespeichert ist. Gespeichert wird je geändertem Abschnitt der vollständige
/// Abschnitt; maskierte Geheimnisse ("•••") lässt der Server unverändert.
@MainActor
@Observable
public final class SettingsEditor {
    public private(set) var drafts: [String: SettingsDraft] = [:]
    public private(set) var isLoaded = false
    public private(set) var isSaving = false
    public private(set) var error: APIError?

    @ObservationIgnored private let api: APIClient

    public init(api: APIClient) { self.api = api }

    public var isDirty: Bool { drafts.values.contains(where: \.isDirty) }

    public func load(force: Bool = false) async {
        guard force || !isLoaded else { return }
        do {
            let envelope = try await api.settings()
            apply(envelope)
            isLoaded = true
            error = nil
        } catch let apiError as APIError {
            error = apiError
        } catch {}
    }

    public func section(_ name: String) -> SettingsDraft { drafts[name] ?? SettingsDraft() }

    public func update(_ name: String, _ change: (inout SettingsDraft) -> Void) {
        var draft = section(name)
        change(&draft)
        drafts[name] = draft
    }

    public func discard() {
        for key in drafts.keys { drafts[key]?.reset() }
    }

    /// Speichert alle geänderten Abschnitte. `false` bei Fehler (Meldung in `error`).
    @discardableResult
    public func save() async -> Bool {
        let changed = drafts.filter(\.value.isDirty)
        guard !changed.isEmpty else { return true }
        isSaving = true
        defer { isSaving = false }
        let sent = changed.mapValues(\.draft)
        do {
            try await api.saveSettings(sent.mapValues { .object($0) })
            let envelope = try await api.settings()
            for (name, sentDraft) in sent {
                // Was während des Speicherns geändert wurde, bleibt als Änderung stehen.
                let current = drafts[name]?.draft ?? sentDraft
                var fresh = SettingsDraft(envelope.section(name))
                for (key, value) in current where sentDraft[key] != value { fresh.set(key, value) }
                drafts[name] = fresh
            }
            error = nil
            return true
        } catch let apiError as APIError {
            error = apiError
            return false
        } catch {
            return false
        }
    }

    private func apply(_ envelope: SettingsEnvelope) {
        for (name, value) in envelope.values {
            let object = value.object ?? [:]
            if var existing = drafts[name] {
                existing.rebase(object)
                drafts[name] = existing
            } else {
                drafts[name] = SettingsDraft(object)
            }
        }
    }
}
