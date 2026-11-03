import tempfile
import unittest
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from hevy_fitatu_sync.state import State
from hevy_fitatu_sync.sync import Config, SyncError, Syncer, row_id_for

from helpers import CARDIO_HIT, FakeHevy, activities_on, demo_fitatu, workout

WARSAW = ZoneInfo("Europe/Warsaw")
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def config(**kw):
    return Config(since=kw.pop("since", date(2026, 10, 1)), timezone=kw.pop("timezone", WARSAW), **kw)


class Harness:
    def __init__(self, workouts, deleted=None, hits=None, **cfg):
        self.hevy = FakeHevy(workouts, deleted)
        self.fitatu = demo_fitatu(hits)
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "state.json"
        self.cfg = config(**cfg)

    def run(self, dry_run=False, **cfg):
        state = State.load(self.path)
        syncer = Syncer(self.hevy.client(), self.fitatu, state, config(**cfg) if cfg else self.cfg,
                        save_state=lambda s: s.save(self.path), now=lambda: NOW)
        return syncer.run(dry_run=dry_run)

    def on(self, day):
        return activities_on(self.fitatu, day)


class RowIds(unittest.TestCase):
    def test_stable_v1_uuid(self):
        a = row_id_for("w1", date(2026, 10, 2), 60, 610)
        self.assertEqual(a, row_id_for("w1", date(2026, 10, 2), 60, 610))
        self.assertEqual(uuid.UUID(a).version, 1)
        self.assertNotEqual(a, row_id_for("w1", date(2026, 10, 2), 61, 610))
        self.assertNotEqual(a, row_id_for("w2", date(2026, 10, 2), 60, 610))


class Sync(unittest.TestCase):
    def harness(self, *args, **kw):
        h = Harness(*args, **kw)
        self.addCleanup(h.dir.cleanup)
        return h

    def test_dry_run_writes_nothing(self):
        h = self.harness([workout("w1", "2026-10-02T17:00:00Z", 75)])
        report = h.run(dry_run=True)
        self.assertEqual([a.kind for a in report.actions], ["add"])
        self.assertEqual(report.actions[0].energy, 375)  # 300 kcal/h for 75 min
        self.assertEqual(h.on("2026-10-02"), [])
        self.assertFalse(h.path.exists())

    def test_adds_each_workout_once(self):
        h = self.harness([workout("w1", "2026-10-02T17:00:00Z", 60), workout("w2", "2026-10-04T08:00:00Z", 90, "Legs")])
        report = h.run()
        self.assertTrue(report.ok)
        self.assertEqual([(a.kind, a.status) for a in report.actions], [("add", "done"), ("add", "done")])
        logged = h.on("2026-10-04")
        self.assertEqual(len(logged), 1)
        self.assertEqual((logged[0]["activityId"], logged[0]["energy"], logged[0]["simpleMeasures"][0]["value"]), (610, 450, 90))

        again = h.run()
        self.assertEqual([a.kind for a in again.actions], ["keep", "keep"])
        self.assertEqual(len(h.on("2026-10-02")), 1)
        self.assertEqual(State.load(h.path).last_run, NOW.isoformat())

    def test_uses_the_local_day(self):
        # 23:30 UTC on 2 Oct is 01:30 on 3 Oct in Warsaw.
        h = self.harness([workout("w1", "2026-10-02T23:30:00Z", 60)])
        h.run()
        self.assertEqual(h.on("2026-10-02"), [])
        self.assertEqual(len(h.on("2026-10-03")), 1)

    def test_lost_state_does_not_duplicate(self):
        h = self.harness([workout("w1", "2026-10-02T17:00:00Z", 60)])
        h.run()
        h.path.unlink()
        report = h.run()
        self.assertEqual(report.actions[0].status, "already_there")
        self.assertEqual(len(h.on("2026-10-02")), 1)
        self.assertIn("w1", State.load(h.path).workouts)

    def test_edited_workout_is_replaced(self):
        h = self.harness([workout("w1", "2026-10-02T17:00:00Z", 60)])
        h.run()
        h.hevy.workouts = [workout("w1", "2026-10-05T17:00:00Z", 80)]  # moved and longer
        report = h.run()
        self.assertEqual([(a.kind, a.status) for a in report.actions], [("replace", "done")])
        self.assertEqual(h.on("2026-10-02"), [])
        self.assertEqual([a["energy"] for a in h.on("2026-10-05")], [400])

    def test_deleted_workout_is_removed(self):
        h = self.harness([workout("w1", "2026-10-02T17:00:00Z", 60), workout("w2", "2026-10-03T17:00:00Z", 60)])
        h.run()
        h.hevy.workouts = h.hevy.workouts[1:]
        h.hevy.deleted = ["w1"]
        report = h.run()
        removed = [a for a in report.actions if a.kind == "remove"]
        self.assertEqual([(a.workout_id, a.reason, a.status) for a in removed], [("w1", "deleted in Hevy", "done")])
        self.assertEqual(h.on("2026-10-02"), [])
        self.assertNotIn("w1", State.load(h.path).workouts)

    def test_old_synced_workouts_outside_window_are_left_alone(self):
        h = self.harness([workout("w1", "2026-09-10T17:00:00Z", 60)], since=date(2026, 9, 1))
        h.run()
        report = h.run(since=date(2026, 10, 1))  # w1 is now older than the window
        self.assertEqual(report.actions, [])
        self.assertEqual(len(h.on("2026-09-10")), 1)

    def test_activity_removed_by_hand_in_fitatu_is_not_an_error_on_delete(self):
        h = self.harness([workout("w1", "2026-10-02T17:00:00Z", 60)])
        h.run()
        row = h.on("2026-10-02")[0]["uuid"]
        h.fitatu.planner.remove_activity(h.fitatu.auth.fitatu_user_id, "2026-10-02", item_id=row)
        h.hevy.workouts, h.hevy.deleted = [], ["w1"]
        report = h.run()
        self.assertTrue(report.ok)
        self.assertEqual(State.load(h.path).workouts, {})

    def test_short_workouts_are_skipped(self):
        h = self.harness([workout("w1", "2026-10-02T17:00:00Z", 3)], min_minutes=10)
        self.assertEqual(h.run().actions, [])

    def test_passes_bmr_and_sex_to_the_catalog(self):
        h = self.harness([])
        h.run(dry_run=True)
        self.assertEqual(h.fitatu.search_seen, {"phrase": "trening siłowy", "bmr": 1600.0, "sex": 1})

    def test_kcal_per_hour_override(self):
        h = self.harness([workout("w1", "2026-10-02T17:00:00Z", 30)], kcal_per_hour=500)
        report = h.run(dry_run=True)
        self.assertEqual((report.rate.activity_id, report.actions[0].energy), (610, 250))

    def test_unknown_activity_id_is_a_clear_error(self):
        h = self.harness([], activity_id=12345)
        with self.assertRaisesRegex(SyncError, "activity id 12345"):
            h.run(dry_run=True)

    def test_activity_without_duration_needs_kcal_per_hour(self):
        h = self.harness([], hits=[{**CARDIO_HIT, "measuresDescription": "10 serii"}])
        with self.assertRaisesRegex(SyncError, "--kcal-per-hour"):
            h.run(dry_run=True)

    def test_a_failed_write_does_not_stop_the_others(self):
        from fitatu_api.exceptions import FitatuApiError

        h = self.harness([workout("w1", "2026-10-02T17:00:00Z", 60), workout("w2", "2026-10-03T17:00:00Z", 60)])
        real_add = h.fitatu.planner.add_activity

        def flaky(user_id, day, **kw):
            if str(day) == "2026-10-02":
                raise FitatuApiError("POST /diet-plan/1/days -> 503 maintenance", status_code=503)
            return real_add(user_id, day, **kw)

        h.fitatu.planner.add_activity = flaky
        report = h.run()
        self.assertFalse(report.ok)
        self.assertEqual([a.status for a in report.actions], ["error", "done"])
        state = State.load(h.path)
        self.assertEqual(set(state.workouts), {"w2"})
        self.assertIsNone(state.last_run)  # not advanced, so the next run retries w1

        h.fitatu.planner.add_activity = real_add
        self.assertEqual([a.kind for a in h.run().actions], ["add", "keep"])

    def test_pages_through_hevy(self):
        workouts = [workout(f"w{i}", f"2026-10-{i + 1:02d}T17:00:00Z", 60) for i in range(25)]
        h = self.harness(workouts, since=date(2026, 10, 10))
        report = h.run(dry_run=True)
        self.assertEqual(len(report.actions), 16)  # 10 Oct .. 25 Oct
        self.assertEqual(h.hevy.calls.count("/v1/workouts"), 2)  # stopped once past the window


if __name__ == "__main__":
    unittest.main()
