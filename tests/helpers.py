"""Shared scaffolding. Nothing here ever invokes a real agent CLI or a real git.

Every test builds a throwaway repo in a temp dir, writes a config into it, and
replaces nightwatch.run.invoke_agent and nightwatch.run.git with recorders.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nightwatch import run as run_mod  # noqa: E402
from nightwatch.config import Config  # noqa: E402
from nightwatch.queue import Queue  # noqa: E402


class FakeAgent:
    """Stands in for the agent CLI. Records calls, reports whatever you set."""

    def __init__(self, queue: Queue, behaviour=None, cost=None, exit_code=0):
        self.queue = queue
        self.behaviour = behaviour or (lambda item_id: ("done", None))
        self.cost = cost
        self.exit_code = exit_code
        self.calls: list[dict] = []

    def __call__(self, argv, env, cwd, log_path, timeout=None):
        item_id = env.get("NIGHTWATCH_ITEM")
        self.calls.append({"argv": argv, "env": env, "item": item_id,
                           "cwd": str(cwd), "log": str(log_path)})
        outcome = self.behaviour(item_id)
        if outcome is not None:
            status, pr = outcome
            if status == "crash":
                pass  # report nothing at all, like a session that died
            elif status == "blocked":
                self.queue.finish(item_id, "blocked", result="the fake agent said no")
            else:
                self.queue.finish(item_id, status, result="the fake agent did it",
                                  pr=pr)
        out = "fake agent ran\n"
        if self.cost is not None:
            out += json.dumps({"total_cost_usd": self.cost}) + "\n"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(out)
        return self.exit_code, out


class FakeGit:
    def __init__(self, code=0):
        self.code = code
        self.calls: list[list[str]] = []

    def __call__(self, args, cwd):
        self.calls.append(list(args))
        return self.code, "fake git\n"


class RepoCase(unittest.TestCase):
    CONFIG: dict = {}

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="nightwatch-test-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        data = {
            "window": {"start": "00:00", "end": "00:00"},   # always open
            "max_items": 10,
            "max_usd": 0,                                    # no spend lid
            "agent_command": ["fake-agent", "{prompt}"],
            "tier1": [{"kinds": ["drift"], "paths": ["docs/**"]}],
            "never_tier1": ["**/*.env", ".github/**"],
            "default_branch": "main",
            "est_usd_per_item": 1.0,
        }
        data.update(self.CONFIG)
        (self.root / ".nightwatch.json").write_text(json.dumps(data, indent=2))
        self.cfg = Config.load(self.root)
        self.queue = Queue(self.cfg.queue_path)
        self.printed: list[str] = []

    def out(self, *parts):
        self.printed.append(" ".join(str(p) for p in parts))

    def printed_text(self) -> str:
        return "\n".join(self.printed)

    def patch_agent(self, agent):
        real = run_mod.invoke_agent
        run_mod.invoke_agent = agent
        self.addCleanup(lambda: setattr(run_mod, "invoke_agent", real))
        return agent

    def patch_git(self, git):
        real = run_mod.git
        run_mod.git = git
        self.addCleanup(lambda: setattr(run_mod, "git", real))
        return git
