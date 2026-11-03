import json
import unittest
from datetime import date
from zoneinfo import ZoneInfo

import anyio
from helpers import FakeFitatuTools, FakeHevy, exercise, workout
from mcp import Client

from hevy_fitatu_sync.mcp_server import FITATU_TOOLS, PROMPTS, Backend, build_server

WARSAW = ZoneInfo("Europe/Warsaw")
WORKOUTS = [
    workout("a", "2026-10-20T17:00:00Z", 60, "Push", [exercise("BENCH", (80, 5))]),
    workout("b", "2026-10-27T17:00:00Z", 60, "Push", [exercise("BENCH", (85, 5))]),
]


def backend(hevy=True, fitatu=None):
    return Backend(
        lambda: FakeHevy(WORKOUTS).client() if hevy else None,
        lambda: fitatu or FakeFitatuTools({"2026-10-20": (2500.0, 140.0)}, {"2026-10-20": 80.0}),
        tz=WARSAW,
        today=lambda: date(2026, 11, 3),
    )


def session_calls(server, calls, prompts=()):
    async def run():
        async with Client(server) as session:
            tools = (await session.list_tools()).tools
            results = {name: await session.call_tool(name, args) for name, args in calls}
            listed_prompts = (await session.list_prompts()).prompts
            texts = {name: (await session.get_prompt(name, args)).messages[0].content.text for name, args in prompts}
            return tools, results, listed_prompts, texts

    return anyio.run(run)


class Server(unittest.TestCase):
    def test_tools_over_mcp(self):
        tools, results, prompts, texts = session_calls(
            build_server(backend()),
            [
                ("get_workouts", {"from_date": "2026-10-20"}),
                ("analyze_training", {}),
                ("analyze_exercise_progress", {"exercise": "bench"}),
                ("get_daily_log", {"from_date": "2026-10-20", "to_date": "2026-10-21"}),
                ("analyze_training_and_nutrition", {}),
                ("get_today", {}),
                ("add_food", {}),
            ],
            prompts=[("training_nutrition_review", {"weeks": "6"})],
        )
        names = [t.name for t in tools]
        self.assertEqual(names[:5], ["analyze_training_and_nutrition", "get_daily_log", "get_workouts",
                                     "analyze_training", "analyze_exercise_progress"])
        self.assertEqual(names[5:], list(FITATU_TOOLS))
        self.assertTrue(all(t.annotations.read_only_hint for t in tools))

        data = lambda name: json.loads(results[name].content[0].text)["data"]
        self.assertEqual(data("get_workouts")["count"], 2)
        # the default window is the last 12 weeks, today included
        self.assertEqual((data("analyze_training")["from_date"], data("analyze_training")["workouts"]), ("2026-08-12", 2))
        self.assertEqual(data("get_workouts")["workouts"][0]["exercises"][0]["sets"], "80kg×5")
        self.assertEqual(data("analyze_exercise_progress")["sessions"][-1]["pr"], True)
        day = data("get_daily_log")["days"][0]
        self.assertEqual((day["trained"], day["nutrition"]["kcal"], day["weight_kg"]), (True, 2500, 80.0))
        self.assertEqual(data("analyze_training_and_nutrition")["from_date"], "2026-10-07")
        self.assertEqual(data("get_today"), {"date": "2026-11-03"})  # delegated to Fitatu
        self.assertTrue(results["add_food"].is_error)  # no writes here

        self.assertEqual({p.name for p in prompts}, set(PROMPTS))
        self.assertIn("last 6 weeks", texts["training_nutrition_review"])

    def test_errors_reach_the_model(self):
        b = backend(hevy=False)
        r = b.safe_call("get_workouts", {})
        self.assertEqual((r["ok"], r["error"]["error_type"]), (False, "auth"))
        self.assertIn("HEVY_API_KEY", r["error"]["message"])
        r = b.safe_call("get_daily_log", {"from_date": "2026-10-20", "to_date": "2026-10-20"})
        self.assertTrue(r["ok"])  # the food side still answers
        self.assertTrue(any("HEVY_API_KEY" in w for w in r["data"]["warnings"]))
        r = b.safe_call("get_workouts", {"from_date": "20.10.2026"})
        self.assertEqual(r["error"]["error_type"], "validation")
        r = backend().safe_call("analyze_exercise_progress", {})
        self.assertIn("exercise is required", r["error"]["message"])

    def test_no_fitatu_session_is_explained(self):
        def no_session():
            raise RuntimeError("No Fitatu session: run `fitatu-api login`")

        b = Backend(lambda: FakeHevy(WORKOUTS).client(), no_session, tz=WARSAW, today=lambda: date(2026, 11, 3))
        r = b.safe_call("get_day", {})
        self.assertFalse(r["ok"])
        self.assertIn("fitatu-api login", r["error"]["message"])
        r = b.safe_call("get_daily_log", {})
        self.assertTrue(r["ok"])
        self.assertTrue(any("fitatu-api login" in w for w in r["data"]["warnings"]))


if __name__ == "__main__":
    unittest.main()
