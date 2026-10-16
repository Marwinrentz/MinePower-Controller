#!/usr/bin/env python3
"""TestFlight-Hinweise ("Was testen?") aus ios/CHANGELOG.md setzen.

Wartet, bis App Store Connect den Build verarbeitet hat, und schreibt den
obersten Abschnitt des Änderungsprotokolls als deutschen Testhinweis.
Benötigt: pyjwt, cryptography; Umgebungsvariablen ASC_KEY_ID, ASC_ISSUER_ID.
"""
import argparse
import json
import os
import re
import time
import urllib.request

import jwt

API = "https://api.appstoreconnect.apple.com/v1"


def token(key_path: str) -> str:
    now = int(time.time())
    return jwt.encode({"iss": os.environ["ASC_ISSUER_ID"], "iat": now, "exp": now + 1200,
                       "aud": "appstoreconnect-v1"}, open(key_path).read(), algorithm="ES256",
                      headers={"kid": os.environ["ASC_KEY_ID"], "typ": "JWT"})


def call(method: str, path: str, auth: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {auth}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def notes(changelog: str) -> str:
    text = open(changelog, encoding="utf-8").read()
    sections = re.split(r"^## ", text, flags=re.M)
    latest = sections[1] if len(sections) > 1 else text
    body = latest.split("\n", 1)[1] if "\n" in latest else latest
    return body.strip()[:3900]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--changelog", required=True)
    args = parser.parse_args()
    auth = token(args.key)
    apps = call("GET", f"/apps?filter[bundleId]={args.bundle_id}", auth)["data"]
    app_id = apps[0]["id"]
    build = None
    for _ in range(60):  # bis zu 30 Minuten Verarbeitung
        found = call("GET", f"/builds?filter[app]={app_id}&filter[version]={args.build}", auth)["data"]
        if found and found[0]["attributes"].get("processingState") == "VALID":
            build = found[0]
            break
        time.sleep(30)
        auth = token(args.key)
    if not build:
        raise SystemExit("Build noch nicht verarbeitet; Hinweise bitte in App Store Connect ergänzen.")
    text = notes(args.changelog)
    existing = call("GET", f"/builds/{build['id']}/betaBuildLocalizations", auth)["data"]
    de = next((loc for loc in existing if loc["attributes"]["locale"].startswith("de")), None)
    if de:
        call("PATCH", f"/betaBuildLocalizations/{de['id']}", auth,
             {"data": {"type": "betaBuildLocalizations", "id": de["id"], "attributes": {"whatsNew": text}}})
    else:
        call("POST", "/betaBuildLocalizations", auth,
             {"data": {"type": "betaBuildLocalizations", "attributes": {"locale": "de-DE", "whatsNew": text},
                       "relationships": {"build": {"data": {"type": "builds", "id": build["id"]}}}}})
    print("Testhinweise gesetzt.")


if __name__ == "__main__":
    main()
