from typing import Callable, Optional, Sequence, Tuple


Rect = Tuple[int, int, int, int]


def _safe_window_snapshot(win):
    try:
        if getattr(win, "isVisible", True) is False:
            return None
        if getattr(win, "isMinimized", False) is True:
            return None
        title = (getattr(win, "title", "") or "").strip()
        left = int(getattr(win, "left", 0) or 0)
        top = int(getattr(win, "top", 0) or 0)
        width = int(getattr(win, "width", 0) or 0)
        height = int(getattr(win, "height", 0) or 0)
    except Exception:
        return None
    if not title or width <= 0 or height <= 0:
        return None
    return title, (left, top, width, height)


def absolute_region_from_window(region: Sequence[int], window_rect: Rect) -> Rect:
    """Convert a window-relative region to absolute screen coordinates."""
    left, top, _, _ = window_rect
    rel_left, rel_top, width, height = region
    return (left + rel_left, top + rel_top, width, height)


def proportional_region_from_window(region: Sequence[int], window_rect: Rect):
    """Convert an absolute screen region to proportional window coordinates."""
    left, top, win_width, win_height = window_rect
    abs_left, abs_top, width, height = region
    if win_width <= 0 or win_height <= 0:
        raise ValueError("Window width and height must be positive.")
    return (
        (abs_left - left) / win_width,
        (abs_top - top) / win_height,
        width / win_width,
        height / win_height,
    )


def absolute_region_from_window_ratio(region_ratio: Sequence[float], window_rect: Rect) -> Rect:
    """Convert proportional window coordinates to absolute screen coordinates."""
    left, top, win_width, win_height = window_rect
    rel_left, rel_top, rel_width, rel_height = region_ratio
    return (
        left + round(rel_left * win_width),
        top + round(rel_top * win_height),
        max(1, round(rel_width * win_width)),
        max(1, round(rel_height * win_height)),
    )


def resolve_window_region(region: Sequence[int], window_rect: Rect,
                          region_ratio: Optional[Sequence[float]] = None,
                          region_window_size: Optional[Sequence[int]] = None) -> Rect:
    """
    Resolve a saved window-relative region against the current window.

    Pixel offsets are preferred while the window remains the same size. If the
    window size changes and proportional coordinates are available, the region
    is scaled to the new window dimensions.
    """
    if region_ratio and region_window_size:
        _, _, current_width, current_height = window_rect
        base_width, base_height = region_window_size
        if abs(current_width - base_width) > 2 or abs(current_height - base_height) > 2:
            return absolute_region_from_window_ratio(region_ratio, window_rect)
    return absolute_region_from_window(region, window_rect)


def resolve_saved_capture_region(
    region: Optional[Sequence[int]],
    region_mode: str,
    region_ratio: Optional[Sequence[float]] = None,
    region_reference_size: Optional[Sequence[int]] = None,
    *,
    window_rect: Optional[Rect] = None,
    monitor_rect: Optional[Rect] = None,
) -> Optional[Rect]:
    """Resolve the same saved condition region for runtime and UI preview."""
    if region is not None:
        if region_mode == "window":
            if window_rect is None:
                return None
            return resolve_window_region(
                region,
                window_rect,
                region_ratio,
                region_reference_size,
            )
        if region_mode == "monitor":
            if monitor_rect is None:
                return None
            return resolve_window_region(
                region,
                monitor_rect,
                region_ratio,
                region_reference_size,
            )
        left, top, width, height = (int(value) for value in region)
        return left, top, width, height
    if window_rect is not None:
        return window_rect
    return monitor_rect


def relative_region_from_window(region: Sequence[int], window_rect: Rect) -> Rect:
    """Convert an absolute screen region to coordinates relative to a window."""
    left, top, _, _ = window_rect
    abs_left, abs_top, width, height = region
    return (abs_left - left, abs_top - top, width, height)


def find_window_rect(title_contains: str, window_provider: Optional[Callable] = None) -> Optional[Rect]:
    """
    Return the first visible window whose title contains the provided text.

    The returned rectangle is (left, top, width, height) in screen coordinates.
    """
    title_contains = title_contains.strip().casefold()
    if not title_contains:
        return None

    if window_provider is None:
        try:
            import pygetwindow as gw
        except ImportError as exc:
            raise RuntimeError(
                "pygetwindow is required for target-window mode. "
                "Install requirements.txt again."
            ) from exc
        window_provider = gw.getAllWindows

    candidates = []
    for order, win in enumerate(window_provider()):
        snapshot = _safe_window_snapshot(win)
        if snapshot is None:
            continue
        title, rect = snapshot
        if title_contains in title.casefold():
            # Prefer an exact title, then the shortest containing title. This
            # avoids attaching to an unrelated window that merely happens to
            # contain the same short text and was enumerated first.
            folded_title = title.casefold()
            candidates.append((folded_title != title_contains, len(title), order, rect))
    return min(candidates)[-1] if candidates else None


def visible_window_titles(window_provider: Optional[Callable] = None):
    if window_provider is None:
        try:
            import pygetwindow as gw
        except ImportError as exc:
            raise RuntimeError(
                "pygetwindow is required to list target windows. "
                "Install requirements.txt again."
            ) from exc
        window_provider = gw.getAllWindows

    titles = []
    seen = set()
    for win in window_provider():
        snapshot = _safe_window_snapshot(win)
        if snapshot is None:
            continue
        title, _rect = snapshot
        folded_title = title.casefold()
        if folded_title in seen:
            continue
        seen.add(folded_title)
        titles.append(title)
    return titles
