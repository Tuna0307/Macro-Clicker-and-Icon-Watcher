# Three-Team Rally v11 Entry-Watch Live Validation — 2026-09-03

## Live failure reproduced

A supervised three-team run reached the Rally page correctly, OCR'd an eligible Level 65 row, freshly revalidated the last-slot `+` at score 1.000, and clicked it.  The game then began opening the normal formation/dispatch screen.

The failure was not caused by mouse movement and was not the v10 all-busy tray race.  The v9 deadloop watchdog was still armed from the original world-map Rally-icon click.  During the Rally-page -> formation transition, the world-map Rally icon became visible again underneath/around the opening panel.  v9 interpreted that visible Rally icon as proof that the original Rally entry had failed and cleared the Rally workflow before `Attack Confirm` could run.

Observed sequence:

```text
Rally page READY
-> OCR Lv65 accepted
-> last-slot + freshly revalidated
-> click matching row
-> attack-screen opening guard armed
-> ~0.30 s later v9 sees RallyIcon
-> v9 clears transient state / disables Rally workflow
-> real formation screen finishes drawing
-> Attack Confirm is no longer active
-> macro appears stuck on the formation screen
```

The same false recovery repeated several times in the uploaded log, proving it was deterministic and independent of user mouse input.

## Root cause

The v9 watchdog is an **entry-only** safeguard.  Its evidence is valid only while the macro is still waiting to learn whether the original world-map Rally-icon click reached the Rally page.

Once the Rally page has positively reached `Joining`, or once a Rally row has been resolved/clicked and the formation transition has started, later visibility of `RallyIcon.png` says nothing about whether the original entry succeeded.  At that point the workflow is already beyond the entry phase.

## v11 correction

v11 makes the v9 watchdog phase-correct without adding a sleep.

The entry watchdog is disarmed when `Joining` reaches its `click_matching_row` action because reaching that action already proves the Rally page/row conditions were READY.

There is also a second guard directly around the v9 watcher.  If either of these is true:

- `_pending_rally_level` is present, meaning a Rally row has already been resolved; or
- `_rally_join_guard_until` is active, meaning the row `+` was clicked and the Attack/formation screen is opening;

then the entry-only watchdog is cleared and v9 recovery is not allowed to run.

This means a `RallyIcon.png` visible behind or around the formation panel cannot clear the workflow after a successful row click.

## What remains unchanged

- The genuine v9 deadloop recovery still runs while the macro is truly waiting for the Rally page after the world-map Rally click.
- The v10 transition-stable all-busy tray recovery remains in place.
- The final Team 1/2/3 fixed-slot detector remains authoritative.
- `UNKNOWN` still never qualifies.
- The random dispatch delay and fresh `Attack.png` revalidation remain unchanged.
- No foreground-window gate is reintroduced.
- The legacy two-team Rally path remains unchanged.

## Build marker

A current explicit three-team run should now include:

```text
[build] JOIN-HOT-RACE-v11 phase-correct entry watchdog loaded
```

When a stale entry watch is deliberately disarmed after Rally-page progress, a useful diagnostic line is:

```text
[rally-v11] entry watchdog disarmed (Joining/Rally page positively reached)
```

or:

```text
[rally-v11] entry watchdog disarmed (Rally row/formation transition already proven)
```

## Regression target

Tests cover:

- a carried Rally level disarming the entry watcher before formation;
- an active attack-open guard disarming the entry watcher;
- the true pre-Rally entry phase still delegating to the original v9 deadloop recovery;
- repeated entry-watch clearing remaining idempotent.

The repository still has the unrelated historical Auto Gather expectation mismatch (test expects 12 level-up clicks while the historical scenario contains 15).  CI results should continue distinguishing that known failure from Rally regressions.
