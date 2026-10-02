#!/usr/bin/env bash
set -e
#
# qa-review.yml's `Post verdict or neutral status` step, which runs it as
# `bash "$PIPELINE_DIR/scripts/post_verdict.sh"`. The shell moved here from
# the workflow's `run:` block unchanged (DRE-5384, by `scripts/step_shell.py
# move`), so it runs as the block ran: `set -e` alone, no pipefail.
#
# What this script does
# ─────────────────────
#
# It posts the critic's result on the pull request as one comment, and the
# same text on the card. Exactly one comment is composed, by the first of
# these that applies:
#
#   1. Oversized — the pull request was too big to review, so no review was
#      attempted. The comment is OVERSIZE_MESSAGE, verbatim.
#   2. Checkout missing — the pipeline checkout's gate script cannot be
#      read, so nothing the gates wrote can be trusted. The comment says
#      that, and blames no credential, install or model.
#   3. A real verdict — the critic's own verdict, its first line bound to
#      the head it reviewed (the verdict line, below), with a footer saying
#      which edit of the description the review read.
#   4. Install failed — Claude Code was never installed, so the critic
#      never started.
#   5. Evidence hold — the critic finished but blocked on a claim about a
#      run it did not carry. The comment is the hold message the evidence
#      gate wrote to /tmp/qa-evidence-hold.md.
#   6. Out of turns — an attempt hit its turn ceiling. The comment says
#      what the attempts spent and asks for a split.
#   7. Finished without a verdict — an attempt ended cleanly and left
#      nothing readable. The comment names its turns, its cost and the
#      cause the gate found, the retry's cause winning when both have one.
#   8. Stopped mid-review — an attempt left no execution record yet ran
#      past 60 seconds. The comment names the runner, its class and both
#      running times, and says the machine ran out of memory only when the
#      back-off sleep was itself ended early.
#   9. Crashed — nothing above explains it. The critic could not run, and
#      whatever the attempts spent is appended.
#
# Every comment but 3 is a neutral status. It carries "QA Critic", so the
# merge gate reads it as the latest word and it supersedes a stale APPROVE,
# and it carries no "VERDICT:" line, so the merge is held and the fix agent
# is not woken for findings nobody made. When REFUTED is true a footer under
# any of the nine says why this head carries two verdicts.
#
# The comment is posted with `gh pr comment`, tried four times, because the
# merge gate reads it. When the pull request has a card, the same text plus
# a last line naming the selected model goes to the card through
# linear_ops.py, best-effort. On a real APPROVE, sync_review_state.py
# dismisses a stale "Changes requested" review and submits an approving
# one, best-effort. This script never fails the job on purpose: the job's
# `Fail if critic never really ran` step does that, after the comment is up.
#
# The verdict line. On a real verdict the comment's first line is
#
#   🔎 QA Critic — VERDICT: <X>[ cause:<tag>] @<reviewed-sha>[ content:<id>]
#
# `head -1` of this comment is what merge_gate's `VERDICT:` match,
# verdict_content.py's end-anchored `content:` read and the cause tag's
# readers all parse, so nothing is ever appended to it. `cause:` comes on a
# REQUEST_CHANGES line from the critic's own first line, and sits before the
# sha. `content:` is there when the reviewed commit's three-dot diff could be
# hashed, and stays last. Every footer goes below the body.
#
# Inputs. Every one arrives in the environment and is read as data: an env
# var expands to its value and is never re-parsed, so a value holding
# `$(...)` or a backtick inside a double-quoted string cannot run. Nothing
# here is a GitHub expression, and nothing may become one: a block holding
# one is compiled into one expression with a 21,000-character ceiling, and
# DRE-3484 moved the last one into the step's env.
#
#   GH_TOKEN          the App token `gh` posts and reads with
#   LINEAR_API_KEY    for the card's copy
#   PR                the pull request number
#   CARD              the card the pull request is for; empty when none
#   REPO              owner/name, for sync_review_state.py
#   REVIEWED_SHA      the head the review looked at, taken at its start
#   CONTENT_ID        the hash of that head's three-dot diff; may be empty
#   REAL              `true` when either gate saw a real verdict
#   A1_OUTCOME, A2_OUTCOME
#                     how each attempt ended: ok, turn_exhaustion, crash,
#                     completed_no_verdict or unknown; empty if it never ran
#   A1_TURNS, A2_TURNS, A1_COST, A2_COST
#                     turns and dollars, bare numbers, for an attempt that
#                     left an execution record
#   A1_CAUSE_TEXT, A2_CAUSE_TEXT
#                     a fixed sentence naming why the verdict file was
#                     unusable — never a byte of that file
#   A1_EVIDENCE, A2_EVIDENCE
#                     `ok` or `defective`: did a finished verdict carry the
#                     evidence for the runs it asserts. The hold MESSAGE
#                     quotes the critic, so it arrives as a file, never here
#   BODY_READ_AT      when the review read the pull request's description
#   MODEL_ID          the model the critic ran on
#   MODEL_WHY         the one-line reason that model was selected
#   STRATEGY          `oversized` for a pull request too big to review
#   OVERSIZE_MESSAGE  the size message pr_size_strategy.py composed
#   REFUTED           `true` when this review read a refutation of its own
#                     previous finding on the same commit
#   INSTALL_FAILED    `true` when the shared Claude Code install failed
#   A1_ELAPSED, A2_ELAPSED
#                     each attempt's running time in bare seconds; empty
#                     when not measured
#   BACKOFF_OUTCOME   the back-off sleep's outcome; `failure` means the
#                     machine was ending new processes
#
# From the runner: PIPELINE_DIR (the pipeline checkout, outside the working
# tree), GITHUB_REPOSITORY (for the description's last-edit time) and
# RUNNER_NAME (shown as data, its control characters and backticks dropped).
# Files: it reads /tmp/qa-verdict.md and /tmp/qa-evidence-hold.md, and
# writes /tmp/qa-comment.md and /tmp/qa-linear.md.
#
# Incident history
# ────────────────
#
# One note per card, oldest first, each dated by when it reached
# qa-review.yml. Where a later card changed an earlier rule, the note states
# the rule as it holds now, once, and names the cards that got it there.
#
# 2026-06-13 · DRE-1330 (with DRE-1332). A critic that crashed on both
#   attempts used to post REQUEST_CHANGES, and that false rejection churned
#   good pull requests (#1441, #1442) into the fix loop. A crash posts a
#   neutral status instead, and the job goes red after it.
#
# 2026-06-24 · DRE-1874. When the critic flipped from REQUEST_CHANGES to
#   APPROVE it only posted a comment, and the stale formal review kept
#   reviewDecision at CHANGES_REQUESTED: nothing, not even --admin, could
#   merge until a person dismissed it. A real APPROVE now brings the formal
#   review into line, idempotently.
#
# 2026-07-09 · DRE-1990, then DRE-2340 (2026-08-10) and DRE-2489
#   (2026-08-18) — the verdict line. DRE-1990 bound a verdict to the commit
#   it reviewed, so a push after an APPROVE demotes it to no verdict; a line
#   with no `@sha`, as every verdict before it was, counts as none. DRE-2340
#   added the content id, so a base merge that changes the head but nothing
#   the pull request contributes keeps the verdict. DRE-2489 added the cause
#   tag, placed before the sha where merge_gate's match has already stopped.
#   The line as it holds now is under "The verdict line" above, and
#   tests/test_verdict_cause_tag.py pins every parser against both formats.
#
# 2026-08-09 · DRE-2317. The card's copy names the selected model, so
#   quality per model can be queried after run logs expire at about 90 days.
#   A line on the copy, not a separate heartbeat: the critic runs on every
#   push, and one more comment each time would add hundreds per repo.
#
# 2026-08-15 · DRE-2465, then DRE-2924 (2026-09-06), DRE-3304 (2026-09-07),
#   DRE-3414 with DRE-3416 (2026-09-14) and DRE-4885 (2026-09-28) — the
#   crash wording. Under DRE-1330 every attempt that left no verdict got the
#   comment blaming a startup or auth failure. Each of these cards found it
#   naming the wrong cause and gave that case its own branch:
#   - DRE-2465: portico PR #297 got it four times over four completed runs
#     (117 turns, about $12.40), and an operator rotated a healthy token
#     twice. A run that finished is branch 7, and an oversized pull request
#     gets its own message, branch 1.
#   - DRE-2924: portico PR #364's critic ran out of turns after 62 turns and
#     about $2.56 and was told it had crashed, which cost three days across
#     two repos. Turn exhaustion is branch 6, ahead of branch 7 because its
#     remedy is the one that acts. Every failing branch states what the
#     attempts spent: a real stale-credential death is instant, one turn and
#     $0, so anything more contradicts the crash wording out loud.
#   - DRE-3304: branch 7 names what was found in the verdict's place rather
#     than pointing at a log; the runner and the verdict file are gone by
#     the time anyone reads it.
#   - DRE-3414: a failed install skipped both attempts and fell into the
#     crash wording, sending an operator after a credential the critic never
#     reached — what the fleet's last install outage, DRE-3416, cost. It is
#     branch 4, right after a real verdict, so no verdict is swallowed and
#     no other failure wording claims the run first.
#   - DRE-4885: portico #717's first attempt reviewed for 1,238 seconds
#     before a light runner ran out of memory, and was told it had crashed.
#     Branch 8 tells a stopped attempt from one that never started by its
#     running time, and comes after every branch that reads an execution
#     record. Its first line is branch 9's byte for byte, because it is the
#     neutral marker medic_classify.CRITIC_NEUTRAL_MARKER and the merge gate
#     match on.
#   What holds now: the crash wording is branch 9, the last resort, and it
#   carries the attempts' numbers so a reader can see when it is wrong.
#
# 2026-09-04 · DRE-3005. A critic blocked two correct pull requests
#   overnight (agent-bureau #2247 among them) on claims about what a command
#   did, without carrying the run, and neither claim reproduced. Such a
#   verdict is held as branch 5 and does not post as REQUEST_CHANGES, which
#   would spend a fix attempt and an operator decision on a finding nobody
#   proved (the #1441/#1442 class). Rule 3 of the same card is the
#   description footer on branch 3. It reads `lastEditedAt`, which moves only
#   when the body or title is edited — `updatedAt` moves on any activity and
#   would flag every commented pull request — and an empty edit time is
#   rendered as no claim, never as freshness.
#
# 2026-09-04 · DRE-3084. A review that read a refutation of its own previous
#   finding on the same commit adds a footer saying so, on every branch,
#   since two verdicts on one head otherwise look like a bug to whoever
#   reads the thread next.
#
# 2026-09-17 · DRE-3226. On bureau-pipeline PR #279 the critic's own
#   experiment deleted the pipeline checkout out of the working tree; the
#   gates ran nothing, and this step blamed a startup failure on a reviewer
#   that had reviewed. The checkout moved outside the working tree, so every
#   pipeline script this file calls is addressed as "$PIPELINE_DIR"/scripts/
#   rather than through the old in-tree copy (DRE-4158 did the same for the
#   critic's context), and branch 2 says what was found when the gate's own
#   script is missing. What holds now: since DRE-5384 this file lives in that
#   checkout too, so branch 2 can speak only when the checkout is partly
#   there. If it is gone entirely the step fails before this file starts,
#   nothing is posted, and `Fail if critic never really ran` turns the job
#   red.
# Is the pipeline checkout still there? It decides branch 2 (DRE-3226).
if [ -n "${PIPELINE_DIR:-}" ] \
   && [ -r "${PIPELINE_DIR}/scripts/check_critic_result.py" ]; then
  PIPELINE_OK=true
else
  PIPELINE_OK=false
  echo "::warning::the pipeline checkout at '${PIPELINE_DIR:-<unset>}' is gone — posting a neutral status instead of a verdict"
fi
if [ "${STRATEGY:-}" = "oversized" ]; then
  # Branch 1, oversized: no review was attempted.
  printf '%s\n' "${OVERSIZE_MESSAGE:-}" > /tmp/qa-comment.md
elif [ "$PIPELINE_OK" != "true" ]; then
  # Branch 2, checkout missing: blame no credential, install or model.
  printf '%s\n\n%s\n' \
    "🔎 QA Critic could not post a verdict — the pipeline checkout was missing, this is NOT a code rejection." \
    "The review workflow's own checkout of the pipeline was gone by the time the verdict was composed, so the step that reads the reviewer's output could not run and there is nothing to post. This is NOT an authentication, install or model failure — no credential needs rotating on the strength of this message — and it says nothing at all about the code in this pull request. The checkout lives outside this pull request's working tree (DRE-3226), so a command run inside the repository can no longer reach it; the job log names the path it looked for. Merge is held until a critic actually reviews this PR; this is not a request for changes." \
    > /tmp/qa-comment.md
else
# RAN: set only for an attempt that finished cleanly with no verdict.
RAN=""
if [ "${A1_OUTCOME:-}" = "completed_no_verdict" ]; then
  RAN="the first attempt ran ${A1_TURNS:-?} turns and billed about \$${A1_COST:-?}"
fi
if [ "${A2_OUTCOME:-}" = "completed_no_verdict" ]; then
  if [ -n "$RAN" ]; then
    RAN="$RAN, the retry ${A2_TURNS:-?} turns and about \$${A2_COST:-?}"
  else
    RAN="the retry ran ${A2_TURNS:-?} turns and billed about \$${A2_COST:-?}"
  fi
fi
# SPENT: what the attempts billed, stated on every failing branch.
SPENT=""
if [ -n "${A1_TURNS:-}" ]; then
  SPENT="the first attempt ran ${A1_TURNS} turns and billed about \$${A1_COST:-?}"
fi
if [ -n "${A2_TURNS:-}" ]; then
  if [ -n "$SPENT" ]; then
    SPENT="$SPENT, the retry ${A2_TURNS} turns and about \$${A2_COST:-?}"
  else
    SPENT="the retry ran ${A2_TURNS} turns and billed about \$${A2_COST:-?}"
  fi
fi
# An attempt with no execution record that ran past 60s was stopped.
A1_SECS=""; A2_SECS=""
case "${A1_ELAPSED:-}" in ''|*[!0-9]*) ;; *) A1_SECS=$((10#$A1_ELAPSED)) ;; esac
case "${A2_ELAPSED:-}" in ''|*[!0-9]*) ;; *) A2_SECS=$((10#$A2_ELAPSED)) ;; esac
A1_STOPPED=""; A2_STOPPED=""
if [ "${A1_OUTCOME:-}" = "unknown" ] && [ -n "$A1_SECS" ] && [ "$A1_SECS" -gt 60 ]; then
  A1_STOPPED=true
fi
if [ "${A2_OUTCOME:-}" = "unknown" ] && [ -n "$A2_SECS" ] && [ "$A2_SECS" -gt 60 ]; then
  A2_STOPPED=true
fi
if [ "$REAL" = "true" ]; then
  # Branch 3, a real verdict, bound to its head (the verdict line).
  { echo "🔎 QA Critic — $(head -1 /tmp/qa-verdict.md) @${REVIEWED_SHA}${CONTENT_ID:+ content:$CONTENT_ID}"; echo; tail -n +2 /tmp/qa-verdict.md; } > /tmp/qa-comment.md
  # The description footer, below the body, never on the parsed line.
  BODY_EDITED=$(gh api graphql -f query='query($owner:String!,$name:String!,$pr:Int!){repository(owner:$owner,name:$name){pullRequest(number:$pr){lastEditedAt}}}' \
    -F owner="${GITHUB_REPOSITORY%%/*}" -F name="${GITHUB_REPOSITORY#*/}" -F pr="$PR" \
    --jq '.data.repository.pullRequest.lastEditedAt // ""' 2>/dev/null || true)
  FOOTER=$(python3 "$PIPELINE_DIR"/scripts/verdict_evidence.py snapshot \
    --reviewed-at "${BODY_READ_AT:-}" --edited-at "${BODY_EDITED:-}" || true)
  if [ -n "$FOOTER" ]; then
    printf '\n%s\n' "$FOOTER" >> /tmp/qa-comment.md
  fi
elif [ "${INSTALL_FAILED:-}" = "true" ]; then
  # Branch 4, install failed: the critic never started.
  printf '%s\n\n%s\n' \
    "🔎 QA Critic could not run — the Claude Code install failed, this is NOT a code rejection." \
    "The shared install step could not produce a Claude Code binary that runs, so the adversarial reviewer never started and no findings were produced. This is an install failure: it is not an authentication failure and not a model failure, so no credential needs rotating and no model is degraded on the strength of this message — the install step's own error on this run names what it tried. Merge is held until a critic actually reviews this PR; this is not a request for changes." \
    > /tmp/qa-comment.md
elif [ "${A2_EVIDENCE:-${A1_EVIDENCE:-}}" = "defective" ] && [ -s /tmp/qa-evidence-hold.md ]; then
  # Branch 5, evidence hold: the message arrives as a file, never env.
  cp /tmp/qa-evidence-hold.md /tmp/qa-comment.md
elif [ "${A1_OUTCOME:-}" = "turn_exhaustion" ] || [ "${A2_OUTCOME:-}" = "turn_exhaustion" ]; then
  # Branch 6, out of turns. Not medic's infra-error marker: no infra failed.
  printf '%s\n\n%s\n' \
    "🔎 QA Critic ran out of turns before it could finish — re-review needed, this is NOT a code rejection." \
    "The adversarial reviewer hit its turn ceiling mid-review and stopped, so there is no verdict to post — ${SPENT:-no turn count was recorded}. This is NOT an authentication or startup failure: it authenticated, did real work, and was billed for it, so no credential needs rotating on the strength of this message. A retry hits the same wall, because the budget it exhausted is the same one — the fix is a smaller change: split this pull request into independently reviewable parts, and each part gets a full review. Merge is held until a critic actually reviews this PR; this is not a request for changes." \
    > /tmp/qa-comment.md
elif [ -n "$RAN" ]; then
  # Branch 7, finished without a verdict; the retry's cause wins.
  WHY="${A2_CAUSE_TEXT:-${A1_CAUSE_TEXT:-}}"
  printf '%s\n\n%s\n' \
    "🔎 QA Critic ran but produced no verdict — re-review needed, this is NOT a code rejection." \
    "The adversarial reviewer completed its run and then left no verdict we could read — $RAN.${WHY:+ What was found in its place: $WHY.} This is NOT an authentication or startup failure: it authenticated, did real work, and was billed for it, so no credential needs rotating on the strength of this message. Merge is held until a critic actually reviews this PR; this is not a request for changes." \
    > /tmp/qa-comment.md
elif [ -n "$A1_STOPPED$A2_STOPPED" ]; then
  # Branch 8, stopped mid-review. Line 1 is branch 9's, byte for byte.
  _took() {
    if [ "$1" -eq 1 ]; then printf '1 second'
    elif [ "$1" -lt 120 ]; then printf '%s seconds' "$1"
    else printf '%s minutes (%s seconds)' "$(( $1 / 60 ))" "$1"
    fi
  }
  if [ -n "$A1_STOPPED" ]; then
    TOOK="the first attempt was stopped after $(_took "$A1_SECS")"
  elif [ -n "$A1_SECS" ]; then
    TOOK="the first attempt ended after $(_took "$A1_SECS")"
  else
    TOOK="the first attempt's running time was not recorded"
  fi
  if [ -n "$A2_STOPPED" ]; then
    TOOK="$TOOK; the retry was stopped after $(_took "$A2_SECS") on the same machine"
  elif [ -n "$A2_SECS" ]; then
    TOOK="$TOOK; the retry ended after $(_took "$A2_SECS") on the same machine"
  else
    TOOK="$TOOK; the retry's running time was not recorded"
  fi
  RUNNER_SHOWN=$(printf '%s' "${RUNNER_NAME:-}" | tr -d '\000-\037`')
  RUNNER_CLASS=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import out_of_memory; print(out_of_memory.runner_class(sys.argv[2]))' \
    "$PIPELINE_DIR/scripts" "$RUNNER_SHOWN" 2>/dev/null || true)
  WHERE="runner \`${RUNNER_SHOWN:-unnamed}\` (class \`${RUNNER_CLASS:-unknown}\`)"
  if [ "${BACKOFF_OUTCOME:-}" = "failure" ]; then
    WHY="The adversarial reviewer was stopped mid-review because the machine ran out of memory. It was reviewing on $WHERE: $TOOK. The 120-second wait before the retry was itself ended early, which a sleep never does on its own, so the machine was killing new processes."
  else
    WHY="The adversarial reviewer was stopped mid-review, cause unknown. It was reviewing on $WHERE: $TOOK. An attempt that runs for more than a minute has started and was working when it stopped."
  fi
  printf '%s\n\n%s\n' \
    "🔎 QA Critic could not run (infra error) — re-review needed, this is NOT a code rejection." \
    "$WHY No verdict was produced and nothing was rejected. This was NOT an authentication or startup failure, so no credential needs rotating on the strength of this message.${SPENT:+ What the execution records report: $SPENT.} Merge is held until a critic actually reviews this PR; this is not a request for changes." \
    > /tmp/qa-comment.md
else
  # Branch 9, crashed: the last resort.
  printf '%s\n\n%s\n' \
    "🔎 QA Critic could not run (infra error) — re-review needed, this is NOT a code rejection." \
    "The adversarial reviewer crashed twice (startup/auth failure, no inference). No findings were produced.${SPENT:+ What the execution records report for those attempts: $SPENT — an instant, one-turn, \$0 run IS the stale-credential signature, and anything more than that means the reviewer did real work and this notice is naming the wrong cause (DRE-2924).} Merge is held until a critic actually reviews this PR; this is not a request for changes." \
    > /tmp/qa-comment.md
fi
fi
# The refutation footer, on every branch, below the parsed line.
if [ "${REFUTED:-}" = "true" ]; then
  printf '\n%s\n' "♻️ re-review after refutation — the fixing agent contested this reviewer's previous finding on this same commit with evidence, so the pipeline ordered one more look. Nothing was pushed between the two verdicts; this one is the answer." \
    >> /tmp/qa-comment.md
fi
# Tried four times: the merge gate reads this comment.
for i in 1 2 3 4; do
  gh pr comment "$PR" --body-file /tmp/qa-comment.md && break
  echo "comment attempt $i failed; retrying in $((i*10))s"; sleep $((i*10))
done
# The card's copy names the model; the pull request's stays byte-identical.
if [ -n "$CARD" ]; then
  { cat /tmp/qa-comment.md
    printf '\n\n🧠 model: %s — %s\n' "$MODEL_ID" "$MODEL_WHY"
  } > /tmp/qa-linear.md
  python3 "$PIPELINE_DIR"/scripts/linear_ops.py comment "$CARD" "$(cat /tmp/qa-linear.md)" || true
fi

# On a real APPROVE, bring the formal review into line (DRE-1874).
if [ "$REAL" = "true" ] && head -1 /tmp/qa-verdict.md | grep -q "VERDICT: APPROVE"; then
  python3 "$PIPELINE_DIR"/scripts/sync_review_state.py "$REPO" "$PR" || true
fi
