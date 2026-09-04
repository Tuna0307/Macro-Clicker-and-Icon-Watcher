"""Recognize the Lv80+ GoldMob artwork as the same three-team Rally mob.

Last War changes the gold-mob portrait from level 80 onward. The three-team
Rally scenarios still use ``templates/GoldMob.png`` as their row identity
anchor, so the new artwork would otherwise look like a wrong mob and be backed
out of even when OCR/Team limits allow level 80.

v16 keeps the existing GoldMob template authoritative and adds one alternate
portrait only when the original positive GoldMob condition misses in explicit
three-team mode. The alternate portrait was cropped from the user-confirmed
Lv80 Rally screenshot and stored as a compact embedded JPEG. It participates in
the normal condition/match pipeline, so row-local Join pairing, level OCR, v9
Rally-entry progress, and v15 no-slot Back routing all continue to use the same
GoldMob condition index.

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
Fe8z/9k=
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
    """Try normal GoldMob first, then the Lv80+ portrait in three-team mode."""

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
