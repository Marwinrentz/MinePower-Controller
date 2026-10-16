#!/usr/bin/env python3
"""Screenshots aus einem Xcode-Ergebnisbündel holen und sprechend benennen.

    python3 tools/export_screenshots.py results/iphone.xcresult screenshots

Nutzt `xcrun xcresulttool export attachments` (Xcode 16+). Die Anhänge heißen
im Test `<gerät>_<variante>_<bereich>`; Xcode hängt eine laufende Nummer und
eine UUID an, die hier wieder abgeschnitten werden. Zusätzlich entsteht
`manifest.json` für die Vorschau (`tools/preview-server`).
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile


def main(result: str, out_dir: str) -> None:
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["xcrun", "xcresulttool", "export", "attachments", "--path", result,
                        "--output-path", tmp], check=True)
        manifest = json.loads((pathlib.Path(tmp) / "manifest.json").read_text())
        for test in manifest:
            for item in test.get("attachments", []):
                exported = pathlib.Path(tmp) / item["exportedFileName"]
                if exported.suffix.lower() not in (".png", ".jpeg", ".jpg"):
                    continue
                name = item.get("suggestedHumanReadableName", exported.name)
                name = re.sub(r"_\d+_[0-9A-F-]{36}(?=\.\w+$)", "", name, flags=re.I)
                shutil.copy(exported, out / name)
    index = sorted(p.name for p in out.glob("*.png"))
    (out / "manifest.json").write_text(json.dumps(index, indent=1))
    print(f"{len(index)} Screenshots in {out}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
