"""Perspective geometry: homography → corners → Fusion CornerPin."""

from __future__ import annotations

import numpy as np

from matchref.perspective import CornerPin, corners_to_cornerpin, homography_corners

W, H = 1920, 1080


def test_identity_keeps_corners() -> None:
    corners = homography_corners(np.eye(3), W, H)
    assert corners == [(0.0, 0.0), (W, 0.0), (W, H), (0.0, H)]


def test_translation_shifts_all_corners() -> None:
    warp = np.array([[1.0, 0.0, 10.0], [0.0, 1.0, -5.0], [0.0, 0.0, 1.0]])
    corners = homography_corners(warp, W, H)
    assert corners[0] == (10.0, -5.0)
    assert corners[2] == (W + 10.0, H - 5.0)


def test_accepts_2x3_affine() -> None:
    warp = np.array([[2.0, 0.0, 0.0], [0.0, 2.0, 0.0]])  # affine, no perspective row
    corners = homography_corners(warp, W, H)
    assert corners[2] == (2 * W, 2 * H)


def test_perspective_row_applies_divide() -> None:
    # A non-zero perspective term makes the divide matter (far corners shrink).
    warp = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 1e-4, 1.0]])
    corners = homography_corners(warp, W, H)
    # bottom corners (y=H) get divided by (1 + 1e-4*H) > 1, so y shrinks below H.
    assert corners[2][1] < H
    assert corners[0] == (0.0, 0.0)  # top-left unaffected (y=0)


def test_cornerpin_normalizes_with_y_up() -> None:
    corners = [(0.0, 0.0), (W, 0.0), (W, H), (0.0, H)]
    cp = corners_to_cornerpin(corners, W, H)
    assert isinstance(cp, CornerPin)
    assert cp.top_left == (0.0, 1.0)       # pixel (0,0) → normalized (0,1) (Y up)
    assert cp.bottom_left == (0.0, 0.0)    # pixel (0,H) → normalized (0,0)
    assert cp.top_right == (1.0, 1.0)
    assert cp.bottom_right == (1.0, 0.0)
