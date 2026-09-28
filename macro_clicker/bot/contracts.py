"""Domain contracts shared by the new bot feature skeleton."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Sequence


class CapabilityStatus(str, Enum):
    """Evidence status used by the bot skeleton.

    Do not promote a feature to LIVE_PROVEN without a supervised live-game proof.
    """

    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    IMPLEMENTED_NOT_LIVE_TESTED = "IMPLEMENTED_NOT_LIVE_TESTED"
    LIVE_TEST_FAILED = "LIVE_TEST_FAILED"
    LIVE_PROVEN = "LIVE_PROVEN"


@dataclass(frozen=True)
class ServerRef:
    server_id: int
    label: str = ""


@dataclass(frozen=True)
class BossCandidate:
    """Observed zombie-boss candidate.

    Keep this model independent from how the boss was discovered (vision, protocol,
    memory, or another future source).
    """

    boss_id: str
    server: Optional[ServerRef] = None
    level: Optional[int] = None
    x: Optional[int] = None
    y: Optional[int] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RallyCandidate:
    rally_id: str
    boss: Optional[BossCandidate] = None
    leader: str = ""
    slots_available: Optional[int] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BotCapability:
    name: str
    status: CapabilityStatus
    detail: str


@dataclass(frozen=True)
class BotResult:
    ok: bool
    status: CapabilityStatus
    message: str
    data: Mapping[str, Any] = field(default_factory=dict)


class GamePort(Protocol):
    """High-level game boundary used by feature controllers.

    A future implementation may be vision-driven, protocol-driven, or hybrid.
    Feature policy must not know whether the implementation uses screenshots,
    Win32 messages, an internal runtime, or another safe transport.
    """

    def current_server(self) -> Optional[ServerRef]:
        ...

    def jump_to_server(self, target: ServerRef) -> BotResult:
        ...

    def scan_zombie_bosses(self) -> Sequence[BossCandidate]:
        ...

    def list_joinable_rallies(
        self, bosses: Sequence[BossCandidate]
    ) -> Sequence[RallyCandidate]:
        ...

    def join_rally(self, rally: RallyCandidate) -> BotResult:
        ...
