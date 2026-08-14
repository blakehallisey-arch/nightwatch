"""The queue, and the two things about it that are easy to get wrong:

  claiming is atomic — two callers never get the same item;
  a crashed item is recorded blocked, never silently dropped.

The atomicity test spawns real processes rather than threads. Threads would pass
against a broken implementation often enough to be useless.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from helpers import FakeAgent, RepoCase
from nightwatch.queue import Queue
from nightwatch.run import Fire

REPO = Path(__file__).resolve().parents[1]

CLAIMER = """
import sys
sys.path.insert(0, %r)
from nightwatch.queue import Queue
item = Queue(%r).claim_next()
print(item["id"] if item else "NONE")
"""


class AtomicClaimTests(RepoCase):
    def test_two_callers_never_get_the_same_item(self):
        for n in range(6):
            self.queue.add(f"item {n}", kind="chore")

        script = CLAIMER % (str(REPO), str(self.cfg.queue_path))
        env = dict(os.environ)
        procs = [subprocess.Popen([sys.executable, "-c", script],
                                  stdout=subprocess.PIPE, text=True, env=env)
                 for _ in range(6)]
        ids = [p.communicate()[0].strip() for p in procs]

        self.assertNotIn("NONE", ids, f"every process should have got one: {ids}")
        self.assertEqual(len(set(ids)), 6, f"an item was handed out twice: {ids}")
        self.assertTrue(all(i["status"] == "running" for i in self.queue.items()))

    def test_an_empty_bench_returns_none(self):
        self.assertIsNone(Queue(self.cfg.queue_path).claim_next())


class CrashTests(RepoCase):
    def test_an_agent_that_never_reports_is_blocked_not_dropped(self):
        self.queue.add("something that kills the session", kind="chore")
        self.patch_agent(FakeAgent(self.queue,
                                   behaviour=lambda _id: ("crash", None),
                                   exit_code=137))

        entry = Fire(self.cfg, out=self.out).run()

        item = self.queue.items()[0]
        self.assertEqual(item["status"], "blocked")
        self.assertIn("exited 137 without reporting", item["result"])
        self.assertEqual(entry["items"][0]["status"], "blocked")

    def test_an_agent_that_exits_clean_but_says_nothing_is_still_blocked(self):
        self.queue.add("quietly does nothing", kind="chore")
        self.patch_agent(FakeAgent(self.queue,
                                   behaviour=lambda _id: ("crash", None),
                                   exit_code=0))
        Fire(self.cfg, out=self.out).run()
        self.assertEqual(self.queue.items()[0]["status"], "blocked")

    def test_an_item_left_running_goes_back_on_the_bench(self):
        item = self.queue.add("interrupted", kind="chore")
        self.queue.claim_next()
        released = self.queue.release_running("the fire ended")
        self.assertEqual(released, 1)
        self.assertEqual(self.queue.get(item["id"])["status"], "queued")


class VerbTests(RepoCase):
    def test_add_list_block_drop(self):
        a = self.queue.add("first", kind="chore")
        b = self.queue.add("second", kind="chore")
        self.assertEqual(len(self.queue.items()), 2)

        self.queue.finish(b["id"], "blocked", result="needs a human")
        self.assertEqual(self.queue.get(b["id"])["status"], "blocked")

        self.queue.drop(a["id"])
        self.assertIsNone(self.queue.get(a["id"]))
        with self.assertRaises(KeyError):
            self.queue.drop(a["id"])

    def test_the_queue_runs_in_the_order_you_added(self):
        # Not in tier order. The only tier available at sort time is the one the
        # item claims, and the claim is not trusted anywhere else either.
        self.queue.add("first in", kind="drift", tier=1)
        self.queue.add("second in", kind="build", tier=2)
        self.assertEqual([i["title"] for i in self.queue.runnable()],
                         ["first in", "second in"])

    def test_the_file_is_plain_readable_json(self):
        self.queue.add("readable", kind="chore")
        data = json.loads(self.cfg.queue_path.read_text())
        self.assertEqual(data["items"][0]["title"], "readable")
        for field in ("id", "title", "body", "kind", "tier", "status", "created",
                      "started", "finished", "result", "pr", "notes"):
            self.assertIn(field, data["items"][0])
