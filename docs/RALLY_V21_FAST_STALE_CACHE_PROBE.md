# Rally v21 — faster stale-cache formation probe

## Live failure

A 2026-09-04 long run showed a remaining performance gap in v19.

At `21:11:29`, Team 1 was freshly dispatched, so the exact fixed-Team cache correctly became restrictive: Team 1 was BUSY and only Teams 2/3 (max60) were known IDLE.

Then Lv80 GoldMob rows appeared repeatedly from about `21:11:41` onward. Detection was healthy:

```text
[rally-v16] Lv80+ GoldMob artwork matched; treating row as GoldMob
[join-rows] ... mob_y=282->plus_y=[309]
[level] ... read 80 ...
```

but the rows were rejected with:

```text
80 > available-team max 60
```

The reason was timing. The exact cache was only about 12-21 seconds old, while v19 required the restrictive cache to age roughly 30 seconds before allowing a formation refresh probe. The Lv80 row disappeared before that threshold was reached.

## v21 policy

For explicit three-team Rally only, v21 lets the existing v19 refresh-probe path become eligible after **10 seconds** instead of waiting about 30 seconds.

This changes only the Rally-page prefilter. It does **not** authorize Attack.

The dispatch sequence remains fail-closed:

```text
high-level GoldMob + detected
-> bounded formation refresh probe
-> fresh fixed Team 1/2/3 capture
-> select capable IDLE Team only
-> configured random delay
-> freshly revalidate Attack.png
-> only then click Attack
```

If Team 1 is still BUSY, or any required fixed-Team status is UNKNOWN, no Attack is sent.

The existing **10-second probe retry cooldown** remains in effect, so failed refresh probes cannot turn into rapid repeated `+` clicking.

The legacy two-team path is unchanged.

## Expected log

Startup:

```text
[build] JOIN-HOT-RACE-v21 fast stale-cache formation probe loaded
```

When a restrictive cache has aged at least about 10 seconds and the configured ceiling is broader:

```text
[rally-v19] exact Team cache is stale (...s; cached ceiling=60, configured ceiling=80); allowing one formation refresh probe
```

A fresh formation screen must still decide whether dispatch is legal.
