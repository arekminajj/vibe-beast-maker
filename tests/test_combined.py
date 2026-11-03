import unittest
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from helpers import FakeFitatuTools, FakeHevy, exercise, workout

from hevy_fitatu_sync.combined import RangeError, analyze, check_range, daily_log, public_log

WARSAW = ZoneInfo("Europe/Warsaw")
START, END = date(2026, 9, 1), date(2026, 10, 12)  # 42 days: two get_progress windows


def six_weeks():
    """Training Mon/Wed/Fri; 2600 kcal and 150 g protein on training days, 2200/110 on rest days;
    a weigh-in every Monday, losing 0.25 kg a week."""
    workouts, food, weights = [], {}, {}
    for i in range((END - START).days + 1):
        d = START + timedelta(days=i)
        if d.weekday() in (0, 2, 4):
            workouts.append(workout(f"w{i}", f"{d}T17:00:00Z", 60, exercises=[exercise("BENCH", (80, 5), (80, 5))]))
            food[d.isoformat()] = (2600.0, 150.0)
        else:
            food[d.isoformat()] = (2200.0, 110.0)
        if d.weekday() == 0:
            weights[d.isoformat()] = 80.0 - 0.25 * (i // 7)
    return workouts, food, weights


class DailyLog(unittest.TestCase):
    def test_joins_by_day_and_reads_fitatu_in_windows(self):
        workouts, food, weights = six_weeks()
        fitatu = FakeFitatuTools(food, weights)
        log = daily_log(FakeHevy(workouts).client(), fitatu, START, END, WARSAW)
        self.assertEqual(len(log["days"]), 42)
        monday = log["days"][0]
        self.assertEqual((monday["date"], monday["trained"], monday["weight_kg"]), ("2026-09-01", False, None))
        tuesday_sept_7 = next(d for d in log["days"] if d["date"] == "2026-09-07")
        self.assertTrue(tuesday_sept_7["trained"])
        self.assertEqual(tuesday_sept_7["workouts"][0]["working_sets"], 2)
        self.assertEqual(tuesday_sept_7["nutrition"]["kcal"], 2600)
        self.assertEqual(tuesday_sept_7["weight_kg"], 80.0)
        self.assertEqual([c for c, _ in fitatu.calls].count("get_progress"), 2)
        self.assertEqual(log["warnings"], [])
        self.assertNotIn("_raws", public_log(log))

    def test_hevy_weight_fills_in_and_fitatu_wins(self):
        hevy = FakeHevy(measurements=[{"date": "2026-09-02", "weight_kg": 79.0}, {"date": "2026-09-07", "weight_kg": 1}])
        log = daily_log(hevy.client(), FakeFitatuTools(weights={"2026-09-07": 80.0}), START, END, WARSAW)
        by_day = {d["date"]: d["weight_kg"] for d in log["days"]}
        self.assertEqual((by_day["2026-09-02"], by_day["2026-09-07"]), (79.0, 80.0))

    def test_one_side_failing_keeps_the_other(self):
        workouts, _, _ = six_weeks()

        class Broken:
            def call(self, name, arguments=None):
                return {"ok": False, "error": {"error_type": "auth", "message": "session expired"}}

        log = daily_log(FakeHevy(workouts).client(), Broken(), START, END, WARSAW)
        self.assertTrue(any(d["trained"] for d in log["days"]))
        self.assertIn("session expired", log["warnings"][0])
        log = daily_log(None, FakeFitatuTools(), START, END, WARSAW)
        self.assertIn("HEVY_API_KEY", log["warnings"][0])

    def test_ranges(self):
        with self.assertRaises(RangeError):
            check_range(END, START)
        with self.assertRaises(RangeError):
            check_range(date(2025, 1, 1), date(2026, 1, 2))


class Analysis(unittest.TestCase):
    def test_training_vs_rest_days_protein_and_balance(self):
        workouts, food, weights = six_weeks()
        log = daily_log(FakeHevy(workouts).client(), FakeFitatuTools(food, weights), START, END, WARSAW)
        a = analyze(log, WARSAW, START, END)
        split = a["training_days_vs_rest_days"]
        self.assertEqual((split["training_days"]["days"], split["training_days"]["avg_kcal"]), (18, 2600))
        self.assertEqual((split["rest_days"]["days"], split["rest_days"]["avg_kcal"]), (24, 2200))
        self.assertEqual(a["training"]["per_week"]["workouts"], 3.0)
        self.assertEqual(a["training"]["muscle_sets_per_week"]["chest"], 6.0)
        balance = a["energy_balance"]
        self.assertEqual(balance["weight_trend_kg_per_week"], -0.25)
        # intake ~2371 kcal/day, losing 0.25 kg/week = 275 kcal/day deficit
        self.assertEqual(balance["estimated_daily_balance_kcal"], -275)
        self.assertEqual(balance["estimated_maintenance_kcal"], 2646)
        self.assertEqual(a["nutrition"]["protein_g_per_kg"], 1.6)  # 127.1 g / 79.4 kg
        self.assertEqual(a["nutrition"]["days_protein_in_target"], 18)

    def test_sparse_logging_gives_no_maintenance_and_says_why(self):
        log = daily_log(None, FakeFitatuTools({"2026-09-03": (900.0, 30.0)}, {"2026-09-01": 80.0}), START, END, WARSAW)
        a = analyze(log, WARSAW, START, END)
        self.assertIsNone(a["energy_balance"]["estimated_maintenance_kcal"])
        self.assertIsNone(a["training"])
        self.assertTrue(any("1 of 42 days" in n for n in a["notes"]))
        self.assertTrue(any("No maintenance estimate" in n for n in a["notes"]))


if __name__ == "__main__":
    unittest.main()
