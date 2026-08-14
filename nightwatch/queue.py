"""The queue: a JSON file at .nightwatch/queue.json, plus the verbs that move
items through it.

It is code and not a note because of one verb — `next`. Claiming an item has to
be atomic. The obvious version (read the file, pick the top item, write it back)
loses an item the first time two things run at once, and the two things that run
at once are exactly a cron fire and a human at a desk trying the same command to
see what it does. So every mutation takes an exclusive lock on
.nightwatch/queue.lock for the whole read-modify-write, and `next` stamps the
item to `running` inside that lock.

Statuses: queued, running, done, pr-open, blocked, skipped.
Nothing is ever deleted except by `drop`, so a blocked item stays visible.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows; falls back to O_EXCL below
    fcntl = None

STATUSES = ("queued", "running", "done", "pr-open", "blocked", "skipped")
# What a fire is still allowed to pick up.
RUNNABLE = ("queued",)

FIELDS = ("id", "title", "body", "kind", "tier", "status", "created",
          "started", "finished", "result", "pr", "notes")


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


class Queue:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(".lock")

    # ------------------------------------------------------------ plumbing
    def _read(self) -> dict:
        if not self.path.exists():
            return {"updated": None, "items": []}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            # A half-written queue is a real possibility on a laptop that slept.
            # Refusing to guess is the right answer; the caller sees the raise.
            raise
        if not isinstance(data, dict):
            return {"updated": None, "items": []}
        data.setdefault("items", [])
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data["updated"] = now_iso()
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(self.path)

    @contextlib.contextmanager
    def _locked(self, timeout: float = 30.0):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if fcntl is not None:
            fh = open(self.lock_path, "a+")
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                with contextlib.suppress(Exception):
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                fh.close()
            return
        # No fcntl: an O_EXCL sentinel. Cruder, and it can strand a lock if the
        # process is killed, so it is the fallback and not the main path.
        deadline = time.time() + timeout
        while True:  # pragma: no cover — only reached without fcntl
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                break
            except FileExistsError:
                if time.time() > deadline:
                    raise TimeoutError(f"could not lock {self.lock_path}")
                time.sleep(0.05)
        try:
            yield
        finally:  # pragma: no cover
            os.close(fd)
            with contextlib.suppress(OSError):
                os.unlink(self.lock_path)

    # --------------------------------------------------------------- reads
    def items(self) -> list[dict]:
        return self._read().get("items", [])

    def get(self, item_id: str) -> dict | None:
        for item in self.items():
            if item.get("id") == item_id:
                return item
        return None

    def runnable(self) -> list[dict]:
        """Queue order, without stamping anything. What `next` would hand out."""
        return sorted([i for i in self.items() if i.get("status") in RUNNABLE],
                      key=_rank)

    # -------------------------------------------------------------- writes
    def add(self, title: str, body: str = "", kind: str = "task",
            tier: int | None = None, paths: list[str] | None = None,
            est_usd: float | None = None, item_id: str | None = None) -> dict:
        with self._locked():
            data = self._read()
            existing = {i.get("id") for i in data["items"]}
            new_id = item_id or _mint_id(title, existing)
            if new_id in existing:
                raise ValueError(f"an item with id {new_id!r} is already on the queue")
            item = {
                "id": new_id,
                "title": title,
                "body": body or "",
                "kind": kind or "task",
                # None means the item claims nothing, which is the honest
                # default. A claim only exists when somebody made one.
                "tier": int(tier) if tier in (1, 2, "1", "2") else None,
                "status": "queued",
                "created": now_iso(),
                "started": None,
                "finished": None,
                "result": None,
                "pr": None,
                "notes": [],
            }
            if paths:
                item["paths"] = list(paths)
            if est_usd is not None:
                item["est_usd"] = float(est_usd)
            data["items"].append(item)
            self._write(data)
            return item

    def claim_next(self) -> dict | None:
        """Take the top queued item and mark it running. Atomic — see the header.

        Returns a copy of the claimed item, or None when the bench is empty.
        """
        with self._locked():
            data = self._read()
            for item in sorted(data["items"], key=_rank):
                if item.get("status") not in RUNNABLE:
                    continue
                item["status"] = "running"
                item["started"] = now_iso()
                self._write(data)
                return json.loads(json.dumps(item))
            return None

    def finish(self, item_id: str, status: str, result: str = "",
               pr: str | None = None, note: str | None = None) -> dict:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        with self._locked():
            data = self._read()
            for item in data["items"]:
                if item.get("id") != item_id:
                    continue
                item["status"] = status
                item["finished"] = now_iso()
                if result:
                    item["result"] = result
                if pr:
                    item["pr"] = pr
                if note:
                    item.setdefault("notes", []).append(f"{now_iso()} {note}")
                self._write(data)
                return json.loads(json.dumps(item))
            raise KeyError(item_id)

    def note(self, item_id: str, text: str) -> None:
        with self._locked():
            data = self._read()
            for item in data["items"]:
                if item.get("id") == item_id:
                    item.setdefault("notes", []).append(f"{now_iso()} {text}")
                    self._write(data)
                    return
            raise KeyError(item_id)

    def drop(self, item_id: str) -> dict:
        with self._locked():
            data = self._read()
            keep, gone = [], None
            for item in data["items"]:
                if item.get("id") == item_id and gone is None:
                    gone = item
                else:
                    keep.append(item)
            if gone is None:
                raise KeyError(item_id)
            data["items"] = keep
            self._write(data)
            return gone

    def release_running(self, reason: str) -> int:
        """Put anything still `running` back on the bench.

        Called after a fire exits. An item left stamped `running` by a session
        that died is wedged forever otherwise — it is not queued, so nothing
        picks it up, and it is not blocked, so nothing reports it.
        """
        with self._locked():
            data = self._read()
            count = 0
            for item in data["items"]:
                if item.get("status") == "running":
                    item["status"] = "queued"
                    item.setdefault("notes", []).append(f"{now_iso()} {reason}")
                    count += 1
            if count:
                self._write(data)
            return count


def _rank(item: dict) -> tuple:
    """Queue order: first in, first out. That is the whole rule.

    An earlier version sorted tier 2 ahead of tier 1, on the theory that the work
    a human has to read is worth reaching before the chores. It was wrong for a
    dull reason: the only tier available at sort time is the one the ITEM claims,
    and this repo's whole position is that the claim is not trusted. Sorting on
    it would let whatever fills the queue decide what gets reached before the lid
    trips, which is most of the decision.

    So: the order you see in `list` is the order that runs. If you want the
    important thing first, add it first.
    """
    return (str(item.get("created") or ""),)


def _mint_id(title: str, existing: set) -> str:
    words = [w for w in "".join(c if c.isalnum() else "-"
                                for c in title.lower()).split("-") if w]
    stem, out = "", []
    for w in words:  # whole words only — a truncated one reads like a typo
        if len(stem) + len(w) + 1 > 32:
            break
        out.append(w)
        stem = "-".join(out)
    stem = stem or "item"
    day = dt.date.today().strftime("%m%d")
    base = f"{day}-{stem}"
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"
