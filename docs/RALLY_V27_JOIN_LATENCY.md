# Rally v27: faster Rally-row `+` response

## Live timing evidence

`logs(3).zip` (2026-09-04, v26) showed a consistent delay between the world-map
Rally click and the actual Rally-row `+` click.

Across 13 successful `click matching row` events:

- Rally-icon click -> row `+` click: about 2.24-2.52 s
- median: about 2.40 s
- once Joining was already READY -> `+` click: about 0.36-0.54 s

The level OCR and final local `+` micro-revalidation were therefore not the main
delay.

The consistent missing time was between:

```text
wait 0.3s (mob2-paced Rally entry settle)
```

and the start of the first `Joining` condition scan. In the measurable events,
that unaccounted interval was about 1.19-1.37 s (median about 1.27 s).

## Cause

The explicit three-team scenario still contains the older world-map step:

```text
Probe fixed three-team status
```

before the time-critical `Joining` step.

That probe is useful on the normal world map. It positively looks for
`RallyIcon.png` plus the bottom `AddSquad.png` tray before opening a fixed-Team
probe.

Once `_rally_hot_entry_latched` is true, however, the macro has already clicked
the Rally icon and is inside the Rally workflow. Running the world-map Team
probe during that phase is unnecessary and consumes the transition window before
Joining can inspect the Rally rows.

## v27 policy

Only in explicit three-team mode:

```text
_rally_hot_entry_latched == True
+
step == "Probe fixed three-team status"
```

the old world-map probe is bypassed for that evaluation.

When the Rally workflow is not latched, the probe is unchanged.

The `Joining` step itself is unchanged and still requires:

1. GoldMob row evidence;
2. same-row last-slot `+`;
3. current Team-cache level ceiling;
4. OCR level validation where configured;
5. fresh local revalidation of the exact `+` immediately before input.

Formation Team identity and final Attack safety are unchanged.

## Diagnostics

Startup:

```text
[build] JOIN-HOT-RACE-v27 latched world-probe bypass loaded
```

When the optimization activates:

```text
[rally-v27] active Rally workflow latched; skipping world-map fixed-team probe so Joining gets the time-critical scan window
```

The first Joining scan also logs measured latency from the Rally-icon click:

```text
[rally-v27] first Joining scan begins 0.xxxs after Rally-icon click
```

This makes the next live run directly comparable with the v26 baseline.

## Safety invariants

Unchanged:

- legacy two-team Rally behavior;
- Team 1 max80, Team 2 max60, Team 3 max60;
- fixed formation slots remain authoritative for Team identity;
- stale-cache refresh probes never authorize Attack;
- UNKNOWN Team state remains fail-closed;
- final dispatch remains:
  fresh fixed Team state -> capable IDLE Team -> configured random delay ->
  fresh Attack revalidation -> Attack.
