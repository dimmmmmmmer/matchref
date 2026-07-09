"""Grade-robust grayscale preparation of online/offline frame pairs for matching."""

from __future__ import annotations

import cv2
import numpy as np

from matchref.config import AppConfig
from matchref.overlay_crop import (
    ContentCrop,
    content_crop_for_shape,
    ecc_match_mask,
    overlay_margins_fraction,
)


def _validate_gray(gray: np.ndarray, label: str) -> str | None:
    if gray is None or gray.size == 0:
        return f"{label}: empty image"
    if gray.ndim != 2:
        return f"{label}: expected 2D grayscale"
    h, w = gray.shape[:2]
    if h < 32 or w < 32:
        return f"{label}: image too small ({w}x{h})"
    if not np.isfinite(gray).all():
        return f"{label}: non-finite pixels"
    if float(np.std(gray)) < 1e-4:
        return f"{label}: flat/uniform frame"
    return None


def _to_gray(image: np.ndarray, max_width: int) -> np.ndarray:
    if image is None or image.size == 0:
        raise ValueError("empty frame")
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = np.ascontiguousarray(image)
    height, width = gray.shape[:2]
    if max_width > 0 and width > max_width:
        scale = max_width / float(width)
        gray = cv2.resize(
            gray,
            (int(width * scale), int(height * scale)),
            interpolation=cv2.INTER_AREA,
        )
    return np.ascontiguousarray(gray)


def _even_crop(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape[:2]
    return gray[: h // 2 * 2, : w // 2 * 2]


def _match_size(online: np.ndarray, offline: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if online.shape == offline.shape:
        return online, offline
    offline = cv2.resize(
        offline,
        (online.shape[1], online.shape[0]),
        interpolation=cv2.INTER_AREA,
    )
    return online, offline


def prepare_gray_uint8(
    image: np.ndarray,
    max_width: int,
    *,
    use_clahe: bool = True,
    edge_emphasis: float = 0.0,
) -> np.ndarray:
    gray: np.ndarray = _even_crop(_to_gray(image, max_width)).astype(np.uint8)
    if use_clahe:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
    if edge_emphasis > 0.0:
        # Blend in Sobel gradient magnitude. Edges survive a colour grade almost
        # unchanged, so this makes the correlation depend on shared structure
        # rather than absolute luminance (which the offline grade shifts wildly).
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        cv2.normalize(mag, mag, 0.0, 255.0, cv2.NORM_MINMAX)
        w = min(1.0, max(0.0, edge_emphasis))
        gray = cv2.addWeighted(gray.astype(np.float32), 1.0 - w, mag, w, 0.0)
        gray = np.clip(gray, 0.0, 255.0).astype(np.uint8)
    return gray


def _prepare_match_pair(
    online_frame: np.ndarray,
    offline_frame: np.ndarray,
    max_width: int,
    config: AppConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, ContentCrop]:
    """Crop overlay margins on both frames; ECC mask only needed when not pre-cropped."""
    margins = overlay_margins_fraction(config)
    # The offline lock-cut is colour-graded; the online source is raw/log. Matching
    # on raw luminance makes ECC score low for clips that are geometrically identical
    # (e.g. a fire scene that was never reframed), so the grade-robust refine never
    # runs. CLAHE (local-contrast equalisation) + optional edge emphasis cancel the
    # grade so the admission score reflects geometry, not colour.
    use_clahe = bool(config.get("match_use_clahe", True))
    edge_emphasis = float(config.get("match_edge_emphasis", 0.0))
    off_gray = prepare_gray_uint8(
        offline_frame, max_width, use_clahe=use_clahe, edge_emphasis=edge_emphasis
    )
    on_gray = prepare_gray_uint8(
        online_frame, max_width, use_clahe=use_clahe, edge_emphasis=edge_emphasis
    )
    on_gray, off_gray = _match_size(on_gray, off_gray)
    crop = content_crop_for_shape(off_gray.shape[0], off_gray.shape[1], margins)
    full_frame = (
        crop.x0 == 0
        and crop.y0 == 0
        and crop.x1 == off_gray.shape[1]
        and crop.y1 == off_gray.shape[0]
    )
    if full_frame:
        mask = (
            ecc_match_mask(off_gray.shape, crop)
            if bool(config.get("ecc_use_overlay_mask", True))
            else None
        )
        return on_gray, off_gray, mask, crop
    on_crop = on_gray[crop.y0 : crop.y1, crop.x0 : crop.x1]
    off_crop = off_gray[crop.y0 : crop.y1, crop.x0 : crop.x1]
    return on_crop, off_crop, None, crop
