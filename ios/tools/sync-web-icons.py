#!/usr/bin/env python3
"""Web-Symbole (frontend/src/components/icons.tsx) nach iOS übernehmen.

    python3 ios/tools/sync-web-icons.py

Schreibt ios/Sources/MinePower/Design/WebIconPaths.swift neu. Die App zeichnet
die Pfade mit demselben 24er-Raster und derselben Strichstärke wie das Web.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "frontend/src/components/icons.tsx"
OUT = ROOT / "ios/Sources/MinePower/Design/WebIconPaths.swift"
RENAME = {"import": "importArrow"}  # Swift-Schlüsselwort


def main() -> None:
    src = SRC.read_text()
    start = src.index("const PATHS")
    body = src[start:src.index("};", start)]
    pairs = re.findall(r'^\s*(\w+):\s*"([^"]+)"', body, re.M)
    cases = ", ".join(RENAME.get(k, k) for k, _ in pairs)
    lines = "\n".join(f'        case .{RENAME.get(k, k)}: "{v}"' for k, v in pairs)
    OUT.write_text(f'''import MinePowerKit
import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

/// Die Strichsymbole der Weboberfläche (`frontend/src/components/icons.tsx`),
/// gleiche Pfade, gleiches 24er-Raster, gleiche Strichstärke 1,7.
///
/// Erzeugt mit `ios/tools/sync-web-icons.py`; bei neuen Web-Symbolen erneut ausführen.
enum WebIcon: String, CaseIterable, Sendable {{
    case {cases}

    var pathData: String {{
        switch self {{
{lines}
        }}
    }}

    /// Flächen statt Linien (wie `FILLED` im Web).
    var isFilled: Bool {{ self == .play || self == .stop || self == .dashboard }}
    /// Punkte: dicke Linie mit runden Enden.
    var isBold: Bool {{ self == .more }}
}}
''')
    print(f"{len(pairs)} Symbole -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
