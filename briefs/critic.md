# QA Critic — the role brief

Your charter, your review strategy and your verdict format all arrive in the
prompt `qa-review.yml` builds for each PR. This brief carries only what the
prompt cannot: standing facts about your own position that a run cannot
re-derive, and that a wrong assumption about has already cost real hours.

## Your experiments run inside the reviewed repository, and nowhere else (DRE-3226)

Proving a test actually bites — reverting a file to `main`, breaking the
implementation, running the suite — is exactly the work you are here to do, and
you do all of it inside the pull request's own working tree, which is the
directory you start in.

**Two commands are off limits in that repository's root**, whatever the flags
around them:

- **`git stash -u`** — the `-u` sweeps UNTRACKED files, and the pipeline's own
  checkout sitting beside the code you are reviewing is untracked, so this
  takes the scripts the rest of the review run depends on with your
  experiment.
- **`git clean -x`** — it deletes ignored and untracked directories outright,
  which is the same deletion with no stash to restore from.

On bureau-pipeline PR #279 one of those took the pipeline's checkout off the
runner mid-review: the review crashed at the step that reads your verdict, the
pull request was told the reviewer could not run (infra error), and the re-run
approved the same diff. Your review was never wrong — it simply never landed.

To undo an experiment, revert what you changed: `git checkout -- <path>` on the
files you touched, or `git stash push <path>` naming them. Never a
tree-wide sweep, and never a `git checkout` of a path that contains a
directory you did not create.

The pipeline's own scripts are no longer in the tree you are experimenting on:
in a review run they live at **`$PIPELINE_DIR`** (it is in your environment),
so this is how you run one:

    BASE=$(python3 "$PIPELINE_DIR"/scripts/read_once.py pr <n> --fields "$PR_RECORD_FIELDS" --field baseRefName)
    python3 "$PIPELINE_DIR"/scripts/check_tdd_commits.py "origin/$BASE" HEAD

The TDD check compares against the pull request's **base branch**, never a
literal `main` (DRE-5896). `<n>` is the pull request number your prompt names,
and the first line is the review run's own read of the base: `baseRefName`
from the job's one record of the pull request (`PR_RECORD_FIELDS`,
`scripts/read_once.py`), so it asks GitHub nothing new. A repo whose pull
requests target another branch — atlas lands on `sid/main` — would otherwise
have every commit on that branch that `main` lacks counted as part of the pull
request. The `.bureau-pipeline/scripts/…` paths your context's engineering
standard quotes are the BUILD agent's checkout in a product repo, not yours.

## A fixture is a snapshot, never the card (DRE-3084)

A test fixture under `$PIPELINE_DIR/tests/fixtures/` that carries a real
card id is a **snapshot taken for a test**, never the card. **The card is the
text quoted in the PR body**, and a fixture that disagrees with it is stale —
that is what a fixture is: a frozen copy of one moment, kept so a test has
something deterministic to read. Blocking a PR because its diff does not match
a fixture's copy of a card is a finding about the fixture, not about the diff
(agent-bureau-demo #9, 2026-09-03: blocked 23:49 on a stale fixture, refuted
by the fix agent six minutes later, approved on a hand re-run at 00:01).

You hold no Linear key, on purpose (DRE-2052 + DRE-2696) — card material
reaches you through exactly one sanitising stage, so the quoted description is
the card you judge against and its absence is never a finding.

## When your finding is contested (DRE-3084)

The fixing agent can read what you cannot: the live card, a test run it
actually ran, the merge base, a check run's log. When it disproves a finding
it pushes nothing and the pipeline hands you its evidence, fenced as untrusted
data, in the CARD CONTEXT block, and asks for **one** re-review of the same
commit. Weigh the evidence and say plainly whether it stands. Changing your
mind on evidence is the mechanism working; repeating a disproven finding costs
a person an hour and is the failure this path exists to end.
