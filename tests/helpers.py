"""Fakes for the tests: a Hevy API in memory and the fitatu-api demo backend."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from fitatu_api._demo import DemoClient  # the library's in-memory Fitatu, used by its own tests too

from hevy_fitatu_sync.hevy import HevyClient

STRENGTH_HIT = {
    "activityId": 610,
    "name": "Trening siłowy",
    "type": 1,
    "intensity": 3,
    "energy": 300,
    "measuresDescription": "60 min",
    "measures": [{"measureId": 1, "measureType": "TIME", "value": 60}],
}
# What Fitatu's real search for "Trójbój siłowy" returns first: a fuzzy match, then the
# activity itself (cardio, measured in minutes), then strength exercises counted in reps.
REAL_CATALOG = [
    {"activityId": 1158, "name": "Tenis stołowy", "type": 2, "energy": 600, "measuresDescription": "75 min"},
    {"activityId": 1191, "name": "Trójbój siłowy", "type": 2, "energy": 258, "measuresDescription": "30 min"},
    {"activityId": 1638, "name": "Skłony tułowia w tył", "type": 1, "energy": 197, "measuresDescription": "100 reps"},
]
CARDIO_HIT = {"activityId": 535, "name": "Bieganie", "type": 2, "energy": 340, "measuresDescription": "5 km w 00:30:00"}


TEMPLATES = [
    {"id": "BENCH", "title": "Bench Press (Barbell)", "primary_muscle_group": "chest",
     "secondary_muscle_groups": ["triceps", "shoulders"]},
    {"id": "SQUAT", "title": "Squat (Barbell)", "primary_muscle_group": "quadriceps",
     "secondary_muscle_groups": ["glutes"]},
]


def sets(*pairs: tuple[float, int], warmup: tuple[float, int] | None = None) -> list[dict[str, Any]]:
    out = [{"index": 0, "type": "warmup", "weight_kg": warmup[0], "reps": warmup[1]}] if warmup else []
    return out + [{"index": i + len(out), "type": "normal", "weight_kg": w, "reps": r} for i, (w, r) in enumerate(pairs)]


def exercise(template_id: str, *pairs: tuple[float, int], warmup: tuple[float, int] | None = None) -> dict[str, Any]:
    title = next(t["title"] for t in TEMPLATES if t["id"] == template_id)
    return {"index": 0, "title": title, "exercise_template_id": template_id, "sets": sets(*pairs, warmup=warmup)}


def workout(
    wid: str, start: str, minutes: int, title: str = "Push", exercises: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    from datetime import datetime, timedelta

    begin = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end = begin + timedelta(minutes=minutes)
    return {"id": wid, "title": title, "start_time": begin.isoformat(), "end_time": end.isoformat(),
            "exercises": exercises or []}


class FakeFitatuTools:
    """The FitatuTools.call() envelope for get_progress and get_weight_history, from per-day data."""

    def __init__(self, food: dict[str, tuple[float, float]] | None = None, weights: dict[str, float] | None = None,
                 kcal_target: float = 2500, protein: tuple[float, float] = (120, 160)) -> None:
        self.food = food or {}  # day -> (kcal, protein g)
        self.weights = weights or {}
        self.kcal_target, self.protein = kcal_target, protein
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        from datetime import date, timedelta

        args = arguments or {}
        self.calls.append((name, args))
        start, end = date.fromisoformat(args.get("from_date", "2000-01-01")), date.fromisoformat(args.get("to_date", "2100-01-01"))
        if name == "get_progress":
            if (end - start).days + 1 > 31:
                return {"ok": False, "error": {"error_type": "validation", "message": "at most 31 days"}}
            days = []
            for i in range((end - start).days + 1):
                day = (start + timedelta(days=i)).isoformat()
                kcal, protein = self.food.get(day, (None, None))
                days.append({
                    "date": day,
                    "energy": {"logged": kcal, "target": self.kcal_target},
                    "protein": {"logged": protein, "target_min": self.protein[0], "target_max": self.protein[1]},
                    "fat": {"logged": kcal and kcal * 0.03}, "carbohydrate": {"logged": kcal and kcal * 0.12},
                })
            return {"ok": True, "data": {"days": days}}
        if name == "get_weight_history":
            rows = [{"date": d, "value": v} for d, v in sorted(self.weights.items(), reverse=True)
                    if start.isoformat() <= d <= end.isoformat()]
            if len(rows) > args.get("limit", 41) or args.get("limit", 0) > 41:
                return {"ok": False, "error": {"error_type": "validation", "message": "limit must be at most 41"}}
            return {"ok": True, "data": rows}
        if name == "get_today":
            return {"ok": True, "data": {"date": "2026-11-03"}}
        return {"ok": False, "error": {"error_type": "validation", "message": f"fake has no {name}"}}


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self) -> Any:
        return self._body


class FakeHevy:
    """Enough of the Hevy API for the sync: paged workouts (newest first) and the events feed."""

    def __init__(
        self,
        workouts: list[dict[str, Any]] | None = None,
        deleted: list[str] | None = None,
        templates: list[dict[str, Any]] | None = None,
        measurements: list[dict[str, Any]] | None = None,
    ) -> None:
        self.workouts = workouts or []
        self.deleted = deleted or []
        self.templates = templates if templates is not None else TEMPLATES
        self.measurements = measurements or []
        self.calls: list[str] = []

    def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, **_: Any):
        path = urlparse(url).path
        params = params or {}
        self.calls.append(path)
        if (headers or {}).get("api-key") != "test-key":
            return FakeResponse(401, "unauthorized")
        page, size = int(params.get("page", 1)), int(params.get("pageSize", 10))
        if path == "/v1/workouts":
            items = sorted(self.workouts, key=lambda w: w["start_time"], reverse=True)
            key = "workouts"
        elif path == "/v1/workouts/events":
            if not self.deleted:  # what the real API sends for an empty feed
                return FakeResponse(200, {"page": 1, "page_count": 1, "workouts": []})
            items = [{"type": "deleted", "id": wid, "deleted_at": "2026-10-30T00:00:00Z"} for wid in self.deleted]
            key = "events"
        elif path == "/v1/exercise_templates":
            items, key = self.templates, "exercise_templates"
        elif path == "/v1/body_measurements":
            items, key = self.measurements, "body_measurements"
        else:
            return FakeResponse(404, "not found")
        page_count = max(1, -(-len(items) // size))
        if page > page_count:
            return FakeResponse(404, "page not found")
        return FakeResponse(200, {"page": page, "page_count": page_count, key: items[(page - 1) * size : page * size]})

    def client(self) -> HevyClient:
        return HevyClient("test-key", session=self, max_retries=0)


def demo_fitatu(hits: list[dict[str, Any]] | None = None) -> DemoClient:
    """The demo Fitatu, with an activity catalog that has a strength activity."""
    client = DemoClient()
    catalog = hits if hits is not None else [CARDIO_HIT, STRENGTH_HIT]
    seen: dict[str, Any] = {}

    def search(phrase: str, *, limit: int = 20, bmr: float | None = None, sex: int | None = None, **_: Any):
        seen.update(phrase=phrase, bmr=bmr, sex=sex)
        return [dict(h) for h in catalog][:limit]

    client.activities.search = search  # type: ignore[method-assign]
    client.search_seen = seen  # type: ignore[attr-defined]
    return client


def activities_on(client: DemoClient, day: str) -> list[dict[str, Any]]:
    return client.planner.get_day(client.auth.fitatu_user_id, day).get("activityPlan") or []
