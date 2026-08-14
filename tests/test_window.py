"""Outside the window it stands down — and says why in the record.

A rail that goes quiet without a reason is indistinguishable from a rail that
crashed, and a week later you cannot tell which one you have. So the reason is
the thing under test here, not just the fact that nothing ran.
"""
import datetime as dt
import json

from helpers import FakeAgent, RepoCase
from nightwatch.run import Fire


class WindowTests(RepoCase):
    CONFIG = {"window": {"start": "23:00", "end": "07:00"}}

    def setUp(self):
        super().setUp()
        self.queue.add("do a thing", kind="drift", paths=["docs/a.md"])
        self.agent = self.patch_agent(FakeAgent(self.queue))

    def test_outside_the_window_stands_down_with_a_reason(self):
        noon = dt.datetime(2026, 8, 14, 12, 0, 0)
        entry = Fire(self.cfg, now=noon, out=self.out).run()

        self.assertIsNotNone(entry["stood_down"])
        self.assertIn("outside the window", entry["stood_down"])
        self.assertIn("23:00-07:00", entry["stood_down"])
        self.assertIn("12:00", entry["stood_down"])
        self.assertEqual(entry["items"], [])
        self.assertEqual(self.agent.calls, [], "nothing may run outside the window")

    def test_standing_down_is_written_to_the_fire_record(self):
        Fire(self.cfg, now=dt.datetime(2026, 8, 14, 12, 0), out=self.out).run()
        fires = json.loads(self.cfg.fires_path.read_text())["fires"]
        self.assertEqual(len(fires), 1)
        self.assertIn("outside the window", fires[0]["stood_down"])
        self.assertEqual(fires[0]["stopped_because"], "stood down")

    def test_inside_the_window_it_runs(self):
        two_am = dt.datetime(2026, 8, 14, 2, 0)
        entry = Fire(self.cfg, now=two_am, out=self.out).run()
        self.assertIsNone(entry["stood_down"])
        self.assertEqual(len(self.agent.calls), 1)

    def test_window_crossing_midnight_covers_both_sides(self):
        for hour, expect in ((23, True), (2, True), (6, True), (7, False),
                             (12, False), (22, False)):
            with self.subTest(hour=hour):
                open_now, why = self.cfg.window_open(
                    dt.datetime(2026, 8, 14, hour, 30))
                self.assertEqual(open_now, expect, why)

    def test_force_runs_outside_the_window_and_says_so(self):
        entry = Fire(self.cfg, now=dt.datetime(2026, 8, 14, 12, 0),
                     force=True, out=self.out).run()
        self.assertIsNone(entry["stood_down"])
        self.assertIn("--force", self.printed_text())
        self.assertEqual(len(self.agent.calls), 1)

    def test_disabled_stands_down_too(self):
        cfg_path = self.cfg.root / ".nightwatch.json"
        data = json.loads(cfg_path.read_text())
        data["enabled"] = False
        cfg_path.write_text(json.dumps(data))
        from nightwatch.config import Config
        entry = Fire(Config.load(self.cfg.root),
                     now=dt.datetime(2026, 8, 14, 2, 0), out=self.out).run()
        self.assertIn("enabled is false", entry["stood_down"])
        self.assertEqual(self.agent.calls, [])
