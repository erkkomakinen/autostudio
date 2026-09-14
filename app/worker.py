"""Taustakäsittely.

GPU-analyysi ajetaan yksi kuva kerrallaan (yksi näytönohjain). Tekoälytaustat ovat verkkokutsuja,
joten ne ajetaan rinnakkain erillisessä säiejoukossa: 12 kuvan erä valmistuu noin puolessa minuutissa.
"""
import logging
import queue
import shutil
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import notify, pipeline, storage

log = logging.getLogger("autostudio.worker")
_queue: "queue.Queue[tuple]" = queue.Queue()
_ai_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ai")
_started = False


def enqueue(job_id: str, name: str):
    _queue.put((job_id, name, None, None))


def enqueue_fix(job_id: str, name: str, prompt: str | None, feedback_id: str | None):
    storage.update_image(job_id, name, status="queued", error=None)
    _queue.put((job_id, name, prompt, feedback_id))


def render_image(job_id: str, name: str) -> str:
    """Ylläpidon "päivitä ulkoasun mukaan": viimeistely ilman tekoälykutsua."""
    pipeline.refinish(job_id, name)
    return f"{name}.jpg"


def _fail(job_id: str, name: str, error: Exception, feedback_id: str | None):
    log.error("Kuvan %s/%s käsittely epäonnistui:\n%s", job_id, name, traceback.format_exc())
    try:
        storage.update_image(job_id, name, status="error", error=str(error)[:300])
        if feedback_id:
            storage.update_feedback(feedback_id, status="error", error=str(error)[:300])
    except Exception:  # noqa: BLE001
        pass


def _generate(job_id: str, name: str, prompt: str | None, feedback_id: str | None):
    try:
        pipeline.generate(job_id, name, extra_prompt=prompt)
        storage.update_image(job_id, name, status="done", error=None)
        if feedback_id:
            _feedback_done(feedback_id, job_id, name)
    except Exception as e:  # noqa: BLE001
        _fail(job_id, name, e, feedback_id)


def _feedback_done(feedback_id: str, job_id: str, name: str):
    fdir = storage.feedback_dir(feedback_id)
    shutil.copy(storage.job_dir(job_id) / "output" / f"{name}.jpg", fdir / "after.jpg")
    fb = storage.update_feedback(feedback_id, status="done")
    body = (
        f"Liike: {fb['dealer_name']}\n"
        f"Erä: {fb['job_title']}\n"
        f"Kuva: {fb['original_name']}\n\n"
        f"Asiakkaan pyyntö:\n{fb['prompt']}\n\n"
        "Liitteinä alkuperäinen kuva, kuva ennen korjausta ja korjattu kuva."
    )
    sent = notify.send(
        f"Autostudio: korjauspyyntö – {fb['dealer_name']}",
        body,
        [(fdir / "original.jpg", "alkuperainen.jpg"), (fdir / "before.jpg", "ennen.jpg"), (fdir / "after.jpg", "korjattu.jpg")],
    )
    storage.update_feedback(feedback_id, notified=sent)


def _run():
    while True:
        job_id, name, prompt, feedback_id = _queue.get()
        try:
            storage.update_image(job_id, name, status="analyzing", error=None)
            pipeline.analyze(job_id, name)
            storage.update_image(job_id, name, status="generating")
            _ai_pool.submit(_generate, job_id, name, prompt, feedback_id)
        except Exception as e:  # noqa: BLE001 - virhe tallennetaan kuvalle, jono jatkaa
            _fail(job_id, name, e, feedback_id)
        finally:
            _queue.task_done()


def start():
    global _started
    if _started:
        return
    _started = True
    # Palvelimen uudelleenkäynnistyksessä kesken jääneet kuvat takaisin jonoon
    for job in reversed(storage.list_jobs()):
        for img in job["images"]:
            if img["status"] in ("queued", "analyzing", "generating") and not img.get("deleted"):
                enqueue(job["id"], img["name"])
    threading.Thread(target=_run, daemon=True, name="gpu-worker").start()
