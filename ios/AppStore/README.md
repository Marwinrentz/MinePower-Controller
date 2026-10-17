# App-Store-Unterlagen

| Datei | Inhalt |
|---|---|
| `metadata.json` | Name, Untertitel, Werbetext, Beschreibung, Schlüsselwörter, Neuerungen, URLs (de-DE, en-US); Längen geprüft |
| `review-notes.md` | Hinweise für die App-Prüfung (Demo ohne Server, Berechtigungen) |
| `privacy.md` | Angaben „App-Datenschutz" und Datenschutzerklärung zum Veröffentlichen |

## Einstellungen in App Store Connect

- **Kategorie:** Dienstprogramme (primär), Lifestyle (sekundär)
- **Altersfreigabe:** 4+ (alle Fragen „Nein")
- **Preis:** offen (siehe Fragen an den Kontoinhaber)
- **Verfügbarkeit:** alle Länder; Sprachen Deutsch und Englisch
- **Exportvorschriften:** nur System-HTTPS, `ITSAppUsesNonExemptEncryption` = NO
- **Inhalte Dritter:** nein (Inter: SIL Open Font License, im Info-Bereich genannt)
- **Anmeldung für die Prüfung:** nicht nötig, Demo im ersten Bildschirm

## Screenshots

Entstehen in der CI (`ios/UITests/ScreenshotTests.swift`) mit der Demo-Anlage
auf dem neuesten iPhone Pro Max (6,9 Zoll, Pflichtgröße) und dem iPad Pro
13 Zoll, je hell und dunkel. Artefakt `screenshots` am Lauf, lokal mit
`ios/tools/preview.sh` ansehen. Vorschlag für die Reihenfolge: Übersicht
(hell), Auto, Warmwasser, Statistik, Übersicht (dunkel), Einstellungen; iPad:
Übersicht quer, Wand-Ansicht.
