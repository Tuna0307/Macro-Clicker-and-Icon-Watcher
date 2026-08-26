"""Small runtime state machine for the resource-gathering workflow.

The scenario engine still owns screen detection and input safety. This module only
tracks information that should not be encoded by duplicating scenario steps:
how many gathering marches have succeeded and which occupied march should be
replaced next.
"""

from dataclasses import dataclass
from typing import Sequence

DEFAULT_GATHER_TARGET_COUNT = 3
DEFAULT_REPLACEMENT_ORDER = (3, 2, 1)
GATHER_COMMANDS = frozenset({"select_replacement", "record_success", "cancel_retry"})

# Offsets are relative to the center of the proven "no free march" detection.
# They are expressed at the scenario's 1920x1080 reference size; MacroEngine
# scales them with the matched template geometry before clicking.
REPLACEMENT_CLICK_OFFSETS = {
    3: (63, 630),
    2: (-61, 630),
    1: (-188, 630),
}


@dataclass(frozen=True)
class GatherProgress:
    successful_dispatches: int
    next_replacement_march: int | None
    used_replacement: bool
    complete: bool


class GatherController:
    """Track gather progress independently from the visual scenario steps."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.successful_dispatches = 0
        self.replacement_index = 0
        self.replacement_pending = False

    @staticmethod
    def _normalized_order(order: Sequence[int]) -> tuple[int, ...]:
        return tuple(int(march) for march in order)

    def current_replacement(self, order: Sequence[int]) -> int | None:
        replacement_order = self._normalized_order(order)
        if self.replacement_index >= len(replacement_order):
            return None
        return replacement_order[self.replacement_index]

    def mark_replacement_selected(self, order: Sequence[int]) -> int | None:
        """Mark the current replacement as pending after its click succeeds."""
        march = self.current_replacement(order)
        if march is not None:
            self.replacement_pending = True
        return march

    def cancel_retry(self) -> None:
        """Retry the same logical dispatch without consuming replacement order."""
        self.replacement_pending = False

    def record_success(
        self,
        *,
        target_count: int = DEFAULT_GATHER_TARGET_COUNT,
        replacement_order: Sequence[int] = DEFAULT_REPLACEMENT_ORDER,
    ) -> GatherProgress:
        used_replacement = self.replacement_pending
        self.successful_dispatches += 1
        if used_replacement:
            self.replacement_index += 1
        self.replacement_pending = False
        return GatherProgress(
            successful_dispatches=self.successful_dispatches,
            next_replacement_march=self.current_replacement(replacement_order),
            used_replacement=used_replacement,
            complete=self.successful_dispatches >= int(target_count),
        )


def replacement_click_offset(march_number: int) -> tuple[int, int]:
    try:
        return REPLACEMENT_CLICK_OFFSETS[int(march_number)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"unsupported gathering march: {march_number!r}") from exc
