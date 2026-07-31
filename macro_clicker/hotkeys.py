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
        combinations = set()
        for combination in step:
            scan_codes = tuple(int(scan_code) for scan_code in combination)
            if len(scan_codes) != len(set(scan_codes)):
                # Generic/sided modifier parsing can include both valid
                # two-key alternatives and an impossible duplicate expansion.
                # Keep the usable alternatives and reject the chord only when
                # every expansion repeats one physical key (for example
                # ``f12+f12``).
                continue
            combinations.add(tuple(sorted(scan_codes)))
        if not combinations:
            raise ValueError("a hotkey chord cannot repeat the same physical key")
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


def is_single_key_hotkey(value: str) -> bool:
    """Return whether ``value`` is one non-sequential physical key."""

    canonical = canonical_hotkey(value)
    return len(canonical) == 1 and all(
        len(combination) == 1 for combination in canonical[0]
    )


def permissive_single_key_conflict(single_key: str, other: str) -> bool:
    """
    Compare a permissive single-key hook with another keyboard binding.

    A kill switch registered as a key hook fires even while modifiers are held.
    Therefore ``f12`` overlaps not only another plain ``f12`` binding, but also
    bindings such as ``ctrl+f12`` and sequences containing F12.
    """

    single = canonical_hotkey(single_key)
    if len(single) != 1 or any(len(combination) != 1 for combination in single[0]):
        return _canonical_hotkeys_conflict(single, canonical_hotkey(other))
    scan_codes = {combination[0] for combination in single[0]}
    return any(
        any(scan_code in scan_codes for scan_code in combination)
        for step in canonical_hotkey(other)
        for combination in step
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
