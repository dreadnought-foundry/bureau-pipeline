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
   A deferral is not a failure and must never be dressed as one. A deferral
   that is transient — another writer is mid-release, and the surface is free
   in minutes — may end its line with `— re-arm in <N> minutes`, exactly
   `deferred: <reason> — re-arm in <N> minutes`, and the train re-arms itself
   for that minute (DRE-6005). Without the clause the line behaves as above.

Anything else the surface needs — a smoke test, a cache invalidation — stays
inside the script, where it already is.

**There are two exceptions, and both are the train's.** The script owes
nothing for either, and neither ever fails a release.

**The first is `whats-new.json`, published for every surface** (DRE-5516).
Once the script's tag is verified, the train collects the `What's new:` lines
of the pull requests the release carries (`standards/whats-new.md`) and
publishes them as the `whats-new.json` asset of the tag's GitHub Release
(`scripts/whats_new_release.py`). It does this for every surface, pipeline or
not. Reading a private repository's pull requests needs `pull-requests: read`
in the stub below. A stub without it publishes nothing, and the run says so in
one `What's new not published` annotation and a block on its summary page.

**The second is the Linear release, for a surface that declares a pipeline.**
A surface that names its Linear release pipeline in `release.json`'s top-level
`linear_pipelines` key gets its release written by the train
(`scripts/release_linear.py`): after the file above, the release is synced with
the tag as its version and the cards its changes named since the previous tag,
completed, and given one note. The note opens with a `## What's new` section
rendered from the same items as the file, so the two never disagree. The
script should not write a release of its own, or the surface gets two. A script
that already writes its own Linear release (agent-bureau's three, from before
this seam) declares no pipeline here and is untouched: it gets the file and no
`## What's new` section in its note. A `channel` surface may declare one too
(DRE-4872): the train still never runs it, and `promote-channel.yml` writes
`stable-<short sha>` each time the channel moves.

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
  deployments: write
  pull-requests: read

jobs:
  call:
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/release-train.yml@stable
    secrets:
      RELEASE_ROLE_ARN: ${{ secrets.BUREAU_RELEASE_ROLE_ARN }}
      LINEAR_RELEASE_KEY: ${{ secrets.LINEAR_RELEASE_KEY }}
      LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}
      BUREAU_APP_ID: ${{ secrets.BUREAU_APP_ID }}
      BUREAU_APP_PRIVATE_KEY: ${{ secrets.BUREAU_APP_PRIVATE_KEY }}
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

**A CI run GitHub never gave a machine is re-run once (DRE-5901).** A run
whose jobs were cancelled "not acquired by Runner" concluded `cancelled`, so
the train reads it as red and leaves without the commit, and the medic, which
wakes on `failure` and `timed_out` only, never sees it. The reconcile sweep
does: in every repo, every pass, the newest CI run of each workflow on the
default branch that concluded `cancelled` with every non-green job carrying
that annotation has its failed and cancelled jobs re-run (`gh run rerun <id>
--failed`), once — its first attempt only — with one `runner-lost:` line in
the sweep's log and step summary naming the repo, the run, the jobs and the
reason. The train reads the re-run's result on its next run, as it reads any
CI result. A run cancelled by a newer push, by a person or by a timeout is
left alone, and a re-run attempt lost the same way is reported once and never
re-run again (`scripts/runner_lost.py`).

**`deployments: write` is what the train records its decision with**
(DRE-4771): every run writes one deployment plus one status per surface it
decides about — the decision as the payload,
`docs/release-decision.md` — and a stub that has not granted it gets
`decision not recorded: caller stub lacks deployments: write` on every run
and never a failure. The grant has to be the CALLER's, because a called job
asking for more than its caller granted fails the whole run at startup, so
the reusable workflow declares no job-level `permissions:` of its own.

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
`lacks actions: write`), and the run is as green as it was. A `deferred:` line
that ends `— re-arm in <N> minutes` names a minute too, and the surface job
re-arms for it the same way (DRE-6005): the same bound, the same collapse onto
a run already waiting, the same clauses after the `deferred:` sentence.

**A re-armed run releases what is green when it wakes (DRE-3791).** A
dispatched run's commit is fixed when it is dispatched, so a run that slept
would otherwise walk from a head half an hour old. On 2026-09-13 the waiter
dispatched at 09:34 PT released `v1.6.61` at 10:00 PT from that commit, and
two merges that had gone green while it waited were left out. So a run that
carries `not_before` re-reads the default branch's tip when it wakes and walks
from there, and its line says which head it walked from and which it was
dispatched at. A merge that is green when the train fires is in the release;
one still checking is stepped past, as always. Nothing in the stub changes.

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

**Documentation never owes a release (DRE-5375).** A change to documentation
alone — `*.md` anywhere, or anything under a `docs/` directory — never makes a
surface owe a release, so a note can neither start a production rollout nor
hold one up. The train excludes those files when it reads what changed under
`paths`, and a surface whose only changes since its newest tag are
documentation reads current, with a line that names the files. A commit that
carries owed code is still released, whatever documentation rides with it. A
surface that needs a different set declares the optional `ignore` list, which
replaces the default; `"ignore": []` counts every file again. Your script's own
change check (rule 2 above) is yours to keep in step: the train's exclusion
decides whether a lap runs, never what the script ships.

**The stub's own cron lines are its morning wake-up (DRE-6052).** GitHub
runs a `schedule:` only from a workflow file on the default branch of the repo
that holds it, and this train is `workflow_call`, so a cron inside the train
never fires for a caller — the stub is where it lives. agent-bureau's and
Portico's stubs carry `0 13 * * *` and `0 12 * * *`, 05:00 PT in standard and
daylight time. The rest of the day a train is woken by every CI completion on
the default branch and by its own re-arm. DRE-4450 also built a fleet wake-up
in bureau-pipeline that dispatched every roster repo's stub at 05:00 PT; it
only ever duplicated these crons, and the CEO retired it on 2026-10-06 because
the fleet runs round the clock.

`RELEASE_ROLE_ARN` is the one required secret — the caller's own OIDC role, so
a repo can only ever deploy itself. The two Linear keys are optional and pass
through to the script's environment; `LINEAR_API_KEY` is also what the train
writes a declared surface's Linear release with.

**The worker App pair is optional too, and it is what lets a tag cover a
workflow change (DRE-5949).** `BUREAU_APP_ID` and `BUREAU_APP_PRIVATE_KEY` are
the worker App's (agent-bureau-bot) — every onboarded repo already holds them,
because the build and verify workflows require them. Passed, the release job
mints the App's installation token and stores it as the git credential of its
checkout of the caller, so the surface script's `git push` of the tag is made
by the App, whose installation grants `workflows: write`. That buys a tag over
a range that changes a `.github/workflows/` file: on 2026-10-05 portico's
release went live and then `portico-portals-v1.0.112` was refused, because
GitHub never grants `github.token` that scope. A stub that passes nothing gets
today's push with `github.token`, which works for every other range. A pair
that is passed but does not mint is one warning on the run and the same
`github.token` push — never a stopped train. Nothing else changes identity:
the decision record and What's new are still written with `github.token`, so
the train still cannot wake itself. One thing does change, and it is GitHub's
rule rather than ours: a tag pushed by the App fires real `push` and `create`
events in the caller, where `github.token`'s pushes start no workflow runs.
The stub above triggers on no tag ref, so the train does not wake itself; a
caller workflow declared `on: push: tags:` now runs on the release tag, which
is what such a workflow asks for.

## The fleet's hours

**The opening time is written once, in the train (DRE-4450).** The CEO, on
2026-09-21: *"The schedule should be integrated into the train so it's in one
place."* Before that it was written twice in every repo with a train — the
`window` in `.github/bureau/release.json` and two `schedule:` crons in the stub
— and on 2026-09-21 Portico's train slept until 07:03 PT because DRE-4357 had
moved one copy and not the other.

The one declaration is `FLEET_WINDOW` in `scripts/release_train.py`, and it is
`always` — round the clock (DRE-5266). The CEO, on 2026-09-29: *"They should
all default to the round-the-clock. … They can change it if they want but they
should default to round the clock."*

* **A surface that omits `window` inherits it.** The schema check accepts the
  omission, and the train's lines name it as the fleet default. Omitting it is
  what a surface on the fleet's hours should do.
* **A surface that declares a different window keeps it**, and every line the
  train prints about that window says it overrides the fleet default — so a
  surface that is deliberately different reads as deliberate, and one that is
  accidentally stale is visible on its own run.
* **A surface that declares `always` anyway is on the fleet default**, and its
  lines say so. Declaring the default overrides nothing.

The train decides when a surface MAY release; it never decides when a train
wakes. That is the stub's — its CI trigger, its own crons and the re-arm
above.

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
refuses one that does. The train that does advance it writes the surface's
decisions and `release` deployments itself — for `pipeline-channel` that is
`promote-channel.yml`, through `scripts/channel_record.py` (DRE-5214).
