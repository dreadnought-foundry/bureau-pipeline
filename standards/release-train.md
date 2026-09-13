# Release-train standard — what a surface owes

One reusable workflow releases every repo in the fleet
(`.github/workflows/release-train.yml`, DRE-3167). It contains no product's
deploy steps and never will: what a surface actually does is the surface's own
script, in the surface's own repo. This is the contract between the two.

The rules the train applies — spacing, window, current, green-at-SHA, the
brake — are `scripts/release_train.py`, unit-tested without GitHub, and the
data every caller declares is `docs/release-train.md`, rendered from the same
schema the check reads. This page is the half a human writes: the script.

## The shape of it

    the stub (data + one `uses:` line)  →  the train  →  your surface script

The stub decides nothing. The train decides everything except what a release
IS. The script does that, and owes the six things below.

## What a surface script owes

The train calls it as `bash <script> --surface <name>`, with `RELEASE_SHA` in
the environment — the commit the plan chose, which is the newest green one
and not always the head — the caller's own identity already assumed, and the
repository checked out at `RELEASE_SHA`. In return:

1. **Migrations first.** Run them on the byte-identical just-built image,
   before any new code serves traffic, idempotently, and abort on failure. A
   surface that rolls out ahead of its schema is the one failure this ordering
   exists to prevent.
2. **Rollout only where changed.** Compute the change from the repository
   root, and ship nothing you did not build. A cwd-relative pathspec that
   matched nothing once shipped an empty deploy that reported success.
3. **Verify before you tag.** Health-check what you just rolled out and fail
   if it is not serving. The tag means "this commit is live", so cutting it
   before that is true makes every downstream reader wrong at once.
4. **Tag with the standard annotation.** An **annotated** tag in the surface's
   declared `tag_series`, at `RELEASE_SHA`. The train verifies exactly that
   and refuses the release if the tag is lightweight, absent, or points
   somewhere else. **The tag IS the receipt** — deploy-lag reads the newest tag
   in the series and the console's Shipped-today panel measures commit
   ancestry against it, so the script writes no second record.
5. **Non-zero on any failure.** Every failure, including a partial one. The
   train reads the exit code and nothing else; a script that swallows an error
   and exits 0 without a tag is refused anyway, but it is refused a step later
   than it should have been.
6. **`deferred: <reason>` when a person is owed the deployment.** Exit 0,
   print one line beginning `deferred:`, and cut no tag. The train reports the
   line verbatim as a no-op, the job concludes success, nothing alerts, and
   deploy-lag goes on reading BEHIND until whoever the reason names has acted.
   A deferral is not a failure and must never be dressed as one.

Anything else the surface needs — a Linear release record, a smoke test, a
cache invalidation — stays inside the script, where it already is.

## The rollback

Every surface declares a `rollback` command that puts it back, in the data,
written out. It is not run by anything: it is there so that the person reading
a red run at 2am does not have to derive it. A surface whose `auto` is true
declares one; only an `auto: false` surface may leave it null.

## The stub, in every repo

`.github/workflows/release-train.yml` in the caller. Data plus one `uses:`
line — copy it as it stands:

```yaml
name: Release Train

on:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
    branches: [main]
  schedule:
    - cron: "0 15 * * *"
    - cron: "0 14 * * *"
  workflow_dispatch:
    inputs:
      surface:
        description: One surface, run even when its auto is false and outside its window.
        type: string
        required: false
        default: ""
      not_before:
        description: Set by the train itself when it re-arms (DRE-3559). Leave empty.
        type: string
        required: false
        default: ""

permissions:
  id-token: write
  contents: write
  checks: read
  actions: write

jobs:
  call:
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/release-train.yml@stable
    secrets:
      RELEASE_ROLE_ARN: ${{ secrets.BUREAU_RELEASE_ROLE_ARN }}
      LINEAR_RELEASE_KEY: ${{ secrets.LINEAR_RELEASE_KEY }}
      LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}
    with:
      pipeline_ref: stable
      surface: ${{ inputs.surface }}
```

**The train is never stopped, and that is why the trigger is CI completing —
not the push (DRE-3263, the CEO's rule of 2026-09-06).** If the commit is
ready it goes; if it is not, the train leaves without it and the next train
picks it up. A gating check still running is a no-op that names it — never a
wait, never a refusal. A `push` fires BEFORE that commit's CI has started, so
a push-triggered stub would find CI pending on every run and release only from
the schedule; `workflow_run` on the caller's CI workflow (`workflows: ["CI"]`
is its `name:`), `types: [completed]`, `branches: [main]` fires the moment the
commit's checks have answered. On that event the train proceeds only when the
CI run concluded `success`, and the commit CI ran on
(`github.event.workflow_run.head_sha`) is one candidate. Only the checks that
GATE A MERGE count — the merge gate's own set, read from one place; a fix
agent, the medic, the sweep and the train's own run on the same SHA are
ignored by verified origin, and every no-op and refusal names what was read
and what was ignored. `checks: read` is the permission green-at-SHA reads
`commits/{sha}/check-runs` with; `actions: write` covers the workflow-runs
record that says which of those runs gate, and the re-arm below dispatches
the stub with it.

**A no-op that names a minute re-arms itself (DRE-3559).** A run that no-ops
on the spacing names the minute the next release may be cut — the first whole
minute at which the spacing has elapsed — and one that no-ops on the window
names the window's next open. Neither minute is a trigger: on 2026-09-12 four
runs between 15:23 and 15:35:52 PT each said "may be cut at 15:36 PT" and the
release waited for a hand dispatch at 15:38:58 PT. So the run dispatches this
stub once, `gh workflow run release-train.yml -f not_before=<UTC minute>`,
under its own `github.token` — hence `actions: write`, and the `not_before`
input, which the train reads straight from the dispatch event, so the `with:`
block does not pass it. The re-armed run waits until that minute (never longer
than the largest `spacing_minutes` plus two minutes) and then decides exactly
as any other run: green-at-SHA, the spacing, the window and the brake, nothing
bypassed. The no-op's line says `— re-armed for HH:MM PT`; two no-ops inside
one spacing window produce one re-arm, and the second line names the run
already waiting. A hold, the brake, `auto: false`, a surface that reads
current and a commit still checking re-arm nothing — a person, or the next CI
completion, owns those. A stub without `not_before` or with `actions: read`
still works: its line says `re-arm skipped: caller stub lacks not_before` (or
`lacks actions: write`), and the run is as green as it was.

**Ready is a commit, not the head (DRE-3266, the CEO's amendment of the same
day).** The train releases the newest commit on the default branch whose
gating checks are all green and which is newer than the surface's deployed
tag. It walks the first-parent line from the head back to the commit the
newest tag points at, newest first: a green commit is released — that one,
not the head; a still-checking commit is stepped past and rides the train its
own CI completion fires; a red commit is stepped past and named, because a
red commit is never released. The run's log names the chosen commit and every
commit stepped past with its reason. A busy main means each train leaves with
a slightly older, proven commit, and the next one carries the rest — nobody
waits, nobody stops, no collision matters. The walk reads at most thirty
commits; a surface whose newest thirty hold no green commit is refused as too
far behind to walk, and a person releases it by hand once. The surface job
checks out the chosen commit, and that is the `RELEASE_SHA` the script
receives and the commit the tag lands on.

**No `paths:` filter, deliberately.** YAML cannot read the data, so the stub
fires on every CI completion on the default branch and the TRAIN does the path
filtering: a surface whose declared `paths` are untouched since its newest tag
reads current and is a no-op that says so.

**Two cron lines, deliberately.** GitHub's `schedule:` takes UTC only and has
no timezone field. 07:00 PT is 15:00 UTC under PST and 14:00 UTC under PDT, so
the stub carries both and knows nothing about which is which: every day one
fires at 07:00 PT and the other at 06:00 PST in winter or 08:00 PDT in summer.
The train reads the clock in `America/Los_Angeles` and applies the surface's
window to it — the 06:00 PST firing is outside `07:00-21:00 PT` and is a no-op
that names the window; the 08:00 PDT firing is an ordinary run. This is the
first schedule in the fleet that tracks a local clock; there is no precedent to
copy and these two lines are the contract.

`RELEASE_ROLE_ARN` is the one required secret — the caller's own OIDC role, so
a repo can only ever deploy itself. The two Linear keys are optional and pass
through to the script's environment.

## The supervised first release

Run the stub by hand with `surface: <name>` on a surface whose `auto` is
false. That runs the one surface, outside its window, with a person watching.
It bypasses nothing else: not green-at-SHA, not the spacing, and not the brake.
If a gating check on the head is still running, the dispatch releases the
newest green commit behind it and names the head as stepped past; only when
no commit newer than the tag is green yet is the dispatch a no-op that names
the newest still-checking one.

## The fleet-wide brake

`RELEASE_HOLD` is a repository variable, read the way `INTAKE_HOLD` is read.
Set it — ideally to the date you set it — and every surface of every train
exits `held`, saying so once each, before any surface job is created. Clear it
and the fleet resumes. Nothing expires it and nothing auto-clears it.

**Where to set it, and this is the part to get right.** A called workflow's
`vars` context resolves against the CALLING repository and the organization,
never against bureau-pipeline's own variables. So:

* the **organization** variable `RELEASE_HOLD` is the FLEET-WIDE brake — it is
  the one to set when something is wrong everywhere;
* a **repository** variable of the same name brakes that one repo's train,
  including this repo's own.

Setting it only on bureau-pipeline brakes bureau-pipeline. That is the whole
caveat, and it is written here rather than assumed because the difference is
invisible until the day it matters.

## What the train never runs

A surface declaring `"record": "channel"` is a moving tag some other train
advances — this repo's own `pipeline-channel` (the `stable` channel,
advanced by `promote-channel.yml`) is the first. It is declared so deploy-lag
and the receipts read it like every other surface, and the release train exits
a no-op saying so. A `channel` surface names no script; the schema check
refuses one that does.
