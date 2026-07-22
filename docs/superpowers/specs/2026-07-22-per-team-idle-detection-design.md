# Per-Team Rally Idle Detection

## Goal

Make low-level rally selection reliably prefer idle Stetmann (Team 3) before
Murphy (Team 1), while preserving immediate Murphy fallback when Stetmann is
genuinely unavailable.

## Root Cause

The selector currently uses one `TeamIdle.png` template captured from
Murphy's card for both teams. In the supplied frame where Murphy and Stetmann
are both idle, that template scores 1.00 on Murphy but only 0.7905 on
Stetmann. Because the threshold is 0.85, the engine rejects idle Stetmann and
selects Murphy.

## Per-Team Templates

Add separate idle template paths to `select_rally_team` actions:

- `team1_idle_template_path` for Murphy.
- `team3_idle_template_path` for Stetmann.

Create `templates/Team1Idle.png` and `templates/Team3Idle.png` from the supplied
two-idle dispatch frame. Keep the existing `team_idle_template_path` as a
backward-compatible fallback for older scenarios, but the two-team scenario
must configure both team-specific paths explicitly. Both continue using the
0.85 idle confidence threshold.

The editor must expose and preserve both team-specific paths. Model loading,
serialization, validation, and file-path checking must accept portable paths
and require an effective template for each configured rally team.

## Selection Flow

For mob levels 1 through 45:

1. Match Stetmann's idle template in Team 3's third-card region.
2. If it meets 0.85, select Team 3.
3. Otherwise match Murphy's idle template in Team 1's first-card region.
4. If Murphy meets 0.85, select Team 1 as the fallback.
5. If neither matches, wait and retry without clicking the blue dispatch
   button.

For mob levels 46 through 65, check only Murphy. Carlie remains excluded from
the candidate list in every case.

## Logging and Diagnostics

Every successful selection log must include every candidate score evaluated,
not only the selected team's score. A low-level fallback should therefore
state both Stetmann's rejected score and Murphy's accepted score.

Whenever a level 1 through 45 rally falls back from Team 3 to Team 1, save the
exact atomic dispatch-panel snapshot used for both decisions. Record the mob
level, threshold, candidate regions, candidate scores, selected team, and
decision `preferred_team_fallback`. This diagnostic is evidence of a
successful but unexpected fallback, not a reason to block the click.

## Verification

Automated tests must prove:

- The old shared Murphy template reproduces the 0.7905 Stetmann false negative.
- Murphy's specific template recognizes idle Murphy.
- Stetmann's specific template recognizes idle Stetmann at or above 0.85.
- At level 45 with both idle, Team 3 is selected.
- If Team 3 fails its own idle check, Team 1 remains the immediate fallback.
- The fallback log contains both scores.
- The fallback diagnostic receives the exact snapshot used for selection.
- Older actions using only `team_idle_template_path` continue to load and run.
- Scenario validation finds every configured template file.
- The complete pytest, Ruff, and mypy checks pass.
