"""The fire: take items off the queue one at a time until a lid trips, and
write down what actually happened.

Four things live here and all four are enforced in this file rather than asked
for in the agent's prompt, because a prompt is a request:

  the window   outside it, the fire stands down and RECORDS THE REASON.
  the lids     a count lid and a spend lid, checked before and after each item.
  the tier     re-derived per item from the config (see tiers.py), never taken
               from what the item claims.
  day mode     --day means nothing merges, full stop. The flag is read here, and
               the merge call is not reached; it is not a note to the agent.

What it cannot see: whether the agent did good work. It reads an exit code and
whatever the CLI printed about cost. That is all. Reviewing is somebody else's
job, and the report says so.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

from . import tiers
from .config import Config
from .queue import Queue, now_iso

# The prompt handed to the agent. Deliberately short: the item's own body is the
# brief, and everything about permissions lives outside the text.
PROMPT = """You are running unattended under nightwatch.

Item {id}: {title}

{body}

Work on branch {branch}. Commit your work there. Do not merge, and do not push
to {default_branch}.

When you are finished, report the outcome:
  nightwatch done {id} --result "<one line on what you did>" [--pr <number>]
If you cannot do it, say why:
  nightwatch block {id} "<reason>"
"""


class Outcome:
    """One item's result inside one fire, as the report will read it."""

    def __init__(self, item, decision, status, result="", pr=None,
                 cost=None, cost_source="", log=None, argv=None, branch=""):
        self.item = item
        self.decision = decision
        self.status = status
        self.result = result
        self.pr = pr
        self.cost = cost
        self.cost_source = cost_source
        self.log = log
        self.argv = argv or []
        self.branch = branch

    def as_dict(self) -> dict:
        return {
            "id": self.item.get("id"),
            "title": self.item.get("title"),
            "kind": self.item.get("kind"),
            "tier": self.decision.tier,
            "tier_claimed": self.decision.claimed,
            "tier_reason": self.decision.reason,
            "tier_disagreement": self.decision.warning(),
            "status": self.status,
            "result": self.result,
            "pr": self.pr,
            "branch": self.branch,
            "cost_usd": self.cost,
            "cost_source": self.cost_source,
            "log": self.log,
            "argv": self.argv,
        }


# ---------------------------------------------------------------- seams
# Both of these are module-level so the tests can replace them. Nothing in the
# test suite is ever allowed to invoke a real agent CLI or a real git.
def invoke_agent(argv: list[str], env: dict, cwd: Path, log_path: Path,
                 timeout: int | None = None) -> tuple[int, str]:
    """Run the agent command. Returns (exit code, combined output)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(argv, cwd=str(cwd), env=env, timeout=timeout,
                              capture_output=True, text=True)
        out = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
    except FileNotFoundError as exc:
        out, code = f"nightwatch: could not run {argv[0]!r} — {exc}\n", 127
    except subprocess.TimeoutExpired:
        out, code = "nightwatch: the agent command timed out\n", 124
    log_path.write_text(out)
    return code, out


def git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd),
                              capture_output=True, text=True, timeout=60)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:  # noqa: BLE001 — no git is a normal state here
        return 127, str(exc)


# ---------------------------------------------------------------- the fire
class Fire:
    def __init__(self, config: Config, day: bool = False, dry_run: bool = False,
                 force: bool = False, now: dt.datetime | None = None,
                 out=print):
        self.cfg = config
        self.day = day
        self.dry_run = dry_run
        self.force = force
        self.now = now or dt.datetime.now()
        self.out = out
        self.queue = Queue(config.queue_path)
        self.id = "fire-" + self.now.strftime("%Y%m%d-%H%M%S")
        self.spent = 0.0
        self.outcomes: list[Outcome] = []
        self.stood_down: str | None = None
        self.stopped_because = ""
        # A dry run walks the same order without stamping anything, so it needs
        # its own memory of what it already "took".
        self._dry_seen: set = set()

    # ------------------------------------------------------------- helpers
    @property
    def log_dir(self) -> Path:
        return self.cfg.logs_dir / self.id

    def _branch(self, item) -> str:
        return f"{self.cfg.get('branch_prefix', 'nightwatch/')}{item.get('id')}"

    def _env(self, item, decision, branch) -> dict:
        env = dict(os.environ)
        env["UNATTENDED_RUN"] = "1"
        env["NIGHTWATCH_FIRE"] = self.id
        env["NIGHTWATCH_ITEM"] = str(item.get("id"))
        env["NIGHTWATCH_TIER"] = str(decision.tier)
        env["NIGHTWATCH_BRANCH"] = branch
        env["NIGHTWATCH_MAY_MERGE"] = "0" if (self.day or decision.tier != 1) else "1"
        if self.day:
            env["NIGHTWATCH_DAY"] = "1"
        return env

    def _argv(self, item, branch) -> list[str]:
        prompt = PROMPT.format(id=item.get("id"), title=item.get("title", ""),
                               body=item.get("body") or "(no further detail)",
                               branch=branch,
                               default_branch=self.cfg.get("default_branch", "main"))
        argv = []
        for part in self.cfg.get("agent_command"):
            argv.append(str(part).replace("{prompt}", prompt)
                        .replace("{id}", str(item.get("id", "")))
                        .replace("{branch}", branch))
        return argv

    def _est(self, item) -> float:
        try:
            return float(item.get("est_usd"))
        except (TypeError, ValueError):
            return float(self.cfg.get("est_usd_per_item", 1.0) or 0.0)

    # -------------------------------------------------------------- the run
    def run(self) -> dict:
        if not self.cfg.get("enabled", True):
            return self._stand_down("enabled is false in .nightwatch.json")

        open_now, why = self.cfg.window_open(self.now)
        if not open_now and not self.force:
            return self._stand_down(why)
        if not open_now and self.force:
            self.out(f"note: {why} — running anyway because --force was passed")

        max_items = int(self.cfg.get("max_items", 4) or 0)
        max_usd = float(self.cfg.get("max_usd", 0) or 0)

        if self.dry_run:
            self.out(f"{self.id}  {'day' if self.day else 'night'} mode  DRY RUN")
            self.out(f"  {why}")
            self.out(f"  lids: {max_items} items, ${max_usd:.2f} estimated")
            if self.day:
                self.out("  day mode: nothing merges, every finished item stops "
                         "at an open PR")
            self.out("")

        picked = 0
        while True:
            if max_items and picked >= max_items:
                self.stopped_because = (
                    f"item lid: {max_items} item(s) is the limit for one fire")
                break
            if max_usd and self.spent >= max_usd:
                self.stopped_because = (
                    f"spend lid: ${self.spent:.2f} of ${max_usd:.2f} is spent")
                break

            item = self._peek() if self.dry_run else self.queue.claim_next()
            if item is None:
                self.stopped_because = "the queue is empty"
                break

            decision = tiers.tier_for(item, self.cfg)
            est = self._est(item)
            if max_usd and self.spent + est > max_usd:
                # Stop BEFORE starting, not after overrunning. An item count is
                # not a token count, so the estimate has to get a vote.
                self.stopped_because = (
                    f"spend lid: ${self.spent:.2f} spent and {item['id']} is "
                    f"estimated at ${est:.2f}, over the ${max_usd:.2f} lid")
                if not self.dry_run:
                    # It was claimed a moment ago. Put it straight back rather
                    # than leaving it stamped `running` for nobody.
                    self.queue.release_running(
                        "not started — the spend lid tripped first")
                break

            picked += 1
            if self.dry_run:
                self._describe(item, decision, est, picked)
                self._dry_seen.add(item["id"])
                continue
            self._work(item, decision)

        if self.dry_run:
            self.out(f"stop: {self.stopped_because}")
            return self._record(write=False)

        released = self.queue.release_running("the fire ended before this item reported")
        if released:
            self.out(f"note: {released} item(s) went back on the bench unreported")
        return self._record(write=True)

    def _peek(self):
        for item in self.queue.runnable():
            if item["id"] not in self._dry_seen:
                return item
        return None

    def _describe(self, item, decision, est, n) -> None:
        branch = self._branch(item)
        argv = self._argv(item, branch)
        merge = (decision.tier == 1 and not self.day)
        self.out(f"  [{n}] {item['id']}  {item.get('title')}")
        self.out(f"      tier {decision.tier} — {decision.reason}")
        warn = decision.warning()
        if warn:
            self.out(f"      WARNING {warn}")
        self.out(f"      branch {branch}")
        self.out(f"      on success: {'merge into ' + self.cfg.get('default_branch', 'main') if merge else 'open a PR and stop'}")
        self.out(f"      estimate ${est:.2f}  (running total ${self.spent + est:.2f})")
        self.out(f"      log {self._rel(self.log_dir / (item['id'] + '.log'))}")
        self.out(f"      env UNATTENDED_RUN=1"
                 + (" NIGHTWATCH_DAY=1" if self.day else "")
                 + f" NIGHTWATCH_MAY_MERGE={'1' if merge else '0'}")
        self.out(f"      run {_show(argv)}")
        self.out("")
        self.spent += est

    def _rel(self, p: Path) -> str:
        try:
            return str(Path(p).relative_to(self.cfg.root))
        except ValueError:
            return str(p)

    def _work(self, item, decision) -> None:
        branch = self._branch(item)
        warn = decision.warning()
        if warn:
            self.out(f"{item['id']}: {warn}")
            self.queue.note(item["id"], warn)

        argv = self._argv(item, branch)
        log_path = self.log_dir / f"{item['id']}.log"
        code, output = invoke_agent(argv, self._env(item, decision, branch),
                                    self.cfg.root, log_path)
        cost, source = read_cost(output)
        if cost is None:
            cost, source = self._est(item), "estimate from config"
        self.spent += cost

        # The agent is supposed to have reported via `nightwatch done`/`block`.
        # Read the queue back rather than believing the exit code.
        current = self.queue.get(item["id"]) or item
        status = current.get("status")
        if status == "running":
            # It exited without reporting. That is BLOCKED, never silently
            # dropped — an item that vanishes is the failure this rail exists to
            # stop being invisible.
            reason = (f"the agent exited {code} without reporting"
                      if code else "the agent exited 0 without reporting")
            current = self.queue.finish(item["id"], "blocked", result=reason)
            status = "blocked"

        pr = current.get("pr")
        if status == "done" and decision.tier == 1 and not self.day:
            ok, note = self._merge(branch)
            self.queue.note(item["id"], note)
            if not ok:
                status = "pr-open"
                current = self.queue.finish(item["id"], "pr-open", result=note)
        elif status == "done" and decision.tier == 1 and self.day:
            note = ("day mode: tier 1 but nothing merges while you are at your "
                    "desk — left on the branch")
            self.queue.note(item["id"], note)
            current = self.queue.finish(item["id"], "pr-open", result=note)
            status = "pr-open"
        elif status == "done" and decision.tier == 2:
            note = "tier 2: stops at an open PR for a human to read"
            self.queue.note(item["id"], note)
            current = self.queue.finish(item["id"], "pr-open", result=current.get("result") or note)
            status = "pr-open"

        self.outcomes.append(Outcome(
            item, decision, status, result=current.get("result") or "",
            pr=pr or current.get("pr"), cost=cost, cost_source=source,
            log=self._rel(log_path), argv=argv, branch=branch))
        self.out(f"{item['id']}: {status} — {current.get('result') or ''}".rstrip(" —"))

    def _merge(self, branch: str) -> tuple[bool, str]:
        """Merge a tier-1 branch. Never called in day mode — see run()."""
        if self.day:  # belt and braces; the caller already decided
            return False, "day mode: refused to merge"
        default = self.cfg.get("default_branch", "main")
        code, out = git(["checkout", default], self.cfg.root)
        if code != 0:
            return False, f"could not check out {default}: {out.strip()[:200]}"
        code, out = git(["merge", "--no-ff", branch, "-m",
                         f"nightwatch: merge {branch}"], self.cfg.root)
        if code != 0:
            return False, f"merge of {branch} failed: {out.strip()[:200]}"
        return True, f"tier 1: merged {branch} into {default}"

    # ------------------------------------------------------------ recording
    def _stand_down(self, why: str) -> dict:
        self.stood_down = why
        self.stopped_because = "stood down"
        self.out(f"{self.id}: stood down — {why}")
        return self._record(write=not self.dry_run)

    def _record(self, write: bool) -> dict:
        entry = {
            "id": self.id,
            "started": self.now.replace(microsecond=0).isoformat(),
            "finished": now_iso(),
            "mode": "day" if self.day else "night",
            "dry_run": self.dry_run,
            "stood_down": self.stood_down,
            "stopped_because": self.stopped_because,
            "spent_usd": round(self.spent, 4),
            "max_usd": self.cfg.get("max_usd"),
            "max_items": self.cfg.get("max_items"),
            "items": [o.as_dict() for o in self.outcomes],
        }
        if write:
            append_fire(self.cfg.fires_path, entry)
        return entry


def _show(argv: list[str]) -> str:
    """One readable line for the dry run. Long prompts get elided, not hidden."""
    parts = []
    for a in argv:
        one = a.replace("\n", " ")
        if len(one) > 70:
            one = one[:67] + "..."
        parts.append(one if re.fullmatch(r"[\w./:@=-]+", one) else f'"{one}"')
    return " ".join(parts)


COST_KEYS = ("total_cost_usd", "cost_usd", "total_cost")


def read_cost(output: str) -> tuple[float | None, str]:
    """What the agent CLI said it spent, or None.

    This is the honest weak point of the whole tool. The number comes from the
    CLI's own reporting, in whatever shape that CLI uses; if it reports nothing
    we fall back to the estimate in config and SAY that in the report, because a
    lid enforced against a made-up number is worse than no lid at all.
    """
    text = output or ""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            blob = json.loads(stripped)
            for key in COST_KEYS:
                if isinstance(blob, dict) and key in blob:
                    return float(blob[key]), f"agent CLI ({key})"
        except (ValueError, TypeError):
            pass
    for key in COST_KEYS:
        m = re.search(rf'"{key}"\s*:\s*([0-9]+(?:\.[0-9]+)?)', text)
        if m:
            return float(m.group(1)), f"agent CLI ({key})"
    m = re.search(r"nightwatch-cost:\s*\$?([0-9]+(?:\.[0-9]+)?)", text)
    if m:
        return float(m.group(1)), "agent CLI (nightwatch-cost line)"
    return None, ""


def append_fire(path: Path, entry: dict, keep: int = 60) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"fires": []}
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict) and isinstance(loaded.get("fires"), list):
                data = loaded
        except (OSError, ValueError):
            pass
    data["fires"].insert(0, entry)
    data["fires"] = data["fires"][:keep]
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)
