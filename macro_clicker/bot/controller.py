"""Small coordinator for user-facing bot features.

It deliberately does not merge Rally/Gather/Position logic.  It serializes
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


@dataclass
class BotStatus:
    running: bool = False
    active_feature: Optional[str] = None
    last_feature: Optional[str] = None
    last_message: str = "Ready"


class BotController:
    """Choose one clicking automation at a time and track dashboard state."""

    def __init__(
        self,
        config_provider: Callable[[], BotConfig],
        runner: Callable[[str], bool],
        stopper: Callable[[], bool],
    ) -> None:
        self._config_provider = config_provider
        self._runner = runner
        self._stopper = stopper
        self.status = BotStatus()

    def enabled_features(self) -> list[str]:
        config = self._config_provider()
        features: list[str] = []
        if config.rally.enabled:
            features.append(FEATURE_RALLY)
        if config.positions.development_enabled:
            features.append(FEATURE_DEVELOPMENT)
        if config.positions.science_enabled:
            features.append(FEATURE_SCIENCE)
        if config.gather.enabled:
            features.append(FEATURE_GATHER)
        return features

    def start(self) -> bool:
        """Start the highest-priority enabled clicking automation.

        The current backend scenarios do not yet expose cooperative yield points,
        so the controller intentionally does not pretend to time-slice Rally and
        Gather.  Users can still run any feature directly from its tab.
        """

        features = self.enabled_features()
        if not features:
            self.status.last_message = "No clicking automation is enabled"
            return False
        return self.run_feature(features[0])

    def run_feature(self, feature: str) -> bool:
        if self.status.active_feature is not None:
            self.status.last_message = (
                f"{self.status.active_feature.title()} is already running"
            )
            return False
        if not self._runner(feature):
            self.status.last_message = f"Could not start {feature.title()}"
            return False
        self.status.running = True
        self.status.active_feature = feature
        self.status.last_feature = feature
        self.status.last_message = f"Running {feature.title()}"
        return True

    def engine_stopped(self) -> None:
        feature = self.status.active_feature
        self.status.running = False
        self.status.active_feature = None
        if feature:
            self.status.last_message = f"{feature.title()} stopped"

    def stop(self) -> bool:
        if self.status.active_feature is None:
            self.status.running = False
            self.status.last_message = "Stopped"
            return True
        stopped = bool(self._stopper())
        self.status.last_message = "Stopping…" if not stopped else "Stopped"
        if stopped:
            self.engine_stopped()
        return stopped
