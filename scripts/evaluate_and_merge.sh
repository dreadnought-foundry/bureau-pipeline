#!/usr/bin/env bash
set -e
# The merge gate's `Evaluate and merge` step. .github/workflows/merge-gate.yml
# runs it as `bash .bureau-pipeline/scripts/evaluate_and_merge.sh`.
#
# ==== What this script does ====
#
# It decides whether one agent pull request merges now, and carries the
# decision out. It reads the pull request and its verdicts from GitHub, checks
# each merge condition through scripts/merge_gate.py, and then waits,
# dispatches the fix agent, holds for a person, refreshes a stale branch, or
# merges. Whenever it holds, it says so on the pull request. The rules live in
# merge_gate.py: this script gathers the records and acts on the answer, and
# restates no rule.
#
# Its inputs are the step's env:
#   PR              the pull request number, from the same expression the job's
#                   concurrency group is keyed on, so the serialization and the
#                   evaluation are always about the same pull request.
#   REPO_FULL       the calling repository, owner/name (github.repository).
#   GH_TOKEN        the qa-bot App's token. Every read, note, branch update and
#                   the merge itself run as the qa-bot, so author != merger.
#   WORKFLOW_TOKEN  the workflow's own token, for what the qa-bot App
#                   deliberately cannot do: list workflow runs, read the Agent
#                   Fix lane, dispatch the fix agent.
#   QA_LOGIN        the qa-bot's login, `<app-slug>[bot]`: the one author whose
#                   verdict counts, and the author of every note.
#   LINEAR_API_KEY  for the card writes linear_ops.py and code_owner_hold.py
#                   make.
# It runs in the calling repo's checkout with the pipeline at .bureau-pipeline,
# writes its records under /tmp, and runs under `set -euo pipefail`, so a
# required read that fails kills the step.
#
# In order, it:
#  1. Stops at a pull request that is not open, or whose branch is not one of
#     the pipeline's own: agent/* (card work), repair/* (red-main repair),
#     dependabot/* (dependency bumps), and the three scheduled branches
#     bot/standards-sync, bot/split-ledger and bot/model-drift, matched as
#     literals. The dependabot and bot branches carry no card. The set is
#     the one reconcile.PIPELINE_BRANCH_PREFIXES holds. It echoes the
#     `bureau-card` line when the branch names a card.
#  2. Gathers the records the decision reads, each from GitHub's own answer:
#     mergeability, the draft flag, the head sha and its check runs, the
#     three-dot compare of base against head, every page of the comments, the
#     workflow runs on the head, the author and the commits, the Agent Fix
#     lane, the open pull requests this branch carries, the code-owner rules,
#     and the pull request's body and creation time. A record that cannot be
#     read fails closed: it is written empty or unreadable, and the decision
#     waits or holds on it. The body and creation time are the exception: they
#     fail soft, an unread one is empty and holds nothing (DRE-5511).
#  3. Runs merge_gate.py over them. It prints `decision=` (merge, hold, wait,
#     conflict or human) and `reason=`, and optional lines: `carried=` and
#     `carried_content_id=` when a verdict carried across a head change, and
#     `stacked_on=`, `code_owner_review=` and `owner_hold=`. Each optional line
#     is read with `|| true`, because under pipefail a grep that finds nothing
#     kills the step. No `decision=merge` line, no merge.
#  4. Posts one carry note per content id when a verdict carried, and moves a
#     card it parked for a code-owner review back to In Review once that
#     review has landed.
#  5. Acts on the decision:
#     conflict  dispatches Agent Fix to reconcile the branch with its base,
#               except on a Dependabot branch, which Dependabot rebases.
#     human     posts one "waiting for human merge" note and stops.
#     hold      posts one note saying why: the open pull requests a stack hold
#               waits on; who must approve which folders, for a code-owner
#               hold, which also parks the card in Green Light; or, for any
#               other hold, `Merge gate: declined @<head> — <reason>`, once per
#               head and reason.
#     wait      stops quietly. A live Agent Fix run on this pull request, or an
#               unfinished CI run on the head, is a wait.
#     Anything but merge exits 0 there.
#  6. Asks scripts/order_sensitive_refresh.py whether merging now could fork
#     the base: `refresh` updates the branch from the base and posts one note
#     per base tip, `wait` stops, `proceed` goes on.
#  7. Moves the card to In Review if it is still In Progress, merges pinned to
#     the evaluated head, and comments the merge on the card. A merge refused
#     because the head moved exits 0; one refused with the head unmoved is a
#     real failure, explained and red.
# No note carries verdict-shaped text, so none passes for an approval or wakes
# the gate's own comment trigger.
#
# ==== Incident history ====
#
# One note per card, in the order each card's first commit reached `main`.
# Where a later card changed what an earlier one did, the note states what
# holds now and names the cards that got it there.
#
# DRE-1990 (2026-07-09). A verdict counts only for the head it reviewed. The
#   merge itself is pinned to that head too (DRE-2117).
# DRE-1992 (2026-07-10). The merge rules moved out of this shell into
#   merge_gate.py, which this script only feeds. The check runs come from the
#   REST API, since `gh pr checks` needs the actions:read the qa-bot App
#   deliberately lacks.
# DRE-1994 (2026-07-10). CI green leaves out the review runs by VERIFIED
#   ORIGIN, never by name: a job calling itself "sneaky-review" still counts.
#   The runs listing says which workflow file produced each check suite, and
#   merge_gate.py drops only the check runs in a suite of qa-review.yml or
#   pr-review.yml. The listing needs actions:read, so WORKFLOW_TOKEN reads it.
# DRE-1927 (2026-07-11). repair/* branches, the red-main auto-repair pull
#   requests, merge here on the same terms as agent/*.
# DRE-2037 (2026-07-11). An update-branch by the qa-bot fires `synchronize` as
#   the qa-bot, which qa-review.yml and verify.yml admit; the workflow's own
#   token would fire nothing. That is why the fork refresh runs on GH_TOKEN.
# DRE-2039 (2026-07-11). Dependabot pull requests (dependabot/*) merge here,
#   minor and patch bumps only. The author is REST's `user.login`, the literal
#   `dependabot[bot]` (gh's GraphQL rendering varies by version), and the
#   semver level is the update-type trailer in the commits. An unreadable
#   record leaves the gate waiting. A major or an unprovable level is `human`:
#   one note, never a merge. The fix agent is never sent to a Dependabot
#   conflict; Dependabot rebases its own.
# DRE-2056 (2026-07-12). In the self-host repo agent-fix.yml is the reusable
#   workflow, workflow_call only, so dispatching it answers 422 (which the
#   `|| true` swallowed). There the conflict arm dispatches self-agent-fix.yml,
#   the resolution reconcile.fix_workflow() makes for the sweep.
# DRE-2117 (2026-07-16). The merge carries --match-head-commit for the head
#   every condition was evaluated against, so a push since makes GitHub refuse
#   it (405) rather than merge a head the critic never reviewed: the DRE-1990
#   skew at the last step. A refusal with the head moved, or unverifiable,
#   exits 0 and the new head's events re-run the gate; with the head unmoved
#   it is a real failure and the run goes red.
# DRE-2121 (2026-07-16). UNKNOWN mergeability is GitHub computing it lazily,
#   not a conflict; reconcile owns the re-read poll.
# DRE-2340 (2026-08-10). A verdict binds to the pull request's CONTENT.
#   merge_gate.py computes a content id from the files[] of the compare
#   record, which is passed whole, so a verdict survives a base merge that
#   touches nothing the pull request changes. A failed compare read is `{}`:
#   no content id, no carry, and a stale verdict needs a fresh review. A carry
#   posts one note, keyed on the note's own ASCII marker plus the content id;
#   the verdict comment carries the bare id too, so matching on it alone would
#   always read as posted. Its once-only check still reads the comments
#   fetched earlier rather than re-reading late like gate_note.py; the per-PR
#   concurrency group keeps two evaluations of one pull request from
#   overlapping. On 2026-08-10 an absent `carried=` line made grep exit 1,
#   killed the step under pipefail before any arm ran, and took the fleet's
#   gate down from 02:46:16. Every optional field is read with `|| true` since.
# DRE-2426 (2026-08-13). The branch set is the one
#   reconcile.PIPELINE_BRANCH_PREFIXES holds, and
#   tests/test_pipeline_ownership.py fails if they differ. Eight hand-copied answers to that question stranded
#   four pull requests for ten hours.
# DRE-2508 (2026-08-18). The `human` note goes through scripts/gate_note.py,
#   not a grep over the comments fetched earlier: two evaluations of one pull
#   request both read before either wrote, and both posted (bureau-pipeline
#   #157, 2026-08-17). gate_note.py reads as late as it can, posts, keeps the
#   earliest note, and counts only the gate's own login, so a person quoting
#   the marker cannot silence it. Every note added since posts through it.
# DRE-2416 (2026-08-20). A conflict is condition 0 of merge_gate.py, fed
#   GitHub's mergeStateStatus, and the `conflict` arm dispatches the fix agent:
#   `update-branch` cannot resolve a textual conflict, and the fix agent's
#   merge moves the head, so a fresh review follows. The shell used to branch
#   on mergeability itself. A branch only behind its base is no longer
#   refreshed or held: the compare record's status is a behind/ahead note and
#   gates nothing. The one branch write the gate still makes is the fork
#   refresh (DRE-4912).
# DRE-2777 (2026-08-27). bot/standards-sync, the nightly dreadnought-standards
#   regeneration, merges here card-less like dependabot/*: a nightly job has no
#   card of its own, and a fixed id would have linear-sync re-close a long-done
#   card every night.
# DRE-2681 (2026-08-28). `?per_page=100` alone is the unpaginated defect with
#   a higher ceiling, so the comments read paginates (DRE-4139).
# DRE-3138 (2026-09-05). reconcile.py refreshes a stale merge ref with the same
#   loud update-branch write the fork refresh makes.
# DRE-3467 (2026-09-09). GitHub will not merge a draft ("Pull Request is still
#   a draft"), and PR #323 was decided `merge` twice and went red twice. The
#   draft flag is read from GitHub and handed to condition 4, with no
#   `|| true`: an unreadable draft state kills the step, because defaulting to
#   "not a draft" would re-arm the failure.
# DRE-3879 (2026-09-15). bot/split-ledger and bot/model-drift are this repo's
#   two scheduled derivations. Both pushed to `main` and both failed every run
#   on branch protection (GH006), so each commits to its own fixed branch and
#   rides one pull request, card-less. The CEO added exactly these two names
#   on 2026-09-15 (signed console answer). They are matched as literals, never
#   `bot/*`, which would hand auto-merge to any branch later named that way.
#   CI green and a SHA-bound critic APPROVE still decide.
# DRE-4139 (2026-09-17). The comments are read on every page, with
#   `--paginate --slurp`, and merge_gate.py flattens the per-page arrays.
#   Unpaginated, the read saw only the OLDEST 30, so past thirty comments no
#   new verdict entered the window and the run still reported success:
#   agent-bureau#2588 was stranded with APPROVE and PASS on a green head.
#   --slurp buffers, so a failure mid-pagination yields nothing and the `[]`
#   substitute reads as "no verdicts yet", never as a partial record. The
#   carry note's check reads the same record, so it sees every page too.
# DRE-4407 (2026-09-20). The run echoes a `bureau-card` line, so a run GitHub
#   records on `main` names its card; qa-review.yml says why. Only the echoed
#   line: this job's name is a check-run name the reconcile sweep and branch
#   protection read. The echo is the one line here carrying the marker with
#   its colon: tests/test_dispatched_run_names_its_card.py runs this file up
#   to the first such line, and a comment carrying it would end the run there.
# DRE-4486 (2026-09-21). Four times an Agent Fix run finished after the merge
#   and pushed onto a branch that had already merged. stranded_fix.py reads
#   which pull requests have a fix run queued or running, and condition F
#   waits on this one's. An unreadable lane is written into the record and
#   waited on, never read as idle. The why is
#   architecture/decisions/adr-stranded-fix-after-merge.md.
# DRE-5045 (2026-09-27). The runs listing also shows a CI run queued with no
#   jobs yet, so condition 1 waits while any non-review workflow run on the
#   head is unfinished. A failed listing writes `{"readable":false}`, never an
#   empty list.
# DRE-4103 (2026-09-28). On 2026-09-16 the gate merged an approved pull request
#   whose branch carried two open pull requests under REQUEST_CHANGES, and
#   GitHub marked those merged too. stacked_prs.py reads every open pull
#   request and the thread of each whose head this branch carries, and
#   condition S holds. The stack note names the blocking set in its marker, so
#   a changed set posts again and an unchanged one does not. An unreadable
#   record holds: a blip is never "no other open pull requests".
# DRE-4341 (2026-09-28). code_owner_hold.py reads the base branch's rules,
#   this pull request's reviews and CODEOWNERS, and condition O holds while a
#   required code-owner review is missing. GitHub's refusal becomes one note
#   per head naming who must approve which folders, and the card parks in
#   Green Light once, where the calling stub wakes the gate on a review. A
#   failed read is UNKNOWN, which merges as before and stays loud if GitHub
#   refuses. Once the review lands (`code_owner_review=met`) the card goes back
#   to In Review, but only one carrying the gate's own hold marker. A real
#   merge failure runs `code_owner_hold.py explain`, so the medic diagnoses
#   from facts.
# DRE-4912, DRE-5070 (2026-09-29). Merging a branch that is behind can fork
#   `main` when both added a file under the same order-sensitive path. The fork
#   refresh is the gate's one branch write, and not the freshness rule DRE-2416
#   retired. What `main` gained since the merge base is read only when the
#   branch is behind and the repo declares .github/bureau/merge-recheck.json,
#   so a current head or an undeclared repo costs no call; a failed read is
#   `{}`, answered `wait`. The refresh module rules on that, the compare
#   record and the receipts (the comments, flattened; unflattenable is
#   `unreadable`, so it waits rather than refresh twice). It exits 2 on an
#   unreadable compare record, which reads as `wait`; any other failure is red.
#   `refresh` updates the branch under the qa-bot token with
#   `expected_head_sha`, so a push since the read is a 422, not an update of an
#   unevaluated head, then posts one note keyed on the base tip. A refused
#   update posts nothing and the next evaluation decides again. A crash
#   between the two leaves a moved head and no note, and the next evaluation
#   reads `behind_by: 0` and goes on to CI's answer. Refresh and wait exit
#   before the card moves: neither is a merge.
# DRE-5228 (2026-10-01). Every hold now says so on the pull request. A verifier
#   FAIL, a critic REQUEST_CHANGES, a stale or unbound verdict or an unreadable
#   stack used to fall through to the merge guard: a green run and nothing on
#   the thread. The stack and code-owner holds already spoke, so one note goes
#   up for a hold carrying neither line, through gate_note.py, quoting
#   merge_gate.py's `reason=` verbatim. No reason carries verdict-shaped text
#   (tests/test_merge_gate_hold_note.py pins it). The marker is the head and
#   the reason, since gate_note.py treats a note as posted when its first line
#   opens with the marker, and a head-only marker would match a note for
#   another reason. That match also wants a word boundary after the marker
#   (merge_gate.opens_with_marker), so the marker drops trailing non-word
#   characters (the stack-UNKNOWN reasons end `(DRE-4103)`) while the note's
#   first line keeps them. A failed post leaves the hold standing; the next
#   wake retries. The same card made the gate wake on the Verifier's verdict
#   as well as the critic's (the job's `if:` in merge-gate.yml).
# DRE-2810, DRE-3130, DRE-5230 (2026-08-29 to 2026-10-01). The cost of that hold note is bounded. On a
#   stub that keys Agent Fix's concurrency group on the commenter's login, the
#   note queues a fix run that skips at its job gate, and GitHub keeps one
#   pending run per group, so it can evict a verdict-triggered fix run still
#   pending (DRE-2810). gate_note.py posts once per marker, so that is at most
#   one eviction per head and reason: once per critic rejection or verifier
#   FAIL, never per wake. The sweep's standing-verdict route re-dispatches an
#   evicted run twenty minutes after the verdict, for a critic rejection
#   (DRE-3130) and for a verifier FAIL (DRE-5230). A stub keying the group on
#   the comment body queues the note's run alone and evicts nothing.
# DRE-5383 (2026-10-02). The step's `run:` block moved into this file,
#   unchanged line for line. While it was a block, a single Actions
#   expression in it made Actions compile all of it into one format()
#   expression with a 21,000-character ceiling
#   (tests/test_workflow_expression_budget.py), and DRE-4407 and DRE-5045
#   trimmed their comments to fit. DRE-4486 moved every github.repository and
#   github.token expression into the step's env, and DRE-4103 moved the last
#   one, QA_LOGIN's app-slug derivation. This file holds no expression, so
#   there is no ceiling to trim for.

set -euo pipefail

BRANCH=$(gh pr view "$PR" --json headRefName,state --jq .headRefName)
STATE=$(gh pr view "$PR" --json state --jq .state)
[ "$STATE" != "OPEN" ] && { echo "PR #$PR is $STATE — nothing to do"; exit 0; }
# The pipeline's own branches, as literals (DRE-2426 keeps this set and reconcile's one).
case "$BRANCH" in agent/*|repair/*|dependabot/*|bot/standards-sync|bot/split-ledger|bot/model-drift) ;; *) echo "not an agent branch — skip"; exit 0;; esac
CARD=$(printf '%s' "$BRANCH" | grep -oiE 'DRE-[0-9]+' | head -1 | tr '[:lower:]' '[:upper:]' || true)
# The ECHOED line only: this job's name is a check-run name (DRE-4407).
if [ -n "$CARD" ]; then echo "bureau-card: $CARD"; fi

# Condition 0; UNKNOWN is GitHub still computing, not a conflict (DRE-2416, DRE-2121).
MSTATE=$(gh pr view "$PR" --json mergeStateStatus --jq .mergeStateStatus)

# No `|| true`, on purpose: an unreadable draft state kills the step (DRE-3467).
IS_DRAFT=$(gh pr view "$PR" --json isDraft --jq .isDraft)

# REST check runs, not `gh pr checks`, which needs actions:read (DRE-1992).
SHA=$(gh pr view "$PR" --json headRefOid --jq .headRefOid)
gh api "repos/$REPO_FULL/commits/$SHA/check-runs" > /tmp/check-runs.json
# The compare record, passed whole; a blip is `{}` and carries nothing (DRE-2340).
BASE=$(gh pr view "$PR" --json baseRefName --jq .baseRefName)
gh api "repos/$REPO_FULL/compare/$BASE...$SHA" > /tmp/compare.json 2>/dev/null \
  || echo '{}' > /tmp/compare.json
# The body and createdAt for the What's new: condition (DRE-5511). A blip is an
# empty file and an empty time, never omitted flags: either reads as "nothing was
# read" and the gate decides as before. DRE-5576 switches the rule on.
gh pr view "$PR" --json body,createdAt > /tmp/pr-view.json 2>/dev/null || : > /tmp/pr-view.json
jq -r '.body // ""' /tmp/pr-view.json > /tmp/pr-body.txt 2>/dev/null || : > /tmp/pr-body.txt
CREATED_AT=$(jq -r '.createdAt // ""' /tmp/pr-view.json 2>/dev/null || true)
# Every page, slurped; a failed read is "no verdicts yet" (DRE-4139, DRE-2681).
gh api --paginate --slurp \
  "repos/$REPO_FULL/issues/$PR/comments?per_page=100" \
  > /tmp/comments.json 2>/dev/null \
  || echo '[]' > /tmp/comments.json
# The workflow's own token; a blip is the unreadable marker (DRE-1994, DRE-5045).
GH_TOKEN="$WORKFLOW_TOKEN" gh api "repos/$REPO_FULL/actions/runs?head_sha=$SHA&per_page=100" > /tmp/workflow-runs.json 2>/dev/null \
  || echo '{"readable":false}' > /tmp/workflow-runs.json
# A blip is an empty record, and the gate waits on it (DRE-2039).
AUTHOR=$(gh api "repos/$REPO_FULL/pulls/$PR" --jq .user.login)
gh api "repos/$REPO_FULL/pulls/$PR/commits?per_page=100" > /tmp/pr-commits.json 2>/dev/null \
  || echo '[]' > /tmp/pr-commits.json
# The workflow's own token; an unreadable lane is written in and waited on (DRE-4486).
GH_TOKEN="$WORKFLOW_TOKEN" python3 .bureau-pipeline/scripts/stranded_fix.py lane \
  --repo "$REPO_FULL" --out /tmp/fix-lane.json
# An unreadable stack is written in and held on (DRE-4103).
python3 .bureau-pipeline/scripts/stacked_prs.py gather \
  --repo "$REPO_FULL" --pr "$PR" \
  --compare-file /tmp/compare.json --out /tmp/stack.json
# A failed owners read is UNKNOWN (DRE-4341).
python3 .bureau-pipeline/scripts/code_owner_hold.py gather \
  --repo "$REPO_FULL" --pr "$PR" --base "$BASE" \
  --compare-file /tmp/compare.json --out /tmp/owners.json

python3 .bureau-pipeline/scripts/merge_gate.py \
  --head-sha "$SHA" \
  --qa-login "$QA_LOGIN" \
  --check-runs-file /tmp/check-runs.json \
  --comments-file /tmp/comments.json \
  --workflow-runs-file /tmp/workflow-runs.json \
  --compare-file /tmp/compare.json \
  --merge-state "$MSTATE" \
  --is-draft "$IS_DRAFT" \
  --review-workflows ".github/workflows/qa-review.yml,.github/workflows/pr-review.yml" \
  --head-branch "$BRANCH" \
  --pr-author "$AUTHOR" \
  --pr-commits-file /tmp/pr-commits.json \
  --fix-lane-file /tmp/fix-lane.json \
  --pr-number "$PR" \
  --stack-file /tmp/stack.json \
  --owners-file /tmp/owners.json \
  --pr-body-file /tmp/pr-body.txt \
  --pr-created-at "$CREATED_AT" \
  | tee /tmp/gate-decision
# Fail-closed on shape drift: no `decision=merge` line, no merge.
DECISION=$(grep -m1 '^decision=' /tmp/gate-decision | cut -d= -f2-)

# Optional fields: `|| true`, or an absent line kills the step (DRE-2340).
CARRIED=$(grep -m1 '^carried=' /tmp/gate-decision | cut -d= -f2- || true)
CARRIED_ID=$(grep -m1 '^carried_content_id=' /tmp/gate-decision | cut -d= -f2- || true)
# Idempotent on the content id, read from the comments record above (DRE-2340, DRE-2508).
CARRY_MARK="carried verdict content:$CARRIED_ID"
if [ -n "$CARRIED" ] && ! grep -q "$CARRY_MARK" /tmp/comments.json; then
  printf '%s\n\n%s\n\n%s\n' \
    "♻️ Merge gate: $CARRY_MARK — the standing review verdict was carried across a branch update." \
    "Reviewed at $CARRIED; current head $SHA. This branch's own changes are byte-identical to what was reviewed: the head moved only because $BASE was merged in, and that merge touched nothing this pull request changes." \
    "Any change to this pull request's own diff changes the fingerprint above and the verdict dies (DRE-2340). CI still re-runs on the merged result." \
    > /tmp/carry-note.md
  gh api "repos/$REPO_FULL/issues/$PR/comments" \
    -F body=@/tmp/carry-note.md >/dev/null || true
fi

# Release a card the gate parked for a code-owner review (DRE-4341).
COR=$(grep -m1 '^code_owner_review=' /tmp/gate-decision | cut -d= -f2- || true)
if [ -n "$CARD" ] && [ "$COR" = "met" ]; then
  python3 .bureau-pipeline/scripts/code_owner_hold.py release --card "$CARD" --pr "$PR"
fi

# conflict: the fix agent reconciles the branch with its base (DRE-2416).
if [ "$DECISION" = "conflict" ]; then
  # Dependabot rebases its own pull requests (DRE-2039).
  case "$BRANCH" in dependabot/*)
    echo "dependabot PR has a conflict — dependabot rebases its own PRs; not dispatching the fix agent"
    exit 0;;
  esac
  echo "merge conflict with $BASE — dispatching fix agent"
  # The self-host repo dispatches its stub, self-agent-fix.yml (DRE-2056).
  FIX_WF=agent-fix.yml
  [ "$REPO_FULL" = "dreadnought-foundry/bureau-pipeline" ] && FIX_WF=self-agent-fix.yml
  # The workflow's own token: the qa-bot App has no Actions permissions.
  GH_TOKEN="$WORKFLOW_TOKEN" gh workflow run "$FIX_WF" \
    --repo "$REPO_FULL" \
    --ref "$(gh api repos/$REPO_FULL --jq .default_branch)" \
    -f pr_number="$PR" || true
  exit 0
fi
# human: one note, through gate_note.py (DRE-2039, DRE-2508).
if [ "$DECISION" = "human" ]; then
  REASON=$(grep -m1 '^reason=' /tmp/gate-decision | cut -d= -f2-)
  HUMAN_MARK="Merge gate: waiting for human merge"
  printf '⏸️ %s — %s\n' "$HUMAN_MARK" "$REASON" > /tmp/human-note.md
  python3 .bureau-pipeline/scripts/gate_note.py \
    --repo "$REPO_FULL" \
    --pr "$PR" \
    --author "$QA_LOGIN" \
    --marker "$HUMAN_MARK" \
    --body-file /tmp/human-note.md
  exit 0
fi
# A stack hold names the pull requests it waits on (DRE-4103).
STACKED=$(grep -m1 '^stacked_on=' /tmp/gate-decision | cut -d= -f2- || true)
if [ -n "$STACKED" ]; then
  REASON=$(grep -m1 '^reason=' /tmp/gate-decision | cut -d= -f2-)
  STACK_MARK="Merge gate: held for $STACKED until approved"
  printf '⏸️ %s — %s\n' "$STACK_MARK" "$REASON" > /tmp/stack-note.md
  python3 .bureau-pipeline/scripts/gate_note.py \
    --repo "$REPO_FULL" \
    --pr "$PR" \
    --author "$QA_LOGIN" \
    --marker "$STACK_MARK" \
    --body-file /tmp/stack-note.md
fi
# A code-owner hold notes who must approve and parks the card (DRE-4341).
OWNER_HOLD=$(grep -m1 '^owner_hold=' /tmp/gate-decision | cut -d= -f2- || true)
if [ -n "$OWNER_HOLD" ]; then
  python3 .bureau-pipeline/scripts/code_owner_hold.py hold \
    --repo "$REPO_FULL" --pr "$PR" --author "$QA_LOGIN" \
    --head-sha "$SHA" --card "$CARD" --groups "$OWNER_HOLD"
  exit 0
fi
# Any other hold: one note per head and reason (DRE-5228, DRE-2810, DRE-3130, DRE-5230).
if [ "$DECISION" = "hold" ] && [ -z "$STACKED" ] && [ -z "$OWNER_HOLD" ]; then
  REASON=$(grep -m1 '^reason=' /tmp/gate-decision | cut -d= -f2-)
  HOLD_LINE="Merge gate: declined @$SHA — $REASON"
  HOLD_MARK=$(python3 -c 'import re, sys; print(re.sub(r"\W+$", "", sys.argv[1]))' "$HOLD_LINE")
  printf '⏸️ %s\n\n%s\n' "$HOLD_LINE" \
    "Not merged. The gate looks again on the next CI completion, review or sweep, and merges once nothing holds it." \
    > /tmp/hold-note.md
  python3 .bureau-pipeline/scripts/gate_note.py \
    --repo "$REPO_FULL" \
    --pr "$PR" \
    --author "$QA_LOGIN" \
    --marker "$HOLD_MARK" \
    --body-file /tmp/hold-note.md \
    || echo "the hold note did not post — the hold stands; the next evaluation tries again"
fi
[ "$DECISION" = "merge" ] || exit 0

# The fork refresh, the gate's one branch write (DRE-4912, DRE-5070).
BEHIND=$(python3 -c 'import json, sys; b = json.load(open(sys.argv[1])).get("behind_by"); print(b if type(b) is int and b > 0 else 0)' /tmp/compare.json 2>/dev/null || echo 0)
echo '{}' > /tmp/base-advance.json
if [ "$BEHIND" -gt 0 ] && [ -f .github/bureau/merge-recheck.json ]; then
  MERGE_BASE=$(python3 -c 'import json, sys; print((json.load(open(sys.argv[1])).get("merge_base_commit") or {}).get("sha") or "")' /tmp/compare.json 2>/dev/null || true)
  if [ -n "$MERGE_BASE" ]; then
    gh api "repos/$REPO_FULL/compare/$MERGE_BASE...$BASE" > /tmp/base-advance.json 2>/dev/null \
      || echo '{}' > /tmp/base-advance.json
  fi
fi
python3 -c 'import json, sys; sys.path.insert(0, ".bureau-pipeline/scripts"); import merge_gate; json.dump(merge_gate.flatten_pages(json.load(open("/tmp/comments.json"))), sys.stdout)' \
  > /tmp/receipts.json 2>/dev/null || echo 'unreadable' > /tmp/receipts.json
REFRESH_RC=0
python3 .bureau-pipeline/scripts/order_sensitive_refresh.py decide \
  --compare-file /tmp/compare.json \
  --base-advance-file /tmp/base-advance.json \
  --declaration-file .github/bureau/merge-recheck.json \
  --receipts-file /tmp/receipts.json \
  > /tmp/refresh-decision || REFRESH_RC=$?
# Exit 2 is an unreadable compare record, read as `wait`; any other failure is loud.
if [ "$REFRESH_RC" = 2 ]; then
  printf 'decision=wait\nreason=the compare record could not be read\n' > /tmp/refresh-decision
elif [ "$REFRESH_RC" != 0 ]; then
  echo "order_sensitive_refresh.py failed (exit $REFRESH_RC) — not merging"
  exit 1
fi
sed 's/^/fork-refresh: /' /tmp/refresh-decision
REFRESH=$(grep -m1 '^decision=' /tmp/refresh-decision | cut -d= -f2- || true)
REFRESH_REASON=$(grep -m1 '^reason=' /tmp/refresh-decision | cut -d= -f2- || true)
# refresh: update-branch as the qa-bot, pinned to the evaluated head (DRE-3138, DRE-2037).
if [ "$REFRESH" = "refresh" ]; then
  TIP=$(python3 -c 'import json, sys; print((json.load(open(sys.argv[1])).get("base_commit") or {}).get("sha") or "")' /tmp/compare.json 2>/dev/null || true)
  if gh api -X PUT "repos/$REPO_FULL/pulls/$PR/update-branch" -f expected_head_sha="$SHA" >/dev/null; then
    REFRESH_MARK="Merge gate: refreshed onto $TIP"
    printf '♻️ %s — %s\n\n%s\n' "$REFRESH_MARK" "$REFRESH_REASON" \
      "Not merged: $BASE gained a file under the same order-sensitive path since this branch's merge base, so merging now could fork $BASE (DRE-4912). The branch was updated from $BASE and CI re-runs on the result, where the repo's own check sees both files together. The standing review carries if this pull request's own changes are unchanged (DRE-2340)." \
      > /tmp/refresh-note.md
    python3 .bureau-pipeline/scripts/gate_note.py \
      --repo "$REPO_FULL" \
      --pr "$PR" \
      --author "$QA_LOGIN" \
      --marker "$REFRESH_MARK" \
      --body-file /tmp/refresh-note.md \
      || echo "the refresh note did not post — the branch update stands"
  else
    echo "update-branch refused for $SHA — not merging; the next evaluation decides again"
  fi
  exit 0
fi
[ "$REFRESH" = "proceed" ] || { echo "fork refresh: waiting — ${REFRESH_REASON:-no decision read}"; exit 0; }

# Both gates green: advance card, merge as qa-bot.
[ -n "$CARD" ] && python3 .bureau-pipeline/scripts/linear_ops.py advance "$CARD" "In Review" "In Progress" || true
# Pinned to the evaluated head; a moved head is benign (DRE-2117, DRE-1990).
if ! gh pr merge "$PR" --merge --delete-branch --match-head-commit "$SHA"; then
  NOW=$(gh pr view "$PR" --json headRefOid --jq .headRefOid 2>/dev/null || true)
  if [ -n "$NOW" ] && [ "$NOW" = "$SHA" ]; then
    echo "merge failed with the head still at $SHA — real failure"
    # Name what the base branch requires and what is met, for the medic (DRE-4341).
    python3 .bureau-pipeline/scripts/code_owner_hold.py explain \
      --owners-file /tmp/owners.json \
      --check-runs-file /tmp/check-runs.json --author "$AUTHOR" || true
    exit 1
  fi
  echo "head moved since evaluation (was $SHA, now ${NOW:-unverifiable}) — not merging; the gate re-runs on the new head's events"
  exit 0
fi
echo "merged PR #$PR"
[ -n "$CARD" ] && python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
  "🔀 Auto-merged by qa-bot: CI green + critic APPROVE. PR: $(gh pr view "$PR" --json url --jq .url)" || true
