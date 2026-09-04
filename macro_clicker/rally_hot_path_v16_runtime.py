"""Recognize the Lv80+ GoldMob artwork as the same three-team Rally mob.

Last War changes the gold-mob portrait from level 80 onward.  The three-team
Rally scenarios still use ``templates/GoldMob.png`` as their row identity
anchor, so the new artwork would otherwise look like a wrong mob and be backed
out of even when OCR/Team limits allow level 80.

v16 keeps the existing GoldMob template authoritative and adds one alternate
portrait only when the original positive GoldMob condition misses in explicit
three-team mode.  The alternate portrait was cropped from the user-confirmed
Lv80 Rally screenshot.  It participates in the normal condition/match pipeline,
so row-local Join pairing, level OCR, v9 Rally-entry progress, and v15 no-slot
Back routing all continue to use the same GoldMob condition index.

The legacy two-team path is intentionally unchanged.
"""

from __future__ import annotations

import base64
import os
import time

import cv2
import numpy as np

from . import rally_hot_path_runtime as _hot

BUILD_MARKER = "JOIN-HOT-RACE-v16 high-level GoldMob variant"
_HIGH_LEVEL_GOLD_TEMPLATE_B64 = """
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
""".strip()

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_EVALUATE_TEMPLATE_CONDITION = None
_HIGH_LEVEL_GOLD_TEMPLATE = None


def _high_level_gold_template():
    global _HIGH_LEVEL_GOLD_TEMPLATE
    if _HIGH_LEVEL_GOLD_TEMPLATE is None:
        raw = base64.b64decode(_HIGH_LEVEL_GOLD_TEMPLATE_B64)
        encoded = np.frombuffer(raw, dtype=np.uint8)
        decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if decoded is None or decoded.size == 0:
            raise RuntimeError("embedded Lv80+ GoldMob template could not be decoded")
        _HIGH_LEVEL_GOLD_TEMPLATE = decoded
    return _HIGH_LEVEL_GOLD_TEMPLATE


def _is_positive_gold_condition(cond):
    return (
        os.path.basename(getattr(cond, "template_path", "") or "").casefold()
        == "goldmob.png"
        and not getattr(cond, "negate", False)
        and not getattr(cond, "comparison_template_path", "")
    )


def _evaluate_template_condition(
    engine,
    index,
    cond,
    frame,
    off_x,
    off_y,
    collect_all,
):
    """Try the normal GoldMob first, then the Lv80+ portrait in three-team mode."""

    result = _ORIGINAL_EVALUATE_TEMPLATE_CONDITION(
        engine,
        index,
        cond,
        frame,
        off_x,
        off_y,
        collect_all,
    )
    ok, matches = result
    if ok or matches:
        return result
    if not _hot._is_three_team(engine) or not _is_positive_gold_condition(cond):
        return result
    if frame is None or getattr(frame, "size", 0) == 0:
        return result

    template_matches = engine._find_template_matches_in_frame(
        frame,
        _high_level_gold_template(),
        cond.confidence,
        collect_all=collect_all,
        allow_coarse=True,
        **engine._condition_matching_kwargs(cond),
    )
    if not template_matches:
        return result

    runtime_matches = engine._template_matches_to_runtime_matches(
        index,
        cond,
        template_matches,
        off_x,
        off_y,
    )
    if not runtime_matches:
        return result

    now = time.monotonic()
    last_log = float(getattr(engine, "_rally_v16_last_high_gold_log", 0.0))
    if now - last_log >= 2.0:
        engine._rally_v16_last_high_gold_log = now
        engine.log(
            "  [rally-v16] Lv80+ GoldMob artwork matched; "
            "treating row as GoldMob"
        )
    return True, runtime_matches


def install_rally_hot_path_v16_runtime():
    """Install the Lv80+ GoldMob alternate matcher after v15."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_EVALUATE_TEMPLATE_CONDITION
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_EVALUATE_TEMPLATE_CONDITION = MacroEngine._evaluate_template_condition

    def start(self):
        self._rally_v16_last_high_gold_log = 0.0
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start
    MacroEngine._evaluate_template_condition = _evaluate_template_condition
    _INSTALLED = True
