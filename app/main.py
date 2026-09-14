"""HTTP-palvelin: asiakasnäkymä /d/<token> ja ylläpito /admin.

Käynnistys: .venv/Scripts/python -m uvicorn app.main:app --port 8765
Ylläpidon kirjautuminen: AUTOSTUDIO_USER ja AUTOSTUDIO_PASSWORD (HTTP Basic).
Ilman niitä ylläpito on avoin, joten ne on asetettava aina palvelimella.
"""
import base64
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import admin_api, admin_styles, dealer_api, worker

STATIC = Path(__file__).parent / "static"
USER = os.environ.get("AUTOSTUDIO_USER")
PASSWORD = os.environ.get("AUTOSTUDIO_PASSWORD")
PUBLIC_PREFIXES = ("/d/", "/api/d/", "/static/")  # asiakkaan linkki tunnistaa itse, ei ylläpidon salasanaa

app = FastAPI(title="Autostudio")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(dealer_api.router)
app.include_router(admin_api.router)
app.include_router(admin_styles.router)


@app.on_event("startup")
def _startup():
    worker.start()


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if USER and PASSWORD and not request.url.path.startswith(PUBLIC_PREFIXES):
        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, USER) and secrets.compare_digest(pw, PASSWORD)
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Autostudio"'})
    return await call_next(request)


@app.get("/")
def index():
    return RedirectResponse("/admin")


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC / "admin.html", headers={"Cache-Control": "no-cache"})


@app.exception_handler(KeyError)
def _key_error(_request, _exc):
    return JSONResponse({"detail": "Ei löytynyt"}, status_code=404)
