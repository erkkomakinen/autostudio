"""Ylläpito: tyyliprofiilien muokkaus, esikatselu ja lattiamateriaalin vaihto.

Seinä ja logo lasketaan aina itse, joten niiden muutokset näkyvät esikatselussa ja päivittyvät
olemassa oleviin kuviin ilmaiseksi. Lattian, laattakoon, mallin ja auton sijoittelun muutokset
vaativat uuden tekoälytaustan (esikatselu tekoälyllä tai kuvan uudelleenteko).
"""
import io
import json
import re
import shutil
import threading

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from PIL import Image

from . import ai_background as aib
from . import config, materials, pipeline, settings, storage

router = APIRouter(prefix="/api/admin")
STYLES = config.ASSETS_DIR / "styles"
ID_RE = re.compile(r"^[a-z0-9-]{1,48}$")
FLOATS = {
    "wall_brightness": (0, 80), "wall_glow": (0, 2), "wall_uplight": (0, 1.5), "wall_vignette": (0, 1),
    "wall_texture": (0, 0.4), "logo_width": (0.05, 0.8), "logo_y": (0.03, 0.5),
    "car_width": (0.4, 0.95), "car_height": (0.3, 0.8), "floor_y": (0.6, 0.97), "tile_size_m": (0.2, 1.5),
}
TEXTS = ("name", "floor_description")
CHOICES = {"tile_direction": ("car", "camera"), "finish_prompt": ("locked", "realism")}
FILES = ("logo.png", "viimeistely-lattia.jpg")


def _dir(style_id: str):
    if not ID_RE.match(style_id) or not (STYLES / style_id / "style.json").exists():
        raise HTTPException(404, "Tyyliä ei löytynyt")
    return STYLES / style_id


def _read(style_id: str) -> dict:
    return json.loads((_dir(style_id) / "style.json").read_text(encoding="utf-8"))


def _write(style_id: str, data: dict):
    path = _dir(style_id) / "style.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _used_by(style_id: str) -> list[dict]:
    return [{"id": d["id"], "name": d["name"]} for d in storage.list_dealers() if d.get("style") == style_id]


def _values(data: dict) -> dict:
    values = {k: data.get(k) for k in (*FLOATS, *TEXTS, "model")}
    values.update({k: data.get(k) or options[0] for k, options in CHOICES.items()})
    return values


def _validate(values: dict) -> dict:
    clean = {}
    for key, (lo, hi) in FLOATS.items():
        if key in values:
            try:
                clean[key] = round(min(max(float(values[key]), lo), hi), 4)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Virheellinen arvo: {key}")
    for key in TEXTS:
        if key in values:
            clean[key] = str(values[key] or "").strip()[:300]
    for key, options in CHOICES.items():
        if key in values:
            if values[key] not in options:
                raise HTTPException(400, f"Virheellinen arvo: {key}")
            clean[key] = values[key]
    if "model" in values:
        if values["model"] not in settings.MODEL_IDS:
            raise HTTPException(400, "Tuntematon malli")
        clean["model"] = values["model"]
    return clean


def _samples() -> list[dict]:
    """Esikatselukuvat: valmiit ulkokuvat, joilla on tallennettu tekoälytausta (enintään 2, eri autoista)."""
    found, jobs_used, files_used = [], set(), set()
    for job in storage.list_jobs():
        for img in job["images"]:
            # Sama valokuva voi olla ladattu useaan erään: esikatseluun kaksi eri kuvaa
            if (img["status"] == "done" and img.get("kind") == "car" and img.get("override") != "other"
                    and not img.get("deleted") and job["id"] not in jobs_used and img["original_name"] not in files_used
                    and (storage.job_dir(job["id"]) / "analysis" / img["name"] / "ai.jpg").exists()):
                found.append({"job": job["id"], "name": img["name"], "label": f"{job['title']} · {img['original_name']}"})
                jobs_used.add(job["id"])
                files_used.add(img["original_name"])
                break
        if len(found) == 2:
            break
    return found


@router.get("/styles")
def list_styles():
    items = []
    for p in sorted(STYLES.glob("*/style.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        sid = p.parent.name
        items.append({"id": sid, "name": data.get("name", sid), "model": data.get("model"), "used_by": _used_by(sid),
                      "logo": (p.parent / "logo.png").exists(), "floor": (p.parent / "viimeistely-lattia.jpg").exists()})
    return items


@router.get("/styles/{style_id}")
def get_style(style_id: str):
    data = _read(style_id)
    return {"id": style_id, "values": _values(data), "used_by": _used_by(style_id), "models": settings.MODELS,
            "samples": _samples(), "limits": FLOATS,
            "files": {f: (_dir(style_id) / f).exists() for f in FILES}}


@router.put("/styles/{style_id}")
def update_style(style_id: str, payload: dict = Body(...)):
    data = _read(style_id)
    data.update(_validate(payload))
    _write(style_id, data)
    return get_style(style_id)


@router.post("/styles/{style_id}/copy")
def copy_style(style_id: str, payload: dict = Body(...)):
    new_id = storage.slugify(str(payload.get("name") or ""))
    if not new_id or not ID_RE.match(new_id):
        raise HTTPException(400, "Anna tyylille nimi")
    if (STYLES / new_id).exists():
        raise HTTPException(409, "Samanniminen tyyli on jo olemassa")
    shutil.copytree(_dir(style_id), STYLES / new_id, ignore=shutil.ignore_patterns("*.tmp"))
    data = _read(new_id)
    data["name"] = str(payload["name"]).strip()[:80]
    _write(new_id, data)
    return get_style(new_id)


@router.get("/styles/{style_id}/file/{filename}")
def style_file(style_id: str, filename: str):
    if filename not in FILES:
        raise HTTPException(404)
    path = _dir(style_id) / filename
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


async def _read_image(file: UploadFile) -> Image.Image:
    try:
        with Image.open(io.BytesIO(await file.read())) as im:
            im.load()
            return im.copy()
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "Tiedosto ei ole kuva (PNG, JPG tai WebP). SVG-logo pitää ensin muuntaa PNG:ksi.")


@router.post("/styles/{style_id}/logo")
async def upload_logo(style_id: str, file: UploadFile = File(...)):
    image = (await _read_image(file)).convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if bbox:
        image = image.crop(bbox)  # läpinäkyvät reunat pois, jotta koko ja paikka toimivat odotetusti
    image.save(_dir(style_id) / "logo.png")
    data = _read(style_id)
    data["logo"] = "logo.png"
    _write(style_id, data)
    return get_style(style_id)


@router.post("/styles/{style_id}/floor/upload")
async def upload_floor(style_id: str, file: UploadFile = File(...)):
    _dir(style_id)
    image = (await _read_image(file)).convert("RGB")
    await run_in_threadpool(materials.install_floor, style_id, image, f"Ladattu kuva: {file.filename}", 0.6)
    return get_style(style_id)


@router.post("/styles/{style_id}/floor/generate")
async def generate_floor(style_id: str, payload: dict = Body(...)):
    _dir(style_id)
    description = str(payload.get("description") or "").strip()
    if len(description) < 5:
        raise HTTPException(400, "Kuvaile lattiamateriaali, esim. kiillotettu harmaa graniitti")
    try:
        image, cost_eur = await run_in_threadpool(materials.generate_floor, description)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    await run_in_threadpool(materials.install_floor, style_id, image, f"Generoitu: {description}", 1.0)
    data = _read(style_id)
    data["floor_description"] = description
    _write(style_id, data)
    return {**get_style(style_id), "cost_eur": cost_eur}


@router.post("/styles/{style_id}/preview")
async def preview(style_id: str, payload: dict = Body(...)):
    samples = _samples()
    try:
        sample = samples[int(payload.get("sample", 0))]
    except (IndexError, TypeError, ValueError):
        raise HTTPException(404, "Esikatselukuvaa ei ole. Käsittele ensin vähintään yksi ulkokuva.")
    style = {**aib.load_style(style_id), **_validate(payload.get("values") or {})}
    try:
        image, cost_eur = await run_in_threadpool(pipeline.render_preview, sample["job"], sample["name"], style,
                                                  bool(payload.get("ai")))
    except Exception as e:  # noqa: BLE001 - tekoälykutsun virhe näytetään käyttäjälle
        raise HTTPException(502, f"Esikatselu epäonnistui: {str(e)[:200]}")
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"X-Cost-Eur": str(cost_eur)})


@router.post("/styles/{style_id}/refinish")
def refinish_all(style_id: str):
    """Päivittää tyylin seinän ja logon kaikkiin sitä käyttävien yritysten kuviin ilman tekoälykutsuja."""
    _dir(style_id)
    dealer_ids = {d["id"] for d in _used_by(style_id)}
    todo = [(j["id"], i["name"]) for j in storage.list_jobs() if j["dealer_id"] in dealer_ids
            for i in j["images"] if i["status"] == "done" and not i.get("deleted")]

    def run():
        for job_id, name in todo:
            try:
                pipeline.refinish(job_id, name)
            except Exception:  # noqa: BLE001 - yksi epäonnistunut kuva ei pysäytä muita
                pass

    threading.Thread(target=run, daemon=True).start()
    return {"queued": len(todo)}
