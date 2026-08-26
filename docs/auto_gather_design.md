# Auto Gather design notes

## Current design — continuous team-state Auto Gather

The original MVP in this file used a finite "send several marches" model with a fixed busy-march replacement order. That design has been superseded for the normal Bot UI.

Current normal Bot behavior is:

```text
read Team 1/2/3 visual state
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
       resume visual monitoring
```

Key rules:

- Team 1/2/3 state is detected from the world-map expedition sidebar.
- Busy teams are never intentionally replaced by normal Auto Gather.
- There is no user-facing team priority.
- Whichever configured team is freshly visually Idle may be dispatched.
- Existing game state at Bot startup is respected; the Bot does not assume it created the current marches.
- Team states include Idle, Travelling, Gathering, Returning, Busy, and Unknown.
- Visible timers are scheduling hints only. A timer reaching zero never changes a team to Idle without fresh visual confirmation.
- The selected team is re-verified on the dispatch panel and explicitly clicked before Dispatch.
- If the chosen team is no longer idle, or the game reports no free march, the attempt exits fail-closed rather than letting the game select/reassign another team.
- The existing Gold search fallback and resource-taken Cancel/retry flow remain underneath as the one-attempt Gather backend.

See `docs/AUTO_GATHER.md` for the authoritative current contract.

## Historical MVP context

The first implementation target, based on the original 1920×1080 recordings, was:

1. Open the gather/search panel.
2. Select Gold.
3. Search from a high preferred level.
4. Lower the level and retry while the search panel remains visible.
5. Open the found resource and press Gather.
6. Use a free march when available.
7. If all marches were occupied, replace one using a fixed `3 -> 2 -> 1` order.
8. Repeat until a target number of dispatches completed.

That model was useful for proving the search, dispatch, no-free-march, and resource-taken paths, but it is **not** the current normal Bot policy because replacing an already-busy team can interfere with work that existed before the Bot started.

The old replacement state remains only for backward compatibility with the stored scenario/Advanced behavior.