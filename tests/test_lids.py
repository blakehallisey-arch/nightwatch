"""The two lids, and that each one stops MID-QUEUE rather than after the fact.

The spend lid is the one worth testing hard. An item count is not a token count,
so a fire that stops after four items can still have spent four times what you
meant to spend. Both lids are checked before an item starts and again after it
finishes.
"""
from helpers import FakeAgent, RepoCase
from nightwatch.run import Fire


class ItemLidTests(RepoCase):
    CONFIG = {"max_items": 2, "max_usd": 0}

    def test_the_item_lid_stops_mid_queue(self):
        for n in range(4):
            self.queue.add(f"item {n}", kind="chore")
        agent = self.patch_agent(FakeAgent(self.queue))

        entry = Fire(self.cfg, out=self.out).run()

        self.assertEqual(len(agent.calls), 2, "the third item must not start")
        self.assertIn("item lid", entry["stopped_because"])
        left = [i for i in self.queue.items() if i["status"] == "queued"]
        self.assertEqual(len(left), 2, "the rest stay on the bench for next time")


class SpendLidTests(RepoCase):
    CONFIG = {"max_items": 10, "max_usd": 5.0, "est_usd_per_item": 1.0}

    def test_the_spend_lid_stops_mid_queue(self):
        for n in range(3):
            self.queue.add(f"item {n}", kind="chore")
        # Each item reports three dollars back through the CLI's own output.
        agent = self.patch_agent(FakeAgent(self.queue, cost=3.0))

        entry = Fire(self.cfg, out=self.out).run()

        self.assertEqual(len(agent.calls), 2,
                         "two items at $3 is $6 — the third must not start")
        self.assertIn("spend lid", entry["stopped_because"])
        self.assertAlmostEqual(entry["spent_usd"], 6.0)

    def test_an_expensive_estimate_stops_the_item_before_it_starts(self):
        self.queue.add("cheap", kind="chore", est_usd=1.0)
        self.queue.add("ruinous", kind="chore", est_usd=99.0)
        agent = self.patch_agent(FakeAgent(self.queue, cost=1.0))

        entry = Fire(self.cfg, out=self.out).run()

        self.assertEqual(len(agent.calls), 1)
        self.assertIn("estimated at $99.00", entry["stopped_because"])
        parked = [i for i in self.queue.items() if i["title"] == "ruinous"][0]
        self.assertEqual(parked["status"], "queued",
                         "an item the lid refused goes back on the bench")

    def test_cost_falls_back_to_the_estimate_and_says_so(self):
        self.queue.add("silent cli", kind="chore")
        self.patch_agent(FakeAgent(self.queue, cost=None))

        entry = Fire(self.cfg, out=self.out).run()

        item = entry["items"][0]
        self.assertEqual(item["cost_usd"], 1.0)
        self.assertEqual(item["cost_source"], "estimate from config")


class CostParsingTests(RepoCase):
    def test_reads_the_cli_json(self):
        from nightwatch.run import read_cost
        cost, source = read_cost('{"total_cost_usd": 2.5, "ok": true}')
        self.assertEqual(cost, 2.5)
        self.assertIn("total_cost_usd", source)

    def test_reads_a_cost_line_in_noisy_output(self):
        from nightwatch.run import read_cost
        cost, source = read_cost("blah blah\nnightwatch-cost: $0.42\ndone\n")
        self.assertEqual(cost, 0.42)
        self.assertIn("nightwatch-cost", source)

    def test_unknown_cost_is_none_not_zero(self):
        from nightwatch.run import read_cost
        self.assertEqual(read_cost("nothing useful here")[0], None)
