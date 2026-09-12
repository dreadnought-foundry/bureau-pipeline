# The fleet says "reviewer down" once — the 2026-09-08 outage replayed, and a quiet live sweep

**DRE-3436.** Read by hand on 2026-09-12 between 09:53 and 09:56 PT against a
fresh worktree of `dreadnought-foundry/bureau-pipeline` at `origin/main` and
the live GitHub Actions logs of `dreadnought-foundry/agent-bureau` and
`dreadnought-foundry/bureau-harness`. The proof child of epic
[DRE-3420](https://linear.app/dreadnoughtfoundry/issue/DRE-3420): what
[DRE-3433](https://linear.app/dreadnoughtfoundry/issue/DRE-3433) (the pure
decision and the replay fixture) and
[DRE-3435](https://linear.app/dreadnoughtfoundry/issue/DRE-3435) (the sweep's
backstop) built, observed against real state rather than a green suite.

**Read-only.** Nothing here dispatched a run, moved a card, staged an outage
or touched a secret. Every live line below came from `gh run view --log` on a
scheduled sweep that had already happened, or from a Linear read. The replay
and the guard ran locally in the worktree, on the same commit the live sweep
had just run on.

Times are Pacific (PDT, UTC−7) and labelled, each converted with `zoneinfo`
from the event's own timestamp. Raw UTC appears only inside verbatim command
output.

## The headline

**All four observations the card asks for were made, and all four hold.** The
recorded 2026-09-08 incident replays to exactly one card with the 15:19 PT /
7 runs / 2 repos title and the `native binary not found` line; 31 minutes
later the window is empty. A real scheduled sweep on a quiet morning printed
`nothing to report` in two repositories and filed nothing — no card whose
title starts `Reviewer down since` exists on the board. No live outage
occurred while the card was open. The act-consumer guard answers `OK` against
the console's default branch.

## What is deployed, and where

| Fact | Value |
| --- | --- |
| DRE-3433 reached bureau-pipeline `main` | `1e6939ab7af389dc73f96ce1f1f2a3943e0cdb2f` — merge of PR #367, 2026-09-10 22:39 PT |
| DRE-3435 reached bureau-pipeline `main` | `5fe6771b313ca48be363e2c5431afe013aa2817f` — merge of PR #368, 2026-09-10 23:59 PT |
| `stable` at the time of reading | `991af430928c60d5ba1841bb831061929a2cd02a` (merge of PR #371, 2026-09-11 22:18 PT); contains both merges above (`git merge-base --is-ancestor` → yes for each) |
| The worktree this record's commands ran on | `991af430928c60d5ba1841bb831061929a2cd02a` — the same commit |
| agent-bureau's sweep | `reconcile.yml@refs/tags/stable`, `pipeline_ref: stable` — resolved to `991af43…` in the run read below |
| bureau-harness's sweep | `reconcile.yml@refs/heads/main` — also `991af43…` in the run read below |
| Console's act row (DRE-3429) | agent-bureau PR #2371, merged 2026-09-08 17:47 PT; confirmed live by observation 4 |

So the code that replayed the incident in observation 1 is byte-for-byte the
code the live sweep ran in observation 2.

## 1. The recorded incident replays to ONE card — MET

Run in the worktree at `991af430928c60d5ba1841bb831061929a2cd02a`, 2026-09-12
09:55:24 PT:

```
python3 scripts/reviewer_down.py replay tests/fixtures/reviewer-down-2026-09-08.json --now 2026-09-08T22:49:00Z
```

Output, verbatim (exit 0):

```json
{
  "action": "file",
  "title": "Reviewer down since 15:19 PT — 7 runs, 2 repos",
  "body": "The code reviewer stopped being able to run at 15:19 PT, and it is not one pull request's problem: the runs below crashed the same way across every repository listed. Each of them got its own could-not-run receipt and nothing was rejected — no verdict was written, so no work here has been judged.\n\nThe first crashed run was on agent-bureau#2367 (https://github.com/dreadnought-foundry/agent-bureau/actions/runs/34512000001), and this is the line its log ended on:\n\n```\nReferenceError: Claude Code native binary not found at /home/runner/.local/bin/claude. Please ensure Claude Code is installed via native installer or specify a valid path\n```\n\nThe action reference those runs used: `anthropics/claude-code-action@v1`\n\n**The three usual suspects, in the order worth checking them.** The order is the point: the 2026-09-08 outage (DRE-3416) cost what it cost because the first person to look was sent to the credential, which was the one thing that was not wrong.\n\n1. the vendor action's release moved under us (a floating tag, or a release pulled) —\n   `gh api repos/anthropics/claude-code-action/git/ref/tags/v1`\n2. the fleet's Claude credential is refused —\n   `make cred-doctor --account <account>`\n3. Anthropic is having an incident —\n   `https://status.anthropic.com`\n\n**The runs this card has counted.** One line per run, and the line is the record — the sweep re-reads them rather than re-deriving what it already knew:\n\n- run repo=agent-bureau pr=- at=2026-09-08T22:19:00Z src=linear:DRE-3409:2026-09-08T22:19:00Z\n- run repo=bureau-pipeline pr=#351 at=2026-09-08T22:20:00Z src=https://github.com/dreadnought-foundry/bureau-pipeline/pull/351#issuecomment-3350000001\n- run repo=bureau-pipeline pr=#352 at=2026-09-08T22:23:00Z src=https://github.com/dreadnought-foundry/bureau-pipeline/pull/352#issuecomment-3350000002\n- run repo=agent-bureau pr=- at=2026-09-08T22:24:00Z src=linear:DRE-3411:2026-09-08T22:24:00Z\n- run repo=bureau-pipeline pr=#353 at=2026-09-08T22:26:00Z src=https://github.com/dreadnought-foundry/bureau-pipeline/pull/353#issuecomment-3350000003\n- run repo=agent-bureau pr=- at=2026-09-08T22:31:00Z src=linear:DRE-3412:2026-09-08T22:31:00Z\n- run repo=agent-bureau pr=- at=2026-09-08T22:47:00Z src=linear:DRE-3409:2026-09-08T22:47:00Z\n\nThe sweep closes this card by itself on the first successful verdict posted after it was filed. Nothing else needs to happen here.",
  "lines": [
    "run repo=agent-bureau pr=- at=2026-09-08T22:19:00Z src=linear:DRE-3409:2026-09-08T22:19:00Z",
    "run repo=bureau-pipeline pr=#351 at=2026-09-08T22:20:00Z src=https://github.com/dreadnought-foundry/bureau-pipeline/pull/351#issuecomment-3350000001",
    "run repo=bureau-pipeline pr=#352 at=2026-09-08T22:23:00Z src=https://github.com/dreadnought-foundry/bureau-pipeline/pull/352#issuecomment-3350000002",
    "run repo=agent-bureau pr=- at=2026-09-08T22:24:00Z src=linear:DRE-3411:2026-09-08T22:24:00Z",
    "run repo=bureau-pipeline pr=#353 at=2026-09-08T22:26:00Z src=https://github.com/dreadnought-foundry/bureau-pipeline/pull/353#issuecomment-3350000003",
    "run repo=agent-bureau pr=- at=2026-09-08T22:31:00Z src=linear:DRE-3412:2026-09-08T22:31:00Z",
    "run repo=agent-bureau pr=- at=2026-09-08T22:47:00Z src=linear:DRE-3409:2026-09-08T22:47:00Z"
  ],
  "runs": 7,
  "repos": 2,
  "first_at": "2026-09-08T22:19:00Z",
  "resolve_note": ""
}
```

What the card asked for, found in that output:

* `"action": "file"` — one card, not seven.
* `"title": "Reviewer down since 15:19 PT — 7 runs, 2 repos"` — the first
  outcome is `2026-09-08T22:19:00Z`, which is 15:19 PT; seven distinct `src`
  values; two slugs (`agent-bureau`, `bureau-pipeline`).
* The body's fenced block is the exact line from DRE-3416:
  `ReferenceError: Claude Code native binary not found at
  /home/runner/.local/bin/claude. Please ensure Claude Code is installed via
  native installer or specify a valid path`.
* The action reference `anthropics/claude-code-action@v1` and the three
  suspects in the order the epic fixed — vendor release, then credential,
  then the status page — each with its command.

**The same fixture, 31 minutes later.** `--now 2026-09-08T22:49:00Z` is
15:49 PT; the registry window is 1800 s, so at `2026-09-08T23:20:00Z`
(16:20 PT) the newest outcome (`22:47:00Z`) is 33 minutes old and every one of
the seven has aged out. Run at 09:55:24 PT:

```
python3 scripts/reviewer_down.py replay tests/fixtures/reviewer-down-2026-09-08.json --now 2026-09-08T23:20:00Z
```

```json
{
  "action": "nothing",
  "title": "",
  "body": "",
  "lines": [],
  "runs": 0,
  "repos": 0,
  "first_at": "",
  "resolve_note": ""
}
```

The window empties, and the decision is `nothing` — exit 0.

**The suites that pin this, run on the same commit at 09:55:26 PT**, so the
next reader knows the replay above is the fixture the tests read
(`tests/test_reviewer_down.py` line 48, `FIXTURE = ROOT / "tests" /
"fixtures" / "reviewer-down-2026-09-08.json"`) and not a second copy:

```
python3 -m pytest tests/test_reviewer_down.py tests/test_reconcile_reviewer_down.py -q
```

```
87 passed in 1.48s
```

A note on provenance the fixture carries in its own `_header`, repeated here
so nobody reads more into the replay than it holds: the seven outcomes record
the incident **as epic DRE-3420 states it** — this repository's three failed
runs that afternoon were planner and engineer runs, and the agent-bureau
receipts on #2367/#2369/#2370 were not readable from this repository's token
when the fixture was written. The error line and the action reference are
verbatim from DRE-3416; the comment URLs are reconstructions of the shape
`gh pr list --json number,comments` emits. The replay proves the decision;
it does not re-read the 2026-09-08 pull requests.

## 2. The live sweep says so on a quiet day, and files nothing — MET

### agent-bureau, on the `stable` channel

Run [34706240996](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/34706240996)
— `Reconcile`, `schedule`, conclusion `success`, created 2026-09-12 09:46:16 PT,
agent-bureau head `609ce181b1b675efc21e6e377c9c0544ce21512a`. Read with
`gh run view 34706240996 --repo dreadnought-foundry/agent-bureau --log` at
09:54 PT.

The reusable workflow it called, from the same log:

```
Uses: dreadnought-foundry/bureau-pipeline/.github/workflows/reconcile.yml@refs/tags/stable (991af430928c60d5ba1841bb831061929a2cd02a)
  pipeline_ref: stable
```

The line, as printed, at 2026-09-12 09:46:52 PT (log line 267 of 454):

```
call / sweep	UNKNOWN STEP	2026-09-12T16:46:52.3084052Z fleet-reviewer-outage: nothing to report — 0 could-not-run in the last 30 min
```

Its neighbours, so the position in the sweep is visible — immediately after
the crashed-review pass and before the watchdog, which is where DRE-3435 put
the backstop in `main()`'s tuple:

```
call / sweep	UNKNOWN STEP	2026-09-12T16:46:52.3081583Z crashed-review: PR #2472 head ce4e456d has a review still running — leaving alone (dispatching would cancel it)
call / sweep	UNKNOWN STEP	2026-09-12T16:46:52.3084052Z fleet-reviewer-outage: nothing to report — 0 could-not-run in the last 30 min
call / sweep	UNKNOWN STEP	2026-09-12T16:46:52.3086146Z watchdog: DRE-3665 is labeled 'hand-built' — no dispatched run is expected, so a missing run receipt and an off-rail repo are both normal here, not a strand
```

`30 min` is `threshold.window_s // 60` read off the
`reviewer-outage-fleet-wide` act row in `config/pipeline-acts.json`
(`"threshold": {"window_s": 1800, …}`), not a literal in the sweep.

### bureau-harness, on `@main`, the same quarter-hour — a second witness

Run [34706251920](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/34706251920)
— `Reconcile`, `schedule`, `success`, created 2026-09-12 09:46:28 PT, head
`c67ee869bc815b65c66da092ef9f541d75e59543`. It called
`reconcile.yml@refs/heads/main (991af430928c60d5ba1841bb831061929a2cd02a)` —
the same commit `stable` resolved to — and printed the same line at
09:47:36 PT:

```
call / sweep	UNKNOWN STEP	2026-09-12T16:47:36.3977233Z fleet-reviewer-outage: nothing to report — 0 could-not-run in the last 30 min
```

### Nothing was filed — three reads of the board

**The sweep's own lookup**, run from the worktree at 09:55:54 PT with the
operator-tools `LINEAR_API_KEY` from agent-bureau's `.env` (read-only; the
key was sourced into the environment and never printed):

```
python3 scripts/linear_ops.py find-open-prefix "Reviewer down since "
```

```
linear-budget: 2499 → 2499 (spent 0 this run; window resets 10:55 PT; budget: undeclared)
```

Exit 0, and no identifier printed. That silence is the answer by design —
`cmd_find_open_prefix`'s docstring: *"Print the identifier of the OLDEST open
card whose title starts with `prefix`, else print nothing."* The budget line
is the wrapper's accounting, and `spent 0` says the lookup was served from
the pass cache.

**A full-text search** of the DRE team for `Reviewer down since` (archived
included), read through the Linear MCP at 09:55 PT, returned 20 issues — the
epic DRE-3420, this card DRE-3436, its siblings DRE-3429/3433/3431/3437, the
incident card DRE-3415, and older cards that merely contain the words. **None
has a title that starts with `Reviewer down since`.**

**Every DRE card created since 2026-09-11 00:00 UTC** (the day DRE-3435
merged), 92 of them, listed by creation time: none carries the prefix.

## 3. A live outage before this card closed — NOT OBSERVED

`no live outage observed while this card was open`

The card was opened 2026-09-08 16:53 PT. The backstop has been on
bureau-pipeline `main` since PR #368 merged at 2026-09-10 23:59 PT, and on the
`stable` channel at least since the 09:46 PT run above (this record did not
read when `stable` first advanced past `5fe6771b`, and does not claim it). In
that time no card with the `Reviewer down since` prefix was filed (section 2),
so there is no outage card, no first receipt time, no filing time and no
`reviewer back at` line to record. The card itself allows this line in place
of those; it is a record, not a failure.

## 4. The act-consumer guard against the console — MET

Run in the worktree at 09:55:51 PT. `BUREAU_CONSOLE_TOKEN` was the operator's
existing `gh` login (`gh auth token`, account `smeed652`, `repo` scope) passed
inline for that one process — never printed, never written to a file:

```
BUREAU_CONSOLE_TOKEN="$(gh auth token)" python3 scripts/check_act_consumers.py check
```

```
OK — every declared act is known to the console. config/pipeline-acts.json declares 30 act(s); the consumer is dreadnought-foundry/agent-bureau:console/backend/receipts.py (ACTS).
```

Exit 0. The guard reads `console/backend/receipts.py` off the console
repository's default branch through the GitHub contents API and parses the
`ACTS` literal with `ast`; `reviewer-outage-fleet-wide` / `fleet-reviewer-outage`
is one of the 30 it found known there — the DRE-3429 mirror is live.

## Where this leaves the card

| Acceptance criterion | State |
| --- | --- |
| Observation 1 recorded: command, commit, JSON showing `file`, the 15:19 PT / 7 runs / 2 repos title, the exact error line; then the window empties at +31 min | **Met** — §1, on `991af430928c…` |
| Observation 2 against a live scheduled Reconcile run in production: run URL, repository, the `nothing to report` line as printed, and the Linear prefix search showing no card | **Met** — §2, agent-bureau run 34706240996 (plus bureau-harness 34706251920), three board reads |
| Observation 3: the live outage card's identifiers and times, or the explicit `no live outage observed` line | **Met** — §3, the explicit line |
| `check_act_consumers.py check` with `BUREAU_CONSOLE_TOKEN` reports `ok` against the console's default branch, output line recorded | **Met** — §4 |
| `architecture/audits/reviewer-down-proof-2026-09-08.md` on `main` | This document |

Nothing is owed. The one thing this record deliberately does not contain is a
live filing — the threshold firing on real receipts — because the only way to
produce one today would be to break the reviewer on purpose, and that is not
something to manufacture on the live fleet. Section 3 is the honest state.

---

Read and written 2026-09-12, 09:53–09:56 PT, from a worktree of
`dreadnought-foundry/bureau-pipeline` at `991af430928c60d5ba1841bb831061929a2cd02a`
and the Actions logs of `dreadnought-foundry/agent-bureau` and
`dreadnought-foundry/bureau-harness`. Every live observation is a read;
nothing on any board was changed.
