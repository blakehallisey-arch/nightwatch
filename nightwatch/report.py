"""The report — what happened, read off git and GitHub rather than off the
rail's own record of itself.

This is the file with the incident behind it. A rail that keeps its own log of
what it did will be wrong every time a write got blocked, and it will be wrong
in the most confusing direction: the board says three items are still building,
while those three were built, PR'd and merged hours ago. Then the rail brakes on
its own count of open PRs and refuses new work over work that already landed.

So the report asks two other sources: `gh pr list` for what is actually open,
and `git log` on the default branch for what actually merged. When the record
and the repo disagree, it prints BOTH and says they disagree. It does not pick a
winner — picking a winner is how you get a confident wrong answer.

If `gh` is not installed, the cross-check is skipped and the report says the
cross-check was skipped. It never prints a clean bill of health it did not check.
"""
from __future__ import annotations

import html
import json
import shutil
import subprocess
from pathlib import Path

from .config import Config
from .queue import Queue


# ------------------------------------------------------------------ sources
def gh_available() -> bool:
    return shutil.which("gh") is not None


def gh_open_prs(root: Path) -> tuple[list[dict] | None, str]:
    """(open PRs, note). None means we could not ask — never an empty list."""
    if not gh_available():
        return None, "gh is not installed, so the GitHub cross-check was skipped"
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json",
             "number,title,state", "--limit", "100"],
            cwd=str(root), capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return None, f"gh could not be run ({exc}), so the cross-check was skipped"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        why = detail[-1] if detail else f"exit {proc.returncode}"
        return None, f"gh failed ({why}), so the cross-check was skipped"
    try:
        return json.loads(proc.stdout or "[]"), "GitHub answered"
    except ValueError:
        return None, "gh returned something that was not JSON; cross-check skipped"


def git_merged_subjects(root: Path, branch: str, limit: int = 300) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "log", "--format=%s", f"-n{limit}", branch],
            cwd=str(root), capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


# ------------------------------------------------------------------ reading
def load_fires(cfg: Config) -> list[dict]:
    path = cfg.fires_path
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    fires = data.get("fires") if isinstance(data, dict) else None
    return fires if isinstance(fires, list) else []


def cross_check(cfg: Config) -> dict:
    """What the record says vs what the repo says, and where they differ."""
    q = Queue(cfg.queue_path)
    waiting = [i for i in q.items() if i.get("status") == "pr-open"]
    prs, note = gh_open_prs(cfg.root)
    subjects = git_merged_subjects(cfg.root, cfg.get("default_branch", "main"))
    prefix = cfg.get("branch_prefix", "nightwatch/")

    disagreements: list[str] = []
    matched_numbers: set[str] = set()

    for item in waiting:
        item_id = str(item.get("id"))
        merged = any(f"{prefix}{item_id}" in s for s in subjects)
        if merged:
            disagreements.append(
                f"{item_id}: the record says waiting on you; git says "
                f"{prefix}{item_id} is already merged into "
                f"{cfg.get('default_branch', 'main')}")
            continue
        if prs is None:
            continue
        num = str(item.get("pr") or "").lstrip("#")
        if num:
            if any(str(p.get("number")) == num for p in prs):
                matched_numbers.add(num)
            else:
                disagreements.append(
                    f"{item_id}: the record says PR #{num} is open; GitHub does "
                    f"not list it among the open PRs")
        else:
            title = str(item.get("title") or "")
            hit = next((p for p in prs if title and title in str(p.get("title"))), None)
            if hit:
                matched_numbers.add(str(hit.get("number")))
            else:
                disagreements.append(
                    f"{item_id}: recorded as an open PR but no PR number was "
                    f"written down, so there is nothing to check it against")

    unknown = []
    if prs is not None:
        for p in prs:
            if str(p.get("number")) in matched_numbers:
                continue
            unknown.append(p)

    return {
        "waiting": waiting,
        "github_open": prs,
        "github_note": note,
        "unmatched_open_prs": unknown,
        "disagreements": disagreements,
    }


# ------------------------------------------------------------------- render
def _fire_lines(fire: dict) -> list[str]:
    lines = []
    head = f"{fire.get('id')}  {fire.get('mode', 'night')} mode"
    if fire.get("dry_run"):
        head += "  (dry run)"
    lines.append(head)
    if fire.get("stood_down"):
        lines.append(f"  stood down — {fire['stood_down']}")
        return lines
    items = fire.get("items") or []
    if not items:
        lines.append("  did nothing")
    for it in items:
        lines.append(f"  {it.get('status'):<9} {it.get('id')}  {it.get('title')}")
        lines.append(f"            tier {it.get('tier')} — {it.get('tier_reason')}")
        if it.get("tier_disagreement"):
            lines.append(f"            WARNING {it['tier_disagreement']}")
        if it.get("result"):
            lines.append(f"            {it['result']}")
        cost = it.get("cost_usd")
        if cost is not None:
            lines.append(f"            ${float(cost):.2f} ({it.get('cost_source') or 'unknown source'})")
    lines.append(f"  spent ${float(fire.get('spent_usd') or 0):.2f} of "
                 f"${float(fire.get('max_usd') or 0):.2f}")
    lines.append(f"  stopped: {fire.get('stopped_because')}")
    return lines


def text_report(cfg: Config, history: bool = False) -> str:
    fires = load_fires(cfg)
    check = cross_check(cfg)
    out: list[str] = []

    waiting = check["waiting"]
    out.append(f"open PRs waiting on you: {len(waiting)}")
    for item in waiting:
        pr = f"#{str(item.get('pr')).lstrip('#')}" if item.get("pr") else "no PR number recorded"
        out.append(f"  {item.get('id')}  {item.get('title')}  [{pr}]")
    out.append("")
    out.append(f"cross-check: {check['github_note']}")
    if check["disagreements"]:
        out.append("the record and the repo disagree:")
        for d in check["disagreements"]:
            out.append(f"  {d}")
        out.append("  nightwatch does not pick a winner here. Look at the repo.")
    elif check["github_open"] is not None:
        out.append("  the record and GitHub agree on what is open")
    for p in check["unmatched_open_prs"]:
        out.append(f"  open on GitHub and not in nightwatch's record: "
                   f"#{p.get('number')} {p.get('title')}")
    out.append("")

    if not fires:
        out.append("no fires recorded yet")
        return "\n".join(out) + "\n"

    for fire in (fires if history else fires[:1]):
        out.extend(_fire_lines(fire))
        out.append("")
    if not history and len(fires) > 1:
        out.append(f"{len(fires) - 1} earlier fire(s) — nightwatch report --history")
    return "\n".join(out).rstrip() + "\n"


def json_report(cfg: Config, history: bool = False) -> str:
    fires = load_fires(cfg)
    check = cross_check(cfg)
    return json.dumps({
        "root": str(cfg.root),
        "waiting_on_you": check["waiting"],
        "cross_check": {
            "note": check["github_note"],
            "github_open": check["github_open"],
            "unmatched_open_prs": check["unmatched_open_prs"],
            "disagreements": check["disagreements"],
        },
        "fires": fires if history else fires[:1],
    }, indent=2) + "\n"


# ---------------------------------------------------------------------- html
CSS = """
:root {
  --bg: #fbfbf9; --fg: #1b1b1a; --muted: #6a6a66; --line: #e2e1dc;
  --card: #ffffff; --warn: #8a4b1d; --ok: #2c5c3a;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16181a; --fg: #e8e8e5; --muted: #9a9a95; --line: #2c2f32;
    --card: #1d2023; --warn: #d9a06a; --ok: #7fc08f;
  }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1.25rem 4rem; background: var(--bg); color: var(--fg);
  font: 15px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
main { max-width: 60rem; margin: 0 auto; }
h1 { font-size: 1.35rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
h2 { font-size: 1rem; margin: 2rem 0 .5rem; }
p.sub { color: var(--muted); margin: 0 0 2rem; }
section { border: 1px solid var(--line); background: var(--card); border-radius: 6px;
  padding: 1rem 1.1rem; margin: 0 0 1rem; }
.row { display: flex; gap: .75rem; align-items: baseline; padding: .3rem 0;
  border-top: 1px solid var(--line); flex-wrap: wrap; }
.row:first-of-type { border-top: 0; }
.tag { font-size: .78rem; padding: .05rem .45rem; border: 1px solid var(--line);
  border-radius: 3px; color: var(--muted); white-space: nowrap; }
.why { color: var(--muted); font-size: .86rem; width: 100%; }
.warn { color: var(--warn); }
.ok { color: var(--ok); }
ul { margin: .35rem 0; padding-left: 1.2rem; }
li { margin: .15rem 0; }
footer { color: var(--muted); font-size: .82rem; margin-top: 2.5rem; }
"""


def html_report(cfg: Config, history: bool = True) -> str:
    fires = load_fires(cfg)
    check = cross_check(cfg)
    e = html.escape

    parts = ["<main>", "<h1>nightwatch</h1>",
             f"<p class=\"sub\">{e(str(cfg.root))}</p>"]

    parts.append("<h2>Open PRs waiting on you</h2><section>")
    if not check["waiting"]:
        parts.append("<div class=\"row\">nothing is waiting on you</div>")
    for item in check["waiting"]:
        pr = f"#{str(item.get('pr')).lstrip('#')}" if item.get("pr") else "no PR number recorded"
        parts.append(
            f"<div class=\"row\"><span class=\"tag\">{e(pr)}</span>"
            f"<strong>{e(str(item.get('title')))}</strong>"
            f"<span class=\"tag\">{e(str(item.get('id')))}</span>"
            f"<span class=\"why\">{e(str(item.get('result') or ''))}</span></div>")
    parts.append("</section>")

    parts.append("<h2>Cross-check against the repo</h2><section>")
    parts.append(f"<div class=\"row\">{e(check['github_note'])}</div>")
    if check["disagreements"]:
        parts.append("<div class=\"row warn\">the record and the repo disagree "
                     "— nightwatch does not pick a winner</div><ul>")
        for d in check["disagreements"]:
            parts.append(f"<li class=\"warn\">{e(d)}</li>")
        parts.append("</ul>")
    elif check["github_open"] is not None:
        parts.append("<div class=\"row ok\">the record and GitHub agree on what "
                     "is open</div>")
    for p in check["unmatched_open_prs"]:
        parts.append(f"<div class=\"row warn\">open on GitHub, not in the record: "
                     f"#{e(str(p.get('number')))} {e(str(p.get('title')))}</div>")
    parts.append("</section>")

    parts.append("<h2>Fires, newest first</h2>")
    if not fires:
        parts.append("<section><div class=\"row\">no fires recorded yet</div></section>")
    for fire in (fires if history else fires[:1]):
        parts.append("<section>")
        head = f"{fire.get('id')} &middot; {fire.get('mode', 'night')} mode"
        if fire.get("dry_run"):
            head += " &middot; dry run"
        parts.append(f"<div class=\"row\"><strong>{head}</strong>"
                     f"<span class=\"tag\">${float(fire.get('spent_usd') or 0):.2f}"
                     f" of ${float(fire.get('max_usd') or 0):.2f}</span></div>")
        if fire.get("stood_down"):
            parts.append(f"<div class=\"row\">stood down &mdash; "
                         f"{e(str(fire['stood_down']))}</div>")
        for it in fire.get("items") or []:
            parts.append(
                f"<div class=\"row\"><span class=\"tag\">{e(str(it.get('status')))}</span>"
                f"<span class=\"tag\">tier {e(str(it.get('tier')))}</span>"
                f"<strong>{e(str(it.get('title')))}</strong>"
                f"<span class=\"why\">{e(str(it.get('tier_reason') or ''))}</span>")
            if it.get("tier_disagreement"):
                parts.append(f"<span class=\"why warn\">{e(str(it['tier_disagreement']))}</span>")
            if it.get("result"):
                parts.append(f"<span class=\"why\">{e(str(it['result']))}</span>")
            parts.append("</div>")
        parts.append(f"<div class=\"row\"><span class=\"why\">stopped: "
                     f"{e(str(fire.get('stopped_because')))}</span></div>")
        parts.append("</section>")

    parts.append("<footer>Generated by nightwatch. The open-PR list is read from "
                 "GitHub and git, not from nightwatch's own record of what it did."
                 "</footer></main>")

    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>nightwatch</title><style>" + CSS + "</style></head><body>"
            + "\n".join(parts) + "</body></html>\n")
