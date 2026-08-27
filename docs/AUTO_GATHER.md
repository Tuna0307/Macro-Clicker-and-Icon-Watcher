# Continuous Auto Gather workflow

This document is the authoritative behavior contract for **normal Bot Auto Gather**.

Normal Bot gathering is a persistent service driven by visually observed Team 1/2/3 availability. It does not use the legacy finite “send N marches” / fixed `3 -> 2 -> 1` replacement policy.

## Current architecture

```text
TeamStatusMonitor
      ↓
TeamStatusDetector
      ↓
TeamStateTracker
      ↓
ContinuousGatherService
      ↓
selected-team runtime Gather scenario
      ↓
MacroEngine
```

Relevant modules:

- `macro_clicker/bot/team_status.py` — read-only trusted-normal-world-map availability observation.
- `macro_clicker/bot/team_state.py` — shared Team 1/2/3 state/freshness.
- `macro_clicker/bot/continuous_gather.py` — decides when one configured team may be sent.
- `macro_clicker/bot/adapters.py` — exact-team dispatch-panel verification/clicking.
- `scenarios/Gather Gold.json` — proven Gold search/resource/taken-warning flow.
- `macro_clicker/resource_gathering.py` — legacy/scenario compatibility, not normal continuous policy.

## Normal-user policy

- Resource: Gold.
- Start at the configured resource level.
- If unavailable, lower one level and search again.
- Users choose which Team 1/2/3 may gather.
- There is no user-facing team priority.
- Busy/Unknown teams are left alone.
- If every configured team is busy, wait.
- Auto Gather never intentionally recalls/replaces a busy team.
- Before Dispatch, re-verify and explicitly click the exact chosen team.
- No-free-march closes/stops instead of replacing an occupied march.
- A confirmed dispatch immediately marks that team non-idle.
- An unconfirmed/aborted attempt pauses fail-closed.

## Correct world-map status semantics

The game’s left deployment/status queue shows **busy teams only**.

That means this is valid visual evidence:

```text
confirmed normal world map
+ no busy team status/count visible
= 0/3 busy
= Team 1 Idle, Team 2 Idle, Team 3 Idle
```

The detector must first prove that it is looking at the normal world map. The current trusted anchor is:

```text
templates/GatherSearchIcon.jpg
reference search region: x=0, y=780, width=110, height=150
reference resolution: 1920x1080
threshold: 0.90
```

This anchor is based on supervised real-game evidence. On the supplied 1920×1080 normal-world-map screenshot, the committed Gather search icon matched at about **0.99**. The previous implementation incorrectly used `templates/RallyIcon.png`; that asset matched only about **0.39** because the Rally workflow icon is not visible on the normal map. That bad gate caused Auto Gather to wait indefinitely even while the game was on the correct screen.

A small non-identifying real-screen regression fixture now lives at:

```text
tests/fixtures/team_status/world_map_search_anchor.jpg
```

Do not substitute a workflow-specific icon for the normal-world-map gate unless real fixtures prove it is actually present on the intended screen.

After the map anchor passes, the detector reuses proven assets:

```text
templates/1_3Squad.png
templates/2_3Squad.png
templates/FullSquad3_3.png
templates/Team1Busy.png
templates/Team3Busy.png
```

Team identities:

- Team 1 = Murphy
- Team 2 = Carlie
- Team 3 = Stetmann

The busy queue compresses upward, so row position is not team identity. Team 1 and Team 3 are recognized by portrait anywhere in the queue. Team 2 is inferred from the busy count and those two known identities.

### Busy-count interpretation

```text
0/3 -> all three Idle candidates
3/3 -> all three Busy
1/3 -> identify Team 1 or Team 3 by portrait; otherwise infer Team 2
2/3 -> identify Team 1/3 by portrait and infer the remaining Team 2 state
```

If count and portrait evidence contradict each other, all teams become `UNKNOWN` for that observation. The bot does not guess.

Crucially, “blank means 0/3” applies **only** after the trusted Gather-search world-map anchor is visible. Blank/hidden status on another overlay or screen cannot authorize a dispatch.

## Previous detector failures that must not return

The earlier prototype incorrectly required `templates/TeamStatusSidebarHeader.png`. That file does not exist, so Auto Gather raised a `FileNotFoundError` and remained stuck.

A later correction removed that missing template but used `templates/RallyIcon.png` as a generic world-map anchor. A supervised live test proved that icon is not present on the normal map, so Auto Gather again stayed stuck at the waiting state.

Both failures are now regression targets:

- every detector template path must exist;
- the normal-world-map anchor must match a real committed normal-map fixture.

When the map cannot be trusted, logs/status should say **“Waiting for a readable world-map team view”**, not imply that a visible team-status sidebar is required.

## Current map-side states

`TeamStateTracker` can model:

```text
IDLE
TRAVELLING
GATHERING
RETURNING
BUSY
UNKNOWN
```

The current live map detector intentionally resolves the decision-critical states only:

```text
IDLE
BUSY
UNKNOWN
```

The previous Travelling/Gathering/Returning detector referenced uncommitted/nonexistent templates, so those richer labels and timer OCR are not part of the current safe runtime. They may be reintroduced later from real committed fixtures without changing the core availability contract.

## Exact-team dispatch is the second safety gate

A map-side `IDLE` observation only allows Auto Gather to start one candidate attempt.

On the dispatch panel, the runtime adapter must still:

1. detect the normal Dispatch button;
2. require the selected team’s exact blue idle indicator;
3. click that exact team card;
4. wait briefly;
5. run Dispatch;
6. verify success through the existing Gather success path.

If the selected team became busy, the attempt exits instead of sending another team. This protects against stale map frames, perception mistakes, or state changes during resource search.

## Timer rule

Timers, if/when observed in a future detector, are scheduling hints only. A locally elapsed countdown must never promote a team to `IDLE`. Fresh visual state remains authoritative.

## Resource-taken behavior

The proven Scenario retains the observed resource-taken Cancel/retry path. A taken resource does not count as a successful dispatch.

## Legacy compatibility

`march_count`, `replacement_order`, and legacy replacement state remain loadable for older configs/Advanced workflows. They are **not** the normal continuous Bot contract.

## Safety invariants

Preserve:

- trusted normal-world-map gate before blank status can mean 0/3;
- Gather search icon is the current real-map gate, not RallyIcon;
- contradictory count/identity evidence -> `UNKNOWN`;
- fresh visual Idle required;
- stale/untrusted observations cannot dispatch;
- timer expiry cannot authorize;
- exact selected team must still be idle on the dispatch panel;
- busy teams are never intentionally overwritten;
- no-free-march does not replace;
- all clicks remain inside `MacroEngine` safe input paths;
- foreground/target-window/kill-switch protections remain active;
- unconfirmed attempt pauses fail-closed.

## Regression coverage

Important automated coverage includes:

- `tests/test_team_status.py`
- `tests/test_continuous_gather.py`
- `tests/test_bot_adapters.py`
- `tests/test_bot_status.py`
- `tests/test_bot_ui_runtime.py`
- existing legacy/scenario Gather tests.

Protect at least:

- real normal-map Gather-search fixture matches the configured gate;
- 0 busy status on trusted world map -> all three Idle candidates;
- 1/3, 2/3, 3/3 inference;
- Team 2 inference from count;
- contradictory evidence -> Unknown;
- detector template paths exist;
- stale/untrusted state cannot dispatch;
- all-busy waiting;
- exact-team click before Dispatch;
- no-free-march no-replacement;
- successful dispatch marks exact team non-idle;
- unconfirmed attempt pauses.

## Live verification still required

A supervised real-game test should cover:

1. normal world map is recognized by the Gather search icon;
2. all three teams free — no busy status visible — Auto Gather starts;
3. one busy team;
4. two busy teams;
5. all three busy — waits;
6. Team 2-only busy inference;
7. exact intended dispatch card is clicked;
8. busy teams remain untouched;
9. one busy team later becomes visually free and can be sent;
10. resource-taken Cancel/retry;
11. F12/unconfirmed attempt pauses;
12. no-free-march never replaces an occupied march;
13. overlays that hide the normal-map search control do not authorize blank-status Idle.
