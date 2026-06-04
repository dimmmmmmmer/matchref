"""Grade-normalised difference map used for debug verification."""

import cv2
import numpy as np

from matchref.debug_frames import _normalized_difference


def _frame() -> np.ndarray:
    rng = np.random.default_rng(5)
    img = (rng.random((240, 320, 3)) * 60 + 20).astype(np.uint8)
    cv2.rectangle(img, (80, 60), (200, 170), (210, 180, 70), -1)
    return img


def test_identical_is_mostly_black() -> None:
    img = _frame()
    _, frac = _normalized_difference(img, img)
    assert frac > 0.95


def test_grade_difference_still_aligns() -> None:
    img = _frame()
    graded = np.clip(img.astype(np.float32) * [0.7, 1.0, 1.4], 0, 255).astype(np.uint8)
    _, frac = _normalized_difference(img, graded)
    assert frac > 0.8  # CLAHE cancels the grade → still mostly black when aligned


def test_shift_lowers_near_black() -> None:
    img = _frame()
    shifted = cv2.warpAffine(img, np.float32([[1, 0, 40], [0, 1, 0]]), (320, 240))
    _, aligned = _normalized_difference(img, img)
    _, misaligned = _normalized_difference(shifted, img)
    assert misaligned < aligned
