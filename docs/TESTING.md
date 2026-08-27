# Testing guide

A green unit suite does not prove live game perception/click geometry.

## Automated checks

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy macro_clicker tools
python -m tools.validate_scenarios
```

Blocking CI: pytest, Ruff lint, scenario/template validation.

## Continuous Gather regressions

Protect:

- resource-search Prepare explicitly clicks middle `採集` / Gather before Gold;
- Gather-tab click is Search-button offset `(0, -480)` and precedes Gold `(+196, -348)`;
- trusted `0/3` -> all Idle candidates;
- 1/3, 2/3, 3/3 busy-count handling;
- compressed ordered-subset mapping rather than fixed row slots;
- missing legacy portrait does **not** imply Team 2;
- `3/3` rows resolve Team 1,2,3 in order;
- exact recent dispatch/history can narrow a row when safe;
- real status crops classify Gathering, Returning, Travelling, Rallying;
- timer parser accepts normal `HH:MM:SS` and common OCR confusions;
- status/timer stay attached to resolved team identity;
- all referenced templates exist;
- stale/untrusted/Unknown state cannot authorize Gather;
- timer expiry never creates Idle;
- exact fixed dispatch-card blue-idle verification remains required;
- no-free-march never replaces;
- unconfirmed attempt pauses.

The committed real status-label crops came from supervised 1920x1080 game screenshots. Keep them as visual regression assets; do not replace them with guessed text rendering.

## Live verification matrix

Test deliberately:

1. Open resource search with `打野` selected; automation must switch to middle `採集`, then Gold.
2. Open resource search with `採集` selected; the normalization click must remain harmless, then Gold.
3. Open resource search with `末日精英` selected; automation must switch to middle `採集`, then Gold.
4. 0/3 all free.
5. each single busy team where possible.
6. each two-busy combination.
7. 3/3 all busy.
8. all three busy, then Team 2 becomes free: visible rows must become Team 1 then Team 3.
9. Gathering (`採集中`).
10. Returning (`返回`).
11. Travelling (`去 X/Y`).
12. Rallying (`集結中`).
13. long and near-zero timers.
14. timer reaches zero but row remains busy: no dispatch until visual Idle.
15. change a team's lead hero and verify no stale portrait misidentification.
16. exact fixed dispatch card is selected before Dispatch.
17. resource taken, no-free-march, F12/unconfirmed safety.

## Cold-start ambiguity

If current portraits are unknown and only part of the team set is busy, expected safe behavior may be `Unknown / waiting for team identity/status confirmation`. That is preferable to guessing. Once identity is learned from unambiguous evidence, compressed layouts should resolve normally.
