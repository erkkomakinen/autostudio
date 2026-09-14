"""Tiedostopohjainen tallennus: liikkeet ja käsittelyerät JSON-tiedostoina.

Tuotannossa tämän voi vaihtaa tietokantaan (PostgreSQL) ja tiedostot S3-yhteensopivaan
tallennukseen ilman muutoksia analyysi- tai kokoamiskoodiin.
"""
import json
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

from . import config
from .compose import settings_with_defaults

_lock = threading.RLock()
ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")


def slugify(text: str) -> str:
    text = text.lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("å", "a")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:48] or "liike"


def _retry(fn, attempts: int = 20):
    """Windows lukitsee tiedoston hetkeksi, kun toinen säie korvaa sen: yritetään uudelleen."""
    for i in range(attempts):
        try:
            return fn()
        except (PermissionError, json.JSONDecodeError):
            if i == attempts - 1:
                raise
            time.sleep(0.02 * (i + 1))


def _read(path: Path) -> dict:
    with _lock:
        return _retry(lambda: json.loads(path.read_text(encoding="utf-8")))


def _write(path: Path, data: dict):
    with _lock:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        _retry(lambda: tmp.replace(path))


# --- Liikkeet ---

def dealer_dir(dealer_id: str) -> Path:
    if not ID_RE.match(dealer_id):
        raise KeyError(dealer_id)
    return config.DEALERS_DIR / dealer_id


def list_dealers() -> list[dict]:
    return sorted(
        (_read(p) for p in config.DEALERS_DIR.glob("*/dealer.json")),
        key=lambda d: d["name"].lower(),
    )


def get_dealer(dealer_id: str) -> dict:
    path = dealer_dir(dealer_id) / "dealer.json"
    if not path.exists():
        raise KeyError(dealer_id)
    dealer = _read(path)
    if not dealer.get("token"):
        # Asiakkaan henkilökohtainen linkki (/d/<token>), luodaan vanhoille liikkeille ensimmäisellä lukukerralla
        with _lock:
            dealer["token"] = secrets.token_urlsafe(12)
            _write(path, dealer)
    dealer.setdefault("style", "")
    dealer.setdefault("price_eur", 0)  # kuukausihinta (alv 0) kateraporttiin
    dealer.setdefault("image_cap", 0)  # kuvakatto kuukaudessa, 0 = ei kattoa
    dealer.setdefault("active", True)
    dealer["settings"] = settings_with_defaults(dealer.get("settings"))
    return dealer


def dealer_by_token(token: str) -> dict | None:
    if not token or len(token) > 64:
        return None
    for p in config.DEALERS_DIR.glob("*/dealer.json"):
        data = _read(p)
        if data.get("token") and secrets.compare_digest(str(data["token"]), token):
            return get_dealer(data["id"])
    return None


def create_dealer(name: str) -> dict:
    with _lock:
        base = slugify(name)
        dealer_id, n = base, 2
        while (config.DEALERS_DIR / dealer_id).exists():
            dealer_id, n = f"{base}-{n}", n + 1
        d = config.DEALERS_DIR / dealer_id
        d.mkdir(parents=True)
        dealer = {"id": dealer_id, "name": name, "created": time.time(), "token": secrets.token_urlsafe(12),
                  "style": "", "settings": settings_with_defaults({})}
        _write(d / "dealer.json", dealer)
        return dealer


def save_dealer(dealer: dict):
    with _lock:
        dealer["settings"] = settings_with_defaults(dealer.get("settings"))
        _write(dealer_dir(dealer["id"]) / "dealer.json", dealer)


# --- Käsittelyerät ---

def job_dir(job_id: str) -> Path:
    if not ID_RE.match(job_id):
        raise KeyError(job_id)
    return config.JOBS_DIR / job_id


def create_job(dealer_id: str, title: str) -> dict:
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = config.JOBS_DIR / job_id
    (d / "originals").mkdir(parents=True)
    job = {"id": job_id, "dealer_id": dealer_id, "title": title, "created": time.time(), "images": []}
    _write(d / "job.json", job)
    return job


def get_job(job_id: str) -> dict:
    path = job_dir(job_id) / "job.json"
    if not path.exists():
        raise KeyError(job_id)
    return _read(path)


def list_jobs(dealer_id: str | None = None) -> list[dict]:
    jobs = [_read(p) for p in config.JOBS_DIR.glob("*/job.json")]
    if dealer_id:
        jobs = [j for j in jobs if j["dealer_id"] == dealer_id]
    return sorted(jobs, key=lambda j: j["created"], reverse=True)


def update_job(job_id: str, fn):
    """Lukitun päivityksen apufunktio: fn muokkaa job-sanakirjaa paikallaan."""
    with _lock:
        job = get_job(job_id)
        fn(job)
        _write(job_dir(job_id) / "job.json", job)
        return job


# --- Korjauspyynnöt (asiakkaan palaute ylläpidolle) ---

FEEDBACK_DIR = config.DATA_DIR / "feedback"


def feedback_dir(feedback_id: str) -> Path:
    if not ID_RE.match(feedback_id):
        raise KeyError(feedback_id)
    return FEEDBACK_DIR / feedback_id


def create_feedback(data: dict) -> dict:
    feedback_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = FEEDBACK_DIR / feedback_id
    d.mkdir(parents=True)
    fb = {"id": feedback_id, "created": time.time(), "status": "processing", **data}
    _write(d / "feedback.json", fb)
    return fb


def get_feedback(feedback_id: str) -> dict:
    path = feedback_dir(feedback_id) / "feedback.json"
    if not path.exists():
        raise KeyError(feedback_id)
    return _read(path)


def update_feedback(feedback_id: str, **fields) -> dict:
    with _lock:
        fb = get_feedback(feedback_id)
        fb.update(fields)
        _write(feedback_dir(feedback_id) / "feedback.json", fb)
        return fb


def list_feedback() -> list[dict]:
    items = [_read(p) for p in FEEDBACK_DIR.glob("*/feedback.json")] if FEEDBACK_DIR.exists() else []
    return sorted(items, key=lambda f: f["created"], reverse=True)


def update_image(job_id: str, name: str, **fields):
    def fn(job):
        for img in job["images"]:
            if img["name"] == name:
                img.update(fields)

    return update_job(job_id, fn)
