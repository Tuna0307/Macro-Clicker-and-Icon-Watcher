# Dedicated Bot migration roadmap

## Phase status

| Phase | Status | Current contract / next proof |
| --- | --- | --- |
| Bot config / shell / adapters | Core complete | Normal controls wrap proven backend. |
| Rally | Core complete | Preserve mature behavior; revalidate after shared changes. |
| Continuous Gather | Implemented, live proof ongoing | Search-tab normalization + trusted map + compressed dynamic sidebar + resilient status/timer tracking + fixed dispatch safety. |
| Alerts | Core complete | Passive observation. |
| Positions | Core complete | Finite workflows. |
| Dashboard | In progress | Detailed Team Idle/Travelling/Gathering/Returning/Rallying/Busy/Unknown + timers now supported. |
| Scheduling | Basic complete | Live proof pending. |
| Testing | Ongoing | CI plus supervised game fixtures required. |

Multi-monitor target-window operation is supported. A 2026-08-28 supervised run detected and clicked the 1920x1080 game on the left secondary monitor at desktop rectangle `(-1920, 0, 1920, 1080)`. Input now activates the exact selected target automatically before revalidating it; retain automated negative-coordinate coverage and repeat live activation verification after capture/input changes.

## Current next work

1. Reproduce the startup-to-first-Gather flow with the new `[team-diag]` telemetry and determine whether the observed long pause is world-map gating, busy-count matching, PaddleOCR initialization, or another slow scan stage before changing behavior.
2. Verify the resource-search popup always normalizes to middle `採集` before Gold and resets a remembered higher level before applying the configured start/maximum level; test Lv3 from a popup left at Lv9/Lv12, then confirm Lv2/Lv1 fallback.
3. Confirm repaired Gathering/Rallying status PNGs decode and classify correctly on the user's live installation.
4. Verify a runtime missing one or more detailed TeamStatus*.png assets degrades affected rows to generic Busy instead of breaking the whole team monitor.
5. Re-run Auto Gather on normal world map and confirm it passes the map gate.
6. Verify 0/3, 1/3, 2/3, 3/3 busy counts; compare live `1/3`, `2/3`, and `3/3` candidate scores from diagnostics if classification is unexpected.
7. Verify ordered compression: all 3 busy -> Team 2 free -> rows become Team 1 then Team 3.
8. Verify Gathering, Returning, Travelling, Rallying recognition and timer OCR, including the logged first-OCR initialization duration.
9. Change a lead hero and verify learned/static identity logic does not mislabel the team.
10. Verify a timer reaching zero only triggers visual refresh, never Idle by itself.
11. Verify exact intended fixed dispatch card is rechecked/clicked and busy teams are untouched. The missing Team 2 idle crop has been restored and automated validation now covers all three cards; supervised Team 1 -> Team 2 -> Team 3 progression remains pending.
12. Verify an input due while another app is active automatically foregrounds the exact Last War target on either monitor, while activation failure remains fail-closed.
13. Verify resource-taken retry, F12/unconfirmed pause, and no-free-march no-replacement.
14. Design safe Rally/Gather cooperative handoff only if simultaneous continuous operation is required.

## Known safe limitations

A cold start at ambiguous 1/3 or 2/3 with changed/unlearned hero portraits can remain Unknown until the bot obtains unambiguous identity evidence. Do not "solve" this by guessing from row number or missing legacy portraits.

Detailed status-label images are optional enhancements. If a local installation is missing one or more of them, affected busy rows may display generic Busy until the assets are restored. This must not be treated as Idle or as a reason to stop core availability monitoring.

The temporary `[team-diag]` logging is intentionally observational. It may add log lines but must not influence team state or dispatch eligibility; remove/reduce it only after the startup-delay cause is proven and the useful permanent diagnostics are decided.

## Asset integrity lesson

A committed image can exist by filename but still be unusable. The 2026-08-27 CI failure caught this for Gathering/Rallying. Future visual-template work must include OpenCV decode checks in addition to path-existence validation.
