"""Zombie-boss scan feature policy skeleton."""

from __future__ import annotations

from typing import Sequence

from ..contracts import BossCandidate, GamePort


class BossScanController:
    def __init__(self, game: GamePort):
        self._game = game

    def run(self) -> Sequence[BossCandidate]:
        """Return observed boss candidates.

        Discovery strategy intentionally belongs behind GamePort.  Codex can later
        add a visual, protocol, or hybrid scanner without changing this controller.
        """
        return tuple(self._game.scan_zombie_bosses())
