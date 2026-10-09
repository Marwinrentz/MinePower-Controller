# Handy: leichter bedienbar (2.17)

Das Aussehen ist bewusst unverändert, an Desktop, Wand-Ansicht und Handy.
Geändert hat sich nur, wie sich die App am Touchscreen bedienen lässt.

Ein Wand- und Handy-Redesign wurde entworfen und wieder verworfen. Gewünscht
war das bisherige Design, nur leichter bedienbar.

## Befunde und Ursachen

### Kopfzeile unter der Dynamic Island kaputt

Ursachen:

* **Zu wenig Höhe:** `.topbar` hatte `min-height: 56px` *inklusive*
  `padding-top: safe-area-inset-top`. Bei 59 px Inset blieb für Logo und
  Status keine Höhe.
* **Sprunglink sichtbar:** Der Link „Zum Inhalt springen“ war mit
  `translateY(-200%)` versteckt, ohne die Safe-Area. Er lugte unter der Insel
  hervor.
* **Falsche Farben:** `theme-color` war im hellen Design Grün statt der
  Hintergrundfarbe. Die Statusleiste stand fest auf `black-translucent`, also
  weiße Statusschrift auch auf hellem Grund.

Lösung:

* Die Kopfzeile ist `56 px + Inset` hoch.
* Der Sprunglink wird um seine Höhe plus Inset versteckt.
* Statusleiste und `theme-color` folgen dem gewählten Design. Ein
  Inline-Skript in `index.html` setzt sie vor dem ersten Bild, beim
  Umschalten übernimmt `store/theme.ts`.
  * Hell: `default`, dunkle Schrift.
  * Dunkel: `black-translucent`, die Kopfzeile rückt unter die Insel.
* Im Querformat bleibt unten Platz für den Home-Indikator.

### Regler lösen beim Scrollen aus

Ursache: Die App nutzte ein natives `<input type="range">` mit
`touch-action: pan-y`.

* Chrome setzt den Wert schon beim Berühren der Spur auf die Tippstelle,
  auch wenn der Finger danach senkrecht scrollt.
* Der alte Regler schickte den Wert dann bei `touchend`/`blur` ab.

Der Test `test_root_cause_native_range_changes_while_scrolling` zeigt das:
Ein senkrechter Wisch scrollt 200 px *und* setzt den Wert von 50 auf 87.

Lösung: Am Touchscreen gibt es keinen freien Regler mehr (`TouchValue.tsx`).
Mit der Maus bleibt alles wie bisher.

* **− / +** neben der Wertleiste: Sie reagieren nur auf einen Tipp, nie auf
  eine Wischbewegung. Gedrückt halten wiederholt den Schritt, bis sich der
  Finger bewegt. Mehrere Tipps werden gesammelt und nach 0,9 s einmal
  gesendet.
* **Tipp auf die Wertleiste** öffnet ein Blatt von unten mit
  Voreinstellungen, −/+ und einem Regler. Der Regler greift erst nach
  eindeutig waagerechter Bewegung (10 px, waagerecht mindestens 1,3-mal so
  viel wie senkrecht). Ein Tipp allein ändert nichts, senkrecht gewinnt immer
  das Scrollen. Gesendet wird beim Loslassen.
* **Rückgängig**: Jede gespeicherte Änderung meldet sich mit einem
  „Rückgängig“-Knopf.

Betroffen sind Ladegrenze (Auto), Zieltemperatur, Höchstleistung und
Wärmepuffer (Warmwasser), Leistung und Ziel (Batterie von Hand), die
Batterie-Einstellungen und der Tarif.

### Ein Tipp mit großer Wirkung am Bildschirmrand

Ursache: Die Daumenleiste über der Tab-Leiste (Boost, Sofort laden) und die
großen Knöpfe auf den Geräteseiten lösten mit einem Tipp aus. Ein Daumen, der
beim Scrollen am Rand hängen bleibt, startete so einen Boost oder Volllast.

Lösung: Am Touchscreen fragt ein kurzes Blatt nach (`Confirm.tsx`), mit
„Abbrechen“ und der Aktion. Mit der Maus wirkt der Klick wie bisher sofort.

Bestätigt werden:

* Boost (Daumenleiste, Übersicht, Warmwasser)
* Sofort voll laden (Daumenleiste, Übersicht, Auto)
* jeder Batterie-Befehl außer „Automatik“
* Regelung pausieren

### Kleinigkeiten am Touchscreen

* Kein hängender Hover nach dem Antippen (`@media (hover: none)`).
* Eingabefelder mit 16 px, damit iOS nicht hineinzoomt.
* Sichtbares Drücken bei Tabs, Segmenten und Menüeinträgen.
* „Mehr“ öffnet sich als Blatt, das sich am Griff nach unten wegziehen lässt.
  Die Seite dahinter scrollt nicht mit.
* Haptische Rückmeldung (`navigator.vibrate`) bei Bestätigen und Übernehmen.
  Das wirkt nur unter Android, iOS unterstützt es nicht.
* Manifest mit `id`, `display_override` und Kurzbefehlen (Auto, Warmwasser,
  Wand-Ansicht), App-Farbe statt Grün.

## Tests

`frontend/e2e/` enthält Tests mit Playwright für Chromium und WebKit, alle
Daten gemockt. Anleitung: `frontend/e2e/README.md`.

| Datei | Prüft |
|---|---|
| `test_touch.py` | Wischen über Daumenleiste, Schnellzugriff, Stepper, Wertleiste und Segmente löst nichts aus. Der Regler im Blatt ignoriert senkrecht, schräg und einen Tipp. Waagerecht ziehen speichert genau einmal beim Loslassen, „Rückgängig“ stellt zurück. Stepper sammeln und senden einmal. Boost, Sofort laden und Batterie sperren laufen erst nach Bestätigung. Das Blatt schließt nur über den Griff. Dazu die Ursache des alten Fehlers. |
| `test_layout.py` | 8 Profile (iPhone 15 mit Insel, iPhone SE, Android 360/412, je hoch und quer): nichts Bedienbares unter der Insel, Kopfzeile unter der Statusleiste, Tab-Leiste und Blatt-Fuß über dem Home-Indikator, seitliche Einzüge, Sprunglink versteckt, kein seitliches Scrollen, Felder mindestens 16 px. |
| `compare.py` | Desktop vorher/nachher als Pixelvergleich. |

Ergebnisse:

* `pytest e2e`: 70 Tests bestanden, je 35 in Chromium und WebKit 26.6.
* Desktop: 40 von 40 Seiten gleich, hell und dunkel, 1440 und 1920 px.
* Wand-Ansicht: unverändert.

## Screenshots

`docs/screenshots/2.17/`:

* Kopfzeile vorher und nachher.
* Bestätigung und Werte-Blatt, jeweils hell und dunkel.
* Stepper auf den Seiten Warmwasser, Auto und Einstellungen.
* Übersicht auf iPhone 15 (hoch und quer), iPhone SE und Android 360.

## Offene Punkte

* Die WebKit-Tests laufen mit Playwright-WebKit unter Linux, nicht auf einem
  echten iPhone. Wischgesten werden dort als Pointer-Events nachgestellt.
* Der Statusleisten-Stil gilt in der installierten iOS-App erst beim
  nächsten Start nach einem Designwechsel.
