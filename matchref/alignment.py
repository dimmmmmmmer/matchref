"""OpenCV ECC and feature-based alignment."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from matchref.config import AppConfig
from matchref.overlay_crop import (
    ContentCrop,
    content_crop_for_shape,
    ecc_match_mask,
    overlay_margins_fraction,
)
from matchref.precision_align import (
    is_maximum_precision,
    normalize_to_canvas,
    refine_warp_phase_correlation,
    upscale_warp_translation,
)


@dataclass
class AlignmentResult:
    scale: float = 1.0
    translation_x: float = 0.0
    translation_y: float = 0.0
    rotation_deg: float = 0.0
    ecc_score: float = 0.0
    ok: bool = False
    message: str = ""
    warp_matrix: np.ndarray | None = None
    analysis_width: int = 0
    analysis_height: int = 0


def _motion_mode(name: str) -> int:
    mapping = {
        "translation": cv2.MOTION_TRANSLATION,
        "euclidean": cv2.MOTION_EUCLIDEAN,
        "affine": cv2.MOTION_AFFINE,
        "homography": cv2.MOTION_HOMOGRAPHY,
    }
    return mapping.get(name.lower(), cv2.MOTION_AFFINE)


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


def prepare_gray_float(image: np.ndarray, max_width: int) -> np.ndarray:
    gray = _even_crop(_to_gray(image, max_width))
    gray = gray.astype(np.float32)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    cv2.normalize(gray, gray, 0.0, 1.0, cv2.NORM_MINMAX)
    return gray


def prepare_gray_uint8(image: np.ndarray, max_width: int, *, use_clahe: bool = True) -> np.ndarray:
    gray = _even_crop(_to_gray(image, max_width))
    if not use_clahe:
        return gray.astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray.astype(np.uint8))


def decompose_affine(warp_matrix: np.ndarray, image_shape: tuple[int, int]) -> tuple[float, float, float, float]:
    """Scale, normalized translation (tx/w, ty/h) and rotation from a warp."""
    from matchref.transform_convert import decompose_warp_matrix

    height, width = image_shape[:2]
    scale, tx, ty, rotation_deg = decompose_warp_matrix(warp_matrix, width, height)
    return scale, tx / max(width, 1), ty / max(height, 1), rotation_deg


def _fill_result(
    result: AlignmentResult,
    warp: np.ndarray,
    score: float,
    shape: tuple[int, int],
    method: str,
    config: AppConfig | None = None,
) -> AlignmentResult:
    scale, pos_x, pos_y, rot = decompose_affine(warp, shape)
    if config is not None:
        max_rot = float(config.get("max_alignment_rotation_deg", 8.0))
        if bool(config.get("lock_alignment_rotation", True)):
            rot = 0.0
        elif abs(rot) > max_rot:
            result.message = f"rotation {rot:.1f}° exceeds limit"
            return result
    result.scale = scale
    result.translation_x = pos_x
    result.translation_y = pos_y
    result.rotation_deg = rot
    result.ecc_score = float(score)
    result.warp_matrix = warp
    result.analysis_height, result.analysis_width = int(shape[0]), int(shape[1])
    result.ok = True
    result.message = method
    return result


def _prepare_match_pair(
    online_frame: np.ndarray,
    offline_frame: np.ndarray,
    max_width: int,
    config: AppConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, ContentCrop]:
    """Crop overlay margins on both frames; ECC mask only needed when not pre-cropped."""
    margins = overlay_margins_fraction(config)
    use_clahe = not is_maximum_precision(config)
    off_gray = prepare_gray_uint8(offline_frame, max_width, use_clahe=use_clahe)
    on_gray = prepare_gray_uint8(online_frame, max_width, use_clahe=use_clahe)
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


def warp_analysis_to_timeline(
    warp: np.ndarray,
    analysis_size: tuple[int, int],
    timeline_size: tuple[int, int],
) -> np.ndarray:
    """Scale translation from analysis pixels to Resolve timeline pixels."""
    ah, aw = analysis_size
    th, tw = timeline_size
    if aw <= 0 or ah <= 0:
        return warp
    sx = tw / float(aw)
    sy = th / float(ah)
    if abs(sx - 1.0) < 1e-6 and abs(sy - 1.0) < 1e-6:
        return warp
    out = np.array(warp, dtype=np.float64)
    if out.shape == (3, 3):
        out[0, 2] *= sx
        out[1, 2] *= sy
    else:
        out[0, 2] *= sx
        out[1, 2] *= sy
    return out.astype(np.float32)


def _run_ecc(
    template: np.ndarray,
    input_img: np.ndarray,
    motion: int,
    criteria: tuple[int, int, float],
    gauss: int,
    init: np.ndarray | None = None,
    mask: np.ndarray | None = None,
) -> tuple[float, np.ndarray]:
    warp = np.eye(2, 3, dtype=np.float32) if init is None else init.astype(np.float32).copy()
    if mask is not None:
        score, warp_out = cv2.findTransformECC(
            template,
            input_img,
            warp,
            motion,
            criteria,
            mask,
            gauss,
        )
    else:
        score, warp_out = cv2.findTransformECC(
            template,
            input_img,
            warp,
            motion,
            criteria,
            gauss,
        )
    return float(score), warp_out


def _ecc_motion_modes(config: AppConfig) -> list[int]:
    primary = _motion_mode(str(config.get("ecc_motion_mode", "euclidean")))
    if bool(config.get("ecc_single_motion_mode", True)):
        return [primary]
    fallbacks = [cv2.MOTION_EUCLIDEAN, cv2.MOTION_TRANSLATION, cv2.MOTION_AFFINE]
    modes = [primary]
    for mode in fallbacks:
        if mode not in modes:
            modes.append(mode)
    return modes


def _try_ecc(
    offline: np.ndarray,
    online: np.ndarray,
    config: AppConfig,
    init: np.ndarray | None = None,
    mask: np.ndarray | None = None,
) -> AlignmentResult | None:
    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        int(config.get("ecc_iterations", 5000)),
        float(config.get("ecc_epsilon", 1e-6)),
    )
    best: AlignmentResult | None = None
    for motion in _ecc_motion_modes(config):
        for gauss in (int(config.get("ecc_gauss_filt_size", 5)), 1):
            try:
                score, warp = _run_ecc(offline, online, motion, criteria, gauss, init, mask)
                out = AlignmentResult()
                filled = _fill_result(out, warp, score, online.shape, "ok (ecc)", config)
                if not filled.ok:
                    continue
                if best is None or filled.ecc_score > best.ecc_score:
                    best = filled
            except cv2.error:
                continue
    if best is None:
        return None

    from matchref.edit_quantize import edit_priority_zoom

    refine_trans = bool(config.get("alignment_refine_translation", True)) and not edit_priority_zoom(
        config
    )
    if refine_trans and init is None:
        try:
            score, warp = _run_ecc(
                offline,
                online,
                cv2.MOTION_TRANSLATION,
                criteria,
                1,
                best.warp_matrix,
                mask,
            )
            out = AlignmentResult()
            refined = _fill_result(out, warp, score, online.shape, "ok (ecc+trans)", config)
            if refined.ok and refined.ecc_score >= best.ecc_score * 0.85:
                return refined
        except cv2.error:
            pass
    return best


def _align_features(
    offline_u8: np.ndarray,
    online_u8: np.ndarray,
    config: AppConfig,
    mask: np.ndarray | None = None,
) -> AlignmentResult | None:
    n_features = int(config.get("feature_max_count", 8000))
    ratio_thresh = float(config.get("feature_match_ratio", 0.75))
    min_inlier_ratio = float(config.get("feature_min_inlier_ratio", 0.15))

    orb = cv2.ORB_create(nfeatures=n_features)
    kp_off, des_off = orb.detectAndCompute(offline_u8, mask)
    kp_on, des_on = orb.detectAndCompute(online_u8, mask)
    if des_off is None or des_on is None or len(kp_off) < 8 or len(kp_on) < 8:
        return None

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    pairs = matcher.knnMatch(des_off, des_on, k=2)
    good = []
    for pair in pairs:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < ratio_thresh * n.distance:
            good.append(m)

    if len(good) < 8:
        return None

    offline_pts = np.float32([kp_off[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    online_pts = np.float32([kp_on[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    warp, inliers = cv2.estimateAffinePartial2D(
        online_pts,
        offline_pts,
        method=cv2.RANSAC,
        ransacReprojThreshold=3.0,
    )
    if warp is None:
        return None

    inlier_count = int(inliers.sum()) if inliers is not None else 0
    score = inlier_count / max(len(good), 1)
    if score < min_inlier_ratio:
        return None

    out = AlignmentResult()
    return _fill_result(out, warp, score, online_u8.shape, "ok (features)", config)


def alignment_admission_threshold(alignment: AlignmentResult, config: AppConfig) -> float:
    """
    Minimum score to run Resolve Edit refine and mark a sample successful.

    Stricter ``min_match_score`` is enforced later before apply (after refine).
    """
    if alignment.ok and "features" in alignment.message:
        return float(config.get("feature_match_threshold", 0.85))
    return float(config.get("ecc_threshold", 0.85))


def _pyramid_ecc(
    online_frame: np.ndarray,
    offline_frame: np.ndarray,
    config: AppConfig,
    *,
    timeline_width: int,
) -> AlignmentResult | None:
    fractions = config.get("ecc_pyramid_width_fractions")
    if not isinstance(fractions, list) or not fractions:
        fractions = [0.25, 0.5, 1.0]
    warp: np.ndarray | None = None
    last_mw = 0
    best: AlignmentResult | None = None
    method = str(config.get("alignment_method", "auto")).lower()
    feat_init = None

    for frac in fractions:
        mw = max(320, int(timeline_width * float(frac)))
        on_u8, off_u8, ecc_mask, _ = _prepare_match_pair(
            online_frame,
            offline_frame,
            mw,
            config,
        )
        if warp is not None and last_mw > 0:
            warp = upscale_warp_translation(warp, mw / float(last_mw))
        if method == "auto" and feat_init is None:
            feat = _align_features(off_u8, on_u8, config, ecc_mask)
            if feat is not None and feat.warp_matrix is not None:
                feat_init = feat.warp_matrix
        ecc = _try_ecc(off_u8, on_u8, config, warp or feat_init, ecc_mask)
        if ecc is not None:
            warp = ecc.warp_matrix
            best = ecc
        last_mw = mw

    if best is not None and warp is not None and bool(
        config.get("refine_subpixel_translation", True)
    ):
        on_full, off_full, mask_full, _ = _prepare_match_pair(
            online_frame,
            offline_frame,
            timeline_width,
            config,
        )
        try:
            warp = refine_warp_phase_correlation(off_full, on_full, warp)
            best.warp_matrix = warp
            best.message = "ok (pyramid+phase)"
        except cv2.error:
            pass
    return best


def align_frames(
    online_frame: np.ndarray,
    offline_frame: np.ndarray,
    config: AppConfig,
    *,
    analysis_max_width: int | None = None,
    timeline_size: tuple[int, int] | None = None,
) -> AlignmentResult:
    """Align online to offline; offline is the reference template."""
    result = AlignmentResult()
    max_width = int(analysis_max_width or config.get("analysis_max_width", 0) or 0)
    if max_width <= 0 and timeline_size:
        max_width = int(timeline_size[0])
    if max_width <= 0:
        result.message = "analysis_max_width not set (need Resolve timeline)"
        return result
    method = str(config.get("alignment_method", "auto")).lower()
    maximum = is_maximum_precision(config)
    if maximum and timeline_size:
        max_width = int(timeline_size[0])
        online_frame, offline_frame = normalize_to_canvas(
            online_frame,
            offline_frame,
            timeline_size,
            str(config.get("input_scaling", "fit")),
        )

    try:
        if maximum and timeline_size:
            pyramid = _pyramid_ecc(
                online_frame,
                offline_frame,
                config,
                timeline_width=max_width,
            )
            if pyramid is not None:
                return pyramid

        online_u8, offline_u8, ecc_mask, _crop = _prepare_match_pair(
            online_frame,
            offline_frame,
            max_width,
            config,
        )

        for err in (_validate_gray(offline_u8, "offline"), _validate_gray(online_u8, "online")):
            if err:
                result.message = err
                return result

        feat_mask = None
        if ecc_mask is not None and online_u8.shape == ecc_mask.shape:
            feat_mask = ecc_mask

        feat_init = None
        if method == "auto":
            feat = _align_features(offline_u8, online_u8, config, feat_mask)
            if feat is not None and feat.warp_matrix is not None:
                feat_init = feat.warp_matrix

        ecc_u8 = _try_ecc(offline_u8, online_u8, config, feat_init, ecc_mask)
        if ecc_u8 is not None:
            return ecc_u8

        online_f = online_u8.astype(np.float32)
        offline_f = offline_u8.astype(np.float32)
        cv2.normalize(online_f, online_f, 0.0, 1.0, cv2.NORM_MINMAX)
        cv2.normalize(offline_f, offline_f, 0.0, 1.0, cv2.NORM_MINMAX)

        ecc_f = _try_ecc(offline_f, online_f, config, feat_init, None)
        if ecc_f is not None:
            return ecc_f

        if method in ("features", "auto"):
            feat = _align_features(offline_u8, online_u8, config, feat_mask)
            if feat is not None:
                return feat

        if method == "features":
            result.message = "feature matching failed"
        elif method == "ecc":
            result.message = "ECC failed on float and uint8"
        else:
            result.message = "ECC and feature matching failed"
    except cv2.error as exc:
        result.message = str(exc)
    except Exception as exc:  # noqa: BLE001
        result.message = str(exc)

    return result


def apply_match_flags(
    alignment: AlignmentResult,
    config: AppConfig,
    base_scale: float = 1.0,
    base_center: tuple[float, float] = (0.5, 0.5),
    base_angle: float = 0.0,
) -> tuple[float, float, float, float]:
    scale = alignment.scale if config.get("match_scale", True) else base_scale
    pos_x = alignment.translation_x if config.get("match_position", True) else base_center[0]
    pos_y = alignment.translation_y if config.get("match_position", True) else base_center[1]
    angle = alignment.rotation_deg if config.get("match_rotation", True) else base_angle

    if config.get("match_scale", True):
        scale = base_scale * scale
    if config.get("match_position", True):
        pos_x = base_center[0] + pos_x
        pos_y = base_center[1] + pos_y
    if config.get("match_rotation", True):
        angle = base_angle + angle

    return scale, pos_x, pos_y, angle


def transforms_are_stable(
    samples: list[tuple[float, float, float, float]],
    threshold: float,
) -> bool:
    if len(samples) < 2:
        return True
    ranges = []
    for idx in range(4):
        values = [s[idx] for s in samples]
        ranges.append(max(values) - min(values))
    return max(ranges) <= threshold
