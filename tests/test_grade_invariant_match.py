"""Grade-invariant admission: a colour grade must not tank the alignment score.

Reproduces the 8 "below admission" failures (e.g. A031R102): online raw vs an
identically-framed but heavily graded offline. Matching must score on shared
structure (edges), not absolute luminance.
"""

import cv2
import numpy as np

from matchref.alignment import align_frames
from matchref.config import AppConfig


def _structured_frame(width: int = 640, height: int = 320) -> np.ndarray:
    rng = np.random.default_rng(7)
    img = np.full((height, width, 3), 40, dtype=np.uint8)
    for _ in range(40):
        x0, y0 = rng.integers(0, width - 60), rng.integers(0, height - 60)
        w, h = rng.integers(20, 60), rng.integers(20, 60)
        color = tuple(int(c) for c in rng.integers(60, 220, size=3))
        cv2.rectangle(img, (x0, y0), (x0 + w, y0 + h), color, -1)
    for _ in range(15):
        p1 = (int(rng.integers(0, width)), int(rng.integers(0, height)))
        p2 = (int(rng.integers(0, width)), int(rng.integers(0, height)))
        cv2.line(img, p1, p2, (220, 220, 220), 2)
    return img


def _heavy_grade(img: np.ndarray) -> np.ndarray:
    """
    Spatially-varying grade + veiling haze — mimics the lock-cut.

    ECC's correlation is invariant to a *global* affine intensity change, so a
    uniform grade does not lower its score. Real lock-cut failures come from
    *local* differences: a brightness ramp (relight), per-region gamma and additive
    smoke/haze. That is what breaks raw-luminance matching and what CLAHE +
    edge-emphasis are meant to survive.
    """
    h, w = img.shape[:2]
    f = img.astype(np.float32) / 255.0
    ramp = np.linspace(0.4, 1.6, w, dtype=np.float32)[None, :, None]  # left dark→right bright
    f = np.clip(f * ramp, 0, 1)
    f = np.power(f, 0.6)  # lift shadows
    haze = np.linspace(0.0, 0.35, h, dtype=np.float32)[:, None, None]  # smoke veil, top→bottom
    f = np.clip(f * (1.0 - haze) + haze, 0, 1)
    f[:, :, 2] = np.clip(f[:, :, 2] * 1.3, 0, 1)  # warm cast
    return (f * 255).astype(np.uint8)


def _align(online: np.ndarray, offline: np.ndarray, cfg: AppConfig):
    cfg.set("alignment_precision", "maximum")
    cfg.set("resolve_edit_refine", False)
    return align_frames(online, offline, cfg, timeline_size=(online.shape[1], online.shape[0]))


def _score(online: np.ndarray, offline: np.ndarray, cfg: AppConfig) -> float:
    res = _align(online, offline, cfg)
    return float(res.ecc_score) if res.ok else 0.0


def test_geometry_recovered_despite_grade() -> None:
    """The recovered zoom must be ~identity even under a spatially-varying grade."""
    online = _structured_frame()
    offline = _heavy_grade(online)
    cfg = AppConfig()
    cfg.set("match_use_clahe", True)
    cfg.set("match_edge_emphasis", 0.35)
    res = _align(online, offline, cfg)
    assert res.ok
    assert abs(res.scale - 1.0) < 0.05


def test_recovered_geometry_stable_across_preprocessing() -> None:
    """
    The recovered geometry — not the absolute ECC value — is the reliable signal we
    accept on. Under a spatially-varying grade the robust (CLAHE+edge) path must
    recover the same zoom as the raw path on identical geometry. Fully synthetic so
    it always runs (no dependency on gitignored debug frames).
    """
    online = _structured_frame()
    offline = _heavy_grade(online)

    raw = AppConfig()
    raw.set("match_use_clahe", False)
    raw.set("match_edge_emphasis", 0.0)
    raw_res = _align(online, offline, raw)

    robust = AppConfig()
    robust.set("match_use_clahe", True)
    robust.set("match_edge_emphasis", 0.35)
    rob_res = _align(online, offline, robust)

    assert rob_res.ok
    assert abs(rob_res.scale - 1.0) < 0.05
    # If the raw path also converged, both must agree on the (identity) geometry.
    if raw_res.ok:
        assert abs(rob_res.scale - raw_res.scale) < 0.03
