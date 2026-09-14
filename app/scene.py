"""3D-studiotausta: lattia ja seinä piirretään alkuperäisen kameran kulmasta.

Kamera (polttoväli, kallistus, kiertymä) arvioidaan analyysissä GeoCalibilla. Kameran
korkeus arvioidaan auton koosta kuvassa. Näin lattian perspektiivi vastaa jokaisen kuvan
kuvakulmaa: edestä saumat kohtaavat kaukana, rengaskuvassa lattia näkyy läheltä.

Lattia on joko proseduraalinen terrazzo-laatta tai valokuvasta tehty saumaton tekstuuri
(esim. betoni). Varjo lasketaan lattiatasolla metreinä, joten se kapenee perspektiivissä
oikein ja on tummin renkaiden kohdalla.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

TEX_SIZE = 1024  # tekstuurin koko pikseleinä (toistuu saumattomasti)
TILES_IN_TEX = 2


def _blur_wrap(img: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussin sumennus toistuvilla reunoilla (OpenCV:n GaussianBlur ei tue BORDER_WRAPia)."""
    pad = int(math.ceil(sigma * 4)) + 1
    padded = np.pad(img, pad, mode="wrap")
    return cv2.GaussianBlur(padded, (0, 0), sigma)[pad:-pad, pad:-pad]


def _pyramid(tex: np.ndarray) -> list[np.ndarray]:
    levels = [tex]
    while levels[-1].shape[0] > 4:
        prev = levels[-1]
        levels.append(cv2.resize(prev, (prev.shape[1] // 2, prev.shape[0] // 2), interpolation=cv2.INTER_AREA))
    return levels


@lru_cache(maxsize=4)
def terrazzo_pyramid(brightness: float, seed: int = 5) -> list[np.ndarray]:
    """Proseduraalinen harmaa terrazzo-laatta saumoineen + mip-tasot (saumaton toisto)."""
    rng = np.random.default_rng(seed)
    n = TEX_SIZE

    def periodic_noise(sigma):
        z = _blur_wrap(rng.normal(0, 1, (n, n)).astype(np.float32), sigma)
        return z / (z.std() + 1e-6)

    tex = np.full((n, n), float(brightness), np.float32)
    tex += periodic_noise(0.7) * 8  # hienojakoinen rakeisuus
    tex += periodic_noise(24) * 4  # pehmeä pilvisyys
    # Kivirouhe: kynnystetty kohina tuottaa epäsäännöllisiä, tiheitä kivenpaloja
    tex -= np.clip(periodic_noise(1.4) - 1.3, 0, None) * 38  # pienet tummat
    tex -= np.clip(periodic_noise(2.6) - 1.9, 0, None) * 45  # isommat tummat, harvassa
    tex += np.clip(periodic_noise(1.2) - 1.6, 0, None) * 32  # vaaleat
    # Laattakohtainen sävyvaihtelu ja saumat
    tile = n // TILES_IN_TEX
    for ty in range(TILES_IN_TEX):
        for tx in range(TILES_IN_TEX):
            tex[ty * tile : (ty + 1) * tile, tx * tile : (tx + 1) * tile] += rng.uniform(-5, 5)
    grout = np.zeros((n, n), np.float32)
    for k in range(TILES_IN_TEX):
        c = k * tile
        cv2.rectangle(grout, (c - 2, 0), (c + 1, n), 1, -1)
        cv2.rectangle(grout, (0, c - 2), (n, c + 1), 1, -1)
    grout[:, n - 2 :] = 1
    grout[n - 2 :, :] = 1
    grout = _blur_wrap(grout, 0.8)
    tex = np.clip(tex * (1 - 0.35 * grout), 0, 255)
    return _pyramid(np.repeat(tex[..., None], 3, axis=2))


@lru_cache(maxsize=8)
def photo_pyramid(path: str, mtime: float) -> tuple[list[np.ndarray], float]:
    """Valokuvatekstuuri (saumaton, ks. textures.py) + mip-tasot ja keskikirkkaus."""
    with Image.open(path) as im:
        tex = np.asarray(im.convert("RGB").resize((TEX_SIZE, TEX_SIZE), Image.LANCZOS), dtype=np.float32)
    return _pyramid(tex), float(tex.mean())


def _bilinear_wrap(tex: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    n = tex.shape[0]
    xf, yf = np.floor(x), np.floor(y)
    ax, ay = (x - xf).astype(np.float32)[:, None], (y - yf).astype(np.float32)[:, None]
    x0 = np.mod(xf, n).astype(np.int32)
    y0 = np.mod(yf, n).astype(np.int32)
    x1, y1 = (x0 + 1) % n, (y0 + 1) % n
    top = tex[y0, x0] * (1 - ax) + tex[y0, x1] * ax
    bottom = tex[y1, x0] * (1 - ax) + tex[y1, x1] * ax
    return top * (1 - ay) + bottom * ay


def _sample_mip(levels, uu, vv, level):
    """Näytteistää toistuvaa RGB-tekstuuria mip-tason mukaan (trilineaarinen suodatus)."""
    out = np.zeros((uu.shape[0], 3), np.float32)
    level = np.clip(level, 0, len(levels) - 1.001)
    lo = np.floor(level).astype(np.int32)
    frac = (level - lo).astype(np.float32)
    for i in np.unique(lo):
        sel = lo == i
        for j, w in ((i, 1 - frac[sel]), (i + 1, frac[sel])):
            if j >= len(levels):
                continue
            n = levels[j].shape[0]
            out[sel] += _bilinear_wrap(levels[j], uu[sel] * n - 0.5, vv[sel] * n - 0.5) * w[:, None]
    return out


def _rays(xo, yo, cam):
    """Kuvapisteet (alkuperäisen kuvan koordinaatit) -> maailman suuntavektorit (y ylös)."""
    f, cx, cy = cam["f"], cam["cx"], cam["cy"]
    pitch, roll = cam["pitch"], cam["roll"]
    dx = (np.asarray(xo, np.float32) - cx) / f
    dy = -(np.asarray(yo, np.float32) - cy) / f  # ylöspäin positiivinen
    cr, sr = math.cos(roll), math.sin(roll)
    rx = cr * dx - sr * dy
    ry0 = sr * dx + cr * dy
    cp, sp = math.cos(pitch), math.sin(pitch)
    ry = cp * ry0 - sp
    rz = sp * ry0 + cp
    return rx, ry, rz


def lower_hull(points: np.ndarray) -> np.ndarray:
    """Siluetin alareunan kupera verho (kulkee renkaiden kosketuskohtien kautta)."""
    pts = sorted(set(map(tuple, points.tolist())))
    hull = []
    for p in pts:
        # Kuvakoordinaateissa y kasvaa alaspäin -> "alempi" verho = suurin y,
        # joten ristitulon etumerkki on käänteinen tavalliseen Andrew'n algoritmiin nähden
        while len(hull) >= 2:
            (x1, y1), (x2, y2) = hull[-2], hull[-1]
            if (x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1) >= 0:
                hull.pop()
            else:
                break
        hull.append(p)
    return np.array(hull, np.float32)


def car_axis_rotation(meta: dict, cam: dict) -> float | None:
    """Auton suunta lattiatasolla pyörien kosketuskohdista, palautetaan laattojen kiertokulmana (astetta).

    Neliölaatoitus toistuu 90 asteen välein, joten riittää löytää yksi pyörien välinen suunta
    (sivu tai akseli). Kolmella pyörällä lävistäjä hylätään: valitaan kaksi keskenään
    kohtisuoraa suuntaa (90° modulossa samaa kulmaa).
    """
    pts = []
    box = meta.get("box")
    for x0, y0, x1, y1 in meta.get("wheels") or []:
        # Vain auton alaosassa olevat pyörät (vanhoissa analyyseissä voi olla mukana esim. ratti)
        if box and y1 < box[3] - 0.45 * (box[3] - box[1]):
            continue
        rx, ry, rz = _rays([(x0 + x1) / 2], [y1], cam)
        if ry[0] < -1e-4:
            t = -1.0 / ry[0]  # kameran korkeus 1: suunta ei riipu mittakaavasta
            pts.append((float(rx[0] * t), float(rz[0] * t)))
    if len(pts) < 2:
        return None
    dirs = []
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            dx, dz = pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]
            if math.hypot(dx, dz) > 0.05:
                a4 = 4 * math.atan2(dz, dx)  # 90° jaksollisuus -> 360° ympyrälle
                dirs.append((math.cos(a4), math.sin(a4)))
    if not dirs:
        return None
    if len(dirs) >= 3:
        best = min(
            ((i, j) for i in range(len(dirs)) for j in range(i + 1, len(dirs))),
            key=lambda p: math.hypot(dirs[p[0]][0] - dirs[p[1]][0], dirs[p[0]][1] - dirs[p[1]][1]),
        )
        dirs = [dirs[best[0]], dirs[best[1]]]
    mx, my = sum(d[0] for d in dirs), sum(d[1] for d in dirs)
    theta = math.atan2(my, mx) / 4
    return -math.degrees(theta)


def camera_height(meta: dict, cam: dict, car_height_m: float) -> float:
    """Arvioi kameran korkeuden auton kuvakoon perusteella (oletettu auton korkeus)."""
    box = meta.get("box")
    edges = meta.get("edges") or {}
    if not box or edges.get("top") or edges.get("bottom"):
        return 0.9  # lähikuva: tyypillisesti matalampi kuvauskorkeus
    x0, y0, x1, y1 = box
    xc = (x0 + x1) / 2
    _, ry_b, rz_b = _rays([xc], [y1], cam)
    _, ry_t, rz_t = _rays([xc], [y0], cam)
    if ry_b[0] >= 0:
        return 1.3
    z1 = -rz_b[0] / ry_b[0]  # kosketuspisteen etäisyys, kun kameran korkeus = 1
    denom = 1 + z1 * ry_t[0] / rz_t[0]
    if denom <= 0.05:
        return 1.3
    return float(np.clip(car_height_m / denom, 0.4, 2.5))


def _contact_shadow(car_alpha, to_orig, cam, h, depth_m=1.7):
    """Varjokartta lattiatasolla: 0..1 metrikoordinaateissa + muunnos rasteriin.

    Auton siluetin alareuna heijastetaan lattialle ja jatketaan kamerasta poispäin auton
    syvyyden verran (jalanjälki). Varjo on tummin jalanjäljen sisällä ja haalenee
    etäisyyden mukaan: terävä kosketusvarjo (~10 cm) + pehmeä ympäristövarjo (~70 cm).
    """
    m = (car_alpha > 0.5).astype(np.uint8)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    pts = np.vstack([c.reshape(-1, 2) for c in contours])
    car_h = float(pts[:, 1].max() - pts[:, 1].min())
    chain = lower_hull(pts)
    # Ketjun päät nousevat puskurien kulmiin: ne eivät ole lattiassa
    chain = chain[chain[:, 1] >= pts[:, 1].max() - 0.45 * car_h]
    if len(chain) < 2:
        return None
    # Tihennetään ketju (2 px välein), jotta jalanjälki seuraa pohjaa tarkasti
    dense = []
    for (x1, y1), (x2, y2) in zip(chain[:-1], chain[1:]):
        k = max(2, int(math.hypot(x2 - x1, y2 - y1) / 2))
        t = np.linspace(0, 1, k, endpoint=False)
        dense.append(np.stack([x1 + (x2 - x1) * t, y1 + (y2 - y1) * t], 1))
    dense.append(chain[-1:])
    dense = np.vstack(dense)
    xo, yo = to_orig(dense[:, 0], dense[:, 1])
    rx, ry, rz = _rays(xo, yo, cam)
    ok = ry < -1e-4
    if ok.sum() < 2:
        return None
    t = -h / ry[ok]
    X, Z = rx[ok] * t, rz[ok] * t
    near = np.stack([X, Z], 1)
    away = near / np.maximum(np.linalg.norm(near, axis=1, keepdims=True), 1e-3)
    far = near + away * depth_m
    poly = np.vstack([near, far[::-1]])

    pad = 3.0
    x_min, z_min = poly.min(axis=0) - pad
    x_max, z_max = poly.max(axis=0) + pad
    res = max(0.015, max(x_max - x_min, z_max - z_min) / 1400)
    gw, gh = int((x_max - x_min) / res) + 1, int((z_max - z_min) / res) + 1
    raster = np.zeros((gh, gw), np.uint8)
    grid = np.stack([(poly[:, 0] - x_min) / res, (poly[:, 1] - z_min) / res], 1)
    cv2.fillPoly(raster, [grid.round().astype(np.int32)], 1)
    dist = cv2.distanceTransform((1 - raster).astype(np.uint8), cv2.DIST_L2, 5) * res
    # Pehmeä ympäristövarjo: tummin auton alla, leviää 20–60 cm ulospäin
    # Hallivalossa varjo on laaja ja pehmeä: tummin auton alla, haalenee noin metrin matkalla
    shade = 0.55 * np.exp(-dist / 0.28) + 0.45 * np.exp(-((dist / 1.1) ** 2))

    # Renkaiden kosketuskohdat = alaverhon kärkipisteet: pieni, selvästi tummempi läiskä
    spots = np.zeros((gh, gw), np.uint8)
    cxo, cyo = to_orig(chain[:, 0], chain[:, 1])
    crx, cry, crz = _rays(cxo, cyo, cam)
    for x, y, z in zip(crx, cry, crz):
        if y < -1e-4:
            gx, gz = (x * -h / y - x_min) / res, (z * -h / y - z_min) / res
            if 0 <= gx < gw and 0 <= gz < gh:
                spots[int(gz), int(gx)] = 1
    if spots.any():
        spot_dist = cv2.distanceTransform((1 - spots).astype(np.uint8), cv2.DIST_L2, 5) * res
        shade = np.maximum(shade, 0.95 * np.exp(-((spot_dist / 0.32) ** 2)))
    return np.clip(shade, 0, 1).astype(np.float32), (x_min, z_min, res)


def _joints(lx, lz, spacing, footprint, width=0.008):
    """Betonilattian sahasaumat (antialiasoitu: kaukana ohuemmat ja haaleammat)."""
    cover = np.zeros(lx.shape, np.float32)
    for c in (lx, lz):
        d = np.abs(np.mod(c / spacing + 0.5, 1.0) - 0.5) * spacing
        cov = np.clip(((width + footprint) / 2 - d) / footprint, 0, 1) * np.minimum(1, width / footprint)
        cover = np.maximum(cover, cov)
    return cover


MATERIALS_DIR = Path(__file__).resolve().parent.parent / "assets" / "floors" / "materials"
CEILING_M = 4.2  # kattovalojen korkeus heijastuksia varten


@lru_cache(maxsize=3)
def load_material(name: str) -> dict | None:
    """Valokuvapohjainen kivimateriaali (väri 2K, karheus ja normaali 1K) + säädöt material.jsonista."""
    if not name or "/" in name or "\\" in name:
        return None
    d = MATERIALS_DIR / name
    if not (d / "material.json").exists():
        return None
    cfg = json.loads((d / "material.json").read_text(encoding="utf-8"))
    with Image.open(d / "color.jpg") as im:
        color = np.asarray(im.convert("RGB").resize((2048, 2048), Image.LANCZOS), dtype=np.float32)
    # Sävytys: harmaaksi, kontrasti ja kirkkaus materiaalin asetusten mukaan
    gray = color.mean(axis=2, keepdims=True)
    color = gray + (color - gray) * cfg.get("saturation", 1.0)
    mean = float(color.mean())
    color = mean + (color - mean) * cfg.get("contrast", 1.0)
    color = np.clip(color * (cfg.get("brightness", 128) / max(float(color.mean()), 1.0)), 0, 255)

    def small(fname):
        p = d / fname
        if not p.exists():
            return None
        with Image.open(p) as im:
            return np.asarray(im.convert("RGB").resize((1024, 1024), Image.LANCZOS), dtype=np.float32) / 255.0

    rough, normal = small("rough.jpg"), small("normal.jpg")
    return {
        "cfg": cfg,
        "color": _pyramid(color.astype(np.float32)),
        "rough": _pyramid(rough) if rough is not None else None,
        "normal": _pyramid(normal * 2 - 1) if normal is not None else None,
    }


def _hash(i, j, seed):
    return np.mod(np.sin(i * 127.1 + j * 311.7 + seed * 74.7) * 43758.5453, 1.0).astype(np.float32)


def _material_albedo(mat, lx, lz, footprint, mip_footprint=None):
    """Laatoitus: jokainen laatta leikataan kivestä eri kohdasta ja käännetään satunnaisesti,
    joten kuvio ei toistu. Saumat ja reunaviiste antialiasoidaan pikselin jalanjäljen mukaan.
    Tekstuurin mip-taso valitaan kapeamman mitan mukaan (mip_footprint), jotta etualan
    kivikuvio pysyy terävänä eikä sumene loivassa kulmassa sivusuunnassa."""
    cfg = mat["cfg"]
    tw, th, tex_m = cfg["tile_w"], cfg["tile_h"], cfg["tex_m"]
    row = np.floor(lz / th)
    shift = np.mod(row, 2) * cfg.get("bond", 0.0) * tw
    col = np.floor((lx + shift) / tw)
    px_ = lx + shift - col * tw
    pz_ = lz - row * th
    ox, oz, rot, tone = (_hash(col, row, k) for k in (1, 2, 3, 4))
    if cfg.get("rotate_tiles", True) and abs(tw - th) < 1e-6:
        k = np.floor(rot * 4)
        cu, cv = px_ - tw / 2, pz_ - th / 2
        u_ = np.where(k == 1, -cv, np.where(k == 2, -cu, np.where(k == 3, cv, cu)))
        v_ = np.where(k == 1, cu, np.where(k == 2, -cv, np.where(k == 3, -cu, cv)))
    else:
        u_, v_ = px_, pz_
    uu, vv = (u_ + ox * tex_m) / tex_m, (v_ + oz * tex_m) / tex_m

    n = mat["color"][0].shape[0]
    level = np.log2(np.maximum((footprint if mip_footprint is None else mip_footprint) * n / tex_m, 1e-6))
    albedo = _sample_mip(mat["color"], uu, vv, level)
    albedo *= (1 + (tone - 0.5) * 2 * cfg.get("tile_variation", 0.03))[:, None]
    rough = _sample_mip(mat["rough"], uu, vv, level - 1)[:, 0] if mat["rough"] else np.full(lx.shape, 0.5, np.float32)
    normal = _sample_mip(mat["normal"], uu, vv, level - 1) if mat["normal"] else None

    edge = np.minimum(np.minimum(px_, tw - px_), np.minimum(pz_, th - pz_))
    # Sauma ei ole piirtoviivan terävä: pehmennetään vähintään 2,5 mm matkalle
    fp = np.maximum(footprint, 0.0025)
    gw = cfg.get("grout_w", 0.003)
    grout = (np.clip((gw / 2 + fp / 2 - edge) / fp, 0, 1) * np.minimum(1, gw / fp)).astype(np.float32)
    bevel = np.exp(-edge / 0.003) * 0.08 * np.minimum(1, 0.003 / fp)
    grout_tone = cfg.get("grout_tone", 0.55) * (0.9 + 0.2 * _hash(col, row, 5))
    albedo = albedo * (1 - grout * (1 - grout_tone))[:, None] * (1 - bevel)[:, None]
    # Laaja sävyvaihtelu (kulumat, epätasainen valo): ilman sitä pinta näyttää liian puhtaalta
    wash = (
        0.5 * np.sin(0.41 * lx + 1.3) * np.sin(0.33 * lz + 0.4)
        + 0.3 * np.sin(1.07 * lx - 0.71 * lz + 2.1)
        + 0.2 * np.sin(2.3 * lx + 1.9 * lz + 0.7)
    )
    albedo = albedo * (1 + cfg.get("wash", 0.07) * wash)[:, None]
    return albedo, rough, normal, grout


def _mirror_car(car_rgb, car_alpha, blur_px):
    """Auton heijastus lattiassa (kuvatasossa): jokainen sarake peilataan renkaiden
    kosketuslinjan suhteen, häivytetään ja sumennetaan."""
    m = (car_alpha > 0.5).astype(np.uint8)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    pts = np.vstack([c.reshape(-1, 2) for c in contours])
    chain = lower_hull(pts)
    if len(chain) < 2:
        return None
    H, W = car_alpha.shape
    car_h = float(pts[:, 1].max() - pts[:, 1].min())
    xs = np.arange(W, dtype=np.float32)
    yg = np.interp(xs, chain[:, 0], chain[:, 1]).astype(np.float32)
    ys = np.arange(H, dtype=np.float32)[:, None]
    below = ys > yg[None, :]
    map_x = np.repeat(xs[None, :], H, axis=0)
    map_y = np.where(below, 2 * yg[None, :] - ys, -10).astype(np.float32)
    rgb = cv2.remap(car_rgb.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    a = cv2.remap(car_alpha.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    a *= np.exp(-np.maximum(ys - yg[None, :], 0) / max(car_h * 0.22, 1))
    if blur_px > 0:
        rgb = cv2.GaussianBlur(rgb * a[..., None], (0, 0), blur_px)
        a = cv2.GaussianBlur(a, (0, 0), blur_px)
        rgb = rgb / np.maximum(a, 1e-3)[..., None]
    return rgb, a


def _material_shade(mat, albedo, rough, normal, grout, light, occl, floor, rays, fx, fz, car_x, car_z, wall_z, s, reflection):
    """Kiillotetun kiven valaistus: diffuusi + Fresnel-heijastus kattovaloista, seinästä ja autosta."""
    cfg = mat["cfg"]
    rx, ry, rz, norm = rays
    dx, dy, dz = rx[floor] / norm[floor], ry[floor] / norm[floor], rz[floor] / norm[floor]
    ns = cfg.get("normal_strength", 0.15)
    if normal is not None:
        nx, nz = normal[:, 0] * ns, normal[:, 1] * ns
    else:
        nx = nz = np.zeros_like(dx)
    inv = 1 / np.sqrt(nx**2 + 1 + nz**2)
    nx, ny, nz = nx * inv, inv, nz * inv
    dot = dx * nx + dy * ny + dz * nz
    r_x, r_y, r_z = dx - 2 * dot * nx, dy - 2 * dot * ny, dz - 2 * dot * nz
    fresnel = 0.04 + 0.96 * (1 - np.clip(-dot, 0, 1)) ** 5
    roughness = np.clip(cfg.get("roughness", 0.2) + (rough - 0.5) * cfg.get("rough_influence", 0.3) + grout * 0.6, 0.02, 1)

    # Heijastussäde osuu kattoon (valopaneelit) tai takaseinään
    t_c = np.where(r_y > 1e-3, CEILING_M / np.maximum(r_y, 1e-3), np.inf)
    t_w = np.where(r_z > 1e-3, (wall_z - fz) / np.maximum(r_z, 1e-3), np.inf)
    hits_wall = t_w < t_c
    t_hit = np.minimum(np.where(np.isfinite(t_c), t_c, 60), 60)
    cx, cz = fx + r_x * t_hit, fz + r_z * t_hit
    blur = 0.04 + roughness * t_hit * 0.25  # sumeus kasvaa karheuden ja etäisyyden myötä
    sx, sz, hx, hz = 3.2, 2.6, 0.9, 0.35
    xc = car_x + np.clip(np.round((cx - car_x) / sx), -2, 2) * sx
    zc = car_z + np.round((cz - car_z) / sz) * sz
    cov = np.clip((hx + blur - np.abs(cx - xc)) / (2 * blur), 0, 1) * np.clip((hz + blur - np.abs(cz - zc)) / (2 * blur), 0, 1)
    ceiling = 10 + cov * cfg.get("light_intensity", 1200) * (0.3 / (0.3 + blur))
    env = np.where(hits_wall, float(s["wall_brightness"]) * 1.2, np.where(np.isfinite(t_c), ceiling, 0.0))
    spec = np.repeat(env[:, None], 3, axis=1)

    k = (fresnel * cfg.get("gloss", 0.8) * (1 - roughness) ** 1.5)[:, None]
    color = albedo * light[:, None] * (1 - k) + spec * k * occl[:, None]

    if reflection is not None:
        # Auton peilikuva kiillotetussa kivessä: näkyy renkaiden edessä ja voimistuu loivassa kulmassa
        rgb, a = reflection
        rk = (a[floor] * cfg.get("car_reflection", 0.0) * (0.08 + 0.5 * fresnel) * (1 - roughness))[:, None]
        color = color * (1 - rk) + rgb[floor] * rk
    return color


def render(
    W: int,
    H: int,
    to_orig,
    cam: dict,
    meta: dict,
    s: dict,
    car_alpha: np.ndarray | None = None,
    floor_texture: Path | None = None,
    car_rgb: np.ndarray | None = None,
) -> np.ndarray:
    """Piirtää studion kankaan kokoisena. to_orig(u, v) -> alkuperäisen kuvan (x, y)."""
    u, v = np.meshgrid(np.arange(W, dtype=np.float32) + 0.5, np.arange(H, dtype=np.float32) + 0.5)
    xo, yo = to_orig(u, v)
    scale = to_orig.scale
    # Kiertymäarvio on autokuvissa epäluotettava ja kallistaisi lattian: oletuksena vaakasuora
    cam = {**cam, "roll": cam.get("roll", 0.0) if str(s["use_roll"]) == "1" else 0.0}
    rx, ry, rz = _rays(xo, yo, cam)
    norm = np.sqrt(rx**2 + ry**2 + rz**2)

    h = camera_height(meta, cam, float(s["car_height_m"]))
    box = meta.get("box") or [0, 0, cam["cx"] * 2, cam["cy"] * 2]
    rxc, ryc, rzc = _rays([(box[0] + box[2]) / 2], [box[3]], cam)
    tc = -h / ryc[0] if ryc[0] < 0 else 6.0
    car_x, car_z = float(rxc[0] * tc), float(rzc[0] * tc)
    wall_z = car_z + float(s["wall_distance"])

    down = ry < -1e-6
    t_floor = np.where(down, -h / np.minimum(ry, -1e-6), np.inf)
    t_wall = np.where(rz > 1e-6, wall_z / np.maximum(rz, 1e-6), np.inf)
    floor = down & (t_floor < t_wall)

    out = np.zeros((H, W, 3), np.float32)

    # --- Lattia ---
    fx, fz = rx[floor] * t_floor[floor], rz[floor] * t_floor[floor]
    rotation = float(s["floor_rotation"])
    if str(s["align_to_car"]) == "1":
        auto = car_axis_rotation(meta, cam)
        if auto is not None:
            rotation = auto
    yaw = math.radians(rotation)
    lx = math.cos(yaw) * (fx - car_x) - math.sin(yaw) * (fz - car_z)
    lz = math.sin(yaw) * (fx - car_x) + math.cos(yaw) * (fz - car_z)
    dist = t_floor[floor] * norm[floor]
    grazing = np.clip(-ry[floor] / norm[floor], 0.05, 1)
    footprint = dist / (cam["f"] * scale) / np.sqrt(grazing)  # metriä per kuvapiste

    guide = s["floor"] == "guide"  # tekoälylle annettava selkeä pohja: tasainen lattia + saumaviivat
    mat = load_material(s["floor_material"]) if s["floor"] == "material" else None
    if mat is not None and s.get("material_overrides"):
        mat = {**mat, "cfg": {**mat["cfg"], **s["material_overrides"]}}
    use_photo = s["floor"] == "texture" and floor_texture is not None and floor_texture.exists()
    if guide:
        albedo = np.full((lx.shape[0], 3), 150.0, np.float32)
        line_m = float(s["guide_line_m"])
        if line_m > 0:
            line_w = np.maximum(line_m, footprint * 1.5)  # vähintään ~1,5 px leveä, jotta viiva erottuu kaukanakin
            # guide_line_tone: 0.2 = tumma viiva, >1 = vaalea saumaviiva
            tone = float(s["guide_line_tone"])
            albedo *= (1 - (1 - tone) * _joints(lx, lz, float(s["tile_size"]), footprint, line_w))[:, None]
    elif mat is not None:
        albedo, rough, normal, grout = _material_albedo(mat, lx, lz, footprint, dist / (cam["f"] * scale))
    elif use_photo:
        levels, tex_mean = photo_pyramid(str(floor_texture), floor_texture.stat().st_mtime)
        tex_m = float(s["texture_size"])
        level_a = np.log2(np.maximum(footprint * TEX_SIZE / tex_m, 1e-6))
        a = _sample_mip(levels, lx / tex_m, lz / tex_m, level_a)
        # Toisen mittakaavan ja kierron näyte + pehmeä sekoitus maailmakoordinaateissa,
        # jotta tekstuurin toisto ei erotu laajalla lattialla
        tex_m2 = tex_m * 1.73
        ca, sa = math.cos(0.61), math.sin(0.61)
        level_b = np.log2(np.maximum(footprint * TEX_SIZE / tex_m2, 1e-6))
        b = _sample_mip(levels, (ca * lx - sa * lz) / tex_m2 + 0.37, (sa * lx + ca * lz) / tex_m2 + 0.11, level_b)
        mix = (0.5 + 0.5 * np.sin(0.83 * lx + 0.4) * np.sin(0.61 * lz + 1.3)).astype(np.float32)[:, None]
        albedo = tex_mean + ((a - tex_mean) * (1 - mix) + (b - tex_mean) * mix) / np.sqrt((1 - mix) ** 2 + mix**2)
        albedo *= float(s["floor_brightness"]) / max(tex_mean, 1.0)
        spacing = float(s["floor_joints"])
        if spacing > 0:
            albedo *= (1 - 0.45 * _joints(lx, lz, spacing, footprint))[:, None]
    else:
        tex_m = float(s["tile_size"]) * TILES_IN_TEX
        level = np.log2(np.maximum(footprint * TEX_SIZE / tex_m, 1e-6))
        albedo = _sample_mip(terrazzo_pyramid(float(s["floor_brightness"])), lx / tex_m, lz / tex_m, level)

    # Valaistus: kirkkain auton ympärillä, himmenee kauas ja seinän viereen
    r2 = (fx - car_x) ** 2 + (fz - car_z) ** 2
    light = 0.55 + 0.5 * np.exp(-r2 / (2 * 5.0**2))
    light /= 1 + 0.012 * dist
    light *= 0.85 + 0.15 * np.clip((wall_z - fz) / 2.0, 0, 1)
    if guide:
        light = np.ones_like(light)

    # Varjo lattiatasolla (varjostaa myös heijastuksia)
    occl = np.ones(light.shape, np.float32)
    strength = float(s["shadow"])
    if strength > 0 and car_alpha is not None:
        res_shadow = _contact_shadow(car_alpha, to_orig, cam, h)
        if res_shadow is not None:
            shade, (x_min, z_min, res) = res_shadow
            gx = ((fx - x_min) / res).astype(np.float32)
            gz = ((fz - z_min) / res).astype(np.float32)
            map_x = np.full((H, W), -10, np.float32)
            map_y = np.full((H, W), -10, np.float32)
            map_x[floor], map_y[floor] = gx, gz
            sampled = cv2.remap(shade, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            occl = 1 - np.clip(sampled[floor], 0, 1) * strength
            light *= occl

    if mat is not None:
        reflection = None
        if car_rgb is not None and car_alpha is not None and mat["cfg"].get("car_reflection", 0) > 0:
            reflection = _mirror_car(car_rgb, car_alpha, mat["cfg"].get("reflection_blur_px", 3))
        out[floor] = _material_shade(
            mat, albedo, rough, normal, grout, light, occl, floor, (rx, ry, rz, norm),
            fx, fz, car_x, car_z, wall_z, s, reflection,
        )
    else:
        out[floor] = albedo * light[:, None]

    # --- Seinä ---
    wall = ~floor
    wy = np.where(np.isfinite(t_wall), h + ry * t_wall, 10.0)[wall]
    wx = np.where(np.isfinite(t_wall), rx * t_wall, 0.0)[wall]
    base = float(s["wall_brightness"])
    glow = np.exp(-np.maximum(wy, 0) / 1.4) * np.exp(-((wx - car_x) / 7) ** 2)
    out[wall] = (base * (0.8 + 0.6 * glow))[:, None]

    # Valokuvamaisuus: kauempana lattia pehmenee (syväterävyys), ja kohina vastaa kameran kohinaa
    out = cv2.GaussianBlur(out, (0, 0), 0.6)
    if guide:
        return np.clip(out, 0, 255)
    dist_map = np.zeros((H, W), np.float32)
    dist_map[floor] = dist
    car_dist = math.sqrt(car_x**2 + car_z**2 + h**2)
    far = np.clip((dist_map - car_dist) / 6.0, 0, 1)[..., None] * floor[..., None]
    out = out * (1 - far) + cv2.GaussianBlur(out, (0, 0), 1.6) * far
    rng = np.random.default_rng(1)
    out += rng.normal(0, 2.2, out.shape[:2]).astype(np.float32)[..., None]
    return np.clip(out, 0, 255)
