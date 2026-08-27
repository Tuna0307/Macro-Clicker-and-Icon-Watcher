# Dedicated Bot UI architecture

The normal product exposes Dashboard, Rally, Gather, Positions, Alerts, Schedule, Logs, and Settings. Scenario/Step internals remain under Advanced.

## Gather page/service contract

Users configure enable/disable, Gold start level, and the Team 1/2/3 subset allowed to gather. Do not expose legacy replacement order as a normal control.

The backend resource-search popup has three remembered tabs. Normal users should not need to manage this: the Gather scenario always selects the fixed middle `採集` / Gather tab before selecting Gold.

Dashboard team labels may show:

```text
Idle
Travelling — HH:MM:SS
Gathering — HH:MM:SS
Returning — HH:MM:SS
Rallying — HH:MM:SS
Busy
Unknown
```

Detailed status-label assets are optional. If one is missing locally, the affected non-idle row should display generic `Busy` (with a timer if OCR succeeds) rather than making the team monitor unavailable. Generic Busy remains non-dispatchable.

The Gathering and Rallying PNGs were repaired after CI proved the earlier blobs could exist but fail OpenCV decoding. UI/runtime validation of image templates therefore depends on successful decoding, not filename presence alone.

The left game sidebar is a compressed busy-only list. Its first/second/third visible rows are **not** fixed Team 1/2/3 positions. Rows represent the busy subset in team-number order and move upward when a team becomes free.

Hero faces also cannot be used as permanent team IDs because changing a lead hero changes both the dispatch and sidebar portrait. The monitor learns current portraits only from unambiguous assignments and uses them as additional evidence later.

If identity at a partial-busy cold start is ambiguous, the UI should show Unknown / waiting for identity confirmation; it must not present an invented free team.

Timers are useful scheduling/display hints, never authority for Idle.

### Logs diagnostics

During the current supervised delay investigation, the Logs tab may show `[team-diag]` lines containing the world-map match score, all three busy-count candidate scores, identity completeness, unreadable-view heartbeats, OCR initialization timing, and `slow scan` duration. These are read-only troubleshooting messages. They do not change Dashboard state, choose a team, or authorize a click.

## Dispatch panel

Unlike the sidebar, Team 1/2/3 dispatch cards have fixed positions. Exact-team blue-idle verification and fixed-card clicking remain the final safety gate regardless of hero portrait.

## Input ownership

Finite Position jobs and continuous workflows must not compete for input. Rally + continuous Gather remain blocked together; Alerts remain passive.
