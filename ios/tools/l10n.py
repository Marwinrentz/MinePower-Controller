#!/usr/bin/env python3
"""Texte der App für den String-Katalog finden (ohne Xcode).

Xcode legt Schlüssel beim Bauen selbst an; ohne Mac übernimmt das dieses
Skript. Es sucht übersetzbare Literale (Text, Button, String(localized:),
eigene Bausteine mit LocalizedStringKey …), bildet daraus die Schlüssel wie
Swift (Interpolation → %@ / %lld, "%" → "%%") und vergleicht mit den
Katalogen.

    python3 ios/tools/l10n.py check          # fehlende Übersetzungen melden (Exit 1)
    python3 ios/tools/l10n.py missing        # fehlende Schlüssel als JSON ausgeben
    python3 ios/tools/l10n.py merge FILE     # Übersetzungen {"Schlüssel": "English"} einspielen

Zusätzliche Schlüssel im Katalog schaden nicht; fehlt einer, bleibt der Text
deutsch. Ganzzahlige Interpolationen erkennt eine Liste bekannter Ausdrücke
(INT_EXPRESSIONS) – neue Fälle dort ergänzen.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGS = {
    "app": (ROOT / "Sources/MinePower/Resources/Localizable.xcstrings",
            [ROOT / "Sources/MinePower"]),
    "widgets": (ROOT / "Widgets/Localizable.xcstrings",
                [ROOT / "Widgets", ROOT / "Sources/MinePower/Shared",
                 ROOT / "Sources/MinePower/Design/Theme.swift", ROOT / "Sources/MinePower/Design/Typography.swift",
                 ROOT / "Sources/MinePower/Design/WebIcon.swift", ROOT / "Sources/MinePower/Design/BrandMark.swift"]),
}

# Literale direkt hinter diesen Stellen sind keine Oberflächentexte.
SKIP_BEFORE = re.compile(
    r"(verbatim:\s*|accessibilityIdentifier\(\s*|identifier:\s*|systemImage:\s*|systemName:\s*|systemImageName:\s*|"
    r"URL\(string:\s*|forKey:\s*|forInfoDictionaryKey:\s*|suiteName:\s*|Image\(\s*|\.custom\(\s*|kind:\s*|"
    r"identifier:\s*|Locale\(identifier:\s*|named:\s*|withExtension:\s*|subdirectory:\s*|hasPrefix\(\s*|"
    r"contains\(\s*|firstMatch\(of:\s*|replacingOccurrences\(of:\s*|with:\s*|string\(\s*|bool\(\s*|double\(\s*|"
    r"int\(\s*|object\(\s*|number\(\s*|set\(\s*|section\(\s*|text\(\s*|flag\(\s*|secret\(\s*|update\(\s*|"
    r"\[\s*|==\s*|!=\s*|case\s+|:\s*\.init\(value:\s*|value:\s*|Character\(\s*|KeyEquivalent\(\s*|"
    r"keyboardShortcut\(\s*)$")

# Interpolationen, die Swift als Ganzzahl (%lld) in den Schlüssel schreibt.
INT_EXPRESSIONS = [
    r"^Int\(.*\)$", r"\.count$", r"^count$", r"^index \+ 1$", r"^n$", r"^detected$", r"^setN$",
    r"^limits\[.*\] \?\? 1$", r"^status\.count$", r"^list\.count - 1$", r"\+ 1$", r"\?\? 1$",
]


def literals(source: str):
    """Einfache Literale ("…", nicht \"\"\"…\"\"\") mit Position; Interpolationen als Liste."""
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        if source.startswith("//", i):
            j = source.find("\n", i)
            i = n if j < 0 else j
            continue
        if source.startswith("/*", i):
            j = source.find("*/", i)
            i = n if j < 0 else j + 2
            continue
        if source.startswith('"""', i):
            j = source.find('"""', i + 3)
            i = n if j < 0 else j + 3
            continue
        if c == '#' and source.startswith('#"', i):
            j = source.find('"#', i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == '"':
            start = i
            parts, exprs, buf = [], [], []
            i += 1
            while i < n and source[i] != '"':
                if source[i] == "\\" and i + 1 < n:
                    nxt = source[i + 1]
                    if nxt == "(":
                        depth, j = 1, i + 2
                        k = j
                        in_str = False
                        while k < n and depth:
                            ch = source[k]
                            if in_str:
                                if ch == "\\":
                                    k += 2
                                    continue
                                if ch == '"':
                                    in_str = False
                            else:
                                if ch == '"':
                                    in_str = True
                                elif ch == "(":
                                    depth += 1
                                elif ch == ")":
                                    depth -= 1
                            k += 1
                        parts.append("".join(buf))
                        buf = []
                        exprs.append(source[j:k - 1].strip())
                        i = k
                        continue
                    buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\", "'": "'", "0": "\0"}.get(nxt, nxt))
                    i += 2
                    continue
                if source[i] == "\n":
                    break
                buf.append(source[i])
                i += 1
            parts.append("".join(buf))
            yield start, parts, exprs
            i += 1
            continue
        i += 1


def specifier(expr: str) -> str:
    return "%lld" if any(re.search(p, expr) for p in INT_EXPRESSIONS) else "%@"


def key_for(parts, exprs) -> str:
    if not exprs:
        return parts[0]
    out = []
    for index, part in enumerate(parts):
        out.append(part.replace("%", "%%"))
        if index < len(exprs):
            out.append(specifier(exprs[index]))
    return "".join(out)


def looks_like_text(text: str) -> bool:
    if re.match(r"^(-UITest|Inter-|#)", text):
        return False
    if " " not in text and re.search(r"[:_]|\.(?!$)", text):  # Bezeichner wie "mcp.publicBase", "off:"
        return False
    plain = re.sub(r"%(lld|@|lf)", "", text)
    if not re.search(r"[A-Za-zÄÖÜäöüß]{2,}", plain):
        return False
    if re.fullmatch(r"[a-z0-9_.\-/]+", plain):  # Schlüssel, Pfade, Bezeichner
        return False
    if re.fullmatch(r"[A-Z][A-Za-z0-9]*", plain) and plain in {"Inter", "MinePower", "PV", "GitHub", "Tibber", "Demo"}:
        return False
    return True


def extract(paths):
    keys = {}
    files = []
    for p in paths:
        files += [p] if p.is_file() else sorted(p.rglob("*.swift"))
    for f in files:
        if f.name == "WebIconPaths.swift":  # Pfaddaten der Symbole
            continue
        src = f.read_text(encoding="utf-8")
        loc_end = -1
        for start, parts, exprs in literals(src):
            before = src[max(0, start - 60):start]
            if SKIP_BEFORE.search(before):
                continue
            # Loc.t("deutsch", "english"): beide Literale gehören nicht in den Katalog.
            if "Loc.t(" in src[max(0, start - 12):start]:
                loc_end = src.find('"', start + 1)
                continue
            if loc_end >= 0 and re.fullmatch(r'"?,\s*', src[loc_end:start]):
                loc_end = -1
                continue
            loc_end = -1
            key = key_for(parts, exprs)
            if not looks_like_text(key):
                continue
            keys.setdefault(key, f"{f.relative_to(ROOT)}:{src.count(chr(10), 0, start) + 1}")
    return keys


def load(catalog: Path):
    if catalog.exists():
        return json.loads(catalog.read_text(encoding="utf-8"))
    return {"sourceLanguage": "de", "strings": {}, "version": "1.0"}


def save(catalog: Path, data):
    data["strings"] = dict(sorted(data["strings"].items(), key=lambda kv: kv[0].lower()))
    catalog.write_text(json.dumps(data, ensure_ascii=False, indent=2, separators=(",", " : ")) + "\n", encoding="utf-8")


def english(entry):
    return entry.get("localizations", {}).get("en", {}).get("stringUnit", {}).get("value")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "merge":
        translations = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        for name, (catalog, paths) in CATALOGS.items():
            data = load(catalog)
            for key in extract(paths):
                if key in translations:
                    data["strings"].setdefault(key, {}).setdefault("localizations", {})["en"] = {
                        "stringUnit": {"state": "translated", "value": translations[key]}}
                else:
                    data["strings"].setdefault(key, {})
            save(catalog, data)
        return
    missing = {}
    for name, (catalog, paths) in CATALOGS.items():
        data = load(catalog)["strings"]
        for key, where in extract(paths).items():
            if not english(data.get(key, {})):
                missing[key] = where
    if cmd == "missing":
        print(json.dumps(missing, ensure_ascii=False, indent=1))
        return
    for key, where in sorted(missing.items(), key=lambda kv: kv[1]):
        print(f"{where}: {key}")
    print(f"{len(missing)} ohne englische Übersetzung")
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
