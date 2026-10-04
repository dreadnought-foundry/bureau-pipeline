#!/usr/bin/env bash
set -e
# What this script does
#
# The `Report` step of agent-fix.yml runs this file once the fix agent
# has finished. It says on the pull request what the round did, or why
# it did nothing, and moves the card when a person has to step in. It
# posts nothing it cannot prove belongs to this run.
#
# 1. It proves the critic verdict the run worked from is this run's:
#    `fix_handoff.py check` reads the handoff the job opened, keyed to
#    (repo, PR, head). When that fails the script prints an error, posts
#    nothing and exits 1. `fix_handoff.py attribution` then gives the
#    one line every comment it posts ends with, naming the card and the
#    pull request the comment answers.
# 2. It handles a pull request that merged while the run was working.
#    `stranded_fix.py local-compare` lists the local commits the base
#    does not hold, and `stranded_fix.py route` decides whether any work
#    is stranded and whether the push reached the branch. When work is
#    stranded it files one card with `linear_ops.py oneoff`, unless
#    `find-open` finds that card already. It posts nothing on the merged
#    pull request and exits 0.
# 3. It reads the agent's three handoff files through `fix_handoff.py
#    read`: the refutation, the blocker and the conflict agent's no-push
#    reason. Exit 4 means the file is not this run's, and the script
#    refuses.
# 4. It reads the answer format from `fix_context.py --answer-format`.
#    Every comment that holds the pull request quotes it.
# 5. It routes the round. The first of these that holds takes it:
#    - Refutation: the agent answered the finding with evidence. The
#      first one on this head posts `fix-finding-refuted`, then
#      dispatches the critic once more on DISPATCH_TOKEN (pr-review.yml
#      in this repo, qa-review.yml elsewhere). If the dispatch fails, the
#      card is parked. A second one on the same head posts
#      `fix-attempt-disputed` with both quoted and parks the card.
#    - Blocker: the agent disputes the finding and pushed nothing. It
#      posts `fix-attempt-disputed` with the answer format and parks.
#    - The head did not move: `fix_dead_run.py decide` reads every page
#      of the thread and answers retry (an outage), retry-turns (out of
#      turns, retried once), hold-turns, hold, or anything else, which
#      is no progress. The no-progress body comes from `fix_budget.py
#      no-push`. Every answer but the two retries parks the card.
#    - The head moved: it posts `fix-attempt-landed`, worded as a fix
#      attempt or, in conflict mode, a conflict resolution round. In fix
#      mode the round's convergence classification from
#      `fix_convergence.py` is appended.
#    Every receipt is composed by `pipeline_act.py receipt` and falls
#    back to the bare body with `|| printf` when composition fails.
# 6. Parking means the `needs-human` label and a move to Triage through
#    `linear_ops.py advance`, falling back to `state --park`. Each park
#    also posts a plain-English note on the card. A draft's card is never
#    parked: `park_for_human` reads the PR's `isDraft` first and leaves the
#    card where it is when it is `true`.
#
# Inputs, all from the step's `env:`:
#   GH_TOKEN         the worker App's token: every PR read and comment
#   LINEAR_API_KEY   Linear's key, read by linear_ops.py
#   CARD             the card's identifier; empty skips every card write
#   PRE_SHA          the PR head the run started from, the handoff's key
#                    and the "did the head move" baseline
#   DISPATCH_TOKEN   the workflow's own github.token, used only for the
#                    re-review dispatch
#   CLASSIFICATION   the round's convergence classification, composed by
#                    fix_convergence.py
#   REPO             the repository, owner/name
#   PR               the pull request number
#   ATTEMPT          this round's attempt number
#   MODE             `fix` or `conflict`
#   EXEC_FILE        the agent's execution record (default: the runner's
#                    claude-execution-output.json)
#   RUN_URL          this run's URL, named in the dead-run and no-push
#                    bodies
# RUNNER_TEMP, which the runner sets, holds the handoff.
#
# Incident history
#
# One note per rule, in the date order git history gives for when it
# reached this block. Where a later card changed a rule, its note states
# the rule as it holds now and names every card that shaped it.
#
# 2026-06-28, DeltaSolv PR #64/#74. Only a new commit re-runs CI and the
#   critic. A dispute or a fix that pushed nothing leaves the latest
#   verdict at REQUEST_CHANGES, the critic never re-fires and the merge
#   gate holds for good: a ~20h stall that ended in a hand merge. So a
#   round with no forward progress goes to a person, never to a "review
#   re-running" receipt.
# 2026-07-10, DRE-1995. Every marker the script counts is read only when
#   the worker bot wrote it. Anyone can comment a marker, and a planted
#   one would burn a re-review or park a healthy PR.
# 2026-07-11, DRE-2018, DRE-4139. A model death is not a failed fix. The
#   2026-07-10 DeltaSolv token outage read as agent failures in the CEO's
#   queue. `fix_dead_run.py` answers retry for an outage (a marker the
#   reconcile sweep re-dispatches on, with no park and no attempt spent)
#   and hold after the cap. The cap counts consecutive worker-bot deaths
#   since the last push, so a recovered outage does not pre-exhaust it
#   (DRE-2018). It reads every page of the thread: unpaginated, the read
#   saw GitHub's oldest 30 comments, and on a long PR a dead loop kept
#   being retried (DRE-4139, 2026-09-17).
# 2026-08-12, DRE-2409, DRE-2199, DRE-2399, DRE-3951. Every comment that
#   holds the PR quotes the answer format from its one source,
#   `fix_context.py`, or the operator writes a sentence the loop never
#   sees: portico #132 (DRE-2199) and agent-bureau #2034 (DRE-2399) each
#   needed a hand dispatch (DRE-2409). Since 2026-09-15 every comment
#   also ends with the attribution line, after the format and after the
#   act's trailer, so the body the registry froze is untouched (DRE-3951).
# 2026-08-25, DRE-2312. Running out of turns is not an outage. It used to
#   post the outage line, which sent the operator to the credential chain
#   for a run that never had a service problem. It now says so and
#   retries once. A second exhaustion holds and names the remedy: split
#   the fix or raise the budget, not a third run into the same wall.
# 2026-08-26, DRE-2722, DRE-2776. A card this script parks goes to
#   Triage with `needs-human`. DRE-2722 split the one "needs you" lane
#   in two: Green Light approves plans, and Triage holds what went wrong
#   (DRE-2723). DRE-2776 (2026-08-27) then moved the engineer's
#   escalate-by-exception question to Green Light, so the reason is the
#   state of the card, not what the lane is for. Everything parked here is
#   a card whose pipeline went wrong. A card waiting on a judgement goes to
#   Green Light. Both stop the loop (reconcile's PARKED_STATES).
# 2026-08-29, DRE-2813, DRE-2817. The round's classification rides the
#   push marker rather than a comment of its own (DRE-2817, 2026-09-08). A
#   further worker-bot comment would outrank a standing operator decision
#   (DRE-2813). The budget counts on "🔧 Fix attempt" and the
#   fix-vs-conflict read-back keys on "pushed — CI and critic review
#   re-running", and both still open the body. The classification is a
#   fixed vocabulary and integers, never thread text.
# 2026-09-01, DRE-2826. Every receipt goes through the one writer,
#   `pipeline_act.py receipt`, which returns the body byte for byte and
#   appends the act's trailer. The two push wordings are one act,
#   fix-attempt-landed. `|| printf` keeps the comment when composition
#   fails: a lost trailer is a missing machine-readable line, while a lost
#   comment is a disputed fix nobody is told about.
# 2026-09-04, DRE-3084, DRE-2696, DRE-2056, DRE-1254. The fixer may answer
#   the critic instead of obeying it. Its evidence is often what the
#   critic cannot read, such as the live card, since the critic holds no
#   Linear key on purpose (DRE-2696). Before this, the verdict stood, the
#   card went to Triage and a person re-ran the review by hand: three PRs
#   on 2026-09-03. A refutation is read before the blocker. It earns one
#   re-review per head, and the receipt is the counter: `refuted-finding
#   @<sha8>`, the key qa-review.yml reads back. The receipt is posted
#   before the dispatch, because the re-review reads it off the thread.
#   The dispatch targets pr-review.yml in this repo, where qa-review.yml
#   is workflow_call-only and a dispatch 422s (DRE-2056). It rides the
#   workflow's own token, because the App token holds no Actions
#   permission (DRE-1254). A stub without `actions: write` 403s, and the
#   card is parked rather than promised a re-review that will not run.
# 2026-09-15, DRE-3951, DRE-3484. The handoff is keyed to (repo, PR,
#   head). The agent used to write fixed paths under /tmp, which any
#   earlier run could leave behind. What cannot be proved to be this
#   run's is refused, because a comment on the wrong pull request is
#   worse than no comment. Every value the step hands the script rides
#   `env:`, and the block carried no Actions expression. That was to stay
#   under the expression ceiling: GitHub compiles a block holding one
#   expression into a single format() call capped at 21,000 characters,
#   and this block had passed 18,000 (DRE-3484). That reason is history
#   now that the block is this file, where Actions substitutes nothing.
# 2026-09-21, DRE-4486, DRE-4183, DRE-4460. A PR that merged while the run
#   worked it gets nothing on the PR and a card for any stranded work.
#   portico #611 took its fix nine minutes after the merge (DRE-4183),
#   nobody knew until an audit six weeks later, and the bug was refiled as
#   DRE-4460. The commits are read from the local clone, because the push
#   may have been refused (the work exists only here) or may have
#   recreated a deleted branch. `--pushed` says which. A card, not a
#   comment: the CEO's answer of 2026-09-21. `find-open` makes it one card
#   per branch.
# 2026-09-25, DRE-4849. The conflict agent may leave a reason for ending
#   without a push. It is read with the other two handoff files, so a
#   foreign one refuses before anything is posted, and quoted only on the
#   no-progress escalation. `fix_budget.no_push_body` owns the wording,
#   and the reason travels by file so none of it meets the shell.
# 2026-10-02, DRE-5225. The block moved here from the step's `run:`,
#   verbatim, with `step_shell.py move`.
# 2026-10-04, DRE-5801. A draft's card is never parked. Five hand-built
#   drafts drew REQUEST_CHANGES, the fix agent disputed each finding, and
#   each card went to Triage with `needs-human` and had to be moved back to
#   Hand-work by hand. The critic now skips drafts and the Resolve step
#   refuses fix mode on one; this is the last line, for a conflict round on
#   a draft and for a PR put back to draft while the run worked it. An
#   unreadable flag parks as before.

# Every read and comment belongs to this (repo, PR, head) or nothing posts (DRE-3951).
handoff() { CMD=$1; shift; python3 .bureau-pipeline/scripts/fix_handoff.py \
  "$CMD" --base "$RUNNER_TEMP" --repo "$REPO" --pr "$PR" --sha "$PRE_SHA" "$@"; }
refuse() { echo "::error::$1 — posting nothing on $REPO#$PR (DRE-3951)"; exit 1; }
handoff check || refuse "the critic verdict is not this run's"
# The one-line trailer every comment below carries: what it answers.
ANSWERS=$(handoff attribution --card "$CARD")

# Merged while this run worked it: a card for stranded work, nothing on the PR (DRE-4486).
PRSTATE=$(gh pr view "$PR" --repo "$REPO" --json state --jq .state 2>/dev/null || true)
if [ "$PRSTATE" = "MERGED" ]; then
  BASEREF=$(gh pr view "$PR" --repo "$REPO" --json baseRefName --jq .baseRefName 2>/dev/null || true)
  BRANCHREF=$(gh pr view "$PR" --repo "$REPO" --json headRefName --jq .headRefName 2>/dev/null || true)
  git fetch origin "$BASEREF" --quiet 2>/dev/null || true
  python3 .bureau-pipeline/scripts/stranded_fix.py local-compare \
    --base "origin/$BASEREF" --out /tmp/stranded-compare.json || true
  LANDED=no
  if git fetch origin "$BRANCHREF" --quiet 2>/dev/null \
     && [ "$(git rev-parse HEAD)" = "$(git rev-parse FETCH_HEAD)" ]; then
    LANDED=yes
  fi
  SLUG=$(python3 .bureau-pipeline/scripts/stranded_fix.py slug --repo "$REPO" || true)
  if [ -n "$SLUG" ] && python3 .bureau-pipeline/scripts/stranded_fix.py route \
       --repo "$REPO" --pr "$PR" \
       --compare-file /tmp/stranded-compare.json \
       --card "$CARD" --pushed "$LANDED" \
       --out-title /tmp/stranded-title.txt \
       --out-body /tmp/stranded-body.md; then
    TITLE=$(cat /tmp/stranded-title.txt)
    EXISTING=$(python3 .bureau-pipeline/scripts/linear_ops.py find-open "$TITLE" || true)
    if [ -z "$EXISTING" ]; then
      python3 .bureau-pipeline/scripts/linear_ops.py oneoff "$TITLE" \
        /tmp/stranded-body.md --label "repo:$SLUG" --label agent:engineer \
        || echo "::error::could not file the stranded-fix card for $REPO#$PR — the commits are named in this run's log above"
    else
      echo "stranded fix already filed as $EXISTING"
    fi
  else
    echo "::warning::PR #$PR merged during this fix run and nothing provable is stranded on ${BRANCHREF:-its branch} — filing no card"
  fi
  echo "PR #$PR merged while this fix run was working it — reporting nothing on it (DRE-4486)"
  exit 0
fi
REFUTED_RC=0; REFUTATION=$(handoff read --kind refutation) || REFUTED_RC=$?
if [ "$REFUTED_RC" -eq 4 ]; then refuse "the refutation is not this run's"; fi
BLOCKED_RC=0; BLOCKER=$(handoff read --kind blocker) || BLOCKED_RC=$?
if [ "$BLOCKED_RC" -eq 4 ]; then refuse "the blocker is not this run's"; fi
# The conflict agent's no-push reason, read with the other two (DRE-4849).
REASON_RC=0; REASON=$(handoff read --kind reason) || REASON_RC=$?
if [ "$REASON_RC" -eq 4 ]; then refuse "the no-push reason is not this run's"; fi

# The answer format, from its one source (DRE-2409).
FORMAT=$(python3 .bureau-pipeline/scripts/fix_context.py --answer-format)

# Broken card: Triage plus needs-human. A judgement call is Green Light (DRE-2722, DRE-2776).
# Never a draft's card (DRE-5801): read live, at the park, since it can go back to draft mid-run.
park_for_human() {
  [ -n "$CARD" ] || return 0
  if [ "$(gh pr view "$PR" --repo "$REPO" --json isDraft --jq .isDraft 2>/dev/null || true)" = "true" ]; then
    echo "PR #$PR is a draft — leaving $CARD where it is: no needs-human, no Triage (DRE-5801)"
    return 0
  fi
  python3 .bureau-pipeline/scripts/linear_ops.py add-label "$CARD" needs-human || true
  python3 .bureau-pipeline/scripts/linear_ops.py advance "$CARD" "Triage" "In Review,In Progress,Todo" || \
    python3 .bureau-pipeline/scripts/linear_ops.py state "$CARD" "Triage" --park || true
}

# A refutation, read before the blocker: one re-review per head (DRE-3084).
if [ "$REFUTED_RC" -eq 0 ]; then
  SHA8=${PRE_SHA:0:8}
  REFUTE_KEY="refuted-finding @$SHA8"
  gh api --paginate --slurp \
    "repos/$REPO/issues/$PR/comments?per_page=100" \
    > /tmp/refuted-thread.json 2>/dev/null \
    || echo '[]' > /tmp/refuted-thread.json
  # Worker-bot authored only (DRE-1995).
  PRIOR=$(jq -r --arg sha8 "$SHA8" '[add[] | select(.user.login == "agent-bureau-bot[bot]") | select(.body | contains("refuted-finding @" + $sha8))] | last | .body // ""' < /tmp/refuted-thread.json || true)
  if [ -z "${PRIOR:-}" ]; then
    REFUTED_TAIL="Nothing was pushed and nothing needs to be. The pipeline is having the reviewer look at this same commit once more with the evidence above attached to its context as untrusted data: if the evidence stands the reviewer says so, and if it does not it says which part fails."
  else
    REFUTED_TAIL="This is the SECOND refutation on this same commit and the one automatic re-review is already spent, so this stops here rather than looping. The first one, quoted:

$(printf '%s\n' "${PRIOR:-}" | sed 's/^/> /')

A person decides which reading is right.

$FORMAT"
  fi
  REFUTED_BODY="🛑 Fix attempt $ATTEMPT refuted the finding: $REFUTATION

$REFUTED_TAIL

$REFUTE_KEY"
  if [ -z "${PRIOR:-}" ]; then
    python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-finding-refuted \
      --body "$REFUTED_BODY" --out /tmp/act-fix-refuted.md \
      || printf '%s' "$REFUTED_BODY" > /tmp/act-fix-refuted.md
    # Posted before the dispatch: the re-review reads it off the thread.
    printf '\n\n%s\n' "$ANSWERS" >> /tmp/act-fix-refuted.md
    gh pr comment "$PR" --repo $REPO \
      --body-file /tmp/act-fix-refuted.md
    # pr-review.yml in this repo, where qa-review.yml cannot be dispatched (DRE-2056).
    REVIEW_WF=qa-review.yml
    [ "$REPO" = "dreadnought-foundry/bureau-pipeline" ] && REVIEW_WF=pr-review.yml
    # The workflow's own token: the App token holds no Actions permission (DRE-1254).
    if GH_TOKEN="$DISPATCH_TOKEN" gh workflow run "$REVIEW_WF" \
         --repo "$REPO" \
         -f pr_number="$PR"; then
      if [ -n "$CARD" ]; then
        python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
          "🔁 The fix agent answered the reviewer's finding with evidence instead of changing the code, so the reviewer is taking one more look at the same work with that evidence in hand. Nothing is needed from you — this is one automatic re-review, not a loop." || true
      fi
    else
      echo "::warning::could not dispatch $REVIEW_WF for PR #$PR — this repo's agent-fix stub needs \`actions: write\`; parking for a human instead of promising a re-review that will not run"
      if [ -n "$CARD" ]; then
        python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
          "🙋 The fix agent answered the reviewer's finding with evidence, but the pipeline could not start the second look by itself. This needs your look. Details on PR #$PR. Move it to **Todo** to retry or to **Backlog** to drop it." || true
      fi
      park_for_human
    fi
  else
    # Capped. Same act the ordinary dispute posts — the fixer pushed
    # nothing, the branch holds a stale REQUEST_CHANGES, and the
    # answer is picked up by the sweep and discharged by
    # `fix-loop-restarted` exactly as it is there.
    python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-attempt-disputed \
      --body "$REFUTED_BODY" --out /tmp/act-fix-refuted-capped.md \
      || printf '%s' "$REFUTED_BODY" > /tmp/act-fix-refuted-capped.md
    printf '\n\n%s\n' "$ANSWERS" >> /tmp/act-fix-refuted-capped.md
    gh pr comment "$PR" --repo $REPO \
      --body-file /tmp/act-fix-refuted-capped.md
    if [ -n "$CARD" ]; then
      python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
        "🙋 The fix agent and the reviewer disagree about the same point twice on the same work, and the automatic second look is already spent. This needs your call. Details on PR #$PR — both sides are quoted there. Answer on the PR with a first line starting Operator decision, or move this card to **Todo** to try again or to **Backlog** to drop it." || true
    fi
    park_for_human
  fi
elif [ "$BLOCKED_RC" -eq 0 ]; then
  # One receipt writer, and `|| printf` keeps the comment if it fails (DRE-2826).
  BLOCKED_BODY="🛑 Fix attempt $ATTEMPT blocked: $BLOCKER

$FORMAT"
  python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-attempt-disputed \
    --body "$BLOCKED_BODY" --out /tmp/act-fix-blocked.md \
    || printf '%s' "$BLOCKED_BODY" > /tmp/act-fix-blocked.md
  printf '\n\n%s\n' "$ANSWERS" >> /tmp/act-fix-blocked.md
  gh pr comment "$PR" --repo $REPO \
    --body-file /tmp/act-fix-blocked.md
  # The fixer pushed nothing, so the card parks rather than stall in review.
  if [ -n "$CARD" ]; then
    python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
      "🙋 The fix agent disagrees with the reviewer's blocking finding and stopped rather than force a change it believes is wrong. This needs your call. Details on PR #$PR. Answer on the PR — comment there with a first line starting Operator decision, for example: **Operator decision** — <your answer here>. What happens next: your comment starts the fix loop on its own, normally within a minute. If that run never arrives, the pipeline sweep picks your answer up on its next pass, normally within about 15 minutes. You do not need to run anything by hand. Or move this card to **Todo** to have the reviewer take another look, or to **Backlog** to drop it." || true
  fi
  park_for_human
else
  # Only a new commit re-runs CI and the critic, so an unmoved head escalates.
  POST_SHA=$(gh pr view "$PR" --repo $REPO --json headRefOid --jq .headRefOid)
  if [ -n "$PRE_SHA" ] && [ "$POST_SHA" = "$PRE_SHA" ]; then
    # Death or no progress: fix_dead_run.py decides over every page (DRE-2018, DRE-4139).
    [ -n "$EXEC_FILE" ] || EXEC_FILE=/home/runner/work/_temp/claude-execution-output.json
    COMMENTS_JSON=$(mktemp)
    gh api --paginate --slurp \
      "repos/$REPO/issues/$PR/comments?per_page=100" \
      > "$COMMENTS_JSON" || echo '[]' > "$COMMENTS_JSON"
    DECISION=$(python3 .bureau-pipeline/scripts/fix_dead_run.py decide \
      "$EXEC_FILE" --comments-json "$COMMENTS_JSON" --run-url "$RUN_URL")
    ACTION=$(printf '%s\n' "$DECISION" | head -1)
    BODY=$(printf '%s\n' "$DECISION" | tail -n +3)
    if [ "$ACTION" = "retry" ]; then
      gh pr comment "$PR" --repo $REPO --body "$BODY

$ANSWERS"
      if [ -n "$CARD" ]; then
        python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
          "🤖 The AI service was unavailable during the last fix run — an outage, not a problem with the work. The pipeline retries automatically; no action needed." || true
      fi
    elif [ "$ACTION" = "retry-turns" ]; then
      # Out of turns, not an outage: one retry (DRE-2312).
      gh pr comment "$PR" --repo $REPO --body "$BODY

$ANSWERS"
      if [ -n "$CARD" ]; then
        python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
          "🤖 The last fix run ran out of steps before it could finish — it used a full run of real work and stopped at its step limit. Nothing is wrong with the setup. The pipeline retries once automatically; no action needed." || true
      fi
    elif [ "$ACTION" = "hold-turns" ]; then
      # Second exhaustion: stop buying identical attempts and tell
      # the operator the actual remedy (split it, or raise the
      # budget) instead of a third $5 run into the same wall.
      gh pr comment "$PR" --repo $REPO --body "$BODY

$FORMAT

$ANSWERS"
      if [ -n "$CARD" ]; then
        python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
          "🙋 Two fix runs in a row ran out of steps on this PR before finishing — both did real work and hit their step limit. That means the remaining fix is too big for one sitting, so it needs breaking into smaller pieces rather than another retry. This needs your look. Details on PR #$PR. Move it to **Todo** to try again anyway, or to **Backlog** to drop it." || true
      fi
      park_for_human
    elif [ "$ACTION" = "hold" ]; then
      gh pr comment "$PR" --repo $REPO --body "$BODY

$FORMAT

$ANSWERS"
      if [ -n "$CARD" ]; then
        python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
          "🙋 The AI service failed several fix runs in a row on this PR — that looks like an outage or account problem, not bad work. This needs your look. Details on PR #$PR. Move it to **Todo** to retry or to **Backlog** to drop it." || true
      fi
      park_for_human
    else
      # fix_budget.py owns the no-push wording; the reason travels by file (DRE-4849).
      REASON_FILE=$(mktemp)
      printf '%s' "$REASON" > "$REASON_FILE"
      NO_PUSH_FILE=$(mktemp)
      python3 .bureau-pipeline/scripts/fix_budget.py no-push \
        --mode "$MODE" --attempt "$ATTEMPT" --head "$POST_SHA" \
        --reason-file "$REASON_FILE" --run-url "$RUN_URL" \
        --out "$NO_PUSH_FILE" \
        || printf '%s' "🛑 Fix attempt $ATTEMPT pushed no new commit (branch still at \`${POST_SHA:0:8}\`) — the reviewer will not re-run and the last verdict stands. Escalating to a human rather than leaving this PR stuck." > "$NO_PUSH_FILE"
      printf '\n\n%s\n\n%s\n' "$FORMAT" "$ANSWERS" >> "$NO_PUSH_FILE"
      gh pr comment "$PR" --repo $REPO --body-file "$NO_PUSH_FILE"
      if [ -n "$CARD" ]; then
        python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
          "🙋 The fix agent ran but produced no change the reviewer can re-check, so this card can't move on its own. This needs your look. Details on PR #$PR. Move it to **Todo** to retry or to **Backlog** to drop it." || true
      fi
      park_for_human
    fi
  else
    if [ "$MODE" = "conflict" ]; then
      BODY="🔀 Conflict resolution round $ATTEMPT pushed — CI and critic review re-running."
    else
      BODY="🔧 Fix attempt $ATTEMPT pushed — CI and critic review re-running."
      # The classification rides this marker (DRE-2817, DRE-2813).
      [ -n "$CLASSIFICATION" ] && BODY="$BODY

$CLASSIFICATION"
    fi
    # Both wordings are one act, fix-attempt-landed (DRE-2826).
    python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-attempt-landed \
      --body "$BODY" --out /tmp/act-fix-pushed.md \
      || printf '%s' "$BODY" > /tmp/act-fix-pushed.md
    printf '\n\n%s\n' "$ANSWERS" >> /tmp/act-fix-pushed.md
    gh pr comment "$PR" --repo $REPO --body-file /tmp/act-fix-pushed.md
  fi
fi
