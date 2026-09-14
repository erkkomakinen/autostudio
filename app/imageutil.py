from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

try:  # iPhonen HEIC-kuvat
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass


def load_image(path: Path) -> Image.Image:
    """Lataa kuvan ja kääntää sen EXIF-orientaation mukaan (puhelinkuvat)."""
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert("RGB")


def load_mask(path: Path) -> np.ndarray | None:
    if not path.exists():
        return None
    with Image.open(path) as im:
        return np.asarray(im.convert("L"), dtype=np.float32) / 255.0


def cover(img: Image.Image, width: int, height: int) -> Image.Image:
    """Skaalaa ja rajaa kuvan täyttämään annetun koon (CSS background-size: cover)."""
    return ImageOps.fit(img, (width, height), Image.LANCZOS)
