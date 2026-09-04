# The act registry — and how a new act ships

`config/pipeline-acts.json` declares every autonomous act the pipeline can
take: a refusal, a recovery or a hold, and per act its tag, its kind, the
state it leaves the work in, the next actor, what it discharges and the
workflow expected to act on it. It is read and written through one module,
`scripts/pipeline_act.py`. That much is DRE-2825, and `config/README.md`
carries the shape.

This page is about the one thing the registry does not say about itself: it
has a reader in **another repository**, and adding a row here changes that
reader's world.

## The sequencing rule: console first, pipeline second

**A new act ships CONSOLE-FIRST.** In order:

1. Open a PR on `dreadnought-foundry/agent-bureau` that teaches
   `console/backend/receipts.py` the new tag with its kind — one line in
   `ACTS`. Merge it.
2. Then open the bureau-pipeline PR that adds the act to
   `config/pipeline-acts.json` and emits its receipt.

**The pipeline PR cannot merge ahead of the console one**, and that is not a
convention anybody has to remember: the `act registry consumers` job in
`.github/workflows/tests.yml` runs `scripts/check_act_consumers.py`, which
reads the console's `ACTS` out of agent-bureau's default branch and fails when
this repo declares an act it does not carry. The failure names the act and the
one line that fixes it.

That job asks the console **only on a run whose changed files include
`config/pipeline-acts.json`**, and skips its steps otherwise. The scoping is
load-bearing, not an optimisation: this job has no path filter, the merge gate
treats any `failure` on the head sha as "wait", and a cross-repo token mint can
fail transiently. Ungated, one blip would hold every open pull request in
bureau-pipeline hostage — the same shape as the two agent-bureau incidents
below, moved one repository upstream. If the changed-file list itself cannot be
read the job asks the console anyway: the gate fails closed, never open.

The order cannot be the other way round, and the asymmetry is the point. The
console learning a tag the pipeline has not declared yet costs nothing — no
receipt carries it, so nothing reads it. The pipeline declaring a tag the
console has not learned costs **every open pull request in agent-bureau**.

## Why this exists

The console has always checked this. Its
`test_every_act_the_pipeline_declares_is_known_to_the_console` (DRE-2825) is
right, and it was the ONLY check — which meant it fired in the wrong repo,
after the fact, against people who had nothing to do with the change:

* **2026-09-03 22:26 PT (DRE-3081)** — DRE-3042 added `conflict-sweep-crashed`
  here, merged green, and turned every open agent-bureau PR red.
* **2026-09-04 09:13 PT (DRE-3090)** — DRE-3084 added `refuted-finding`, and
  it happened again, twelve hours later.

Both times the fix was one line in the console and a card to ask for it, while
unrelated work sat blocked waiting for someone to notice.

`standards/engineering.md` already states the rule that was broken: *a shared
contract is either one module both sides import, or every consumer is updated
in the same change.* Two repositories can do neither, so the producer grows
the consumer's question and asks it before the merge instead of after.

## The three places the check appears

| Where | What it does | On failure |
| -- | -- | -- |
| `tests/test_pipeline_acts_consumers.py` | `test_every_act_the_pipeline_declares_is_known_to_the_console` — the producer-side twin | red test naming the act |
| `.github/workflows/tests.yml`, job `act registry consumers` | on a run that CHANGES the registry, runs that test with a token scoped to agent-bureau, then asserts it did not skip | red build |
| `.github/workflows/qa-review.yml` | puts the guard's result in the critic's context for any PR touching the registry | the critic sees "unknown to the console" before it approves |

## Unread is never a pass

Offline — no `BUREAU_CONSOLE_TOKEN`, no network, the console file moved, or
`ACTS` built by a comprehension rather than written out — the guard **skips
with the reason printed**. It never concludes "the console carries no acts",
because that would fail every act at once for a reason that is not true.

That skip is deliberate and it is what keeps the rest of the suite runnable in
a local checkout with no console credentials. It is also why the CI job runs
the guard under `pytest --junit-xml` and then runs
`check_act_consumers.py assert-ran` over the report: **in that job a skip is
red.** A guard that can quietly not run is a guard that eventually does not.

## Where the consumer is declared

Not in code. `config/pipeline-acts.json` carries a `console` block — the
repository, the path, the module name to fall back on when the path moves, the
symbol, and the template for the one-line fix. The path is a fast path and the
module name is the truth: when `console/backend/receipts.py` 404s the guard
locates `receipts.py` by name in the console's tree, so moving the file costs a
slower check rather than a wrong answer. (`config/lane-contract.json`'s own
`console` block makes the same argument for the same reason.)
