"""Additive bot skeleton.

This package is intentionally separate from the mature macro engine.  It provides
domain-level feature contracts and a cursor-free input backend boundary so future
work can evolve without rewriting the existing rally workflows.
"""

from .contracts import (
    BossCandidate,
    BotCapability,
    BotResult,
    CapabilityStatus,
    RallyCandidate,
    ServerRef,
)
from .runtime import BotRuntime

__all__ = [
    "BossCandidate",
    "BotCapability",
    "BotResult",
    "BotRuntime",
    "CapabilityStatus",
    "RallyCandidate",
    "ServerRef",
]
