"""Kertaluonteinen muunnos: vanhat dollarikulut (cost_total, ai.cost) euroiksi (cost_total_eur, ai.cost_eur).

Ajo: .venv/Scripts/python scripts/migrate_eur.py [kurssi]
Turvallinen ajaa uudelleen: jo muunnettuihin kuviin ei kosketa.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rate = float(sys.argv[1]) if len(sys.argv) > 1 else json.loads(
    (ROOT / "data" / "settings.json").read_text(encoding="utf-8")).get("usd_eur", 0.86) \
    if (ROOT / "data" / "settings.json").exists() else 0.86

changed = 0
for path in (ROOT / "data" / "jobs").glob("*/job.json"):
    job = json.loads(path.read_text(encoding="utf-8"))
    dirty = False
    for img in job["images"]:
        ai = img.get("ai") or {}
        if "cost" in ai:
            usd = ai.pop("cost")
            if usd is not None:
                ai["cost_eur"] = round(float(usd) * rate, 4)
            dirty = True
        if "cost_total" in img:
            img["cost_total_eur"] = round(float(img.pop("cost_total") or 0) * rate, 4)
            dirty = True
        elif int(img.get("ai_calls") or 0) == 0 and ai.get("cost_eur"):
            img["cost_total_eur"] = ai["cost_eur"]
            dirty = True
    if dirty:
        path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        changed += 1
print(f"Kurssi {rate}, muunnettu {changed} kuvaerää")
