"""Small coordinator for user-facing bot features.

It deliberately does not merge Rally/Gather/Position logic. It serializes
clicking automations and leaves passive observers free to run alongside them.
Continuous Auto Gather has its own state-driven service and is therefore not a
finite queue stage here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .config import BotConfig

FEATURE_RALLY = "rally"
FEATURE_GATHER = "gather"
FEATURE_DEVELOPMENT = "development"
FEATURE_SCIENCE = "science"

FEATURE_LABELS = {
    FEATURE_RALLY: "Gold Mob Rally",
    FEATURE_GATHER: "Auto Gather",
    FEATURE_DEVELOPMENT: "Development Position",
    FEATURE_SCIENCE: "Science Position",
}

# Finite setup-style jobs run first. Rally is deliberately last because its
# normal scenario is continuous. Gather is intentionally absent: the dedicated
# ContinuousGatherService watches actual Team 1/2/3 state instead of behaving
# like a one-shot queued macro.
BOT_FEATURE_PRIORITY = (
    FEATURE_DEVELOPMENT,
    FEATURE_SCIENCE,
    FEATURE_RALLY,
)


@dataclass
class BotStatus:
    running: bool = False
    session_active: bool = False
    active_feature: Optional[str] = None
    last_feature: Optional[str] = None
    last_message: str = "Ready"


class BotController:
    """Run enabled finite/continuous clicking scenarios sequentially."""

    def __init__(
        self,
        config_provider: Callable[[], BotConfig],
        runner: Callable[[str], bool],
        stopper: Callable[[], bool],
    ) -> None:
        self._config_provider = config_provider
        self._runner = runner
        self._stopper = stopper
        self._pending_features: list[str] = []
        self.status = BotStatus()

    @property
    def pending_features(self) -> tuple[str, ...]:
        return tuple(self._pending_features)

    def enabled_features(self) -> list[str]:
        config = self._config_provider()
        enabled = {
            FEATURE_DEVELOPMENT: config.positions.development_enabled,
            FEATURE_SCIENCE: config.positions.science_enabled,
            FEATURE_RALLY: config.rally.enabled,
        }
        return [feature for feature in BOT_FEATURE_PRIORITY if enabled[feature]]

    def _start_feature(self, feature: str) -> bool:
        if not self._runner(feature):
            self.status.last_message = f"Could not start {feature.title()}"
            return False
        self.status.running = True
        self.status.active_feature = feature
        self.status.last_feature = feature
        self.status.last_message = f"Running {feature.title()}"
        return True

    def _finish_session(self, message: str) -> None:
        self._pending_features.clear()
        self.status.running = False
        self.status.session_active = False
        self.status.active_feature = None
        self.status.last_message = message

    def _start_next_session_feature(self) -> bool:
        if not self._pending_features:
            self._finish_session("Bot cycle completed")
            return False
        feature = self._pending_features.pop(0)
        if self._start_feature(feature):
            return True
        self._finish_session(f"Bot cycle stopped: could not start {feature.title()}")
        return False

    def start(self) -> bool:
        if self.status.active_feature is not None:
            self.status.last_message = f"{self.status.active_feature.title()} is already running"
            return False
        self._pending_features = self.enabled_features()
        if not self._pending_features:
            self.status.last_message = "No queued clicking automation is enabled"
            return False
        self.status.session_active = True
        return self._start_next_session_feature()

    def run_feature(self, feature: str) -> bool:
        if self.status.active_feature is not None:
            self.status.last_message = f"{self.status.active_feature.title()} is already running"
            return False
        self._pending_features.clear()
        self.status.session_active = False
        return self._start_feature(feature)

    def engine_stopped(self) -> None:
        feature = self.status.active_feature
        self.status.running = False
        self.status.active_feature = None
        if self.status.session_active:
            self._start_next_session_feature()
            return
        if feature:
            self.status.last_message = f"{feature.title()} stopped"

    def stop(self) -> bool:
        self._pending_features.clear()
        self.status.session_active = False
        if self.status.active_feature is None:
            self.status.running = False
            self.status.last_message = "Stopped"
            return True
        stopped = bool(self._stopper())
        self.status.last_message = "Stopping…" if not stopped else "Stopped"
        if stopped:
            self.status.running = False
            self.status.active_feature = None
        return stopped
