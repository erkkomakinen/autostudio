"""Luo oletustaustat (tumma showroom, vaalea studio) ja esimerkkilogon.

Oikeassa käytössä liike lataa oman taustakuvansa ja logonsa käyttöliittymästä.
"""
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
BG_DIR = ROOT / "assets" / "backgrounds"
LOGO_DIR = ROOT / "assets" / "logos"
W, H = 2880, 2160
rng = np.random.default_rng(7)


def noise(scale: float, amount: float) -> np.ndarray:
    small = rng.normal(0, 1, (max(2, int(H / scale)), max(2, int(W / scale)))).astype(np.float32)
    big = cv2.resize(small, (W, H), interpolation=cv2.INTER_CUBIC)
    if scale > 4:
        big = cv2.GaussianBlur(big, (0, 0), scale / 2)
        big /= big.std() + 1e-6
    return big * amount


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def showroom_dark(horizon=0.56):
    y = np.linspace(0, 1, H)[:, None]
    x = np.linspace(-1, 1, W)[None, :]
    # Seinä: lähes musta, pehmeä valokeila keskellä alhaalla
    wall = 12 + 20 * np.exp(-((x / 0.75) ** 2) - (((y - horizon) / 0.35) ** 2))
    # Lattia: sileä harmaa betoni, valaistu keskeltä auton kohdalta
    fy = np.clip((y - horizon) / (1 - horizon), 0, 1)
    spot = np.exp(-((x / 0.8) ** 2) - (((fy - 0.7) / 0.6) ** 2))
    floor = 34 + 30 * fy + 42 * spot
    floor = floor + noise(90, 2.5) + noise(20, 1.2) + noise(1, 1.5)
    t = smoothstep(horizon - 0.004, horizon + 0.02, y)
    img = wall * (1 - t) + floor * t
    rgb = np.stack([img, img, img * 1.015], -1).clip(0, 255).astype(np.uint8)
    return Image.fromarray(rgb)


def studio_light():
    y = np.linspace(0, 1, H)[:, None]
    x = np.linspace(-1, 1, W)[None, :]
    # Saumaton kaarikulissi: vaalea, tummuu hieman ylä- ja sivureunoille
    img = 238 - 16 * x**2 - 14 * (1 - y) ** 3 - 10 * smoothstep(0.55, 1.0, y) * x**2
    img = img + noise(1, 0.8)
    rgb = np.stack([img, img, img], -1).clip(0, 255).astype(np.uint8)
    return Image.fromarray(rgb)


def font(size):
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def sample_logo():
    im = Image.new("RGBA", (1600, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((10, 10, 280, 280), outline=(214, 170, 64, 255), width=18)
    d.text((145, 150), "EA", font=font(120), fill=(214, 170, 64, 255), anchor="mm")
    d.text((330, 150), "Esimerkki Autot Oy", font=font(110), fill=(214, 170, 64, 255), anchor="lm")
    return im.crop(im.getbbox())


if __name__ == "__main__":
    BG_DIR.mkdir(parents=True, exist_ok=True)
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    showroom_dark().save(BG_DIR / "showroom-dark.jpg", quality=95)
    studio_light().save(BG_DIR / "studio-light.jpg", quality=95)
    sample_logo().save(LOGO_DIR / "esimerkki-logo.png")
    print("Valmis", file=sys.stderr)
