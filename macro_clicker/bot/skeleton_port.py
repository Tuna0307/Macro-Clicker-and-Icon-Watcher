"""Fail-closed GamePort placeholder used by smoke tooling and future development."""

from __future__ import annotations

from typing import Optional, Sequence

from .contracts import (
    BossCandidate,
    BotResult,
    CapabilityStatus,
    RallyCandidate,
    ServerRef,
)


class SkeletonGamePort:
    """Explicit placeholder: nothing here pretends to control the live game."""

    def current_server(self) -> Optional[ServerRef]:
        return None

    def jump_to_server(self, target: ServerRef) -> BotResult:
        return BotResult(
            ok=False,
            status=CapabilityStatus.NOT_IMPLEMENTED,
            message=(
                "Server-jump game binding is not implemented. "
                "Add a verified GamePort implementation first."
            ),
            data={"requested_server_id": target.server_id},
        )

    def scan_zombie_bosses(self) -> Sequence[BossCandidate]:
        return ()

    def list_joinable_rallies(
        self, bosses: Sequence[BossCandidate]
    ) -> Sequence[RallyCandidate]:
        return ()

    def join_rally(self, rally: RallyCandidate) -> BotResult:
        return BotResult(
            ok=False,
            status=CapabilityStatus.NOT_IMPLEMENTED,
            message="Rally-join game binding is not implemented.",
            data={"rally_id": rally.rally_id},
        )
