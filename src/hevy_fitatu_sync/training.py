"""Pure training analysis over Hevy workouts as the API sends them."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

SECONDARY_MUSCLE_WEIGHT = 0.5  # a set counts 1 for the primary muscle, this much for each secondary one


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def workout_day(raw: dict[str, Any], tz: ZoneInfo) -> date:
    return parse_time(raw["start_time"]).astimezone(tz).date()


def workout_minutes(raw: dict[str, Any]) -> int:
    seconds = (parse_time(raw["end_time"]) - parse_time(raw["start_time"])).total_seconds()
    return max(1, round(seconds / 60))


def working_sets(exercise: dict[str, Any]) -> list[dict[str, Any]]:
    """Every set but warm-ups."""
    return [s for s in exercise.get("sets") or [] if s.get("type") != "warmup"]


def e1rm(weight_kg: float | None, reps: int | None) -> float | None:
    """Estimated one-rep max (Epley), for sets of 1-15 reps with a weight."""
    if not weight_kg or not reps or reps < 1 or reps > 15:
        return None
    return weight_kg if reps == 1 else weight_kg * (1 + reps / 30)


def _r(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _set_text(s: dict[str, Any]) -> str:
    parts = []
    if s.get("weight_kg") is not None:
        parts.append(f"{s['weight_kg']:g}kg")
    if s.get("reps") is not None:
        parts.append(f"{s['reps']}")
    if s.get("distance_meters"):
        parts.append(f"{s['distance_meters']:g}m")
    if s.get("duration_seconds"):
        parts.append(f"{s['duration_seconds']}s")
    text = "×".join(parts) or "?"
    if s.get("rpe"):
        text += f"@{s['rpe']:g}"
    if s.get("type") and s["type"] not in ("normal", "warmup"):
        text += f" ({s['type']})"
    return text


def summarize_workout(
    raw: dict[str, Any],
    templates: dict[str, dict[str, Any]],
    tz: ZoneInfo,
    *,
    with_sets: bool = True,
) -> dict[str, Any]:
    """One workout: day, length, working sets, reps, volume, sets per muscle group and, per
    exercise, its working sets as short text ("80kg×5@8") and best e1RM."""
    muscles: dict[str, float] = defaultdict(float)
    exercises = []
    total_sets = total_reps = 0
    volume = 0.0
    for ex in raw.get("exercises") or []:
        sets = working_sets(ex)
        template = templates.get(str(ex.get("exercise_template_id"))) or {}
        primary = template.get("primary_muscle_group")
        if primary:
            muscles[primary] += len(sets)
        for secondary in template.get("secondary_muscle_groups") or []:
            muscles[secondary] += len(sets) * SECONDARY_MUSCLE_WEIGHT
        ex_volume = sum((s.get("weight_kg") or 0) * (s.get("reps") or 0) for s in sets)
        best = max((v for s in sets if (v := e1rm(s.get("weight_kg"), s.get("reps"))) is not None), default=None)
        total_sets += len(sets)
        total_reps += sum(s.get("reps") or 0 for s in sets)
        volume += ex_volume
        entry: dict[str, Any] = {
            "title": ex.get("title"),
            "exercise_template_id": ex.get("exercise_template_id"),
            "muscle": primary,
            "working_sets": len(sets),
            "volume_kg": round(ex_volume),
            "best_e1rm_kg": _r(best),
        }
        if with_sets:
            entry["sets"] = ", ".join(_set_text(s) for s in sets)
        exercises.append(entry)
    return {
        "workout_id": raw.get("id"),
        "date": workout_day(raw, tz).isoformat(),
        "title": raw.get("title"),
        "minutes": workout_minutes(raw),
        "working_sets": total_sets,
        "reps": total_reps,
        "volume_kg": round(volume),
        "muscle_sets": {k: _r(v) for k, v in sorted(muscles.items(), key=lambda kv: -kv[1])},
        "exercises": exercises,
    }


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def training_summary(
    raws: list[dict[str, Any]],
    templates: dict[str, dict[str, Any]],
    tz: ZoneInfo,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Totals, per-week figures and sets per muscle group for the workouts between ``start`` and
    ``end`` (both included)."""
    workouts = sorted(
        (summarize_workout(r, templates, tz, with_sets=False) for r in raws),
        key=lambda w: w["date"],
    )
    workouts = [w for w in workouts if start.isoformat() <= w["date"] <= end.isoformat()]
    weeks_count = max(1.0, ((end - start).days + 1) / 7)
    muscles: dict[str, float] = defaultdict(float)
    by_exercise: dict[str, dict[str, Any]] = {}
    weekly: dict[str, dict[str, Any]] = {}
    for w in workouts:
        for name, sets in w["muscle_sets"].items():
            muscles[name] += sets
        wk = weekly.setdefault(
            week_start(date.fromisoformat(w["date"])).isoformat(),
            {"workouts": 0, "minutes": 0, "working_sets": 0, "volume_kg": 0},
        )
        wk["workouts"] += 1
        wk["minutes"] += w["minutes"]
        wk["working_sets"] += w["working_sets"]
        wk["volume_kg"] += w["volume_kg"]
        for ex in w["exercises"]:
            agg = by_exercise.setdefault(
                ex["title"], {"title": ex["title"], "sessions": 0, "working_sets": 0, "volume_kg": 0, "last": None}
            )
            agg["sessions"] += 1
            agg["working_sets"] += ex["working_sets"]
            agg["volume_kg"] += ex["volume_kg"]
            agg["last"] = w["date"]
    days = sorted({w["date"] for w in workouts})
    gaps = [(date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in pairwise(days)]
    return {
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "workouts": len(workouts),
        "training_days": len(days),
        "per_week": {
            "workouts": _r(len(workouts) / weeks_count),
            "minutes": _r(sum(w["minutes"] for w in workouts) / weeks_count, 0),
            "working_sets": _r(sum(w["working_sets"] for w in workouts) / weeks_count),
            "volume_kg": _r(sum(w["volume_kg"] for w in workouts) / weeks_count, 0),
        },
        "muscle_sets_per_week": {k: _r(v / weeks_count) for k, v in sorted(muscles.items(), key=lambda kv: -kv[1])},
        "longest_gap_days": max(gaps, default=None),
        "last_workout": days[-1] if days else None,
        "weeks": [{"week_of": k, **v} for k, v in sorted(weekly.items())],
        "top_exercises": sorted(by_exercise.values(), key=lambda e: -e["working_sets"])[:15],
        "workout_list": [
            {k: w[k] for k in ("date", "title", "minutes", "working_sets", "volume_kg")} for w in workouts
        ],
    }


def _norm(text: str) -> str:
    return " ".join(text.casefold().replace("(", " ").replace(")", " ").split())


def match_exercise(query: str, titles: list[str]) -> list[str]:
    """Titles matching ``query``: an exact title first, else every title containing all its words."""
    q = _norm(query)
    exact = [t for t in titles if _norm(t) == q]
    if exact:
        return exact
    words = q.split()
    return sorted(t for t in titles if all(word in _norm(t) for word in words))


def exercise_progress(raws: list[dict[str, Any]], query: str, tz: ZoneInfo) -> dict[str, Any]:
    """Per session of the exercise matching ``query``: top weight, best set, e1RM and volume, with
    PR flags and the change over the period."""
    titles = sorted({str(ex.get("title")) for r in raws for ex in r.get("exercises") or []})
    matches = match_exercise(query, titles)
    if not matches:
        return {"query": query, "matches": [], "sessions": [], "note": "no exercise with that name in the period"}
    title = matches[0]
    sessions = []
    exact: list[float | None] = []  # each session's unrounded e1RM, for the trend
    best_so_far = 0.0
    for raw in sorted(raws, key=lambda r: r["start_time"]):
        for ex in raw.get("exercises") or []:
            if ex.get("title") != title:
                continue
            sets = working_sets(ex)
            scored = [(e1rm(s.get("weight_kg"), s.get("reps")), s) for s in sets]
            scored = [(v, s) for v, s in scored if v is not None]
            best_e1rm, best_set = max(scored, key=lambda vs: vs[0], default=(None, None))
            pr = best_e1rm is not None and best_e1rm > best_so_far
            if pr:
                best_so_far = best_e1rm  # type: ignore[assignment]
            exact.append(best_e1rm)
            sessions.append({
                "date": workout_day(raw, tz).isoformat(),
                "working_sets": len(sets),
                "top_weight_kg": max((s.get("weight_kg") or 0 for s in sets), default=0) or None,
                "best_set": _set_text(best_set) if best_set else None,
                "e1rm_kg": _r(best_e1rm),
                "volume_kg": round(sum((s.get("weight_kg") or 0) * (s.get("reps") or 0) for s in sets)),
                "pr": pr and len(sessions) > 0,
            })
    rated = [(s, v) for s, v in zip(sessions, exact, strict=True) if v is not None]
    trend = None
    if len(rated) >= 2:
        (first, first_v), (last, last_v) = rated[0], rated[-1]
        days = (date.fromisoformat(last["date"]) - date.fromisoformat(first["date"])).days
        change = last_v - first_v
        trend = {
            "first_e1rm_kg": first["e1rm_kg"],
            "last_e1rm_kg": last["e1rm_kg"],
            "best_e1rm_kg": _r(max(v for _, v in rated)),
            "change_kg": _r(change),
            "change_pct": _r(100 * change / first_v),
            "per_4_weeks_kg": _r(change / days * 28) if days else None,
        }
    return {"query": query, "exercise": title, "other_matches": matches[1:10], "sessions": sessions, "trend": trend}
