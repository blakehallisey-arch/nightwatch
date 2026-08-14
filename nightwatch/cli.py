"""The command line. One verb per thing you actually do.

Exit codes: 0 fine, 1 error, 2 stop and look up (a disagreement between the
record and the repo, or a config that cannot be read).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import report as report_mod
from . import tiers
from .config import CONFIG_NAME, Config, ConfigError, find_root, starter_config
from .queue import Queue
from .run import Fire

GITIGNORE_LINES = [
    "# nightwatch keeps its state here. Logs are noise; the rest is small.",
    ".nightwatch/logs/",
    ".nightwatch/*.lock",
    ".nightwatch/*.tmp",
]


def _cfg(args) -> Config:
    root = Path(args.root).resolve() if getattr(args, "root", None) else find_root()
    return Config.load(root)


# --------------------------------------------------------------------- init
def cmd_init(args) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    cfg_path = root / CONFIG_NAME
    if cfg_path.exists() and not args.force:
        print(f"{cfg_path} already exists — nothing changed (--force to overwrite)")
        return 0
    cfg_path.write_text(json.dumps(starter_config(), indent=2) + "\n")

    state = root / ".nightwatch"
    (state / "logs").mkdir(parents=True, exist_ok=True)

    gi = root / ".gitignore"
    have = gi.read_text().splitlines() if gi.exists() else []
    missing = [line for line in GITIGNORE_LINES if line not in have]
    if missing:
        with gi.open("a") as fh:
            if have and have[-1].strip():
                fh.write("\n")
            fh.write("\n".join(missing) + "\n")

    print(f"wrote {cfg_path.relative_to(root)}")
    print(f"made  .nightwatch/logs/")
    print(f"added {len(missing)} line(s) to .gitignore")
    print()
    print("Next: nightwatch add \"the thing you want done\" --kind drift "
          "--paths docs/thing.md")
    print("Then: nightwatch run --dry-run")
    return 0


# -------------------------------------------------------------------- queue
def cmd_add(args) -> int:
    cfg = _cfg(args)
    q = Queue(cfg.queue_path)
    item = q.add(title=args.title, body=args.body or "", kind=args.kind,
                 tier=args.tier, paths=args.paths, est_usd=args.est_usd,
                 item_id=args.id)
    decision = tiers.tier_for(item, cfg)
    print(f"queued {item['id']}")
    print(f"  derived tier {decision.tier} — {decision.reason}")
    warn = decision.warning()
    if warn:
        print(f"  WARNING {warn}")
    return 0


def cmd_next(args) -> int:
    cfg = _cfg(args)
    item = Queue(cfg.queue_path).claim_next()
    if item is None:
        print("nothing queued", file=sys.stderr)
        return 1
    decision = tiers.tier_for(item, cfg)
    item["tier_derived"] = decision.tier
    item["tier_reason"] = decision.reason
    print(json.dumps(item, indent=2))
    return 0


def cmd_done(args) -> int:
    cfg = _cfg(args)
    q = Queue(cfg.queue_path)
    item = q.get(args.id)
    if item is None:
        print(f"no queue item with id {args.id!r}", file=sys.stderr)
        return 1
    decision = tiers.tier_for(item, cfg)
    # `done` is a claim of completion, not a claim of authority. Whether it can
    # merge is still nightwatch's call, made from the config, and the runner
    # makes it. Here we only record.
    status = "done" if decision.tier == 1 else "pr-open"
    if args.pr:
        status = "pr-open"
    q.finish(args.id, status, result=args.result or "", pr=args.pr)
    print(f"{args.id} -> {status}")
    if status == "pr-open" and not args.pr:
        print("  no PR number recorded — the report cannot cross-check this one")
    return 0


def cmd_block(args) -> int:
    cfg = _cfg(args)
    q = Queue(cfg.queue_path)
    if q.get(args.id) is None:
        print(f"no queue item with id {args.id!r}", file=sys.stderr)
        return 1
    q.finish(args.id, "blocked", result=args.reason)
    print(f"{args.id} -> blocked: {args.reason}")
    return 0


def cmd_list(args) -> int:
    cfg = _cfg(args)
    items = Queue(cfg.queue_path).items()
    if args.json:
        rows = []
        for item in items:
            d = tiers.tier_for(item, cfg)
            rows.append({**item, "tier_derived": d.tier, "tier_reason": d.reason,
                         "tier_disagreement": d.warning()})
        print(json.dumps(rows, indent=2))
        return 0
    if not items:
        print("the queue is empty")
        return 0
    for item in items:
        d = tiers.tier_for(item, cfg)
        print(f"{item.get('status'):<9} {item.get('id'):<28} tier {d.tier}  "
              f"{item.get('title')}")
        warn = d.warning()
        if warn:
            print(f"          WARNING {warn}")
    return 0


def cmd_drop(args) -> int:
    cfg = _cfg(args)
    try:
        gone = Queue(cfg.queue_path).drop(args.id)
    except KeyError:
        print(f"no queue item with id {args.id!r}", file=sys.stderr)
        return 1
    print(f"dropped {gone.get('id')} ({gone.get('status')})")
    return 0


# ---------------------------------------------------------------------- run
def cmd_run(args) -> int:
    cfg = _cfg(args)
    fire = Fire(cfg, day=args.day, dry_run=args.dry_run, force=args.force)
    entry = fire.run()
    if args.json:
        print(json.dumps(entry, indent=2))
    return 0


# ------------------------------------------------------------------- report
def cmd_report(args) -> int:
    cfg = _cfg(args)
    if args.html:
        out = Path(args.html)
        out.write_text(report_mod.html_report(cfg, history=True))
        print(f"wrote {out}")
        return 0
    if args.json:
        sys.stdout.write(report_mod.json_report(cfg, history=args.history))
    else:
        sys.stdout.write(report_mod.text_report(cfg, history=args.history))
    check = report_mod.cross_check(cfg)
    return 2 if check["disagreements"] else 0


# --------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nightwatch",
        description="The run rail for unattended agent work: a queue, a window, "
                    "two lids, two tiers, and a log that reads git instead of "
                    "itself.")
    p.add_argument("--root", help="repo root (default: walk up for "
                                  ".nightwatch.json, then .git)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="write a starter config and .nightwatch/")
    s.add_argument("--force", action="store_true", help="overwrite an existing config")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("add", help="put an item on the queue")
    s.add_argument("title")
    s.add_argument("--body", default="", help="the brief handed to the agent")
    s.add_argument("--kind", default="task", help="free text; tier1 rules match on it")
    s.add_argument("--tier", type=int, choices=(1, 2), default=None,
                   help="what the item CLAIMS. nightwatch re-derives it anyway, "
                        "and prints a warning when the two differ. Leave it off "
                        "and the item claims nothing.")
    s.add_argument("--paths", nargs="*", default=None,
                   help="paths this item will touch — what the tier rules match")
    s.add_argument("--est-usd", dest="est_usd", type=float, default=None,
                   help="cost estimate used by the spend lid before the item runs")
    s.add_argument("--id", default=None, help="set the id instead of minting one")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("next", help="claim the top item (atomic) and print it")
    s.set_defaults(fn=cmd_next)

    s = sub.add_parser("done", help="record an item as finished")
    s.add_argument("id")
    s.add_argument("--pr", default=None, help="the PR number it opened")
    s.add_argument("--result", default="", help="one line on what happened")
    s.set_defaults(fn=cmd_done)

    s = sub.add_parser("block", help="record an item as blocked, with the reason")
    s.add_argument("id")
    s.add_argument("reason")
    s.set_defaults(fn=cmd_block)

    s = sub.add_parser("list", help="the whole queue")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("drop", help="remove an item")
    s.add_argument("id")
    s.set_defaults(fn=cmd_drop)

    s = sub.add_parser("run", help="one fire: work the queue until a lid trips")
    s.add_argument("--day", action="store_true",
                   help="day mode — same queue, nothing merges")
    s.add_argument("--dry-run", action="store_true",
                   help="print exactly what would run, invoke nothing")
    s.add_argument("--force", action="store_true",
                   help="run outside the window because you said so")
    s.add_argument("--json", action="store_true", help="print the fire record")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("report", help="what happened, cross-checked against git")
    s.add_argument("--history", action="store_true", help="every fire, newest first")
    s.add_argument("--json", action="store_true")
    s.add_argument("--html", metavar="PATH",
                   help="write a self-contained HTML page instead")
    s.set_defaults(fn=cmd_report)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"nightwatch: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("nightwatch: interrupted", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — one place to fail loudly
        print(f"nightwatch: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
