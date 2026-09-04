# Rally v17 Fixed-Panel Profile Recovery

## Live failure

A 2026-09-04 three-team Rally run exposed a recovery deadlock after the final fixed Team check correctly proved all three squads BUSY.

The live sequence was:

```text
14:23:18.200 [team-cache] exact fixed slots cached: T1=BUSY T2=BUSY T3=BUSY
14:23:18.201 [team3] Rally Lv75; fresh final T1=BUSY T2=BUSY T3=BUSY
14:23:18.201 [team3] eligible none; selected none
14:23:18.202 [team3] no capable idle Team; backing out without dispatch
14:23:18.202 [team3] dismissed fixed team panel safely above its validated anchor at (-947, 198)
```

The outside-panel dismissal returned to the world map but landed on a player and opened the player-profile popup. The scenario then stopped making progress.

## Root cause

`Attack Confirm -> select_rally_team` deliberately disarms the existing `MisClick Profile` event gate before final Team selection. That is correct for normal formation handling.

However, the all-busy abort path calls `_dismiss_fixed_rally_team_panel()`. Its dismissal point is outside the validated formation panel and therefore can interact with the world map underneath. Before v17, that helper did not re-arm `MisClick Profile` around this second risky click.

The scenario already has positive profile evidence through `templates/FriendStatus.png`, but the hot-path event gate prevented the step from being evaluated. No blind click was missing; the detector was simply gated off.

## v17 behavior

v17 wraps only the fixed-panel dismissal boundary in explicit three-team mode.

Before the existing outside-panel click it:

1. records whether Profile recovery was already armed;
2. arms the existing `MisClick Profile` detector;
3. opens a bounded 3-second recovery window; and
4. performs the unchanged fixed-panel dismissal click.

If the click fails, v17 restores the previous gate state. If it succeeds, the existing `FriendStatus.png` condition is allowed to detect a profile popup and the existing recovery click handles it. A successful profile recovery clears the v17 window.

If no profile appears, a v17-owned temporary gate expires after 3 seconds. The next normal Rally-entry click also retains the existing hot-path behavior that disarms Profile recovery for the next workflow phase.

No new blind popup dismissal is introduced.

## Expected logs

A current three-team run should include:

```text
[build] JOIN-HOT-RACE-v17 fixed-panel profile recovery loaded
```

When a fixed formation panel is dismissed through the outside-panel recovery point:

```text
[rally-v17] fixed-panel outside dismissal armed Profile recovery
```

If that click opens the player profile, the existing recovery should then continue with:

```text
[fire] MisClick Profile
[rally-fast] profile misclick recovered; rescan immediately
```

The v7 entry-latch cleanup for an aborted `select_rally_team` action remains authoritative, so after the popup is closed the world-map Rally scanner can resume normally.

## Scope

v17 applies only to explicit three-team Rally scenarios. The legacy two-team path is unchanged.

It does not alter:

- GoldMob row matching;
- OCR level filtering;
- Team 1/2/3 limits or priority;
- final fixed-slot Team identity;
- Join/+ micro-revalidation;
- v13 Back/latch recovery;
- v15 multi-row no-slot routing; or
- v16 Lv80+ GoldMob identity.

## Regression requirements

Tests must continue proving:

- the Profile gate is armed before a three-team fixed-panel dismissal click;
- a successful dismissal keeps the bounded Profile detector available;
- a failed dismissal restores the previous gate state;
- a pre-existing Profile gate is never erased by a failed v17 dismissal;
- a v17-owned temporary gate expires rather than remaining armed indefinitely; and
- two-team dismissals are unchanged.
