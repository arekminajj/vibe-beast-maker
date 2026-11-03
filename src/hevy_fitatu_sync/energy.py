"""Turning Fitatu's activity catalog estimate into kcal for a workout of a given length."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

STRENGTH = 1  # Fitatu ActivityType: 0 import, 1 strength, 2 cardio, 3 stretching
_HMS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})")
_MIN = re.compile(r"(\d+(?:[.,]\d+)?)\s*min")
_HOUR = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:h|godz)")


@dataclass(frozen=True)
class ActivityRate:
    """A Fitatu catalog activity and how many kcal it burns per minute."""

    activity_id: int
    name: str
    kcal_per_minute: float
    source: str  # what the rate was derived from, for the report

    def kcal(self, minutes: int) -> int:
        return max(1, round(self.kcal_per_minute * minutes))


def reference_minutes(hit: dict[str, Any]) -> float | None:
    """The duration the catalog's ``energy`` is for, e.g. 30 for ``'5 km w 00:30:00'`` or ``'30 min'``."""
    description = str(hit.get("measuresDescription") or "")
    if match := _HMS.search(description):
        h, m, s = (int(x) for x in match.groups())
        return h * 60 + m + s / 60 or None
    if match := _MIN.search(description):
        return float(match.group(1).replace(",", ".")) or None
    if match := _HOUR.search(description):
        return float(match.group(1).replace(",", ".")) * 60 or None
    for measure in hit.get("measures") or []:  # the first time measure, in minutes
        if measure.get("measureType") == "TIME" and measure.get("value"):
            return float(measure["value"])
    return None


def rate_from_hit(hit: dict[str, Any]) -> ActivityRate | None:
    minutes, energy = reference_minutes(hit), hit.get("energy")
    if not minutes or not energy:
        return None
    return ActivityRate(
        activity_id=int(hit["activityId"]),
        name=str(hit.get("name")),
        kcal_per_minute=float(energy) / minutes,
        source=f"Fitatu: {energy} kcal for {hit.get('measuresDescription') or f'{minutes:g} min'}",
    )


def pick_hit(
    hits: list[dict[str, Any]], activity_id: int | None = None, phrase: str | None = None
) -> dict[str, Any] | None:
    """The requested activity, else the one named ``phrase``, else the first strength activity,
    else the first hit.

    Fitatu's search is fuzzy: "trójbój siłowy" also finds "Tenis stołowy" and dozens of
    strength exercises counted in reps, so an exact name match wins over the hit order.
    """
    if activity_id is not None:
        return next((h for h in hits if int(h.get("activityId", -1)) == activity_id), None)
    if phrase:
        wanted = phrase.strip().casefold()
        exact = next((h for h in hits if str(h.get("name", "")).strip().casefold() == wanted), None)
        if exact is not None:
            return exact
    return next((h for h in hits if h.get("type") == STRENGTH), hits[0] if hits else None)
