"""Einrichtungsanleitungen für Zugangsdaten (seit 3.5).

Für jeden Treiber mit Token, Passwort oder API-Schlüssel steht hier Schritt
für Schritt, woher der Wert kommt. Das Geräteformular zeigt die Schritte
über den Feldern; Treiber mit ``oauth`` bekommen zusätzlich den Knopf
'Verbinden', der die Anmeldung beim Hersteller im Browser erledigt.

Zentral statt in jedem Treiber, damit gleiche Wege (Home Assistant, MQTT)
gleich erklärt werden. ``registry.register`` hängt die Schritte an die
Treiber-Metadaten; ein Test stellt sicher, dass kein Treiber mit
Passwortfeld ohne Anleitung bleibt.
"""
from __future__ import annotations

from .base import SetupStep


def _s(text: str, link: str | None = None, label: str | None = None) -> SetupStep:
    return SetupStep(text=text, link=link, link_label=label)


HOME_ASSISTANT = [
    _s("Home-Assistant-URL oben eintragen, so wie du Home Assistant im Browser öffnest "
       "(z. B. http://homeassistant.local:8123)."),
    _s("Home Assistant öffnen, unten links auf deinen Namen (Profil) klicken und den Reiter "
       "„Sicherheit“ wählen.", "{url}/profile/security", "Profil in Home Assistant öffnen"),
    _s("Ganz unten bei „Langlebige Zugangstoken“ auf „Token erstellen“ klicken, Name „MinePower“."),
    _s("Das Token sofort kopieren (es wird nur einmal angezeigt) und unten bei „Access-Token“ einfügen."),
    _s("Entitäten findest du in Home Assistant unter Einstellungen → Geräte & Dienste → Entitäten "
       "(z. B. sensor.netz_leistung). Danach „Verbindung testen“."),
]

MQTT = [
    _s("Broker ist der MQTT-Server, der die Werte bekommt (z. B. Mosquitto auf dem Server oder das "
       "Mosquitto-Add-on in Home Assistant). IP-Adresse eintragen, Port meist 1883."),
    _s("Benutzer und Passwort sind die Zugangsdaten des Brokers. Beim Home-Assistant-Add-on ist das ein "
       "Home-Assistant-Benutzer (Einstellungen → Personen → Benutzer anlegen, z. B. „mqtt“)."),
    _s("Ohne Anmeldung am Broker (anonymous) beide Felder leer lassen."),
    _s("Die Topics zeigt z. B. das Programm MQTT Explorer (mqtt-explorer.com), wenn du dich damit am "
       "Broker anmeldest.", "https://mqtt-explorer.com", "MQTT Explorer"),
]

HTTP_AUTH = [
    _s("Nur nötig, wenn die Adresse eine Anmeldung verlangt. Sonst „Authorization-Header“ leer lassen."),
    _s("Token: „Bearer “ und das Token eintragen, z. B. Bearer abc123."),
    _s("Benutzer und Passwort: „Basic “ und Benutzer:Passwort in Base64, z. B. Basic dXNlcjpwYXNz "
       "(Umrechner: base64encode.org).", "https://www.base64encode.org", "Base64 umrechnen"),
]

TASMOTA = [
    _s("IP-Adresse: In der Tasmota-Weboberfläche unter „Information“ oder in der Geräteliste deines Routers."),
    _s("Ein Passwort gibt es nur, wenn du eines gesetzt hast: Tasmota → Configuration → Configure Other → "
       "„Web Admin Password“. Benutzer ist dann „admin“.", "http://{host}/co", "Tasmota-Einstellungen öffnen"),
    _s("Ohne Web-Passwort beide Felder leer lassen."),
]

GUIDES: dict[str, list[SetupStep]] = {
    **{d: HOME_ASSISTANT for d in ("ha_battery", "ha_inverter", "ha_meter", "ha_switch_heater", "ha_wallbox",
                                   "sgready_homeassistant")},
    **{d: MQTT for d in ("mqtt_battery", "mqtt_inverter", "mqtt_meter", "mqtt_relay_heater", "mqtt_wallbox")},
    **{d: HTTP_AUTH for d in ("http_battery", "http_inverter", "http_meter", "http_relay_heater")},
    "tasmota_meter": TASMOTA,
    "tasmota_relay_heater": TASMOTA,
    "openwb_mqtt": [
        _s("Broker ist die IP-Adresse der openWB, Port 1883."),
        _s("Die openWB 1.9 braucht keine Anmeldung: Benutzer und Passwort leer lassen. Nur wenn du am "
           "openWB-Broker selbst Zugangsdaten eingerichtet hast, diese eintragen."),
        _s("Ladepunkt: Nummer wie in der openWB-Oberfläche (meist 1)."),
    ],
    "viessmann_vicare": [
        _s("Viessmann Developer Portal öffnen und mit deinem ViCare-Konto anmelden (dieselben Zugangsdaten "
           "wie in der ViCare-App).", "https://app.developer.viessmann-climatesolutions.com",
           "Viessmann Developer Portal"),
        _s("„Clients“ → „Client hinzufügen“: Name „MinePower“, Google reCAPTCHA aus, Typ „Public“. Als "
           "Redirect-URI die Adresse aus dem Kasten unten eintragen (Knopf „Kopieren“)."),
        _s("Die angezeigte Client-ID kopieren und unten bei „Client-ID“ einfügen."),
        _s("Auf „Mit Viessmann verbinden“ klicken, anmelden und den Zugriff erlauben. MinePower trägt das "
           "Token selbst ein."),
        _s("„Verbindung testen“ und speichern. Das Token gilt 180 Tage, danach einfach erneut verbinden."),
    ],
    "daikin_onecta": [
        _s("Daikin Developer Portal öffnen und mit deinem Onecta-Konto anmelden (dieselben Zugangsdaten wie "
           "in der Onecta-App).", "https://developer.cloud.daikineurope.com", "Daikin Developer Portal"),
        _s("„My Apps“ → „New application“: Name „MinePower“. Als Redirect-URI die Adresse aus dem Kasten "
           "unten eintragen (Knopf „Kopieren“). Daikin verlangt eine https-Adresse."),
        _s("Client-ID und Client-Secret kopieren und unten einfügen. Das Secret zeigt Daikin nur einmal."),
        _s("Auf „Mit Daikin verbinden“ klicken, anmelden und den Zugriff erlauben."),
        _s("Zeigt der Browser danach „Seite nicht erreichbar“, ist das normal: die komplette Adresse aus der "
           "Adresszeile kopieren und im Kasten unten einfügen."),
        _s("„Verbindung testen“ zeigt alle Geräte des Kontos. Speichern – MinePower erneuert das Token "
           "danach selbst."),
    ],
    "homey_pro": [
        _s("Homey-Web-App öffnen und anmelden.", "https://my.homey.app", "my.homey.app öffnen"),
        _s("Einstellungen (Zahnrad) → „API-Schlüssel“ → „Neuer API-Schlüssel“, Name „MinePower“, Rechte "
           "„Geräte ansehen“ und „Geräte steuern“ anhaken."),
        _s("Schlüssel kopieren (wird nur einmal angezeigt) und unten bei „API-Schlüssel“ einfügen."),
        _s("IP-Adresse des Homey Pro: Einstellungen → Allgemein, oder in der Geräteliste deines Routers."),
        _s("Geräte-ID leer lassen und „Verbindung testen“: MinePower zeigt alle Geräte mit ihrer ID. Die "
           "passende ID kopieren und eintragen."),
    ],
    "easee": [
        _s("Benutzer und Passwort sind die Zugangsdaten der Easee-App (E-Mail oder Telefonnummer mit "
           "Ländervorwahl, z. B. +49…)."),
        _s("Seriennummer: in der Easee-App unter der Wallbox oder auf dem Typenschild (beginnt mit EH)."),
        _s("Danach „Verbindung testen“. MinePower meldet sich selbst an und erneuert die Anmeldung."),
    ],
    "zaptec": [
        _s("Benutzer und Passwort sind die Zugangsdaten des Zaptec-Portals bzw. der Zaptec-App.",
           "https://portal.zaptec.com", "Zaptec-Portal"),
        _s("Lader-ID leer lassen nimmt den ersten Lader im Konto. Bei mehreren: im Portal den Lader öffnen, "
           "die ID steht in der Adresszeile."),
        _s("In der Zaptec-App den Stand-alone-Modus ausschalten, sonst nimmt der Lader keine Vorgaben an."),
    ],
    "fronius_wattpilot": [
        _s("Passwort ist das Passwort, das du bei der Einrichtung in der App „Fronius Solar.wattpilot“ "
           "vergeben hast."),
        _s("Vergessen? In der App unter Einstellungen → „Passwort ändern“ neu setzen."),
        _s("IP-Adresse: in der App unter Internet → WLAN oder in der Geräteliste deines Routers."),
    ],
    "hardybarth_salia": [
        _s("Benutzer und Passwort sind nur nötig, wenn die Weboberfläche des Salia-Controllers eine Anmeldung "
           "verlangt. Sonst leer lassen.", "http://{host}", "Weboberfläche öffnen"),
    ],
    "sma_evcharger": [
        _s("Die Weboberfläche des EV Chargers öffnen.", "{scheme}://{host}", "Weboberfläche öffnen"),
        _s("Benutzer und Passwort sind die Zugangsdaten dieser Oberfläche (Benutzergruppe „Installateur“ oder "
           "„Benutzer“), so wie bei der Erstinbetriebnahme vergeben."),
        _s("Vergessen? Das Passwort lässt sich mit dem WLAN-Passwort vom Typenschild (WPA2-PSK) zurücksetzen "
           "– Anleitung im Handbuch."),
    ],
    "ocpp_wallbox": [
        _s("Das Passwort ist optional. Hast du in der Wallbox beim OCPP-Backend ein Passwort (Basic Auth) "
           "eingetragen, muss hier dasselbe stehen."),
        _s("In der Wallbox als OCPP-Server ws://<IP von MinePower>:<Server-Port>/<Charge-Point-ID> eintragen."),
    ],
    "tesla_vehicle": [
        _s("Empfohlen: der Tesla-BLE-Proxy im Haus (Bluetooth, ohne Cloud). Dann „Anbindung“ = BLE-Proxy und "
           "nur dessen Adresse eintragen; Proxy-Auth-Header bleibt leer, außer du hast am Proxy einen gesetzt."),
        _s("Fleet-API (Cloud) braucht ein eigenes Entwicklerkonto bei Tesla und ein Access-Token daraus.",
           "https://developer.tesla.com", "Tesla Developer"),
        _s("Die VIN steht in der Tesla-App unter „Software“ oder unten an der Windschutzscheibe."),
    ],
}

OAUTH: dict[str, str] = {"viessmann_vicare": "viessmann", "daikin_onecta": "daikin_onecta"}


def apply(meta) -> None:
    if not meta.setup and meta.id in GUIDES:
        meta.setup = list(GUIDES[meta.id])
    if meta.oauth is None and meta.id in OAUTH:
        meta.oauth = OAUTH[meta.id]
