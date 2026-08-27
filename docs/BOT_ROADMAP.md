# Dedicated Bot migration roadmap

## Phase status

| Phase | Status | Current contract / next proof |
| --- | --- | --- |
| Bot config / shell / adapters | Core complete | Normal controls wrap proven backend. |
| Rally | Core complete | Preserve mature behavior; revalidate after shared changes. |
| Continuous Gather | Implemented, live proof ongoing | Trusted map + compressed dynamic sidebar + status/timer tracking + fixed dispatch safety. |
| Alerts | Core complete | Passive observation. |
| Positions | Core complete | Finite workflows. |
| Dashboard | In progress | Detailed Team Idle/Travelling/Gathering/Returning/Rallying/Busy/Unknown + timers now supported. |
| Scheduling | Basic complete | Live proof pending. |
| Testing | Ongoing | CI plus supervised game fixtures required. |

## Current next work

1. Obtain green Windows CI for dynamic-sidebar/status change.
2. Re-run Auto Gather on normal world map and confirm it passes the map gate.
3. Verify 0/3, 1/3, 2/3, 3/3 busy counts.
4. Verify ordered compression: all 3 busy -> Team 2 free -> rows become Team 1 then Team 3.
5. Verify Gathering, Returning, Travelling, Rallying recognition and timer OCR.
6. Change a lead hero and verify learned/static identity logic does not mislabel the team.
7. Verify a timer reaching zero only triggers visual refresh, never Idle by itself.
8. Verify exact intended fixed dispatch card is rechecked/clicked and busy teams are untouched.
9. Verify resource-taken retry, F12/unconfirmed pause, and no-free-march no-replacement.
10. Design safe Rally/Gather cooperative handoff only if simultaneous continuous operation is required.

## Known safe limitation

A cold start at ambiguous 1/3 or 2/3 with changed/unlearned hero portraits can remain Unknown until the bot obtains unambiguous identity evidence. Do not "solve" this by guessing from row number or missing legacy portraits.
