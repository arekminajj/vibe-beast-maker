# hevy-fitatu-sync

Logs your [Hevy](https://hevy.com) workouts in the [Fitatu](https://www.fitatu.com) diary as
activities, so the kcal you burn lifting count toward the day's balance in Fitatu.

Each Hevy workout becomes one Fitatu activity (by default *Trójbój siłowy* at 300 kcal/h) on
the workout's day, with its length and an estimate of the kcal burned. Running the sync again is safe:
workouts already in Fitatu are left alone, edited workouts are replaced and deleted workouts
are removed.

> **Fitatu has no public API.** This uses [fitatu-api](https://github.com/Jezue/fitatu_library),
> an unofficial client reverse-engineered from the Fitatu apps. It can break when Fitatu changes
> its app, and automated access may break Fitatu's Terms of Service. Use it with your own
> account and at your own risk.

## Requirements

- Python 3.11+
- A **Hevy Pro** API key: in the Hevy app, *Settings → Developer*, or <https://hevy.com/settings?developer>.
- A Fitatu account (Premium is not needed).

## Install

```bash
pip install "git+https://github.com/arekminajj/vibe-beast-maker"
```

## Log in

```bash
export HEVY_API_KEY=your-hevy-key
fitatu-api login                 # asks for the e-mail and password, saves ~/.fitatu/session.json
```

Instead of `fitatu-api login` you can set `FITATU_EMAIL` and `FITATU_PASSWORD`: the first run
logs in and saves the session file. Accounts made with Apple or Facebook have no password: use
`fitatu-api import-session` (see the [fitatu-api README](https://github.com/Jezue/fitatu_library#authentication--session-file)),
or put the session JSON in `FITATU_SESSION_JSON`.

## Use

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

## How it works

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
```

The tests run offline: Hevy is faked, and Fitatu is the in-memory demo backend that ships with
fitatu-api.

## License

MIT
