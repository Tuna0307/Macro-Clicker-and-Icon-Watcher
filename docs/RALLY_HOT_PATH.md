# Three-Team Rally Hot Path

This document describes the speed-sensitive runtime used only by explicit three-team Rally scenarios. The legacy two-team Rally path is intentionally unchanged.

## Goal

Rally joining is a race. The macro should spend CPU/time on expensive safety work only when that work can actually matter, while preserving fail-closed final team selection.

The hot path therefore keeps normal world-map polling lightweight, avoids fixed transition sleeps, and moves misclick protection to the moments that can cause those misclicks.

## World-map loop

Three-team Rally runs at a 0.05 second polling interval at runtime. The scenario file is not rewritten merely to activate this runtime optimization.

The normal entry condition still detects the small Rally icon first. When that condition is true, the runtime checks whether the left-side squad counter can positively prove `3/3`.

The original fast gate keeps the historical small counter ROI. The v7 overlay adds a second bounded left-side search band around that area so ordinary layout movement cannot make the macro depend on one exact pixel rectangle.

- positively proven `3/3` means all three squads are out, so Rally entry is suppressed and polling continues immediately;
- failure to prove `3/3` is fail-open only for this entry optimization;
- failure to prove `3/3` never authorizes a dispatch, because the final fixed-slot / tray checks remain fail-closed;
- this count gate never identifies Team 1/2/3 and never replaces the authoritative fixed-slot detector.

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
- the all-busy tray recovery described below releases it.

This is a state guard only. It adds no sleep or cooldown.

## Fixed waits

The historical 0.5 second wait immediately after clicking the world-map Rally icon is skipped in explicit three-team mode. The engine's normal hot polling detects the Rally page as soon as it appears.

The configured random dispatch wait on `Attack Confirm` remains unchanged. It is the intentional user-configured Rally delay and is the only deliberate join delay in the fast path.

## MisClick Base

`MisClick Base` keeps full-screen coverage. Its detection ROI is not narrowed because another player's base can appear anywhere on the world map.

The optimization is scheduling only:

1. during normal Rally polling, the expensive whole-screen Base step is gated off;
2. immediately before the risky world-map Rally click, the Base watchdog is armed;
3. Rally-page checks continue without waiting for the Base watchdog;
4. if the Rally page becomes recognizable, the `Joining` path disarms the Base watchdog before an expensive scan is needed;
5. if the click actually opened a base popup, the same full-screen detector performs the existing DiamondCart recovery;
6. successful Base recovery disables stale Rally workflow steps and returns control to the world-map hot loop.

The v7 all-busy tray dismissal also arms the existing Base watchdog immediately afterward because that user-confirmed dismissal is a map-adjacent click. This preserves full-screen Base recovery even for that new exit path.

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

### Residual profile race

There is still a very small interval between the final micro-check and the physical click. If the profile page opens anyway, the event-armed `MisClick Profile` step closes it, clears stale carried Rally state, and allows an immediate fresh Rally rescan without releasing the Rally-entry latch.

## Full-squad tray recovery

A second live state exists when all three squads are already out.

After the macro clicks a valid Rally row `+`, the game can show only the fixed bottom squad-card tray over the map instead of the normal central `SquadAmount` / Attack panel. There is no dispatch button to press and the original scenario has no step that recognizes this tray, so the old runtime could sit there indefinitely.

v7 recognizes this state with bounded visual evidence:

1. `templates/AddSquad.png` must positively validate the fixed bottom squad-card bar inside the existing bottom-band ROI;
2. `templates/SquadAmount.png` is checked in the proven formation-panel anchor band;
3. if `SquadAmount.png` is present, this is a normal formation screen and normal `Attack Confirm` handling remains in charge;
4. only when the bottom tray is proven and the formation anchor is absent are the three fixed `TeamIdleZZ.png` slot ROIs interpreted;
5. all three slots must positively resolve to `BUSY`;
6. only that exact `BUSY / BUSY / BUSY` state triggers recovery;
7. the macro clicks a neutral padded area of the tray once, never clicks a team card and never clicks Attack;
8. Rally transient state and enabled workflow steps are cleared, the entry latch is released, and hot world-map polling resumes on the next 30–50 ms cycle.

If any tray slot is `IDLE` or `UNKNOWN`, v7 does not guess and does not perform the tray recovery click.

The tray probe itself uses bounded bottom/formation captures rather than a whole-screen template scan.

## Final team safety

Speed optimizations do not change the final authority:

- fixed slot 1 = Team 1;
- fixed slot 2 = Team 2;
- fixed slot 3 = Team 3;
- ZZ present = `IDLE`;
- validated slot without ZZ = `BUSY`;
- invalid/unproven formation screen = `UNKNOWN`.

Only exact `IDLE` teams that satisfy their configured level limit can be dispatched. `UNKNOWN` or no capable idle team still backs out without dispatch.

Current configured limits remain Team 1 = 65, Team 2 = 55, Team 3 = 55.

Therefore a positively proven `3/3`, or a later positively proven `BUSY / BUSY / BUSY` tray, can never dispatch a fourth squad.

## Live marker and logging

The underlying v6 hot runtime still logs its original build line, and the v7 overlay then logs:

`[build] JOIN-HOT-RACE-v7 full-squad recovery loaded`

Useful v7 logs include:

- `[rally-fast] Rally entry latched until workflow exit`
- `[rally-fast] broad 3/3 gate: all squads out; Rally entry suppressed`
- `[team3] fixed squad tray shows T1=BUSY T2=BUSY T3=BUSY; dismissed ... without dispatch`

Existing race-path logs remain useful:

- `[rally-fast] last-slot + vanished before input; stale click cancelled`
- `[rally-fast] revalidated last-slot + score=...`
- `[rally-fast] profile misclick recovered; rescan immediately`

## Regression requirements

Tests must continue proving:

- the legacy two-team path is unchanged;
- whole-screen MisClick Base coverage is retained;
- Base/Profile checks are event-gated when unarmed;
- a positive `3/3` result suppresses Rally entry;
- the v7 broad `3/3` search can find the counter outside the historical tiny ROI;
- Rally entry is latched after the first world-map click and cannot re-fire inside the same workflow;
- normal back/recovery and successful dispatch release the latch;
- last-moment Join revalidation uses a small fresh capture;
- a vanished Join control cancels the stale click;
- the normal formation fixture is not mistaken for the tray-only state;
- the committed all-busy fixed-card pixels still resolve to Team 1/2/3 = `BUSY` when the formation anchor is absent;
- all-busy tray recovery exits without team-card or Attack input;
- the existing fixed-slot screenshot matrix and configured level ceiling remain green.
