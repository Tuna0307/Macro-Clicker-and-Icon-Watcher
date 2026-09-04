# Rally v20 — final-abort transition-guard reset

## Live failure

A long `Rally gold mob_ 3 team` run on 2026-09-04 stayed healthy until a
fresh fixed-Team check correctly rejected a Lv70 Rally because Team 1 was BUSY:

```text
17:03:18.826 [team3] Rally Lv70; fresh final T1=BUSY T2=IDLE T3=IDLE
17:03:18.827 [team3] eligible none; selected none
17:03:18.828 [team3] no capable idle Team; backing out without dispatch
17:03:18.828 [team3] dismissed fixed team panel safely above its validated anchor
17:03:18.829 [rally-v17] fixed-panel outside dismissal armed Profile recovery
```

The abort itself was correct and fail-closed. The problem happened immediately
after it. A new Rally entry began while the previous row-click's 2.5-second
formation-opening guard was still active:

```text
17:03:18.995 [fire] Enter Rally after team probe
17:03:19.308   step 'Enter Rally after team probe' -> enabled
17:03:20.089   [rally-v11] entry watchdog disarmed
                 (Rally row/formation transition already proven)
```

That guard belonged to the *previous* Rally workflow. v11 therefore mistook
stale transition state for proof that the new Rally entry had already advanced.
The v9 entry watchdog was disarmed. The new Rally entry never produced a
GoldMob/Rally page, but the latch remained set, so the run spent the rest of the
log repeatedly evaluating `Joining` and `MisClick Base` without another
successful Rally workflow.

## v20 behavior

After the existing explicit three-team final-abort routine completes, v20 clears:

```text
_rally_join_guard_until = 0.0
```

Expected live marker:

```text
[build] JOIN-HOT-RACE-v20 abort transition-guard reset loaded
```

When an abort retires a still-active previous transition guard:

```text
[rally-v20] final abort cleared prior formation-transition guard before next Rally entry
```

The next Rally entry therefore starts in the true entry phase. If it fails to
reach the Rally page, the existing v9 watchdog remains authoritative and can
release the workflow/latch safely.

## Safety / scope

v20 does not authorize Attack and does not relax any final validation. Attack
still requires:

```text
fresh fixed Team status
-> select capable IDLE Team
-> configured random delay
-> freshly revalidate Attack.png
-> click Attack
```

The patch changes only the explicit three-team final-abort cleanup. It does not
modify the legacy two-team Rally path, Team level limits, Rally row pairing,
GoldMob identity, OCR thresholds, or Profile/Base positive-template recovery.
