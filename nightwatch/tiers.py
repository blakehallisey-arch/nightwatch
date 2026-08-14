"""Decides, for one queue item, whether it may auto-merge (tier 1) or must stop
at an open PR for a human to read (tier 2).

The whole reason this is a module and not a field on the item: the thing that
PROPOSES work is not the thing that AUTHORIZES it. An item arrives carrying
`"tier": 1`, and that number is treated as a claim, never as a fact. tier_for()
re-derives the tier from the config every time the item is looked at, and when
the two disagree that disagreement gets printed — a proposer that keeps claiming
tier 1 on paths it is not allowed to touch is itself a finding.

Order of decision, and the order matters:
  1. never_tier1 path globs  -> tier 2, always, no argument
  2. any tier1 rule matches  -> tier 1
  3. everything else         -> tier 2
"""
from __future__ import annotations

import re
from typing import NamedTuple


class Decision(NamedTuple):
    tier: int
    reason: str
    claimed: int | None
    disagrees: bool

    @property
    def may_merge(self) -> bool:
        return self.tier == 1

    def warning(self) -> str | None:
        """The line to log when the item's claim and the derived tier differ."""
        if not self.disagrees:
            return None
        return (f"item claimed tier {self.claimed} — derived tier {self.tier} "
                f"({self.reason}). The claim was ignored.")


def item_paths(item: dict) -> list[str]:
    """The paths an item says it will touch. Empty is normal and means unknown."""
    raw = item.get("paths") or item.get("files") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(p).replace("\\", "/").strip().lstrip("./") for p in raw if str(p).strip()]


def tier_for(item: dict, config) -> Decision:
    """Re-derive the tier. `config` is a Config or any dict-alike with .get()."""
    claimed = item.get("tier")
    claimed = int(claimed) if claimed in (1, 2, "1", "2") else None

    paths = item_paths(item)
    kind = str(item.get("kind") or "").strip().lower()

    for pattern in config.get("never_tier1", []) or []:
        hit = _first_match(paths, pattern)
        if hit:
            return _decide(2, f"{hit} is on never_tier1 ({pattern})", claimed)

    for rule in config.get("tier1", []) or []:
        ok, why = _rule_matches(rule, kind, paths)
        if ok:
            return _decide(1, why, claimed)

    if not (config.get("tier1") or []):
        return _decide(2, "no tier1 rules are configured, so nothing auto-merges", claimed)
    return _decide(2, "no tier1 rule matched", claimed)


def _decide(tier: int, reason: str, claimed: int | None) -> Decision:
    return Decision(tier, reason, claimed, claimed is not None and claimed != tier)


def _rule_matches(rule: dict, kind: str, paths: list[str]) -> tuple[bool, str]:
    if not isinstance(rule, dict):
        return False, ""
    kinds = [str(k).strip().lower() for k in (rule.get("kinds") or [])]
    globs = rule.get("paths") or []
    if not kinds and not globs:
        # A rule that constrains nothing would make everything tier 1. Refuse it
        # rather than quietly opening the gate.
        return False, ""

    if kinds and kind not in kinds:
        return False, ""

    if globs:
        if not paths:
            # The rule is about paths and the item named none. Unknown is not
            # the same as allowed.
            return False, ""
        for p in paths:
            if not any(_match(p, g) for g in globs):
                return False, ""
        where = ", ".join(globs)
        if kinds:
            return True, f"kind '{kind}' and every path under {where}"
        return True, f"every path under {where}"

    return True, f"kind '{kind}' is on the tier1 list"


def _first_match(paths: list[str], pattern: str) -> str | None:
    for p in paths:
        if _match(p, pattern):
            return p
    return None


_CACHE: dict[str, "re.Pattern[str]"] = {}


def _match(path: str, pattern: str) -> bool:
    """Glob match where `*` stops at a slash and `**` does not.

    fnmatch was the obvious choice and it is wrong here: its `*` crosses
    directory separators, so `*.env` would match `docs/notes.env` and, worse,
    `docs/*` would match the whole tree. On a list whose job is to deny, a
    pattern that matches more than it reads is not the safe direction of wrong.
    """
    pattern = str(pattern).replace("\\", "/").strip()
    if not pattern:
        return False
    rx = _CACHE.get(pattern)
    if rx is None:
        rx = re.compile("^" + _translate(pattern) + "$")
        _CACHE[pattern] = rx
    if rx.match(path):
        return True
    # A bare pattern with no slash also matches the basename, so "*.env"
    # catches "config/prod.env" the way a reader expects it to.
    if "/" not in pattern:
        return bool(rx.match(path.rsplit("/", 1)[-1]))
    return False


def _translate(pattern: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)
