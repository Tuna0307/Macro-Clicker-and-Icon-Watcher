# Three-Team Rally Hot Path

This document describes the speed-sensitive runtime used only by explicit three-team Rally scenarios. The legacy two-team Rally path is intentionally unchanged.

## Goal

Rally joining is a race. The macro should spend CPU/time on expensive safety work only when that work can actually matter, while preserving fail-closed final team selection.

The hot path therefore keeps normal world-map polling lightweight, avoids fixed transition sleeps, and moves misclick protection to the moments that can cause those misclicks.

## World-map loop

Three-team Rally runs at a 0.05 second polling interval at runtime. The scenario file is not rewritten merely to activate this runtime optimization.

The normal entry condition still detects the small Rally icon first. When that condition is true, the runtime also checks the fixed squad-count ROI against `templates/FullSquad3_3.png`.

- positively proven `3/3` means all three squads are out, so Rally entry is suppressed and polling continues immediately;
- failure to prove `3/3` is fail-open for this optimization, because the final fixed-slot ZZ detector still blocks unsafe dispatch;
- this count gate never identifies Team 1/2/3 and never replaces the authoritative fixed-slot detector.

## Fixed waits

The historical 0.5 second wait immediately after clicking the world-map Rally icon is skipped in explicit three-team mode. The engine's normal hot polling detects the Rally page as soon as it appears.

The configured random dispatch wait on `Attack Confirm` remains unchanged. With the current scenario it is 1.0–1.5 seconds and is the intended user-configured Rally delay.

## MisClick Base

`MisClick Base` keeps full-screen coverage. Its detection ROI is not narrowed because another player's base can appear anywhere on the world map.

The optimization is scheduling only:

1. during normal Rally polling, the expensive whole-screen Base step is gated off;
2. immediately before the risky world-map Rally click, the Base watchdog is armed;
3. its whole-screen scan is deferred by only 0.12 seconds without blocking Rally-page checks;
4. if the Rally page becomes recognizable, the `Joining` path disarms the Base watchdog before an expensive scan is needed;
5. if the click actually opened a base popup, the same full-screen detector becomes eligible after the short transition window and performs the existing DiamondCart recovery;
6. successful Base recovery disables stale Rally workflow steps and returns control to the world-map hot loop.

This preserves the original safety coverage without paying roughly 0.45–0.86 seconds on every ordinary cycle.

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

There is still a very small interval between the final micro-check and the physical click. If the profile page opens anyway, the event-armed `MisClick Profile` step closes it, clears the stale carried Rally level and transition guard, and allows an immediate fresh Rally rescan.

## Final team safety

Speed optimizations do not change the final authority:

- fixed slot 1 = Team 1;
- fixed slot 2 = Team 2;
- fixed slot 3 = Team 3;
- ZZ present = `IDLE`;
- validated slot without ZZ = `BUSY`;
- invalid/unproven formation screen = `UNKNOWN`.

Only exact `IDLE` teams that satisfy their configured level limit can be dispatched. `UNKNOWN` or no capable idle team still backs out without dispatch.

Current configured limits remain Team 1 = 65, Team 2 = 55, Team 3 = 55, with explicit three-team priority/policy already handled by the existing Rally policy layer.

## Live marker and logging

An explicit three-team run should log build marker:

`JOIN-HOT-RACE-v6`

Useful race-path logs include:

- `[rally-fast] last-slot + vanished before input; stale click cancelled`
- `[rally-fast] revalidated last-slot + score=...`
- `[rally-fast] profile misclick recovered; rescan immediately`

## Regression requirements

Tests must continue proving:

- the two-team scenario file remains byte-identical;
- whole-screen MisClick Base coverage is retained;
- Base/Profile checks are event-gated when unarmed;
- a positive 3/3 result suppresses Rally entry while a failed gate does not authorize dispatch;
- last-moment Join revalidation uses a small fresh capture;
- a vanished Join control cancels the stale click;
- the BackButton no-match fallback is not intercepted by Join revalidation;
- the existing real fixed-slot screenshot matrix and the 65 Rally-level ceiling remain green.
