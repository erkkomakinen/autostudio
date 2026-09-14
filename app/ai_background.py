"""Tekoälytausta liikekohtaisella tyyliprofiililla (OpenRouter, oletuksena Seedream 5 Pro).

Kulku:
1. Ohjauspohja lasketaan: auto, lattian saumat auton suuntaan ohuina vaaleina viivoina, tumma seinä.
2. Kuvamalli tekee pinnoista valokuvamaiset liikkeen materiaalinäytteiden mukaan.
3. Viimeistely laskennalla, koska tekoäly ei noudata ohjeita aina:
   - pyörät, joita alkuperäisessä kuvassa ei ole, poistetaan (autoon ei keksitä mitään)
   - tekoälyn varjo sallitaan vain auton välittömässä läheisyydessä
   - seinän ja lattian kirkkaus liikkeen tasoon
   - alkuperäinen auto, ikkunoiden taustan vaihto ja logo

Tyyliprofiili: assets/styles/<liike>/style.json + materiaalinäytteet + logo.
Uusi autoliike = uusi kansio, koodiin ei tarvitse koskea.
"""
import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import compose, config

STYLES_DIR = config.ASSETS_DIR / "styles"
API = "https://openrouter.ai/api/v1/images"
ASPECTS = {"1:1": 1.0, "5:4": 1.25, "4:3": 4 / 3, "3:2": 1.5, "16:9": 16 / 9, "4:5": 0.8, "3:4": 0.75, "2:3": 2 / 3}


def load_style(name: str) -> dict:
    d = STYLES_DIR / name
    style = json.loads((d / "style.json").read_text(encoding="utf-8"))
    style["dir"] = d
    return style


def prompt_for(style: dict) -> str:
    cm = round(float(style["tile_size_m"]) * 100)
    return (
        "Image 1 is a LAYOUT GUIDE for a car dealership studio photo: the vehicle, a plain grey floor with thin light "
        f"lines and a plain dark wall. The thin light lines are the exact grout lines of {cm} x {cm} cm floor tiles. "
        "Image 2 is a close-up sample of the floor material. Image 3 is a sample of the wall surface. "
        "Produce a photorealistic photograph from image 1. "
        "THE VEHICLE IS UNTOUCHABLE: keep it exactly as in image 1, same position, size, shape and details. Never add, "
        "remove, invent or complete anything on, around or under the vehicle: no wheels, tires, parts or objects that "
        "are not visible in image 1, not even parts that would normally be hidden or cut off. "
        "The tile grout lines must follow the thin light lines of image 1 exactly: same positions, same directions, "
        "same perspective, no other tile lines anywhere. Keep the line where the floor meets the wall exactly where it is. "
        "THE FLOOR ENDS AT THE FLOOR AND THE WALL IS ONLY WALL: the floor stops exactly at that line and never continues "
        "up the wall. No skirting board, no baseboard, no tile strip, no border, no ledge and no floor material of any "
        "kind on the wall; the wall surface goes straight down to the floor. "
        f"Floor: tiles made of the material in image 2, {style['floor_description']}, {style['grout_description']}. "
        f"Wall: like image 3, {style['wall_description']}. "
        "Lighting: soft, even studio lighting. SHADOWS ONLY AT THE VEHICLE: the only shadow is a small soft contact "
        "shadow directly under the vehicle body and where the tires touch the floor, reaching only a few centimetres "
        "beyond the vehicle outline. No other shadows anywhere on the floor or the wall, no large dark areas, no light "
        "streaks along the wall. No skirting board, no text, no logos, no people."
    )


def prompt_for_base_with_samples(style: dict) -> str:
    """Pohjakuvatila + viimeistelynäytteet: rakenne lukitaan pohjakuvasta, pinnan aitous tulee näytteistä.
    Kaikki mallit saavat samat näytteet, joten ne päätyvät samannäköiseen lopputulokseen."""
    return (
        "Image 1 is a nearly finished photo of a car in a car dealership studio. Image 2 is a real photographed "
        "close-up of the polished granite floor surface as it must look in the final photo. Image 3 is the wall "
        "surface as it must look in the final photo. Turn image 1 into a real, high-end photograph. "
        "LOCKED, do not change: the camera framing and zoom; the car, its position, size, shape and every detail; the "
        "position, direction and perspective of every grout line and the tile size; the grey tone and brightness of "
        "the floor; the line where the floor meets the wall; the soft shadow and the faint reflection under the car. "
        "Do not zoom, crop or move anything. "
        "RE-RENDER the surfaces so they look real: the floor tiles must look exactly like the real stone in image 2 "
        "(same grain size, same speckle, same subtle polished sheen), not like a computer texture, with thin light "
        "grout lines exactly where they are in image 1. The wall must look exactly like image 3: one continuous plain "
        "neutral dark charcoal surface with a very subtle soft gradient, neutral grey with no blue or other colour "
        "tint, no corners, no vertical lines, no seams, no panels, and nothing on it, going straight down to the floor "
        "line. No skirting board, strip, border or ledge on the wall. Do not add any shadows, reflections, outlines, "
        "lines or dark areas on the floor. No objects, no second car, no text, no logos, no people."
    )


def prompt_for_base(style: dict) -> str:
    """Pohjakuvatila: kuva on jo lähes valmis, tekoäly tekee vain valokuvamaisen viimeistelyn."""
    return (
        "This image is a nearly finished photo of a car in a car dealership studio. Turn it into a real, high-end "
        "photograph with minimal changes. The composition is final: keep the camera framing and zoom, and keep the car, "
        "its position, size, shape and every detail exactly as they are. Do not zoom out, do not crop, do not move "
        "anything. Keep the floor exactly as it is: the same stone tiles, the same colour and speckle, every grout line "
        "in the same place and direction. Keep the wall exactly as it is: a plain dark wall that goes straight down to "
        "the floor line, with nothing on it. The shadow and the faint reflection under the car are already correct: "
        "keep them exactly as they are. Do not add any other shadows, do not darken the floor anywhere, and do not "
        "draw any new reflections, outlines, lines or shapes on the floor. Only add photographic realism to the "
        "existing surfaces: a subtle even polished sheen on the stone, natural light falloff and fine photographic "
        "grain. Do not add anything: no skirting board, strip, border or ledge on the wall, no objects, no second car, "
        "no text, no logos, no people."
    )


def guide_settings(style: dict, car_height_m: float) -> dict:
    W, H = style["canvas"]
    if style.get("guide_mode") == "base":
        return {
            "width": W, "height": H, "layout": "center",
            "car_width": style["car_width"], "car_height": style["car_height"], "floor_y": style["floor_y"],
            "background_mode": "studio3d", "floor": "material", "floor_material": style["base_material"],
            "shadow": float(style.get("base_shadow", 0.5)), "car_height_m": car_height_m,
            "wall_distance": 6.0, "wall_brightness": float(style.get("base_wall_brightness", 24)),
            "align_to_car": 1, "use_roll": 0, "logo": "", "window_transparency": 1.0, "window_tint": 0.0,
        }
    return {
        "width": W, "height": H, "layout": "center",
        "car_width": style["car_width"], "car_height": style["car_height"], "floor_y": style["floor_y"],
        "background_mode": "studio3d", "floor": "guide", "tile_size": style["tile_size_m"],
        "guide_line_m": 0.005, "guide_line_tone": 1.45, "shadow": 0.0, "car_height_m": car_height_m,
        "wall_distance": 6.0, "wall_brightness": 22, "align_to_car": 1, "use_roll": 0,
        "logo": "", "window_transparency": 1.0, "window_tint": 0.0,
    }


def build_guide(source: Path, analysis_dir: Path, style: dict, car_height_m: float = 1.5):
    layers: dict = {}
    guide = compose.render(source, analysis_dir, guide_settings(style, car_height_m), None, "car", layers=layers)
    return guide, layers


def _data_url(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def request_background(guide: Image.Image, style: dict, timeout: int = 400, extra: str | None = None):
    """extra: asiakkaan korjausohje, lisätään ohjeen loppuun (ei koskaan saa muuttaa autoa)."""
    W, H = guide.size
    aspect = min(ASPECTS, key=lambda k: abs(ASPECTS[k] - W / H))
    if style.get("guide_mode") == "base" and style.get("finish_floor_sample") and style.get("use_finish_samples", True):
        refs = [guide, Image.open(style["dir"] / style["finish_floor_sample"]),
                Image.open(style["dir"] / style["finish_wall_sample"])]
        prompt = prompt_for_base_with_samples(style)
    elif style.get("guide_mode") == "base":
        refs, prompt = [guide], prompt_for_base(style)
    else:
        refs = [guide, Image.open(style["dir"] / style["floor_sample"]), Image.open(style["dir"] / style["wall_sample"])]
        prompt = prompt_for(style)
    if extra:
        prompt += (
            " Additional correction requested by the customer (it may be in Finnish). Apply it only to the floor, "
            f"lighting, shadow or reflections, never to the car: {extra[:500]}"
        )
    payload = {
        "model": style["model"], "prompt": prompt, "aspect_ratio": aspect,
        "input_references": [{"type": "image_url", "image_url": {"url": _data_url(im)}} for im in refs],
    }
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "Content-Type": "application/json",
                 "X-Title": "Autostudio"},
    )
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Kuvamalli palautti virheen {e.code}: {e.read().decode()[:300]}")
    ai = Image.open(io.BytesIO(base64.b64decode(data["data"][0]["b64_json"]))).convert("RGB")
    info = {"model": style["model"], "seconds": round(time.time() - t, 1), "cost": (data.get("usage") or {}).get("cost")}
    return ai.resize((W, H), Image.LANCZOS), info


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _remove_invented_wheels(ai: np.ndarray, A: np.ndarray, layers: dict, meta: dict):
    """Pyöräntunnistin tekoälyn kuvaan: pyörä, jota ei ole alkuperäisessä kuvassa ja joka on auton
    ulkopuolella, on keksitty -> alue korvataan ympäröivällä lattialla."""
    from . import models

    H, W = A.shape
    ys, xs = np.where(A > 0.5)
    if len(xs) == 0:
        return ai, 0
    car_h = ys.max() - ys.min()
    search = [float(xs.min()), float(ys.min()), float(xs.max()), float(min(H - 1, ys.max() + 0.25 * car_h))]
    scale, px, py, cx0, cy0 = layers["transform"]
    known = [[(x0 - cx0) * scale + px, (y0 - cy0) * scale + py, (x1 - cx0) * scale + px, (y1 - cy0) * scale + py]
             for x0, y0, x1, y1 in meta.get("wheels") or []]
    found = models.window_detector().wheels(Image.fromarray(ai.clip(0, 255).astype(np.uint8)), search)
    mask = np.zeros((H, W), np.uint8)
    removed = 0
    for b in found:
        if any(_iou(b, k) > 0.25 for k in known):
            continue
        x0, y0, x1, y1 = (int(round(v)) for v in b)
        region = A[y0:y1, x0:x1]
        if region.size == 0 or (region < 0.5).mean() < 0.25:
            continue  # pääosin auton sisällä: alkuperäinen auto peittää sen joka tapauksessa
        m = np.zeros((H, W), np.uint8)
        m[y0:y1, x0:x1] = 1
        mask |= m & (A < 0.5).astype(np.uint8)
        removed += 1
    if removed:
        mask = cv2.dilate(mask, np.ones((9, 9), np.uint8))
        ai = cv2.inpaint(ai.clip(0, 255).astype(np.uint8), mask * 255, 9, cv2.INPAINT_TELEA).astype(np.float32)
    return ai, removed


def _limit_shadow(ai: np.ndarray, A: np.ndarray, floor_mask: np.ndarray, reach_ratio: float) -> np.ndarray:
    """Tummennus sallitaan vain auton alaosan läheisyydessä. Muualla lattian laaja-alainen
    tummuminen nostetaan rivin tyypilliseen kirkkauteen (pintakuvio ja kiilto säilyvät)."""
    H, W = A.shape
    car = (A > 0.5).astype(np.uint8)
    ys, xs = np.where(car > 0)
    if len(xs) == 0 or floor_mask.sum() < 1000:
        return ai
    car_h = int(ys.max() - ys.min())
    lower = car.copy()
    lower[: ys.min() + car_h // 2] = 0
    reach = max(8.0, reach_ratio * car_h)
    dist = cv2.distanceTransform(1 - lower, cv2.DIST_L2, 5)
    keep = np.clip(1 - (dist - reach) / reach, 0, 1)

    lum = ai.mean(axis=2)
    fm = floor_mask.astype(np.float32)
    sigma = max(8.0, H / 60)
    L = cv2.GaussianBlur(lum * fm, (0, 0), sigma) / np.maximum(cv2.GaussianBlur(fm, (0, 0), sigma), 1e-3)
    free = floor_mask & (keep < 0.01)
    ref = np.full(H, np.nan, np.float32)
    for y in range(H):
        vals = L[y][free[y]]
        if vals.size > 30:
            ref[y] = np.percentile(vals, 75)
    ok = ~np.isnan(ref)
    if ok.sum() < 10:
        return ai
    ref = np.interp(np.arange(H), np.where(ok)[0], ref[ok]).astype(np.float32)
    gain = np.clip(ref[:, None] * 0.92 / np.maximum(L, 1.0), 1.0, 2.2)
    gain = 1 + (gain - 1) * (1 - keep) * cv2.GaussianBlur(fm, (0, 0), 2)
    return ai * gain[..., None]


def _clean_wall_base(ai: np.ndarray, guide_lum: np.ndarray, A: np.ndarray, band_ratio: float = 0.05) -> np.ndarray:
    """Seinä on vain seinää: seinän alaosan kaista (lattiarajan yläpuolella) korvataan seinällä
    kaistan yläpuolelta peilattuna. Tekoälyn mahdollisesti piirtämä laattalista tai jalkalista häviää,
    ja seinän oma pinta ja liukuväri jatkuvat luontevasti lattiaan asti."""
    H, W = guide_lum.shape
    band = max(6, int(H * band_ratio))
    is_floor = guide_lum > 90

    # Lattiaraja on seinällä suora viiva -> sovitetaan suora autottomista sarakkeista
    # (auton peittämissä sarakkeissa ensimmäinen lattiarivi olisi auton alareuna, ei seinän raja)
    cols = [c for c in np.where(A.max(axis=0) < 0.02)[0] if is_floor[:, c].any()]
    if len(cols) < 10:
        return ai
    ys_line = np.array([is_floor[:, c].argmax() for c in cols], np.float32)
    slope, intercept = np.polyfit(np.array(cols, np.float32), ys_line, 1)
    line = np.round(slope * np.arange(W) + intercept).astype(np.int32)

    # Peilauslähteestä poistetaan auto täyttämällä se ympäröivällä seinällä,
    # jotta kaistaan ei peilaudu auton osia. Alkuperäinen auto liimataan lopuksi päälle.
    y_lo = int(max(0, line.min() - 2 * band - 4))
    y_hi = int(min(H, line.max() + 4))
    car = cv2.dilate((A > 0.01).astype(np.uint8), np.ones((7, 7), np.uint8))
    mask = np.zeros((H, W), np.uint8)
    mask[y_lo:y_hi] = car[y_lo:y_hi]
    src = ai.clip(0, 255).astype(np.uint8)
    if mask.any():
        src = cv2.inpaint(src, mask * 255, 5, cv2.INPAINT_TELEA)
    src = src.astype(np.float32)

    ys = np.arange(H)[:, None]
    top = line[None, :] - band
    in_band = (ys >= top) & (ys < line[None, :] + 2)
    # Peilaus kaistan yläreunan suhteen: rivi top+k <- rivi top-1-k
    src_y = np.clip(2 * top - 1 - ys, 0, H - 1)
    mirrored = src[src_y, np.arange(W)[None, :]]
    # Pehmeä reuna kaistan yläosaan, lattiarajalla jyrkkä (lattia alkaa suoraan)
    fade = np.clip((ys - top) / 4.0, 0, 1)
    w = (in_band * fade).astype(np.float32)[..., None]
    return ai * (1 - w) + mirrored * w


def _match_brightness(ai, floor_mask, wall_mask, style):
    lum = ai.mean(axis=2)
    gain = np.ones(lum.shape, np.float32)
    for mask, key, lo, hi in ((wall_mask, "wall_brightness", 0.4, 2.0), (floor_mask, "floor_brightness", 0.6, 1.6)):
        if mask.sum() > 1000 and key in style:
            k = float(np.clip(float(style[key]) / max(float(lum[mask].mean()), 1.0), lo, hi))
            gain += cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 2) * (k - 1)
    return ai * gain[..., None]


def _apply_logo(img: Image.Image, style: dict) -> Image.Image:
    if not style.get("logo"):
        return img
    W, H = img.size
    logo = Image.open(style["dir"] / style["logo"]).convert("RGBA")
    lw = round(W * float(style["logo_width"]))
    logo = logo.resize((lw, round(logo.height * lw / logo.width)), Image.LANCZOS)
    base = img.convert("RGBA")
    base.alpha_composite(logo, (round((W - lw) / 2), round(H * float(style["logo_y"]) - logo.height / 2)))
    return base.convert("RGB")


def wall_layer(source: Path, analysis_dir: Path, base: Image.Image, style: dict, car_height_m: float = 1.5):
    """Seinä pohjakuvasta: pehmeä seinämaski (3D-geometriasta) ja seinän kuva, jossa hienovarainen
    pintakuvio seinänäytteestä. Tekoäly ei koskaan määrää seinän sävyä."""
    lines, layers = build_guide(source, analysis_dir, {**style, "guide_mode": "lines"}, car_height_m)
    g = np.asarray(lines, np.float32).mean(axis=2)
    A = layers["alpha"]
    hard = ((g < 70) & (A < 0.5)).astype(np.float32)
    mask = cv2.GaussianBlur(hard, (0, 0), 1.5)
    return render_wall(g, A, style), mask


def render_wall(guide_lum: np.ndarray, A: np.ndarray, style: dict) -> np.ndarray:
    """Seinän kuva liikkeen asetuksilla (style.json):
    wall_brightness  seinän perussävy (0-255)
    wall_glow        valokeila auton takana (0 = ei, 1 = seinä kaksinkertaisesti kirkas keilan keskellä)
    wall_uplight     vaalennus lattiarajaa kohti
    wall_vignette    tummuus yläkulmiin (0-1)
    wall_texture     maalipinnan kuvion voimakkuus (0 = tasainen)
    wall_tone        RGB-kerroin, esim. [1, 1, 1.02] hyvin viileä sävy
    """
    H, W = guide_lum.shape
    base = float(style.get("wall_brightness", 24))
    glow = float(style.get("wall_glow", 0.6))
    uplight = float(style.get("wall_uplight", 0.35))
    vignette = float(style.get("wall_vignette", 0.35))
    texture = float(style.get("wall_texture", 0.08))

    # Lattiaraja jokaiselle sarakkeelle: sovitetaan suora autottomista sarakkeista
    is_floor = guide_lum > 90
    cols = [c for c in range(W) if A[:, c].max() < 0.02 and is_floor[:, c].any()]
    if len(cols) >= 10:
        k, b = np.polyfit(np.array(cols, np.float32), np.array([is_floor[:, c].argmax() for c in cols], np.float32), 1)
        line = k * np.arange(W) + b
    else:
        line = np.full(W, H * 0.6)
    ys, xs = np.where(A > 0.5)
    car_cx = float(xs.mean()) if len(xs) else W / 2

    y = np.arange(H, dtype=np.float32)[:, None]
    x = np.arange(W, dtype=np.float32)[None, :]
    height = np.clip((line[None, :] - y) / max(float(line.mean()), 1.0), 0, 1)  # 0 lattiarajalla, 1 kuvan yläreunassa
    beam = np.exp(-((x - car_cx) / (0.42 * W)) ** 2) * np.exp(-(height / 0.75) ** 2)
    up = np.exp(-height / 0.18)
    corner = (np.abs(x - W / 2) / (W / 2)) ** 2 * height
    light = (1 + glow * beam + uplight * up) * (1 - vignette * np.clip(corner, 0, 1))
    wall = base * light

    tex_name = style.get("wall_texture_sample")
    if texture > 0 and tex_name and (style["dir"] / tex_name).exists():
        s = np.asarray(Image.open(style["dir"] / tex_name).convert("L").resize((W, H), Image.LANCZOS), np.float32)
        detail = s / np.maximum(cv2.GaussianBlur(s, (0, 0), 25), 1.0) - 1
        detail /= max(float(detail.std()), 1e-3)
        wall = wall * (1 + texture * np.clip(detail, -3, 3) / 3)
    wall = wall + np.random.default_rng(7).normal(0, 1.1, (H, W)).astype(np.float32)
    tone = np.array(style.get("wall_tone", [1.0, 1.0, 1.0]), np.float32)
    return np.clip(wall[..., None] * tone, 0, 255)


def finish(ai_img: Image.Image, guide: Image.Image, layers: dict, meta: dict, style: dict, corrections: bool = True,
           wall: tuple | None = None):
    """corrections=False: vain alkuperäinen auto, ikkunat ja logo (tekoälyn oma tulos näkyy sellaisenaan).
    wall=(seinäkuva, maski): seinä tulee pohjakuvasta, tekoälyn kuvasta käytetään vain lattia."""
    A = layers["alpha"]
    F = layers["car"]
    ai = np.asarray(ai_img, np.float32).copy()
    if wall is not None:
        wall_img, wall_mask = wall
        ai = ai * (1 - wall_mask[..., None]) + wall_img * wall_mask[..., None]
    removed = 0
    if corrections:
        g = np.asarray(guide, np.float32).mean(axis=2)
        outside = A < 0.02
        floor_mask = (g > 90) & outside
        wall_mask = (g < 70) & outside
        ai, removed = _remove_invented_wheels(ai, A, layers, meta)
        ai = _clean_wall_base(ai, g, A)
        ai = _limit_shadow(ai, A, floor_mask, float(style.get("shadow_reach", 0.1)))
        ai = _match_brightness(ai, floor_mask, wall_mask, style)

    out = F * A[..., None] + ai * (1 - A[..., None])
    if layers.get("window_t") is not None:
        out = out + layers["window_t"][..., None] * (ai * (1 - layers["window_tint"]) - layers["window_old"])
    img = _apply_logo(Image.fromarray(out.clip(0, 255).astype(np.uint8)), style)
    return img, {"removed_wheels": removed}


def generate(source: Path, analysis_dir: Path, style_name: str, car_height_m: float = 1.5, out_dir: Path | None = None):
    style = load_style(style_name)
    meta = json.loads((analysis_dir / "meta.json").read_text(encoding="utf-8"))
    guide, layers = build_guide(source, analysis_dir, style, car_height_m)
    ai, info = request_background(guide, style)
    img, report = finish(ai, guide, layers, meta, style)
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        guide.save(out_dir / "ohjauspohja.jpg", quality=90)
        ai.save(out_dir / "tekoaly-raaka.jpg", quality=90)
        img.save(out_dir / "valmis.jpg", quality=92)
    return img, {**info, **report}
