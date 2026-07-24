"""Shared screen-capture, scaling, and template-matching foundation.

Macro Builder and Icon Alerts intentionally keep their own workflow policies,
but both route their visual detection through this module.
"""

from __future__ import annotations

import ctypes
import math
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

import cv2
import numpy as np

Rect = tuple[int, int, int, int]
Size = tuple[int, int]

ALERT_DEFAULT_SCALES = (
    1.00,
    0.95,
    1.05,
    0.90,
    1.10,
    0.85,
    1.15,
    0.80,
    1.20,
    0.75,
    0.70,
    0.65,
    0.60,
    0.55,
    0.50,
    1.30,
    1.40,
    1.50,
)
MACRO_DEFAULT_SCALES = (
    1.00,
    0.95,
    1.05,
    0.90,
    1.10,
    0.85,
    1.15,
    0.80,
    1.20,
)
# Compatibility name used by Icon Alerts and external callers.
DEFAULT_SCALES = ALERT_DEFAULT_SCALES
DEFAULT_ROTATIONS = (0, -5, 5, -8, 8)
DEFAULT_LOW_VARIANCE_THRESHOLD = 1.0
DEFAULT_MAX_VARIANT_PIXELS = 24_000_000
MATCH_MODE_TEXT = "colored_text"
MATCH_MODE_STATIC = "static_picture"
MATCH_MODE_ANIMATED = "animated_picture"
MATCH_MODE_LABELS = {
    MATCH_MODE_TEXT: "Text / colored text",
    MATCH_MODE_STATIC: "Static picture",
    MATCH_MODE_ANIMATED: "Animated/rotating picture",
}
MATCH_MODE_BY_LABEL = {label: mode for mode, label in MATCH_MODE_LABELS.items()}
MATCH_MODE_LIST_TAGS = {
    MATCH_MODE_TEXT: "Text",
    MATCH_MODE_STATIC: "Static",
    MATCH_MODE_ANIMATED: "Animated",
}
MATCH_MODE_VALUES = frozenset(MATCH_MODE_LABELS)
DEFAULT_NEW_MATCH_MODE = MATCH_MODE_STATIC
LEGACY_ALERT_MATCH_MODE = MATCH_MODE_ANIMATED
LEGACY_MACRO_MATCH_MODE = MATCH_MODE_STATIC
DETECTION_UNAVAILABLE = object()


def enable_process_dpi_awareness():
    """Keep capture, window, and click coordinates in physical pixels on Windows."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


enable_process_dpi_awareness()


class LegacyTemplateMatch(tuple):
    """Six-field tuple compatibility plus exact geometry metadata."""

    scale_x: float
    scale_y: float
    angle: float

    def __new__(cls, values, *, scale_x=1.0, scale_y=1.0, angle=0.0):
        instance = super().__new__(cls, values)
        instance.scale_x = float(scale_x)
        instance.scale_y = float(scale_y)
        instance.angle = float(angle)
        return instance


@dataclass(frozen=True)
class TemplateMatch:
    x: int
    y: int
    width: int
    height: int
    score: float
    scale: float
    angle: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0

    def legacy_tuple(self):
        return LegacyTemplateMatch(
            (self.x, self.y, self.width, self.height, self.score, self.scale),
            scale_x=self.scale_x,
            scale_y=self.scale_y,
            angle=self.angle,
        )


def normalize_match_mode(value, default=LEGACY_ALERT_MATCH_MODE):
    return value if value in MATCH_MODE_VALUES else default


def capture_bgr(capture, target):
    """Capture an MSS target and return the shared BGR image contract."""
    raw = capture.grab(target)
    pixels = np.asarray(raw)
    if pixels.ndim != 3 or pixels.shape[2] < 3:
        raise ValueError("Screen capture did not return a color image")
    return np.ascontiguousarray(pixels[:, :, :3])


def monitor_rect(monitor) -> Rect:
    return (
        int(monitor["left"]),
        int(monitor["top"]),
        int(monitor["width"]),
        int(monitor["height"]),
    )


def physical_monitor_index(monitors, requested_index, *, use_fallback=True):
    """Resolve one physical MSS monitor index using one shared policy."""
    valid_request = (
        not isinstance(requested_index, bool)
        and isinstance(requested_index, int)
        and 1 <= requested_index < len(monitors)
    )
    if valid_request:
        return requested_index
    if not use_fallback:
        return None
    if len(monitors) > 1:
        return 1
    return 0 if monitors else None


def monitor_indices_for_rect(monitors, rect: Sequence[int]):
    """Return every physical monitor overlapped by an absolute rectangle."""
    left, top, width, height = (int(value) for value in rect)
    right, bottom = left + width, top + height
    result = []
    for index, monitor in enumerate(monitors[1:], start=1):
        ml, mt, mw, mh = monitor_rect(monitor)
        if min(right, ml + mw) > max(left, ml) and min(bottom, mt + mh) > max(top, mt):
            result.append(index)
    return tuple(result)


def region_for_capture(region: Sequence[int]):
    left, top, width, height = (int(value) for value in region)
    return {"left": left, "top": top, "width": width, "height": height}


def intersect_region_with_monitor(monitor, absolute_region: Optional[Sequence[int]]):
    """Return an absolute region as monitor-local coordinates."""
    if absolute_region is None:
        return None
    monitor_left, monitor_top, monitor_width, monitor_height = monitor_rect(monitor)
    region_left, region_top, region_width, region_height = (
        int(value) for value in absolute_region
    )
    left = max(region_left, monitor_left)
    top = max(region_top, monitor_top)
    right = min(region_left + region_width, monitor_left + monitor_width)
    bottom = min(region_top + region_height, monitor_top + monitor_height)
    if right <= left or bottom <= top:
        return None
    return (left - monitor_left, top - monitor_top, right - left, bottom - top)


def monitor_index_for_rect(monitors, rect: Sequence[int]):
    """Return the physical MSS monitor with the greatest overlap."""
    left, top, width, height = (int(value) for value in rect)
    right, bottom = left + width, top + height
    best_index = None
    best_area = 0
    for index, monitor in enumerate(monitors[1:], start=1):
        ml, mt, mw, mh = monitor_rect(monitor)
        overlap_width = max(0, min(right, ml + mw) - max(left, ml))
        overlap_height = max(0, min(bottom, mt + mh) - max(top, mt))
        area = overlap_width * overlap_height
        if area > best_area:
            best_index, best_area = index, area
    return best_index


def _valid_size(value) -> Optional[Size]:
    if value is None or isinstance(value, (str, bytes, dict)):
        return None
    try:
        width, height = (int(part) for part in value)
    except (TypeError, ValueError, OverflowError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _valid_reference_sizes(reference_size=None, reference_sizes=()):
    result = []
    seen = set()
    for value in (reference_size, *(reference_sizes or ())):
        parsed = _valid_size(value)
        if parsed is None or parsed in seen:
            continue
        seen.add(parsed)
        result.append(parsed)
    return tuple(result)


def resolution_scale_candidates(
    reference_size,
    current_size,
    legacy_scales: Iterable[float] = DEFAULT_SCALES,
    additional_reference_sizes=(),
):
    """Add exact uniform resolution scales while preserving legacy fallbacks."""
    references = _valid_reference_sizes(
        reference_size,
        additional_reference_sizes,
    )
    current = _valid_size(current_size)
    legacy = [float(scale) for scale in legacy_scales if float(scale) > 0.0]
    if not references or current is None:
        return tuple(dict.fromkeys(round(scale, 6) for scale in legacy))

    derived = []
    for reference in references:
        scale_x = current[0] / reference[0]
        scale_y = current[1] / reference[1]
        geometric = math.sqrt(scale_x * scale_y)
        derived.extend(
            (geometric, scale_x, scale_y, geometric * 0.95, geometric * 1.05)
        )
    result = []
    seen = set()
    for scale in (*derived, *legacy):
        if not math.isfinite(scale) or not 0.05 <= scale <= 8.0:
            continue
        rounded = round(float(scale), 6)
        if rounded in seen:
            continue
        seen.add(rounded)
        result.append(float(scale))
    return tuple(result)


def resolution_scale_pairs(
    reference_size,
    current_size,
    legacy_scales: Iterable[float] = DEFAULT_SCALES,
    additional_reference_sizes=(),
):
    """Return exact x/y resolution scales plus uniform fallback candidates."""
    primary_reference = _valid_size(reference_size)
    references = _valid_reference_sizes(
        reference_size,
        additional_reference_sizes,
    )
    current = _valid_size(current_size)
    pairs = []
    seen = set()

    def add(scale_x, scale_y):
        try:
            scale_x, scale_y = float(scale_x), float(scale_y)
        except (TypeError, ValueError, OverflowError):
            return
        if (
            not math.isfinite(scale_x)
            or not math.isfinite(scale_y)
            or not 0.05 <= scale_x <= 8.0
            or not 0.05 <= scale_y <= 8.0
        ):
            return
        key = (round(scale_x, 6), round(scale_y, 6))
        if key in seen:
            return
        seen.add(key)
        pairs.append((scale_x, scale_y))

    if current is not None:
        for reference in references:
            add(current[0] / reference[0], current[1] / reference[1])
    if primary_reference is not None:
        uniform_scales = resolution_scale_candidates(
            primary_reference,
            current_size,
            legacy_scales,
        )
    else:
        uniform_scales = tuple(float(scale) for scale in legacy_scales)
    for scale in uniform_scales:
        add(scale, scale)
    if current is not None:
        for reference in references:
            if reference == primary_reference:
                continue
            geometric = math.sqrt(
                (current[0] / reference[0]) * (current[1] / reference[1])
            )
            add(geometric, geometric)
    return tuple(pairs)


def preferred_resolution_scale(reference_size=None, current_size=None):
    reference = _valid_size(reference_size)
    current = _valid_size(current_size)
    if reference is None or current is None:
        return 1.0
    return math.sqrt((current[0] / reference[0]) * (current[1] / reference[1]))


def resize_template(template, scale, cache=None, interpolation=None):
    if abs(float(scale) - 1.0) < 0.000001:
        return template
    key = (id(template), round(float(scale), 6), interpolation)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None and cached[0] is template:
            return cached[1]
    height, width = template.shape[:2]
    resized_width = max(1, round(width * float(scale)))
    resized_height = max(1, round(height * float(scale)))
    if interpolation is None:
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(
        template,
        (resized_width, resized_height),
        interpolation=interpolation,
    )
    if cache is not None:
        cache[key] = (template, resized)
    return resized


def box_iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    width, height = max(0, ix2 - ix1), max(0, iy2 - iy1)
    intersection = width * height
    if intersection == 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return intersection / float(area_a + area_b - intersection)


def _crop_region(image, region):
    if region is None:
        return image, (0, 0)
    x, y, width, height = (int(value) for value in region)
    image_height, image_width = image.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1 = min(image_width, x + max(0, width))
    y1 = min(image_height, y + max(0, height))
    if x1 <= x0 or y1 <= y0:
        return image[:0, :0], (x0, y0)
    return image[y0:y1, x0:x1], (x0, y0)


def _prepare_match_image(image_bgr, use_grayscale):
    if not use_grayscale:
        return image_bgr
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)


def _colored_text_profile(template_bgr):
    if template_bgr.ndim != 3 or template_bgr.shape[2] < 3 or template_bgr.size == 0:
        return None
    bgr = template_bgr[:, :, :3]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    border = np.concatenate((lab[0], lab[-1], lab[:, 0], lab[:, -1]))
    background = np.median(border, axis=0)
    distance = np.linalg.norm(lab - background, axis=2)
    maximum = float(distance.max())
    if maximum <= 1e-6:
        return None
    normalized = np.clip(distance * (255.0 / maximum), 0, 255).astype(np.uint8)
    _value, foreground = cv2.threshold(
        normalized, 0.0, 255.0, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    coverage = float(np.count_nonzero(foreground)) / float(foreground.size)
    if not 0.005 <= coverage <= 0.80:
        return None
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    pixels = hsv[foreground > 0]
    if pixels.size == 0:
        return None
    hue_angles = pixels[:, 0].astype(np.float64) * (2.0 * math.pi / 180.0)
    hue_angle = math.atan2(
        float(np.sin(hue_angles).mean()), float(np.cos(hue_angles).mean())
    )
    hue = (hue_angle % (2.0 * math.pi)) * (180.0 / (2.0 * math.pi))
    saturation = float(np.median(pixels[:, 1]))
    value = float(np.median(pixels[:, 2]))
    return {
        "hue": hue,
        "saturation": saturation,
        "value": value,
        "colorful": bool(saturation >= 45.0),
    }


def _colored_text_mask(image_bgr, profile):
    if image_bgr.ndim != 3 or image_bgr.shape[2] < 3 or image_bgr.size == 0:
        return np.zeros(image_bgr.shape[:2], dtype=np.uint8)
    hsv = cv2.cvtColor(image_bgr[:, :, :3], cv2.COLOR_BGR2HSV)
    saturation = profile["saturation"]
    value = profile["value"]
    if profile["colorful"]:
        hue = hsv[:, :, 0].astype(np.int16)
        hue_distance = np.abs(hue - int(round(profile["hue"])))
        hue_distance = np.minimum(hue_distance, 180 - hue_distance)
        selected = (
            (hue_distance <= 10)
            & (hsv[:, :, 1] >= max(40.0, saturation * 0.50))
            & (hsv[:, :, 2] >= max(70.0, value * 0.45))
        )
    else:
        selected = (hsv[:, :, 2] >= max(140.0, value * 0.65)) & (
            hsv[:, :, 1] <= min(255.0, saturation + 60.0)
        )
    return selected.astype(np.uint8) * 255


def _spatial_deviation(image):
    if image.ndim == 3:
        return float(np.max(np.std(image.astype(np.float32), axis=(0, 1))))
    return float(np.std(image))


def _text_shape_iou(screen, template, location):
    x, y = location
    height, width = template.shape[:2]
    candidate = screen[y : y + height, x : x + width] > 127
    expected = template > 127
    intersection = int(np.count_nonzero(candidate & expected))
    union = int(np.count_nonzero(candidate | expected))
    return (intersection / union) if union else -1.0


def _text_column_runs(template):
    """Return visible glyph runs separated by blank mask columns."""
    occupied = np.any(template > 127, axis=0)
    runs = []
    start = None
    for index, visible in enumerate(occupied):
        if visible and start is None:
            start = index
        if start is not None and (not visible or index == len(occupied) - 1):
            end = index if not visible else index + 1
            runs.append((start, end))
            start = None
    return runs


def _shifted_glyph_iou(screen, expected, x, y, max_shift):
    height, width = expected.shape[:2]
    screen_height, screen_width = screen.shape[:2]
    best = -1.0
    for offset_y in range(-max_shift, max_shift + 1):
        top = y + offset_y
        if top < 0 or top + height > screen_height:
            continue
        for offset_x in range(-max_shift, max_shift + 1):
            left = x + offset_x
            if left < 0 or left + width > screen_width:
                continue
            candidate = screen[top : top + height, left : left + width] > 127
            intersection = int(np.count_nonzero(candidate & expected))
            union = int(np.count_nonzero(candidate | expected))
            if union:
                best = max(best, intersection / union)
    return best


def _text_shape_score(screen, template, location):
    """Score the full text and reject a single substituted glyph.

    A whole-string IoU can rate ``#2210`` highly against ``#2212`` because
    four of the five glyphs are identical.  For text with separable glyphs,
    compare every glyph locally and require the weakest one to be consistent
    with the typical glyph quality.  This preserves tolerance for uniform
    anti-aliasing/background changes while making one wrong character
    decisive.
    """
    full_score = _text_shape_iou(screen, template, location)
    runs = _text_column_runs(template)
    if full_score < 0.0 or len(runs) < 3:
        return full_score

    x, y = location
    expected_mask = template > 127
    max_shift = max(1, min(2, round(template.shape[0] * 0.10)))
    glyph_scores = []
    for left, right in runs:
        expected = expected_mask[:, left:right]
        score = _shifted_glyph_iou(
            screen,
            expected,
            x + left,
            y,
            max_shift,
        )
        if score >= 0.0:
            glyph_scores.append(score)
    if len(glyph_scores) < 3:
        return full_score

    typical_score = float(np.median(glyph_scores))
    if typical_score <= 1e-6:
        return -1.0
    weakest_consistency = min(glyph_scores) / typical_score
    # Exact text rendered on a different translucent background commonly has
    # a whole-shape IoU around 0.8. Normalize that expected variation, then
    # let the weakest glyph veto an otherwise similar string.
    normalized_full_score = min(1.0, full_score / 0.80)
    return min(normalized_full_score, weakest_consistency)


def _score_map(screen, template, low_variance):
    if low_variance:
        raw = cv2.matchTemplate(screen, template, cv2.TM_SQDIFF)
        channels = template.shape[2] if template.ndim == 3 else 1
        height, width = template.shape[:2]
        sample_count = float(width * height * channels)
        mean_squared_error = np.maximum(raw / sample_count, 0.0)
        mean_squared_error = np.where(
            mean_squared_error <= 0.01,
            0.0,
            mean_squared_error,
        )
        # A 255-range normalization makes unrelated near-flat noise look
        # almost perfect. This score makes even one-level differences matter
        # while preserving an exact pixel match at 1.0.
        return 1.0 / (1.0 + 4.0 * mean_squared_error)
    raw = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    return np.nan_to_num(raw, nan=-1.0, posinf=1.0, neginf=-1.0)


def _best_variant_match(screen, template, low_variance, text_shape=False):
    if template.shape[0] > screen.shape[0] or template.shape[1] > screen.shape[1]:
        return -1.0, None
    scores = _score_map(screen, template, low_variance)
    _, max_score, _, max_location = cv2.minMaxLoc(scores)
    if text_shape and scores.size:
        count = min(8, scores.size)
        flat = scores.reshape(-1)
        indices = np.argpartition(flat, -count)[-count:]
        best_score = -1.0
        best_location = max_location
        best_correlation = -1.0
        for index in indices:
            y, x = np.unravel_index(int(index), scores.shape)
            location = (int(x), int(y))
            shape_score = _text_shape_score(screen, template, location)
            correlation = float(scores[y, x])
            if shape_score > best_score or (
                abs(shape_score - best_score) <= 1e-9 and correlation > best_correlation
            ):
                best_score = shape_score
                best_location = location
                best_correlation = correlation
        return best_score, best_location
    if low_variance and scores.size > 1:
        if float(max_score) - float(scores.min()) <= 1e-6:
            return -1.0, None
    return float(max_score), max_location


def _rotate_image(image, angle):
    if angle == 0:
        return image
    height, width = image.shape[:2]
    center = (width / 2, height / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def prepare_template_variants(
    template_bgr,
    scales=DEFAULT_SCALES,
    rotations=DEFAULT_ROTATIONS,
    use_grayscale=False,
    match_mode=LEGACY_ALERT_MATCH_MODE,
    reference_size=None,
    current_size=None,
    reference_sizes=(),
    low_variance_threshold=DEFAULT_LOW_VARIANCE_THRESHOLD,
    cancel_event=None,
    stop_check=None,
    max_variant_pixels=DEFAULT_MAX_VARIANT_PIXELS,
):
    match_mode = normalize_match_mode(match_mode)
    scale_pairs = resolution_scale_pairs(
        reference_size,
        current_size,
        scales,
        reference_sizes,
    )
    try:
        low_variance_threshold = max(0.0, float(low_variance_threshold))
    except (TypeError, ValueError, OverflowError):
        low_variance_threshold = DEFAULT_LOW_VARIANCE_THRESHOLD
    if not math.isfinite(low_variance_threshold):
        low_variance_threshold = DEFAULT_LOW_VARIANCE_THRESHOLD
    text_profile = None
    if match_mode == MATCH_MODE_TEXT:
        text_profile = _colored_text_profile(template_bgr)
        template = (
            _colored_text_mask(template_bgr, text_profile)
            if text_profile is not None
            else _prepare_match_image(template_bgr, True)
        )
        rotations = (0,)
    else:
        template = _prepare_match_image(template_bgr, use_grayscale)
        if match_mode == MATCH_MODE_STATIC:
            rotations = (0,)

    rotations = tuple(rotations)
    base_height, base_width = template.shape[:2]
    projected_pixels = sum(
        max(1, round(base_width * scale_x)) * max(1, round(base_height * scale_y))
        for scale_x, scale_y in scale_pairs
    ) * len(rotations)
    if max_variant_pixels is not None and projected_pixels > max(
        1, int(max_variant_pixels)
    ):
        raise ValueError(
            "Template is too large for this detection profile; "
            "capture a tighter region or use Static picture mode"
        )
    variants = []
    for angle in rotations:
        if _cancelled(cancel_event, stop_check):
            break
        rotated = _rotate_image(template, angle)
        for scale_x, scale_y in scale_pairs:
            if _cancelled(cancel_event, stop_check):
                break
            interpolation = (
                cv2.INTER_NEAREST
                if match_mode == MATCH_MODE_TEXT and text_profile is not None
                else (
                    cv2.INTER_AREA
                    if scale_x < 1.0 or scale_y < 1.0
                    else cv2.INTER_LINEAR
                )
            )
            width = max(1, round(base_width * scale_x))
            height = max(1, round(base_height * scale_y))
            resized = cv2.resize(rotated, (width, height), interpolation=interpolation)
            scale = math.sqrt(scale_x * scale_y)
            variants.append(
                {
                    "image": resized,
                    "scale": float(scale),
                    "scale_x": float(scale_x),
                    "scale_y": float(scale_y),
                    "angle": float(angle),
                    "low_variance": (
                        _spatial_deviation(resized) < low_variance_threshold
                    ),
                    "use_grayscale": bool(use_grayscale),
                    "match_mode": match_mode,
                    "text_profile": text_profile,
                }
            )
    return tuple(variants)


def _cancelled(cancel_event=None, stop_check: Optional[Callable] = None):
    if stop_check is not None:
        stop_check()
    return cancel_event is not None and cancel_event.is_set()


def _coarse_multiscale_match(
    screen,
    variants,
    early_exit_score=None,
    cancel_event=None,
    stop_check=None,
    factor=0.5,
    preferred_scale=1.0,
    low_variance_threshold=DEFAULT_LOW_VARIANCE_THRESHOLD,
):
    small_screen = cv2.resize(
        screen, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA
    )
    zero_angle = [
        variant for variant in variants if abs(float(variant.get("angle", 0))) < 1e-9
    ]
    initial = zero_angle or list(variants)
    records = []

    def evaluate(variant):
        if _cancelled(cancel_event, stop_check):
            return
        template = variant["image"]
        height, width = template.shape[:2]
        small_width = max(1, round(width * factor))
        small_height = max(1, round(height * factor))
        if small_width > small_screen.shape[1] or small_height > small_screen.shape[0]:
            return
        small_template = cv2.resize(
            template, (small_width, small_height), interpolation=cv2.INTER_AREA
        )
        score, location = _best_variant_match(
            small_screen,
            small_template,
            _spatial_deviation(small_template) < low_variance_threshold,
            text_shape=variant.get("match_mode") == MATCH_MODE_TEXT,
        )
        if location is not None:
            records.append((score, location, variant))

    for variant in initial:
        evaluate(variant)
    if not records or _cancelled(cancel_event, stop_check):
        return -1.0, None, 1.0, None

    best_base_scale = float(max(records, key=lambda record: record[0])[2]["scale"])
    by_angle = {}
    for variant in variants:
        angle = float(variant.get("angle", 0))
        if abs(angle) < 1e-9:
            continue
        by_angle.setdefault(angle, []).append(variant)
    for angle_variants in by_angle.values():
        for variant in sorted(
            angle_variants,
            key=lambda item: abs(float(item["scale"]) - best_base_scale),
        )[:4]:
            evaluate(variant)

    best_score, best_location, best_scale = -1.0, None, 1.0
    best_angle = 0.0
    best_variant = None
    for _coarse_score, coarse_location, variant in sorted(
        records, key=lambda item: item[0], reverse=True
    ):
        if _cancelled(cancel_event, stop_check):
            break
        template = variant["image"]
        height, width = template.shape[:2]
        expected_x = round(coarse_location[0] / factor)
        expected_y = round(coarse_location[1] / factor)
        margin = max(8, round(max(width, height) * 0.35))
        left = max(0, expected_x - margin)
        top = max(0, expected_y - margin)
        right = min(screen.shape[1], expected_x + width + margin)
        bottom = min(screen.shape[0], expected_y + height + margin)
        local = screen[top:bottom, left:right]
        score, local_location = _best_variant_match(
            local,
            template,
            bool(variant["low_variance"]),
            text_shape=variant.get("match_mode") == MATCH_MODE_TEXT,
        )
        if local_location is None:
            continue
        scale = float(variant["scale"])
        angle = float(variant["angle"])
        tied = abs(score - best_score) <= 1e-9
        if (
            score > best_score + 1e-9
            or (
                tied
                and abs(scale - preferred_scale) < abs(best_scale - preferred_scale)
            )
            or (
                tied
                and abs(scale - best_scale) <= 1e-9
                and abs(angle) < abs(best_angle)
            )
        ):
            best_score = score
            best_location = (left + local_location[0], top + local_location[1])
            best_scale = scale
            best_angle = angle
            best_variant = variant
            if early_exit_score is not None and best_score >= early_exit_score:
                break
    return best_score, best_location, best_scale, best_variant


def match_template_multiscale(
    screen_bgr,
    template_bgr,
    scales=DEFAULT_SCALES,
    use_grayscale=False,
    region=None,
    rotations=DEFAULT_ROTATIONS,
    variants=None,
    early_exit_score=None,
    cancel_event=None,
    match_mode=LEGACY_ALERT_MATCH_MODE,
    reference_size=None,
    current_size=None,
    reference_sizes=(),
    stop_check=None,
    allow_coarse=True,
    low_variance_threshold=DEFAULT_LOW_VARIANCE_THRESHOLD,
    max_variant_pixels=DEFAULT_MAX_VARIANT_PIXELS,
    return_details=False,
):
    best_score, best_location, best_scale = -1.0, None, 1.0
    best_angle = 0.0
    best_variant = None
    preferred_scale = preferred_resolution_scale(reference_size, current_size)
    screen_bgr, offset = _crop_region(screen_bgr, region)
    if screen_bgr.size == 0:
        if return_details:
            return best_score, best_location, best_scale, best_variant
        return best_score, best_location, best_scale

    match_mode = normalize_match_mode(match_mode)
    if variants is None:
        variants = prepare_template_variants(
            template_bgr,
            scales=scales,
            rotations=rotations,
            use_grayscale=use_grayscale,
            match_mode=match_mode,
            reference_size=reference_size,
            current_size=current_size,
            reference_sizes=reference_sizes,
            low_variance_threshold=low_variance_threshold,
            cancel_event=cancel_event,
            stop_check=stop_check,
            max_variant_pixels=max_variant_pixels,
        )
    variants = tuple(variants)
    if variants:
        match_mode = normalize_match_mode(
            variants[0].get("match_mode", match_mode), default=match_mode
        )
        use_grayscale = bool(variants[0].get("use_grayscale", use_grayscale))
    if match_mode == MATCH_MODE_TEXT:
        profile = variants[0].get("text_profile") if variants else None
        screen = (
            _colored_text_mask(screen_bgr, profile)
            if profile is not None
            else _prepare_match_image(screen_bgr, True)
        )
    else:
        screen = _prepare_match_image(screen_bgr, use_grayscale)

    if (
        allow_coarse
        and screen.shape[0] * screen.shape[1] >= 500_000
        and min(template_bgr.shape[:2]) >= 20
        and len(variants) > 8
    ):
        score, location, scale, matched_variant = _coarse_multiscale_match(
            screen,
            variants,
            early_exit_score=early_exit_score,
            cancel_event=cancel_event,
            stop_check=stop_check,
            factor=0.5 if min(template_bgr.shape[:2]) >= 30 else 0.67,
            preferred_scale=preferred_scale,
            low_variance_threshold=low_variance_threshold,
        )
        if location is not None:
            location = (location[0] + offset[0], location[1] + offset[1])
        if return_details:
            return score, location, scale, matched_variant
        return score, location, scale

    screen_height, screen_width = screen.shape[:2]
    for variant in variants:
        if _cancelled(cancel_event, stop_check):
            break
        resized = variant["image"]
        height, width = resized.shape[:2]
        if width > screen_width or height > screen_height:
            continue
        score, location = _best_variant_match(
            screen,
            resized,
            bool(variant["low_variance"]),
            text_shape=variant.get("match_mode") == MATCH_MODE_TEXT,
        )
        if location is None:
            continue
        scale = float(variant["scale"])
        angle = float(variant["angle"])
        epsilon = 1e-6 if variant["low_variance"] else 1e-9
        tied = abs(score - best_score) <= epsilon
        if (
            score > best_score + epsilon
            or (
                tied
                and abs(scale - preferred_scale) < abs(best_scale - preferred_scale)
            )
            or (
                tied
                and abs(scale - best_scale) <= 1e-9
                and abs(angle) < abs(best_angle)
            )
        ):
            best_score = score
            best_location = (location[0] + offset[0], location[1] + offset[1])
            best_scale = scale
            best_angle = angle
            best_variant = variant
            if early_exit_score is not None and best_score >= early_exit_score:
                break
    if return_details:
        return best_score, best_location, best_scale, best_variant
    return best_score, best_location, best_scale


def _bounded_local_peaks(scores, threshold, width, height, limit):
    kernel_width = max(1, min(width, scores.shape[1]))
    kernel_height = max(1, min(height, scores.shape[0]))
    local_max = cv2.dilate(
        scores, np.ones((kernel_height, kernel_width), dtype=np.uint8)
    )
    peak_mask = (scores >= threshold) & (scores >= local_max - 1e-6)
    indices = np.flatnonzero(peak_mask)
    if indices.size == 0:
        return []
    flat_scores = scores.reshape(-1)
    if indices.size > limit:
        selected = np.argpartition(flat_scores[indices], -limit)[-limit:]
        indices = indices[selected]
    result_width = scores.shape[1]
    return [
        (
            int(index) % result_width,
            int(index) // result_width,
            float(flat_scores[index]),
        )
        for index in indices
    ]


def find_template_matches(
    frame_bgr,
    template_bgr,
    confidence,
    *,
    collect_all=False,
    allow_coarse=True,
    match_mode=LEGACY_MACRO_MATCH_MODE,
    use_grayscale=False,
    scales=DEFAULT_SCALES,
    rotations=DEFAULT_ROTATIONS,
    variants=None,
    reference_size=None,
    current_size=None,
    reference_sizes=(),
    cancel_event=None,
    stop_check=None,
    low_variance_threshold=DEFAULT_LOW_VARIANCE_THRESHOLD,
    max_variant_pixels=DEFAULT_MAX_VARIANT_PIXELS,
    early_exit_score=None,
    max_matches_per_scale=128,
    max_candidates=512,
):
    """Return typed matches for either workflow using one matching engine."""
    if not collect_all:
        score, location, scale, matched_variant = match_template_multiscale(
            frame_bgr,
            template_bgr,
            scales=scales,
            use_grayscale=use_grayscale,
            rotations=rotations,
            variants=variants,
            early_exit_score=early_exit_score,
            cancel_event=cancel_event,
            match_mode=match_mode,
            reference_size=reference_size,
            current_size=current_size,
            reference_sizes=reference_sizes,
            stop_check=stop_check,
            allow_coarse=allow_coarse,
            low_variance_threshold=low_variance_threshold,
            max_variant_pixels=max_variant_pixels,
            return_details=True,
        )
        if location is None or score < confidence:
            return []
        if matched_variant is None:
            width = max(1, round(template_bgr.shape[1] * scale))
            height = max(1, round(template_bgr.shape[0] * scale))
            angle = 0.0
            scale_x = scale_y = scale
        else:
            height, width = matched_variant["image"].shape[:2]
            angle = float(matched_variant.get("angle", 0.0))
            scale_x = float(matched_variant.get("scale_x", scale))
            scale_y = float(matched_variant.get("scale_y", scale))
        return [
            TemplateMatch(
                location[0],
                location[1],
                width,
                height,
                score,
                scale,
                angle,
                scale_x,
                scale_y,
            )
        ]

    match_mode = normalize_match_mode(match_mode, LEGACY_MACRO_MATCH_MODE)
    if variants is None:
        variants = prepare_template_variants(
            template_bgr,
            scales=scales,
            rotations=rotations,
            use_grayscale=use_grayscale,
            match_mode=match_mode,
            reference_size=reference_size,
            current_size=current_size,
            reference_sizes=reference_sizes,
            low_variance_threshold=low_variance_threshold,
            cancel_event=cancel_event,
            stop_check=stop_check,
            max_variant_pixels=max_variant_pixels,
        )
    variants = tuple(variants)
    if variants:
        use_grayscale = bool(variants[0].get("use_grayscale", use_grayscale))
    if match_mode == MATCH_MODE_TEXT:
        profile = variants[0].get("text_profile") if variants else None
        frame = (
            _colored_text_mask(frame_bgr, profile)
            if profile is not None
            else _prepare_match_image(frame_bgr, True)
        )
    else:
        frame = _prepare_match_image(frame_bgr, use_grayscale)

    coarse_factor = None
    coarse_frame = None
    if (
        allow_coarse
        and match_mode != MATCH_MODE_TEXT
        and frame.shape[0] * frame.shape[1] >= 500_000
        and min(template_bgr.shape[:2]) >= 20
        and len(variants) > 4
    ):
        coarse_factor = 0.5 if min(template_bgr.shape[:2]) >= 30 else 0.67
        coarse_frame = cv2.resize(
            frame,
            None,
            fx=coarse_factor,
            fy=coarse_factor,
            interpolation=cv2.INTER_AREA,
        )

    candidates = []

    def full_resolution_peaks(template, variant, text_shape):
        height, width = template.shape[:2]
        scores = _score_map(frame, template, bool(variant["low_variance"]))
        if variant["low_variance"] and scores.size > 1:
            if float(scores.max()) - float(scores.min()) <= 1e-6:
                return []
        peak_threshold = min(float(confidence), 0.35) if text_shape else confidence
        return _bounded_local_peaks(
            scores,
            peak_threshold,
            width,
            height,
            max(1, int(max_matches_per_scale)),
        )

    for variant in variants:
        if _cancelled(cancel_event, stop_check):
            break
        template = variant["image"]
        height, width = template.shape[:2]
        if height > frame.shape[0] or width > frame.shape[1]:
            continue
        text_shape = variant.get("match_mode") == MATCH_MODE_TEXT
        if coarse_frame is not None and coarse_factor is not None:
            small_width = max(1, round(width * coarse_factor))
            small_height = max(1, round(height * coarse_factor))
            if (
                small_width > coarse_frame.shape[1]
                or small_height > coarse_frame.shape[0]
            ):
                continue
            small_template = cv2.resize(
                template,
                (small_width, small_height),
                interpolation=cv2.INTER_AREA,
            )
            small_low_variance = (
                _spatial_deviation(small_template) < low_variance_threshold
            )
            coarse_scores = _score_map(
                coarse_frame,
                small_template,
                small_low_variance,
            )
            coarse_collapsed = False
            if small_low_variance and coarse_scores.size > 1:
                if float(coarse_scores.max()) - float(coarse_scores.min()) <= 1e-6:
                    coarse_collapsed = True
            if coarse_collapsed:
                # High-frequency templates (for example a one-pixel checker)
                # can become flat when downsampled. Full-resolution fallback is
                # required or collect-all would produce a false negative.
                peaks = (
                    full_resolution_peaks(template, variant, text_shape)
                    if not variant["low_variance"]
                    else []
                )
            else:
                # Half-resolution phase changes can lower an exact match sharply;
                # this stage only proposes locations, and full pixels still enforce
                # the real confidence threshold below.
                coarse_threshold = max(0.10, float(confidence) - 0.80)
                if float(coarse_scores.max()) < coarse_threshold:
                    continue
                coarse_peaks = _bounded_local_peaks(
                    coarse_scores,
                    coarse_threshold,
                    small_width,
                    small_height,
                    max(1, int(max_matches_per_scale)),
                )
                peaks = []
                for coarse_x, coarse_y, _coarse_score in coarse_peaks:
                    expected_x = round(coarse_x / coarse_factor)
                    expected_y = round(coarse_y / coarse_factor)
                    margin = max(6, round(max(width, height) * 0.30))
                    left = max(0, expected_x - margin)
                    top = max(0, expected_y - margin)
                    right = min(frame.shape[1], expected_x + width + margin)
                    bottom = min(frame.shape[0], expected_y + height + margin)
                    local = frame[top:bottom, left:right]
                    if local.shape[0] < height or local.shape[1] < width:
                        continue
                    local_scores = _score_map(
                        local,
                        template,
                        bool(variant["low_variance"]),
                    )
                    if variant["low_variance"] and local_scores.size > 1:
                        if (
                            float(local_scores.max()) - float(local_scores.min())
                            <= 1e-6
                        ):
                            continue
                    for x, y, score in _bounded_local_peaks(
                        local_scores,
                        confidence,
                        width,
                        height,
                        max(1, int(max_matches_per_scale)),
                    ):
                        peaks.append((left + x, top + y, score))
        else:
            peaks = full_resolution_peaks(template, variant, text_shape)
        for x, y, raw_score in peaks:
            score = (
                _text_shape_score(frame, template, (x, y)) if text_shape else raw_score
            )
            if score < confidence:
                continue
            candidates.append(
                TemplateMatch(
                    x,
                    y,
                    width,
                    height,
                    score,
                    float(variant["scale"]),
                    float(variant["angle"]),
                    float(variant.get("scale_x", variant["scale"])),
                    float(variant.get("scale_y", variant["scale"])),
                )
            )

    candidates.sort(key=lambda item: item.score, reverse=True)
    candidates = candidates[: max(1, int(max_candidates))]
    kept = []
    for candidate in candidates:
        if _cancelled(cancel_event, stop_check):
            break
        box = (
            candidate.x,
            candidate.y,
            candidate.x + candidate.width,
            candidate.y + candidate.height,
        )
        if any(
            box_iou(
                box,
                (item.x, item.y, item.x + item.width, item.y + item.height),
            )
            > 0.3
            for item in kept
        ):
            continue
        kept.append(candidate)
    kept.sort(key=lambda item: (item.y, item.x))
    return kept
