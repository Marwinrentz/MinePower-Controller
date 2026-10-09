"""Pixelvergleich zweier Screenshot-Ordner (Desktop vorher/nachher).

    python e2e/compare.py <vorher> <nachher> [diff-ordner]

Meldet je Datei den Anteil abweichender Pixel; schreibt bei Abweichung ein
Differenzbild. Rückgabewert 1, wenn eine Datei abweicht oder fehlt.
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops

#: Abweichung darunter gilt als Animationsrauschen (Prozent der Pixel)
NOISE_PCT = 0.05


def main() -> int:
    before, after = Path(sys.argv[1]), Path(sys.argv[2])
    diff_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    bad = 0
    for a in sorted(before.glob("*.png")):
        b = after / a.name
        if not b.exists():
            print(f"FEHLT   {a.name}")
            bad += 1
            continue
        ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
        if ia.size != ib.size:
            print(f"GRÖSSE  {a.name}: {ia.size} → {ib.size}")
            bad += 1
            continue
        diff = ImageChops.difference(ia, ib)
        box = diff.getbbox()
        if box is None:
            print(f"gleich  {a.name}")
            continue
        changed = sum(1 for px in diff.convert("L").point(lambda v: 255 if v > 24 else 0).getdata() if px)
        share = changed / (ia.size[0] * ia.size[1]) * 100
        if share < NOISE_PCT:
            # Laufende Animationen (Ladekabel, Diagramm-Einblendung) – kein Layout-Unterschied
            print(f"gleich  {a.name} (Rauschen {share:.3f} %)")
            continue
        print(f"ANDERS  {a.name}: {share:.3f} % in {box}")
        bad += 1
        if diff_dir:
            diff_dir.mkdir(parents=True, exist_ok=True)
            diff.point(lambda v: 255 if v else 0).save(diff_dir / a.name)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
