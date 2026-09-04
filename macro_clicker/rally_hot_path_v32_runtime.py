"""Bound three-team Rally OCR latency by using cross-crop consensus.

A 2026-09-05 v28 live run recognized a Lv80+ GoldMob row and its same-row Join
button correctly, but the level OCR spent about 18.65 seconds inside one crop
because recognition returned a high-confidence bare ``80`` without a literal
Lv/Level prefix. The existing reader then exhausted many sharpened/threshold
variants inside that single crop. By the time it safely accepted 80, the Join
button had vanished and the fresh input-boundary revalidation correctly
cancelled the stale click.

The surrounding Rally row reader already captures up to six independent vertical
crops and already requires repeated provisional agreement before accepting a
non-strong result. v32 therefore avoids the expensive *inside-one-crop*
exhaustive fallback only for explicit three-team Rally rows:

* one literal high-confidence Lv/Level result is still strong immediately;
* a high-confidence bare/corrected-prefix number is deliberately returned below
  the strong threshold so the outer cross-crop consensus must corroborate it;
* low-confidence or unreadable single-crop results stay unreadable;
* the outer reader keeps its existing two-crop provisional consensus rule.

This changes latency, not the level limit or final Attack safety. Legacy
two-team behavior is untouched.
"""

from __future__ import annotations

import threading
import time

from . import rally_hot_path_runtime as _hot
from . import rally_matching as _rm
from .level_ocr import LevelOcrReader, LevelOcrResult

BUILD_MARKER = "JOIN-HOT-RACE-v32 bounded cross-crop level OCR"

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_READ_LEVEL_FOR_ROW = None
_ORIGINAL_READ_LEVEL_LOCKED = None
_THREAD_STATE = threading.local()


def _stats():
    stats = getattr(_THREAD_STATE, "stats", None)
    if stats is None:
        stats = {"literal": 0, "provisional": 0, "unread": 0}
        _THREAD_STATE.stats = stats
    return stats


def _provisional_result(result):
    confidence = result.confidence
    if confidence is None:
        confidence = LevelOcrReader.MIN_ACCEPT_CONFIDENCE
    confidence = min(
        float(confidence),
        LevelOcrReader.STRONG_ACCEPT_CONFIDENCE - 0.001,
    )
    return LevelOcrResult(
        result.level,
        text=result.text,
        confidence=confidence,
        engine=result.engine,
        error=result.error,
    )


def _read_level_locked(reader, frame):
    if not getattr(_THREAD_STATE, "three_team_row_read", False):
        return _ORIGINAL_READ_LEVEL_LOCKED(reader, frame)

    # Keep the original validation/error handling for malformed input. That path
    # is cheap and should retain the reader's exact semantics.
    if (
        frame is None
        or not hasattr(frame, "size")
        or frame.size == 0
        or getattr(frame, "ndim", None) not in (2, 3)
    ):
        return _ORIGINAL_READ_LEVEL_LOCKED(reader, frame)

    try:
        fast_image = reader._preprocess_fast_variant(frame)
        result = reader._run_text_recognition(fast_image)
    except Exception:
        # Fail closed through the original reader if preprocessing itself broke.
        return _ORIGINAL_READ_LEVEL_LOCKED(reader, frame)

    stats = _stats()

    if reader._is_fast_path_result(result):
        stats["literal"] += 1
        return result

    if reader._is_acceptable_result(result):
        # Do not let the outer row reader mistake one bare/corrected-prefix
        # observation for a strong result merely because recognition confidence
        # is high. It must agree with another independent crop.
        stats["provisional"] += 1
        return _provisional_result(result)

    stats["unread"] += 1
    return LevelOcrResult(
        None,
        text="" if result is None else result.text,
        confidence=None if result is None else result.confidence,
        engine="paddleocr_rec" if result is None else result.engine,
        error=None if result is None else result.error,
    )


def _read_level_for_row(engine, action, reference):
    if not _hot._is_three_team(engine):
        return _ORIGINAL_READ_LEVEL_FOR_ROW(engine, action, reference)

    previous_active = getattr(_THREAD_STATE, "three_team_row_read", False)
    previous_stats = getattr(_THREAD_STATE, "stats", None)
    _THREAD_STATE.three_team_row_read = True
    _THREAD_STATE.stats = {"literal": 0, "provisional": 0, "unread": 0}
    started = time.perf_counter()
    result = None
    try:
        result = _ORIGINAL_READ_LEVEL_FOR_ROW(engine, action, reference)
        return result
    finally:
        elapsed = time.perf_counter() - started
        stats = dict(getattr(_THREAD_STATE, "stats", {}))
        _THREAD_STATE.three_team_row_read = previous_active
        _THREAD_STATE.stats = previous_stats
        engine.log(
            "  [rally-v32] cross-crop level OCR "
            f"result={result if result is not None else 'unread'} "
            f"elapsed={elapsed:.3f}s literal={stats.get('literal', 0)} "
            f"provisional={stats.get('provisional', 0)} "
            f"unread={stats.get('unread', 0)}"
        )


def install_rally_hot_path_v32_runtime():
    """Install bounded three-team cross-crop OCR after v31."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_READ_LEVEL_FOR_ROW
    global _ORIGINAL_READ_LEVEL_LOCKED
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_READ_LEVEL_FOR_ROW = _rm.RallyMatchingMixin._read_level_for_row
    _ORIGINAL_READ_LEVEL_LOCKED = LevelOcrReader._read_level_locked

    def start(self):
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start
    _rm.RallyMatchingMixin._read_level_for_row = _read_level_for_row
    LevelOcrReader._read_level_locked = _read_level_locked
    _INSTALLED = True
