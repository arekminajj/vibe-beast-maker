"""The sync's memory: which Hevy workout became which Fitatu activity, on which day."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def default_state_path() -> Path:
    raw = os.environ.get("HEVY_FITATU_STATE")
    return Path(raw).expanduser() if raw else Path.home() / ".hevy-fitatu-sync" / "state.json"


@dataclass
class SyncedWorkout:
    day: str
    row_id: str
    activity_id: int
    minutes: int
    energy: int
    title: str = ""
    synced_at: str = ""


@dataclass
class State:
    workouts: dict[str, SyncedWorkout] = field(default_factory=dict)
    last_run: str | None = None

    @classmethod
    def load(cls, path: Path) -> State:
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        return cls(
            workouts={wid: SyncedWorkout(**w) for wid, w in (data.get("workouts") or {}).items()},
            last_run=data.get("last_run"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "last_run": self.last_run,
            "workouts": {wid: asdict(w) for wid, w in sorted(self.workouts.items())},
        }
        # Write then rename, so a crash never leaves half a file.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
