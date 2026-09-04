"""Route a GoldMob row with no same-row Join ``+`` into normal Back refresh.

A 2026-09-04 live three-team run exposed a multi-rally layout deadloop.  When a
non-gold rally appeared above the desired GoldMob rally, the desired row moved
from roughly y=283 to y=560.  The engine's row-local Join search correctly found
no ``Join.png`` target near that GoldMob row, but the generic AND condition gate
returned before ``click_matching_row`` could execute.  The workflow therefore
sat on the Rally page repeatedly logging ``BLOCKED at LastSlot+`` until a slower
unrelated recovery eventually fired.

The row-action itself already has the safe behavior we want: an empty set of
same-row Join targets becomes ``no_eligible_row`` and uses the configured
``BackButton.png`` fallback.  v15 only changes the three-team ``Joining`` gate so
a positively found GoldMob reference with zero row-local Join targets is allowed
to reach that existing no-match branch.

This does *not* make a Join target from another rally eligible.  The original
row-local search remains authoritative and is still bounded around the GoldMob
reference rows.  It also does not change the legacy two-team path.
"""

from __future__ import annotations

from . import rally_hot_path_runtime as _hot

BUILD_MARKER = "JOIN-HOT-RACE-v15 multi-row no-slot Back routing"

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_EVALUATE_LOCAL_TARGET = None


def _matching_row_action_for_target(step, index):
    return next(
        (
            action
            for action in getattr(step, "actions", ())
            if getattr(action, "type", None) == "click_matching_row"
            and getattr(action, "on_condition_index", None) == index
            and getattr(action, "match_condition_index", None) is not None
        ),
        None,
    )


def _evaluate_matching_row_target_locally(
    engine,
    step,
    index,
    cond,
    matches,
    frame_cache,
    *,
    collect_all,
):
    """Let a proven GoldMob/no-same-row-Join state reach no-match Back."""

    result = _ORIGINAL_EVALUATE_LOCAL_TARGET(
        engine,
        step,
        index,
        cond,
        matches,
        frame_cache,
        collect_all=collect_all,
    )
    if result is None:
        return None

    ok, target_matches = result
    if ok or target_matches:
        return result
    if not _hot._is_three_team(engine) or getattr(step, "name", None) != "Joining":
        return result

    action = _matching_row_action_for_target(step, index)
    if action is None:
        return result

    reference_index = getattr(action, "match_condition_index", None)
    references = list(matches.get(reference_index, ()))
    if not references:
        return result

    # The original evaluator has already searched only inside bands around the
    # GoldMob references.  An empty result therefore means there is no Join +
    # belonging to any desired GoldMob row in this atomic snapshot.  Report the
    # condition as pass-with-no-target so _evaluate_step continues to the
    # optional BackButton condition and click_matching_row can execute its
    # existing no_eligible_row -> Back fallback.
    engine.log(
        "  [rally-v15] GoldMob found but no same-row Join +; "
        "routing to no-match Back refresh"
    )
    return True, []


def install_rally_hot_path_v15_runtime():
    """Install multi-row no-slot Back routing after v14."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_EVALUATE_LOCAL_TARGET
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_EVALUATE_LOCAL_TARGET = MacroEngine._evaluate_matching_row_target_locally

    def start(self):
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start
    MacroEngine._evaluate_matching_row_target_locally = (
        _evaluate_matching_row_target_locally
    )
    _INSTALLED = True
