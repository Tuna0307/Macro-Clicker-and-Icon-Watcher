"""Automatic zombie-boss rally join policy skeleton."""

from __future__ import annotations

from typing import Optional, Sequence

from ..contracts import (
    BossCandidate,
    BotResult,
    CapabilityStatus,
    GamePort,
    RallyCandidate,
)


class RallyJoinController:
    def __init__(self, game: GamePort):
        self._game = game

    def choose_rally(
        self, rallies: Sequence[RallyCandidate]
    ) -> Optional[RallyCandidate]:
        """Minimal deterministic policy placeholder.

        Prefer known available-slot counts, then stable rally id ordering.  Future
        policy can add boss level, server, march availability, cooldowns, etc.
        """
        if not rallies:
            return None
        return min(
            rallies,
            key=lambda rally: (
                rally.slots_available is None,
                -(rally.slots_available or 0),
                rally.rally_id,
            ),
        )

    def run(self, bosses: Sequence[BossCandidate]) -> BotResult:
        rallies = tuple(self._game.list_joinable_rallies(bosses))
        selected = self.choose_rally(rallies)
        if selected is None:
            return BotResult(
                ok=False,
                status=CapabilityStatus.IMPLEMENTED_NOT_LIVE_TESTED,
                message="No joinable zombie-boss rally was observed.",
            )
        return self._game.join_rally(selected)
