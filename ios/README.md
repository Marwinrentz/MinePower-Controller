# MinePower für iPhone und iPad

Native SwiftUI-App für einen selbst betriebenen MinePower-Server. Sie sieht aus
wie die Weboberfläche (gleiche Farben, Inter, dieselben Symbole und Grafiken)
und nutzt auf iOS 26 Liquid Glass für Navigation und schwebende Leisten.
Funktionsumfang wie im Web: Übersicht, Auto, Warmwasser, Batterie, Tarif,
Statistik, Diagnose, alle Einstellungen, Changelog und Wand-Ansicht – dazu
Widgets, Live-Aktivität, Kurzbefehle und iPad-Fenster.

Die App spricht nur mit dem eigenen Server des Nutzers. Ohne Server zeigt die
eingebaute Demo-Anlage alles mit simulierten Werten.

## Aufbau

```
ios/
  MinePowerKit/            Swift-Paket ohne SwiftUI, unter Linux getestet
    Models/                Antworten des Backends, Formate
    Networking/            APIClient (Aktor), WebSocket, Zertifikats-Pinning (TOFU)
    State/                 AppSession, LiveStore, SettingsEditor, Geräte-Editor, Assistent
    Logic/                 Erklärungen, Wand-Zustand, SVG-Pfade, MCP-Anleitungen, Eingaben
    Shared/                Stand für Widgets und Kurzbefehle (App Group)
    Demo/                  eingebaute Demo-Anlage (URLProtocol, alle Endpunkte)
  Sources/MinePower/       App (SwiftUI)
    App/                   Start, Navigation (Tabs/Seitenleiste), Tastaturbefehle
    Design/                Theme, Inter, Web-Symbole, Bausteine, Grafiken (Garage, Boiler …)
    Features/              Seiten, je ein Ordner
    Core/                  Schlüsselbund, Server-Suche, Widget-Brücke
    Shared/                Live-Aktivität (gemeinsam mit den Widgets)
    Resources/             Schriften, String-Kataloge (de/en)
  Widgets/                 Widget-Erweiterung: Status-Widget, Live-Aktivität
  App/                     Info.plist, Entitlements, Datenschutz, Icons
  AppStore/                Texte, Prüfhinweise, Datenschutz für App Store Connect
  UITests/                 Screenshots aller Bereiche mit der Demo-Anlage
  project.yml              XcodeGen (Xcode-Projekt für CI und Mac)
```

Muster: `@MainActor @Observable`-Stores im Kit, Ansichten lesen sie aus der
Umgebung. Swift 6 mit strikter Nebenläufigkeitsprüfung, keine Abhängigkeiten
außer Apples Frameworks.

## Bauen

### Ohne Mac (Linux)

```bash
cd ios/MinePowerKit && swift test        # Kit-Tests
cd ios && xtool dev build                 # App für iOS übersetzen (SwiftPM + Darwin-SDK)
ios/tools/check-widgets.sh                # Widget-Erweiterung gegen das iOS-SDK prüfen
python3 ios/tools/l10n.py check           # jeder Text mit englischer Übersetzung
python3 ios/tools/no-typographic-quotes.py --check
```

`xtool dev build` baut die App ohne Widget-Erweiterung, ohne App Group und
ohne String-Kataloge (deutsch). Für die Installation auf dem eigenen Gerät
meldet man sich einmal mit `xtool setup` bei Apple an.

### Mit Xcode (Mac oder CI)

```bash
brew install xcodegen && cd ios && xcodegen generate && open MinePower.xcodeproj
```

Ziele: `MinePower` (App), `MinePowerWidgets` (Erweiterung), `MinePowerUITests`.

### CI (`.github/workflows/ios.yml`)

1. **Kit (Linux):** `swift test`.
2. **Prüfungen:** Anführungszeichen, Plists, Übersetzungen.
3. **App (macOS):** XcodeGen, Build, UI-Tests auf iPhone Pro Max und iPad Pro
   13 Zoll, Screenshots als Artefakt `screenshots`, danach Abgleich der
   Compiler-Schlüssel mit den String-Katalogen (`l10n.py verify`).
4. **TestFlight** (nur von Hand: Actions → ios → Run workflow → testflight,
   mit Freigabe in der Umgebung `testflight`).

Secrets für TestFlight: `ASC_KEY_P8_BASE64`, `ASC_KEY_ID`, `ASC_ISSUER_ID`
(App-Store-Connect-API-Schlüssel mit Rolle „App Manager"), `APPLE_TEAM_ID`.
Im Entwicklerkonto müssen die Bundle-IDs `de.minepower.app` und
`de.minepower.app.widgets` sowie die App Group `group.de.minepower.app`
existieren (automatische Signierung mit `-allowProvisioningUpdates` legt sie
in der Regel selbst an).

## Funktionen

- **Verbinden:** Suche im Heimnetz (Bonjour `_minepower._tcp` und Subnetz),
  Adresse von Hand, mehrere Server, selbst signierte Zertifikate mit
  Fingerabdruck-Bestätigung (Trust on First Use). Ohne HTTPS nur im Heimnetz.
- **Anmelden:** wie im Web; Token im Schlüsselbund, optional mit Face ID.
- **Seiten:** alle Bereiche der Weboberfläche mit denselben Texten und
  Grafiken; Einrichtungsassistent und Geräteformular mit Verbindungstest.
- **iPad:** Seitenleiste, breite Layouts, Tastaturbefehle (⌘1–⌘9, ⌘R,
  ⌃⌘F), Wand-Ansicht als eigenes Fenster.
- **Widgets:** Home- und Sperrbildschirm. Das Widget liest den Stand, den die
  App ablegt, und holt mit ungeschütztem Token selbst einen frischen; mit
  Face-ID-Sperre bleiben Werte auf dem gesperrten Gerät verdeckt.
- **Live-Aktivität:** Boost und Sofortladen auf Sperrbildschirm und Dynamic
  Island. Der Server schickt keine Push-Mitteilungen; die Restzeit läuft als
  Timer selbst weiter, Werte aktualisiert die App, solange sie offen ist.
- **Kurzbefehle/Siri:** Status, Boost, Sofort laden, Automatik, nicht laden.
- **Sprachen:** Deutsch (Leitsprache) und Englisch (`Localizable.xcstrings`,
  `InfoPlist.xcstrings`, `AppShortcuts.xcstrings`).

## Demo-Anlage

`DemoBackend` beantwortet alle Endpunkte im Prozess (URLProtocol, Host
`demo.minepower.invalid`) mit Szenarien `sunny`, `night`, `attention`,
`fresh`. Sie dient der Demo im Verbindungsbildschirm, der App-Prüfung durch
Apple, den UI-Tests und den Screenshots.

## Sicherheit und Datenschutz

- Keine Datensammlung, keine Drittanbieter, kein Tracking
  (`App/PrivacyInfo.xcprivacy`, `Widgets/PrivacyInfo.xcprivacy`).
- Token nur im Schlüsselbund (`AfterFirstUnlockThisDeviceOnly`, mit Sperre
  `.userPresence`), nie in UserDefaults; Widget und App teilen nur den
  Anzeigestand und die Server-Adresse.
- ATS: HTTP nur für lokale Netze (`NSAllowsLocalNetworking`, `fritz.box`,
  `.lan`, `.home`, `home.arpa`); entfernte Server nur mit HTTPS.

## Was geprüft ist – und was nicht

Geprüft (ohne Mac): Kit-Tests unter Linux, Übersetzung der App mit xtool und
der Widget-Erweiterung gegen das iOS-SDK (Swift 6, strikte Nebenläufigkeit),
Code-Reviews je Meilenstein.

Erst in der CI bzw. auf einem Gerät sichtbar: das tatsächliche Aussehen
(Screenshots), Widgets und Live-Aktivität im System, Siri-Sätze, Signierung
mit App Group und Schlüsselbundfreigabe.
