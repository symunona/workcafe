"""
image_utils.py
==============
Shared image save utility for all scrapers.

save_image(img_bytes, save_path) -> (actual_path, meta)
  - Always outputs JPEG at quality 75
  - Resizes longest side to MAX_SIDE (1024px) if larger
  - Returns actual save path (always .jpg) and meta dict
    with keys: width, height, file_size
  - On decode failure: writes raw bytes, returns original path, None meta values
"""

import os
from io import BytesIO
from PIL import Image

MAX_SIDE = 1024
QUALITY  = 75


def save_image(
    img_bytes: bytes,
    save_path: str,
    max_side: int = MAX_SIDE,
    quality: int = QUALITY,
) -> tuple[str, dict]:
    """
    Decode img_bytes, resize longest side to max_side (only if larger),
    save as JPEG quality=quality. The extension in save_path is replaced
    with .jpg regardless of input format.

    Returns (actual_save_path, meta) where meta = {width, height, file_size}.
    On Pillow decode failure, falls back to writing raw bytes with the original
    extension — callers must use the returned path, not the input save_path.
    """
    jpg_path = os.path.splitext(save_path)[0] + '.jpg'

    try:
        img = Image.open(BytesIO(img_bytes))

        # JPEG can't store palette or alpha — convert to RGB
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')

        w, h = img.size
        if max(w, h) > max_side:
            if w >= h:
                nw, nh = max_side, int(h * max_side / w)
            else:
                nw, nh = int(w * max_side / h), max_side
            img = img.resize((nw, nh), Image.LANCZOS)

        out = BytesIO()
        img.save(out, format='JPEG', quality=quality, optimize=True)
        data = out.getvalue()

        with open(jpg_path, 'wb') as f:
            f.write(data)

        w, h = img.size
        return jpg_path, {'width': w, 'height': h, 'file_size': len(data)}

    except Exception:
        with open(save_path, 'wb') as f:
            f.write(img_bytes)
        return save_path, {'width': None, 'height': None, 'file_size': len(img_bytes)}
