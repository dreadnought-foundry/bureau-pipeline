# The Parts Check stub — the duplicate-parts check on a repo's pull requests

`.github/workflows/parts-check.yml` (DRE-8842) is a reusable workflow that
runs the duplicate-parts check, `scripts/parts_check.py check`
([`parts-check.md`](parts-check.md)), over a pull request's diff. A repo turns
it on with the thin stub below, saved as `.github/workflows/parts-check.yml`
in the repo.

```yaml
name: Parts Check

on:
  pull_request:
    types: [opened, reopened, synchronize, ready_for_review]

permissions:
  contents: read

jobs:
  call:
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/parts-check.yml@stable
    with:
      pipeline_ref: stable
```

The canary repos, agent-bureau and bureau-pipeline, ride `@main` with `pipeline_ref: main` instead, per [`docs/self-hosting.md`](self-hosting.md).

The stub grants `contents: read` alone, and passes `secrets:` only as the
caller's own channel guard requires (below). The check
needs no secret, and bureau-pipeline is public, so a Dependabot pull request,
with its empty secrets store and read-only token, runs it the same way. There
is no `paths:` filter, on purpose: a filtered-out pull request gets no check
run at all (`standards/engineering.md`, CI rule 1). The check passes a diff
that changes no UI file, and a repo with no design system, by itself.

The stub's `secrets:` follows the caller's own channel guard, read from its
`.github/bureau/pipeline-guard.json`. Where `secrets_style` is `inherit`, add
`secrets: inherit` to the `call` job. It passes nothing the check reads, and a
Dependabot pull request runs it the same way. Where it is `explicit` (a repo
outside `dreadnought-foundry`), the guard names every secret a stub passes.
So an `explicit` caller maps the seven canonical
secrets one per line, as the guard asks, under a `secrets:` key on the `call`
job: `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `LINEAR_API_KEY`,
`BUREAU_APP_ID`, `BUREAU_APP_PRIVATE_KEY`, `BUREAU_QA_APP_ID` and
`BUREAU_QA_APP_PRIVATE_KEY`, each as `${{ secrets.<NAME> }}`. The reusable
declares each one optional and reads none (DRE-8999), so the check runs the
same as it does for a stub that passes nothing.

## What it does

The reusable's one job, `parts-check`, is named `Parts check`, so the stub's
`call` job produces the check run **`call / Parts check`**. It checks out the
pull request's head with full history, checks out bureau-pipeline at
`pipeline_ref` into `.bureau-pipeline`, writes `git diff <base sha>...<head
sha>` to a file, and runs `.bureau-pipeline/scripts/parts_check.py check` on it
with the repo's checkout as `--repo-root`. The job's log carries the check's
own lines unchanged: its `parts-check:` status line and, on a failure, every
`parts-check: COPY …` and `parts-check: NO-SHARED-PART …` line.

The check's exit code is the job's. A copy of a shared part the diff adds, or
a UI file the diff adds or declares a component in that imports no shared
part, exits 1 and turns the check run red; so does a crash or a diff the check cannot read, which fails closed. The
merge gate holds any pull request with a check run that is not green
(`merge_gate.evaluate_checks`, `GREEN_CONCLUSIONS`), so a red Parts Check holds
the merge with no branch-protection change. On an event that is not a pull
request the job prints `parts-check: does not apply — not a pull request` and
passes.

## What a caller adds besides the stub

Add the stub's name, `Parts Check`, to the `workflow_run` list of the repo's
Merge Gate stub and of its Pipeline Medic stub:

```
on:
  workflow_run:
    workflows: [<the repo's other checks>, Parts Check]
```

The Merge Gate re-evaluates a pull request only when a workflow named in that
list completes, so a pull-request workflow missing from it is a check the
gate never re-evaluates on (DRE-2028, the rule `self-merge-gate.yml` states).
A pull request held by a red Parts Check would then wait for some other
workflow to finish before the gate read its fixed, green head. The Pipeline
Medic watches the workflows its list names, so a Parts Check run that crashed
is diagnosed rather than left red with nobody watching.

## Rollback

Delete the stub. Nothing else holds state: every run recomputes from the
head's files, and there is no receipt or marker to clean up.
