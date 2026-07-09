"""Save side-by-side frames and comparison notes for troubleshooting."""

from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np

from matchref.config import AppConfig


def resolve_debug_dir(config: AppConfig) -> Path | None:
    if not bool(config.get("debug_save_frames", False)):
        return None
    raw = str(config.get("debug_output_dir", "")).strip()
    if raw:
        path = Path(raw).expanduser()
    else:
        path = Path.home() / "Documents" / "matchref" / "debug"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(value: str, max_len: int = 48) -> str:
    cleaned = re.sub(r"[^\w\-.]+", "_", value.strip()) or "clip"
    return cleaned[:max_len]


def _stack_horizontal(left: np.ndarray, right: np.ndarray, gap: int = 8) -> np.ndarray:
    h = max(left.shape[0], right.shape[0])

    def pad(img: np.ndarray) -> np.ndarray:
        out = np.zeros((h, img.shape[1], 3), dtype=np.uint8)
        y0 = (h - img.shape[0]) // 2
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        out[y0 : y0 + img.shape[0], :] = img
        return out

    left_p = pad(left)
    right_p = pad(right)
    sep = np.zeros((h, gap, 3), dtype=np.uint8)
    sep[:] = (40, 40, 40)
    return np.hstack([left_p, sep, right_p])


def _normalized_difference(
    result: np.ndarray, offline: np.ndarray, *, near_black: int = 24
) -> tuple[np.ndarray, float]:
    """
    Grade-independent |result - offline|: grayscale, local-contrast equalised
    (CLAHE) so the colour grade cancels, then absolute difference. Dark = aligned.
    Returns (diff image, fraction of near-black pixels).
    """
    gr = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
    go = cv2.cvtColor(offline, cv2.COLOR_BGR2GRAY)
    if gr.shape != go.shape:
        go = cv2.resize(go, (gr.shape[1], gr.shape[0]), interpolation=cv2.INTER_AREA)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    diff = cv2.absdiff(clahe.apply(gr), clahe.apply(go))
    frac = float((diff < near_black).mean())
    return diff, frac


def save_match_debug(
    debug_dir: Path,
    *,
    clip_name: str,
    sample_point: str,
    online_for_match: np.ndarray,
    offline_ref: np.ndarray,
    online_raw: np.ndarray | None = None,
    result_render: np.ndarray | None = None,
    mapping_detail: str = "",
    extra_lines: list[str] | None = None,
) -> str:
    """
    Write comparison JPEG + text manifest. Returns path to the image.

    Layout: [online for match | offline reference]
    """
    stamp = _safe_name(clip_name)
    point = _safe_name(sample_point, 12)
    base = debug_dir / stamp / point
    base.mkdir(parents=True, exist_ok=True)

    compare = _stack_horizontal(online_for_match, offline_ref)
    image_path = base / "compare_online_vs_offline.jpg"
    cv2.imwrite(str(image_path), compare)

    diff_line = _write_result_images(base, result_render, offline_ref)
    raw_line = _write_raw_image(base, online_raw, online_for_match)
    cv2.imwrite(str(base / "online_for_match.jpg"), online_for_match)
    cv2.imwrite(str(base / "offline_reference.jpg"), offline_ref)

    lines = [
        f"clip: {clip_name}",
        f"sample: {sample_point}",
        "compare: online (left) vs offline lock-cut (right)",
        f"mapping: {mapping_detail}",
        f"image: {image_path}",
    ]
    if raw_line:
        lines.append(raw_line)
    if diff_line:
        lines.append(diff_line)
    if extra_lines:
        lines.extend(extra_lines)

    info_path = base / "comparison.txt"
    info_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(image_path)


def _write_result_images(
    base: Path, result_render: np.ndarray | None, offline_ref: np.ndarray
) -> str:
    """Write the applied-result stack + difference map; returns the manifest line."""
    if result_render is None:
        return ""
    # [applied transform result | offline] — this is what Resolve should show.
    cv2.imwrite(
        str(base / "result_vs_offline.jpg"),
        _stack_horizontal(result_render, offline_ref),
    )
    # Difference map: grade-normalised (CLAHE) grayscale |result - offline|.
    # Dark = aligned. The fraction of near-black pixels is a quick "did it lock"
    # number that ignores colour grade and shows residual geometric error.
    diff, near_black = _normalized_difference(result_render, offline_ref)
    cv2.imwrite(str(base / "difference.jpg"), diff)
    return f"difference: {near_black * 100:.1f}% near-black (higher = better aligned)"


def _write_raw_image(
    base: Path, online_raw: np.ndarray | None, online_for_match: np.ndarray
) -> str:
    """Write the native-source frame; returns the manifest line."""
    if online_raw is None:
        return ""
    cv2.imwrite(str(base / "online_raw_source.jpg"), online_raw)
    rh, rw = online_raw.shape[:2]
    fh, fw = online_for_match.shape[:2]
    return f"online_raw_source: {rw}x{rh} (native source, before fit) → fit canvas {fw}x{fh}"


def format_sample_compare_line(
    *,
    media_path: str,
    source_frame: int,
    timeline_frame: int,
    offline_frame: int,
    offline_mapping: str,
    mapping_detail: str,
    reel: str,
    baseline_zoom: float,
    compensated: bool,
    offline_decoded_index: int = -1,
) -> str:
    comp = (
        "yes (current Edit on source before match)" if compensated else "no (identity / fit only)"
    )
    decode_note = ""
    if offline_decoded_index >= 0:
        decode_note = f" | decoded index {offline_decoded_index}"
        if offline_decoded_index != offline_frame:
            decode_note += f" (requested {offline_frame})"
    return (
        f"online: {Path(media_path).name} src frame {source_frame} | "
        f"timeline {timeline_frame} | pre-transform sim: {comp} | baseline zoom {baseline_zoom:.3f}\n"
        f"offline: lock-cut frame {offline_frame} via {offline_mapping} — {mapping_detail}"
        f"{decode_note}\n"
        f"(hub = Resolve timeline frame {timeline_frame})\n"
        f"reel: {reel!r}"
    )
