# The escape census — every way work leaves the one path

There is meant to be one way work lands: a card, then a branch named
`agent/DRE-<n>-<slug>`, then a pull request, then CI, the critic, the merge
gate, and the card closing off GitHub's merge event. This document is the list
of ways work leaves that path, each with a real incident behind it — no
hypotheticals — and, in the last column, what actually reads for it today.

Assembled 2026-08-23, built into code by DRE-2682. The pattern across every
row is the same: **the machinery keys off a pull request, so anything that
never becomes one is outside the system entirely** — and several of our own
conventions quietly produce exactly that state.

Keep the last column honest. `hand-built` proved that a label nothing reads is
not a gate; a census row claiming a watcher that no longer exists is the same
trap one level up. Every reader named below is resolved by
`tests/test_escape_census_doc.py`, and the count in the next line is recomputed
from the table rather than typed.

**6 of 10 routes are unwatched**.

## The census

| # | Escape route | Evidence | Reader today |
| -- | -- | -- | -- |
| A | Branch pushed with commits, **no PR ever opened** | DRE-2655 — nineteen hours invisible, found by eye | `reconcile.flag_unlanded_work` — reports the branch on its card once it has been idle past the bounded interval |
| B | PR opened on a **non-**`agent/*` **branch** | DRE-2664 / portico #339 on `docs/forms-critic-findings` | `reconcile.flag_unowned_prs` — announces it; nothing acts on it |
| C | Card in a working lane, no dispatch, nothing to point at | DRE-2655 sat In Progress nineteen hours; the state was true and carried no information | `reconcile.flag_stranded` for dispatched cards · `reconcile.flag_unlanded_work` for hand-built ones |
| D | PR opened but **left a draft indefinitely** | #178 and #343, both held deliberately | — a forgotten draft and a deliberate hold are indistinguishable |
| E | Hand-built through the **GitHub API** — no local test run, no test-first commit order | DRE-2694 — #176 and #178, three hours lost to a history rewrite | `scripts/check_tdd_commits.py` — after CI, and only where the check runs |
| F | Two agents in **one worktree** — a commit lands on another agent's branch | 2026-08-23; repaired by a refspec push | — |
| G | Author identity **misleads about which path built it** | DRE-2694 (bot work wearing the operator's identity) and DRE-2655 (operator work wearing the bot's) | — and any author-based check is actively wrong; the dispatch record is the only fact a local git config cannot spoof |
| H | A review re-run by hand dispatch writes its check to `main`, not the PR head | Recorded hazard: the PR looks blocked while the gate is satisfied | — |
| I | PR merges but the **card never closes**, because the branch name carries no card id | `linear-sync` reads the id out of the head ref | — |
| J | A branch names a card id **belonging to a different card**, so the wrong card closes on merge | Near-miss 2026-08-23: `agent/DRE-2697-scaffold-guard-lint`, filed against a card that already existed and was unrelated; caught by hand seconds before the PR opened | — |

## What the census says

**The naming convention is load-bearing and buys nothing on its own.** In case
A the branch was named perfectly — `agent/DRE-2655-drift-count-out-of-the-pill`
— and every gate still missed it, because gates match the head ref *of a pull
request*. In case B the branch was named wrongly and the work was equally
stranded. The convention only starts paying once a PR exists, so it cannot be
what we rely on to notice that one does not.

**Several holes are produced by deliberate design choices**, which is why they
persist. `hand-built` suppresses the stranded-card alarm on purpose (case C).
A draft is the correct way to hold a PR in a sequence (case D). The API route
is the sanctioned way to write a sibling repo (case E). None of these are bugs
to remove — each needs its own replacement signal, or removing the symptom
breaks something that works. DRE-2682 did exactly that for C: the `hand-built`
label still silences `flag_stranded`, and `_flag_hand_built_idle` now measures
what hand-built work actually owes (a branch, then a PR) in its place.

**Authorship is never the signal.** This is a constraint on every row above,
not a row of its own remedy: git authorship misled in both directions in the
two incidents we have, so any detection distinguishing hand-built from
dispatched work by author will be wrong. Use the dispatch record — whether an
`agent-task` run exists — and the pull request.

## The rule to build to

**Every card being worked has exactly one observable next step, and the absence
of that step is itself reported.** For any card not in a terminal state, the
system should be able to answer *"what is the next thing that must happen, and
how long has it been outstanding?"* — and when the answer is *nothing is
happening*, that is surfaced on the card rather than inferred by a human
scanning a board.

That rule closes A, C and D directly, and makes B, F and I visible as
anomalies rather than silence.

## Detections, in cost order

Branch lists and PR lists are one API call each and the sweep already runs
every fifteen minutes, so none of these is expensive.

1. **Branch with commits, no PR** (A) — *built, DRE-2682*:
   `reconcile.flag_unlanded_work`.
2. **Card working, nothing to point at** (C) — *built for hand-built cards,
   DRE-2682*; dispatched cards were already covered by `flag_stranded`. It
   reports what is missing, not that the card is "stalled".
3. **Draft age** (D) — not built. A draft past a bounded interval reported
   with the reason it is held **if the body states one**: a deliberate hold
   stays legitimate, an unexplained one stops being invisible.
4. **Commits ahead on a branch whose card is Done or Cancelled** (F, I) — not
   built. `flag_unlanded_work` deliberately says nothing about a terminal
   card's leftover branch, which is this detection's job rather than that
   alarm's.
5. **Card ids are issued, not typed** (J) — not built. Detection is the floor
   (resolve the id in a ref on push: does it exist, is it already terminal,
   does its `repo:` label match the repo being written); issuance is the fix —
   a writer asks for the card and receives the branch name, the shape Linear
   already returns as `gitBranchName`, so the failure mode disappears rather
   than being caught.

## Do not solve this with more labels

The argument that opened DRE-2682 is that `hand-built` proved a label is not a
gate, because nothing read it. The same trap applies to the fix: a `no-pr-yet`
or `held-deliberately` label that nothing reads would reproduce the exact
failure one level up. Every detection here must be a **reader** — something
that runs, looks, and writes to the card — never a marker someone is expected
to notice.

## Two repos

The refusal half of the same wave lives elsewhere. The promoter reads a card's
verdict in `scripts/reconcile.py` (DRE-2735); the relay reads it in
`cloud/relay/lambda_function.py` in **agent-bureau**, which is not this
repository and is deployed by the operator. A card created directly in Todo,
or dragged there by a human, dispatches on that path without the promoter ever
seeing it — so a gate on the promoter alone guards the minority of the traffic.
