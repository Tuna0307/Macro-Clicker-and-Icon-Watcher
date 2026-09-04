# Rally v24: deep diagnostic state trace

## Purpose

v24 is a temporary diagnostic overlay for the explicit three-team Rally path.
It is intentionally behavior-neutral: it adds observability only.

The long-run Rally workflow now contains several independent safety layers for
row pairing, Team identity, sidebar-count lag, stale-cache probes, full-squad
gating, transition recovery, and final Attack revalidation.  Sparse logs can
make two different failures look identical, so v24 records the state machine at
the exact points where evidence changes or input is sent.

Once the workflow is stable across long live runs, these traces can be reduced
or removed without changing the underlying safety logic.

## Startup marker

```text
[build] JOIN-HOT-RACE-v24 deep diagnostic state trace loaded
```

## State heartbeat

v24 emits a state snapshot whenever important state changes and, while otherwise
unchanged, approximately every two seconds.

Example:

```text
[rally-v24][state:change:count] latch=0 pending_level=none pending_team=none |
cache=VALID cache_age=4.21s T1=BUSY T2=IDLE T3=BUSY busy=2 |
sidebar=2/3 expected=none candidate=none |
expect_rally_age=none join_guard_rem=0.00s probe_rem=0.00s
full_hold_rem=0.00s reentry_rem=0.00s |
base_arm=0 profile_arm=0 abort=0 retry=0 cleanup=0
```

The snapshot includes:

- Rally-entry latch state;
- pending Rally level;
- pending selected Team;
- exact fixed-Team cache validity and age;
- T1/T2/T3 fixed-slot state;
- exact BUSY cardinality;
- last world-map sidebar count;
- expected post-dispatch sidebar count and age;
- pending stable-count candidate and age;
- Rally-entry transition-watch age;
- formation transition guard remaining time;
- stale-cache refresh-probe remaining time;
- recent full-squad hold remaining time;
- v23 no-match re-entry cooldown remaining time;
- Base/Profile recovery arming; and
- abort/retry/cleanup flags.

## Sidebar-count trace

Every positively recognized `0/3`..`3/3` sample logs the reconciliation state
before and after the existing v23/v18/v14/v12 observer runs:

```text
[rally-v24][count] observed=1/3
before(sidebar=0/3,expected=1/3,candidate=none,cache=1,busy=1) ->
after(sidebar=1/3,expected=none,candidate=none,cache=1,busy=1) changed=0
```

This makes it possible to distinguish:

- corroborating fixed-Team cardinality;
- expected dispatch confirmation;
- stable Team-return evidence;
- transient/backtracking sidebar reads; and
- actual cache invalidation.

## Rally-entry trace

The `Enter Rally after team probe` evaluation is traced with the current hard
full-squad reason, v23 re-entry cooldown, latch state, sidebar count, and exact
BUSY count.

Example:

```text
[rally-v24][entry] ready=0
hard_reason=exact fixed Team cache is T1=BUSY T2=BUSY T3=BUSY
cooldown_rem=0.00s | ...
```

This is useful for determining whether a missed Rally entry came from:

- all Teams BUSY;
- stable/recent `3/3` evidence;
- the one-second no-match pacing window;
- the entry latch;
- scenario conditions; or
- some other transition state.

## Action trace

Before and after important actions, v24 records the step, action type, result,
and a full state snapshot.

Covered steps include:

- `Enter Rally after team probe`;
- `Joining`;
- `Attack Confirm`;
- `Back if wrong mob`;
- `Back if no slot`;
- `MisClick Base`; and
- `MisClick Profile`.

Example:

```text
[rally-v24][action:before] step='Attack Confirm' type='select_rally_team' ...
[rally-v24][action:after]  step='Attack Confirm' type='select_rally_team' result=True ...
```

The existing detailed Team-selection and fresh-Attack logs remain unchanged.

## Fixed-Team formation trace

Each fixed-Team status capture logs the raw validated state and per-Team idle
match score:

```text
[rally-v24][formation] screen_valid=1 error=none
T1=BUSY idle_score=0.142
T2=IDLE idle_score=0.992
T3=BUSY idle_score=0.188
```

This helps separate a real Team-state change from a cache or sidebar-accounting
problem.

## Rally-row ceiling trace

Every `click_matching_row` level-cap query records the final cap returned by the
already-installed v23/v22/v21/v19/v9 chain together with the complete state.

Example:

```text
[rally-v24][row-cap] result=60 | cache=VALID ... T1=BUSY T2=IDLE T3=BUSY ...
```

A `result=80` can therefore be tied directly to the reason a stale-cache probe
was allowed, while `result=none` can be tied to an all-busy gate.

## No-match trace

The Joining no-match fallback is logged before and after the existing fallback
logic.  The after-state exposes the v23 one-second re-entry cooldown and confirms
whether the entry latch was released.

## Dispatch-cache trace

Immediately around the existing v23 dispatch bookkeeping, v24 logs the cache,
sidebar, expectation, and BUSY cardinality before and after the selected Team is
marked BUSY.

Example:

```text
[rally-v24][dispatch-cache:before] team=T2 | ... busy=2 sidebar=0/3 ...
[rally-v23] dispatch expectation aligned to exact BUSY count: 1/3 -> 3/3
[rally-v24][dispatch-cache:after] team=T2 | ... busy=3 expected=3/3 ...
```

This directly exposes any future mismatch between fixed Team cardinality and the
world-map expectation.

## Safety invariants

v24 does **not** alter behavior.

Unchanged:

- Team identity comes only from fixed formation-screen Team slots.
- Team 1 max level remains 80.
- Team 2 max level remains 60.
- Team 3 max level remains 60.
- Rally-page cache/filter state cannot authorize Attack.
- All-busy/full-squad state remains hard-blocked.
- UNKNOWN Team state remains fail-closed.
- Final dispatch still requires a fresh fixed-Team read, a capable IDLE Team,
  the configured delay, and a fresh `Attack.png` revalidation.
- Legacy two-team Rally behavior is unchanged.

## Cleanup plan

Keep the verbose trace while investigating live runs.  After several stable
long-duration tests, reduce logging in stages:

1. remove two-second heartbeat snapshots;
2. retain state-change snapshots and action/count traces;
3. keep only exceptional reconciliation/recovery events; and
4. preserve the normal concise production logs.
