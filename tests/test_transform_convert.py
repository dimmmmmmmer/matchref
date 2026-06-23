"""Warp → Edit Inspector math (decomposition and center-relative translation)."""

from __future__ import annotations

import math

import numpy as np

from matchref.transform_convert import center_relative_translation, decompose_warp_matrix

W, H = 1920, 1080


def _center_scale_warp(s: float) -> np.ndarray:
    """Affine that scales by s about the frame center (Resolve's convention)."""
    cx, cy = W * 0.5, H * 0.5
    tx = cx - s * cx
    ty = cy - s * cy
    return np.array([[s, 0.0, tx], [0.0, s, ty]], dtype=np.float64)


def test_decompose_pure_corner_scale() -> None:
    warp = np.array([[1.5, 0.0, 0.0], [0.0, 1.5, 0.0]])
    scale, tx, ty, rot = decompose_warp_matrix(warp, W, H)
    assert scale == 1.5
    assert (tx, ty) == (0.0, 0.0)
    assert rot == 0.0


def test_decompose_translation_only() -> None:
    warp = np.array([[1.0, 0.0, 40.0], [0.0, 1.0, -25.0]])
    scale, tx, ty, rot = decompose_warp_matrix(warp, W, H)
    assert scale == 1.0
    assert (tx, ty) == (40.0, -25.0)
    assert rot == 0.0


def test_decompose_rotation() -> None:
    theta = math.radians(5.0)
    c, s = math.cos(theta), math.sin(theta)
    warp = np.array([[c, -s, 0.0], [s, c, 0.0]])
    scale, _tx, _ty, rot = decompose_warp_matrix(warp, W, H)
    assert abs(scale - 1.0) < 1e-9
    assert abs(rot - 5.0) < 1e-6


def test_center_scale_has_no_pan_tilt() -> None:
    # A pure center zoom carries a large corner translation but zero Pan/Tilt.
    pan, tilt = center_relative_translation(_center_scale_warp(1.4), W, H)
    assert abs(pan) < 1e-6
    assert abs(tilt) < 1e-6


def test_center_relative_translation_recovers_pan() -> None:
    # Pure translation: center-relative pan/tilt equal the raw translation.
    warp = np.array([[1.0, 0.0, 30.0], [0.0, 1.0, 12.0]])
    pan, tilt = center_relative_translation(warp, W, H)
    assert abs(pan - 30.0) < 1e-6
    assert abs(tilt - 12.0) < 1e-6


def test_decompose_accepts_3x3() -> None:
    warp = np.array([[2.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 1.0]])
    scale, _tx, _ty, _rot = decompose_warp_matrix(warp, W, H)
    assert scale == 2.0
