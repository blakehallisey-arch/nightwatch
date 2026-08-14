# nightwatch

The run rail for unattended coding-agent work. You hand it a queue; it works the
items one at a time inside a time window, with a spend lid, and writes down what
actually happened.

## The problem

Someone ran an agent rail overnight against a real repo for eight nights. The
rail kept its own record of what it had done, and that record went stale every
time a write got blocked — the session did the work, the recorder call failed,
and nothing noticed. So the morning board would show three items still
"building" that had in fact been built, PR'd and merged hours earlier. Then it
got worse: the rail brakes when too many PRs are waiting on a human, and it
counted those PRs off its own record, so it sat refusing new work over work that
had already merged. Nobody was lying. The rail was reading itself.

The fix was to stop believing the rail about itself and read git instead.
That is the fifth of the five things below, and it is the reason this exists as
a tool rather than as a shell script.

## Install

```
git clone https://github.com/blakehallisey-arch/nightwatch && cd nightwatch
./install.sh          # writes a 3-line shim to ~/.local/bin/nightwatch
```

Python 3.9+, standard library only. No dependencies, no account, no network
calls except the `gh` and `git` you already have. `gh` is optional; without it
the report says the cross-check was skipped rather than pretending.

## What it looks like

Three items on the queue. One is a doc fix, one claims tier 1 while touching a
`.env`, one is a feature. It is 10am and the window is 23:00-07:00:

```
$ nightwatch run --dry-run

fire-20260814-101317: stood down — outside the window (23:00-07:00) — local time 10:13
```

That is the whole point of the window: a silent night has a reason attached to
it. Force it to see the plan:

```
$ nightwatch run --dry-run --force

note: outside the window (23:00-07:00) — local time 10:13 — running anyway because --force was passed
fire-20260814-101317  night mode  DRY RUN
  outside the window (23:00-07:00) — local time 10:13
  lids: 4 items, $8.00 estimated

  [1] 0814-fix-the-dead-link-in-docs  Fix the dead link in docs/install.md
      tier 1 — kind 'drift' and every path under docs/**, **/*.md
      branch nightwatch/0814-fix-the-dead-link-in-docs
      on success: merge into main
      estimate $1.00  (running total $1.00)
      log .nightwatch/logs/fire-20260814-101317/0814-fix-the-dead-link-in-docs.log
      env UNATTENDED_RUN=1 NIGHTWATCH_MAY_MERGE=1
      run claude -p "You are running unattended under nightwatch.  Item 0814-fix-the-dea..."

  [2] 0814-rotate-the-deploy-key-reference  Rotate the deploy key reference
      tier 2 — deploy/keys.env is on never_tier1 (**/*.env)
      WARNING item claimed tier 1 — derived tier 2 (deploy/keys.env is on never_tier1 (**/*.env)). The claim was ignored.
      branch nightwatch/0814-rotate-the-deploy-key-reference
      on success: open a PR and stop
      estimate $1.00  (running total $2.00)
      log .nightwatch/logs/fire-20260814-101317/0814-rotate-the-deploy-key-reference.log
      env UNATTENDED_RUN=1 NIGHTWATCH_MAY_MERGE=0
      run claude -p "You are running unattended under nightwatch.  Item 0814-rotate-the-..."

  [3] 0814-add-a-since-flag-to-the-export  Add a --since flag to the export command
      tier 2 — no tier1 rule matched
      branch nightwatch/0814-add-a-since-flag-to-the-export
      on success: open a PR and stop
      estimate $3.50  (running total $5.50)
      log .nightwatch/logs/fire-20260814-101317/0814-add-a-since-flag-to-the-export.log
      env UNATTENDED_RUN=1 NIGHTWATCH_MAY_MERGE=0
      run claude -p "You are running unattended under nightwatch.  Item 0814-add-a-since..."

stop: the queue is empty
```

Then a real fire, and the report. For this capture `agent_command` was set to
`/bin/echo`, so the only thing invoked was echo — which exits 0 without ever
reporting back, and that is the case worth showing:

```
$ nightwatch run --force

note: outside the window (23:00-07:00) — local time 10:13 — running anyway because --force was passed
0814-fix-the-dead-link-in-docs: blocked — the agent exited 0 without reporting
0814-rotate-the-deploy-key-reference: item claimed tier 1 — derived tier 2 (deploy/keys.env is on never_tier1 (**/*.env)). The claim was ignored.
0814-rotate-the-deploy-key-reference: blocked — the agent exited 0 without reporting

$ nightwatch report

open PRs waiting on you: 1
  0814-add-a-since-flag-to-the-export  Add a --since flag to the export command  [#41]

cross-check: gh failed (failed to run git: fatal: not a git repository (or any of the parent directories): .git), so the cross-check was skipped

fire-20260814-101317  night mode
  blocked   0814-fix-the-dead-link-in-docs  Fix the dead link in docs/install.md
            tier 1 — kind 'drift' and every path under docs/**, **/*.md
            the agent exited 0 without reporting
            $1.00 (estimate from config)
  blocked   0814-rotate-the-deploy-key-reference  Rotate the deploy key reference
            tier 2 — deploy/keys.env is on never_tier1 (**/*.env)
            WARNING item claimed tier 1 — derived tier 2 (deploy/keys.env is on never_tier1 (**/*.env)). The claim was ignored.
            the agent exited 0 without reporting
            $1.00 (estimate from config)
  spent $2.00 of $8.00
  stopped: item lid: 2 item(s) is the limit for one fire
```

Two items ran and the third never started, because `max_items` was 2. Both
landed blocked with a reason. Neither vanished. The full capture, including the
config and the queue that produced it, is in `examples/`.

## How it works

Five things, and each one is enforced in the runner rather than asked for in the
agent's prompt. A prompt is a request; a check against the clock is a rule.

**1. A window.** `window: {start, end}` in 24h local time, and `end` before
`start` means it crosses midnight. Outside it, the fire stands down and writes
the reason into the record: `stood down — outside the window (23:00-07:00) —
local time 10:13`. A quiet night with a reason attached is a different thing
from a quiet night, and after a week you can tell them apart.

**2. A lid. Two of them.** `max_items` per fire, and `max_usd` estimated spend.
Both, because an item count is not a token count — the queue tends to sort the
most expensive work to the top, so "two items" can be the two priciest on the
board. The spend lid is checked twice: before an item starts, against its
estimate, and after it finishes, against what the agent CLI said it actually
spent. An item the lid refuses goes straight back on the bench as `queued`.

**3. Two tiers.** Tier 1 may auto-merge. Tier 2 stops at an open PR for a human
to read. **The tier is re-derived by nightwatch from the config, never taken
from what the item claims about itself. The thing that proposes work is not the
thing that authorizes it.** An item arrives carrying `"tier": 1` and that number
is treated as a claim: `tier_for()` re-derives from the config every time the
item is looked at, and when the two differ it prints the disagreement — a
proposer that keeps claiming tier 1 on paths it may not touch is itself a
finding. `never_tier1` path globs are checked first and win over everything.

**4. A day mode.** `--day` runs the same queue with the same items and merges
nothing, because you are at your desk and possibly in the same files. It is
enforced in `run.py`: in day mode the merge call is not reached, and a tier-1
item that finishes is written back as `pr-open` with the reason. The agent also
sees `NIGHTWATCH_DAY=1`, but that is a courtesy, not the mechanism.

**5. An honest log.** `nightwatch report` reads `gh pr list` for what is open and
`git log` on the default branch for what merged, and compares both against its
own record. When they disagree it prints both and says they disagree. It does
not pick a winner — picking a winner is how you get a confident wrong answer.
If `gh` is missing, it says the cross-check was skipped.

**What it cannot see.** Whether the work is any good. It reads an exit code and
whatever the CLI printed about cost, and that is all it has. If the agent exits
without calling `nightwatch done` or `nightwatch block`, the item is recorded
`blocked` with the exit code — never silently dropped, because a vanished item
is the failure this whole thing exists to make visible.

**Where the state lives.** `.nightwatch/` in the repo you run it in, and nowhere
else. `queue.json`, `fires.json`, `logs/<fire-id>/<item-id>.log`, and a lock
file. `nightwatch init` adds the logs to `.gitignore`. The queue is small and
readable and is meant to be committed if you want the history.

**The verbs.**

```
nightwatch init                                  starter config + .nightwatch/
nightwatch add "<title>" --kind K --paths ...    put an item on the queue
nightwatch next                                  claim the top item (atomic)
nightwatch done <id> [--pr N] [--result "..."]   record it finished
nightwatch block <id> "<reason>"                 record why it could not be done
nightwatch list [--json]                         the whole queue, with derived tiers
nightwatch drop <id>                             remove an item
nightwatch run [--day] [--dry-run] [--force]     one fire
nightwatch report [--history] [--json] [--html PATH]
```

`next` takes an exclusive lock for the whole read-modify-write, so a cron fire
and a human at a desk running the same command never get the same item. The
test for that spawns six real processes, not six threads.

Cron it however you cron things:

```
0 * * * *  cd /path/to/repo && /home/you/.local/bin/nightwatch run
```

The window does the gating, so an hourly cron is fine — outside the window each
fire stands down in about a millisecond and logs why.

## Configuration

One file, `.nightwatch.json`, at the repo root. `nightwatch init` writes it with
every key present.

| key | default | what it does |
|---|---|---|
| `window` | `{"start": "23:00", "end": "07:00"}` | 24h local. `end` < `start` crosses midnight. Equal start and end means always open. |
| `max_items` | `4` | items per fire |
| `max_usd` | `8.0` | estimated spend per fire. `0` turns the spend lid off. |
| `agent_command` | `["claude", "-p", "{prompt}"]` | whatever CLI you drive. A list, so nothing goes through a shell. `{prompt}`, `{id}` and `{branch}` are substituted. |
| `tier1` | `[]` | match rules. Each `{"kinds": [...], "paths": [...]}`; an item matches if its kind is listed AND every path it names falls under one of the globs. Empty list means nothing ever auto-merges. |
| `never_tier1` | `[".github/**", "**/*.env", "**/secrets/**"]` | path globs that can never be tier 1, whatever a rule above says. Checked first. |
| `default_branch` | `"main"` | what a tier-1 item merges into, and what `git log` is read from |
| `branch_prefix` | `"nightwatch/"` | branch per item: `nightwatch/<item-id>` |
| `est_usd_per_item` | `1.0` | the spend lid's fallback when an item carries no `est_usd` and the CLI reports no cost. The report says when a number came from here. |
| `enabled` | `true` | `false` and every fire stands down saying so |

In globs, `*` stops at a slash and `**` does not. A pattern with no slash also
matches the basename, so `*.env` catches `config/prod.env`. `fnmatch` was the
obvious choice and it is wrong here — its `*` crosses directory separators, and
on a list whose job is to deny, matching more than it reads is not the safe
direction of wrong.

The environment the agent sees: `UNATTENDED_RUN=1` always, `NIGHTWATCH_DAY=1` in
day mode, plus `NIGHTWATCH_FIRE`, `NIGHTWATCH_ITEM`, `NIGHTWATCH_TIER`,
`NIGHTWATCH_BRANCH` and `NIGHTWATCH_MAY_MERGE`. A write-time policy layer reads
those.

## What this is not

- **`.nightwatch.json` is executable input.** `agent_command` is the command
  nightwatch runs for every item on the queue. In a repo you cloned, that is
  a command a stranger picked, about to run unattended on your machine. It is
  passed as an argument list rather than through a shell, and `--dry-run`
  prints it without running anything — use that first on any repo you did not
  write. Content from outside is data, not instructions, and this tool's own
  config counts as outside when the repo is not yours.
- **It does not decide what the agent may write.** nightwatch schedules; it does
  not police the edits. That is [curfew](https://github.com/blakehallisey-arch/curfew),
  which denies at write time by rule. `UNATTENDED_RUN=1` is the flag curfew reads.
- **It does not review the work.** A tier-1 merge means the config said this
  shape of change may land unattended. It does not mean the change is correct.
- **The spend lid is an estimate and it will drift.** The number comes from the
  agent CLI's own reporting, in whatever shape that CLI uses. When the CLI says
  nothing, nightwatch falls back to `est_usd_per_item` and labels it. Treat the
  lid as a brake, not as a budget.
- **It is a single-machine rail, not a distributed job queue.** One JSON file,
  one lock, one repo. Two machines pointed at the same checkout is not a case it
  handles.
- **It does not run your agent's git for it.** The agent commits on its branch.
  nightwatch only merges, and only tier 1, and only at night.

## Part of a family

Six small tools for when an AI coding agent does the work and a human is not
watching every step.

| repo | one line |
|---|---|
| [curfew](https://github.com/blakehallisey-arch/curfew) | write-time policy for an unattended agent — deny by rule, not by prompt |
| [breaker](https://github.com/blakehallisey-arch/breaker) | stops a session that is spinning, spreading, or inventing work |
| [shipgate](https://github.com/blakehallisey-arch/shipgate) | will not let a merge through until the checks it actually needs have run |
| nightwatch | the run rail — a queue, a budget lid, a window, and an honest log |
| [draftdiff](https://github.com/blakehallisey-arch/draftdiff) | learns your voice from the edits you make before you hit send |
| [ledger](https://github.com/blakehallisey-arch/ledger) | gives stateless agents a memory of what you did with their advice |
