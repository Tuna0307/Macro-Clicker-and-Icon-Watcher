# Three-Team Rally Hot Path

This document describes the speed-sensitive runtime used only by explicit three-team Rally scenarios. The legacy two-team Rally path is intentionally unchanged.

## Goal

Rally joining is a race. The macro should spend CPU/time on expensive safety work only when that work can actually matter, while preserving fail-closed final team selection.

The current hot path combines the v6 fast scheduler, v7 entry latch/full-squad recovery, v8 Mob2-paced final dispatch, v9 deadloop/team-availability cache, v10 transition-stable tray recovery, v11 phase-correct entry watchdog, and v12 stable squad-count cache guard.

## World-map loop

Three-team Rally runs at a 0.05 second polling interval at runtime. The scenario file is not rewritten merely to activate this runtime optimization.

The normal entry condition still detects the small Rally icon first. When that condition is true, the runtime checks whether the left-side squad counter can positively prove `3/3`.

The original fast gate keeps the historical small counter ROI. The v7 overlay adds a second bounded left-side search band around that area so ordinary layout movement cannot make the macro depend on one exact pixel rectangle.

- positively proven `3/3` means all three squads are out, so Rally entry is suppressed and polling continues immediately;
- failure to prove `3/3` is fail-open only for this entry optimization;
- failure to prove `3/3` never authorizes a dispatch, because the final fixed-slot / tray checks remain fail-closed;
- this count gate never identifies Team 1/2/3 and never replaces the authoritative fixed-slot detector.

### v9/v12 squad-count change observer

v9 samples the tiny world-map counter area at a slower cadence for one purpose only: detecting whether the number of squads out changed after an exact Team 1/2/3 state was learned.

Dedicated templates positively recognize `1/3`, `2/3`, and `3/3`. The repository does not contain a dedicated `0/3` template, so zero is inferred only when the stable right-hand `/3` suffix is positively matched and none of the complete `1/3`, `2/3`, or `3/3` templates match.

The count is never mapped to a team identity. A persistent change such as `2/3 -> 1/3` means only that the old exact identity cache may be stale, so that cache is discarded. The next final formation screen will establish exact Team 1/2/3 state again.

v12 changes how that staleness evidence is accepted after a 2026-09-03 live run showed a lagging/transitioning sidebar could erase stronger fixed-slot knowledge and make a high-level Rally row look eligible:

- count polling is suppressed while Rally entry is latched or the formation-opening guard is active;
- the world-map Rally icon must be positively visible before the sidebar count can affect the exact-team cache;
- a one-frame unexpected count change no longer invalidates identity immediately;
- `1/3`, `2/3`, and `3/3` changes require a short stable confirmation;
- inferred `0/3` requires a longer 2.0 second stable confirmation because there is no dedicated `0/3` template;
- after a confirmed dispatch, the macro's exact knowledge of which Team it sent outranks a temporarily lagging sidebar count;
- the expected `count + 1` may arrive late and still preserves the exact-team cache;
- only a prolonged failure to ever observe the expected increment (30 seconds) abandons that cache conservatively.

A fresh validated fixed-slot formation capture cancels any pending count-change candidate because fixed Team slots are the stronger evidence source.

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

## v9/v11 deadloop recovery

A latch needs its own failure exit. After a successful Rally-icon click, v9 starts a short `expect Rally page` watch.

Progress is positively proven by either `RallyPage.png` or `GoldMob.png`. Once either appears, the watch disarms and the normal Rally workflow continues.

If no progress is visible:

1. after a 0.45 second grace period, a freshly visible world-map `RallyIcon.png` proves the click did not leave the map; v9 immediately clears the stale Rally workflow/latch and resumes world-map scanning;
2. otherwise, after 2.5 seconds with no Rally-page progress, v9 clears the transient Rally level/team state, disables `Joining`, `Attack Confirm`, `Back if wrong mob`, and `Back if no slot`, and releases the entry latch;
3. v9 does **not** send a blind dismissal click;
4. when the world map is not positively proven at timeout, the existing full-screen `MisClick Base` safety is kept armed briefly so only its own positive Base template may perform a known-safe recovery click.

v11 makes this watchdog phase-correct. Once `Joining` has positively reached the Rally page, a Rally level is carried, or the formation-opening guard is active, the world-map-entry watchdog is disarmed. A Rally icon visible behind/around an opening formation panel can therefore no longer unwind a successfully entered Rally workflow.

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

## Exact-team availability cache and level refresh

The final fixed formation slots remain the only authoritative Team 1/2/3 identity source:

- fixed slot 1 = Team 1;
- fixed slot 2 = Team 2;
- fixed slot 3 = Team 3;
- ZZ present = `IDLE`;
- validated slot without ZZ = `BUSY`;
- invalid/unproven formation screen = `UNKNOWN`.

v9 remembers a fixed-slot result only when the formation screen is validated and all three states are exact `IDLE` or `BUSY`.

After a successful dispatch, the selected Team is immediately marked `BUSY` in that cache. The next Rally-page level filter then computes its ceiling only from the remaining known-IDLE teams.

For example, if Team 1 is the only Team configured above 55 and the cache says `T1=BUSY, T2=IDLE, T3=IDLE`, the Rally-page ceiling becomes 55. A Lv70 row is therefore rejected by OCR/level policy before its `+` can be clicked.

When no row is eligible, the three-team `Joining` action already uses the same no-match recovery shape as the working `Rally gold mob_ 2 team` flow: it clicks the positively matched `BackButton.png`, disables the Rally sub-steps, returns to the world map, and lets the normal Rally scanner reopen the Rally page. v12 does not add a new blind refresh click; it preserves the correct availability evidence long enough for this existing Back -> reopen path to run.

If all cached teams are known `BUSY`, the cached ceiling is `none`, so no Rally row `+` qualifies.

This cache can reject unnecessary `+` clicks earlier, but it can never make an `UNKNOWN` team eligible and it never replaces the fresh final fixed-slot check before dispatch.

## Build markers and useful logs

A current explicit-three-team run should include:

```text
[build] JOIN-HOT-RACE-v7 full-squad recovery loaded
[build] JOIN-HOT-RACE-v8 mob2-paced dispatch loaded
[build] JOIN-HOT-RACE-v9 deadloop+team-cache loaded
[build] JOIN-HOT-RACE-v10 transition-stable tray recovery loaded
[build] JOIN-HOT-RACE-v11 phase-correct entry watchdog loaded
[build] JOIN-HOT-RACE-v12 stable squad-count cache guard loaded
```

Useful lines include:

```text
[rally-v9] stale Rally entry recovered (...); workflow/latch cleared, no blind click sent
[rally-v11] entry watchdog disarmed (...)
[team-cache] exact fixed slots cached: T1=... T2=... T3=...
[team-cache] confirmed dispatch => T1=BUSY; remaining known-idle level ceiling recalculated
[team-cache] sidebar count still 0/3 while confirmed dispatch expects 1/3; preserving exact fixed-team cache
[team-cache] using known fixed-team availability; Rally-row ceiling=55
[team-cache] candidate world-map squad change 2/3 -> 1/3; require 0.45s stable confirmation
[team-cache] invalidated (stable world-map squad count changed 2/3 -> 1/3)
[team3] all-busy tray candidate observed after formation grace; waiting for stable confirmation (normal Attack polling continues)
[team3] stable all-busy tray confirmed after transition guard
```

For a row above the known available-team ceiling, expected logs are:

```text
[team-cache] using known fixed-team availability; Rally-row ceiling=55
[level] ... read 70
[skip] ... 70 > available-team max 55
[skip] 'Joining' no valid matching row target
[no-match] click condition #2 (...BackButton...)
```

There must be no last-slot `+` revalidation/click for that rejected row.

## Regression requirements

Tests must continue proving:

- the legacy two-team path is unchanged;
- whole-screen MisClick Base coverage is retained;
- Base/Profile checks remain event-gated when unarmed;
- positive `3/3` suppresses Rally entry;
- Rally entry is latched after the first world-map click and cannot re-fire inside the same workflow;
- v9 releases a stale latch when RallyIcon positively reappears during true initial entry;
- v9 releases a no-progress latch after the bounded timeout without sending an unrecognized click;
- v11 prevents the entry-only watchdog from running after Rally-page/formation progress is proven;
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
- a lagging post-dispatch count does not erase stronger exact fixed-slot state;
- a late expected `count + 1` preserves cache;
- unrelated count changes require stable world-map confirmation before invalidating identity;
- inferred `0/3` requires the longer confirmation window;
- invalid levels use the existing Back -> reopen Rally no-match path and never click row `+`;
- the existing fixed-slot screenshot matrix and final fail-closed team selection remain green.
