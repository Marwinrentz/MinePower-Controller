import MinePowerKit
import SwiftUI

struct TrustRequest: Identifiable, Hashable {
    var address: String
    var host: String
    var fingerprint: String
    var id: String { host + fingerprint }
}

/// Bestätigung eines selbstsignierten Zertifikats ("Vertrauen beim ersten Kontakt").
///
/// Der Fingerabdruck entspricht `openssl x509 -noout -fingerprint -sha256`
/// auf dem Server. Wer ihn dort vergleicht, schließt einen Angreifer im
/// Netz sicher aus; die App merkt ihn sich und fragt erst wieder, wenn sich
/// das Zertifikat ändert.
struct TrustCertificateSheet: View {
    let request: TrustRequest
    var onTrust: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    Label {
                        Text("Unbekanntes Zertifikat")
                            .font(Typo.title2)
                    } icon: {
                        Image(systemName: "lock.trianglebadge.exclamationmark")
                            .foregroundStyle(Palette.warn.fill)
                    }
                    .accessibilityAddTraits(.isHeader)

                    Text("\(request.host) verwendet ein selbst erstelltes Zertifikat. Das ist bei eigenen Servern üblich. Vertraue ihm nur, wenn der Fingerabdruck mit dem auf deinem Server übereinstimmt.")
                        .font(Typo.body)

                    Card {
                        VStack(alignment: .leading, spacing: Space.s) {
                            Text("SHA-256-Fingerabdruck").font(Typo.caption).foregroundStyle(.secondary)
                            Text(TrustRegistry.display(request.fingerprint))
                                .font(.system(.footnote, design: .monospaced))
                                .textSelection(.enabled)
                                .accessibilityLabel(Text("Fingerabdruck \(TrustRegistry.display(request.fingerprint))"))
                        }
                    }

                    Text("Auf dem Server prüfen mit:")
                        .font(Typo.subheadline).foregroundStyle(.secondary)
                    Text(verbatim: "openssl x509 -in cert.pem -noout -fingerprint -sha256")
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .padding(Space.m)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(uiColor: .tertiarySystemGroupedBackground), in: .rect(cornerRadius: 12))

                    VStack(spacing: Space.s) {
                        Button("Zertifikat vertrauen", action: onTrust)
                            .buttonStyle(.primary(Palette.warn.ink))
                            .accessibilityIdentifier("trust")
                        Button("Abbrechen") { dismiss() }
                            .buttonStyle(.secondary)
                    }
                    .padding(.top, Space.s)
                }
                .padding(Space.xl)
            }
            .background(Color(uiColor: .systemGroupedBackground))
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.large])
        .interactiveDismissDisabled(false)
    }
}
