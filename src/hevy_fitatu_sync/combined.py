"""Training (Hevy) and eating (Fitatu) side by side, day by day, and what follows from it.

The Fitatu side goes through anything with ``call(name, arguments) -> {"ok", "data" | "error"}``,
i.e. :class:`fitatu_api.agent.FitatuTools`, so tests can pass a fake.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from statistics import mean
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .hevy import HevyClient
from .training import summarize_workout, training_summary

KCAL_PER_KG = 7700  # energy in a kilogram of body weight change, the usual rule of thumb
FITATU_DAYS_PER_CALL = 31  # get_progress covers at most 31 days without Fitatu Premium
WEIGHT_DAYS_PER_CALL = 40  # get_weight_history lists at most 41 entries
MAX_RANGE_DAYS = 366


class ToolCaller(Protocol):
    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]: ...


class RangeError(ValueError):
    pass


def check_range(start: date, end: date, max_days: int = MAX_RANGE_DAYS) -> None:
    if end < start:
        raise RangeError(f"to_date {end} is before from_date {start}")
    if (end - start).days + 1 > max_days:
        raise RangeError(f"this tool covers at most {max_days} days, got {(end - start).days + 1}")


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _r(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


# -- Fitatu ------------------------------------------------------------------------------


def _unwrap(result: dict[str, Any], what: str) -> Any:
    if not result.get("ok"):
        error = result.get("error") or {}
        raise FitatuReadError(f"Fitatu {what}: {error.get('message') or error}", error)
    return result.get("data")


class FitatuReadError(RuntimeError):
    def __init__(self, message: str, error: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.error = error or {}


def nutrition_by_day(fitatu: ToolCaller, start: date, end: date) -> dict[str, dict[str, Any]]:
    """Per day: kcal, protein, fat and carbohydrate logged in the Fitatu diary (None when the
    day has nothing logged) and the day's goals."""
    out: dict[str, dict[str, Any]] = {}
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(end, chunk_start + timedelta(days=FITATU_DAYS_PER_CALL - 1))
        data = _unwrap(
            fitatu.call("get_progress", {"from_date": chunk_start.isoformat(), "to_date": chunk_end.isoformat()}),
            "get_progress",
        )
        for day in data.get("days") or []:
            energy, protein = day.get("energy") or {}, day.get("protein") or {}
            fat, carbs = day.get("fat") or {}, day.get("carbohydrate") or {}
            logged = energy.get("logged")
            out[day["date"]] = {
                "logged": bool(logged),
                "kcal": _r(logged, 0) if logged else None,
                "protein_g": _r(protein.get("logged")) if logged else None,
                "fat_g": _r(fat.get("logged")) if logged else None,
                "carbohydrate_g": _r(carbs.get("logged")) if logged else None,
                "kcal_target": _r(energy.get("target"), 0),
                "protein_target_g": [protein.get("target_min"), protein.get("target_max")],
            }
        chunk_start = chunk_end + timedelta(days=1)
    return out


def fitatu_weights(fitatu: ToolCaller, start: date, end: date) -> dict[str, float]:
    """Weigh-ins logged in Fitatu, read in windows of 40 days: a call lists at most 41."""
    out: dict[str, float] = {}
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(end, chunk_start + timedelta(days=WEIGHT_DAYS_PER_CALL - 1))
        data = _unwrap(
            fitatu.call("get_weight_history", {"from_date": chunk_start.isoformat(),
                                               "to_date": chunk_end.isoformat(), "limit": WEIGHT_DAYS_PER_CALL + 1}),
            "get_weight_history",
        )
        out.update({w["date"]: float(w["value"]) for w in data or [] if w.get("value") is not None})
        chunk_start = chunk_end + timedelta(days=1)
    return out


def hevy_weights(hevy: HevyClient, start: date, end: date) -> dict[str, float]:
    out = {}
    for m in hevy.body_measurements():
        day, kg = str(m.get("date") or "")[:10], m.get("weight_kg")
        if kg and start.isoformat() <= day <= end.isoformat():
            out[day] = float(kg)
    return out


# -- the join ----------------------------------------------------------------------------


def daily_log(
    hevy: HevyClient | None,
    fitatu: ToolCaller | None,
    start: date,
    end: date,
    tz: ZoneInfo,
) -> dict[str, Any]:
    """Every day in the range with its workouts, what was eaten and the weight, plus which side
    could not be read (``warnings``)."""
    check_range(start, end)
    warnings: list[str] = []
    workouts: dict[str, list[dict[str, Any]]] = {}
    weights: dict[str, dict[str, float]] = {}
    nutrition: dict[str, dict[str, Any]] = {}
    raws: list[dict[str, Any]] = []
    templates: dict[str, dict[str, Any]] = {}

    if hevy is None:
        warnings.append("Hevy is not connected (HEVY_API_KEY is not set): no workouts")
    else:
        try:
            raws = _raws_in(hevy, start, end, tz)
            templates = hevy.exercise_templates() if raws else {}
            for raw in raws:
                w = summarize_workout(raw, templates, tz, with_sets=False)
                workouts.setdefault(w["date"], []).append(w)
            for day, kg in hevy_weights(hevy, start, end).items():
                weights.setdefault(day, {})["hevy"] = kg
        except Exception as exc:  # noqa: BLE001 - report it, keep the other side
            warnings.append(f"Hevy could not be read: {exc}")

    if fitatu is None:
        warnings.append("Fitatu is not connected: no nutrition")
    else:
        try:
            nutrition = nutrition_by_day(fitatu, start, end)
            for day, kg in fitatu_weights(fitatu, start, end).items():
                weights.setdefault(day, {})["fitatu"] = kg
        except Exception as exc:  # noqa: BLE001 - report it, keep the other side
            warnings.append(f"Fitatu could not be read: {exc}")

    days = []
    for d in _days(start, end):
        key = d.isoformat()
        sessions = workouts.get(key, [])
        weight = weights.get(key, {})
        days.append({
            "date": key,
            "weekday": d.strftime("%a"),
            "trained": bool(sessions),
            "workouts": [
                {k: w[k] for k in ("title", "minutes", "working_sets", "volume_kg", "muscle_sets")} for w in sessions
            ],
            "nutrition": nutrition.get(key),
            # Fitatu's weigh-in wins when both apps have one: it is the diary's own.
            "weight_kg": weight.get("fitatu", weight.get("hevy")),
        })
    return {"from_date": start.isoformat(), "to_date": end.isoformat(), "days": days, "warnings": warnings,
            "_raws": raws, "_templates": templates}


def _raws_in(hevy: HevyClient, start: date, end: date, tz: ZoneInfo) -> list[dict[str, Any]]:
    since = datetime.combine(start, time.min, tz)
    until = datetime.combine(end + timedelta(days=1), time.min, tz)
    return hevy.raw_workouts_between(since, until)


def public_log(log: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in log.items() if not k.startswith("_")}


# -- the analysis ------------------------------------------------------------------------


def _avg(values: list[float]) -> float | None:
    return mean(values) if values else None


def _slope_per_day(points: list[tuple[date, float]]) -> float | None:
    """Least-squares slope of weight against time, in kg per day."""
    if len(points) < 2:
        return None
    xs = [(d - points[0][0]).days for d, _ in points]
    ys = [kg for _, kg in points]
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx


def _side(days: list[dict[str, Any]]) -> dict[str, Any]:
    eaten = [d["nutrition"] for d in days if d["nutrition"] and d["nutrition"]["logged"]]
    return {
        "days": len(days),
        "days_with_food_logged": len(eaten),
        "avg_kcal": _r(_avg([n["kcal"] for n in eaten]), 0),
        "avg_protein_g": _r(_avg([n["protein_g"] for n in eaten if n["protein_g"] is not None])),
        "avg_carbohydrate_g": _r(_avg([n["carbohydrate_g"] for n in eaten if n["carbohydrate_g"] is not None])),
        "avg_fat_g": _r(_avg([n["fat_g"] for n in eaten if n["fat_g"] is not None])),
    }


def analyze(log: dict[str, Any], tz: ZoneInfo, start: date, end: date) -> dict[str, Any]:
    """Training and nutrition together: intake on training vs rest days, protein per kg, energy
    balance against the weight trend (with an estimated maintenance intake) and the training load."""
    days = log["days"]
    notes: list[str] = list(log["warnings"])
    logged = [d for d in days if d["nutrition"] and d["nutrition"]["logged"]]
    weights = [(date.fromisoformat(d["date"]), d["weight_kg"]) for d in days if d["weight_kg"] is not None]

    avg_weight = _avg([kg for _, kg in weights])
    avg_protein = _avg([d["nutrition"]["protein_g"] for d in logged if d["nutrition"]["protein_g"] is not None])
    avg_kcal = _avg([d["nutrition"]["kcal"] for d in logged])
    targets = [d["nutrition"]["kcal_target"] for d in logged if d["nutrition"]["kcal_target"]]

    slope = _slope_per_day(weights)
    weight_span = (weights[-1][0] - weights[0][0]).days if len(weights) >= 2 else 0
    balance: dict[str, Any] = {
        "weigh_ins": len(weights),
        "first_weight_kg": weights[0][1] if weights else None,
        "last_weight_kg": weights[-1][1] if weights else None,
        "weight_trend_kg_per_week": _r(slope * 7, 2) if slope is not None else None,
        "avg_intake_kcal": _r(avg_kcal, 0),
        "avg_target_kcal": _r(_avg(targets), 0),
        "estimated_maintenance_kcal": None,
    }
    # Maintenance = intake - stored energy; only meaningful with enough logged days and weigh-ins.
    coverage = len(logged) / len(days) if days else 0
    if slope is not None and avg_kcal is not None and weight_span >= 14 and len(logged) >= 14 and coverage >= 0.7:
        balance["estimated_maintenance_kcal"] = _r(avg_kcal - slope * KCAL_PER_KG, 0)
        balance["estimated_daily_balance_kcal"] = _r(slope * KCAL_PER_KG, 0)
    else:
        notes.append(
            "No maintenance estimate: it needs at least 14 days with food logged (70% of the range) and "
            "weigh-ins spanning 14+ days"
        )
    if len(logged) < len(days):
        notes.append(
            f"Food is logged on {len(logged)} of {len(days)} days; averages cover the logged days only, "
            "and a day logged only in part lowers them"
        )

    trained = [d for d in days if d["trained"]]
    rest = [d for d in days if not d["trained"]]
    summary = training_summary(log["_raws"], log["_templates"], tz, start, end) if log["_raws"] else None

    return {
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "training": None if summary is None else {
            k: summary[k] for k in ("workouts", "training_days", "per_week", "muscle_sets_per_week",
                                    "longest_gap_days", "last_workout", "weeks")
        },
        "nutrition": {
            **_side(days),
            "protein_g_per_kg": _r(avg_protein / avg_weight, 2) if avg_protein and avg_weight else None,
            "days_protein_in_target": sum(
                1 for d in logged
                if d["nutrition"]["protein_g"] is not None and d["nutrition"]["protein_target_g"][0] is not None
                and d["nutrition"]["protein_g"] >= d["nutrition"]["protein_target_g"][0]
            ),
        },
        "training_days_vs_rest_days": {"training_days": _side(trained), "rest_days": _side(rest)},
        "energy_balance": balance,
        "notes": notes,
    }

