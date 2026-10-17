# Datenschutz

## App Store Connect → App-Datenschutz

**Datenerfassung: Nein – „Daten werden nicht erfasst".**

Begründung: Die App sendet Daten ausschließlich an den Server, den der Nutzer
selbst betreibt (Adresse von ihm eingegeben oder im eigenen Heimnetz
gefunden). Weder der Entwickler noch Dritte erhalten Daten. Keine Analyse,
keine Absturzberichte an Dritte, keine Werbung, kein Tracking
(`NSPrivacyTracking` = false, keine Tracking-Domains).

Laut Apple gelten Daten nur dann als „erfasst", wenn sie das Gerät in Richtung
des Entwicklers oder seiner Partner verlassen; Übertragungen an einen vom
Nutzer selbst betriebenen Server fallen nicht darunter.

Datenschutz-Manifeste: `App/PrivacyInfo.xcprivacy`,
`Widgets/PrivacyInfo.xcprivacy` (UserDefaults: CA92.1, 1C8F.1).

## Datenschutzerklärung (zur Veröffentlichung)

App Store Connect verlangt eine erreichbare Adresse, z. B. diese Datei im
Repository oder eine Seite unter GitHub Pages.

### Deutsch

**MinePower für iOS – Datenschutzerklärung**

MinePower für iOS verbindet sich ausschließlich mit einem MinePower-Server,
den du selbst betreibst. Alle Werte (Messwerte, Einstellungen, Geräte,
Ladevorgänge) werden zwischen deinem Gerät und deinem Server ausgetauscht.
Der Entwickler betreibt keinen Dienst, über den Daten laufen, und erhält
keine Daten von dir.

Auf deinem Gerät gespeichert werden: die Adressen deiner Server und der
Fingerabdruck bestätigter Zertifikate, ein Anmelde-Schlüssel im Schlüsselbund
(nicht dein Passwort), Darstellungs-Einstellungen sowie für Widgets der
letzte Anlagenstand. Diese Daten verlassen dein Gerät nicht und werden mit
dem Löschen der App entfernt.

Die App enthält keine Werbung, keine Analyse- oder Tracking-Werkzeuge und
keine Komponenten von Drittanbietern. Berechtigungen: Lokales Netzwerk (um
deinen Server zu finden und zu erreichen), optional Face ID (App-Sperre).

Fragen: über die Issues des Projekts auf GitHub
(https://github.com/Marwinrentz/MinePower-Controller/issues).

### English

**MinePower for iOS – Privacy Policy**

MinePower for iOS connects only to a MinePower server that you run yourself.
All values (readings, settings, devices, charging sessions) are exchanged
between your device and your server. The developer operates no service that
your data passes through and receives no data from you.

Stored on your device: your server addresses and the fingerprints of
certificates you confirmed, a sign-in token in the keychain (not your
password), display preferences and, for widgets, the latest system status.
This data never leaves your device and is removed when you delete the app.

The app contains no advertising, no analytics or tracking tools and no
third-party components. Permissions: Local Network (to find and reach your
server), optionally Face ID (app lock).

Questions: via the project's issues on GitHub
(https://github.com/Marwinrentz/MinePower-Controller/issues).
