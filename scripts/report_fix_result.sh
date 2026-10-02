#!/usr/bin/env bash
set -e

# DRE-3951 — every read and every comment below belongs to THIS
# (repo, PR, head) or nothing is posted at all. `handoff` reads only
# what the open step stamped for this run; `refuse` is what happens
# when something is there and cannot be proved to be ours, because a
# comment on the wrong pull request is worse than no comment.
handoff() { CMD=$1; shift; python3 .bureau-pipeline/scripts/fix_handoff.py \
  "$CMD" --base "$RUNNER_TEMP" --repo "$REPO" --pr "$PR" --sha "$PRE_SHA" "$@"; }
refuse() { echo "::error::$1 — posting nothing on $REPO#$PR (DRE-3951)"; exit 1; }
handoff check || refuse "the critic verdict is not this run's"
# The one-line trailer every comment below carries: what it answers.
ANSWERS=$(handoff attribution --card "$CARD")

# DRE-4486 — the pull request merged while this run was working it.
#
# Everything below this block reports ON the pull request: a fix
# attempt to re-review, a dispute for the critic to answer, a park
# for a human. None of it means anything once the PR is merged —
# the card is Done, the reviewer will not look again, and the merge
# gate has nothing left to gate. What the run owes instead is a
# record of the work that has nowhere to go, which is exactly what
# the four earlier occurrences were missing: portico #611
# (DRE-4183) took its fix nine minutes after the merge and nobody
# knew until a stale-branch audit six weeks later, by which time
# the bug it fixed was live and refiled as DRE-4460.
#
# The commits are read from the LOCAL clone, not from a compare
# against origin, because both outcomes have to be nameable: the
# pre-push guard above may have refused the push (the work exists
# only here) or the run may be on a channel that predates the guard
# (the work is on a branch GitHub deleted at merge and the push
# recreated). `--pushed` says which, and the card says so plainly
# rather than implying the work is recoverable when it is not.
#
# Filed as a card, not a comment: the CEO's answer of 2026-09-21
# chose a card, and a comment on a merged pull request is read by
# nobody. ONE card per branch — `find-open` is the idempotency key,
# so a second run on the same branch appends nothing.
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
# DRE-4849: the conflict agent's reason for ending without a push.
# Read here, with the other two, so a foreign one refuses before
# anything is posted; quoted only on the no-new-commit escalation.
REASON_RC=0; REASON=$(handoff read --kind reason) || REASON_RC=$?
if [ "$REASON_RC" -eq 4 ]; then refuse "the no-push reason is not this run's"; fi

# The copy-pasteable answer format, from its ONE source (DRE-2409).
# Every comment below that HOLDS this PR quotes it: the escalate-by-
# exception exit door has to state what an answer looks like, or the
# operator writes a perfectly sensible sentence the loop never sees
# (portico #132 / DRE-2199 and agent-bureau #2034 / DRE-2399, both
# of which then needed a hand dispatch).
FORMAT=$(python3 .bureau-pipeline/scripts/fix_context.py --answer-format)

# Park-to-human helper. A dispute or a no-progress fix run leaves the
# branch's LATEST critic verdict at REQUEST_CHANGES with no new commit
# to re-review — so the critic never re-fires and merge-gate correctly
# but permanently HOLDS ("latest verdict is not APPROVE — holding").
# The card then sits silently in the review lane forever (DeltaSolv PR #64/#74,
# 2026-06-28: ~20h stall, hand-merged). So route it to the human:
# Triage (the lane for a card that went wrong, DRE-2722/2723) + a
# needs-human stamp, with a plain-English note. The CEO answers and
# moves the card → Todo (proceed) or → Backlog (drop).
#
# Triage and not Green Light, and the reason is the STATE OF THE CARD,
# not what the lane is for (DRE-2776 moved the engineer's
# escalate-by-exception question to Green Light, so "that lane is for
# epics" stopped being true). Everything that reaches this function is
# a card whose pipeline went wrong — a fix loop that would not
# converge, a run with no forward progress, a cap exhausted. A card
# waiting on a JUDGEMENT goes to Green Light; a card that broke goes
# here. Both are human queues and both stop the loop (reconcile's
# PARKED_STATES); they differ in what the human is being asked for.
park_for_human() {
  [ -n "$CARD" ] || return 0
  python3 .bureau-pipeline/scripts/linear_ops.py add-label "$CARD" needs-human || true
  python3 .bureau-pipeline/scripts/linear_ops.py advance "$CARD" "Triage" "In Review,In Progress,Todo" || \
    python3 .bureau-pipeline/scripts/linear_ops.py state "$CARD" "Triage" --park || true
}

# DRE-3084 — the fixer ANSWERED the critic instead of obeying it.
#
# Read BEFORE the blocker file, because it is the more specific
# outcome and the two are not the same act: a blocker asks a HUMAN
# to settle a disagreement the fixer cannot; a refutation settles it
# with evidence the critic could not read (the live card — the
# critic holds no Linear key on purpose, DRE-2696 — a test run, the
# merge base) and needs only the reviewer to look again. Before this
# card there was no such outcome: the verdict stood, the card went to
# Triage with `needs-human`, and a person ran `gh run rerun` hours
# later (agent-bureau #2247 hand-merged 17:15 PT, bureau-pipeline
# #251 approved on a hand re-run at 22:04 PT, agent-bureau-demo #9
# refuted 23:55 and approved 00:01 — all 2026-09-03).
#
# ONE re-review per HEAD, and the receipt IS the counter: the body
# carries `refuted-finding @<sha8>`, the same key qa-review.yml
# reads back to put the refutation in the critic's context. A second
# refutation on the same commit is a loop, not a re-review, so it
# escalates exactly as a blocker does, with both quoted. A new
# commit is a new head and earns its own re-review, like every other
# budget in this loop.
if [ "$REFUTED_RC" -eq 0 ]; then
  SHA8=${PRE_SHA:0:8}
  REFUTE_KEY="refuted-finding @$SHA8"
  gh api --paginate --slurp \
    "repos/$REPO/issues/$PR/comments?per_page=100" \
    > /tmp/refuted-thread.json 2>/dev/null \
    || echo '[]' > /tmp/refuted-thread.json
  # Worker-bot authored ONLY (DRE-1995): anyone can comment this
  # marker, and a planted one would either burn the one re-review a
  # healthy PR is owed or park it outright.
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
    # Posted BEFORE the dispatch, and that order is load-bearing:
    # the re-review reads this comment off the thread to build its
    # context, so dispatching first races the write.
    printf '\n\n%s\n' "$ANSWERS" >> /tmp/act-fix-refuted.md
    gh pr comment "$PR" --repo $REPO \
      --body-file /tmp/act-fix-refuted.md
    # The dispatchable critic stub for THIS repo (DRE-2056): in
    # bureau-pipeline qa-review.yml IS the workflow_call-only
    # reusable and dispatching it 422s, so the stub is pr-review.yml.
    # Same resolution reconcile.review_workflow() makes.
    REVIEW_WF=qa-review.yml
    [ "$REPO" = "dreadnought-foundry/bureau-pipeline" ] && REVIEW_WF=pr-review.yml
    # The App token holds NO Actions permission (DRE-1254: "HTTP
    # 403: Resource not accessible by integration"), so the dispatch
    # rides the workflow's own GITHUB_TOKEN — which carries
    # actions:write only if the CALLING STUB grants it (README, "The
    # agent-fix stub's actions: write"). A stub that has not been
    # updated 403s here, and a promised re-review nobody runs is the
    # exact silent stall this card exists to remove: so the failure
    # is loud, and it degrades to today's behaviour rather than to a
    # lie.
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
  # DRE-2826: composed through the one receipt writer, which returns
  # this body byte-identically and appends the act trailer. The body
  # itself is untouched — reconcile's restart sweep reads the 🛑 that
  # opens it, and fix_budget counts it.
  #
  # The body goes into a variable first so the fallback can reuse it
  # without a second copy of the wording. `|| printf` is the fail-soft
  # rule this repo keeps everywhere the report is the escalation:
  # composition can only fail on a broken checkout (the act name and
  # the registry are both checked in CI), and a lost trailer is a
  # missing machine-readable line, while a lost comment is a disputed
  # fix nobody is told about.
  BLOCKED_BODY="🛑 Fix attempt $ATTEMPT blocked: $BLOCKER

$FORMAT"
  python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-attempt-disputed \
    --body "$BLOCKED_BODY" --out /tmp/act-fix-blocked.md \
    || printf '%s' "$BLOCKED_BODY" > /tmp/act-fix-blocked.md
  printf '\n\n%s\n' "$ANSWERS" >> /tmp/act-fix-blocked.md
  gh pr comment "$PR" --repo $REPO \
    --body-file /tmp/act-fix-blocked.md
  # Escalate, don't just narrate. The fixer disputed the critic's
  # findings and (per its instructions) pushed NOTHING — so the branch
  # holds a stale REQUEST_CHANGES the critic will never lift on its own.
  # Park the card in Triage so it surfaces in the CEO's queue
  # instead of stalling invisibly in the review lane.
  if [ -n "$CARD" ]; then
    python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
      "🙋 The fix agent disagrees with the reviewer's blocking finding and stopped rather than force a change it believes is wrong. This needs your call. Details on PR #$PR. Answer on the PR — comment there with a first line starting Operator decision, for example: **Operator decision** — <your answer here>. What happens next: your comment starts the fix loop on its own, normally within a minute. If that run never arrives, the pipeline sweep picks your answer up on its next pass, normally within about 15 minutes. You do not need to run anything by hand. Or move this card to **Todo** to have the reviewer take another look, or to **Backlog** to drop it." || true
  fi
  park_for_human
else
  # The fixer reported success — but only a NEW commit re-triggers CI +
  # the critic. If the head SHA did not advance (the agent committed
  # nothing, or its push was a no-op), announcing "review re-running"
  # is a lie: no run fires, the stale REQUEST_CHANGES stands, and the
  # card stalls in the review lane (DeltaSolv PR #74, 2026-06-28). Verify real
  # forward progress; escalate to the human when there is none.
  POST_SHA=$(gh pr view "$PR" --repo $REPO --json headRefOid --jq .headRefOid)
  if [ -n "$PRE_SHA" ] && [ "$POST_SHA" = "$PRE_SHA" ]; then
    # DRE-2018: distinguish a model DEATH (is_error — API outage,
    # exhausted subscription; the 2026-07-10 DeltaSolv token outage)
    # from a fix that RAN and pushed nothing. An outage must not
    # read as an agent failure in the CEO's queue: fix_dead_run.py
    # answers retry (post the fix-run-model-death marker the
    # reconcile sweep re-dispatches on — no park, no needs-human,
    # no fix-attempt burned), hold (the death after the cap —
    # medic's cap pattern), or escalate (genuine no-progress:
    # today's behavior, unchanged). The cap counts CONSECUTIVE
    # worker-bot deaths since the last successful push, not every
    # death marker ever posted — a recovered outage episode must not
    # pre-exhaust the cap for a fresh one (DRE-2018 review). Hand the
    # full comment list to fix_dead_run.py; it filters to
    # worker-bot-authored markers (DRE-1995 discipline) and stops the
    # count at the last push marker.
    # "Full" means EVERY PAGE (DRE-4139): unpaginated this read saw
    # GitHub's default 30 — the OLDEST 30 — so on a long PR the cap
    # counted deaths that were no longer the consecutive run, or
    # none at all, and a dead loop kept being retried.
    # fix_dead_run.py flattens the per-page arrays `--slurp` emits.
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
      # DRE-2312: the run ran out of steps. Say that, and only that
      # — this used to post the outage line above, which sent the
      # operator to the credential chain for a run that never had a
      # service problem. One retry (agent runs vary in how far they
      # get); no park, no needs-human.
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
      # DRE-4849: in conflict mode the body quotes the agent's own
      # reason for not pushing, or says it left none and names this
      # run's log. fix_budget.no_push_body owns the wording; the
      # reason goes through a file so none of it meets the shell.
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
      # DRE-2817 (AC7): the round's classification is recorded HERE
      # rather than in a comment of its own — this marker already
      # goes up every round, and an extra worker-bot comment would
      # consume a standing operator decision (DRE-2813). The budget
      # counts on "🔧 Fix attempt" and the fix-vs-conflict read-back
      # keys on "pushed — CI and critic review re-running": both
      # still open the body, so appending disturbs neither.
      # $CLASSIFICATION comes from fix_convergence.py through this
      # step's env — a fixed vocabulary and integers, never thread
      # text.
      [ -n "$CLASSIFICATION" ] && BODY="$BODY

$CLASSIFICATION"
    fi
    # DRE-2826: both wordings are the same act — the fixer pushed a
    # commit, so CI and the critic re-run. The read-back above keys on
    # "pushed — CI and critic review re-running", which the composed
    # body still opens with.
    python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-attempt-landed \
      --body "$BODY" --out /tmp/act-fix-pushed.md \
      || printf '%s' "$BODY" > /tmp/act-fix-pushed.md
    printf '\n\n%s\n' "$ANSWERS" >> /tmp/act-fix-pushed.md
    gh pr comment "$PR" --repo $REPO --body-file /tmp/act-fix-pushed.md
  fi
fi
