"""Asiakkaan (autoliikkeen) rajapinta: henkilökohtainen linkki /d/<token>, ei salasanaa eikä säätöjä.

Asiakas voi lisätä autoja (kuvaerät), nimetä niitä, lisätä ja poistaa kuvia, pyytää korjausta tekstillä,
valita alkuperäisen kuvan ja tallentaa kuvat. Korjauspyynnöistä tallennetaan palaute ylläpidolle kuvineen.
"""
import io
import shutil
import threading
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import ai_background as aib
from . import notify, pipeline, storage, worker

STATIC = Path(__file__).parent / "static"
router = APIRouter()
REDO = "Tee uudelleen"
PENDING = ("queued", "analyzing", "generating")


def _dealer(token: str) -> dict:
    dealer = storage.dealer_by_token(token)
    if not dealer:
        raise HTTPException(404, "Linkki ei ole voimassa. Pyydä uusi linkki palveluntarjoajalta.")
    if not dealer.get("active", True):
        raise HTTPException(403, "Palvelu ei ole tällä hetkellä käytössä. Ota yhteyttä palveluntarjoajaan.")
    return dealer


def _job(dealer: dict, job_id: str) -> dict:
    try:
        job = storage.get_job(job_id)
    except KeyError:
        raise HTTPException(404, "Autoa ei löytynyt.")
    if job["dealer_id"] != dealer["id"]:
        raise HTTPException(404, "Autoa ei löytynyt.")
    return job


def _image(job: dict, name: str) -> dict:
    img = next((i for i in job["images"] if i["name"] == name and not i.get("deleted")), None)
    if not img:
        raise HTTPException(404, "Kuvaa ei löytynyt.")
    return img


def _visible(job: dict) -> list[dict]:
    return [i for i in job["images"] if not i.get("deleted")]


def _public(job: dict) -> dict:
    return {
        "id": job["id"],
        "title": job["title"],
        "created": job["created"],
        "images": [{k: img.get(k) for k in ("name", "original_name", "status", "kind", "override", "version", "error")}
                   for img in _visible(job)],
    }


def _logo_path(dealer: dict) -> Path | None:
    if dealer.get("style"):
        try:
            style = aib.load_style(dealer["style"])
            if style.get("logo") and (style["dir"] / style["logo"]).exists():
                return style["dir"] / style["logo"]
        except (OSError, ValueError):
            pass
    own = storage.dealer_dir(dealer["id"]) / "logo.png"
    return own if own.exists() else None


@router.get("/d/{token}")
def dealer_page(token: str):
    # Sivu näytetään myös pois käytöstä olevalle liikkeelle, jotta asiakas näkee syyn
    if not storage.dealer_by_token(token):
        raise HTTPException(404, "Linkki ei ole voimassa.")
    return FileResponse(STATIC / "dealer.html", headers={"Cache-Control": "no-cache"})


@router.get("/d/{token}/manifest.webmanifest")
def manifest(token: str):
    dealer = storage.dealer_by_token(token)
    if not dealer:
        raise HTTPException(404)
    return JSONResponse({
        "name": f"{dealer['name']} · Kuvat",
        "short_name": "Kuvat",
        "start_url": f"/d/{token}",
        "scope": f"/d/{token}",
        "display": "standalone",
        "background_color": "#0d0e10",
        "theme_color": "#0d0e10",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }, media_type="application/manifest+json")


@router.get("/api/d/{token}")
def dealer_info(token: str):
    dealer = _dealer(token)
    return {"name": dealer["name"], "logo": _logo_path(dealer) is not None}


@router.get("/api/d/{token}/logo")
def dealer_logo(token: str):
    path = _logo_path(_dealer(token))
    if not path:
        raise HTTPException(404)
    return FileResponse(path)


@router.get("/api/d/{token}/batches")
def batches(token: str):
    dealer = _dealer(token)
    result = []
    for j in storage.list_jobs(dealer["id"])[:50]:
        images = _visible(j)
        if not images:
            continue
        done = [i for i in images if i["status"] == "done"]
        cover = next((i for i in done if i.get("kind") == "car" and i.get("override") != "other"), done[0] if done else None)
        result.append({
            "id": j["id"], "title": j["title"], "created": j["created"], "count": len(images), "done": len(done),
            "pending": sum(1 for i in images if i["status"] in PENDING),
            "errors": sum(1 for i in images if i["status"] == "error"),
            "cover": cover and {"name": cover["name"], "version": cover.get("version", 0)},
        })
    return result


async def _add(job_id: str, files: list[UploadFile]) -> dict:
    added = []
    for f in files:
        rec = await run_in_threadpool(pipeline.save_upload, job_id, f.filename or "kuva.jpg", await f.read())
        if rec:
            added.append(rec)
    if not added:
        raise HTTPException(400, "Kuvia ei voitu lukea. Valitse JPG-, PNG- tai HEIC-kuvia.")
    storage.update_job(job_id, lambda j: j["images"].extend(added))
    for rec in added:
        worker.enqueue(job_id, rec["name"])
    return _public(storage.get_job(job_id))


def _default_title() -> str:
    t = time.localtime()
    return f"Auto {t.tm_mday}.{t.tm_mon}. klo {t.tm_hour}.{t.tm_min:02d}"


@router.post("/api/d/{token}/batches")
async def create_batch(token: str, files: list[UploadFile] = File(...), title: str = Form("")):
    dealer = _dealer(token)
    job = storage.create_job(dealer["id"], title.strip()[:80] or _default_title())
    try:
        return await _add(job["id"], files)
    except HTTPException:
        # Tyhjää autoa ei jätetä listaan, jos yhtään kuvaa ei voitu lukea
        shutil.rmtree(storage.job_dir(job["id"]), ignore_errors=True)
        raise


@router.post("/api/d/{token}/batches/{job_id}/images")
async def add_to_batch(token: str, job_id: str, files: list[UploadFile] = File(...)):
    return await _add(_job(_dealer(token), job_id)["id"], files)


@router.get("/api/d/{token}/batches/{job_id}")
def batch(token: str, job_id: str):
    return _public(_job(_dealer(token), job_id))


@router.put("/api/d/{token}/batches/{job_id}")
def rename_batch(token: str, job_id: str, payload: dict = Body(...)):
    job = _job(_dealer(token), job_id)
    title = str(payload.get("title") or "").strip()[:80]
    if not title:
        raise HTTPException(400, "Anna autolle nimi.")
    storage.update_job(job["id"], lambda j: j.update(title=title))
    return _public(storage.get_job(job["id"]))


@router.get("/api/d/{token}/batches/{job_id}/zip")
def batch_zip(token: str, job_id: str):
    job = _job(_dealer(token), job_id)
    return zip_response(job)


def zip_response(job: dict) -> StreamingResponse:
    out_dir = storage.job_dir(job["id"]) / "output"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        n = 0
        for img in _visible(job):
            path = out_dir / f"{img['name']}.jpg"
            if img["status"] == "done" and path.exists():
                n += 1
                z.write(path, f"{storage.slugify(job['title'])}-{n:02d}.jpg")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{storage.slugify(job["title"])}.zip"'})


@router.get("/api/d/{token}/batches/{job_id}/{name}/{kind}")
def image_file(token: str, job_id: str, name: str, kind: str):
    job = _job(_dealer(token), job_id)
    img = _image(job, name)
    jdir = storage.job_dir(job["id"])
    if kind == "original":
        path = jdir / "originals" / img["file"]
    elif kind == "output":
        path = jdir / "output" / f"{name}.jpg"
    else:
        raise HTTPException(404)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000"})


@router.delete("/api/d/{token}/batches/{job_id}/{name}")
def delete_image(token: str, job_id: str, name: str):
    job = _job(_dealer(token), job_id)
    _image(job, name)
    # Pehmeä poisto: tiedostot ja kulut jäävät ylläpidon nähtäväksi, asiakkaalta kuva katoaa
    storage.update_image(job["id"], name, deleted=True, deleted_at=time.time())
    return _public(storage.get_job(job["id"]))


def _feedback(dealer: dict, job: dict, img: dict, prompt: str, status: str = "processing", kind: str = "fix") -> dict:
    """kind: fix = asiakas pyysi korjausta, original = asiakas valitsi alkuperäisen kuvan."""
    fb = storage.create_feedback({
        "dealer_id": dealer["id"], "dealer_name": dealer["name"], "job_id": job["id"], "job_title": job["title"],
        "image": img["name"], "original_name": img["original_name"], "prompt": prompt, "status": status, "type": kind,
    })
    fdir = storage.feedback_dir(fb["id"])
    jdir = storage.job_dir(job["id"])
    shutil.copy(jdir / "originals" / img["file"], fdir / "original.jpg")
    if (jdir / "output" / f"{img['name']}.jpg").exists():
        shutil.copy(jdir / "output" / f"{img['name']}.jpg", fdir / "before.jpg")
    return fb


@router.post("/api/d/{token}/batches/{job_id}/{name}/fix")
def fix_image(token: str, job_id: str, name: str, payload: dict = Body(...)):
    dealer = _dealer(token)
    job = _job(dealer, job_id)
    img = _image(job, name)
    if img["status"] not in ("done", "error"):
        raise HTTPException(409, "Kuvaa käsitellään vielä. Odota hetki.")
    prompt = str(payload.get("prompt") or "").strip()[:500] or REDO
    fb = _feedback(dealer, job, img, prompt)
    if img.get("override") == "other":
        storage.update_image(job["id"], name, override="auto")
    worker.enqueue_fix(job["id"], name, None if prompt == REDO else prompt, fb["id"])
    return _public(storage.get_job(job["id"]))


@router.post("/api/d/{token}/batches/{job_id}/{name}/original")
async def use_original(token: str, job_id: str, name: str):
    dealer = _dealer(token)
    job = _job(dealer, job_id)
    img = _image(job, name)
    fb = _feedback(dealer, job, img, "Asiakas valitsi alkuperäisen kuvan.", status="done", kind="original")
    storage.update_image(job["id"], name, override="other")
    await run_in_threadpool(pipeline.refinish, job["id"], name)
    fdir = storage.feedback_dir(fb["id"])
    threading.Thread(target=lambda: storage.update_feedback(fb["id"], notified=notify.send(
        f"Autostudio: alkuperäinen kuva valittu – {dealer['name']}",
        f"Liike: {dealer['name']}\nAuto: {job['title']}\nKuva: {img['original_name']}\n\nAsiakas valitsi alkuperäisen kuvan käsitellyn sijaan.",
        [(fdir / "original.jpg", "alkuperainen.jpg"), (fdir / "before.jpg", "kasitelty.jpg")],
    )), daemon=True).start()
    return _public(storage.get_job(job["id"]))


@router.post("/api/d/{token}/batches/{job_id}/{name}/restore")
async def restore_processed(token: str, job_id: str, name: str):
    job = _job(_dealer(token), job_id)
    _image(job, name)
    storage.update_image(job["id"], name, override="auto")
    await run_in_threadpool(pipeline.refinish, job["id"], name)
    return _public(storage.get_job(job["id"]))
