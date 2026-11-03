"""Log Hevy workouts in the Fitatu diary as activities (burned kcal)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fitatu_api import FitatuApiClient
from fitatu_api.auth import resolve_session_path
from fitatu_api.exceptions import FitatuApiError

from . import __version__
from .energy import rate_from_hit
from .hevy import HevyClient, HevyError
from .state import State, default_state_path
from .sync import DEFAULT_ACTIVITY_PHRASE, DEFAULT_KCAL_PER_HOUR, Config, Report, Syncer, SyncError, search_activities


class CliError(Exception):
    """A problem with the setup (keys, session, options): reported, exit code 2."""


def _timezone(name: str | None) -> ZoneInfo:
    name = name or os.environ.get("HEVY_FITATU_TIMEZONE") or os.environ.get("HEVY_TIMEZONE")
    if not name:
        local = datetime.now().astimezone().tzinfo
        name = getattr(local, "key", None) or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise CliError(f"unknown time zone {name!r}; use an IANA name such as Europe/Warsaw") from None


def fitatu_client() -> FitatuApiClient:
    """The Fitatu session: $FITATU_SESSION_JSON or the session file, else a login with
    $FITATU_EMAIL / $FITATU_PASSWORD (whose session is then saved to the session file)."""
    session_path = resolve_session_path()
    if os.environ.get("FITATU_SESSION_JSON") or session_path.exists():
        return FitatuApiClient.from_session()
    email, password = os.environ.get("FITATU_EMAIL"), os.environ.get("FITATU_PASSWORD")
    if email and password:
        session_path.parent.mkdir(parents=True, exist_ok=True)
        return FitatuApiClient.login(email, password, token_store_path=session_path)
    raise CliError(
        "No Fitatu session: run `fitatu-api login`, or set FITATU_EMAIL and FITATU_PASSWORD "
        "(or FITATU_SESSION_JSON)."
    )


def hevy_client() -> HevyClient:
    key = os.environ.get("HEVY_API_KEY")
    if not key:
        raise CliError("HEVY_API_KEY is not set (Hevy Pro: Settings → Developer).")
    return HevyClient(key, base_url=os.environ.get("HEVY_BASE_URL"))


def _since(args: argparse.Namespace, tz: ZoneInfo) -> date:
    if args.since:
        try:
            return date.fromisoformat(args.since)
        except ValueError:
            raise CliError(f"--since must be a date such as 2025-01-01, got {args.since!r}") from None
    if args.days < 1:
        raise CliError("--days must be at least 1")
    return datetime.now(tz).date() - timedelta(days=args.days - 1)


def print_report(report: Report, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), indent=1, ensure_ascii=False))
        return
    rate = report.rate
    print(f"Fitatu activity: {rate.name} (id {rate.activity_id}), {round(rate.kcal_per_minute * 60)} kcal/h [{rate.source}]")
    print(f"Workouts since {report.since}:")
    changes = [a for a in report.actions if a.kind != "keep"]
    for action in report.actions:
        if action.kind == "keep":
            continue
        mark = {"planned": "·", "done": "✓", "already_there": "=", "error": "✗"}[action.status]
        line = f"  {mark} {action.describe()}"
        if action.status == "already_there":
            line += " (already in Fitatu)"
        if action.error:
            line += f"\n      error: {action.error}"
        print(line)
    kept = len(report.actions) - len(changes)
    if not changes:
        print("  nothing to do" + (f" ({kept} already synced)" if kept else ""))
    elif kept:
        print(f"  ({kept} already synced)")
    if report.dry_run and changes:
        print("Dry run: nothing was written. Run again without --dry-run to apply.")


def cmd_sync(args: argparse.Namespace) -> int:
    tz = _timezone(args.timezone)
    state_path = Path(args.state).expanduser() if args.state else default_state_path()
    state = State.load(state_path)
    config = Config(
        since=_since(args, tz),
        timezone=tz,
        activity_phrase=args.activity,
        activity_id=args.activity_id,
        kcal_per_hour=args.kcal_per_hour,
        min_minutes=args.min_minutes,
    )
    with fitatu_client() as fitatu:
        syncer = Syncer(hevy_client(), fitatu, state, config, save_state=lambda s: s.save(state_path))
        report = syncer.run(dry_run=args.dry_run)
    print_report(report, args.json)
    return 0 if report.ok else 1


def cmd_activities(args: argparse.Namespace) -> int:
    with fitatu_client() as fitatu:
        hits = search_activities(fitatu, args.phrase, limit=args.limit)
    rows: list[dict[str, Any]] = []
    for hit in hits:
        rate = rate_from_hit(hit)
        rows.append({
            "activity_id": hit.get("activityId"),
            "name": hit.get("name"),
            "type": {0: "import", 1: "strength", 2: "cardio", 3: "stretching"}.get(hit.get("type"), hit.get("type")),
            "energy_kcal": hit.get("energy"),
            "energy_for": hit.get("measuresDescription"),
            "kcal_per_hour": round(rate.kcal_per_minute * 60) if rate else None,
        })
    if args.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    else:
        for r in rows:
            print(f"{r['activity_id']:>6}  {r['name']}  [{r['type']}]  {r['energy_kcal']} kcal for {r['energy_for']}"
                  f"  ≈ {r['kcal_per_hour']} kcal/h")
        if not rows:
            print("no activities found")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state_path = Path(args.state).expanduser() if args.state else default_state_path()
    state = State.load(state_path)
    print(f"State file: {state_path}{'' if state_path.exists() else ' (none yet)'}")
    print(f"Last run: {state.last_run or 'never'}")
    print(f"Synced workouts: {len(state.workouts)}")
    for wid, w in sorted(state.workouts.items(), key=lambda kv: kv[1].day)[-args.limit:]:
        print(f"  {w.day}  {w.title!r}  {w.minutes} min  {w.energy} kcal  (hevy {wid})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hevy-fitatu-sync", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="log Hevy workouts as Fitatu activities")
    window = sync.add_mutually_exclusive_group()
    window.add_argument("--days", type=int, default=7, help="sync the last N days, today included (default 7)")
    window.add_argument("--since", help="sync workouts from this day on (YYYY-MM-DD), e.g. for a backfill")
    sync.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    sync.add_argument("--activity", default=DEFAULT_ACTIVITY_PHRASE,
                      help=f"Fitatu activity to log, searched by name (default {DEFAULT_ACTIVITY_PHRASE!r})")
    sync.add_argument("--activity-id", type=int, help="a specific activity id from `activities`")
    burn = sync.add_mutually_exclusive_group()
    burn.add_argument("--kcal-per-hour", type=float, default=DEFAULT_KCAL_PER_HOUR,
                      help=f"burn rate used for the kcal (default {DEFAULT_KCAL_PER_HOUR:g})")
    burn.add_argument("--fitatu-estimate", dest="kcal_per_hour", action="store_const", const=None, default=DEFAULT_KCAL_PER_HOUR,
                      help="use Fitatu's catalog estimate for the activity instead of --kcal-per-hour")
    sync.add_argument("--min-minutes", type=int, default=1, help="skip workouts shorter than this")
    sync.add_argument("--timezone", help="IANA zone for the workout's day (default $HEVY_FITATU_TIMEZONE, "
                      "$HEVY_TIMEZONE, else the system zone)")
    sync.add_argument("--state", help="state file (default $HEVY_FITATU_STATE or ~/.hevy-fitatu-sync/state.json)")
    sync.add_argument("--json", action="store_true", help="print the report as JSON")
    sync.set_defaults(func=cmd_sync)

    acts = sub.add_parser("activities", help="search the Fitatu activity catalog")
    acts.add_argument("phrase", nargs="?", default=DEFAULT_ACTIVITY_PHRASE)
    acts.add_argument("--limit", type=int, default=10)
    acts.add_argument("--json", action="store_true")
    acts.set_defaults(func=cmd_activities)

    status = sub.add_parser("status", help="show what has been synced")
    status.add_argument("--state")
    status.add_argument("--limit", type=int, default=20)
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (CliError, HevyError, FitatuApiError, SyncError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        hint = getattr(exc, "hint", None)
        if hint:
            print(f"hint: {hint}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
