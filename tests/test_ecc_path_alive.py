"""Guard the ECC alignment path itself — not just the geometry it returns.

Three latent bugs once made ``findTransformECC`` throw on every call (wrong
argument order), the pyramid crash on ``warp or feat_init`` (numpy truthiness),
and the sub-pixel phase-correlation refine crash on a (2,3)@(2,3) matmul. Each
was swallowed by a broad ``except`` and masked the next, so ``align_frames``
silently fell back to ORB feature matching while still reporting an "ecc_score".
These tests assert the ECC path actually runs and reports an ECC-derived match.
"""

import cv2
import numpy as np

from matchref.alignment import _pyramid_ecc, _run_ecc, align_frames
from matchref.config import AppConfig
from matchref.precision_align import refine_warp_phase_correlation


def _structured_frame(width: int = 640, height: int = 360) -> np.ndarray:
    img = np.zeros((height, width, 3), np.uint8)
    for k in range(0, width, 40):
        cv2.circle(img, (k, (k * 3) % height), 18, (int(k % 255), 120, 255 - int(k % 255)), -1)
    cv2.putText(img, "MATCHREF", (60, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 4)
    return img


def test_run_ecc_no_mask_does_not_raise() -> None:
    """The no-mask call must place gaussFiltSize in its real (7th) slot."""
    template = np.zeros((100, 100), np.float32)
    template[30:60, 30:60] = 1.0
    inp = np.zeros((100, 100), np.float32)
    inp[33:63, 32:62] = 1.0
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6)
    score, warp = _run_ecc(template, inp, cv2.MOTION_TRANSLATION, criteria, 5, None, None)
    assert score > 0.9
    # ~2px shift recovered (template box is offset from input box by (2, 3)).
    assert abs(float(warp[0, 2]) - 2.0) < 1.0


def test_phase_correlation_refine_composes_without_crashing() -> None:
    """The residual-translation compose must stay in homogeneous 3x3 space."""
    ref = _structured_frame()[:, :, 0].astype(np.uint8)
    moved = np.roll(ref, shift=(2, 3), axis=(0, 1))
    warp = np.eye(2, 3, dtype=np.float32)
    out = refine_warp_phase_correlation(ref, moved, warp)
    assert out.shape == (2, 3)
    assert np.isfinite(out).all()


def test_pyramid_ecc_runs_and_reports_ecc_path() -> None:
    """A clean translated pair must be solved by ECC, not the feature fallback."""
    online = _structured_frame()
    offline = cv2.warpAffine(online, np.float32([[1, 0, 5], [0, 1, 3]]), (640, 360))
    cfg = AppConfig()  # defaults: alignment_precision=maximum → pyramid path
    result = _pyramid_ecc(online, offline, cfg, timeline_width=640)
    assert result is not None
    assert result.ok
    assert "ecc" in result.message or "pyramid" in result.message
    assert "features" not in result.message


def test_align_frames_uses_ecc_for_clean_translation() -> None:
    online = _structured_frame()
    offline = cv2.warpAffine(online, np.float32([[1, 0, 5], [0, 1, 3]]), (640, 360))
    cfg = AppConfig()
    result = align_frames(online, offline, cfg, timeline_size=(640, 360))
    assert result.ok
    # Rigid ECC must recover ~identity scale on a pure translation (no zoom).
    assert abs(result.scale - 1.0) < 0.05
    assert "features" not in result.message
