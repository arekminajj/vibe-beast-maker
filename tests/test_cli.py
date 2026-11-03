import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from helpers import FakeHevy, activities_on, demo_fitatu, workout

from hevy_fitatu_sync import cli
from hevy_fitatu_sync.state import State


class Cli(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state.json"
        self.fitatu = demo_fitatu()
        self.hevy = FakeHevy([workout("w1", "2026-10-02T17:00:00Z", 60)])
        patches = [
            mock.patch.object(cli, "fitatu_client", lambda: self.fitatu),
            mock.patch.object(cli, "hevy_client", lambda: self.hevy.client()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_sync_dry_run_then_apply_then_status(self):
        args = ["sync", "--since", "2026-10-01", "--timezone", "Europe/Warsaw", "--state", str(self.state)]
        code, out = self.main(*args, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("add 2026-10-02 'Push' 60 min, 300 kcal", out)
        self.assertIn("Dry run", out)
        self.assertFalse(self.state.exists())

        code, out = self.main(*args, "--json")
        report = json.loads(out)
        self.assertEqual((code, report["counts"]["add"], report["actions"][0]["status"]), (0, 1, "done"))
        self.assertEqual(len(activities_on(self.fitatu, "2026-10-02")), 1)

        code, out = self.main(*args)
        self.assertIn("nothing to do (1 already synced)", out)

        code, out = self.main("status", "--state", str(self.state))
        self.assertIn("Synced workouts: 1", out)
        self.assertEqual(len(State.load(self.state).workouts), 1)

    def test_activities(self):
        code, out = self.main("activities", "--json")
        rows = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual([(r["activity_id"], r["type"], r["kcal_per_hour"]) for r in rows],
                         [(535, "cardio", 680), (610, "strength", 300)])

    def test_errors_are_reported_not_raised(self):
        with mock.patch.object(cli, "hevy_client", lambda: FakeHevy().client().__class__("bad", session=FakeHevy(), max_retries=0)):
            with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                code = cli.main(["sync", "--state", str(self.state)])
        self.assertEqual(code, 2)
        self.assertIn("HEVY_API_KEY", err.getvalue())


class Setup(unittest.TestCase):
    def test_bad_options_exit_2(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(cli.main(["sync", "--since", "yesterday"]), 2)
            self.assertEqual(cli.main(["sync", "--timezone", "Mars/Olympus"]), 2)
        self.assertIn("--since must be a date", err.getvalue())
        self.assertIn("unknown time zone", err.getvalue())


class Credentials(unittest.TestCase):
    def test_missing_fitatu_login_is_explained(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"FITATU_SESSION": str(Path(tmp) / "none.json")}, clear=False
        ):
            for key in ("FITATU_SESSION_JSON", "FITATU_EMAIL", "FITATU_PASSWORD"):
                os.environ.pop(key, None)
            with self.assertRaisesRegex(cli.CliError, "fitatu-api login"):
                cli.fitatu_client()


if __name__ == "__main__":
    unittest.main()
