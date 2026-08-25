# Auto Gather design notes

This document records the first implementation target for the resource-gathering automation.

## Confirmed live flow

The current reference recordings were captured at 1920x1080 fullscreen and show the following behavior:

1. Open the gather/search panel from the world map.
2. Select Gold.
3. Search from a high preferred resource level.
4. If the search panel remains visible after Search, lower the resource level and try again.
5. When a resource is found, the search panel closes and the game focuses the matching resource tile.
6. Open the resource tile and press Gather.
7. If a free march exists, the game can use it directly.
8. If all marches are already occupied, the game still allows changing an existing gathering march to the new location.
9. Replacement priority is **March 3 -> March 2 -> March 1**.
10. An unexpected UI state can be normalized by opening the Food resource window from the top resource bar and closing it with X, returning to the world map.

A future recording will be added if the rare case where a resource is taken between Search and Gather is observed.

## Reliability rules

- Do not modify the mature rally implementation to add gathering.
- Reuse shared detection, target-window scaling, click safety, stop handling, and scenario infrastructure.
- Treat unknown states as retry/recovery states rather than guessing.
- Prefer verification of visible UI state before sending input.
- Keep any gathering-specific policy outside `rally_matching.py`.
- The first implementation should be testable from saved screenshots/fixtures and Windows CI before supervised live use.

## First MVP

The first MVP should focus on:

- Gold gathering;
- configurable preferred and minimum search levels;
- descending level fallback until a result is found;
- multiple dispatches per run;
- free-march path;
- replacement path using priority 3 -> 2 -> 1;
- safe world-map recovery using Food -> X;
- fail-closed handling for unknown screens.

The implementation should remain easy to extend later to Food/Iron and other selection policies without changing rally behavior.
