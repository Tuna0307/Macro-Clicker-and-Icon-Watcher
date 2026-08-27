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

## Current next work

1. Verify the three-tab resource-search popup always normalizes to middle `採集` before Gold, regardless of the previously selected tab.
2. Confirm repaired Gathering/Rallying status PNGs decode and classify correctly on Windows CI and the user's live installation.
3. Verify a runtime missing one or more detailed TeamStatus*.png assets degrades affected rows to generic Busy instead of breaking the whole team monitor.
4. Re-run Auto Gather on normal world map and confirm it passes the map gate.
5. Verify 0/3, 1/3, 2/3, 3/3 busy counts.
6. Verify ordered compression: all 3 busy -> Team 2 free -> rows become Team 1 then Team 3.
7. Verify Gathering, Returning, Travelling, Rallying recognition and timer OCR.
8. Change a lead hero and verify learned/static identity logic does not mislabel the team.
9. Verify a timer reaching zero only triggers visual refresh, never Idle by itself.
10. Verify exact intended fixed dispatch card is rechecked/clicked and busy teams are untouched.
11. Verify resource-taken retry, F12/unconfirmed pause, and no-free-march no-replacement.
12. Design safe Rally/Gather cooperative handoff only if simultaneous continuous operation is required.

## Known safe limitations

A cold start at ambiguous 1/3 or 2/3 with changed/unlearned hero portraits can remain Unknown until the bot obtains unambiguous identity evidence. Do not "solve" this by guessing from row number or missing legacy portraits.

Detailed status-label images are optional enhancements. If a local installation is missing one or more of them, affected busy rows may display generic Busy until the assets are restored. This must not be treated as Idle or as a reason to stop core availability monitoring.

## Asset integrity lesson

A committed image can exist by filename but still be unusable. The 2026-08-27 CI failure caught this for Gathering/Rallying. Future visual-template work must include OpenCV decode checks in addition to path-existence validation.
