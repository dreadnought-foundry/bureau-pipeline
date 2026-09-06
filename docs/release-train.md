# The release train's data

<!-- GENERATED FILE — do not edit. Source: scripts/release_train.py.
     Regenerate with `python3 scripts/release_train.py render`. -->

Every repo the train serves declares its surfaces in `.github/bureau/release.json`. This page is rendered from the same table the schema check reads, so it cannot drift from what is enforced — run `python3 scripts/release_train.py schema` to check a file, and every refusal names the surface and the field.

## A surface

```json
{
  "surfaces": {
    "<name>": {
      "tag_series": [
        "<glob>",
        "<legacy glob>"
      ],
      "paths": [
        "<dir>/"
      ],
      "script": "infra/release-<name>.sh",
      "rollback": "make rollback-<name> VERSION=<tag>",
      "spacing_minutes": 30,
      "window": "07:00-21:00 PT",
      "auto": false,
      "identity": "<role name>",
      "record": "tag"
    }
  }
}
```

| Field | Shape | What it means |
| --- | --- | --- |
| `tag_series` | list of tag globs | The newest tag across ALL the globs is the deployment record. It is what deploy-lag reads and what the console's Shipped-today panel measures commit ancestry against, so a legacy glob belongs here rather than in anyone's memory. |
| `paths` | list of paths | What counts as changed. `[]` means every commit counts. A surface whose paths are untouched since its newest tag reads current, and the train exits a no-op saying so. |
| `script` | path in the caller, or null | The surface's own release script, called as `bash <script> --surface <name>` with `RELEASE_SHA` in the environment and the identity already assumed. Null only while `auto` is false. |
| `rollback` | command, or null | The one command that puts the surface back, written out so nobody has to derive it at 2am. Null only while `auto` is false. |
| `spacing_minutes` | whole minutes | How long after the newest tag in the series the next release may be cut. Two triggers inside the spacing produce one release. |
| `window` | `HH:MM-HH:MM PT`, or `always` | The hours a release may be cut, read on the America/Los_Angeles clock. A trigger outside the window is a no-op that names it; a hand dispatch runs anyway. |
| `auto` | true or false | Whether the train releases this surface unattended. False means it releases only on a hand dispatch naming it — which is how a supervised first release is run. |
| `identity` | role name | Who the script runs as: the caller's own OIDC role, fed from the one required secret `RELEASE_ROLE_ARN`. On a `channel` surface it names whatever advances the ref, since nothing is assumed. |
| `record` | `tag` or `channel` | `tag` is the default and the deployment record is the annotated tag the script cuts. `channel` is a moving tag another train advances: the release train never runs it, and deploy-lag measures it by compare. |

## What the train decides, in order

One rule set, asked twice: once to build the matrix, and once inside each surface's own concurrency lane. `no-op` and `held` conclude the job green — they are the train working; only `refuse` is red.

**Ready is a commit, not the head** (DRE-3266, the CEO's amendment of 2026-09-06). The train releases the newest green commit on the default branch that is newer than the surface's deployed tag: the candidates are the first-parent line from the head back to the commit the newest tag points at, read newest first and at most 30 of them. A green candidate is released — that commit, not the head; a still-checking one is stepped past; a red one is stepped past and named, because a red commit is never released. The run's log names the chosen commit and every commit stepped past with its reason. The head that was stepped past rides the train its own CI completion fires. Two reads per candidate (the check runs and the workflow-runs record that says which of them gate), so a walk that exhausts the bound costs at most 60 reads.

| Order | Decision | Act | Says |
| --- | --- | --- | --- |
| 1 | `held` | `held` | `RELEASE_HOLD` is set: every surface exits held, once each, before any surface job exists |
| 2 | `channel` | `no-op` | the surface records a channel another train advances |
| 3 | `no-script` | `no-op` | the surface declares no script |
| 4 | `auto-false` | `no-op` | unattended runs skip an `auto: false` surface; a hand dispatch runs it |
| 5 | `current` | `no-op` | nothing under the surface's `paths` has changed since its newest tag |
| 6 | `spacing` | `no-op` | the newest tag in the series is younger than `spacing_minutes` |
| 7 | `window` | `no-op` | the America/Los_Angeles clock is outside the window; a hand dispatch runs anyway |
| 8 | `ci-pending` | `no-op` | no candidate is green yet — the newest still checking is named, the train leaves without it, and the run its CI completion fires takes it (never a wait: the train is never stopped) |
| 9 | `ci-red / ci-absent` | `refuse` | every candidate is red, or no gating check has reported on it — each named in the refusal, with what was read and what was ignored; a red commit is never released |
| 10 | `walk-bound` | `refuse` | 30 candidates read newest-first and none green, with more behind them — the surface is too far behind to walk; release it by hand, or raise the bound |
| 11 | `deferred` | `no-op` | the script exited 0 printing `deferred: …` — the deployment is owed to a person, and that is not a failure |
| 12 | `released` | `release` | the script ran and the train verified the annotated tag it cut at the released commit |

The brake is the repository variable `RELEASE_HOLD`, read the way `INTAKE_HOLD` is read — see `standards/release-train.md` for where to set it and what the surface script owes.

## The trigger, and what the stub must declare

**The train is never stopped** (DRE-3263, the CEO's rule of 2026-09-06). If the commit is ready it goes; if it is not, the train leaves without it and the next train picks it up. A gating check still running is therefore a `no-op` that names it — never a wait, never a refusal — and the run that picks the commit up is the one fired by CI completing on the default branch. A `push` fires BEFORE that commit's CI has started, so a push-triggered stub would find CI pending on every run and release only from the schedule. The stub in every caller (`.github/workflows/release-train.yml`) must declare exactly this:

```yaml
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
        type: string
        required: false
        default: ""
```

`workflows: ["CI"]` is the `name:` of the caller's CI workflow, and `branches: [main]` is the branch that CI ran on. On that event the reusable workflow reads the commit from `github.event.workflow_run.head_sha` and proceeds only when `github.event.workflow_run.conclusion` is `success`; a CI run that concluded anything else is a `no-op` that says so, and the commit waits for the repair the medic files. The schedule and the hand dispatch read the head of the branch at that moment, as before.

Which check runs on the commit COUNT is decided in one place — `merge_gate.gating_check_runs`, the same classifier the merge gate's all-green rule rests on. A check run counts when GitHub's own workflow-runs record says the commit itself triggered it (`push`, `pull_request`, `pull_request_target`) and it is not a review workflow or the train's own stub; a fix agent, the medic, the sweep, a hand dispatch and the train's own run all report against the default branch's head without being about it, and are ignored by that origin — never by name. Every no-op and refusal line names what was read and what was ignored, by producing workflow file.
