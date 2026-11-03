# Hevy + Fitatu for AI agents

Gives Claude, or any other MCP agent, your strength training from [Hevy](https://hevy.com)
and your food diary from [Fitatu](https://www.fitatu.com) side by side. Then you can ask
questions like these:

- "How did the last 4 weeks go? Do I eat enough protein for how much I train?"
- "Do I eat more on training days than on rest days?"
- "My bench has stalled since September. Did my eating or weight change then?"
- "What is my maintenance intake, judging by my weight trend?"
- "Which muscle groups am I under-training?"

It is **read-only**: the agent cannot change anything in Hevy or Fitatu.

The same package also has [`hevy-fitatu-sync`](#sync-workouts-into-fitatu), which logs your
Hevy workouts in Fitatu as activities.

> **Fitatu has no public API.** This uses [fitatu-api](https://github.com/Jezue/fitatu_library),
> an unofficial client reverse-engineered from the Fitatu apps. It can break when Fitatu changes
> its app, and automated access may break Fitatu's Terms of Service. Use it with your own
> account and at your own risk.

## Requirements

- A **Hevy Pro** API key: in the Hevy app, *Settings → Developer*, or <https://hevy.com/settings?developer>.
- A Fitatu account (Premium is not needed).
- [uv](https://docs.astral.sh/uv/) for the Claude Code plugin, or Python 3.11+ and pip.

## Add it to Claude Code (plugin)

```text
/plugin marketplace add arekminajj/vibe-beast-maker
/plugin install hevy-fitatu@vibe-beast-maker
```

Claude Code then asks for your Hevy API key, your Fitatu e-mail and password, and your time
zone. The key and the password are stored as secrets. The plugin also adds a skill that tells
Claude how to read the numbers.

## Add it to Claude Desktop, Cursor or another MCP client

```bash
pip install "hevy-fitatu-sync[mcp] @ git+https://github.com/arekminajj/vibe-beast-maker"
fitatu-api login      # once: saves the Fitatu session to ~/.fitatu/session.json
```

```json
{
  "mcpServers": {
    "hevy-fitatu": {
      "command": "hevy-fitatu-mcp",
      "env": {
        "HEVY_API_KEY": "your-hevy-key",
        "HEVY_FITATU_TIMEZONE": "Europe/Warsaw"
      }
    }
  }
}
```

If the client cannot find `hevy-fitatu-mcp`, use its full path (`which hevy-fitatu-mcp`).
With [uv](https://docs.astral.sh/uv/) you can skip the `pip install`: set `"command": "uvx"` and
`"args": ["--from", "hevy-fitatu-sync[mcp] @ git+https://github.com/arekminajj/vibe-beast-maker", "hevy-fitatu-mcp"]`.

Instead of `fitatu-api login` you can set `FITATU_EMAIL` and `FITATU_PASSWORD`. The first
call logs in and saves the session file. Accounts made with Apple or Facebook have no
password: use `fitatu-api import-session` (see the [fitatu-api README](https://github.com/Jezue/fitatu_library#authentication--session-file)),
or put the session JSON in `FITATU_SESSION_JSON`.

## Tools

### Training and food together

| Tool | What it answers |
| --- | --- |
| `analyze_training_and_nutrition` | Start here. Training per week and sets per muscle group. Average kcal, protein, carbs and fat on **training days vs rest days**. Protein **g/kg**. The **weight trend** (kg/week). With 14+ logged days and weigh-ins, an **estimated maintenance intake** and daily surplus or deficit. `notes` says what the data does not cover. |
| `get_daily_log` | Day by day: workouts (minutes, sets, volume, muscles), what was logged in Fitatu against the goals, and the weight (from Fitatu, else Hevy). |

### Training (Hevy)

| Tool | What it answers |
| --- | --- |
| `analyze_training` | Weekly workouts, minutes, working sets and volume. Sets per muscle group per week. The longest gap. The most trained exercises. |
| `analyze_exercise_progress` | One lift by name (`"bench"`): per session the top weight, best set, e1RM and volume, with PRs flagged and the trend. |
| `get_workouts` | Every workout with its working sets as text (`80kg×5@8`). |

### Food (Fitatu)

The read tools of [fitatu-api](https://github.com/Jezue/fitatu_library), as they are:
`get_today`, `get_day`, `get_progress`, `get_report`, `get_range_summary`, `get_top_foods`,
`get_targets`, `get_profile`, `get_weight_history`, `get_body_measurements`,
`get_measurement_history`, `get_water`, `search_food`, `get_food_measures`, `search_recipes`,
`get_recipe`. To log meals from an agent, add fitatu-api's own server, `fitatu-mcp`, next to this one.

### Prompts

- `training_nutrition_review`: a review of the last N weeks with 3 recommendations.
- `lift_progress`: how one lift progressed, and whether eating explains a stall.

### How the numbers are calculated

- **Days** follow your time zone (`HEVY_FITATU_TIMEZONE`, else `HEVY_TIMEZONE`, else the system's).
- **Working sets** are all sets except warm-ups. **Volume** is weight × reps. A set counts 1
  for the exercise's primary muscle and 0.5 for each secondary one.
- **e1RM** uses Epley, `w × (1 + reps/30)`, for sets of 1–15 reps.
- **Food** is everything logged in the Fitatu diary for a day, planned or ticked as eaten. A day
  with nothing logged is left out of the averages, not counted as zero.
- **Maintenance** is the average intake minus the change in stored energy: the weight trend
  (least squares over the weigh-ins) × 7700 kcal/kg. It is only given with food logged on at
  least 14 days and 70% of the range, and weigh-ins spanning 14+ days.
- Without Fitatu Premium, Fitatu answers day by day, so a long range takes a while (31 days per call).

## Sync workouts into Fitatu

`hevy-fitatu-sync` logs each Hevy workout in the Fitatu diary as an activity (by default
*Trójbój siłowy* at 300 kcal/h) on the workout's day, with its length and an estimate of the
kcal burned, so the kcal you burn lifting count toward the day's balance. Running the sync again
is safe: workouts already in Fitatu are left alone, edited workouts are replaced and deleted
workouts are removed. It is the only part of this package that writes to Fitatu.

```bash
pip install "git+https://github.com/arekminajj/vibe-beast-maker"
export HEVY_API_KEY=your-hevy-key
fitatu-api login
```

### Use

```bash
hevy-fitatu-sync sync --dry-run       # what would change in the last 7 days; writes nothing
hevy-fitatu-sync sync                 # do it
hevy-fitatu-sync sync --since 2025-01-01 --dry-run   # a backfill: check first, then run without --dry-run
hevy-fitatu-sync status               # what has been synced
hevy-fitatu-sync activities "trening" # the Fitatu activities you can log workouts as
```

```text
$ hevy-fitatu-sync sync
Fitatu activity: Trójbój siłowy (id 1191), 300 kcal/h [--kcal-per-hour 300]
Workouts since 2026-10-28:
  ✓ add 2026-10-29 'Push' 72 min, 360 kcal
  ✓ replace 2026-10-30 60 min/300 kcal -> 2026-10-31 'Legs' 85 min, 425 kcal
  ✓ remove 2026-11-01 'Pull' (deleted in Hevy)
```

`sync` options:

| Option | Default | |
| --- | --- | --- |
| `--days N` / `--since YYYY-MM-DD` | `--days 7` | Which workouts to sync, by the day they started. Older synced workouts are never touched. |
| `--dry-run` | off | Show the plan, write nothing. |
| `--activity PHRASE` | `Trójbój siłowy` | The Fitatu activity to log, found by searching the catalog (an exact name match wins, else the first *strength* hit). |
| `--activity-id ID` | | A specific activity from `hevy-fitatu-sync activities`. |
| `--kcal-per-hour N` | `300` | The burn rate the kcal are computed from. |
| `--fitatu-estimate` | off | Use Fitatu's catalog estimate for the activity instead (516 kcal/h for *Trójbój siłowy*, scaled to your profile). |
| `--min-minutes N` | `1` | Skip shorter workouts. |
| `--timezone ZONE` | `$HEVY_FITATU_TIMEZONE`, `$HEVY_TIMEZONE`, system | Decides which day a late-evening workout falls on. |
| `--state PATH` | `$HEVY_FITATU_STATE`, `~/.hevy-fitatu-sync/state.json` | What has been synced. |
| `--json` | off | The report as JSON. |

Exit codes: `0` everything synced, `1` some workouts failed (the others were synced), `2` the
sync could not start, e.g. a wrong key or no Fitatu session.

### Run it automatically

Every hour with cron:

```cron
0 * * * * HEVY_API_KEY=... HEVY_FITATU_TIMEZONE=Europe/Warsaw /path/to/venv/bin/hevy-fitatu-sync sync >> ~/.hevy-fitatu-sync/sync.log 2>&1
```

### How the sync works

- **The activity.** Fitatu's catalog has no time-based *strength training*: its strength
  entries are single exercises counted in reps. *Trójbój siłowy* (powerlifting) is the
  closest activity measured in minutes, so that is what gets logged.
- **The kcal.** Hevy does not estimate calories, so the workout's length (from its start to its
  end time in Hevy) is multiplied by 300 kcal/h, a moderate rate for a gym session with rests
  between sets. Change it with `--kcal-per-hour`, or pass `--fitatu-estimate` to use Fitatu's
  own figure for the activity (its catalog kcal for an effort such as *30 min*, computed for
  your BMR and sex and scaled to the workout's length; for powerlifting that is about
  516 kcal/h, which overcounts most sessions).
- **No duplicates.** Each activity gets an id derived from the Hevy workout and its day,
  length and activity, so a repeated run, or a run after the state file was lost, finds the
  activity already in Fitatu instead of adding it again.
- **Edits and deletions.** A workout whose day or length changed in Hevy is removed and
  logged again. A workout deleted in Hevy (seen in Hevy's workout events, or missing from the
  synced period) is removed from Fitatu. An activity you already removed by hand in Fitatu is
  fine.
- **Once logged, an activity keeps its kcal.** If your BMR changes later, already synced
  workouts are not recalculated.

## Develop

```bash
git clone https://github.com/arekminajj/vibe-beast-maker && cd vibe-beast-maker
pip install -e ".[dev]"
pytest
claude plugin validate .        # the Claude Code plugin and marketplace manifests
```

The tests run offline. Hevy is faked. Fitatu is either a fake or the in-memory demo backend
that ships with fitatu-api. The MCP tests drive the server through an MCP client.

Layout:

- `src/hevy_fitatu_sync/mcp_server.py`: the MCP server (`hevy-fitatu-mcp`)
- `src/hevy_fitatu_sync/combined.py`: the day-by-day join, and the training vs nutrition analysis
- `src/hevy_fitatu_sync/training.py`: training analysis (sets, volume, muscles, e1RM)
- `src/hevy_fitatu_sync/hevy.py`: the Hevy API client
- `src/hevy_fitatu_sync/cli.py`, `sync.py`, `energy.py`, `state.py`: the sync
- `.claude-plugin/`, `.mcp.json`, `skills/`: the Claude Code plugin

## License

MIT
