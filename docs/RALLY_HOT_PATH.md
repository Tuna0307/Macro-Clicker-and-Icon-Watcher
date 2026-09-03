# Three-Team Rally Hot Path

This document describes the speed-sensitive runtime used only by explicit three-team Rally scenarios. The legacy two-team Rally path is intentionally unchanged.

## Goal

Rally joining is a race. The macro should spend CPU/time on expensive safety work only when that work can actually matter, while preserving fail-closed final team selection.

The current hot path combines the v6 fast scheduler, v7 entry latch/full-squad recovery, v8 Mob2-paced final dispatch, v9 deadloop/team-availability cache, and v10 transition-stable tray recovery.

## World-map loop

Three-team Rally runs at a 0.05 second polling interval at runtime. The scenario file is not rewritten merely to activate this runtime optimization.

The normal entry condition still detects the small Rally icon first. When that condition is true, the runtime checks whether the left-side squad counter can positively prove `3/3`.

The original fast gate keeps the historical small counter ROI. The v7 overlay adds a second bounded left-side search band around that area so ordinary layout movement cannot make the macro depend on one exact pixel rectangle.

- positively proven `3/3` means all three squads are out, so Rally entry is suppressed and polling continues immediately;
- failure to prove `3/3` is fail-open only for this entry optimization;
- failure to prove `3/3` never authorizes a dispatch, because the final fixed-slot / tray checks remain fail-closed;
- this count gate never identifies Team 1/2/3 and never replaces the authoritative fixed-slot detector.

### v9 squad-count change observer

v9 also samples the same tiny world-map counter area at a slower 0.15 second cadence for one purpose only: detecting whether the number of squads out changed after an exact Team 1/2/3 state was learned.

Dedicated templates positively recognize `1/3`, `2/3`, and `3/3`. The repository does not contain a dedicated `0/3` template, so zero is accepted only when the stable right-hand `/3` suffix is positively matched and none of the complete `1/3`, `2/3`, or `3/3` templates match.

The count is never mapped to a team identity. A change such as `2/3 -> 1/3` means only that the old exact identity cache may be stale, so that cache is discarded. The next final formation screen will establish exact Team 1/2/3 state again.

A confirmed dispatch is the one exception. If the macro just dispatched one known team and the counter then increases by exactly one, that increase is expected and the exact-team cache is preserved. If the expected increase never materializes after the short settle window, the cache is invalidated rather than guessed.

## Rally-entry latch

A live run showed that the red Rally icon can remain detectable after the macro has already entered the Rally workflow. Without an explicit workflow latch, `Enter Rally after team probe` could fire again while the macro was already on the Rally/formation transition.

v7 therefore latches Rally entry after the first successful world-map Rally click.

While latched:

- `Enter Rally after team probe` is not evaluated again;
- the latch stays set while Joining, profile-race recovery, and the formation transition are in progress;
- normal wrong-mob/no-slot back recovery releases it;
- MisClick Base recovery releases it;
- a final three-team abort releases it;
- a successful Attack/dispatch click releases it;
- a confirmed all-busy tray recovery releases it.

This is a state guard, not a long cooldown.

## v9 deadloop recovery

A latch needs its own failure exit. After a successful Rally-icon click, v9 starts a short `expect Rally page` watch.

Progress is positively proven by either `RallyPage.png` or `GoldMob.png`. Once either appears, the watch disarms and the normal Rally workflow continues.

If no progress is visible:

1. after a 0.45 second grace period, a freshly visible world-map `RallyIcon.png` proves the click did not leave the map; v9 immediately clears the stale Rally workflow/latch and resumes world-map scanning;
2. otherwise, after 2.5 seconds with no Rally-page progress, v9 clears the transient Rally level/team state, disables `Joining`, `Attack Confirm`, `Back if wrong mob`, and `Back if no slot`, and releases the entry latch;
3. v9 does **not** send a blind dismissal click;
4. when the world map is not positively proven at timeout, the existing full-screen `MisClick Base` safety is kept armed briefly so only its own positive Base template may perform a known-safe recovery click.

The Team 1/2/3 availability cache is not invalidated merely because Rally entry failed; no squad was dispatched by that failed transition.

## Transition pacing

v8 restored the user-confirmed two-team pacing at the world-map -> Rally boundary: after the Rally icon click, the explicit three-team path performs one 0.3 second settle.

The configured random dispatch wait on `Attack Confirm` also remains. After a fixed Team card is chosen, v8 consumes that configured random wait once and then freshly revalidates only `Attack.png` before the final input.

The macro does not re-require every pre-selection formation pixel after the Team-card click because changing Team legitimately changes those pixels.

No fixed Attack coordinate is used. If fresh `Attack.png` cannot be proven, no Attack click is sent.

## MisClick Base

`MisClick Base` keeps full-screen coverage. Its detection ROI is not narrowed because another player's base can appear anywhere on the world map.

The optimization is scheduling only:

1. during normal Rally polling, the expensive whole-screen Base step is gated off;
2. immediately around a risky world-map Rally click, the Base watchdog is armed;
3. Rally-page checks continue without serializing every hot-loop pass behind a whole-screen Base scan;
4. if the Rally page becomes recognizable, `Joining` disarms the Base watchdog;
5. if the click actually opened a base popup, the same full-screen detector performs the existing recognized recovery;
6. successful Base recovery clears stale Rally workflow state and returns control to world-map scanning.

The all-busy tray dismissal also arms the existing Base watchdog immediately afterward because that user-confirmed dismissal is a map-adjacent click.

## MisClick Profile

The profile race can only occur around a Rally row `+` click: another player fills the last slot after detection, the `+` disappears, and their portrait can occupy the old click location.

The runtime therefore gates `MisClick Profile` off during normal polling and arms it only around `click_matching_row` in the `Joining` step.

### Last-moment Join revalidation

Immediately before the click reaches PyAutoGUI, the runtime:

1. confirms the click belongs to the `Join.png` search region rather than the BackButton fallback;
2. captures a tiny fresh rectangle around the exact selected `+`;
3. matches only `Join.png` in that tiny rectangle;
4. if the `+` still exists, clicks its fresh center;
5. if it vanished, cancels the stale click before a replacement portrait can receive it.

This does not re-run Gold matching or OCR.

## Full-squad tray recovery

When all three squads are already out, clicking a valid Rally row `+` can show only the fixed bottom squad-card tray over the map instead of the normal central `SquadAmount` / Attack panel.

v7 recognizes this state with bounded visual evidence:

1. `templates/AddSquad.png` must positively validate the fixed bottom squad-card bar;
2. `templates/SquadAmount.png` is checked in the proven formation-panel anchor band;
3. if `SquadAmount.png` is present, normal `Attack Confirm` remains in charge;
4. only when the bottom tray is proven and the formation anchor is absent are the three fixed `TeamIdleZZ.png` slot ROIs interpreted;
5. all three slots must resolve to `BUSY`;
6. only exact `BUSY / BUSY / BUSY` can enter recovery;
7. the macro clicks the user-validated neutral tray area once, never a team card and never Attack;
8. Rally transient state is cleared, the entry latch is released, and the hot loop resumes.

If any tray slot is `IDLE` or `UNKNOWN`, recovery is not allowed.

### v10 transition-stable confirmation

A supervised run on 2026-09-03 exposed an important race in the v7 rule. A valid Lv65 Rally row `+` was clicked at `21:57:22.814`. Only about 0.45 seconds later, the bottom squad-card tray had rendered while the central formation panel and ZZ glyphs were still settling. That transient frame looked like `AddSquad present + SquadAmount absent + no ZZ`, so v7 incorrectly classified it as `BUSY / BUSY / BUSY`, sent the tray-dismiss click, and cleared the Rally workflow. The real formation screen then finished opening, but `Attack Confirm` had already been disabled, leaving the macro visually stuck on the formation screen.

v10 tightens only the recovery decision:

- immediately after a successful Rally-row `+` click, tray-only recovery is prohibited for 1.0 second;
- this is **not a sleep** and does not block normal dispatch: the normal engine cycle and `Attack Confirm` detection continue running throughout the grace period;
- after the grace period, `BUSY / BUSY / BUSY` must first be observed as a candidate;
- that candidate must remain stable for at least 0.25 second;
- v7 then performs its own fresh capture again immediately before any dismiss click;
- any appearance of the normal formation anchor, any `IDLE`, or any `UNKNOWN` cancels the candidate and sends no tray recovery click.

Therefore a normal formation screen can proceed as soon as it is recognizable, while the rare true all-busy tray is still cleaned up after positive stable evidence.

## Exact-team availability cache

The final fixed formation slots remain the only authoritative Team 1/2/3 identity source:

- fixed slot 1 = Team 1;
- fixed slot 2 = Team 2;
- fixed slot 3 = Team 3;
- ZZ present = `IDLE`;
- validated slot without ZZ = `BUSY`;
- invalid/unproven formation screen = `UNKNOWN`.

v9 remembers a fixed-slot result only when the formation screen is validated and all three states are exact `IDLE` or `BUSY`.

After a successful dispatch, the selected Team is immediately marked `BUSY` in that cache. The next Rally-page level filter then computes its ceiling only from the remaining known-IDLE teams.

With the current limits Team 1 = 65, Team 2 = 55, Team 3 = 55:

- if T1 is known `BUSY` and T2/T3 are known `IDLE`, the Rally-page ceiling becomes 55;
- a Lv56-65 row is rejected by OCR/level policy before its `+` is clicked;
- if all cached teams are known `BUSY`, the cached ceiling is `none`, so no Rally row `+` qualifies;
- once the world-map squad count changes unexpectedly, the exact identity cache is discarded and the runtime falls back to the configured broad ceiling until a new fixed formation result is observed.

This cache is an optimization only. It can reduce unnecessary `+` clicks, but it can never make an `UNKNOWN` team eligible and it never replaces the fresh final fixed-slot check before dispatch.

## Build markers and useful logs

A current explicit-three-team run should include:

```text
[build] JOIN-HOT-RACE-v7 full-squad recovery loaded
[build] JOIN-HOT-RACE-v8 mob2-paced dispatch loaded
[build] JOIN-HOT-RACE-v9 deadloop+team-cache loaded
[build] JOIN-HOT-RACE-v10 transition-stable tray recovery loaded
```

Useful lines include:

```text
[rally-v9] stale Rally entry recovered (...); workflow/latch cleared, no blind click sent
[team-cache] exact fixed slots cached: T1=... T2=... T3=...
[team-cache] confirmed dispatch => T1=BUSY; remaining known-idle level ceiling recalculated
[team-cache] using known fixed-team availability; Rally-row ceiling=55
[team-cache] invalidated (world-map squad count changed 2/3 -> 1/3)
[team3] all-busy tray candidate observed after formation grace; waiting for stable confirmation (normal Attack polling continues)
[team3] stable all-busy tray confirmed after transition guard
```

Existing race-path logs remain useful:

- `[rally-fast] Rally entry latched until workflow exit`
- `[rally-fast] broad 3/3 gate: all squads out; Rally entry suppressed`
- `[rally-fast] last-slot + vanished before input; stale click cancelled`
- `[rally-fast] revalidated last-slot + score=...`
- `[team3] dispatch committed through fresh Attack target`

## Regression requirements

Tests must continue proving:

- the legacy two-team path is unchanged;
- whole-screen MisClick Base coverage is retained;
- Base/Profile checks remain event-gated when unarmed;
- positive `3/3` suppresses Rally entry;
- Rally entry is latched after the first world-map click and cannot re-fire inside the same workflow;
- v9 releases a stale latch when RallyIcon positively reappears;
- v9 releases a no-progress latch after the bounded timeout without sending an unrecognized click;
- RallyPage/GoldMob progress disarms the deadloop watch;
- last-moment Join revalidation uses a small fresh capture;
- a vanished Join control cancels the stale click;
- normal formation detection keeps running during the v10 tray-recovery grace period;
- v10 cannot probe/dismiss an all-busy tray during the first 1.0 second after the Rally-row `+` click;
- v10 requires stable BUSY/BUSY/BUSY evidence before delegating to the existing fresh v7 dismiss proof;
- any IDLE/UNKNOWN evidence cancels pending tray recovery;
- all-busy tray recovery exits without team-card or Attack input;
- validated fixed-slot states populate the exact-team cache;
- a confirmed dispatch marks only the selected fixed Team BUSY in cache;
- the remaining known-IDLE teams determine the Rally-page level ceiling;
- expected `count + 1` after our own dispatch preserves cache;
- an unexpected world-map squad-count change invalidates cached identity;
- the existing fixed-slot screenshot matrix and final fail-closed team selection remain green.
