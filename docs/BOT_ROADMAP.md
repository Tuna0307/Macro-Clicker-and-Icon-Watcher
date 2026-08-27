# Dedicated Bot migration roadmap

This file tracks migration from the low-level Macro Builder to the dedicated Bot interface so future AI/development sessions can tell what is complete, what still needs live proof, and what should not be rewritten.

## Phase status

| Phase | Status | Current contract / remaining work |
| --- | --- | --- |
| 1. Bot configuration layer | Complete | `bot/config.py` owns normal-user settings/persistence. |
| 2. Feature adapters | Complete for current features | Runtime adapters deep-copy proven Scenarios; Gather re-verifies/clicks one exact requested team. |
| 3. Bot Controller | V1 complete | One input owner at a time; continuous Gather is separate persistent service. |
| 4. Bot UI shell | Complete, polish ongoing | Dashboard/Rally/Gather/Positions/Alerts/Schedule/Logs/Settings are normal pages. |
| 5. Rally tab | Core complete; live re-validation required | Normal controls wrap mature Rally behavior. |
| 6. Gather tab | Continuous architecture implemented; live proof required | Normal-map availability uses the real Gather-search control, handles 0/3 blank-status semantics, and uses committed busy-count/identity assets. |
| 7. Alerts tab | Core complete | Common groups/sound/watcher controls exposed. |
| 8. Positions tab | Core complete; live re-validation required | Development/Science and retry policy exposed. |
| 9. Advanced mode | Complete | Legacy editor/alert setup retained but isolated. |
| 10. Dashboard/live status | In progress | Current map-side detector targets Idle/Busy/Unknown. Rich Travelling/Gathering/Returning/timer detail is deferred pending real fixtures. |
| 11. Scheduling | Basic implementation complete; live proof pending | Saved start/stop times/weekdays. |
| 12. Remove remaining hard-coded user settings | Ongoing audit | Move genuine user choices only. |
| 13. Testing throughout | Ongoing | Added real map-anchor, 0/3/busy-count/identity/template-existence regressions; full Windows CI + real-game verification required. |

## Latest live finding

A supervised 1920×1080 normal-world-map screenshot exposed a second map-gate bug after the nonexistent-sidebar-template bug was removed.

The previous normal-map gate used `templates/RallyIcon.png`, but that workflow-specific icon is not visible on the normal map. It matched the supplied screenshot at only about **0.39**, below the 0.85 threshold, so Auto Gather remained at the waiting state for more than two minutes.

The existing `templates/GatherSearchIcon.jpg` control is visible on the normal map and matched the same screenshot at about **0.99**. It is now the trusted map gate in reference region `(0, 780, 110, 150)` with threshold `0.90`. A small regression fixture is stored at `tests/fixtures/team_status/world_map_search_anchor.jpg`.

## Current next work

1. Obtain a green Windows CI run for the Gather-search map-gate fix.
2. Repeat the supervised normal-world-map test and verify a `[team] T1=... T2=... T3=...` observation appears instead of indefinite waiting.
3. The supplied live screenshot currently shows **1/3 busy**; verify the detector identifies/infer one busy team and selects one of the other configured Idle teams.
4. Test 0/3, 2/3, and 3/3 busy states.
5. Specifically test Team 2-only busy inference.
6. Verify exact intended team is re-verified/clicked on the dispatch panel.
7. Verify all-busy waits and never replaces an occupied march.
8. Verify a team that later returns free can be dispatched again.
9. Verify resource-taken Cancel/retry and F12/unconfirmed pause.
10. Re-test Rally, Positions, Alerts, schedule, and Advanced isolation.
11. Add richer Travelling/Gathering/Returning/timer recognition only after real committed screenshots/templates exist.
12. Design cooperative Rally/Gather handoff only if simultaneous continuous operation is required.

## Current perception contract

```text
GatherSearchIcon visible on normal map?
   ├─ no  -> observation unavailable; do not dispatch from blank screen
   └─ yes
       ↓
 read 1/3, 2/3, 3/3 busy count
       ↓
 no count/status match -> 0/3 busy
       ↓
 identify Team 1/3 portraits; infer Team 2
       ↓
 IDLE / BUSY / UNKNOWN
```

`RallyIcon.png` remains part of Rally workflow behavior but is no longer treated as a universal world-map marker.

The exact-team dispatch-panel blue-idle check remains the final authority before Dispatch.

## Live verification checklist

Before treating continuous Auto Gather as proven:

- normal map is recognized by the Gather search control;
- all-free blank status is recognized as 0/3 and starts gathering;
- Team 1/2/3 busy combinations are classified correctly;
- contradictory evidence does not dispatch;
- exact-team card selection occurs before Dispatch;
- busy teams remain untouched;
- all-busy means wait;
- no-free-march does not invoke legacy replacement;
- resource-taken retry remains safe;
- F12/failed attempt pauses;
- target-window/foreground safety remains intact;
- overlays hiding the Gather search control cannot turn a blank queue into Idle.

## Do not regress to old Gather product model

Do not present normal Gather as `send 3 marches` or `replacement order 3 -> 2 -> 1`. Those fields remain only for backward compatibility.

See `AGENTS.md` for mandatory descriptive commit and Markdown synchronization rules.
