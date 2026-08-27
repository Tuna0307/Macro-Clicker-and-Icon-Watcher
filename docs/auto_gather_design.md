# Auto Gather design notes

## Current design — continuous team-state Auto Gather

The original MVP used a finite “send several marches” model with fixed busy-march replacement. That design is superseded for the normal Bot UI.

Current normal Bot behavior:

```text
confirm normal world-map view
        ↓
read busy count + busy identities
        ↓
any configured team visually Idle?
   ┌────┴────┐
   No       Yes
   │          │
 wait      search Gold
              ↓
        re-verify exact team
              ↓
        click exact team card
              ↓
           Dispatch
              ↓
       mark non-idle locally
              ↓
       resume monitoring
```

## World-map availability design

The left deployment queue contains **busy marches only**.

Therefore:

```text
trusted normal world map + no busy status = 0/3 busy = all teams free
```

This is not equivalent to “blank screen means free.” The detector first requires the normal-map Gather search control:

- template: `GatherSearchIcon.jpg`
- reference region: `(0, 780, 110, 150)` at 1920×1080
- threshold: `0.90`

A supervised real-game screenshot matched the existing Gather search icon at about **0.99**. The previous gate used `RallyIcon.png`, which scored only about **0.39** on that normal map because it is a Rally workflow icon rather than a universal map control. That incorrect assumption caused Auto Gather to remain stuck in its waiting state.

The real map-gate behavior is locked by `tests/fixtures/team_status/world_map_search_anchor.jpg`.

After map confirmation, current evidence sources are all committed/proven:

- `1_3Squad.png`
- `2_3Squad.png`
- `FullSquad3_3.png`
- `Team1Busy.png` (Murphy)
- `Team3Busy.png` (Stetmann)

Team 2 (Carlie) is inferred from the busy count because there is intentionally no Team 2 identity template.

The previous prototype expected `TeamStatusSidebarHeader.png` plus new march/status templates that were never committed. That design is superseded and must not be restored without real assets/fixtures.

Current map-side classification is `Idle`, `Busy`, or `Unknown`. The tracker can hold richer states later, but detailed activity/timer recognition is not required to decide whether Gather may start.

Contradictory count/portrait evidence returns `Unknown`, not a guess.

If the normal map cannot be confirmed, the service waits for a **readable world-map team view**. It does not require a visible busy-team sidebar and it does not treat an unrelated blank overlay as all free.

## Second safety gate

Before Dispatch, the selected-team runtime still requires that exact team’s blue idle indicator and clicks that exact card. If it is no longer idle, the attempt exits. This protects against stale map observations and mid-search state changes.

## Other current rules

- Busy teams are never intentionally replaced by normal Auto Gather.
- There is no user-facing team priority.
- Existing game state at Bot startup is respected.
- No-free-march closes/stops rather than replacing.
- Resource-taken Cancel/retry remains underneath.
- An unconfirmed attempt pauses fail-closed.
- Timer expiry, if timers are added later, must never create `Idle` by itself.
- Workflow-specific templates must not become generic screen gates without real screenshot regression evidence.

See `docs/AUTO_GATHER.md` for the authoritative current contract.

## Historical MVP context

The first implementation target was:

1. open resource search;
2. select Gold;
3. search from a preferred level;
4. lower level while unavailable;
5. open resource and Gather;
6. use a free march;
7. if all occupied, replace one in `3 -> 2 -> 1` order;
8. repeat until a target count.

That model proved the resource/search/retry paths but is not current normal Bot policy. Legacy replacement state remains only for backward compatibility/Advanced behavior.
