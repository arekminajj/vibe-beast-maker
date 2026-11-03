import unittest
from datetime import date
from zoneinfo import ZoneInfo

from helpers import TEMPLATES, exercise, workout

from hevy_fitatu_sync.training import e1rm, exercise_progress, match_exercise, summarize_workout, training_summary

WARSAW = ZoneInfo("Europe/Warsaw")
BY_ID = {t["id"]: t for t in TEMPLATES}


class Workouts(unittest.TestCase):
    def test_summary_skips_warmups_and_weights_secondary_muscles(self):
        raw = workout("w1", "2026-10-02T22:30:00Z", 60, exercises=[
            exercise("BENCH", (80, 5), (80, 5), warmup=(40, 10)),
            exercise("SQUAT", (100, 3)),
        ])
        w = summarize_workout(raw, BY_ID, WARSAW)
        self.assertEqual(w["date"], "2026-10-03")  # 00:30 in Warsaw
        self.assertEqual((w["working_sets"], w["reps"], w["volume_kg"]), (3, 13, 1100))
        self.assertEqual(w["muscle_sets"], {"chest": 2, "quadriceps": 1, "triceps": 1, "shoulders": 1, "glutes": 0.5})
        self.assertEqual(w["exercises"][0]["sets"], "80kg×5, 80kg×5")
        self.assertEqual(w["exercises"][0]["best_e1rm_kg"], 93.3)

    def test_e1rm(self):
        self.assertEqual(e1rm(100, 1), 100)
        self.assertAlmostEqual(e1rm(100, 10), 133.33, places=2)
        self.assertIsNone(e1rm(100, 20))
        self.assertIsNone(e1rm(None, 5))

    def test_training_summary_per_week(self):
        raws = [
            workout("a", "2026-10-05T17:00:00Z", 60, exercises=[exercise("BENCH", (80, 5))]),
            workout("b", "2026-10-07T17:00:00Z", 90, exercises=[exercise("SQUAT", (100, 5), (100, 5))]),
            workout("c", "2026-10-15T17:00:00Z", 30, exercises=[exercise("BENCH", (82.5, 5))]),
        ]
        s = training_summary(raws, BY_ID, WARSAW, date(2026, 10, 5), date(2026, 10, 18))
        self.assertEqual((s["workouts"], s["per_week"]["workouts"], s["per_week"]["minutes"]), (3, 1.5, 90))
        self.assertEqual(s["muscle_sets_per_week"]["quadriceps"], 1.0)
        self.assertEqual(s["longest_gap_days"], 8)
        self.assertEqual([w["week_of"] for w in s["weeks"]], ["2026-10-05", "2026-10-12"])
        self.assertEqual(s["top_exercises"][0]["title"], "Bench Press (Barbell)")


class Progress(unittest.TestCase):
    def test_match_exercise(self):
        titles = ["Bench Press (Barbell)", "Incline Bench Press (Dumbbell)", "Squat (Barbell)"]
        self.assertEqual(match_exercise("bench press (barbell)", titles), ["Bench Press (Barbell)"])
        self.assertEqual(match_exercise("bench", titles), ["Bench Press (Barbell)", "Incline Bench Press (Dumbbell)"])
        self.assertEqual(match_exercise("deadlift", titles), [])

    def test_progress_flags_prs_and_trend(self):
        raws = [
            workout("a", "2026-09-01T17:00:00Z", 60, exercises=[exercise("BENCH", (80, 5))]),
            workout("b", "2026-09-15T17:00:00Z", 60, exercises=[exercise("BENCH", (77.5, 5))]),
            workout("c", "2026-09-29T17:00:00Z", 60, exercises=[exercise("BENCH", (85, 5))]),
        ]
        p = exercise_progress(raws, "bench", WARSAW)
        self.assertEqual(p["exercise"], "Bench Press (Barbell)")
        self.assertEqual([s["pr"] for s in p["sessions"]], [False, False, True])
        self.assertEqual(p["trend"]["change_kg"], 5.8)
        self.assertEqual(p["trend"]["per_4_weeks_kg"], 5.8)
        self.assertEqual(exercise_progress(raws, "row", WARSAW)["sessions"], [])


if __name__ == "__main__":
    unittest.main()
