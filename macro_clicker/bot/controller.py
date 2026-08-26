"""Small coordinator for user-facing bot features.

It deliberately does not merge Rally/Gather/Position logic. It serializes
clicking automations and leaves passive alerts free to run alongside them.
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
# normal scenario is continuous and would otherwise prevent later jobs from
# ever receiving control of the input engine.
BOT_FEATURE_PRIORITY = (
    FEATURE_DEVELOPMENT,
    FEATURE_SCIENCE,
    FEATURE_GATHER,
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
    """Run enabled clicking automations sequentially and track dashboard state."""

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
        """Read-only queued work for normal-user status displays."""

        return tuple(self._pending_features)

    def enabled_features(self) -> list[str]:
        config = self._config_provider()
        enabled = {
            FEATURE_DEVELOPMENT: config.positions.development_enabled,
            FEATURE_SCIENCE: config.positions.science_enabled,
            FEATURE_GATHER: config.gather.enabled,
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

        # A start failure means the controller cannot establish the expected
        # state for this stage. Do not silently skip ahead and give a different
        # workflow ownership of the mouse/keyboard from an uncertain state.
        self._finish_session(f"Bot cycle stopped: could not start {feature.title()}")
        return False

    def start(self) -> bool:
        """Start a serialized run of all currently enabled clicking features."""

        if self.status.active_feature is not None:
            self.status.last_message = (
                f"{self.status.active_feature.title()} is already running"
            )
            return False
        self._pending_features = self.enabled_features()
        if not self._pending_features:
            self.status.last_message = "No clicking automation is enabled"
            return False
        self.status.session_active = True
        return self._start_next_session_feature()

    def run_feature(self, feature: str) -> bool:
        """Run one feature directly without creating a multi-feature bot cycle."""

        if self.status.active_feature is not None:
            self.status.last_message = (
                f"{self.status.active_feature.title()} is already running"
            )
            return False
        self._pending_features.clear()
        self.status.session_active = False
        return self._start_feature(feature)

    def engine_stopped(self) -> None:
        """Record a finished scenario and continue an active bot cycle if needed."""

        feature = self.status.active_feature
        self.status.running = False
        self.status.active_feature = None
        if self.status.session_active:
            self._start_next_session_feature()
            return
        if feature:
            self.status.last_message = f"{feature.title()} stopped"

    def stop(self) -> bool:
        """Stop the current feature and discard any queued bot-cycle work."""

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
