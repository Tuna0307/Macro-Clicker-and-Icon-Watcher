"""Helpers for comparing global hotkeys by their physical key combinations."""

from typing import Iterable

import keyboard

CanonicalHotkey = tuple[tuple[tuple[int, ...], ...], ...]


def canonical_hotkey(value: str) -> CanonicalHotkey:
    """
    Return an order-independent representation of each physical hotkey step.

    The ``keyboard`` package accepts aliases such as ``ctrl``/``control`` and
    modifier orders such as ``ctrl+shift``/``shift+ctrl``. Comparing the saved
    text would miss those collisions, so normalize the scan-code combinations
    produced by the same parser used to register the hotkey.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("hotkey must be non-empty text")
    try:
        parsed_steps = keyboard.parse_hotkey_combinations(value.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc

    canonical_steps = []
    for step in parsed_steps:
        combinations = {
            tuple(sorted(int(scan_code) for scan_code in combination))
            for combination in step
        }
        if not combinations:
            raise ValueError("hotkey contains an empty key combination")
        canonical_steps.append(tuple(sorted(combinations)))
    if not canonical_steps:
        raise ValueError("hotkey must contain at least one key")
    return tuple(canonical_steps)


def hotkeys_conflict(first: str, second: str) -> bool:
    """Return whether either accepted hotkey sequence prefixes the other."""

    return _canonical_hotkeys_conflict(
        canonical_hotkey(first),
        canonical_hotkey(second),
    )


def _canonical_hotkeys_conflict(
    first: CanonicalHotkey,
    second: CanonicalHotkey,
) -> bool:
    """
    Compare sequences by the physical combinations accepted at each step.

    A generic modifier such as ``ctrl`` accepts either sided scan code, while
    ``left ctrl`` accepts only one. Their canonical tuples therefore differ,
    but the hotkeys still overlap whenever their accepted combinations
    intersect.
    """

    shared_length = min(len(first), len(second))
    for index in range(shared_length):
        if set(first[index]).isdisjoint(second[index]):
            return False
    return len(first) == shared_length or len(second) == shared_length


def find_hotkey_conflicts(
    bindings: Iterable[tuple[str, str]],
) -> list[tuple[str, str]]:
    """
    Return label pairs whose physical hotkey sequences overlap.

    This small registry-style helper can validate bindings owned by different
    application surfaces without coupling those surfaces to each other.
    """

    owners: list[tuple[str, CanonicalHotkey]] = []
    conflicts: list[tuple[str, str]] = []
    for label, hotkey in bindings:
        canonical = canonical_hotkey(hotkey)
        for previous_label, previous_hotkey in owners:
            if _canonical_hotkeys_conflict(previous_hotkey, canonical):
                conflicts.append((previous_label, label))
        owners.append((label, canonical))
    return conflicts
