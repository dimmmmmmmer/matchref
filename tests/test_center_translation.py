"""Pan/Tilt recovery from an affine warp (center-relative, Resolve convention)."""

import numpy as np

from matchref.clip_edit_transform import _edit_warp_matrix
from matchref.transform_convert import center_relative_translation

_W, _H = 1920, 1080


def _warp(zoom: float, pan: float, tilt: float, rot: float = 0.0) -> np.ndarray:
    # _edit_warp_matrix is the simulation that mirrors how Resolve applies the
    # Edit transform, so it is the ground truth for the inverse we want to test.
    return _edit_warp_matrix(zoom, pan, tilt, rot, _W, _H, invert_tilt_y=False)


def test_pure_zoom_has_no_pan() -> None:
    pan, tilt = center_relative_translation(_warp(1.25, 0.0, 0.0), _W, _H)
    assert abs(pan) < 1e-6
    assert abs(tilt) < 1e-6


def test_pure_translation_at_unit_zoom() -> None:
    pan, tilt = center_relative_translation(_warp(1.0, 40.0, -25.0), _W, _H)
    assert abs(pan - 40.0) < 1e-6
    assert abs(tilt + 25.0) < 1e-6


def test_zoom_and_translation_combined() -> None:
    pan, tilt = center_relative_translation(_warp(1.3, 60.0, 30.0), _W, _H)
    assert abs(pan - 60.0) < 1e-4
    assert abs(tilt - 30.0) < 1e-4


def test_corner_translation_differs_from_pan_under_zoom() -> None:
    # Regression: the raw corner translation is large for a centered zoom even
    # though the true Pan is zero, which is exactly what used to be mis-applied.
    warp = _warp(1.5, 0.0, 0.0)
    corner_tx = float(warp[0, 2])
    assert abs(corner_tx) > 100.0  # corner moves a lot
    pan, _ = center_relative_translation(warp, _W, _H)
    assert abs(pan) < 1e-6  # but center pan is zero
