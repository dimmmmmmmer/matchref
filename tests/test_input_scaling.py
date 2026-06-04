"""Input scaling modes (Resolve 'Mismatched Resolution' base placement)."""

import numpy as np

from matchref.clip_edit_transform import _fit_frame_to_canvas

# 16:9 source onto a 2:1 canvas — the case that bit the firesense project.
_SRC = np.full((1080, 1920, 3), 200, np.uint8)
_W, _H = 800, 400  # 2:1


def _nonblack_columns(canvas: np.ndarray) -> int:
    return int(np.count_nonzero(canvas.max(axis=(0, 2)) > 0))


def test_fit_pillarboxes() -> None:
    out = _fit_frame_to_canvas(_SRC, _W, _H, "fit")
    # 16:9 fit into 2:1 fills height, leaves pillarbox bars → not all columns covered.
    assert _nonblack_columns(out) < _W


def test_fill_covers_whole_canvas() -> None:
    out = _fit_frame_to_canvas(_SRC, _W, _H, "fill")
    assert _nonblack_columns(out) == _W  # no bars
    assert out.shape == (_H, _W, 3)


def test_fill_fit_scale_ratio_is_aspect_ratio() -> None:
    # For 16:9 into 2:1 the fill/fit ratio is (2.0 / 1.7778) = 1.125 — the exact
    # factor MatchRef used to add to every clip's Zoom under the wrong base.
    fit_scale = min(_W / 1920, _H / 1080)
    fill_scale = max(_W / 1920, _H / 1080)
    assert abs(fill_scale / fit_scale - 1.125) < 1e-6


def test_stretch_and_none_shapes() -> None:
    assert _fit_frame_to_canvas(_SRC, _W, _H, "stretch").shape == (_H, _W, 3)
    assert _fit_frame_to_canvas(_SRC, _W, _H, "none").shape == (_H, _W, 3)
