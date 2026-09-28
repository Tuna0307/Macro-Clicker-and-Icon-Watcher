# Bot skeleton

This is an **additive** architecture for the next automation generation. It does
not replace or rewrite the mature scenario/rally engine.

## Initial priorities

1. Jump to another server.
2. Scan zombie bosses.
3. Automatically join zombie-boss rallies.
4. Do all bot input without taking ownership of the user's physical mouse.

## Non-negotiable input rule

New bot feature code must not silently call `pyautogui` or another physical-cursor
fallback.

The bot depends on an injected game boundary:

```text
Feature policy
    |
    v
GamePort (domain operations)
    |
    +-- future visual/window implementation
    +-- future protocol/runtime implementation
    +-- future hybrid implementation
```

A low-level `Win32MessageInputBackend` is included as the first cursor-free
candidate. It uses `PostMessageW` with client-relative coordinates and therefore
does not move the desktop cursor.

**Evidence status: IMPLEMENTED_NOT_LIVE_TESTED.**

Many games use raw input, DirectInput, or other input paths and may ignore posted
window messages. Do not mark this backend WORKING until a supervised live-game
test proves both the action and its resulting game state.

If it fails, keep the `InputBackend` / `GamePort` boundary and replace only the
backend. Do not push physical mouse control back into feature code.

## Package layout

```text
macro_clicker/bot/
    contracts.py        domain models, result/status contracts, GamePort
    input_backend.py    cursor-free low-level input boundary
    runtime.py          feature facade
    skeleton_port.py    explicit fail-closed placeholder

    features/
        server_jump.py
        boss_scan.py
        rally_join.py
```

The existing `macro_clicker/engine.py`, rally logic, scenarios, templates, and
alerts remain untouched.

## Feature 1: server jump

The first implementation target should be server jumping.

The controller deliberately requires **post-action verification**:

```text
read current server
    -> request jump
    -> wait for transition
    -> read current server again
    -> success only if observed server == requested server
```

Codex should implement this behind `GamePort`, not inside
`ServerJumpController`.

Recommended first states for a future visual implementation:

```text
UNKNOWN
WORLD_MAP
SERVER_SELECTOR
SERVER_TARGET_ENTERED
SERVER_SWITCHING
SERVER_ACTIVE
ERROR
```

Unknown UI must fail closed/retry instead of clicking guessed coordinates.

## Feature 2: zombie-boss scan

`BossScanController` only consumes `GamePort.scan_zombie_bosses()`.

A future scanner can therefore evolve independently:

- screenshot/template/OCR scanning;
- server/map navigation;
- structured/runtime state if a safe supported source is later available;
- hybrid scanning.

Keep discovered boss data in `BossCandidate` rather than exposing screenshot
coordinates throughout the bot.

## Feature 3: auto-join rally

The rally controller currently contains only a deterministic placeholder selection
policy. Existing mature rally knowledge can later be adapted deliberately, rather
than importing the old MacroEngine wholesale.

Expected future flow:

```text
scan bosses
    -> observe joinable rallies
    -> filter by policy
    -> choose rally
    -> join
    -> verify joined state
    -> recover/retry on stale/full rally
```

## Evidence labels

Use these exact labels for the new bot:

- `NOT_IMPLEMENTED`
- `IMPLEMENTED_NOT_LIVE_TESTED`
- `LIVE_TEST_FAILED`
- `LIVE_PROVEN`

Source code existing is not proof that a feature works in the live game.

## Suggested Codex order

1. Add a concrete `GamePort` for **server jump only**.
2. Reuse existing capture/window geometry code where it helps, but keep new bot
   policy outside the mature MacroEngine.
3. Prove cursor-free input on one harmless deterministic UI action.
4. Implement current-server recognition and server-selector recognition.
5. Implement jump + post-jump verification.
6. Only after server jump is `LIVE_PROVEN`, add boss scanning.
7. Add rally joining last, reusing old rally knowledge selectively.
8. Add diagnostics/screenshots for every failed state transition.

## Smoke check

The skeleton can be inspected without sending input:

```powershell
python -m tools.run_bot_skeleton
```

It should report server jump, scan, and rally join as `NOT_IMPLEMENTED`, and the
Win32 message backend as `IMPLEMENTED_NOT_LIVE_TESTED`.
