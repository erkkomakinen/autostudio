"""Palvelun yleiset asetukset (data/settings.json), muokataan ylläpidon Asetukset-näkymässä."""
import json
import threading

from . import config

PATH = config.DATA_DIR / "settings.json"
# Vain testeissä hyväksytyt mallit (ks. TESTAUS.md)
MODELS = [
    {"id": "google/gemini-3.1-flash-image", "label": "Gemini 3.1 Flash Image – nopea, suositus"},
    {"id": "bytedance-seed/seedream-5-0-pro", "label": "Seedream 5 Pro – tarkin, hidas"},
]
MODEL_IDS = {m["id"] for m in MODELS}
DEFAULTS = {
    "usd_eur": 0.86,  # OpenRouter laskuttaa dollareina: kutsun hinta muunnetaan euroiksi tällä kurssilla
    "fallback_model": "bytedance-seed/seedream-5-0-pro",  # käytetään, jos tyylin malli ei vastaa
    "contact": "",  # palveluntarjoajan yhteystiedot asiakkaille (kuvausohje, pois käytöstä -viesti)
}
_lock = threading.Lock()


def get() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {**DEFAULTS, **data}


def usd_eur() -> float:
    return float(get()["usd_eur"])


def update(values: dict) -> dict:
    current = get()
    if "usd_eur" in values:
        rate = float(values["usd_eur"])
        if not 0.3 <= rate <= 2:
            raise ValueError("Kurssin pitää olla välillä 0,30–2,00")
        current["usd_eur"] = round(rate, 4)
    if "fallback_model" in values:
        model = str(values["fallback_model"] or "")
        if model and model not in MODEL_IDS:
            raise ValueError("Tuntematon malli")
        current["fallback_model"] = model
    if "contact" in values:
        current["contact"] = str(values["contact"] or "").strip()[:200]
    with _lock:
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        tmp.replace(PATH)
    return current
