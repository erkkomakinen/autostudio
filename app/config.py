"""Yleiset asetukset. Ympäristömuuttujilla voi ohittaa oletukset palvelimella."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("AUTOSTUDIO_DATA", ROOT / "data"))
ASSETS_DIR = ROOT / "assets"

# Mallit ladataan projektin sisään (ei C:-levyn käyttäjäkansioon)
os.environ.setdefault("HF_HOME", str(ROOT / "models"))
os.environ.setdefault("TORCH_HOME", str(ROOT / "models" / "torch"))  # GeoCalib-painot

# Auton rajaus: BiRefNet (MIT). Vaihtoehdot: ZhengPeng7/BiRefNet, ZhengPeng7/BiRefNet_HR (vaatii ~8 Gt VRAM)
SEGMENT_MODEL = os.environ.get("AUTOSTUDIO_SEGMENT_MODEL", "ZhengPeng7/BiRefNet")
SEGMENT_SIZE = int(os.environ.get("AUTOSTUDIO_SEGMENT_SIZE", "1024"))
# Peilikuvakeskiarvo: hieman siistimmät reunat, kaksinkertainen rajausaika. Pois CPU-palvelimella.
FLIP_TTA = os.environ.get("AUTOSTUDIO_FLIP_TTA", "1") == "1"

# Ikkunat: Grounding DINO (Apache 2.0) löytää ikkunat, SAM 2.1 (Apache 2.0) rajaa ne tarkasti
WINDOW_DETECTOR = os.environ.get("AUTOSTUDIO_WINDOW_DETECTOR", "IDEA-Research/grounding-dino-base")
WINDOW_SEGMENTER = os.environ.get("AUTOSTUDIO_WINDOW_SEGMENTER", "facebook/sam2.1-hiera-large")

DEVICE = os.environ.get("AUTOSTUDIO_DEVICE", "")  # tyhjä = cuda jos saatavilla

DEALERS_DIR = DATA_DIR / "dealers"
JOBS_DIR = DATA_DIR / "jobs"
for d in (DEALERS_DIR, JOBS_DIR):
    d.mkdir(parents=True, exist_ok=True)
