# Prompt für Claude Opus 5.5: MinePower-iOS-App bis App-Store-Reife

> Einfügen in eine frische Claude-Code-Sitzung mit Arbeitsverzeichnis
> `~/Dokumente/Mine-Power-Controller-main`, Modell `claude-opus-5-5`, Aufwand hoch.
> Vorher in einem Terminal: `. ~/.local/share/swiftly/env.sh` (damit `swift` im PATH ist).

---

## Rolle und Ziel

Du bist leitender iOS-Entwickler und Designer. Bring die **MinePower-iOS-App** (SwiftUI, Ordner `ios/`)
auf **App-Store-Niveau**: vollständig, poliert, mit Liquid Glass, für iPhone **und** iPad. Alles, was die
Web-Oberfläche kann, muss auch in der App gehen. Die App ist reiner Client einer selbst gehosteten
MinePower-Instanz (FastAPI-Backend, Docker/Unraid). Das Backend ändern wir nur, wo es für die App
nötig ist, und abwärtskompatibel.

## Ausgangslage (zuerst selbst prüfen, nichts davon blind glauben)

- Backend: `backend/app/api/*.py` (REST + WebSocket), Version in `VERSION` (aktuell 3.1.1), `CHANGELOG.md`.
- Web-Frontend (Referenz für Funktionsumfang und Design): `frontend/src/` mit Seiten Dashboard, Auto,
  Warmwasser, Batterie, Tarif, Statistik, Diagnose, Einstellungen (Geräte, Regelung, Batterie, KI/MCP,
  Sonstiges), Changelog, Wand-Ansicht, Login und **Wizard** (= der Einrichtungs-Assistent).
  Design-Tokens: `frontend/src/theme.css`, Texte: `frontend/src/i18n/de.ts`, Mobil-Erfahrungen:
  `docs/handy-bedienung-2.17.md`.
- Es gibt bereits einen Ansatz: `ios/MinePower/` (Core: APIClient, LiveSocket, Keychain, AppState;
  Models; Design/Theme + Glass; Views Dashboard, Gerätedetail, Statistik, Einstellungen, Login) plus
  `ios/project.yml` (XcodeGen), `ios/README.md`, Tests in `ios/MinePowerKitTests`. Lies das alles,
  bewerte die Qualität ehrlich und baue darauf auf oder ersetze es, wo es besser ist. Dokumentiere die Entscheidung.
- Markenfarben: Grün `#22C55E` → Cyan `#0EA5B7`, Logo `~/Bilder/logo.svg`, Hausschrift Inter.
  Kern-Claim: „Dein E-Auto lädt, wenn die Sonne scheint.“

## Entwicklungsumgebung (Linux, kein Mac)

- Installiert: `xtool` (`~/.local/bin/xtool`), Swift 6.3.3 (swiftly), Darwin-SDK mit iPhoneOS/iPhoneSimulator 27.1
  (`xtool sdk status`). Der Nutzer macht den Apple-Login (`xtool setup`) **selbst**. Frage nie nach
  Passwörtern oder Codes und gib keine ein.
- **Es gibt keinen iOS-Simulator unter Linux.** Plane daher realistisch:
  1. Lokale Kompilierprüfung mit `swift build` bzw. `xtool dev build`, wo möglich; plattformunabhängige
     Logik (Models, Formatierung, Dekodierung) in einem SwiftPM-Target mit Tests, die unter Linux laufen.
  2. Echter Simulator-Lauf und App-Store-Archiv über **GitHub Actions (macOS-Runner)**:
     `.github/workflows/ios.yml` pflegen bzw. neu schreiben.
  3. Prüfe zuerst, ob xtool mit Swift 6.3.3 und dem 27.1-SDK überhaupt baut (Test-Build). Falls nicht, sag es klar und
     weiche auf CI aus. Erfinde keine Fähigkeiten, die nicht belegt sind.

## Skills und Plugins: nutze sie aktiv, nicht nur nennen

Lade vor der jeweiligen Arbeit die passenden Skills über das Skill-Tool:
`liquid-glass:liquid-glass` und `all-ios-skills:swiftui-liquid-glass` (Glas-API, Migration, Fallstricke),
`swiftui-patterns`, `swiftui-navigation`, `swiftui-layout-components`, `swiftui-animation`,
`swiftui-gestures`, `swiftui-performance`, `swift-concurrency`, `swift-architecture`, `swift-testing`,
`swiftlint`, `swift-charts` (Statistik), `swift-codable`, `swift-formatstyle`, `ios-networking`
(WebSocket, lokales Netz, Bonjour), `swift-security` (Keychain, Zertifikats-Handling), `ios-accessibility`,
`ios-localization`, `widgetkit` und `activitykit` (Widget und Live Activity fürs Laden), `app-intents`,
`push-notifications`, `tipkit` (Onboarding-Hinweise), `app-store-review` und `app-store-optimization`
(Einreichung, Metadaten), `debugging-instruments`, `ios-simulator`, `apple-design-skill` (HIG-Review der
Screens). Nutze außerdem `frontend`-Wissen aus dem Web-Code als Spezifikation. Skills, die hier
nicht passen, lässt du weg; jeder genutzte Skill soll im Ergebnis erkennbar etwas verbessern.

## Design-Vorgaben

- **Apple-Look mit unserer Identität:** native Komponenten und HIG-konforme Muster, aber unsere Farben
  (Domain-Farben je Quelle aus `theme.css`), Inter für Zahlen/Headlines (eingebettet, Lizenz prüfen und
  in `NOTICE` aufnehmen), Marken-Verlauf als Akzent. Kein Web-Nachbau, sondern „so hätte Apple unsere App gebaut“.
- **Liquid Glass richtig:** `glassEffect`, `GlassEffectContainer`, Glass-Tab-Bar / Toolbar mit
  `backgroundExtensionEffect` und Scroll-Edge-Effekten; Glas nur für Navigation und schwebende
  Bedienelemente, nicht für Inhaltsflächen. Fallback für ältere iOS-Versionen in `Design/Glass.swift`
  kapseln (Mindestversion begründen, aktuell iOS 17).
- **Zwei Formate, zwei Strukturen:** iPhone = Tab-Bar mit höchstens 5 Zielen, Rest unter „Mehr“;
  iPad = `NavigationSplitView` mit Seitenleiste, Mehrspaltigkeit, Querformat, Stage Manager/Split View
  (frei skalierbare Fenster). Übersichtlicher als die Web-Version: weniger auf einmal, klare Hierarchie,
  progressive Offenlegung statt langer Formulare.
- **Keine Überlappungen:** Safe Areas, Dynamic Island, Home-Indikator, Tastatur, große Schriftgrößen
  (Dynamic Type bis AX5), Landscape, Split View. Prüfe mit Layout-Tests/Previews für kleinste (iPhone SE)
  und größte Geräte (iPad Pro 13") sowie jeder Schriftgröße; nutze `ViewThatFits`, `Grid`, adaptive Layouts.
- Hell/Dunkel, Reduce Motion/Transparency (Glas muss lesbar bleiben), VoiceOver-Labels, Kontrast ≥ 4,5:1.
- Haptik und Animationen dezent. Leere/Lade-/Fehlerzustände für jeden Screen gestalten.

## Funktionsumfang (Parität mit dem Web, alles muss real funktionieren)

1. **Server verbinden:** URL-Eingabe mit Prüfung (`/api/system/health`), Bonjour/Auto-Erkennung im
   Heimnetz, http im LAN erlaubt (gezielte ATS-Ausnahme, kein pauschales `AllowsArbitraryLoads` ohne Begründung),
   https/Reverse-Proxy, selbstsignierte Zertifikate mit Nutzerbestätigung (Pinning), mehrere Server/Anlagen,
   Fernzugriff-Hinweise. Zugangsdaten im Keychain, Biometrie-Sperre optional. Verständliche Fehlermeldungen.
2. **Einrichtungs-Assistent:** den kompletten Wizard (`Wizard.tsx`, `/api/system/setup-state`) nativ nachbauen:
   Erst-Setup, Geräte hinzufügen (Sungrow, myPV, Tesla, go-e, OCPP, Shelly …) über die Treiber-API,
   Tarif, Batterie, Regelung, Test der Verbindung je Schritt, Demo-Modus (`/api/system/demo`).
   Auch ein erstmals leerer Server muss sich **ganz** vom Handy einrichten lassen.
3. **Dashboard** mit Live-Werten per WebSocket (PV, Haus, Netz, Batterie, Auto, Warmwasser), Energiefluss,
   Wiederverbinden/Hintergrund-Verhalten sauber, Offline-Anzeige mit letztem Stand.
4. **Ladecontroller / Auto:** Modi (z. B. „Nur PV“), Ziel-Ladestand, Zeitpläne (`ScheduleEditor`), Priorität,
   Garage/Fahrzeuge. **Warmwasser**, **Batterie-Steuerung**, **Tarif**, **Statistik** (Swift Charts),
   **Diagnose/Ereignisse**, **Einstellungen** inkl. Geräteverwaltung, Backup/Export/Import, Konto/Rollen,
   **KI-Zugang (MCP-Schlüssel)**, Changelog.
5. **Wand-Modus** fürs iPad (Dauerbetrieb, Bildschirm an lassen, Burn-in-Schutz).
6. **System-Integration:** Widgets (Home-/Sperrbildschirm), Live Activity „Auto lädt“, App Intents/Siri/
   Kurzbefehle („Lade nur mit PV“), lokale Mitteilungen bzw. Push, wo das Backend es hergibt.
   Was das Backend dafür noch braucht, sauber und rückwärtskompatibel ergänzen und im CHANGELOG nennen.

## Updates „ohne Umstände“

- **App:** Auslieferung über TestFlight/App Store per CI (Workflow mit fastlane oder `xcodebuild` +
  `altool`/`notarytool`-Äquivalent für iOS, API-Key-Auth, automatische Build-Nummer aus Git, Release-Notes aus
  `CHANGELOG.md`). Dokumentiere die einmaligen Schritte für den Nutzer (Apple Developer Program,
  App-Store-Connect-API-Key als GitHub-Secret).
- **In-App:** Versionsabgleich App ↔ Server (`/api/system/health`, `VERSION`), Hinweis bei zu altem Server
  oder zu alter App (Kompatibilitätsmatrix), Banner „Update verfügbar“ mit Changelog (analog `UpdatePopup.tsx`)
  und Link in den App Store. Server-Update-Hinweis mit den genauen Docker/Unraid-Befehlen.
- Daten- und Einstellungsmigration zwischen App-Versionen ohne Datenverlust.

## Live-Vorschau auf dem PC

Ziel: Der Nutzer sieht den Fortschritt als iPhone- und iPad-Bild auf dem PC. Unter Linux gibt es keinen
Simulator, deshalb:
- **CI-Vorschau:** Der macOS-Runner startet den Simulator (iPhone 17 Pro, iPad Pro 13"), führt UI-Tests
  mit `XCTAttachment`-Screenshots über alle Screens aus (hell/dunkel, große Schrift, Hoch-/Querformat) und lädt sie
  als Artefakt hoch. Ein lokales Skript (`ios/tools/preview.sh`) holt per `gh` das neueste Artefakt und
  ein kleiner lokaler Web-Server (`ios/tools/preview-server`, Port frei wählbar) zeigt sie in
  Gerätrahmen nebeneinander und lädt automatisch neu, sobald ein neuer Lauf fertig ist.
- **Sofort-Vorschau:** Wo es keinen CI-Lauf braucht, zusätzlich eine statische HTML-Vorschau der Layouts
  (Geräterahmen, mit denselben Design-Tokens) für schnelles Feedback zu Struktur und Abständen. Kennzeichne
  klar, was echtes Simulator-Bild ist und was nur Annäherung.
- Zeige dem Nutzer die Vorschau-Seite im eingebauten Browser, sobald sie läuft.

## Arbeitsweise

1. **Plan zuerst** (kurz): Bestandsaufnahme, Architektur (Schichten, Navigation, State, Fehlerbehandlung),
   Meilensteine. Dann in Meilensteinen arbeiten: Fundament+CI → Verbinden/Login → Wizard → Dashboard →
   Steuerseiten → Statistik/Diagnose/Einstellungen → iPad-Layouts → Widgets/Live Activity/Intents →
   Politur/Accessibility → Store-Unterlagen.
2. Nach jedem Meilenstein: bauen, Tests laufen lassen, Vorschau aktualisieren, `/code-review` auf den Diff,
   kurze Zusammenfassung. Nichts als „fertig“ melden, was nicht gebaut/getestet wurde; Lücken offen benennen.
3. Strikte Nebenläufigkeit (Swift 6) beibehalten, keine `@unchecked Sendable`-Abkürzungen ohne Begründung.
   Keine Secrets im Repo. Keine neuen Abhängigkeiten ohne Grund (Lizenz prüfen).
4. Tests: Dekodierung gegen echte Fixtures aus dem Backend, API-Client mit Mock-URLProtocol, ViewModels,
   UI-Screenshot-Tests. Mock-Server oder Demo-Modus (`/api/system/demo`) für reproduzierbare Läufe nutzen.
5. Commits klein und sauber. **Nicht pushen, keine Releases, keine Uploads** ohne ausdrückliche Freigabe des Nutzers.

## App-Store-Reife (Abnahmeliste)

- Bundle-ID, Version/Build, Privacy-Manifest (`PrivacyInfo.xcprivacy`), Datenschutz-Angaben („Data Not Collected“,
  falls zutreffend), Exportcompliance, `NSLocalNetworkUsageDescription` + Bonjour-Dienste, Face-ID-Text.
- App-Icon im neuen Layered-Format (Icon Composer läuft nur auf dem Mac, daher: Quell-Ebenen als SVG/PNG liefern
  und beschreiben, wie der Nutzer die `.icon`-Datei erzeugt; bis dahin klassisches Asset-Katalog-Icon, 1024 px).
- Screenshots für Pflichtgrößen (iPhone 6,9", iPad 13") aus den UI-Tests, Texte (Name, Untertitel, Beschreibung,
  Keywords, Support-/Datenschutz-URL, Review-Hinweise **mit Demo-Zugang** über den Demo-Modus) auf Deutsch und Englisch.
- Richtlinien-Check mit dem Skill `app-store-review` (u. a. 4.2 Mindestfunktionalität bei Self-Hosting-Clients,
  5.1 Datenschutz, Demo-Zugang für Prüfer, keine toten Links).
- Stabilität: kein Absturz bei Netzverlust/Serverwechsel, Speicher/Energie mit Instruments geprüft.

## Was nur der Nutzer kann (frag am Ende gebündelt, rate nichts)

Apple-Developer-Mitgliedschaft und `xtool setup`-Login, App-Store-Connect-API-Key, GitHub-Secrets, App-Name/
Bundle-ID-Freigabe, finale Einreichung, Entscheidungen zu Mindest-iOS-Version und zum Preismodell.

## Abgabe

Ein Abschlussbericht: was gebaut ist, was getestet wurde (mit Ausgabe), bekannte Lücken, Schritte für den Nutzer,
Pfad zur Vorschau-Seite, Befehle für Build/Test/Release. Keine Behauptung ohne Beleg.
