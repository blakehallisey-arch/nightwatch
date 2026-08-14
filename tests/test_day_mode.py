"""Day mode: same queue, same items, nothing merges.

It is enforced in the runner, not requested in the prompt. So the test is not
"was the agent told" — it is "was git ever asked to merge". The answer has to be
no, even for an item nightwatch itself derived as tier 1.
"""
from helpers import FakeAgent, FakeGit, RepoCase
from nightwatch.run import Fire


class DayModeTests(RepoCase):
    CONFIG = {"tier1": [{"kinds": ["drift"], "paths": ["docs/**"]}]}

    def setUp(self):
        super().setUp()
        self.queue.add("fix a stale doc link", kind="drift", paths=["docs/a.md"])
        self.agent = self.patch_agent(FakeAgent(self.queue))
        self.git = self.patch_git(FakeGit())

    def test_day_mode_refuses_to_merge_a_tier_1_item(self):
        entry = Fire(self.cfg, day=True, out=self.out).run()

        item = entry["items"][0]
        self.assertEqual(item["tier"], 1, "it really is tier 1")
        self.assertEqual(item["status"], "pr-open")
        self.assertEqual(self.git.calls, [], "git must never be asked to merge")
        self.assertIn("day mode", item["result"])

    def test_day_mode_tells_the_agent_too(self):
        Fire(self.cfg, day=True, out=self.out).run()
        env = self.agent.calls[0]["env"]
        self.assertEqual(env["NIGHTWATCH_DAY"], "1")
        self.assertEqual(env["UNATTENDED_RUN"], "1")
        self.assertEqual(env["NIGHTWATCH_MAY_MERGE"], "0")

    def test_night_mode_does_merge_the_same_item(self):
        entry = Fire(self.cfg, day=False, out=self.out).run()

        self.assertEqual(entry["items"][0]["status"], "done")
        self.assertTrue(any(c[0] == "merge" for c in self.git.calls),
                        f"expected a merge, got {self.git.calls}")
        self.assertEqual(self.agent.calls[0]["env"]["NIGHTWATCH_MAY_MERGE"], "1")
        self.assertNotIn("NIGHTWATCH_DAY", self.agent.calls[0]["env"])

    def test_a_failed_merge_leaves_the_item_open_rather_than_done(self):
        self.patch_git(FakeGit(code=1))
        entry = Fire(self.cfg, day=False, out=self.out).run()
        self.assertEqual(entry["items"][0]["status"], "pr-open")
        self.assertIn("could not check out", entry["items"][0]["result"])


class TierTwoInNightModeTests(RepoCase):
    CONFIG = {"tier1": [{"kinds": ["drift"], "paths": ["docs/**"]}]}

    def test_tier_2_stops_at_a_pr_even_at_night(self):
        self.queue.add("rewrite the scheduler", kind="build", paths=["src/main.py"])
        self.patch_agent(FakeAgent(self.queue))
        git = self.patch_git(FakeGit())

        entry = Fire(self.cfg, out=self.out).run()

        self.assertEqual(entry["items"][0]["tier"], 2)
        self.assertEqual(entry["items"][0]["status"], "pr-open")
        self.assertEqual(git.calls, [])
