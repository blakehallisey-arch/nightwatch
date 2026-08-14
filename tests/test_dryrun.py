"""The dry run is how a stranger evaluates this tool in sixty seconds, so it is
worth a test: it must print the real argv, the real tier reasoning, and it must
invoke nothing and stamp nothing.
"""
import datetime as dt

from helpers import FakeAgent, FakeGit, RepoCase
from nightwatch.run import Fire


class DryRunTests(RepoCase):
    CONFIG = {"max_items": 2, "max_usd": 8.0,
              "tier1": [{"kinds": ["drift"], "paths": ["docs/**"]}],
              "never_tier1": ["deploy/**"]}

    def setUp(self):
        super().setUp()
        self.queue.add("fix a stale link", kind="drift", paths=["docs/a.md"])
        self.queue.add("ship the deploy script", kind="drift",
                       paths=["deploy/go.sh"], tier=1)
        self.queue.add("third", kind="chore")
        self.agent = self.patch_agent(FakeAgent(self.queue))
        self.git = self.patch_git(FakeGit())

    def test_it_invokes_nothing_and_stamps_nothing(self):
        Fire(self.cfg, dry_run=True, out=self.out).run()
        self.assertEqual(self.agent.calls, [])
        self.assertEqual(self.git.calls, [])
        self.assertTrue(all(i["status"] == "queued" for i in self.queue.items()))
        self.assertFalse(self.cfg.fires_path.exists(),
                         "a dry run is not a fire and does not get recorded")

    def test_it_prints_the_argv_the_tier_and_the_lid(self):
        Fire(self.cfg, dry_run=True, out=self.out).run()
        text = self.printed_text()
        self.assertIn("DRY RUN", text)
        self.assertIn("fake-agent", text)
        self.assertIn("tier 1", text)
        self.assertIn("tier 2", text)
        self.assertIn("never_tier1", text)
        self.assertIn("WARNING", text, "a demoted claim is printed")
        self.assertIn("item lid", text)

    def test_day_mode_dry_run_says_nothing_merges(self):
        Fire(self.cfg, day=True, dry_run=True, out=self.out).run()
        text = self.printed_text()
        self.assertIn("day mode: nothing merges", text)
        self.assertIn("NIGHTWATCH_DAY=1", text)
        self.assertNotIn("on success: merge into", text)

    def test_a_dry_run_outside_the_window_still_stands_down(self):
        cfg = self.cfg
        cfg.data["window"] = {"start": "23:00", "end": "07:00"}
        entry = Fire(cfg, dry_run=True, now=dt.datetime(2026, 8, 14, 12, 0),
                     out=self.out).run()
        self.assertIn("outside the window", entry["stood_down"])
