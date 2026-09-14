"""AI-mallit: auton rajaus (BiRefNet) ja ikkunoiden tunnistus (Grounding DINO + SAM 2.1).

Mallit ladataan laiskasti ensimmäisellä käyttökerralla ja pidetään muistissa.
Kaikki mallit ovat kaupalliseen käyttöön sopivilla lisensseillä (MIT / Apache 2.0).
"""
import threading

import numpy as np
import torch
from PIL import Image

from . import config

_lock = threading.Lock()
_segmenter = None
_window_detector = None


def device():
    if config.DEVICE:
        return config.DEVICE
    return "cuda" if torch.cuda.is_available() else "cpu"


class CarSegmenter:
    def __init__(self):
        from transformers import AutoModelForImageSegmentation

        self.device = device()
        self.half = self.device == "cuda"
        self.size = config.SEGMENT_SIZE
        self.model = AutoModelForImageSegmentation.from_pretrained(config.SEGMENT_MODEL, trust_remote_code=True)
        self.model.to(self.device).eval()
        if self.half:
            self.model.half()
        self.mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)

    def _predict(self, x):
        pred = self.model(x)[-1].sigmoid().float()
        return pred

    @torch.inference_mode()
    def alpha(self, img: Image.Image, flip_tta: bool | None = None) -> np.ndarray:
        """Palauttaa alfan (float32 0..1) alkuperäisessä resoluutiossa."""
        if flip_tta is None:
            flip_tta = config.FLIP_TTA
        rgb = img.convert("RGB").resize((self.size, self.size), Image.BICUBIC)
        x = torch.from_numpy(np.array(rgb)).permute(2, 0, 1).unsqueeze(0).float() / 255.0
        x = ((x - self.mean) / self.std).to(self.device)
        if self.half:
            x = x.half()
        pred = self._predict(x)
        if flip_tta:
            # Peilikuvan keskiarvo tasoittaa reunoja pienellä lisäkustannuksella
            pred = (pred + self._predict(torch.flip(x, dims=[3])).flip(dims=[3])) / 2
        pred = torch.nn.functional.interpolate(pred, size=(img.height, img.width), mode="bilinear", align_corners=False)
        return pred[0, 0].clamp(0, 1).cpu().numpy()


class WindowDetector:
    PROMPT = "car window. windshield."

    def __init__(self):
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor, Sam2Model, Sam2Processor

        self.device = device()
        self.dino_proc = AutoProcessor.from_pretrained(config.WINDOW_DETECTOR)
        self.dino = AutoModelForZeroShotObjectDetection.from_pretrained(config.WINDOW_DETECTOR).to(self.device).eval()
        self.sam_proc = Sam2Processor.from_pretrained(config.WINDOW_SEGMENTER)
        self.sam = Sam2Model.from_pretrained(config.WINDOW_SEGMENTER).to(self.device).eval()

    @torch.inference_mode()
    def boxes(self, img: Image.Image, car_box, threshold=0.28):
        inputs = self.dino_proc(images=img, text=self.PROMPT, return_tensors="pt").to(self.device)
        out = self.dino(**inputs)
        res = self.dino_proc.post_process_grounded_object_detection(
            out, inputs.input_ids, threshold=threshold, text_threshold=0.25, target_sizes=[(img.height, img.width)]
        )[0]
        cx0, cy0, cx1, cy1 = car_box
        car_area = max(1, (cx1 - cx0) * (cy1 - cy0))
        kept = []
        for box, score in zip(res["boxes"].tolist(), res["scores"].tolist()):
            x0, y0, x1, y1 = box
            area = (x1 - x0) * (y1 - y0)
            # Hylätään koko auton kattavat laatikot ja auton ulkopuoliset osumat
            if area > 0.45 * car_area:
                continue
            ix = max(0, min(x1, cx1) - max(x0, cx0))
            iy = max(0, min(y1, cy1) - max(y0, cy0))
            if ix * iy < 0.7 * area:
                continue
            kept.append((box, score))
        return kept

    @torch.inference_mode()
    def masks(self, img: Image.Image, car_box) -> tuple[list[np.ndarray], list]:
        """Palauttaa jokaisen löydetyn ikkunan oman binäärimaskin (bool HxW)."""
        found = self.boxes(img, car_box)
        if not found:
            return [], []
        boxes = [b for b, _ in found]
        inputs = self.sam_proc(images=img, input_boxes=[boxes], return_tensors="pt").to(self.device)
        # Kolme maskiehdotusta per ikkuna, valitaan SAM:n oman laatuarvion mukaan paras
        out = self.sam(**inputs, multimask_output=True)
        masks = self.sam_proc.post_process_masks(out.pred_masks.cpu(), inputs["original_sizes"].cpu())[0]
        masks = masks.reshape(len(boxes), -1, img.height, img.width)
        best = out.iou_scores.reshape(len(boxes), -1).argmax(dim=1).cpu()
        chosen = [masks[i, best[i]].numpy() for i in range(len(boxes))]
        return chosen, found

    @torch.inference_mode()
    def car_score(self, img: Image.Image, mask_box) -> float:
        """Varatarkistus: löytääkö Grounding DINO auton, joka vastaa maskin rajausta (IoU * score)."""
        inputs = self.dino_proc(images=img, text="car.", return_tensors="pt").to(self.device)
        out = self.dino(**inputs)
        res = self.dino_proc.post_process_grounded_object_detection(
            out, inputs.input_ids, threshold=0.35, text_threshold=0.25, target_sizes=[(img.height, img.width)]
        )[0]
        best = 0.0
        for box, score in zip(res["boxes"].tolist(), res["scores"].tolist()):
            best = max(best, _iou(box, mask_box) * score)
        return best

    @torch.inference_mode()
    def wheels(self, img: Image.Image, car_box) -> list[list[float]]:
        """Pyörien rajauslaatikot auton sisältä: niiden alareuna = renkaan kosketuskohta lattiaan."""
        inputs = self.dino_proc(images=img, text="wheel.", return_tensors="pt").to(self.device)
        out = self.dino(**inputs)
        res = self.dino_proc.post_process_grounded_object_detection(
            out, inputs.input_ids, threshold=0.3, text_threshold=0.25, target_sizes=[(img.height, img.width)]
        )[0]
        cx0, cy0, cx1, cy1 = car_box
        car_area = max(1, (cx1 - cx0) * (cy1 - cy0))
        found = []
        for box, score in sorted(zip(res["boxes"].tolist(), res["scores"].tolist()), key=lambda b: -b[1]):
            x0, y0, x1, y1 = box
            w, h = x1 - x0, y1 - y0
            if w * h > 0.15 * car_area or not (0.25 < w / max(h, 1) < 2.5):
                continue
            # Renkaan alareunan pitää olla auton alaosassa (hylkää esim. ratin ohjaamossa).
            # Kauempi pyörä on perspektiivin takia kuvassa ylempänä, joten raja on auton puolivälin alapuolella.
            if y1 < cy1 - 0.45 * (cy1 - cy0):
                continue
            ix = max(0, min(x1, cx1) - max(x0, cx0))
            iy = max(0, min(y1, cy1) - max(y0, cy0))
            if ix * iy < 0.7 * w * h:
                continue
            if any(_iou(box, f) > 0.3 for f in found):
                continue
            found.append([round(v, 1) for v in box])
        return found[:4]


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


class CameraCalibrator:
    """GeoCalib (koodi Apache 2.0, painot CC BY 4.0): polttoväli, kallistus ja kiertymä yhdestä kuvasta."""

    def __init__(self):
        from geocalib import GeoCalib

        self.device = device()
        self.model = GeoCalib().to(self.device).eval()

    @torch.inference_mode()
    def estimate(self, img: Image.Image) -> dict:
        x = torch.from_numpy(np.array(img)).permute(2, 0, 1).float().div(255).to(self.device)
        r = self.model.calibrate(x)
        f = r["camera"].f.squeeze().tolist()
        c = r["camera"].c.squeeze().tolist()
        roll, pitch = r["gravity"].rp.squeeze().tolist()
        # GeoCalibissa negatiivinen pitch = kamera katsoo alaspäin; scene.py käyttää + = alaspäin
        return {"f": float(f[1]), "cx": float(c[0]), "cy": float(c[1]), "pitch": float(-pitch), "roll": float(roll)}


_calibrator = None


def calibrator() -> CameraCalibrator:
    global _calibrator
    with _lock:
        if _calibrator is None:
            _calibrator = CameraCalibrator()
    return _calibrator


def segmenter() -> CarSegmenter:
    global _segmenter
    with _lock:
        if _segmenter is None:
            _segmenter = CarSegmenter()
    return _segmenter


def window_detector() -> WindowDetector:
    global _window_detector
    with _lock:
        if _window_detector is None:
            _window_detector = WindowDetector()
    return _window_detector
