"""Lattiamateriaalin vaihto tyyliprofiiliin: tekoälyllä generoitu tai ladattu kuva.

Sama kuva käytetään kahdesti (ks. ai_background): saumattomana tekstuurina pohjakuvan 3D-lattiassa
ja viimeistelynäytteenä kuvamallille, jolloin kaikki kuvat ja mallit päätyvät samaan lattiaan.
"""
import base64
import io
import json
import os
import shutil
import time
import urllib.error
import urllib.request

from PIL import Image

from . import config, scene, settings, textures

GEN_MODEL = "bytedance-seed/seedream-5-0-pro"  # testeissä ainoa, joka teki tarkan materiaalikuvan (Gemini kieltäytyi)
MATERIALS = config.ASSETS_DIR / "floors" / "materials"
STYLES = config.ASSETS_DIR / "styles"
PROMPT = (
    "Top-down orthographic material scan of a single large floor tile surface made of {description}, filling the "
    "entire frame. Perfectly flat, camera pointing straight down, even diffuse lighting, no reflections, no "
    "highlights, no shadows, no vignette, no perspective, no grout lines, no tile edges, no objects. Sharp, high "
    "detail, photographic, like a PBR texture scan."
)


def generate_floor(description: str) -> tuple[Image.Image, float]:
    """Palauttaa materiaalikuvan ja hinnan euroina."""
    payload = {"model": GEN_MODEL, "prompt": PROMPT.format(description=description[:300]), "aspect_ratio": "1:1"}
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/images", data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "Content-Type": "application/json",
                 "X-Title": "Autostudio"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Materiaalin generointi epäonnistui ({e.code}): {e.read().decode()[:200]}")
    image = Image.open(io.BytesIO(base64.b64decode(data["data"][0]["b64_json"]))).convert("RGB")
    cost_eur = round(float((data.get("usage") or {}).get("cost") or 0) * settings.usd_eur(), 4)
    return image, cost_eur


def install_floor(style_name: str, image: Image.Image, source: str, contrast: float = 1.0) -> dict:
    """Asentaa kuvan tyylin lattiaksi. Palauttaa päivitetyn style.jsonin."""
    style_dir = STYLES / style_name
    style = json.loads((style_dir / "style.json").read_text(encoding="utf-8"))
    old_dir = MATERIALS / style.get("base_material", "")
    mat_name = f"{style_name}-lattia"
    mat_dir = MATERIALS / mat_name
    mat_dir.mkdir(parents=True, exist_ok=True)

    raw = mat_dir / "raw.png"
    image.save(raw)
    textures.make_seamless(raw, contrast=contrast).save(mat_dir / "color.jpg", quality=95)
    for f in ("rough.jpg", "normal.jpg"):
        src = old_dir / f if (old_dir / f).exists() else MATERIALS / "graniitti-ttc" / f
        if src.exists() and src.resolve() != (mat_dir / f).resolve():
            shutil.copy(src, mat_dir / f)
    cfg_path = old_dir / "material.json" if (old_dir / "material.json").exists() else MATERIALS / "sf-trading-ai-graniitti" / "material.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.update({"name": f"{style.get('name', style_name)}: lattia", "source": source, "updated": time.time()})
    (mat_dir / "material.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    image.resize((1024, 1024), Image.LANCZOS).save(style_dir / "viimeistely-lattia.jpg", quality=95)
    style.update(guide_mode="base", base_material=mat_name, finish_floor_sample="viimeistely-lattia.jpg")
    (style_dir / "style.json").write_text(json.dumps(style, ensure_ascii=False, indent=2), encoding="utf-8")
    scene.load_material.cache_clear()  # sama kansionimi, uusi sisältö
    return style
