"""Server-jump feature policy.

This is the first priority feature, but no game-specific interaction is invented
here.  The GamePort must provide a verified implementation later.
"""

from __future__ import annotations

from ..contracts import BotResult, CapabilityStatus, GamePort, ServerRef


class ServerJumpController:
    status = CapabilityStatus.NOT_IMPLEMENTED

    def __init__(self, game: GamePort):
        self._game = game

    def run(self, target: ServerRef) -> BotResult:
        if target.server_id <= 0:
            return BotResult(
                ok=False,
                status=CapabilityStatus.NOT_IMPLEMENTED,
                message="Target server id must be positive.",
            )

        current = self._game.current_server()
        if current is not None and current.server_id == target.server_id:
            return BotResult(
                ok=True,
                status=CapabilityStatus.IMPLEMENTED_NOT_LIVE_TESTED,
                message=f"Already on server {target.server_id}.",
                data={"server_id": target.server_id, "changed": False},
            )

        # Future GamePort implementations own the concrete interaction and MUST
        # verify the resulting server after the jump rather than trusting a click.
        result = self._game.jump_to_server(target)
        if not result.ok:
            return result

        observed = self._game.current_server()
        if observed is None or observed.server_id != target.server_id:
            return BotResult(
                ok=False,
                status=CapabilityStatus.LIVE_TEST_FAILED,
                message=(
                    "Server-jump action returned success but post-action server "
                    "verification did not match the requested target."
                ),
                data={
                    "requested_server_id": target.server_id,
                    "observed_server_id": (
                        observed.server_id if observed is not None else None
                    ),
                },
            )
        return BotResult(
            ok=True,
            status=result.status,
            message=f"Verified server {target.server_id}.",
            data={"server_id": target.server_id, "changed": True},
        )
