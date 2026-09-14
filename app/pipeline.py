"""Yhden kuvan käsittely alusta loppuun.

1. save_upload  kuva talteen (EXIF-kääntö, HEIC, pienennys)
2. analyze      auto, ikkunat, pyörät, kamera (GPU, kerran per kuva)
3. generate     tekoälytausta liikkeen tyyliprofiililla (maksullinen kutsu) + viimeistely
4. refinish     viimeistely uudelleen tallennetusta raakakuvasta (ilmainen: logo, seinä, tyylin muutokset)

Jokainen tulos tallennetaan uutena versiona, edellinen jää talteen (<nimi>.v<n>.jpg).
Kulut tallennetaan euroina kutsuhetken kurssilla (settings.usd_eur).
"""
import io
import json
import logging
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

from . import ai_background as aib
from . import analysis, compose, notify, settings, storage
from .imageutil import load_image

log = logging.getLogger("autostudio.pipeline")
class AiFailed(Exception):
    """Tekoälykutsu epäonnistui korjauksessa, mutta kuvalla on jo valmis tulos, joka jätettiin ennalleen."""


UPLOAD_MAX = 2400
INTERIOR_MAX = 1600
ALLOWED = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


def save_upload(job_id: str, filename: str, data: bytes) -> dict | None:
    ext = Path(filename or "").suffix.lower()
    if ext and ext not in ALLOWED:
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            img = ImageOps.exif_transpose(im).convert("RGB")
    except Exception:  # noqa: BLE001 - ei kuva
        return None
    img.thumbnail((UPLOAD_MAX, UPLOAD_MAX), Image.LANCZOS)
    name = uuid.uuid4().hex[:10]
    img.save(storage.job_dir(job_id) / "originals" / f"{name}.jpg", quality=93)
    return {
        "name": name,
        "file": f"{name}.jpg",
        "original_name": Path(filename or "kuva.jpg").name[:200],
        "status": "queued",
        "kind": None,
        "override": "auto",
        "version": 0,
        "uploaded": time.time(),
    }


def _context(job_id: str, name: str):
    job = storage.get_job(job_id)
    img = next(i for i in job["images"] if i["name"] == name)
    dealer = storage.get_dealer(job["dealer_id"])
    jdir = storage.job_dir(job_id)
    return img, dealer, jdir / "originals" / img["file"], jdir / "analysis" / name


def analyze(job_id: str, name: str) -> dict:
    img, _, src, adir = _context(job_id, name)
    meta_path = adir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else analysis.analyze(src, adir)
    storage.update_image(job_id, name, kind=meta["kind"])
    return meta


def _save_output(job_id: str, name: str, image: Image.Image, extra: dict | None = None):
    out_dir = storage.job_dir(job_id) / "output"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{name}.jpg"
    img = next(i for i in storage.get_job(job_id)["images"] if i["name"] == name)
    version = int(img.get("version") or 0)
    if out.exists():
        shutil.copy(out, out_dir / f"{name}.v{version}.jpg")
    image.convert("RGB").save(out, "JPEG", quality=92, optimize=True, progressive=True, subsampling=0)
    now = time.time()
    fields = {"version": version + 1, "done_at": now, **(extra or {})}
    if not img.get("first_done_at"):
        fields["first_done_at"] = now  # käsittelyaika raportteihin: vastaanotosta ensimmäiseen valmiiseen
    storage.update_image(job_id, name, **fields)


def _is_car(img: dict, adir: Path) -> bool:
    if img.get("override", "auto") != "auto":
        return img["override"] == "car"
    meta = json.loads((adir / "meta.json").read_text(encoding="utf-8"))
    return meta["kind"] == "car"


def _original(src: Path) -> Image.Image:
    im = load_image(src)
    im.thumbnail((INTERIOR_MAX, INTERIOR_MAX), Image.LANCZOS)
    return im


# --- Kuvakatto ---

def ai_images_this_month(dealer_id: str) -> int:
    now = datetime.now()
    start = datetime(now.year, now.month, 1).timestamp()
    return sum(
        1 for j in storage.list_jobs(dealer_id) for i in j["images"]
        if int(i.get("ai_calls") or 0) > 0 and float(i.get("uploaded") or j["created"]) >= start
    )


def _cap_reached(dealer: dict, img: dict) -> bool:
    cap = int(dealer.get("image_cap") or 0)
    if not cap or int(img.get("ai_calls") or 0) > 0:  # jo käsitellyn kuvan korjaus ei kuluta kattoa
        return False
    return ai_images_this_month(dealer["id"]) >= cap


def _notify_cap(dealer: dict):
    month = datetime.now().strftime("%Y-%m")
    if dealer.get("cap_notified") == month:
        return
    dealer["cap_notified"] = month
    storage.save_dealer(dealer)
    threading.Thread(target=notify.send, daemon=True, args=(
        f"Autostudio: kuvakatto täynnä – {dealer['name']}",
        f"{dealer['name']} on käyttänyt kuukauden kuvakaton ({dealer['image_cap']} kuvaa). Uudet kuvat tehdään "
        "ilman tekoälytaustaa lasketulla lattialla, kunnes katto nostetaan ylläpidossa tai kuukausi vaihtuu.",
        [],
    )).start()


# --- Tekoälytausta ---

def _request(base: Image.Image, style: dict, extra: str | None):
    try:
        return aib.request_background(base, style, extra=extra)
    except Exception as first:  # noqa: BLE001
        fallback = settings.get().get("fallback_model")
        if not fallback or fallback == style.get("model"):
            raise
        log.warning("Malli %s epäonnistui, käytetään varamallia %s: %s", style.get("model"), fallback, first)
        ai, info = aib.request_background(base, {**style, "model": fallback}, extra=extra)
        info["fallback"] = f"{style.get('model')} epäonnistui: {str(first)[:150]}"
        return ai, info


def generate(job_id: str, name: str, extra_prompt: str | None = None):
    img, dealer, src, adir = _context(job_id, name)
    if not _is_car(img, adir):
        _save_output(job_id, name, _original(src))
        return
    if not dealer.get("style"):
        # Ei tyyliprofiilia: laskettu kokoaminen liikkeen asetuksilla
        result = compose.render(src, adir, dealer["settings"], storage.dealer_dir(dealer["id"]), img.get("override", "auto"))
        _save_output(job_id, name, result)
        return
    style = aib.load_style(dealer["style"])
    base, _ = aib.build_guide(src, adir, style)
    if _cap_reached(dealer, img):
        info = {"skipped": "Kuukauden kuvakatto täynnä"}
        _notify_cap(dealer)
    else:
        try:
            ai, info = _request(base, style, extra_prompt)
            ai.save(adir / "ai.jpg", quality=95)
            info["cost_eur"] = round(float(info.pop("cost", 0) or 0) * settings.usd_eur(), 4)
            # Kulut kertyvät kuvalle: myös korjaukset ja uudelleenteot näkyvät ylläpidon raporteissa
            storage.update_image(job_id, name, ai_calls=int(img.get("ai_calls") or 0) + 1,
                                 cost_total_eur=round(float(img.get("cost_total_eur") or 0) + info["cost_eur"], 4))
        except Exception as e:  # noqa: BLE001 - kuva tehdään silti lasketulla pohjalla tai edellisellä raakakuvalla
            log.warning("Tekoälytausta epäonnistui (%s/%s): %s", job_id, name, e)
            info = {"error": str(e)[:300]}
            if (adir / "ai.jpg").exists() and (storage.job_dir(job_id) / "output" / f"{name}.jpg").exists():
                # Korjaus tai uudelleenteko: edellinen kuva säilyy sellaisenaan, ei turhaa samannäköistä versiota
                storage.update_image(job_id, name, ai={**(img.get("ai") or {}), "error": info["error"]})
                raise AiFailed("Tekoälytausta epäonnistui, edellinen kuva säilytettiin") from e
    _finish(job_id, name, style, src, adir, info)


def refinish(job_id: str, name: str):
    """Ilman tekoälykutsua: tallennetusta raakakuvasta (tai lasketusta pohjasta) uusi viimeistely."""
    img, dealer, src, adir = _context(job_id, name)
    if not _is_car(img, adir):
        _save_output(job_id, name, _original(src))
        return
    if not dealer.get("style"):
        result = compose.render(src, adir, dealer["settings"], storage.dealer_dir(dealer["id"]), img.get("override", "auto"))
        _save_output(job_id, name, result)
        return
    _finish(job_id, name, aib.load_style(dealer["style"]), src, adir, None)


def _compose_final(style: dict, src: Path, adir: Path, ai: Image.Image | None) -> Image.Image:
    meta = json.loads((adir / "meta.json").read_text(encoding="utf-8"))
    base, layers = aib.build_guide(src, adir, style)
    if ai is None:
        ai_path = adir / "ai.jpg"
        ai = Image.open(ai_path).convert("RGB") if ai_path.exists() else base
    wall = aib.wall_layer(src, adir, base, style)
    result, _ = aib.finish(ai.resize(base.size, Image.LANCZOS), base, layers, meta, style, corrections=False, wall=wall)
    return result


def _finish(job_id: str, name: str, style: dict, src: Path, adir: Path, info: dict | None):
    result = _compose_final(style, src, adir, None)
    keys = ("model", "seconds", "cost_eur", "error", "skipped", "fallback")
    extra = {"ai": {k: info[k] for k in keys if info.get(k) is not None}} if info is not None else None
    _save_output(job_id, name, result, extra)


def render_preview(job_id: str, name: str, style: dict, use_ai: bool = False) -> tuple[Image.Image, float]:
    """Tyylieditorin esikatselu tallentamatta mitään. use_ai=True tekee uuden tekoälytaustan (maksullinen)."""
    _, _, src, adir = _context(job_id, name)
    ai, cost_eur = None, 0.0
    if use_ai:
        base, _ = aib.build_guide(src, adir, style)
        ai, info = aib.request_background(base, style)
        cost_eur = round(float(info.get("cost") or 0) * settings.usd_eur(), 4)
    return _compose_final(style, src, adir, ai), cost_eur
