"""Plan and apply the sync: each Hevy workout becomes one Fitatu activity on the workout's day."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fitatu_api import FitatuApiClient
from fitatu_api.exceptions import FitatuApiError

from .energy import ActivityRate, pick_hit, rate_from_hit
from .hevy import HevyClient, Workout
from .state import State, SyncedWorkout

# Fitatu's catalog has no time-based "strength training"; its strength entries are single
# exercises counted in reps. Powerlifting is the closest activity measured in minutes.
DEFAULT_ACTIVITY_PHRASE = "Trójbój siłowy"
# Fitatu rates powerlifting at ~516 kcal/h, which overcounts a gym session with rests between
# sets; 300 kcal/h is a moderate estimate for strength training.
DEFAULT_KCAL_PER_HOUR = 300.0
_NAMESPACE = uuid.UUID("6f1d3c2a-8b7e-4c55-9a0e-2f4b1d7c9e31")

ActionKind = Literal["add", "replace", "remove", "keep"]


class SyncError(RuntimeError):
    pass


def row_id_for(workout_id: str, day: date, minutes: int, activity_id: int) -> str:
    """A stable Fitatu activity uuid for one version of a workout.

    The same workout with the same day, length and activity always gets the same id, so a run
    that repeats (or a lost state file) re-sends an add the server already has instead of
    logging the workout twice. Fitatu's day sync accepts only version-1 UUIDs.
    """
    digest = uuid.uuid5(_NAMESPACE, f"{workout_id}|{day.isoformat()}|{minutes}|{activity_id}")
    return str(uuid.UUID(int=digest.int, version=1))


@dataclass
class Config:
    since: date
    timezone: ZoneInfo
    activity_phrase: str = DEFAULT_ACTIVITY_PHRASE
    activity_id: int | None = None
    kcal_per_hour: float | None = DEFAULT_KCAL_PER_HOUR  # None: use Fitatu's catalog estimate
    min_minutes: int = 1


@dataclass
class Action:
    kind: ActionKind
    workout_id: str
    title: str
    day: str
    minutes: int
    energy: int
    row_id: str
    old: SyncedWorkout | None = None
    reason: str = ""
    status: str = "planned"  # planned | done | already_there | error
    error: str | None = None

    def describe(self) -> str:
        what = f"{self.day} {self.title!r} {self.minutes} min, {self.energy} kcal"
        if self.kind == "remove":
            return f"remove {self.day} {self.title!r} ({self.reason})"
        if self.kind == "replace" and self.old:
            return f"replace {self.old.day} {self.old.minutes} min/{self.old.energy} kcal -> {what}"
        return f"{self.kind} {what}"


@dataclass
class Report:
    rate: ActivityRate
    since: str
    actions: list[Action] = field(default_factory=list)
    dry_run: bool = True

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for action in self.actions:
            out[action.kind] = out.get(action.kind, 0) + 1
        out["errors"] = sum(a.status == "error" for a in self.actions)
        return out

    @property
    def ok(self) -> bool:
        return not any(a.status == "error" for a in self.actions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "since": self.since,
            "activity": {
                "activity_id": self.rate.activity_id,
                "name": self.rate.name,
                "kcal_per_hour": round(self.rate.kcal_per_minute * 60),
                "source": self.rate.source,
            },
            "counts": self.counts(),
            "actions": [
                {
                    "kind": a.kind,
                    "status": a.status,
                    "workout_id": a.workout_id,
                    "title": a.title,
                    "day": a.day,
                    "minutes": a.minutes,
                    "energy_kcal": a.energy,
                    "row_id": a.row_id,
                    **({"reason": a.reason} if a.reason else {}),
                    **({"error": a.error} if a.error else {}),
                }
                for a in self.actions
            ],
        }


def search_activities(fitatu: FitatuApiClient, phrase: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search the Fitatu activity catalog, with kcal computed for the account's BMR and sex."""
    user_id = str(fitatu.auth.fitatu_user_id)
    try:
        settings = fitatu.user_settings.get_new(user_id)
    except FitatuApiError:
        settings = {}
    bmr = (settings.get("calculatedValues") or {}).get("BMR")
    sex = (settings.get("userSettings") or {}).get("sex")
    return fitatu.activities.search(
        phrase, limit=limit, bmr=float(bmr) if bmr else None, sex=sex if isinstance(sex, int) else None
    )


class Syncer:
    def __init__(
        self,
        hevy: HevyClient,
        fitatu: FitatuApiClient,
        state: State,
        config: Config,
        save_state: Callable[[State], None] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.hevy = hevy
        self.fitatu = fitatu
        self.state = state
        self.config = config
        self._save = save_state or (lambda _state: None)
        self._now = now or (lambda: datetime.now(UTC))
        self.user_id = str(fitatu.auth.fitatu_user_id)

    # -- the activity and its kcal rate -----------------------------------------------
    def search_activities(self, phrase: str, limit: int = 10) -> list[dict[str, Any]]:
        return search_activities(self.fitatu, phrase, limit)

    def resolve_rate(self) -> ActivityRate:
        cfg = self.config
        hits = self.search_activities(cfg.activity_phrase, limit=20)
        hit = pick_hit(hits, cfg.activity_id, cfg.activity_phrase)
        if cfg.kcal_per_hour is not None:
            if hit is None and cfg.activity_id is None:
                raise SyncError(f"no Fitatu activity matches {cfg.activity_phrase!r}; pass --activity or --activity-id")
            activity_id = cfg.activity_id if cfg.activity_id is not None else int(hit["activityId"])  # type: ignore[index]
            name = str(hit.get("name")) if hit else f"activity {activity_id}"
            return ActivityRate(activity_id, name, cfg.kcal_per_hour / 60, f"--kcal-per-hour {cfg.kcal_per_hour:g}")
        if hit is None:
            wanted = f"activity id {cfg.activity_id}" if cfg.activity_id is not None else "anything"
            names = ", ".join(f"{h.get('name')} ({h.get('activityId')})" for h in hits[:8]) or "no hits"
            raise SyncError(
                f"searching Fitatu for {cfg.activity_phrase!r} did not find {wanted} ({names}); "
                "try `hevy-fitatu-sync activities <phrase>` and pass --activity / --activity-id"
            )
        rate = rate_from_hit(hit)
        if rate is None:
            raise SyncError(
                f"Fitatu gives no usable kcal estimate for {hit.get('name')!r} "
                f"({hit.get('energy')} kcal for {hit.get('measuresDescription')!r}); pass --kcal-per-hour or "
                "choose an activity measured in minutes"
            )
        return rate

    # -- planning ---------------------------------------------------------------------
    def plan(self, rate: ActivityRate) -> list[Action]:
        cfg = self.config
        since_dt = datetime.combine(cfg.since, time.min, cfg.timezone)
        workouts = sorted(self.hevy.workouts_since(since_dt), key=lambda w: w.start)
        actions: list[Action] = []
        seen: set[str] = set()
        for workout in workouts:
            seen.add(workout.id)
            if workout.minutes < cfg.min_minutes:
                continue
            actions.append(self._plan_workout(workout, rate))

        deleted: set[str] = set()
        if self.state.workouts:
            events_since = since_dt
            if self.state.last_run:
                events_since = min(events_since, datetime.fromisoformat(self.state.last_run))
            deleted = self.hevy.deleted_workout_ids(events_since)
        for wid, old in sorted(self.state.workouts.items(), key=lambda kv: kv[1].day):
            if wid in seen:
                continue
            if wid in deleted:
                reason = "deleted in Hevy"
            elif old.day >= cfg.since.isoformat():
                reason = "no longer in Hevy"
            else:
                continue  # outside the window: leave it alone
            actions.append(Action("remove", wid, old.title, old.day, old.minutes, old.energy, old.row_id, old, reason))
        return actions

    def _plan_workout(self, workout: Workout, rate: ActivityRate) -> Action:
        day = workout.start.astimezone(self.config.timezone).date()
        minutes = workout.minutes
        row_id = row_id_for(workout.id, day, minutes, rate.activity_id)
        old = self.state.workouts.get(workout.id)
        if old is not None and old.row_id == row_id:
            # Same workout as last time: keep what was logged, even if the kcal rate changed since.
            return Action("keep", workout.id, workout.title, old.day, old.minutes, old.energy, row_id, old)
        kind: ActionKind = "replace" if old is not None else "add"
        return Action(kind, workout.id, workout.title, day.isoformat(), minutes, rate.kcal(minutes), row_id, old)

    # -- applying ---------------------------------------------------------------------
    def run(self, dry_run: bool = True) -> Report:
        started = self._now()
        rate = self.resolve_rate()
        report = Report(rate=rate, since=self.config.since.isoformat(), actions=self.plan(rate), dry_run=dry_run)
        if dry_run:
            return report
        for action in report.actions:
            if action.kind == "keep":
                action.status = "done"
                continue
            try:
                self._apply(action, rate)
            except (FitatuApiError, SyncError) as exc:
                action.status, action.error = "error", str(exc)
            self._save(self.state)  # after every write, so a crash loses nothing
        if report.ok:
            self.state.last_run = started.isoformat()
            self._save(self.state)
        return report

    def _activity_on_day(self, day: str, row_id: str) -> dict[str, Any] | None:
        day_data = self.fitatu.planner.get_day(self.user_id, day)
        return next((a for a in day_data.get("activityPlan") or [] if str(a.get("uuid")) == row_id), None)

    def _apply(self, action: Action, rate: ActivityRate) -> None:
        if action.kind in ("remove", "replace") and action.old is not None:
            result = self.fitatu.planner.remove_activity(self.user_id, action.old.day, item_id=action.old.row_id)
            if not result.get("ok") and result.get("reason") != "item_not_found":  # gone already is fine
                raise SyncError(f"could not remove the activity: {result.get('message') or result}")
            self.state.workouts.pop(action.workout_id, None)
            if action.kind == "remove":
                action.status = "done"
                return
        existing = self._activity_on_day(action.day, action.row_id)
        if existing is not None:
            # Logged by an earlier run whose state was lost: adopt it instead of adding it again.
            action.energy = int(existing.get("energy") or action.energy)
            action.status = "already_there"
        else:
            result = self.fitatu.planner.add_activity(
                self.user_id,
                action.day,
                activity_id=rate.activity_id,
                minutes=action.minutes,
                energy=action.energy,
                row_id=action.row_id,
            )
            if not result.get("ok"):
                raise SyncError("Fitatu accepted the activity but it is not on the day when re-read")
            action.status = "done"
        self.state.workouts[action.workout_id] = SyncedWorkout(
            day=action.day,
            row_id=action.row_id,
            activity_id=rate.activity_id,
            minutes=action.minutes,
            energy=action.energy,
            title=action.title,
            synced_at=self._now().isoformat(),
        )
