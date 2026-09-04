"""Speed the safe unprefixed level-OCR consensus path for three-team Rally.

A 2026-09-05 live v27 run proved that Rally entry itself was fast again: the
first Joining scan began about 0.30 s after the world-map Rally click.  The same
run exposed one separate 16 s stall while reading a Lv85 row.  Text recognition
returned a high-confidence bare ``85`` without a literal Lv/Level prefix.  The
existing OCR safety policy correctly refused to trust one bare number, but its
fallback walked sharpened/threshold variants before reaching another independent
plain crop that could corroborate the same number.

v28 changes only the order of those already-existing fallback variants while an
explicit three-team Rally row is being read.  It tries independent plain crops
first, then preserves every original sharpened/threshold variant as fallback.
The acceptance rules are untouched: one unprefixed number is still provisional,
and repeated high-confidence agreement is still required.

The legacy two-team Rally path and every final Attack safeguard are untouched.
"""

from __future__ import annotations

import threading
import time

from . import rally_hot_path_runtime as _hot
from . import rally_matching as _rm
from .level_ocr import LevelOcrReader

BUILD_MARKER = "JOIN-HOT-RACE-v28 fast unprefixed OCR consensus"

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_READ_LEVEL_FOR_ROW = None
_ORIGINAL_PREPROCESS_VARIANTS = None
_THREAD_STATE = threading.local()


def _reordered_variant_indices(reader, variant_count):
    """Return a safe ordering that keeps the reader's fast-index invariant."""

    try:
        per_region = int(reader._VARIANTS_PER_REGION)
        fast_region = int(reader._FAST_REGION_INDEX)
    except (AttributeError, TypeError, ValueError):
        return None

    if per_region != 3 or fast_region != 1 or variant_count <= 0:
        return None
    if variant_count % per_region:
        return None

    region_count = variant_count // per_region
    if region_count < 4:
        return None

    fast_index = fast_region * per_region
    if fast_index >= variant_count:
        return None

    # Plain variants are offset 0 in each [plain, sharpened, threshold] group.
    # Region 1 is the existing one-read fast crop and must remain at index 3
    # because LevelOcrReader deliberately skips that duplicate in its fallback.
    preferred_regions = [2, 3, 0]
    preferred_regions.extend(
        region
        for region in range(region_count)
        if region not in {0, 1, 2, 3}
    )

    prefix = [region * per_region for region in preferred_regions]
    prefix.insert(min(3, len(prefix)), fast_index)

    seen = set()
    ordered = []
    for index in prefix:
        if 0 <= index < variant_count and index not in seen:
            seen.add(index)
            ordered.append(index)

    # Preserve every original variant exactly once after the plain-crop prefix.
    for index in range(variant_count):
        if index not in seen:
            seen.add(index)
            ordered.append(index)

    return ordered if len(ordered) == variant_count else None


def _preprocess_variants(reader, frame):
    variants = _ORIGINAL_PREPROCESS_VARIANTS(reader, frame)
    if not getattr(_THREAD_STATE, "three_team_row_read", False):
        return variants

    order = _reordered_variant_indices(reader, len(variants))
    if order is None:
        return variants

    _THREAD_STATE.reordered_fallback = True
    return [variants[index] for index in order]


def _read_level_for_row(engine, action, reference):
    if not _hot._is_three_team(engine):
        return _ORIGINAL_READ_LEVEL_FOR_ROW(engine, action, reference)

    previous_active = getattr(_THREAD_STATE, "three_team_row_read", False)
    previous_reordered = getattr(_THREAD_STATE, "reordered_fallback", False)
    _THREAD_STATE.three_team_row_read = True
    _THREAD_STATE.reordered_fallback = False
    started = time.perf_counter()
    try:
        return _ORIGINAL_READ_LEVEL_FOR_ROW(engine, action, reference)
    finally:
        elapsed = time.perf_counter() - started
        used_reordered_fallback = bool(
            getattr(_THREAD_STATE, "reordered_fallback", False)
        )
        _THREAD_STATE.three_team_row_read = previous_active
        _THREAD_STATE.reordered_fallback = previous_reordered
        if used_reordered_fallback:
            engine.log(
                "  [rally-v28] unprefixed/uncertain level OCR used "
                "plain-crop-first consensus ordering; "
                f"row read completed in {elapsed:.3f}s"
            )


def install_rally_hot_path_v28_runtime():
    """Install the three-team-only OCR fallback ordering after v27."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_READ_LEVEL_FOR_ROW
    global _ORIGINAL_PREPROCESS_VARIANTS
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_READ_LEVEL_FOR_ROW = _rm.RallyMatchingMixin._read_level_for_row
    _ORIGINAL_PREPROCESS_VARIANTS = LevelOcrReader._preprocess_variants

    def start(self):
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start
    _rm.RallyMatchingMixin._read_level_for_row = _read_level_for_row
    LevelOcrReader._preprocess_variants = _preprocess_variants
    _INSTALLED = True
