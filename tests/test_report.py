"""The report reads git and GitHub, not nightwatch's own record.

Two things under test. First: when gh is missing, the report says the cross-check
was skipped instead of printing a clean bill of health it never checked. Second:
when the record says "waiting on you" and git says that branch already merged,
the report prints both and refuses to pick a winner. That second case is the
whole reason this file exists — a rail that believed itself sat refusing new work
over PRs that had merged hours earlier.
"""
import json

from helpers import RepoCase
from nightwatch import report as report_mod


class NoGhTests(RepoCase):
    def setUp(self):
        super().setUp()
        item = self.queue.add("a thing", kind="chore")
        self.queue.finish(item["id"], "pr-open", result="opened a PR", pr="12")
        self.patch(report_mod, "gh_available", lambda: False)
        self.patch(report_mod, "git_merged_subjects", lambda *a, **k: [])

    def patch(self, mod, name, value):
        real = getattr(mod, name)
        setattr(mod, name, value)
        self.addCleanup(lambda: setattr(mod, name, real))

    def test_it_says_the_cross_check_was_skipped(self):
        text = report_mod.text_report(self.cfg)
        self.assertIn("gh is not installed", text)
        self.assertIn("cross-check was skipped", text)
        self.assertNotIn("agree on what is open", text)

    def test_the_waiting_list_still_prints(self):
        text = report_mod.text_report(self.cfg)
        self.assertIn("open PRs waiting on you: 1", text)
        self.assertIn("#12", text)


class DisagreementTests(RepoCase):
    def setUp(self):
        super().setUp()
        self.item = self.queue.add("stale doc", kind="drift", paths=["docs/a.md"])
        self.queue.finish(self.item["id"], "pr-open", result="opened a PR", pr="7")
        self.patch(report_mod, "gh_available", lambda: True)

    def patch(self, mod, name, value):
        real = getattr(mod, name)
        setattr(mod, name, value)
        self.addCleanup(lambda: setattr(mod, name, real))

    def test_git_says_merged_while_the_record_says_waiting(self):
        subject = f"nightwatch: merge nightwatch/{self.item['id']}"
        self.patch(report_mod, "git_merged_subjects", lambda *a, **k: [subject])
        self.patch(report_mod, "gh_open_prs",
                   lambda root: ([], "GitHub answered"))

        check = report_mod.cross_check(self.cfg)
        self.assertEqual(len(check["disagreements"]), 1)
        self.assertIn("already merged", check["disagreements"][0])

        text = report_mod.text_report(self.cfg)
        self.assertIn("the record and the repo disagree", text)
        self.assertIn("does not pick a winner", text)

    def test_github_does_not_list_a_pr_the_record_calls_open(self):
        self.patch(report_mod, "git_merged_subjects", lambda *a, **k: [])
        self.patch(report_mod, "gh_open_prs",
                   lambda root: ([{"number": 99, "title": "other", "state": "OPEN"}],
                                 "GitHub answered"))

        check = report_mod.cross_check(self.cfg)
        joined = " ".join(check["disagreements"])
        self.assertIn("GitHub does not list it", joined)
        self.assertEqual(len(check["unmatched_open_prs"]), 1)

    def test_they_agree_when_they_agree(self):
        self.patch(report_mod, "git_merged_subjects", lambda *a, **k: [])
        self.patch(report_mod, "gh_open_prs",
                   lambda root: ([{"number": 7, "title": "stale doc", "state": "OPEN"}],
                                 "GitHub answered"))
        text = report_mod.text_report(self.cfg)
        self.assertIn("the record and GitHub agree", text)


class OutputShapeTests(RepoCase):
    def setUp(self):
        super().setUp()
        self.patch(report_mod, "gh_available", lambda: False)
        self.patch(report_mod, "git_merged_subjects", lambda *a, **k: [])
        from nightwatch.run import append_fire
        append_fire(self.cfg.fires_path, {
            "id": "fire-20260814-020000", "mode": "night", "dry_run": False,
            "stood_down": None, "stopped_because": "the queue is empty",
            "spent_usd": 2.0, "max_usd": 8.0, "max_items": 4,
            "items": [{"id": "x", "title": "a thing", "tier": 2,
                       "tier_reason": "no tier1 rule matched", "status": "pr-open",
                       "result": "opened a PR", "cost_usd": 2.0,
                       "cost_source": "agent CLI (total_cost_usd)"}]})

    def patch(self, mod, name, value):
        real = getattr(mod, name)
        setattr(mod, name, value)
        self.addCleanup(lambda: setattr(mod, name, real))

    def test_json_report_parses(self):
        blob = json.loads(report_mod.json_report(self.cfg, history=True))
        self.assertEqual(len(blob["fires"]), 1)
        self.assertIn("cross_check", blob)

    def test_html_is_self_contained_and_theme_aware(self):
        page = report_mod.html_report(self.cfg)
        self.assertIn("<!doctype html>", page)
        self.assertIn("prefers-color-scheme", page)
        self.assertNotIn("http://", page)
        self.assertNotIn("<script", page)
        self.assertIn("fire-20260814-020000", page)

    def test_the_page_escapes_what_it_prints(self):
        from nightwatch.run import append_fire
        append_fire(self.cfg.fires_path, {
            "id": "fire-x", "mode": "night", "stood_down": None,
            "stopped_because": "done", "spent_usd": 0, "max_usd": 8,
            "items": [{"id": "y", "title": "<script>alert(1)</script>",
                       "tier": 2, "tier_reason": "r", "status": "blocked"}]})
        page = report_mod.html_report(self.cfg)
        self.assertIn("&lt;script&gt;", page)
