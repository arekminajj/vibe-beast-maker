import unittest

from helpers import CARDIO_HIT, STRENGTH_HIT

from hevy_fitatu_sync.energy import pick_hit, rate_from_hit, reference_minutes


class ReferenceMinutes(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(reference_minutes({"measuresDescription": "5 km w 00:30:00"}), 30)
        self.assertEqual(reference_minutes({"measuresDescription": "15 km w 01:00:00"}), 60)
        self.assertEqual(reference_minutes({"measuresDescription": "60 min"}), 60)
        self.assertEqual(reference_minutes({"measuresDescription": "1,5 h"}), 90)
        self.assertEqual(reference_minutes({"measures": [{"measureType": "DISTANCE", "value": 5},
                                                         {"measureType": "TIME", "value": 45}]}), 45)
        self.assertIsNone(reference_minutes({"measuresDescription": "10 powtórzeń"}))


class Rates(unittest.TestCase):
    def test_rate_scales_to_workout_length(self):
        rate = rate_from_hit(STRENGTH_HIT)
        self.assertEqual(rate.activity_id, 610)
        self.assertEqual(rate.kcal(60), 300)
        self.assertEqual(rate.kcal(90), 450)
        self.assertEqual(rate.kcal(1), 5)

    def test_no_rate_without_duration_or_energy(self):
        self.assertIsNone(rate_from_hit({"activityId": 1, "energy": 0, "measuresDescription": "60 min"}))
        self.assertIsNone(rate_from_hit({"activityId": 1, "energy": 100, "measuresDescription": "x"}))

    def test_pick_prefers_strength_then_requested_id(self):
        self.assertEqual(pick_hit([CARDIO_HIT, STRENGTH_HIT])["activityId"], 610)
        self.assertEqual(pick_hit([CARDIO_HIT], None)["activityId"], 535)
        self.assertEqual(pick_hit([CARDIO_HIT, STRENGTH_HIT], 535)["activityId"], 535)
        self.assertIsNone(pick_hit([CARDIO_HIT], 999))
        self.assertIsNone(pick_hit([]))

    def test_pick_hit_prefers_the_exact_name(self):
        hits = [STRENGTH_HIT, CARDIO_HIT]
        self.assertEqual(pick_hit(hits, phrase=" bieganie ")["activityId"], 535)
        self.assertEqual(pick_hit(hits, phrase="biegi")["activityId"], 610)  # no exact match: strength first


if __name__ == "__main__":
    unittest.main()
