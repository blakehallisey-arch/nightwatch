"""The tier is re-derived, never taken from what the item claims.

The thing that proposes work is not the thing that authorizes it. Every test in
here is a version of that sentence.
"""
from helpers import RepoCase
from nightwatch.tiers import tier_for


class TierTests(RepoCase):
    CONFIG = {
        "tier1": [{"kinds": ["drift"], "paths": ["docs/**", "**/*.md"]}],
        "never_tier1": ["**/*.env", ".github/**", "deploy/**"],
    }

    def test_a_matching_item_is_tier_1(self):
        d = tier_for({"kind": "drift", "paths": ["docs/api.md"], "tier": 1}, self.cfg)
        self.assertEqual(d.tier, 1)
        self.assertFalse(d.disagrees)
        self.assertTrue(d.may_merge)

    def test_claimed_tier_1_on_a_never_tier1_path_is_demoted(self):
        item = {"kind": "drift", "paths": ["docs/api.md", "deploy/prod.yml"],
                "tier": 1}
        d = tier_for(item, self.cfg)
        self.assertEqual(d.tier, 2)
        self.assertIn("deploy/prod.yml", d.reason)
        self.assertIn("never_tier1", d.reason)
        self.assertTrue(d.disagrees)
        self.assertIn("The claim was ignored", d.warning())

    def test_never_tier1_beats_a_matching_tier1_rule(self):
        # docs/**/*.md matches the tier1 rule; the .env does not, and the deny
        # list is checked first on purpose.
        d = tier_for({"kind": "drift", "paths": ["docs/x.md", "docs/prod.env"],
                      "tier": 1}, self.cfg)
        self.assertEqual(d.tier, 2)
        self.assertIn(".env", d.reason)

    def test_the_wrong_kind_does_not_match(self):
        d = tier_for({"kind": "build", "paths": ["docs/api.md"], "tier": 1}, self.cfg)
        self.assertEqual(d.tier, 2)
        self.assertTrue(d.disagrees)

    def test_an_item_with_no_paths_cannot_reach_a_path_rule(self):
        # Unknown is not the same as allowed.
        d = tier_for({"kind": "drift", "tier": 1}, self.cfg)
        self.assertEqual(d.tier, 2)

    def test_every_path_must_match_not_just_one(self):
        d = tier_for({"kind": "drift", "paths": ["docs/a.md", "src/main.py"],
                      "tier": 1}, self.cfg)
        self.assertEqual(d.tier, 2)

    def test_an_unclaimed_tier_is_not_a_disagreement(self):
        d = tier_for({"kind": "drift", "paths": ["docs/a.md"]}, self.cfg)
        self.assertEqual(d.tier, 1)
        self.assertFalse(d.disagrees)
        self.assertIsNone(d.warning())


class NoRulesTests(RepoCase):
    CONFIG = {"tier1": [], "never_tier1": []}

    def test_with_no_tier1_rules_nothing_auto_merges(self):
        d = tier_for({"kind": "drift", "paths": ["docs/a.md"], "tier": 1}, self.cfg)
        self.assertEqual(d.tier, 2)
        self.assertIn("no tier1 rules", d.reason)


class EmptyRuleTests(RepoCase):
    CONFIG = {"tier1": [{}], "never_tier1": []}

    def test_a_rule_that_constrains_nothing_matches_nothing(self):
        # An empty rule would otherwise make the whole repo auto-mergeable, which
        # is the one mistake this file exists to make impossible.
        d = tier_for({"kind": "drift", "paths": ["src/main.py"], "tier": 1}, self.cfg)
        self.assertEqual(d.tier, 2)


class GlobTests(RepoCase):
    def test_a_single_star_does_not_cross_a_slash(self):
        from nightwatch.tiers import _match
        self.assertTrue(_match("docs/a.md", "docs/*.md"))
        self.assertFalse(_match("docs/deep/a.md", "docs/*.md"))
        self.assertTrue(_match("docs/deep/a.md", "docs/**"))

    def test_a_bare_pattern_also_matches_the_basename(self):
        from nightwatch.tiers import _match
        self.assertTrue(_match("config/prod.env", "*.env"))
        self.assertTrue(_match("prod.env", "*.env"))
