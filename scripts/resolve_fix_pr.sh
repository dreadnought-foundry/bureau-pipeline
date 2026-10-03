#!/usr/bin/env bash
set -e
#
# agent-fix.yml's `Resolve PR, mode, and attempt budget` step (id `pr`),
# which runs it as `bash .bureau-pipeline/scripts/resolve_fix_pr.sh`. The
# shell moved here from the workflow's `run:` block unchanged (DRE-5224, by
# `scripts/step_shell.py move`), so it runs as the block ran: `set -e`
# alone, no pipefail. The block's seven GitHub expressions became the two
# env inputs PR_NUMBER and REPO, in the same quoting.
#
# What this script does
# ─────────────────────
#
# It decides whether this fix run does any work, and in which mode. Every
# later step in the job is gated on `steps.pr.outputs.*`, so a run that
# stops here with go=false stops the whole job. In order:
#
#   1. Reads the pull request once with `gh pr view`: its state, head
#      branch, head sha, merge state and base branch. The head sha goes out
#      as `head_sha` first, because the Report step compares it with the head
#      after the run to tell a fix that pushed nothing from one that did. The
#      base branch goes out as `base_ref` for the inherited-failure step,
#      which used to read the pull request again for it (Stage 2 fix #21).
#   2. Refuses anything that is not an `agent/*` or `repair/*` branch, and
#      any pull request that is not OPEN, with go=false.
#   3. Derives the card from the branch name, the first DRE-<n> in any
#      case, upper-cased; empty for a repair branch, and every card write
#      downstream is guarded on it. It prints the card line the medic
#      reads to the log (DRE-4407).
#   4. Reads the whole comment thread ONCE, every page, through
#      gh_read_retry.py into $TMPD/thread.json. A refusal is retried; one
#      that persists stops the step with its reason and writes no go=true.
#      An unreadable thread is never replaced by an empty list, because an
#      empty list reads as no halt, a fresh budget and no standing hold.
#      Everything below reads that one file.
#   5. Applies the convergence halt (`fix_convergence.py halt`). When
#      enough fix runs on THIS head sha already ended with nothing to
#      show, it refuses with go=false and, once per round, posts a
#      `🧯 fix-convergence-halt @<sha8>` comment with WRITER_TOKEN. Only
#      the worker bot's own markers count, and a new commit resets the
#      count. A person's `Operator decision`, newer than the newest halt on
#      this commit, allows exactly one more run there; if that run pushes
#      nothing too, the halt stands again and is posted once for the new
#      round.
#   6. Picks the mode. A DIRTY pull request is in conflict mode unless the
#      critic's standing verdict is REQUEST_CHANGES with no fix pushed
#      since it landed; anything else is fix mode. Only verdicts the
#      qa-bot authored and push markers the worker bot authored count.
#   7. Tells a hand dispatch from a machine one. A workflow_dispatch is by
#      hand only when TRIGGERING_ACTOR is a plain login; `github-actions`
#      and any `[bot]` actor is the pipeline restarting itself.
#   8. Applies the attempt budget (`fix_budget.py decide`), which answers
#      ACTION run, noop or hold, with the attempt number, whether this
#      attempt is re-armed by an operator's answer, and the convergence
#      counts. Fix mode stops on consecutive non-converging review rounds
#      or at a hard ceiling; conflict mode has its own count of five
#      rounds. Its env file is sourced: every value in it is a word or an
#      integer, never thread text.
#   9. Composes the next agent's escalation line: conflict-only
#      instructions in conflict mode, a fresh-eyes re-derivation on fix
#      mode's last budgeted non-converging round, else empty.
#  10. Ends. ACTION=run writes go=true. Otherwise it writes go=false and
#      no_work=true, and then: noop (an answer is standing and the sweep
#      owns the restart) posts at most one tagged notice and never a hold;
#      hold posts the 🛑 hold that asks for an Operator decision and
#      writes held=true.
#
# Outputs, to $GITHUB_OUTPUT, for the steps that read `steps.pr.outputs.*`:
# head_sha, go, mode, number, branch, card, attempt, rearmed, stopped_by,
# classification, fresh_eyes, nonconverging, stop, ceiling, escalation,
# and on the no-work paths no_work and held.
#
# Inputs, all from the step's env, each read as data:
#
#   GH_TOKEN          the pool-selected reader token every read uses, and
#                     the noop notice and the 🛑 hold are posted with
#   WRITER_TOKEN      the App token the convergence halt is posted with, so
#                     it is authored by the login WORKER_LOGIN names
#   WORKER_LOGIN      the loop's own identity, `<app-slug>[bot]`, from this
#                     run's minted token; the markers it counts are its own
#   EVENT_NAME        how the run started (workflow_dispatch, issue_comment…)
#   TRIGGERING_ACTOR  who started it, read from the event, never from text
#                     a commenter can write
#   PR_NUMBER         the pull request: the comment's issue number, or the
#                     dispatch's pr_number input
#   REPO              owner/name, for every `gh` call
#
# From the runner: GITHUB_OUTPUT and RUNNER_TEMP ($TMPD, /tmp without it).
# Files it leaves in $TMPD: thread.json, halt.env, fix-budget.env,
# fix-no-work-note.md, fix-classification.txt, and fix-no-work.txt, which
# the job's no-work report step reads.
#
# Incident history
# ────────────────
#
# One note per card, oldest first, each dated by when it reached
# agent-fix.yml. Where a later card changed an earlier rule, the note states
# the rule as it holds now, once, and names the cards that got it there.
#
# 2026-07-09 · DRE-1988. The loop takes instructions only from the qa-bot:
#   a "QA Critic" verdict counts toward the mode only when the QA bot
#   identity authored it, because any commenter can type the marker. The same card set the discipline WORKER_LOGIN follows: the
#   identity comes from this run's minted token, never a hardcoded name an
#   App rename outdates.
#
# 2026-07-10 · DRE-1995. The worker bot's own bookkeeping (the push
#   marker and the attempt counters) is posted as agent-bureau-bot[bot],
#   which DRE-1988's qa-bot filter did not cover. Every read-back of it —
#   the push marker here, the markers the halt and the budgets count — is
#   filtered to that author, so a planted marker can neither flip
#   fix-versus-conflict routing, freeze the loop on a healthy pull request,
#   burn a budget, nor free one.
#
# 2026-07-10 · DRE-1996. No agent-influenced field reaches the shell raw.
#   The convergence receipt carried to the Report step is one line
#   fix_budget composes from a fixed vocabulary and integers; nothing here
#   echoes a comment body. WHY and CLASSIFICATION carry no backtick,
#   dollar, quote or backslash (pinned by tests/test_fix_convergence.py),
#   which is what makes them safe inside the hold's double-quoted body.
#
# 2026-07-11 · DRE-1927. repair/* branches join agent/*: a repair pull
#   request's REQUEST_CHANGES runs through this loop with its existing
#   budgets, the ADR's no-new-retry-loop guardrail. A repair branch carries
#   no card, which is why every card write is guarded on CARD.
#
# 2026-07-11 · DRE-2024, then DRE-1995 and DRE-4848 (2026-09-25) — the
#   convergence halt. DeltaSolv PR #120 died at "Reached maximum number of
#   turns (60)" five times in one evening; another identical run was a
#   token bonfire, not a retry. DRE-2024 refused a run on a head sha where
#   earlier fix runs ended with the no-progress marker, saying so once per
#   sha; is_error deaths keep their own cap in fix_dead_run.py, and this
#   closed the uncapped escalate path. DRE-1995's filter means only worker
#   bot markers count. DRE-4848 moved the halt's one write to WRITER_TOKEN:
#   posted on the pool reader, the halt was authored by whichever pool bot
#   was selected, its own once-per-commit check never found it, and portico
#   #687 got forty identical halts. The rule as it holds now is step 5
#   above, and lives in fix_convergence.halt().
#
# 2026-08-09 · DRE-2316. Every fix attempt rides the workhorse ladder, and
#   the model id is not pinned here: the job's "Select model" step resolves
#   it from config/models.yaml. The last budgeted non-converging round
#   changes the framing, never the model.
#
# 2026-08-12 · DRE-2409, then DRE-2548 (2026-08-27), DRE-2813 (2026-08-29)
#   and DRE-3451 (2026-09-09) — the hold message. DRE-2409 made the hold
#   state its own way out: an Operator decision comment, matched by intent.
#   The message stays inline rather than reading `fix_context.py
#   --answer-format`, and tests pin each sentence to its fix_context
#   constant so the two cannot drift: the example to DECISION_EXAMPLE
#   (tests/test_operator_decision_restart.py), the next two to
#   RESTART_PROMISE and SKIP_NOTICE (DRE-2548, which tells the operator
#   what actually starts the loop), and the last to HAND_DISPATCH_NOTICE
#   (DRE-2813). What starts the loop, since DRE-3451, is the comment
#   itself: the job's gate admits it and its decision step validates it,
#   with the 15-minute reconcile sweep as the backstop rather than the only
#   route. DRE-4848 made that promise hold through the convergence halt
#   too, for one run.
#
# 2026-08-29 · DRE-2813 (with DRE-2053) — the budgets and the hand
#   dispatch. On PR #199 an operator answered a spent budget at 15:27; a
#   hand dispatch at 15:29 did no work but posted a fresh 🛑 hold; the hold
#   was newer than the answer, and at 15:33 the reconcile sweep stood down
#   for good. Both budgets and what a spent one may do about a standing
#   answer are now fix_budget.py's to decide, through the same fix_context
#   predicates the sweep reads, so "the sweep is armed" and "this dispatch
#   may act" cannot disagree. A hand dispatch on an answered pull request
#   is a noop that posts no hold. The pipeline's own dispatch runs on one
#   re-armed attempt. A run that did nothing writes no_work, which the
#   job's last step turns into a notice and a summary. Hand versus machine
#   is read from the event: `gh workflow run` under a workflow's
#   github.token initiates as `github-actions` (DRE-2053), and the
#   incident's run records show `github-actions[bot]` on all four machine
#   dispatches and a person's login on the hand one
#   (tests/fixtures/agent-fix-runs-2026-08-29.json). Any bot-shaped actor
#   counts as machine. That is the safe direction: reading the sweep's own
#   restart as a hand dispatch would strand the pull request with its one
#   answer spent, while reading a hand dispatch as machine only buys one
#   attempt of real work. The two budgets were split long before, after
#   PR #13 nearly reached a human hold on conflict churn while holding an
#   APPROVE, and PR #25 spent review attempts on a fix the critic never
#   saw — which is why a REQUEST_CHANGES verdict is outstanding only if no
#   fix was pushed since.
#
# 2026-09-08 · DRE-2817. The fix budget measures convergence, not
#   attempts. Fix mode stops on consecutive non-converging review rounds,
#   with a hard ceiling as the runaway backstop. fix_budget's env file
#   gained STOPPED_BY, NONCONVERGING, STOP and CEILING under the same
#   fixed-vocabulary rule, and its one line of prose went to its own file,
#   carried to the Report step as the attempt's classification. The
#   fresh-eyes re-derivation moved off "attempt 3", where a converging loop
#   was told to throw a good approach away, onto the last budgeted
#   non-converging round. The 🛑 hold says which stop it was —
#   non-convergence (the loop was circling) or the ceiling (it kept
#   finding new work and ran out of runway) — after the unchanged "🛑 Fix
#   budget exhausted" prefix that linear_ops.CONSOLE_HOLD_MARKERS and the
#   act registry's `unconverted` row key on.
#
# 2026-09-17 · DRE-4139, then DRE-4157 with DRE-4109 (2026-09-18) — one
#   read. DRE-4139 found three unpaginated reads of the thread, each seeing
#   only GitHub's default page of the oldest 30 comments, so on a long pull
#   request the standing verdict and the last push marker were invisible
#   and a DIRTY pull request picked the wrong mode; it made them one
#   paginated read, so both indexes point into the same list. DRE-4157
#   found four reads in all, the first piped straight into jq, and a
#   throttled `gh` put its error page on stdout and killed jq (portico
#   #582). The thread is now read once, for the halt, the mode and the
#   budgets, through gh_read_retry.py, the sweep's DRE-4109 retry seam, and
#   an unreadable thread stops the step rather than reading as empty.
#
# 2026-09-20 · DRE-4407. A run GitHub records on the default branch, as a
#   dispatch is, names its card in its log on the card line below, so the
#   medic can find the card for it; qa-review.yml carries the full reason.
#   Only that line may carry the card prefix: the medic's test cuts the
#   step at the first line that does.
#
# 2026-10-02 · DRE-5224. The step's shell moved to this file. In the
#   workflow it was a 17,921-character block holding seven expressions,
#   which compiles to one expression against the 18,000-character budget
#   in tests/test_workflow_expression_budget.py (DRE-3484). Its comments
#   were kept short and pointed elsewhere for that reason, and the note on
#   the card-in-the-log line called the block "expression-budget tight".
#   That budget no longer applies here: this file holds no expression.
#
PR=${PR_NUMBER}
INFO=$(gh pr view "$PR" --repo ${REPO} --json state,headRefName,headRefOid,mergeStateStatus,baseRefName)
STATE=$(echo "$INFO" | python3 -c "import json,sys; print(json.load(sys.stdin)['state'])")
BRANCH=$(echo "$INFO" | python3 -c "import json,sys; print(json.load(sys.stdin)['headRefName'])")
MSTATE=$(echo "$INFO" | python3 -c "import json,sys; print(json.load(sys.stdin)['mergeStateStatus'])")
# Read by the Report step: did the fix push a new commit (step 1).
HEAD_SHA=$(echo "$INFO" | python3 -c "import json,sys; print(json.load(sys.stdin)['headRefOid'])")
echo "head_sha=$HEAD_SHA" >> "$GITHUB_OUTPUT"
# The base branch, off the same read (Stage 2 fix #21): the inherited-failure
# step used to read the pull request again for it. Empty when absent, and the
# step then reads it itself.
BASE_REF=$(echo "$INFO" | python3 -c "import json,sys; print(json.load(sys.stdin).get('baseRefName') or '')")
echo "base_ref=$BASE_REF" >> "$GITHUB_OUTPUT"
# agent/* and repair/* only (DRE-1927); a repair branch has no card.
case "$BRANCH" in agent/*|repair/*) ;; *) echo "not an agent branch"; echo "go=false" >> "$GITHUB_OUTPUT"; exit 0;; esac
[ "$STATE" != "OPEN" ] && { echo "PR is $STATE"; echo "go=false" >> "$GITHUB_OUTPUT"; exit 0; }
CARD=$(printf '%s' "$BRANCH" | grep -oiE 'DRE-[0-9]+' | head -1 | tr '[:lower:]' '[:upper:]' || true)
# The card in the log (DRE-4407).
if [ -n "$CARD" ]; then echo "bureau-card: $CARD"; fi

# One read of the thread, then the convergence halt (steps 4 and 5;
# DRE-4157, DRE-2024, DRE-4848).
TMPD="${RUNNER_TEMP:-/tmp}"
python3 .bureau-pipeline/scripts/gh_read_retry.py --out "$TMPD/thread.json" \
  gh api --paginate --slurp \
  "repos/${REPO}/issues/$PR/comments?per_page=100"
SHA8=${HEAD_SHA:0:8}
# Sourced: words and an integer. Unreadable stops the step (bash -e).
python3 .bureau-pipeline/scripts/fix_convergence.py halt \
  --comments-file "$TMPD/thread.json" --worker-login "$WORKER_LOGIN" \
  --sha8 "$SHA8" --env-out "$TMPD/halt.env"
. "$TMPD/halt.env"
if [ "$HALT" = "true" ]; then
  if [ "$POST_HALT" = "true" ]; then
    GH_TOKEN="$WRITER_TOKEN" gh pr comment "$PR" --repo ${REPO} \
      --body "🧯 fix-convergence-halt @$SHA8: $NOPROG fix runs on this exact commit already ended with nothing to show — the loop is not converging, so the pipeline refuses to dispatch another identical run. A new commit on the branch, or a comment from your own account whose first line starts with the words Operator decision, is needed to move this PR. A decision allows one more run on this commit."
  fi
  echo "convergence halt: $NOPROG no-push fix runs at $SHA8 — refusing to run again"
  echo "go=false" >> "$GITHUB_OUTPUT"
  exit 0
fi
if [ "$CLEARED" = "true" ]; then
  echo "convergence halt cleared: a person's Operator decision allows one more run at $SHA8"
fi

# The mode (step 6). Only qa-bot verdicts count (DRE-1988).
VERDICT=$(jq -r '[(add // [])[] | select(.user.login == "agent-bureau-qa-bot[bot]") | select(.body | contains("QA Critic"))] | last | .body // empty' \
  "$TMPD/thread.json" 2>/dev/null | head -1 || true)
# REQUEST_CHANGES is outstanding only if no fix was pushed since.
LAST_VERDICT_IDX=$(jq -r '[(add // []) | to_entries[] | select(.value.user.login == "agent-bureau-qa-bot[bot]") | select(.value.body | contains("QA Critic"))] | last | .key // -1' \
  "$TMPD/thread.json")
# Worker-bot push markers only (DRE-1995).
LAST_PUSH_IDX=$(jq -r '[(add // []) | to_entries[] | select(.value.user.login == "agent-bureau-bot[bot]") | select(.value.body | contains("pushed — CI and critic review re-running"))] | last | .key // -1' \
  "$TMPD/thread.json")
MODE=fix
if [ "$MSTATE" = "DIRTY" ]; then
  case "$VERDICT" in
    *"VERDICT: REQUEST_CHANGES"*)
      [ "$LAST_PUSH_IDX" -gt "$LAST_VERDICT_IDX" ] && MODE=conflict || MODE=fix;;
    *) MODE=conflict;;
  esac
fi
echo "mode=$MODE" >> "$GITHUB_OUTPUT"

# Hand dispatch or machine one, then the budgets (steps 7 and 8;
# DRE-2813).
HAND=false
if [ "$EVENT_NAME" = "workflow_dispatch" ]; then
  HAND=true
  case "$TRIGGERING_ACTOR" in
    github-actions|*'[bot]') HAND=false ;;
  esac
fi
python3 .bureau-pipeline/scripts/fix_budget.py decide \
  --comments-file "$TMPD/thread.json" \
  --worker-login "$WORKER_LOGIN" --mode "$MODE" \
  --hand-dispatch "$HAND" --pr "$PR" \
  --env-out "$TMPD/fix-budget.env" \
  --note-out "$TMPD/fix-no-work-note.md" \
  --summary-out "$TMPD/fix-no-work.txt" \
  --classification-out "$TMPD/fix-classification.txt"
# Sourced: every value is a word or an integer (DRE-2817).
. "$TMPD/fix-budget.env"
# The convergence receipt, never a comment body (DRE-1996).
CLASSIFICATION=$(cat "$TMPD/fix-classification.txt" 2>/dev/null || true)
# Fresh eyes on the last budgeted non-converging round (DRE-2817).
FRESH_EYES=false
if [ "$MODE" = "fix" ] && [ "${NONCONVERGING:-0}" -ge $(( ${STOP:-2} - 1 )) ]; then
  FRESH_EYES=true
fi
{
  echo "number=$PR"; echo "branch=$BRANCH"; echo "card=$CARD"
  echo "attempt=$ATTEMPT"; echo "rearmed=$REARMED"
  echo "stopped_by=$STOPPED_BY"; echo "classification=$CLASSIFICATION"
  echo "fresh_eyes=$FRESH_EYES"; echo "nonconverging=$NONCONVERGING"
  echo "stop=$STOP"; echo "ceiling=$CEILING"
} >> "$GITHUB_OUTPUT"

if [ "$MODE" = "conflict" ]; then
  {
    echo "escalation=MODE: merge-conflict resolution ONLY. The critic has not requested changes — do NOT rework the diff. Merge the default branch into this branch, resolve conflicts preserving BOTH sides' intent (renumber this branch's alembic migration to main's-head + 1 if that is the conflict). A GENERATED lockfile in conflict (package-lock.json, yarn.lock, pnpm-lock.yaml, poetry.lock, Cargo.lock) must NEVER be hand-merged — hand-merging one eats the whole turn budget: take the default branch's copy (git checkout origin/<default-branch> -- <lockfile>) and regenerate it from this branch's manifest with the package manager (e.g. npm install). Then run local checks and push. Commit message: \"merge: resolve conflicts with main (round $ATTEMPT)\". Nothing else."
  } >> "$GITHUB_OUTPUT"
# The model id is not pinned here (DRE-2316).
elif [ "$FRESH_EYES" = "true" ]; then
  echo "escalation=ESCALATION CONTEXT: the last review round did not read as progress — the reviewer re-found something an earlier round already named, reported that an earlier fix came undone, or said nothing about it either way. One more round like that stops this loop. Do NOT assume the previous attempt's approach was right — re-read the card, the spec, the critic's full review history on this PR, and the current diff, then re-derive the correct fix from scratch. If you conclude the critic is wrong or the card itself is flawed, say so in the blocker file named in step 6 below rather than forcing a bad fix." >> "$GITHUB_OUTPUT"
else
  echo "escalation=" >> "$GITHUB_OUTPUT"
fi

if [ "$ACTION" = "run" ]; then
  echo "go=true" >> "$GITHUB_OUTPUT"
  exit 0
fi

# Nothing to do: no_work becomes the run's notice (DRE-2813).
echo "go=false" >> "$GITHUB_OUTPUT"
echo "no_work=true" >> "$GITHUB_OUTPUT"

if [ "$ACTION" = "noop" ]; then
  # An answer is standing and the sweep owns the restart: no hold.
  if [ -s "$TMPD/fix-no-work-note.md" ]; then
    gh pr comment "$PR" --repo ${REPO} \
      --body-file "$TMPD/fix-no-work-note.md"
  fi
  exit 0
fi

# ACTION=hold. Its sentences are pinned to fix_context (DRE-2409,
# DRE-2548, DRE-3451, DRE-2813).
if [ "$MODE" = "conflict" ]; then
  gh pr comment "$PR" --repo ${REPO} \
    --body "🛑 Conflict-resolution budget exhausted ($ATTEMPTS rounds) — main keeps moving under this branch; holding for a human decision.

To release this PR, comment here with a first line that starts with the words Operator decision, for example: **Operator decision** — <your answer here>. Any casing works and your answer can continue on that line. What happens next: your comment starts the fix loop on its own, normally within a minute. If that run never arrives, the pipeline sweep picks your answer up on its next pass, normally within about 15 minutes. You do not need to run anything by hand. If your wording is not recognised as a decision the run skips instead of starting the loop, and the pipeline posts a note here telling you how to re-post it. A skip means the wording was not recognised, never that your answer was rejected. Dispatching the fix workflow by hand is not a second way out: with the budget spent it cannot add an attempt, and while your answer is standing it deliberately does nothing rather than post over it."
else
  # Which stop this was (DRE-2817); WHY and CLASSIFICATION are
  # quote-safe (DRE-1996).
  if [ "$STOPPED_BY" = "ceiling" ]; then
    WHY="it reached the hard ceiling of $CEILING fix attempts. That is the runaway backstop, not a judgement that the loop stopped making progress — it kept finding new work and never finished"
  else
    WHY="$NONCONVERGING review rounds in a row made no progress (the stop budget is $STOP). A round that finds something new, leaves the earlier fixes working and stays in scope does not spend that budget"
  fi
  gh pr comment "$PR" --repo ${REPO} \
    --body "🛑 Fix budget exhausted — $WHY. Holding for a human decision.

$CLASSIFICATION

To release this PR, comment here with a first line that starts with the words Operator decision, for example: **Operator decision** — <your answer here>. Any casing works and your answer can continue on that line. What happens next: your comment starts the fix loop on its own, normally within a minute. If that run never arrives, the pipeline sweep picks your answer up on its next pass, normally within about 15 minutes. You do not need to run anything by hand. If your wording is not recognised as a decision the run skips instead of starting the loop, and the pipeline posts a note here telling you how to re-post it. A skip means the wording was not recognised, never that your answer was rejected. Dispatching the fix workflow by hand is not a second way out: with the budget spent it cannot add an attempt, and while your answer is standing it deliberately does nothing rather than post over it."
fi
echo "held=true" >> "$GITHUB_OUTPUT"
