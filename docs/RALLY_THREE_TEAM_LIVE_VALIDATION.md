# Three-Team Rally Live Validation — 2026-09-03

## First live failures observed

The first real three-team run exposed two independent problems:

1. `Joining` accepted a Level 70 Rally even though the configured three-team limits were Team 1 = 65, Team 2 = 55, Team 3 = 55.
2. On the Attack/formation screen, the fixed-slot Team 1/2/3 detector returned `UNKNOWN` for all three teams and therefore used the safe back-out click instead of selecting a team and dispatching.

The game visually defaults to Team 1, so a highlighted Team 1 card is not proof that the macro selected Team 1. The macro only considers a team selected after the fixed-slot status check succeeds and the selector logs/clicks the chosen team card.

## First live fixes

### Dynamic Rally level ceiling

For explicit three-team mode (`team_priority = [3, 2, 1]`), `Joining` derives its OCR ceiling from the highest configured enabled-team maximum when no exact team-availability cache is available.

With the current live settings:

- Team 1 max = 65
- Team 2 max = 55
- Team 3 max = 55
- broad Joining ceiling = 65

Therefore Level 66+ rows are rejected before the `+` / Join control is clicked. Level 56–65 can only be dispatched by Team 1 at the final selection stage. Level 55 and below can use Teams 1/2/3 according to the fresh fixed-slot idle/busy state and priority.

### Live Attack/formation screen anchor

The original fixed detector validated `SquadAmount.png` only inside a narrow `(900, 480, 130, 145)` 1920×1080 reference ROI. Real screenshots proved that valid formation panels can place the same anchor lower, causing the entire status read to fail closed as `UNKNOWN`.

Three-team runtime validation uses the broader 1920×1080 reference ROI `(740, 448, 466, 299)`, matching the already-proven broad `Attack Confirm` panel area. The Team 1/2/3 ZZ slot regions and 0.90 idle threshold remain unchanged.

If the panel still cannot be proven, the runtime remains fail-closed and logs the detector error and anchor score.

## Second live failure: full-squad tray and repeated Rally entry

A later supervised run reproduced the all-squads-out case.

The uploaded log shows a successful Rally-page row decision followed by a stale `+` cancellation, then `Enter Rally after team probe` firing again while the Rally workflow was already active. In a later attempt the row `+` was successfully clicked but the game exposed only the fixed bottom squad-card tray over the map. The normal central `SquadAmount` / Attack panel never appeared, so the scenario had no condition that could advance or recover and remained stuck on that tray.

The user confirmed two important game rules:

- when the world-map counter is already `3/3`, all three squads are out and the macro must not dispatch;
- on the tray-only screen, one ordinary click outside the active team controls dismisses the interface.

The live tray screenshot shows the existing bottom `AddSquad.png` control at its proven position, no central `SquadAmount.png` anchor, and no `ZZ` glyph in any of the three fixed Team 1/2/3 status ROIs. This is the exact `BUSY / BUSY / BUSY` state.

## v7 correction

### Rally-entry latch

After the first successful world-map Rally-icon click, v7 latches entry until the current Rally workflow has genuinely exited.

While latched, `Enter Rally after team probe` cannot fire again even if the red Rally icon remains visually detectable.

The latch is released by normal wrong-mob/no-slot recovery, MisClick Base recovery, final three-team abort, successful Attack/dispatch, or all-busy tray recovery.

### Broader `3/3` gate

The historical `FullSquad3_3.png` fast check used a tiny fixed ROI around the original counter calibration. v7 keeps that fast gate and adds a second bounded left-side search band around the counter area.

A positive `3/3` suppresses Rally entry immediately. Failure to prove it remains fail-open only for this optimization; it never authorizes final dispatch.

### All-busy tray recovery

When a Rally row has already been clicked and a carried Rally level exists, v7 performs a bounded tray probe before the normal cycle:

1. validate the bottom fixed-card bar with `AddSquad.png`;
2. check the broad formation anchor for `SquadAmount.png`;
3. if `SquadAmount.png` is present, leave normal `Attack Confirm` behavior untouched;
4. if the tray is present but the formation anchor is absent, read the three fixed `TeamIdleZZ.png` ROIs;
5. require Team 1 = BUSY, Team 2 = BUSY, Team 3 = BUSY;
6. click the user-validated neutral tray area once to dismiss;
7. never click Team 1/2/3 and never click Attack;
8. clear transient Rally state and resume the hot loop.

Any `IDLE` or `UNKNOWN` tray state fails closed and does not run this recovery click.

## v8 correction: Mob2-paced final dispatch

A later live run reached the correct formation state and selected the correct fixed Team, but the generic action loop revalidated the full original `Attack Confirm` condition set after Team selection. Because selecting a Team legitimately changes formation pixels, that stale revalidation cancelled the final Attack click.

v8 corrects that transition:

- the world-map Rally click gets the working two-team 0.3 second entry settle;
- the configured random 1.0–1.5 second dispatch delay is consumed once after Team selection;
- only the exact `Attack.png` target is freshly revalidated after that delay;
- if fresh Attack is proven, it is clicked through the normal safe input path;
- if fresh Attack is not proven, no Attack input is sent.

This preserves the fixed-slot final Team authority and prevents stale full-condition pixels from cancelling an otherwise valid dispatch.

## v9 correction: deadloop recovery

The v7 latch still needed a bounded failure exit for cases where the Rally icon click did not actually transition to the Rally page.

After every successful world-map Rally-icon click, v9 starts an `expect Rally page` watch:

- positive `RallyPage.png` or `GoldMob.png` means forward progress and disarms the watch;
- after a 0.45 second grace period, if `RallyIcon.png` is positively visible again, v9 immediately clears the stale Rally workflow/latch and resumes world-map scanning;
- otherwise, after 2.5 seconds with no Rally-page progress, v9 clears the transient Rally state, disables the stale Rally sub-steps, and releases the latch;
- no blind dismissal click is sent by this timeout recovery;
- if the world map is not positively proven at timeout, full-screen `MisClick Base` remains armed briefly so only a positively recognized Base popup may perform its established safe recovery.

This specifically prevents a failed Rally click from leaving the macro permanently latched.

## v9 correction: remembered exact Team availability

The fixed formation slots remain the only authoritative Team identity source. v9 caches a Team state only after a validated formation screen yields exact `IDLE`/`BUSY` for all three fixed slots.

After a successful dispatch, the selected fixed Team is immediately marked `BUSY` in the cache. `Joining` then computes its level ceiling from only the remaining known-IDLE teams.

For the current settings T1=65, T2=55, T3=55:

- if T1 is known BUSY while T2/T3 are known IDLE, the effective Rally-page ceiling is 55;
- a Lv56–65 row is rejected by the level filter before another `+` click;
- if all cached Teams are BUSY, the effective ceiling is `none`, so no Rally row `+` qualifies.

The world-map squad counter is used only to determine whether the cached identity may have become stale. It never maps a visible row or count position to a Team number.

- expected `count + 1` after the macro's own confirmed dispatch preserves the exact-team cache;
- an unexpected count change invalidates exact Team identity because a Team may have returned;
- if the expected post-dispatch count increase never appears after the short settle window, the cache is invalidated rather than trusted.

Once invalidated, the macro falls back to the broad configured ceiling (currently 65) until another validated fixed formation screen re-establishes exact Team 1/2/3 state. Final dispatch remains fail-closed and always uses a fresh fixed-slot read.

## Third live failure: transient tray misclassified as all-busy

The 21:57 live run exposed a different race from the real all-busy tray case.

The relevant sequence was:

1. `Joining` positively found a Gold rally and accepted `Lv.65`.
2. The final `+` was revalidated at score `1.000` and clicked at `21:57:22.814`.
3. At `21:57:23.266` — only about **0.45 seconds later** — v7 logged `fixed squad tray shows T1=BUSY T2=BUSY T3=BUSY` and immediately performed its tray-dismiss recovery.
4. The macro then cleared the Rally workflow.
5. The game continued rendering and ended on a normal formation screen with the central troop panel, blue `出征`/Attack button, and visible ZZ team-status glyphs.
6. Because `Attack Confirm` had already been disabled by the false tray recovery, the macro had nothing left that could advance that screen and only the background MisClick Base watchdog continued running.

The uploaded screenshot therefore is **not** the true all-busy tray state. It is the normal formation screen that completed after v7 had already made its decision on a partially rendered transition frame.

## v10 correction: transition-stable tray recovery

v10 keeps the real v7 all-busy recovery but prevents it from treating a half-rendered formation transition as authoritative.

After a successful Rally-row `+` click:

- tray-only recovery is prohibited for the first **1.0 second**;
- this is not a blocking sleep: normal engine polling and `Attack Confirm` detection continue immediately, so a normal formation screen can advance as soon as it appears;
- after the grace period, BUSY/BUSY/BUSY must first be observed as a candidate;
- the candidate must remain BUSY/BUSY/BUSY for at least **0.25 second**;
- only then is control delegated back to the existing v7 recovery, which performs another fresh tray capture immediately before any dismiss click;
- any normal formation evidence, any IDLE, or any UNKNOWN cancels the pending tray recovery.

This gives a genuine tray-only state time to prove itself without adding latency to successful formation/Attack handling.

## Screenshot regression evidence

The existing committed fixed-slot matrix remains:

- `204523` → IDLE / IDLE / IDLE
- `203451` → IDLE / BUSY / IDLE
- `211656` → BUSY / IDLE / IDLE
- `211753` → BUSY / IDLE / BUSY
- `211946` → IDLE / BUSY / BUSY
- `212029` → BUSY / BUSY / BUSY
- `212218` → IDLE / IDLE / BUSY
- `211912` world-map-only → UNKNOWN / UNKNOWN / UNKNOWN

The all-busy tray classifier continues to reuse the committed `212029` BUSY / BUSY / BUSY fixture with the central formation-anchor band masked while preserving the real fixed bottom-card pixels.

## Safety contract

- A positively proven `3/3` must never dispatch.
- A validated, transition-stable all-busy tray must never dispatch.
- Tray-only recovery cannot fire during the first 1.0 second after a valid Rally-row `+` click.
- BUSY/BUSY/BUSY must remain stable and then pass v7's final fresh proof before a dismiss click is allowed.
- Final Team identity still comes only from fixed Team 1/2/3 slots.
- `UNKNOWN` never qualifies.
- Cached availability can only reject unnecessary Rally rows earlier; it cannot authorize final dispatch.
- An unexpected world-map squad-count change invalidates cached Team identity.
- Deadloop timeout recovery sends no blind click.
- Full-screen MisClick Base coverage remains available where a Base popup can occur.
- Final Attack remains positively image-proven immediately before input.
- The legacy two-team runtime is unchanged.

## Current live markers

A current explicit-three-team run should include:

```text
[build] JOIN-HOT-RACE-v7 full-squad recovery loaded
[build] JOIN-HOT-RACE-v8 mob2-paced dispatch loaded
[build] JOIN-HOT-RACE-v9 deadloop+team-cache loaded
[build] JOIN-HOT-RACE-v10 transition-stable tray recovery loaded
```

Useful current lines include:

```text
[rally-v9] stale Rally entry recovered (...); workflow/latch cleared, no blind click sent
[team-cache] exact fixed slots cached: T1=... T2=... T3=...
[team-cache] confirmed dispatch => T1=BUSY; remaining known-idle level ceiling recalculated
[team-cache] using known fixed-team availability; Rally-row ceiling=55
[team-cache] invalidated (world-map squad count changed 2/3 -> 1/3)
[team3] all-busy tray candidate observed after formation grace; waiting for stable confirmation (normal Attack polling continues)
[team3] stable all-busy tray confirmed after transition guard
```

## Validation target

Focused tests now cover:

- entry latch activation/release and blocked re-entry;
- shifted/broader `3/3` detection;
- all-busy tray classification/recovery;
- v8 fresh Attack-only post-selection dispatch;
- v9 fast recovery when RallyIcon reappears;
- v9 2.5 second no-progress recovery without blind input;
- exact fixed-Team cache population and selected-Team BUSY update;
- reduced Rally-row ceiling from remaining known-IDLE Teams;
- expected own-dispatch counter increment preserving cache;
- unexpected squad-count change invalidating cached identity;
- v10 preventing any tray probe/dismiss during the formation-transition grace period;
- v10 requiring stable BUSY/BUSY/BUSY before the existing fresh v7 dismiss proof;
- IDLE evidence cancelling a pending tray-recovery candidate;
- legacy two-team delegation.

The repository still carries the pre-existing historical Auto Gather expectation mismatch: the test expects 12 level-up clicks while the historical scenario contains 15. CI results must distinguish that known failure from Rally regressions.
