"""Komentorivikäsittely: python scripts/batch.py <kuvakansio> <tuloskansio> [asetukset.json] [logo.png]

Analysoi kuvat (GPU) ja kokoaa lopputulokset. Analyysit tallennetaan tuloskansion
_analysis-alikansioon, joten uudelleenajo eri asetuksilla ei aja tekoälyä uudestaan.
"""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import analysis, compose  # noqa: E402


def main():
    src_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    settings = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8")) if len(sys.argv) > 3 else {}
    dealer_dir = out_dir / "_dealer"
    dealer_dir.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 4:
        shutil.copy(sys.argv[4], dealer_dir / "logo.png")
        settings["logo"] = "logo.png"

    files = sorted(p for p in src_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    for f in files:
        adir = out_dir / "_analysis" / f.stem
        if not (adir / "meta.json").exists():
            t = time.time()
            meta = analysis.analyze(f, adir)
            print(f"{f.name}: {meta['kind']} ikkunoita={meta['windows']} ({time.time() - t:.1f}s)")
        t = time.time()
        img = compose.render(f, adir, settings, dealer_dir)
        (out_dir / f"{f.stem}.jpg").write_bytes(compose.to_jpeg(img, compose.settings_with_defaults(settings)["quality"]))
        print(f"  koottu {time.time() - t:.2f}s")


if __name__ == "__main__":
    main()
