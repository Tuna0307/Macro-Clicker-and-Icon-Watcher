"""Top-level orchestration for the additive bot skeleton."""

from __future__ import annotations

from typing import Sequence

from .contracts import (
    BossCandidate,
    BotCapability,
    BotResult,
    CapabilityStatus,
    GamePort,
    ServerRef,
)
from .features import BossScanController, RallyJoinController, ServerJumpController


class BotRuntime:
    """Feature facade intentionally isolated from the existing MacroEngine."""

    def __init__(self, game: GamePort):
        self.game = game
        self.server_jump = ServerJumpController(game)
        self.boss_scan = BossScanController(game)
        self.rally_join = RallyJoinController(game)

    def capabilities(self) -> tuple[BotCapability, ...]:
        return (
            BotCapability(
                name="cursor_free_input",
                status=CapabilityStatus.IMPLEMENTED_NOT_LIVE_TESTED,
                detail=(
                    "Win32 message backend exists and does not move the physical "
                    "cursor; target-game acceptance still needs live proof."
                ),
            ),
            BotCapability(
                name="server_jump",
                status=CapabilityStatus.NOT_IMPLEMENTED,
                detail="Feature contract + post-jump verification skeleton only.",
            ),
            BotCapability(
                name="zombie_boss_scan",
                status=CapabilityStatus.NOT_IMPLEMENTED,
                detail="Feature contract only; discovery backend still required.",
            ),
            BotCapability(
                name="auto_join_zombie_boss_rally",
                status=CapabilityStatus.NOT_IMPLEMENTED,
                detail="Selection policy skeleton only; game binding still required.",
            ),
        )

    def jump_to_server(self, server_id: int, label: str = "") -> BotResult:
        return self.server_jump.run(ServerRef(server_id=server_id, label=label))

    def scan_bosses(self) -> Sequence[BossCandidate]:
        return self.boss_scan.run()

    def scan_and_join_rally(self) -> BotResult:
        bosses = tuple(self.scan_bosses())
        return self.rally_join.run(bosses)
