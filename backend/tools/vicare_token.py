"""Einmalig ein Refresh-Token für die Viessmann-API (ViCare) erzeugen.

Voraussetzung: Im Viessmann Developer Portal (developer.viessmann-climatesolutions.com)
einen API-Client anlegen – Typ 'Public', Redirect-URI ``http://localhost:4200/``,
Google reCAPTCHA aus. Die angezeigte Client-ID hier angeben.

    python3 tools/vicare_token.py <CLIENT_ID>

1. Das Skript gibt eine Adresse aus. Im Browser öffnen und mit dem
   ViCare-Konto anmelden.
2. Der Browser landet danach auf ``http://localhost:4200/?code=…`` – die Seite
   lädt nicht, das ist richtig. Die komplette Adresse aus der Adresszeile
   kopieren und hier einfügen.
3. Das Skript gibt das Refresh-Token aus (gilt 180 Tage). In MinePower beim
   Gerät 'Viessmann Wärmepumpe (ViCare-API)' eintragen.

Nur Python-Standardbibliothek, keine Daten verlassen den Rechner außer an
iam.viessmann.com.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import urllib.parse
import urllib.request

AUTH = "https://iam.viessmann.com/idp/v3/authorize"
TOKEN = "https://iam.viessmann.com/idp/v3/token"
REDIRECT = "http://localhost:4200/"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    client_id = sys.argv[1].strip()
    verifier, challenge = pkce()
    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": "IoT User offline_access", "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print("1) Diese Adresse im Browser öffnen und anmelden:\n\n" + url + "\n")
    back = input("2) Adresse nach der Anmeldung (http://localhost:4200/?code=…) hier einfügen: ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(back).query).get("code", [""])[0] or back
    body = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": REDIRECT, "grant_type": "authorization_code",
        "code_verifier": verifier, "code": code,
    }).encode()
    req = urllib.request.Request(TOKEN, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 – feste HTTPS-Adresse
        res = json.loads(resp.read().decode())
    token = res.get("refresh_token")
    if not token:
        print("Kein Refresh-Token erhalten:", {k: v for k, v in res.items() if "token" not in k})
        return 1
    print("\n3) Refresh-Token (in MinePower eintragen, nicht weitergeben):\n\n" + token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
