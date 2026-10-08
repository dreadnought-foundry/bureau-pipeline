#!/usr/bin/env bash
set -e
# What this script does
#
# The `Report result to Linear` step of agent-task.yml runs this file. It
# reads what the agent run left behind, posts one receipt to the card and
# moves the card to the lane that owns what happened. It is the last step
# that runs for the card.
#
# 1. It resolves the card's branch and PR. The branch is the remote
#    `agent/<CARD>-*` branch, passed through `resume_branch.py proof`,
#    which discounts a branch this run started with until it moves. Then
#    `card_pr.py find` answers OPEN or MERGED with the PR's URL. It reads
#    PRs in every state and checks the head ref against this card. Its
#    exit 3 means GitHub would not answer, and the script records
#    UNREADABLE, never "no PR".
# 2. It classifies the run's death through `check_agent_result.py`:
#    `classify` answers none, api_death, turn_exhaustion or
#    credential_expiry. `delivery` answers whether committed work on the
#    runner reached GitHub, and `started` whether the agent took a turn
#    at all. Each answer is the shared predicate's, and each fails soft
#    (to none, delivered and yes). GitHub's outcomes for the five steps
#    before the agent name the one that failed, and `medic_classify.py`
#    says whether that failure was Linear refusing a quota.
# 3. It routes the card. The first of these that holds takes it:
#    - PR already merged: a receipt, and no move. linear-sync owns Done.
#    - PR opened: a receipt, and the card advances to In Review.
#    - Hand-back to Planning: the agent's /tmp/agent-handback.txt is
#      posted and the card advances to Planning. A turn-cap death skips
#      this branch, and the dead-run branch reads it instead.
#    - Escalation to Green Light: /tmp/agent-escalation.txt is posted and
#      the card advances to Green Light, the CEO's "needs you" queue.
#    - Blocker to Backlog: /tmp/agent-blocker.txt is posted and the card
#      parks in Backlog with `state --park`, never Todo.
#    - Rescue delivery: the run failed to deliver and its patch exists,
#      so `deliver_rescue.py handoff` names the artifact on the card and
#      dispatches the job that opens the PR. Nothing is requeued.
#    - GitHub unreadable: a receipt, no move, nothing counted. The
#      reconcile sweep re-checks. A run that failed to deliver skips it.
#    - Otherwise, the dead-run or turn-cap decision. `dead_run.py decide`
#      reads the budget the death spends and answers requeue (Todo), hold
#      (`dead_run.py park`: Backlog and the hold label, both or neither,
#      then the stamp naming the budget), replan (Planning — a turn-cap death
#      short of green, or the dead-run cap trying the planner once on a run
#      that left no PR and no blocker note), infra (a run warning, the card
#      left where it is)
#      or defer (a receipt only, for a cancelled run).
# 4. A turn-cap death that an escalation, a blocker, a rescue delivery or
#    the unreadable receipt took still gets its tagged receipt from
#    `dead_run.py turn-noted`, so the turn-cap count sees it.
#
# Inputs, all from the step's `env:`:
#   CARD                   the card's identifier, from the dispatch payload
#   LINEAR_API_KEY         Linear's key, read by linear_ops.py and dead_run.py
#   GH_TOKEN               the worker App's token, for card_pr.py's PR lookup
#   GH_DISPATCH_TOKEN      the workflow's github.token, which
#                          deliver_rescue.py dispatches deliver-rescue with
#   BUREAU_SERVER_URL, BUREAU_REPOSITORY, BUREAU_RUN_ID
#                          github.server_url, repository and run_id: the run
#                          URL on every receipt, and the delivery's repo and
#                          run id
#   RESUME_BRANCH, RESUME_SHA
#                          the card branch this run resumed, and its tip
#                          when the run started
#   RESUME_SNAPSHOT        every card branch's tip when the run started
#   MODEL_USED             the model the agent ran on, named in the
#                          model-error marker an api_death writes
#   CLAUDE_OUTCOME         the agent step's outcome; `cancelled` defers
#   CLAUDE_EXECUTION_FILE  the agent's execution record (default: the
#                          runner's claude-execution-output.json)
#   DEDUPE_OUTCOME, MODEL_OUTCOME, CTX_OUTCOME, SANITIZE_OUTCOME,
#   INPROGRESS_OUTCOME     GitHub's outcome for each step before the agent
#   PRE_AGENT_LOG          what those steps printed, read for Linear's quota
#                          fingerprint
#   RESCUE_LOCAL_WORK      true when Push rescue found committed work for
#                          this card that GitHub does not have
#   RESCUE_PUSHED          whether the rescue's push landed
#   RESCUE_PUSH_STATUS     the HTTP status GitHub refused that push with
#   RESCUE_ERROR           GitHub's own words when it refused
#   RESCUE_PATCH           the rescue patch's path on the runner
#   RESCUE_ARTIFACT        the name the patch was uploaded under
# RUNNER_TEMP, which the runner sets, holds the card-thread dump.
#
# Incident history
#
# One note per rule, in the date order the comments and git history give.
# Where a later card changed a rule, its note states the rule as it holds
# now and names every card that shaped it.
#
# 2026-06-13, DRE-1403. The shared dead-run cap is counted by the
#   `dead-run-requeue` tag. Silent deaths (here), hung runs (the reconcile
#   sweep), API/model deaths and credential expiries all spend it; turn-cap
#   deaths have their own (see DRE-2312 below). Since 2026-08-09 the count
#   reads only deaths after the last `dead-run-budget-reset` marker, which
#   `linear_ops.py unpark` posts: a card an operator released used to walk
#   back in with its exhausted history and re-hold on its first death.
# 2026-06-14, DRE-1354, DRE-1300. An API/model death (is_error) counts
#   toward the shared cap, and records a `model-error:` marker so the
#   requeue's next attempt selects the next model down the ladder. Before
#   this, such a death failed the job and the medic re-ran it on the same
#   model, past the cap: DRE-1300 looped 18 times.
# 2026-06-24, DRE-1286, DRE-1885. A blocker parks in Backlog, never Todo.
#   The relay dispatches a fresh agent on every Todo transition, and a
#   blocker is deterministic: DRE-1286 was redispatched 6 seconds after its
#   blocker note on 2026-06-12. The move passes `--park`, because the
#   DRE-1885 building-card guard re-routes an In Progress card aimed at
#   Backlog to Todo. The same guard is why the hand-back and replan
#   branches use `advance`, not `state`.
# 2026-06-28, DRE-2034. "Could not tell" is not "no PR". Parsing a failed
#   read as emptiness yanked healthy cards around, so an unreadable PR state
#   posts a receipt, moves nothing and counts nothing, and the reconcile
#   sweep re-asks off the run's real conclusion. That branch sits below the
#   blocker and escalation branches, since a note the agent wrote is better
#   evidence than a failed lookup.
# 2026-07-12, DRE-2074, DRE-2070, DRE-2032. A cancelled agent step is not a
#   death. The job timeout or an external cancel killed a still-working
#   agent, and the old code counted each kill as a silent death: DRE-2070
#   parked after four. `decide --cancelled` answers defer, a receipt that
#   moves and counts nothing, and the reconcile sweep requeues off the run's
#   real conclusion (DRE-2032) under the existing cap.
# 2026-08-08, DRE-1343, DRE-2316. The PR lookup is `card_pr.py find`, the
#   one PR-existence predicate the reconcile sweep shares, run after the
#   card's own branch is resolved. Two incidents shaped it. On 2026-06-12 an
#   empty `--head` filter made `gh pr list` return the repo's newest open
#   PR, which attributed another card's PR to a dead run and sent it to the
#   review lane with a false "PR opened" receipt (DRE-1343). On 2026-08-08
#   the bare `gh pr list --head`, which reads OPEN PRs only, read DRE-2316's
#   PR #137, merged ten seconds earlier (22:22:19, step at 22:22:29), as no
#   PR and requeued shipped work onto a second agent. `card_pr.py` reads
#   every state and checks the head ref against the card, and a merged PR
#   gets its own receipt with no move. `advance` also refuses to drag a card
#   that went Done in the meantime (DRE-2316).
# 2026-08-25, DRE-2312, DRE-2695. One classifier,
#   `check_agent_result.classify_death`, decides how a run died, and every
#   caller goes through it. It replaced the inline is_error test DRE-1354
#   added, which read the raw result dict and reported DRE-2695's
#   turn-exhausted build, 36 minutes of real work, as "agent died with
#   API/model error" with a `model-error: claude-opus-5` marker. Turn
#   exhaustion is its own class with its own budget (DRE-2312): it reads
#   and spends the `turn-exhaustion-requeue` count and passes no
#   `--error-model`, so the DRE-1354 fallback cannot switch models on a
#   budget ceiling.
# 2026-08-27, DRE-1655, DRE-1706, DRE-2776. Escalate by exception: an agent
#   that hit a decision only the CEO should make stops before a PR and
#   writes a plain-English question (DRE-1655/1706). The question is posted
#   and the card goes to Green Light, the CEO's "needs you" queue
#   (DRE-2776), not the broken-card lane: that lane rotted the first time
#   (17 machine-created cards, none ever moved) by mixing the two meanings.
#   The CEO answers in the thread and moves the card to Todo to proceed or
#   Backlog to drop it. Escalation is checked before the blocker branch.
# 2026-09-01, DRE-2931, DRE-2911, DRE-2923. A run that failed before the
#   agent started is a platform fault, not a strike. DRE-2911 was told
#   twice it had died of an API/model error for 20-second runs that cost
#   $0, and the third parked it. Such a run reads no count, since the cap is
#   the card's budget, and `decide` answers infra. GitHub's own step
#   outcomes name the failed step, and DRE-2923's fingerprint (host and code
#   on one line) says whether Linear refused a quota.
# 2026-09-01, DRE-2931, DRE-2911. The park is atomic or it is nothing.
#   DRE-2911's park was two independent `|| true` writes: the label landed
#   and the move did not, and the card sat In Progress for seven and a half
#   hours while its comment said Backlog. `dead_run.py park` retries each
#   write and takes its own label back off when the state never lands. When
#   it writes neither it exits nonzero, and `park-unlanded` posts a receipt
#   that says so, carrying `$PARK_TAG_FLAGS` so it bills the cap the hold
#   came from.
# 2026-09-04, DRE-3043, DRE-3098. A run that finished the card and whose
#   push GitHub refused is a credential expiry, not a dead agent.
#   `--work-on-runner` is what Push rescue saw on the disk, never a reading
#   of the transcript. The death spends the shared dead-run budget, since
#   the rebuild is real, and writes no `model-error:` marker. Its receipt
#   names GitHub's status and the rescue artifact (DRE-3098). Both reach the
#   word-split ERR_FLAGS, so only a three-digit status and a
#   `[A-Za-z0-9._-]` artifact name are admitted.
# 2026-09-09, DRE-3262, DRE-3165. Committed work on the runner plus a push
#   GitHub did not take is a failed delivery, whatever the PR lookup says.
#   On 2026-09-06 a 93-minute green build sat in `rescue-DRE-3165.patch`
#   while the card read "could not read this card's PR state", until a
#   person looked at 11:05 PT. When the patch exists, the rescue-delivery
#   branch names the artifact on the card and hands the delivery to the
#   `deliver-rescue` workflow, without a requeue. It sits above the
#   unreadable branch because the same expired token refused the push and
#   the PR read. When the patch does not exist, the unreadable branch
#   stands aside and DRE-3043's credential-expiry requeue takes the card.
# 2026-09-29, DRE-4366, DRE-3097, DRE-2312. A turn-cap death is read before
#   it is retried. DRE-2312 gave it its own budget, and DRE-3097 a per-card
#   turn budget whose park receipt tells budget from size. Since DRE-4366
#   `decide` reads how far the run got from the card's own `⏳ n/5`
#   receipts, grouped by the `🧠 model-attempt` heartbeat. At or past
#   implementation green the first death requeues at the same budget and
#   the second parks. Before it, the answer is replan: Planning, with no
#   retry at any count. An unreadable thread reads as no marker. Every
#   turn-cap death leaves the tag, whichever exit took the card.
# 2026-09-29, DRE-2727, DRE-4366, DRE-4370. The hand-back goes to Planning,
#   the lane that owes a decomposition, with the agent's pieces attached,
#   and it is the first of the stop-before-a-PR branches (DRE-2727). A
#   turn-cap death skips it: the dead-run branch replans that card the same
#   way and its tagged receipt carries the note (DRE-4366). The note is a
#   split proposal written by the decide-by turn the prompt names, and the
#   receipt's first words make `planning_classify` read the card afresh
#   rather than trust the planner stamp it arrived with. A hand stamp is
#   the operator's override and still stands (DRE-4370).
# 2026-09-29, DRE-4368. A card branch that was already there when this run
#   started, the dead run's whether resumed or not, proves nothing until it
#   moves.
RUN_URL="$BUREAU_SERVER_URL/$BUREAU_REPOSITORY/actions/runs/$BUREAU_RUN_ID"
# The card's own branch first, so the PR lookup reads no other card's (DRE-1343).
BRANCH=$(git branch -r | grep -o "agent/${CARD}-[^ ]*" | head -1 | sed 's|origin/||' || true)
# A branch this run started with proves nothing until it moves (DRE-4368).
BRANCH=$(python3 .bureau-pipeline/scripts/resume_branch.py proof --card "$CARD" --branch "$RESUME_BRANCH" --sha "$RESUME_SHA" --snapshot "$RESUME_SNAPSHOT" --fallback "$BRANCH")
# One PR predicate, every state, head ref checked; exit 3 is UNREADABLE (DRE-2316, DRE-2034).
PR_STATE=""
PR_URL=""
if PR_INFO=$(python3 .bureau-pipeline/scripts/card_pr.py find "$CARD" --branch "$BRANCH"); then
  PR_STATE=$(printf '%s' "$PR_INFO" | cut -f1)
  PR_URL=$(printf '%s' "$PR_INFO" | cut -f2)
else
  PR_STATE="UNREADABLE"
fi

# How the run died, from the one shared classifier (DRE-2312, DRE-2695).
EXEC_FILE="$CLAUDE_EXECUTION_FILE"
[ -n "$EXEC_FILE" ] || EXEC_FILE=/home/runner/work/_temp/claude-execution-output.json
# --work-on-runner: committed work GitHub does not have, from the disk (DRE-3043).
LOCAL_WORK="$RESCUE_LOCAL_WORK"
DEATH_CLASS=$(python3 .bureau-pipeline/scripts/check_agent_result.py classify "$EXEC_FILE" \
  --work-on-runner "$LOCAL_WORK" || echo none)

# Did the work reach GitHub? Fail-soft to `delivered` (DRE-3262).
FAILED_DELIVERY=$(python3 .bureau-pipeline/scripts/check_agent_result.py delivery \
  --work-on-runner "$LOCAL_WORK" --push-status "$RESCUE_PUSH_STATUS" \
  --pushed "$RESCUE_PUSHED" || echo delivered)
RUN_ID="$BUREAU_RUN_ID"
DELIVERY_REPO="$BUREAU_REPOSITORY"

# Did the agent start at all? Fail-open to `yes` (DRE-2931).
AGENT_STARTED=$(python3 .bureau-pipeline/scripts/check_agent_result.py started \
  "$EXEC_FILE" --claude-outcome "$CLAUDE_OUTCOME" --branch "$BRANCH" || echo yes)
# WHICH step failed, from GitHub's own outcomes rather than a guess.
FAILED_STEP=""
for STEP_OUTCOME in "Duplicate-dispatch guard=$DEDUPE_OUTCOME" \
                    "Select model=$MODEL_OUTCOME" \
                    "Assemble agent context=$CTX_OUTCOME" \
                    "Sanitize untrusted card text=$SANITIZE_OUTCOME" \
                    "Card → In Progress=$INPROGRESS_OUTCOME"; do
  if [ "${STEP_OUTCOME##*=}" = "failure" ]; then
    FAILED_STEP="${STEP_OUTCOME%=*}"
    break
  fi
done
# Was that failure Linear refusing a quota? DRE-2923 owns the fingerprint.
RATE_FLAGS=""
if python3 .bureau-pipeline/scripts/medic_classify.py \
     "Agent Task" "$PRE_AGENT_LOG" 2>/dev/null \
     | grep -qx "class=linear_ratelimited"; then
  RATE_FLAGS="--rate-limited"
fi

# Which exit took the card, read by the turn-cap tag at the end (DRE-4366).
EXIT_TAKEN=""
if [ "$PR_STATE" = "MERGED" ]; then
  # Shipped while the run was finishing: report it, move nothing (DRE-2316).
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "🤖 PR already merged: $PR_URL — this card's work shipped while the run was finishing; no requeue. Run: $RUN_URL"
elif [ -n "$PR_URL" ] && [ "$PR_URL" != "null" ]; then
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "🤖 PR opened: $PR_URL — CI + critic review running. Run: $RUN_URL"
  python3 .bureau-pipeline/scripts/linear_ops.py advance "$CARD" "In Review" "In Progress,Todo"
elif [ "$DEATH_CLASS" != "turn_exhaustion" ] && [ -f /tmp/agent-handback.txt ] && [ -s /tmp/agent-handback.txt ]; then
  # Hand-back to Planning; never for a turn-cap death (DRE-2727, DRE-4366, DRE-4370).
  {
    echo "🤖 Handed back to Planning: this card was dispatched as one piece of work and is an epic's worth. The agent stopped rather than sprawl it into one unreviewable pull request. The pieces below are its plan for the split (the agent's split proposal, written at its decide-by turn)."
    echo ""
    cat /tmp/agent-handback.txt
    echo ""
    echo "Run: $RUN_URL"
  } > /tmp/handback-comment.md
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "$(cat /tmp/handback-comment.md)"
  python3 .bureau-pipeline/scripts/linear_ops.py advance "$CARD" "Planning" "In Progress,Todo" || true
elif [ -f /tmp/agent-escalation.txt ] && [ -s /tmp/agent-escalation.txt ]; then
  # Escalation to Green Light, checked before the blocker (DRE-1655, DRE-2776).
  {
    echo "🙋 The agent paused for a decision before building — it judged this needs your call rather than a guess."
    echo ""
    cat /tmp/agent-escalation.txt
    echo ""
    echo "Answer here, then move this card to **Todo** to proceed (the agent picks up your guidance), or to **Backlog** to drop it. Run: $RUN_URL"
  } > /tmp/escalation-comment.md
  EXIT_TAKEN="escalation"
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "$(cat /tmp/escalation-comment.md)"
  python3 .bureau-pipeline/scripts/linear_ops.py advance "$CARD" "Green Light" "In Progress,Todo" || \
    python3 .bureau-pipeline/scripts/linear_ops.py state "$CARD" "Green Light" || true
elif [ -f /tmp/agent-blocker.txt ]; then
  # Backlog, never Todo: a redispatched blocker hits the same wall (DRE-1286).
  EXIT_TAKEN="blocker note"
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "🛑 Agent blocked: $(cat /tmp/agent-blocker.txt) — parked in Backlog until the blocker is resolved (a Todo return here would redispatch agents into the same wall). Run: $RUN_URL"
  # --park, or the DRE-1885 building-card guard re-routes this to Todo.
  python3 .bureau-pipeline/scripts/linear_ops.py state "$CARD" "Backlog" --park || true
elif [ "$FAILED_DELIVERY" = "failed" ] && [ -n "$RESCUE_PATCH" ] && [ -f "$RESCUE_PATCH" ]; then
  # Rescue delivery, deliberately above the unreadable branch (DRE-3262, DRE-3165).
  EXIT_TAKEN="rescue delivery"
  python3 .bureau-pipeline/scripts/deliver_rescue.py handoff \
    --card "$CARD" --repo "$DELIVERY_REPO" --run-id "$RUN_ID" \
    --artifact "$RESCUE_ARTIFACT" --status "$RESCUE_PUSH_STATUS" \
    --stderr "$RESCUE_ERROR" || true
elif [ "$PR_STATE" = "UNREADABLE" ] && [ "$FAILED_DELIVERY" != "failed" ]; then
  # GitHub unreadable: move and count nothing, unless delivery failed (DRE-2034, DRE-3262).
  EXIT_TAKEN="unreadable-PR-state receipt"
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "⚠️ Could not read this card's PR state from GitHub (API error), so this run is NOT being recorded as a dead agent — the reconcile sweep will re-check. Run: $RUN_URL"
else
  # The dead-run or turn-cap decision. PARK_TAG_FLAGS carries which cap a hold spent.
  PARK_TAG_FLAGS=""
  if [ "$AGENT_STARTED" = "no" ]; then
    # A platform fault, not a strike: no count is read (DRE-2931, DRE-2911).
    PRIOR=0
    ERR_FLAGS="--pre-agent $RATE_FLAGS"
  elif [ "$DEATH_CLASS" = "turn_exhaustion" ]; then
    PRIOR=$(python3 .bureau-pipeline/scripts/linear_ops.py count-comments \
      "$CARD" "turn-exhaustion-requeue" --since "dead-run-budget-reset" || echo 0)
    # How far the run got, from the card's ⏳ receipts; fail-soft to [] (DRE-4366, DRE-3097).
    COMMENTS_FILE="${RUNNER_TEMP:-/tmp}/card-comments.json"
    python3 .bureau-pipeline/scripts/linear_ops.py dump-comments "$CARD" \
      > "$COMMENTS_FILE" || echo '[]' > "$COMMENTS_FILE" || true
    ERR_FLAGS="--turn-exhaustion --execution-file $EXEC_FILE --comments-file $COMMENTS_FILE"
    PARK_TAG_FLAGS="--turn-exhaustion"
  elif [ "$DEATH_CLASS" = "credential_expiry" ]; then
    # Finished, push refused: the shared budget, no model-error marker (DRE-3043).
    PRIOR=$(python3 .bureau-pipeline/scripts/linear_ops.py count-comments \
      "$CARD" "dead-run-requeue" --since "dead-run-budget-reset" || echo 0)
    ERR_FLAGS="--credential-expiry"
    # ERR_FLAGS is word-split: only a three-digit status gets in (DRE-3098).
    case "$RESCUE_PUSH_STATUS" in
      ''|*[!0-9]*) ;;
      *) ERR_FLAGS="$ERR_FLAGS --push-status $RESCUE_PUSH_STATUS" ;;
    esac
    # Same for the artifact name, which carries the payload's card identifier.
    if [ -n "$RESCUE_PATCH" ] && [ -f "$RESCUE_PATCH" ]; then
      case "$RESCUE_ARTIFACT" in
        *[!A-Za-z0-9._-]*) ;;
        *) ERR_FLAGS="$ERR_FLAGS --artifact $RESCUE_ARTIFACT" ;;
      esac
    fi
  else
    PRIOR=$(python3 .bureau-pipeline/scripts/linear_ops.py count-comments \
      "$CARD" "dead-run-requeue" --since "dead-run-budget-reset" || echo 0)
    # The thread decides whether the cap tries the planner first (DRE-6178).
    COMMENTS_FILE="${RUNNER_TEMP:-/tmp}/card-comments.json"
    python3 .bureau-pipeline/scripts/linear_ops.py dump-comments "$CARD" \
      > "$COMMENTS_FILE" || echo '[]' > "$COMMENTS_FILE" || true
    ERR_FLAGS="--comments-file $COMMENTS_FILE"
    if [ "$DEATH_CLASS" = "api_death" ]; then
      ERR_FLAGS="$ERR_FLAGS --is-error --error-model $MODEL_USED"
    fi
  fi
  if [ "$CLAUDE_OUTCOME" = "cancelled" ]; then
    ERR_FLAGS="$ERR_FLAGS --cancelled"
  fi
  # The stamp names the budget the hold spent (DRE-6178).
  PARK_REASON="dead-run-cap"
  if [ "$PARK_TAG_FLAGS" = "--turn-exhaustion" ]; then PARK_REASON="turn-cap-park"; fi
  # shellcheck disable=SC2086  # ERR_FLAGS is deliberately word-split
  DECISION=$(python3 .bureau-pipeline/scripts/dead_run.py decide \
    "${PRIOR:-0}" $ERR_FLAGS --failed-step "$FAILED_STEP" --run-url "$RUN_URL")
  ACTION=$(printf '%s\n' "$DECISION" | head -1)
  BODY=$(printf '%s\n' "$DECISION" | tail -n +3)
  if [ "$ACTION" = "hold" ] && ! python3 .bureau-pipeline/scripts/dead_run.py park "$CARD" --reason "$PARK_REASON"; then ACTION="hold-unlanded"; fi
  if [ "$ACTION" = "hold-unlanded" ]; then BODY=$(python3 .bureau-pipeline/scripts/dead_run.py park-unlanded --run-url "$RUN_URL" "$PARK_TAG_FLAGS"); fi
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" "$BODY"
  # The park is atomic or it is nothing; an unlanded park says so (DRE-2931).
  if [ "$ACTION" = "hold-unlanded" ]; then
    echo "::error title=Park did not land::$CARD reached a hold cap but Linear refused the writes — the card is NOT parked and carries no label. The reconcile sweep re-attempts it."
  elif [ "$ACTION" = "requeue" ]; then
    python3 .bureau-pipeline/scripts/linear_ops.py state "$CARD" "Todo" || true
  elif [ "$ACTION" = "replan" ]; then
    # A turn-cap death before green, or the dead-run cap's hand-off: Planning (DRE-4366, DRE-6178).
    python3 .bureau-pipeline/scripts/linear_ops.py advance "$CARD" "Planning" "In Progress,Todo" || true
  elif [ "$ACTION" = "infra" ]; then
    # A fault against the run, reported on the run; the card stays where it is.
    echo "::warning title=Infrastructure fault before the agent started::${FAILED_STEP:-a step before the agent} failed; no agent ran and no model was called, so this is NOT charged to $CARD."
  fi
  # ACTION=defer (cancelled run): receipt only; reconcile owns the requeue (DRE-2074).
fi

# Every turn-cap death leaves the tag, whichever exit took the card (DRE-4366).
if [ -n "$EXIT_TAKEN" ] && [ "$DEATH_CLASS" = "turn_exhaustion" ]; then
  TURN_NOTE=$(python3 .bureau-pipeline/scripts/dead_run.py turn-noted \
    --exit "$EXIT_TAKEN" --execution-file "$EXEC_FILE" --run-url "$RUN_URL") || TURN_NOTE=""
  if [ -n "$TURN_NOTE" ]; then
    python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" "$TURN_NOTE" || true
  fi
fi
