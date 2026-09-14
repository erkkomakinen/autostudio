"""Lattiatekstuurit: valokuvasta tai tekoälyn generoimasta kuvasta saumaton materiaali.

Valokuvassa on lähes aina valaistusvaihtelua ja rajuja läiskiä, jotka toistuessaan
paljastaisivat kuvion. Käsittely:
1. poistaa suurimittaisen valaistuksen (ylipäästö), jotta pinta on tasaisesti valaistu
2. vaimentaa kontrastia säädettävästi (kiillotettu betoni on hillitympi kuin rapattu seinä)
3. tekee reunoista saumattomat varianssin säilyttävällä ristihäivytyksellä
"""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

SIZE = 1024


def make_seamless(src: Path, contrast: float = 0.6, brightness: float | None = None) -> Image.Image:
    with Image.open(src) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
    side = min(img.size)
    img = ImageOps.fit(img, (side, side), Image.LANCZOS).resize((SIZE, SIZE), Image.LANCZOS)
    a = np.asarray(img, dtype=np.float32)

    # 1. Valaistuksen tasaus: jaetaan pois hyvin pehmeä versio kuvasta
    low = cv2.GaussianBlur(a, (0, 0), SIZE / 6)
    mean = a.reshape(-1, 3).mean(axis=0)
    flat = a / np.maximum(low, 1) * mean

    # 2. Kontrastin vaimennus keskiarvon ympärillä
    flat = mean + (flat - mean) * contrast
    if brightness is not None:
        flat = flat * (brightness / max(1.0, float(mean.mean())))

    # 3. Saumattomuus: sekoitetaan kuva ja puolen kuvan verran siirretty kopio.
    #    Painot ovat nollassa kummankin oman sauman kohdalla.
    n = SIZE
    ramp = np.minimum(np.arange(n), n - 1 - np.arange(n)).astype(np.float32) / (n / 2)
    # Pieni vakio: reunan keskikohdissa molempien kuvien painot olisivat muuten nollia
    w_a = np.minimum(ramp[None, :], ramp[:, None]) ** 1.5 + 1e-3
    shifted = np.roll(flat, (n // 2, n // 2), axis=(0, 1))
    w_b = np.roll(w_a, (n // 2, n // 2), axis=(0, 1))
    total = w_a + w_b + 1e-6
    wa, wb = w_a / total, w_b / total
    m2 = flat.reshape(-1, 3).mean(axis=0)
    blended = wa[..., None] * (flat - m2) + wb[..., None] * (shifted - m2)
    # Varianssin säilytys: ristihäivytys muuten laimentaa pintaa saumakohdissa
    blended = blended / np.sqrt(wa**2 + wb**2)[..., None] + m2
    return Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8))
