# PC Automation Bot

A Windows visual-automation and passive-monitoring utility. Normal users configure Dashboard, Rally, Gather, Positions, Alerts, Schedule, Logs, and Settings; low-level Scenario editing remains under Advanced.

Run with:

```text
Run PC Automation Bot.bat
```

Per-user runtime state normally lives under `%LOCALAPPDATA%\Macro Clicker and Icon Watcher`.

## Architecture

```text
Bot UI -> BotConfig -> services/adapters -> MacroEngine -> Detection/OCR/Safety
```

Normal Bot saves do not rewrite tuned Scenario JSON.

## Input ownership

Only one active clicking automation owns input. Development/Science are finite, Rally is continuous, Auto Gather is a persistent state-driven service, and Alerts are passive. Rally + continuous Gather are still blocked together until safe cooperative handoff is implemented.

## Continuous Auto Gather

Users choose Gold start level and which Team 1/2/3 may gather. Auto Gather watches actual march availability, sends one exact free team, then keeps monitoring until another configured team is visually free.

The normal world map is verified with `GatherSearchIcon.jpg`. The left deployment queue shows busy marches only, so a trusted map with no busy count/status means real `0/3` and all teams are Idle candidates.

The resource-search popup contains three remembered tabs. Auto Gather explicitly selects the fixed middle `採集` / Gather tab before selecting Gold, so it works even when the popup was previously left on `打野` or `末日精英`.

The popup remembers its last level too. Before the first search, normal Bot Gather clamps that remembered value to the minimum and raises it to the configured starting/maximum level. Setting Lv3 therefore searches Lv3 first, then Lv2 and Lv1 only if higher levels are unavailable; it does not restrict searches to exactly Lv3.

### Dynamic sidebar rows

Sidebar rows are compressed, not fixed:

```text
busy 3       -> [3]
busy 1 + 3   -> [1, 3]
busy 1+2+3   -> [1, 2, 3]
2 becomes free -> [1, 3]
```

Rows stay ordered by team number among the busy teams. Hero portraits also change whenever that team's lead hero changes, so the bot does not treat a particular hero face as a permanent Team number.

The detector learns current portraits only after identity is unambiguous and stores them in per-user runtime data. Legacy Team 1/Team 3 portrait templates are positive bootstrap hints only; failure to match them never means Team 2.

### Team status and timers

Live screenshots confirmed:

- Gathering (`採集中`)
- Returning (`返回`)
- Travelling (`去 X:... Y:...`)
- Rallying (`集結中`)

Detailed status images are optional enhancements. If one is unavailable in a local runtime, that busy row degrades to generic `Busy` instead of breaking the whole team monitor. Timer OCR may still provide a countdown. The bot never treats this fallback as Idle.

Gathering and Rallying status PNGs were rebuilt on 2026-08-27 after Windows CI showed their earlier committed blobs could exist by filename while still being unreadable by OpenCV. Status-template validation therefore checks actual image decoding, not just file existence.

The Dashboard can show detailed states with countdowns when the assets are available. Timers reduce unnecessary polling, but reaching `00:00:00` never makes a team Idle automatically; fresh visual evidence is required.

If a cold start at `1/3` or `2/3` cannot safely identify the busy subset yet, state remains Unknown and Gather waits instead of guessing.

### Delay diagnostics

During the current supervised startup-delay investigation, the Logs tab emits `[team-diag]` evidence without changing automation behavior. It reports the world-map match score, separate `1/3` / `2/3` / `3/3` busy-count scores, whether team identity is complete, rate-limited unreadable-view messages, PaddleOCR initialization start/ready/failure duration, and any team-monitor scan that takes at least two seconds. These lines are intended to show exactly where a pause occurs; they are not dispatch authority.

## Fixed dispatch positions remain final authority

On the dispatch panel Team 1, Team 2 and Team 3 have permanent card locations even when hero portraits change. Before Dispatch, the runtime must still verify the chosen team's exact blue idle indicator, click that exact fixed card, then use the proven Dispatch path. A stale/incorrect map-side guess therefore cannot intentionally overwrite another busy team.

Each fixed card has its own live-captured idle template, including `Team2Idle.png`. Scenario validation and regression tests require all three templates to exist and decode with OpenCV so a later-team attempt cannot fail only after an earlier team was sent.

Legacy `march_count` and `replacement_order` remain compatibility-only.

## Development checks

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy macro_clicker tools
python -m tools.validate_scenarios
```

See `AGENTS.md` for mandatory AI commit and Markdown-sync rules.
