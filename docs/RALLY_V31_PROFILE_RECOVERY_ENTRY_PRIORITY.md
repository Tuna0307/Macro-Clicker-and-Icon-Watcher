# Rally v31: Profile-recovery entry priority

## Live failure

A 2026-09-05 v30 run hit the existing fail-closed no-capable-Team path:

```text
fresh fixed Team status: T1=BUSY T2=IDLE T3=IDLE
Rally Lv65
eligible none
no capable idle Team; backing out without dispatch
dismissed fixed team panel safely above its validated anchor at (-947, 198)
[rally-v17] fixed-panel outside dismissal armed Profile recovery
```

The outside dismissal click landed on a player on the dense world map and opened
the player profile popup shown by the user.

v17 had correctly armed the scenario's existing `MisClick Profile` recovery, but
the next normal Rally-entry evaluation became READY about 0.32 seconds later.
The base hot-path Rally-entry click deliberately sets
`_rally_hot_profile_armed = False` before sending input. That disarmed v17 before
`FriendStatus.png` could recover the popup.

The following Rally click therefore landed while the player popup was already
covering the world map. v9 later cleared the failed Rally-entry latch, but the
profile popup remained.

## v31 behavior

While v17 owns a still-active profile recovery window:

- `Enter Rally after team probe` is blocked;
- `MisClick Profile` remains enabled and continues to evaluate the existing
  positive `FriendStatus.png` evidence;
- a defensive action-level gate also suppresses any stale Rally click that was
  evaluated before the recovery window took ownership.

If no popup exists, the unchanged v17 timeout expires and normal Rally entry
resumes. v31 adds no blind popup dismissal and does not alter final Attack
authorization.

Legacy two-team Rally remains unchanged.

## Startup marker

```text
[build] JOIN-HOT-RACE-v31 profile-recovery entry priority loaded
```

## Expected live trace

After a risky fixed-panel dismissal:

```text
[rally-v17] fixed-panel outside dismissal armed Profile recovery
[rally-v31] profile recovery owns input priority; new Rally entry blocked for another ...
[rally-v31] profile recovery probe READY; window_rem=...
[fire] MisClick Profile
[rally-fast] profile misclick recovered; rescan immediately
```

If no popup appears, `profile recovery probe blocked` may repeat until the bounded
v17 window expires, after which normal Rally entry resumes.
