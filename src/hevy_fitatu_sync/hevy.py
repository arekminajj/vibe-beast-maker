"""A small read-only client for the Hevy public API (https://api.hevyapp.com/docs)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

DEFAULT_BASE_URL = "https://api.hevyapp.com"
PAGE_SIZE = 10  # the API's maximum for workouts and events
TEMPLATE_PAGE_SIZE = 100  # the API's maximum for exercise templates


class HevyError(RuntimeError):
    def __init__(self, status: int, path: str, body: str) -> None:
        hint = " Check HEVY_API_KEY (Hevy Pro: Settings → Developer)." if status in (401, 403) else ""
        super().__init__(f"Hevy API {status} on {path}: {body[:300]}{hint}")
        self.status = status


@dataclass(frozen=True)
class Workout:
    id: str
    title: str
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return max(1, round((self.end - self.start).total_seconds() / 60))

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Workout:
        return cls(
            id=str(data["id"]),
            title=str(data.get("title") or "Workout"),
            start=_parse_time(data["start_time"]),
            end=_parse_time(data["end_time"]),
        )


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Hevy time without a zone: {value!r}")
    return parsed


class HevyClient:
    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        session: Any | None = None,
        timeout: float = 30,
        max_retries: int = 3,
    ) -> None:
        if not api_key:
            raise ValueError("HEVY_API_KEY is not set")
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self._templates: dict[str, dict[str, Any]] | None = None

    def request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(self.max_retries + 1):
            res = self.session.get(
                self.base_url + path,
                params=params,
                headers={"api-key": self.api_key, "accept": "application/json"},
                timeout=self.timeout,
            )
            if res.status_code == 200:
                return res.json()
            if (res.status_code == 429 or res.status_code >= 500) and attempt < self.max_retries:
                time.sleep(min(10.0, 0.5 * 2**attempt))
                continue
            raise HevyError(res.status_code, path, res.text)
        raise AssertionError("unreachable")

    def _pages(
        self, path: str, key: str, params: dict[str, Any] | None = None, page_size: int = PAGE_SIZE
    ) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            try:
                data = self.request(path, {**(params or {}), "page": page, "pageSize": page_size})
            except HevyError as exc:
                if exc.status == 404 and page > 1:  # the API answers 404 past the last page
                    return
                raise
            # An empty events feed comes back as {"workouts": []} instead of {"events": []}.
            items = data.get(key) or []
            yield from items
            if not items or page >= int(data.get("page_count") or 0):
                return
            page += 1

    def workouts_since(self, since: datetime) -> list[Workout]:
        """Workouts that started at or after ``since``, newest first."""
        out: list[Workout] = []
        for raw in self._pages("/v1/workouts", "workouts"):
            workout = Workout.from_api(raw)
            if workout.start < since:
                break  # the list is newest first
            out.append(workout)
        return out

    def deleted_workout_ids(self, since: datetime) -> set[str]:
        """Ids of workouts deleted since ``since`` (from the workout events feed)."""
        params = {"since": since.isoformat()}
        return {str(e["id"]) for e in self._pages("/v1/workouts/events", "events", params) if e.get("type") == "deleted"}

    def raw_workouts_between(self, start: datetime, end: datetime | None = None) -> list[dict[str, Any]]:
        """Workouts as the API sends them (exercises and sets included) that started in
        [``start``, ``end``), newest first."""
        out: list[dict[str, Any]] = []
        for raw in self._pages("/v1/workouts", "workouts"):
            began = _parse_time(raw["start_time"])
            if began < start:
                break  # the list is newest first
            if end is None or began < end:
                out.append(raw)
        return out

    def exercise_templates(self) -> dict[str, dict[str, Any]]:
        """Every exercise template (built-in and custom) by id, fetched once per client."""
        if self._templates is None:
            self._templates = {
                str(t["id"]): t
                for t in self._pages("/v1/exercise_templates", "exercise_templates", page_size=TEMPLATE_PAGE_SIZE)
            }
        return self._templates

    def body_measurements(self) -> list[dict[str, Any]]:
        """Body measurements logged in Hevy ({date, weight_kg, ...}), as the API sends them."""
        return list(self._pages("/v1/body_measurements", "body_measurements"))
