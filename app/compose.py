"""Kuvan kokoaminen (CPU, ei tekoälyä): tausta + varjo + auto + ikkunat + logo.

Auton pikselit, joiden alfa on 1 ja jotka eivät ole ikkunaa, kopioidaan
alkuperäisestä kuvasta sellaisenaan. Vain reunojen puoliläpinäkyvät pikselit
(väri puhdistetaan vanhan taustan sävystä) ja ikkunat muuttuvat. Jos autoa
skaalataan, uudelleennäytteistys on luonnollisesti ainoa muutos.
"""
import io
import json
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import config, scene
from .imageutil import cover, load_image, load_mask

DEFAULT_SETTINGS = {
    "width": 1440,
    "height": 1080,
    "background": "",  # liikkeen oma taustakuva (tiedostonimi liikkeen kansiossa)
    "background_preset": "showroom-dark",
    "background_mode": "image",  # image = kiinteä taustakuva, studio3d = lattia kuvakulman mukaan
    "floor": "tiles",  # tiles = terrazzo-laatat, texture = valokuvatekstuuri (esim. betoni)
    "floor_texture": "",  # tekstuuritiedosto liikkeen kansiossa tai assets/floors
    "use_roll": 0,
    "align_to_car": 0,
    "guide_line_m": 0.012,  # ohjauspohjan saumaviivan leveys metreinä (0 = ei viivoja)
    "guide_line_tone": 0.2,  # ohjauspohjan saumaviivan sävykerroin (0.2 tumma, 1.4 vaalea)  # 1 = laattojen saumat auton suuntaan (pyörien kosketuskohdista)  # 1 = käytä kameran kiertymäarviota (oletuksena lattia vaakasuorassa)
    "floor_material": "",  # floor = material: kansio assets/floors/materials (kivilaatat, kiilto)
    "texture_size": 2.5,  # kuinka monta metriä yksi tekstuurikuva kattaa
    "floor_joints": 0.0,  # betonin sahasaumojen väli metreinä (0 = ei saumoja)
    "tile_size": 0.8,  # laatan koko metreinä
    "floor_brightness": 112,
    "floor_rotation": 0,  # laattojen kiertokulma asteina
    "wall_distance": 6.0,  # seinän etäisyys auton takana metreinä
    "wall_brightness": 16,
    "car_height_m": 1.5,  # oletettu auton korkeus kameran korkeuden arviointiin
    "layout": "center",  # center = yhtenäinen koko ja paikka, original = alkuperäinen rajaus
    "floor_y": 0.88,
    "car_width": 0.84,
    "car_height": 0.58,
    "car_x": 0.5,
    "shadow": 0.55,
    "reflection": 0.0,
    "window_transparency": 1.0,  # kuinka täysin lasin läpi näkyvä vanha tausta vaihdetaan
    "window_tint": 0.0,  # uuden taustan lisätummennus lasin läpi
    "logo": "",
    "logo_x": 0.5,
    "logo_y": 0.12,
    "logo_width": 0.42,
    "logo_opacity": 1.0,
    "logo_on_details": 0,  # 1 = logo myös lähikuviin (auto rajautuu kuvan reunaan)
    "non_car": "original",  # original = sisäkuvat ennallaan, logo = sisäkuviin logo
    "quality": 92,
}


def settings_with_defaults(settings: dict | None) -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update({k: v for k, v in (settings or {}).items() if k in DEFAULT_SETTINGS})
    return s


@lru_cache(maxsize=16)
def _background(path: str, mtime: float, width: int, height: int) -> np.ndarray:
    return np.asarray(cover(load_image(Path(path)), width, height), dtype=np.float32)


@lru_cache(maxsize=16)
def _logo(path: str, mtime: float) -> Image.Image:
    with Image.open(path) as im:
        return im.convert("RGBA")


def _background_path(s: dict, dealer_dir: Path | None) -> Path:
    if s["background"] and dealer_dir and (dealer_dir / s["background"]).exists():
        return dealer_dir / s["background"]
    preset = config.ASSETS_DIR / "backgrounds" / f"{s['background_preset']}.jpg"
    return preset if preset.exists() else config.ASSETS_DIR / "backgrounds" / "showroom-dark.jpg"


def _floor_texture_path(s: dict, dealer_dir: Path | None) -> Path | None:
    name = s["floor_texture"]
    if not name or "/" in name or "\\" in name:
        return None
    for base in (dealer_dir, config.ASSETS_DIR / "floors"):
        if base and (base / name).exists():
            return base / name
    return None


def _estimate_foreground(image: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Blur-fusion -värinpuhdistus (Forte & Pitié 2021): poistaa vanhan taustan värin
    puoliläpinäkyvistä reunapikseleistä. Täysin peittävät pikselit säilyvät ennallaan."""
    img = image / 255.0
    a = alpha[..., None]

    def blur_fusion(F, B, r):
        blurred_a = cv2.blur(alpha, (r, r))[..., None]
        blurred_F = cv2.blur(F * a, (r, r)) / (blurred_a + 1e-5)
        blurred_B = cv2.blur(B * (1 - a), (r, r)) / ((1 - blurred_a) + 1e-5)
        F = blurred_F + a * (img - a * blurred_F - (1 - a) * blurred_B)
        return np.clip(F, 0, 1), blurred_B

    F, blurred_B = blur_fusion(img, img, 91)
    F, _ = blur_fusion(F, blurred_B, 7)
    F = F * 255.0
    solid = alpha >= 0.999
    F[solid] = image[solid]
    return F


def _paste(canvas_shape, layer: np.ndarray, x: int, y: int) -> np.ndarray:
    """Asettaa tason kankaan kokoiseen tyhjään taulukkoon kohtaan (x, y), leikaten reunoilta."""
    H, W = canvas_shape[:2]
    out = np.zeros((H, W) + layer.shape[2:], np.float32)
    h, w = layer.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 < x1 and y0 < y1:
        out[y0:y1, x0:x1] = layer[y0 - y : y1 - y, x0 - x : x1 - x]
    return out


def _resize(arr: np.ndarray, w: int, h: int) -> np.ndarray:
    interp = cv2.INTER_AREA if w < arr.shape[1] else cv2.INTER_LANCZOS4
    return cv2.resize(arr, (w, h), interpolation=interp)


def _shadow(alpha: np.ndarray, strength: float) -> np.ndarray:
    m = (alpha > 0.5).astype(np.uint8)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours or strength <= 0:
        return np.zeros(alpha.shape, np.float32)
    pts = np.vstack([c.reshape(-1, 2) for c in contours])
    car_h = float(pts[:, 1].max() - pts[:, 1].min())
    car_w = float(pts[:, 0].max() - pts[:, 0].min())
    chain = scene.lower_hull(pts)

    def band(up: float, down: float, spread: float, sigma: float) -> np.ndarray:
        lower = chain + [0, down]
        lower[0, 0] -= spread
        lower[-1, 0] += spread
        upper = (chain - [0, up])[::-1]
        poly = np.vstack([lower, upper]).round().astype(np.int32)
        layer = np.zeros(alpha.shape, np.float32)
        cv2.fillPoly(layer, [poly], 1.0)
        return cv2.GaussianBlur(layer, (0, 0), max(1.0, sigma))

    # Kosketusvarjo renkaiden alla + laajempi, pehmeä ympäristövarjo auton alla
    contact = band(car_h * 0.03, car_h * 0.012, 0, car_h * 0.008)
    ambient = band(car_h * 0.12, car_h * 0.045, car_w * 0.02, car_h * 0.04)
    wide = band(car_h * 0.1, car_h * 0.09, car_w * 0.06, car_h * 0.11)
    return np.clip(contact * 0.9 + ambient * 0.6 + wide * 0.35, 0, 0.95) * strength


def render(
    source: Path,
    analysis_dir: Path,
    settings: dict,
    dealer_dir: Path | None,
    kind_override: str = "auto",
    layers: dict | None = None,
) -> Image.Image:
    """layers: jos annettu, täytetään auton kerroksilla (alfa, auto, ikkunat, sijoittelu) jatkokäsittelyä varten."""
    s = settings_with_defaults(settings)
    W, H = int(s["width"]), int(s["height"])
    img = load_image(source)
    alpha = load_mask(analysis_dir / "alpha.png")
    windows = load_mask(analysis_dir / "windows.png")
    plate_path = analysis_dir / "plate.png"
    plate = np.asarray(load_image(plate_path), dtype=np.float32) if plate_path.exists() else None
    meta = json.loads((analysis_dir / "meta.json").read_text(encoding="utf-8"))
    kind = meta["kind"] if kind_override == "auto" else kind_override

    if kind != "car" or alpha is None:
        out = np.asarray(cover(img, W, H), dtype=np.float32)
        if s["non_car"] == "logo":
            out = _apply_logo(out, s, dealer_dir)
        return Image.fromarray(out.round().clip(0, 255).astype(np.uint8))

    image = np.asarray(img, dtype=np.float32)
    if windows is None:
        windows = np.zeros(alpha.shape, np.float32)

    # --- Asettelu ---
    m = alpha > 0.5
    ys, xs = np.where(m)
    bx0, by0, bx1, by1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    touches = any((meta.get("edges") or {}).values())
    if s["layout"] == "original" or touches:
        # Auto on rajattu kuvan reunaan: säilytetään alkuperäinen rajaus
        cx0, cy0, cx1, cy1 = 0, 0, img.width, img.height
        scale = max(W / img.width, H / img.height)
        dest_x = (W - img.width * scale) / 2
        dest_y = (H - img.height * scale) / 2
    else:
        pad = 12
        cx0, cy0 = max(0, bx0 - pad), max(0, by0 - pad)
        cx1, cy1 = min(img.width, bx1 + pad), min(img.height, by1 + pad)
        bw, bh = bx1 - bx0, by1 - by0
        scale = min(s["car_width"] * W / bw, s["car_height"] * H / bh)
        dest_x = s["car_x"] * W - (bx0 + bw / 2 - cx0) * scale
        dest_y = s["floor_y"] * H - (by1 - cy0) * scale

    crop_img = image[cy0:cy1, cx0:cx1]
    crop_a = alpha[cy0:cy1, cx0:cx1]
    crop_w = windows[cy0:cy1, cx0:cx1]
    fg = _estimate_foreground(crop_img, crop_a)

    tw, th = max(1, round((cx1 - cx0) * scale)), max(1, round((cy1 - cy0) * scale))
    if (tw, th) != (cx1 - cx0, cy1 - cy0):
        fg = _resize(fg, tw, th)
        crop_a = np.clip(_resize(crop_a, tw, th), 0, 1)
        crop_w = np.clip(_resize(crop_w, tw, th), 0, 1)
    px, py = round(dest_x), round(dest_y)

    A = _paste((H, W), crop_a, px, py)
    F = _paste((H, W), fg, px, py)
    Wm = _paste((H, W), crop_w, px, py) * A

    # --- Tausta ---
    cam = meta.get("camera")
    studio = s["background_mode"] == "studio3d" and cam is not None
    if studio:
        sx, sy = (cx1 - cx0) / tw, (cy1 - cy0) / th

        def to_orig(u, v):
            # Kankaan pikseli -> alkuperäisen kuvan koordinaatti (skaalaus + siirto),
            # jolloin lattia piirretään täsmälleen alkuperäisen kameran perspektiivissä
            return (u - px) * sx + cx0, (v - py) * sy + cy0

        to_orig.scale = 1 / sx
        canvas = scene.render(W, H, to_orig, cam, meta, s, car_alpha=A, floor_texture=_floor_texture_path(s, dealer_dir), car_rgb=F)
    else:
        bg_path = _background_path(s, dealer_dir)
        canvas = _background(str(bg_path), bg_path.stat().st_mtime, W, H).copy()

    # --- Heijastus lattiaan (valinnainen) ---
    if s["reflection"] > 0 and not touches and s["layout"] != "original":
        floor = py + th - round(pad * scale)
        fade_h = max(1, int(th * 0.35))
        fade = np.clip(1 - np.arange(th) / fade_h, 0, 1)[:, None] * s["reflection"]
        RA = _paste(canvas.shape, crop_a[::-1] * fade, px, floor - round(pad * scale))
        RF = _paste(canvas.shape, fg[::-1], px, floor - round(pad * scale))
        canvas = RF * RA[..., None] + canvas * (1 - RA[..., None])

    # --- Varjo (kiinteällä taustakuvalla; 3D-studio laskee varjon itse lattiatasolla) ---
    if not studio and s["layout"] != "original" and not touches:
        shadow = _shadow(A, float(s["shadow"]))
        canvas = canvas * (1 - shadow[..., None])

    # --- Auto ---
    a3 = A[..., None]
    out = F * a3 + canvas * (1 - a3)

    # --- Ikkunat: vaihdetaan vain lasin läpi näkyvän vanhan taustan osuus ---
    t = B_old = None
    if plate is not None and Wm.max() > 0 and s["window_transparency"] > 0:
        plate_full = cv2.resize(plate, (img.width, img.height), interpolation=cv2.INTER_LINEAR)
        old_bg = plate_full[cy0:cy1, cx0:cx1]
        if (tw, th) != (cx1 - cx0, cy1 - cy0):
            old_bg = _resize(old_bg, tw, th)
        B_old = _paste(canvas.shape, old_bg, px, py)
        t = _see_through(F, B_old) * Wm * A * float(s["window_transparency"])
        B_new = canvas * (1 - float(s["window_tint"]))
        out = out + t[..., None] * (B_new - B_old)

    if layers is not None:
        layers.update(
            alpha=A, car=F, window_t=t, window_old=B_old, window_tint=float(s["window_tint"]),
            transform=(scale, px, py, cx0, cy0), touches=touches,
        )

    # Lähikuvissa (esim. rengas) logo peittäisi auton, joten oletuksena se jätetään pois
    if not touches or str(s["logo_on_details"]) == "1":
        out = _apply_logo(out, s, dealer_dir)
    return Image.fromarray(out.round().clip(0, 255).astype(np.uint8))


def _see_through(F: np.ndarray, B_old: np.ndarray) -> np.ndarray:
    """Arvioi per pikseli, kuinka suuri osa vanhasta taustasta näkyy lasin läpi (0..1).

    Lasi läpäisee osan takana olevasta valosta: pikseli ≈ t · vanha tausta. Kirkkaussuhde
    antaa t:n, ja sävyvertailu varmistaa, että kyse on taustasta eikä heijastuksesta tai
    sisätilasta. Tummat sisätilat, tummennetut lasit ja heijastukset jäävät ennalleen.
    """
    y_f = F.mean(axis=-1)
    y_b = np.maximum(B_old.mean(axis=-1), 12.0)
    t = np.clip(y_f / y_b, 0, 1)
    c_f = F / (F.sum(axis=-1, keepdims=True) + 1e-3)
    c_b = B_old / (B_old.sum(axis=-1, keepdims=True) + 1e-3)
    dist = np.sqrt(((c_f - c_b) ** 2).sum(axis=-1))
    similarity = np.exp(-((dist / 0.05) ** 2))
    return cv2.GaussianBlur((t * similarity).astype(np.float32), (0, 0), 1.2)


def _apply_logo(canvas: np.ndarray, s: dict, dealer_dir: Path | None) -> np.ndarray:
    if not s["logo"] or not dealer_dir or not (dealer_dir / s["logo"]).exists():
        return canvas
    path = dealer_dir / s["logo"]
    logo = _logo(str(path), path.stat().st_mtime)
    H, W = canvas.shape[:2]
    lw = max(1, round(W * float(s["logo_width"])))
    lh = max(1, round(logo.height * lw / logo.width))
    arr = np.asarray(logo.resize((lw, lh), Image.LANCZOS), dtype=np.float32)
    x = round(W * float(s["logo_x"]) - lw / 2)
    y = round(H * float(s["logo_y"]) - lh / 2)
    rgb = _paste(canvas.shape, arr[..., :3], x, y)
    a = _paste(canvas.shape, arr[..., 3] / 255.0 * float(s["logo_opacity"]), x, y)[..., None]
    return rgb * a + canvas * (1 - a)


def to_jpeg(image: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=int(quality), optimize=True, progressive=True, subsampling=0)
    return buf.getvalue()
