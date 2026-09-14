"""Ylläpidon rajapinta: yleiskatsaus, yritykset, erät ja korjauspyynnöt.

Suojattu main.py:n HTTP Basic -tunnistuksella (AUTOSTUDIO_USER / AUTOSTUDIO_PASSWORD).
Kaikki kulut ovat euroja: tekoälykutsun hinta muunnetaan euroiksi kutsuhetkellä (kurssi Asetuksista).
"""
import calendar
import os
import re
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from PIL import Image

from . import config, dealer_api, notify, pipeline, settings, storage, worker

router = APIRouter(prefix="/api/admin")
DAY = 86400


def _bounds(year: int, month: int) -> tuple[float, float, int]:
    start = datetime(year, month, 1).timestamp()
    end = datetime(year + (month == 12), month % 12 + 1, 1).timestamp()
    return start, end, calendar.monthrange(year, month)[1]


def _parse_month(month: str | None) -> tuple[int, int]:
    if month and re.match(r"^\d{4}-(0[1-9]|1[0-2])$", month):
        y, m = month.split("-")
        return int(y), int(m)
    now = datetime.now()
    return now.year, now.month


def _uploaded(job: dict, img: dict) -> float:
    return float(img.get("uploaded") or job["created"])


def _cost_eur(img: dict) -> float:
    if img.get("cost_total_eur") is not None:
        return float(img["cost_total_eur"])
    # Ennen euromuutosta käsitellyt kuvat: dollarit muunnetaan nykyisellä kurssilla
    usd = img.get("cost_total")
    if usd is None:
        usd = (img.get("ai") or {}).get("cost") or 0
    return float(usd) * settings.usd_eur()


def _stats(jobs: list[dict], feedback: list[dict], start: float, end: float) -> dict:
    images = cars = errors = 0
    cost = 0.0
    durations = []
    for job in jobs:
        in_range = [i for i in job["images"] if start <= _uploaded(job, i) < end]
        cost += sum(_cost_eur(i) for i in in_range)  # myös asiakkaan poistamista kuvista on maksettu
        visible = [i for i in in_range if not i.get("deleted")]
        if not visible:
            continue
        cars += 1
        images += len(visible)
        for img in visible:
            errors += img["status"] == "error"
            if img.get("first_done_at"):
                durations.append(img["first_done_at"] - _uploaded(job, img))
    fixes = sum(1 for f in feedback if start <= f["created"] < end)
    return {
        "images": images,
        "cars": cars,
        "cost_eur": round(cost, 2),
        "fixes": fixes,
        "fix_rate": round(fixes / images, 4) if images else 0,
        "errors": errors,
        "avg_seconds": round(sum(durations) / len(durations), 1) if durations else None,
    }


def _dealer(dealer_id: str) -> dict:
    try:
        return storage.get_dealer(dealer_id)
    except KeyError:
        raise HTTPException(404, "Yritystä ei löytynyt")


def _job(job_id: str) -> dict:
    try:
        return storage.get_job(job_id)
    except KeyError:
        raise HTTPException(404, "Erää ei löytynyt")


def _company_public(d: dict) -> dict:
    return {k: d.get(k) for k in ("id", "name", "token", "style", "price_eur", "image_cap", "active", "created")}


# --- Yleiskatsaus ---

@router.get("/overview")
def overview(month: str | None = None):
    year, mon = _parse_month(month)
    start, end, days = _bounds(year, mon)
    dealers = [storage.get_dealer(d["id"]) for d in storage.list_dealers()]
    jobs = storage.list_jobs()
    feedback = storage.list_feedback()
    now = datetime.now().timestamp()

    daily = [0] * days
    for job in jobs:
        for img in job["images"]:
            t = _uploaded(job, img)
            if start <= t < end:
                daily[datetime.fromtimestamp(t).day - 1] += 1

    rows, alerts = [], []
    for d in dealers:
        djobs = [j for j in jobs if j["dealer_id"] == d["id"]]
        dfb = [f for f in feedback if f["dealer_id"] == d["id"]]
        st = _stats(djobs, dfb, start, end)
        last = max((_uploaded(j, i) for j in djobs for i in j["images"]), default=None)
        price = float(d.get("price_eur") or 0) if d.get("active", True) else 0.0
        open_fb = sum(1 for f in dfb if not f.get("handled"))
        rows.append({
            **_company_public(d), **st, "price_eur": price, "margin_eur": round(price - st["cost_eur"], 2),
            "last_used": last, "open_feedback": open_fb,
        })
        if d.get("active", True) and last and now - last > 30 * DAY:
            alerts.append({"level": "info", "text": f"{d['name']} ei ole käyttänyt palvelua 30 päivään", "link": f"#/yritys/{d['id']}"})
        cap = int(d.get("image_cap") or 0)
        current = (year, mon) == (datetime.now().year, datetime.now().month)
        if cap and current:
            # Sama laskenta kuin katon valvonnassa: tekoälyllä käsitellyt kuvat
            used = pipeline.ai_images_this_month(d["id"])
            if used >= cap:
                alerts.append({"level": "critical", "text": f"{d['name']}: kuvakatto täynnä ({used}/{cap}), uudet kuvat tehdään ilman tekoälyä", "link": f"#/yritys/{d['id']}"})
            elif used >= cap * 0.9:
                alerts.append({"level": "warning", "text": f"{d['name']}: kuvakatto lähes täynnä ({used}/{cap})", "link": f"#/yritys/{d['id']}"})

    open_total = sum(r["open_feedback"] for r in rows)
    if open_total:
        alerts.insert(0, {"level": "warning", "text": f"{open_total} käsittelemätöntä korjauspyyntöä", "link": "#/palautteet"})
    failed = [(j, i) for j in jobs for i in j["images"] if i["status"] == "error" and now - _uploaded(j, i) < 30 * DAY]
    if failed:
        alerts.insert(0, {"level": "critical", "text": f"{len(failed)} kuvan käsittely epäonnistui (30 pv)", "link": f"#/era/{failed[0][0]['id']}"})

    totals = _stats(jobs, feedback, start, end)
    revenue = sum(r["price_eur"] for r in rows)
    totals.update(revenue_eur=round(revenue, 2), margin_eur=round(revenue - totals["cost_eur"], 2),
                  companies=len(rows), active_companies=sum(1 for r in rows if r["active"]))
    rows.sort(key=lambda r: (-r["images"], r["name"].lower()))
    return {"month": f"{year}-{mon:02d}", "totals": totals, "daily": [{"day": i + 1, "images": n} for i, n in enumerate(daily)],
            "companies": rows, "alerts": alerts}


# --- Yritykset ---

@router.post("/companies")
def create_company(payload: dict = Body(...)):
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Anna yrityksen nimi")
    return _company_public(storage.get_dealer(storage.create_dealer(name[:120])["id"]))


@router.get("/companies/{dealer_id}")
def company(dealer_id: str):
    d = _dealer(dealer_id)
    jobs = storage.list_jobs(d["id"])
    fb = [f for f in storage.list_feedback() if f["dealer_id"] == d["id"]]
    now = datetime.now()
    months = []
    for k in range(11, -1, -1):
        y, m = now.year, now.month - k
        while m <= 0:
            m += 12
            y -= 1
        start, end, _ = _bounds(y, m)
        st = _stats(jobs, fb, start, end)
        # Kuukausihinta vain asiakkuuden ajalta: ei tuloja kuukausille ennen yrityksen luontia
        customer = d.get("active", True) and float(d.get("created") or 0) < end
        price = float(d.get("price_eur") or 0) if customer else 0.0
        months.append({"month": f"{y}-{m:02d}", **st, "price_eur": price, "margin_eur": round(price - st["cost_eur"], 2)})
    batches = []
    for j in jobs:
        visible = [i for i in j["images"] if not i.get("deleted")]
        done = [i for i in visible if i["status"] == "done"]
        cover = next((i for i in done if i.get("kind") == "car"), done[0] if done else None)
        batches.append({
            "id": j["id"], "title": j["title"], "created": j["created"], "count": len(visible), "done": len(done),
            "deleted": len(j["images"]) - len(visible),
            "errors": sum(1 for i in visible if i["status"] == "error"),
            "pending": sum(1 for i in visible if i["status"] in ("queued", "analyzing", "generating")),
            "cost_eur": round(sum(_cost_eur(i) for i in j["images"]), 2),
            "cover": cover and {"name": cover["name"], "version": cover.get("version", 0)},
        })
    return {"company": _company_public(d), "months": months, "batches": batches,
            "open_feedback": sum(1 for f in fb if not f.get("handled")), "styles": _styles(),
            "cap_used": pipeline.ai_images_this_month(d["id"])}


def _styles() -> list[str]:
    return sorted(p.parent.name for p in (config.ASSETS_DIR / "styles").glob("*/style.json"))


@router.put("/companies/{dealer_id}")
def update_company(dealer_id: str, payload: dict = Body(...)):
    d = _dealer(dealer_id)
    if payload.get("name"):
        d["name"] = str(payload["name"]).strip()[:120]
    if "style" in payload:
        style = str(payload["style"] or "")
        if style and style not in _styles():
            raise HTTPException(400, "Tyyliprofiilia ei löytynyt")
        d["style"] = style
    for key, cast in (("price_eur", float), ("image_cap", int)):
        if key in payload:
            try:
                d[key] = max(0, cast(payload[key] or 0))
            except (TypeError, ValueError):
                raise HTTPException(400, "Tarkista hinta ja kuvakatto")
    if "active" in payload:
        d["active"] = bool(payload["active"])
    storage.save_dealer(d)
    return _company_public(storage.get_dealer(dealer_id))


@router.post("/companies/{dealer_id}/token")
def rotate_token(dealer_id: str):
    d = _dealer(dealer_id)
    d["token"] = secrets.token_urlsafe(12)
    storage.save_dealer(d)
    return _company_public(storage.get_dealer(dealer_id))


@router.post("/companies/{dealer_id}/batches")
async def upload_for_company(dealer_id: str, files: list[UploadFile] = File(...), title: str = Form("")):
    d = _dealer(dealer_id)
    t = datetime.now()
    job = storage.create_job(d["id"], title.strip()[:80] or f"Kuvat {t.day}.{t.month}. klo {t.hour}.{t.minute:02d}")
    await dealer_api._add(job["id"], files)
    return {"id": job["id"]}


# --- Erät ja kuvat ---

def _versions(out_dir: Path, name: str) -> list[int]:
    found = []
    for p in out_dir.glob(f"{name}.v*.jpg"):
        m = re.match(rf"^{re.escape(name)}\.v(\d+)$", p.stem)
        if m:
            found.append(int(m.group(1)))
    return sorted(found)


@router.get("/batches/{job_id}")
def batch(job_id: str):
    job = _job(job_id)
    d = _dealer(job["dealer_id"])
    out_dir = storage.job_dir(job_id) / "output"
    fb = [f for f in storage.list_feedback() if f["job_id"] == job_id]
    images = []
    for img in job["images"]:
        images.append({
            **img, "cost_eur": round(_cost_eur(img), 3), "versions": _versions(out_dir, img["name"]),
            "feedback": [{k: f.get(k) for k in ("id", "created", "prompt", "status", "handled")} for f in fb if f["image"] == img["name"]],
            "seconds": round(img["first_done_at"] - _uploaded(job, img), 1) if img.get("first_done_at") else None,
        })
    return {"job": {k: job[k] for k in ("id", "title", "created")}, "company": _company_public(d), "images": images,
            "cost_eur": round(sum(i["cost_eur"] for i in images), 2)}


def _image(job: dict, name: str) -> dict:
    img = next((i for i in job["images"] if i["name"] == name), None)
    if not img:
        raise HTTPException(404, "Kuvaa ei löytynyt")
    return img


@router.get("/batches/{job_id}/{name}/file/{kind}")
def batch_file(job_id: str, name: str, kind: str):
    job = _job(job_id)
    img = _image(job, name)
    jdir = storage.job_dir(job_id)
    if kind == "original":
        path = jdir / "originals" / img["file"]
    elif kind == "output":
        path = jdir / "output" / f"{name}.jpg"
    elif re.match(r"^v\d+$", kind):
        path = jdir / "output" / f"{name}.{kind}.jpg"
    else:
        raise HTTPException(404)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000"})


@router.post("/batches/{job_id}/refinish")
async def refinish_batch(job_id: str):
    job = _job(job_id)
    for img in job["images"]:
        if img["status"] == "done":
            await run_in_threadpool(pipeline.refinish, job_id, img["name"])
    return batch(job_id)


@router.post("/batches/{job_id}/{name}/redo")
def redo(job_id: str, name: str, payload: dict = Body(default={})):
    job = _job(job_id)
    img = _image(job, name)
    if img["status"] not in ("done", "error"):
        raise HTTPException(409, "Kuvaa käsitellään jo")
    prompt = str(payload.get("prompt") or "").strip()[:500] or None
    worker.enqueue_fix(job_id, name, prompt, None)
    return batch(job_id)


@router.post("/batches/{job_id}/{name}/override")
async def set_override(job_id: str, name: str, payload: dict = Body(...)):
    job = _job(job_id)
    _image(job, name)
    override = payload.get("override")
    if override not in ("auto", "car", "other"):
        raise HTTPException(400)
    storage.update_image(job_id, name, override=override)
    await run_in_threadpool(pipeline.refinish, job_id, name)
    return batch(job_id)


@router.post("/batches/{job_id}/{name}/version")
async def restore_version(job_id: str, name: str, payload: dict = Body(...)):
    job = _job(job_id)
    _image(job, name)
    try:
        version = int(payload.get("version"))
    except (TypeError, ValueError):
        raise HTTPException(400)
    path = storage.job_dir(job_id) / "output" / f"{name}.v{version}.jpg"
    if not path.exists():
        raise HTTPException(404, "Versiota ei löytynyt")
    with Image.open(path) as im:
        restored = im.convert("RGB")
    await run_in_threadpool(pipeline._save_output, job_id, name, restored)
    return batch(job_id)


# --- Korjauspyynnöt ---

@router.get("/feedback")
def feedback_list():
    items = storage.list_feedback()
    return sorted(items, key=lambda f: (bool(f.get("handled")), -f["created"]))


@router.put("/feedback/{feedback_id}")
def update_feedback(feedback_id: str, payload: dict = Body(...)):
    fields = {}
    if "handled" in payload:
        fields["handled"] = bool(payload["handled"])
    if "note" in payload:
        fields["note"] = str(payload["note"] or "")[:1000]
    try:
        return storage.update_feedback(feedback_id, **fields)
    except KeyError:
        raise HTTPException(404)


@router.get("/feedback/{feedback_id}/file/{filename}")
def feedback_file(feedback_id: str, filename: str):
    if filename not in ("original.jpg", "before.jpg", "after.jpg"):
        raise HTTPException(404)
    try:
        path = storage.feedback_dir(feedback_id) / filename
    except KeyError:
        raise HTTPException(404)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


@router.get("/batches/{job_id}/zip")
def batch_zip(job_id: str):
    return dealer_api.zip_response(_job(job_id))


@router.post("/batches/{job_id}/{name}/undelete")
def undelete(job_id: str, name: str):
    job = _job(job_id)
    _image(job, name)
    storage.update_image(job_id, name, deleted=False)
    return batch(job_id)


# --- Asetukset ---

@router.get("/settings")
def get_settings():
    return {
        **settings.get(), "models": settings.MODELS,
        "smtp_configured": notify.configured(), "admin_email": os.environ.get("AUTOSTUDIO_ADMIN_EMAIL", ""),
        "openrouter_key": bool(os.environ.get("OPENROUTER_API_KEY")),
        "admin_protected": bool(os.environ.get("AUTOSTUDIO_USER") and os.environ.get("AUTOSTUDIO_PASSWORD")),
    }


@router.put("/settings")
def put_settings(payload: dict = Body(...)):
    try:
        settings.update(payload)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e))
    return get_settings()
