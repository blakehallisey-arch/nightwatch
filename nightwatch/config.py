"""Reads .nightwatch.json and answers the two questions the runner asks first:
am I allowed to run right now, and what am I allowed to merge.

This is code and not a note because both answers have to be the same every time.
A window written in a prompt ("only run at night, please") is a request. A window
read off a file and compared to the clock is a rule, and the runner cannot talk
its way past it.

State lives under .nightwatch/ in the repo you run this in. Nothing is written
anywhere else.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

CONFIG_NAME = ".nightwatch.json"
STATE_DIR = ".nightwatch"

# Every key, with the default that applies when it is missing. The README's
# configuration table is generated from reading this dict — keep them together.
DEFAULTS = {
    # 24h local clock. end < start means the window crosses midnight.
    "window": {"start": "23:00", "end": "07:00"},
    # Two lids, because an item count is not a token count.
    "max_items": 4,
    "max_usd": 8.0,
    # Whatever CLI you drive. {prompt} is replaced with the item's prompt text.
    # A list, not a string, so nothing goes through a shell.
    "agent_command": ["claude", "-p", "{prompt}"],
    # Match rules. An item that matches any of these MAY auto-merge.
    # Each rule: {"kinds": [...], "paths": [...]}. Both optional; a rule with
    # neither matches nothing, on purpose.
    "tier1": [],
    # Path globs that can never be tier 1, whatever a rule above says and
    # whatever the item claims about itself. Checked last and it wins.
    "never_tier1": [".github/**", "**/*.env", "**/secrets/**"],
    "default_branch": "main",
    "branch_prefix": "nightwatch/",
    # The fallback when the agent CLI does not report what it spent. Used per
    # item, and the report says when a number came from here instead of the CLI.
    "est_usd_per_item": 1.0,
    "enabled": True,
}


class ConfigError(Exception):
    """The config is present but unusable. Better to stop than to guess."""


def find_root(start: Path | None = None) -> Path:
    """Walk up for .nightwatch.json, then for .git, else use where you are."""
    here = Path(start or os.getcwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / CONFIG_NAME).exists():
            return candidate
    for candidate in [here, *here.parents]:
        if (candidate / ".git").exists():
            return candidate
    return here


class Config:
    def __init__(self, data: dict, root: Path):
        self.root = Path(root)
        merged = dict(DEFAULTS)
        merged.update(data or {})
        self.data = merged
        self._check()

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, root: Path | None = None) -> "Config":
        root = Path(root) if root else find_root()
        path = root / CONFIG_NAME
        if not path.exists():
            return cls({}, root)
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ConfigError(f"{path} could not be read: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must hold a JSON object")
        return cls(raw, root)

    def _check(self) -> None:
        w = self.data.get("window") or {}
        for key in ("start", "end"):
            _parse_hhmm(w.get(key, DEFAULTS["window"][key]))
        if not isinstance(self.data.get("agent_command"), list) or \
                not self.data["agent_command"]:
            raise ConfigError("agent_command must be a non-empty list of argv parts")
        if not isinstance(self.data.get("tier1"), list):
            raise ConfigError("tier1 must be a list of match rules")
        if not isinstance(self.data.get("never_tier1"), list):
            raise ConfigError("never_tier1 must be a list of path globs")

    # ---------------------------------------------------------------- getters
    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    @property
    def queue_path(self) -> Path:
        return self.state_dir / "queue.json"

    @property
    def fires_path(self) -> Path:
        return self.state_dir / "fires.json"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    # ----------------------------------------------------------- the window
    def window_open(self, now: dt.datetime | None = None) -> tuple[bool, str]:
        """(open?, the sentence that goes in the log either way).

        The sentence is the point. A rail that stands down silently is
        indistinguishable from a rail that crashed, and after a quiet week you
        cannot tell which one you have.
        """
        now = now or dt.datetime.now()
        w = self.data.get("window") or {}
        start = _parse_hhmm(w.get("start", DEFAULTS["window"]["start"]))
        end = _parse_hhmm(w.get("end", DEFAULTS["window"]["end"]))
        cur = now.time()
        text = f"{_fmt(start)}-{_fmt(end)}"
        if start == end:
            return True, f"window {text} covers the whole day"
        if start < end:
            inside = start <= cur < end
        else:
            # Crosses midnight: 23:00-07:00 means late tonight or early tomorrow.
            inside = cur >= start or cur < end
        if inside:
            return True, f"inside the window ({text}), local time {cur.strftime('%H:%M')}"
        return False, (f"outside the window ({text}) — local time "
                       f"{cur.strftime('%H:%M')}")


def _parse_hhmm(value) -> dt.time:
    try:
        hh, mm = str(value).split(":")
        return dt.time(int(hh), int(mm))
    except Exception as exc:  # noqa: BLE001 — any bad shape is the same answer
        raise ConfigError(f"window times must be HH:MM, got {value!r}") from exc


def _fmt(t: dt.time) -> str:
    return t.strftime("%H:%M")


def starter_config() -> dict:
    """What `nightwatch init` writes. Every key present, so nothing is hidden."""
    out = json.loads(json.dumps(DEFAULTS))
    out["tier1"] = [
        {"kinds": ["drift"], "paths": ["docs/**", "**/*.md"]},
    ]
    return out
