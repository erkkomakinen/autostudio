"""Analyysivaihe (GPU): ajetaan KERRAN per kuva.

Tallentaa auton alfamaskin, ikkunamaskin ja metatiedot. Taustan/logon vaihto ja
asettelun säätö tehdään näiden pohjalta ilman tekoälyä (compose.py), joten
liikkeen asetusten muuttaminen ei maksa GPU-aikaa.
"""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import models
from .imageutil import load_image


def _clean_alpha(alpha: np.ndarray) -> np.ndarray:
    """Poistaa irralliset pienet saarekkeet (esim. heijastukset lattiassa, vesileimat)."""
    m = (alpha > 0.5).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 2:
        return alpha
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = np.zeros(n, bool)
    keep[1:] = areas >= 0.03 * areas.max()
    keep_mask = keep[labels].astype(np.uint8)
    # Pehmeät reunat säilyvät: laajennetaan säilytettävää aluetta hieman ennen leikkausta
    keep_mask = cv2.dilate(keep_mask, np.ones((7, 7), np.uint8))
    return alpha * keep_mask


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.uint8)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.zeros_like(m)
    cv2.drawContours(out, contours, -1, 1, thickness=cv2.FILLED)
    return out


def _window_mask(window_masks, found, alpha) -> np.ndarray:
    h, w = alpha.shape
    union = np.zeros((h, w), np.uint8)
    for m, (box, _score) in zip(window_masks, found):
        # Rajataan ikkunan tunnistuslaatikkoon, ettei maski valu oven peltiin
        x0, y0, x1, y1 = box
        px, py = (x1 - x0) * 0.04, (y1 - y0) * 0.04
        clip = np.zeros((h, w), np.uint8)
        clip[int(max(0, y0 - py)) : int(min(h, y1 + py)), int(max(0, x0 - px)) : int(min(w, x1 + px))] = 1
        mm = _fill_holes(m) & clip
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mm, connectivity=8)
        if n <= 1:
            continue
        largest = (labels == 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])).astype(np.uint8)
        hull = cv2.convexHull(cv2.findNonZero(largest))
        hull_mask = np.zeros((h, w), np.uint8)
        cv2.fillConvexPoly(hull_mask, hull, 1)
        # Lasit ovat lähes kuperia: täytetään puuttuvat kohdat, jos maski on jo melkein kupera
        if hull_mask.sum() <= 1.35 * largest.sum():
            largest = hull_mask
        union |= largest
    union &= (alpha > 0.5).astype(np.uint8)
    # Kavennetaan hieman, jotta ikkunan tiivisteet ja karmit jäävät koskemattomiksi
    union = cv2.erode(union, np.ones((5, 5), np.uint8))
    soft = cv2.GaussianBlur(union.astype(np.float32), (0, 0), 1.5)
    return soft


def _background_plate(image: np.ndarray, alpha: np.ndarray, scale: int = 4) -> np.ndarray:
    """Arvio alkuperäisestä taustasta auton takana (1/4-resoluutio).

    Jokainen rivi interpoloidaan auton vasemmalta ja oikealta puolelta. Studiossa tausta on
    tyypillisesti vaakasuunnassa tasainen (seinä, lattia), joten arvio on hyvä juuri
    ikkunoiden kohdalla. Käytetään kokoamisessa tunnistamaan lasin läpi näkyvä tausta.
    """
    h, w = alpha.shape
    sw, sh = max(1, w // scale), max(1, h // scale)
    small = cv2.resize(image, (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32)
    car = cv2.resize((alpha > 0.05).astype(np.uint8), (sw, sh), interpolation=cv2.INTER_NEAREST)
    car = cv2.dilate(car, np.ones((5, 5), np.uint8)).astype(bool)
    plate = small.copy()
    xs = np.arange(sw)
    for y in range(sh):
        known = ~car[y]
        if known.all():
            continue
        if known.sum() < 2:
            continue  # rivi kokonaan auton peitossa, täytetään pystysuunnassa alla
        for c in range(3):
            plate[y, ~known, c] = np.interp(xs[~known], xs[known], small[y, known, c])
    # Täysin peitetyt rivit: kopioidaan lähin kelvollinen rivi
    valid = (~car).sum(axis=1) >= 2
    if valid.any() and not valid.all():
        idx = np.where(valid)[0]
        for y in np.where(~valid)[0]:
            plate[y] = plate[idx[np.abs(idx - y).argmin()]]
    return cv2.GaussianBlur(plate, (0, 0), 2).clip(0, 255).astype(np.uint8)


def analyze(src: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    img = load_image(src)
    h, w = img.height, img.width

    alpha = _clean_alpha(models.segmenter().alpha(img))
    m = alpha > 0.5
    meta = {"width": w, "height": h, "kind": "other", "windows": 0}

    if m.sum() > 0.005 * h * w:
        ys, xs = np.where(m)
        box = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
        edges = {
            "left": box[0] <= 2,
            "top": box[1] <= 2,
            "right": box[2] >= w - 3,
            "bottom": box[3] >= h - 3,
        }
        area = float(m.mean())
        meta.update(box=box, edges=edges, area=round(area, 4))

        detector = models.window_detector()
        win_masks, found = detector.masks(img, box)
        meta["windows"] = len(found)
        # Pyörät: niiden kosketuskohdista lasketaan auton suunta lattialla (laattojen suuntaus)
        meta["wheels"] = detector.wheels(img, box)

        n_edges = sum(edges.values())
        if found and 0.06 <= area <= 0.85 and n_edges <= 2:
            meta["kind"] = "car"
        elif not found and n_edges == 0 and 0.08 <= area <= 0.75:
            # Ei ikkunoita (esim. ikkunaton pakettiauto): varmistetaan autotunnistimella
            score = detector.car_score(img, box)
            meta["car_score"] = round(score, 3)
            if score >= 0.35:
                meta["kind"] = "car"

        if meta["kind"] == "car":
            windows = _window_mask(win_masks, found, alpha)
            Image.fromarray((windows * 255).round().astype(np.uint8)).save(out_dir / "windows.png")
            plate = _background_plate(np.asarray(img), alpha)
            Image.fromarray(plate).save(out_dir / "plate.png")

        # Kamera lasketaan kaikille kuville, joissa on kohde: myös lähikuvat (esim. rengas),
        # jotka käyttäjä voi pakottaa 3D-studioon
        try:
            meta["camera"] = models.calibrator().estimate(img)
        except Exception as e:  # noqa: BLE001 - 3D-studio ei ole pakollinen, kuvatausta toimii ilman
            meta["camera_error"] = str(e)

    Image.fromarray((alpha * 255).round().astype(np.uint8)).save(out_dir / "alpha.png")
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta
