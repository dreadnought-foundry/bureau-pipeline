#!/usr/bin/env bash
set -e
RUN_URL="$BUREAU_SERVER_URL/$BUREAU_REPOSITORY/actions/runs/$BUREAU_RUN_ID"
# Resolve the card's branch FIRST: an empty --head filter makes
# `gh pr list` return the repo's most recent open PR, which once
# attributed another card's PR to a dead run and sent it to the review lane
# with a false "PR opened" receipt (DRE-1343, 2026-06-12).
BRANCH=$(git branch -r | grep -o "agent/${CARD}-[^ ]*" | head -1 | sed 's|origin/||' || true)
# DRE-4368: a branch that was already there when this run started
# (the dead run's, resumed or not) proves nothing until it moves.
BRANCH=$(python3 .bureau-pipeline/scripts/resume_branch.py proof --card "$CARD" --branch "$RESUME_BRANCH" --sha "$RESUME_SHA" --snapshot "$RESUME_SNAPSHOT" --fallback "$BRANCH")
# Then ask card_pr.py — the ONE PR-existence predicate the reconcile
# sweep shares (DRE-2316). It queries --state all, so a PR that
# MERGED seconds ago is visible; this step used to run a bare
# `gh pr list --head` (default: OPEN only), which on 2026-08-08 read
# DRE-2316's PR #137 — merged ten seconds earlier — as NO PR, posted
# "agent died with no PR", and requeued shipped work onto a second
# agent. It also confirms the head ref against this card, so the
# DRE-1343 mis-attribution cannot return through the new seam.
# Exit 3 means UNREADABLE (403 / rate limit / network) — NOT "no PR"
# (DRE-2034); that branch defers instead of declaring a death.
PR_STATE=""
PR_URL=""
if PR_INFO=$(python3 .bureau-pipeline/scripts/card_pr.py find "$CARD" --branch "$BRANCH"); then
  PR_STATE=$(printf '%s' "$PR_INFO" | cut -f1)
  PR_URL=$(printf '%s' "$PR_INFO" | cut -f2)
else
  PR_STATE="UNREADABLE"
fi

# HOW did the run die? (DRE-1354, split by DRE-2312.) One shared
# classifier answers it — `api_death`, `turn_exhaustion`, or `none`
# — so this step and the gate can never tell different stories.
#
# This line used to be its own inline is_error test, reading the raw
# result dict. That is why DRE-2695's turn-exhausted BUILD run was reported
# as "agent died with API/model error (is_error) … dead run 1/3"
# with a `model-error: claude-opus-5` marker after 36 minutes of real
# work: the shared predicate was one import away and this path did
# not call it. There is exactly one is_error test in the codebase now
# (check_agent_result.classify_death) and every caller goes through
# it.
EXEC_FILE="$CLAUDE_EXECUTION_FILE"
[ -n "$EXEC_FILE" ] || EXEC_FILE=/home/runner/work/_temp/claude-execution-output.json
# --work-on-runner (DRE-3043): what the Push rescue step OBSERVED —
# committed work for this card that GitHub does not have. It is the
# difference between "the agent died with no PR" and "the run did the
# work and its credential expired before it could deliver it", and it
# is a fact from the disk, never a reading of the transcript.
LOCAL_WORK="$RESCUE_LOCAL_WORK"
DEATH_CLASS=$(python3 .bureau-pipeline/scripts/check_agent_result.py classify "$EXEC_FILE" \
  --work-on-runner "$LOCAL_WORK" || echo none)

# DID IT DELIVER? (DRE-3262.) A second question about the same fact,
# and the one the DRE-3165 run never asked: committed work on the
# runner plus a push GitHub did not accept is a FAILED DELIVERY,
# whatever the PR lookup answers. Both inputs are needed — a rescue
# that pushed successfully still reports local_work=true — and the
# answer comes from the shared predicate, never from a shell test of
# its own. Fail-soft to `delivered`: an unreadable answer must leave
# every other branch exactly as it was.
FAILED_DELIVERY=$(python3 .bureau-pipeline/scripts/check_agent_result.py delivery \
  --work-on-runner "$LOCAL_WORK" --push-status "$RESCUE_PUSH_STATUS" \
  --pushed "$RESCUE_PUSHED" || echo delivered)
RUN_ID="$BUREAU_RUN_ID"
DELIVERY_REPO="$BUREAU_REPOSITORY"

# DID THE AGENT START AT ALL? (DRE-2931.) Every class above presumes
# a run: a run that failed BEFORE claude-code-action took no turn,
# called no model and spent nothing, so it cannot have failed at this
# card's work. Same discipline as the classifier — one shared
# predicate, never a shell test of its own. Fail-open to "yes": if
# the predicate itself cannot answer, count the death exactly as
# today rather than quietly stop counting real ones.
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
# And was that failure Linear refusing a quota? DRE-2923 owns that
# fingerprint (host AND code on one line, so prose quoting a
# RATELIMITED payload cannot classify) — read it, never re-derive it.
RATE_FLAGS=""
if python3 .bureau-pipeline/scripts/medic_classify.py \
     "Agent Task" "$PRE_AGENT_LOG" 2>/dev/null \
     | grep -qx "class=linear_ratelimited"; then
  RATE_FLAGS="--rate-limited"
fi

# WHICH EXIT TOOK THE CARD, when it was not the death branch below
# (DRE-4366). A turn-cap death that also left an escalation or a
# blocker, or whose PR state GitHub would not give, or whose work
# went to a rescue delivery, is owned by that exit — and used to
# leave no `turn-exhaustion-requeue` receipt at all, so neither the
# count nor the split ledger saw it. Read after the chain.
EXIT_TAKEN=""
if [ "$PR_STATE" = "MERGED" ]; then
  # The work SHIPPED while this run was finishing (DRE-2316: PR #137
  # merged at 22:22:19, this step ran at 22:22:29). Report it and
  # change nothing: linear-sync's merge→Done owns the card's state,
  # and the reconcile sweep re-closes it if that ever failed. The one
  # thing this branch must never do is treat a merged PR as a death.
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "🤖 PR already merged: $PR_URL — this card's work shipped while the run was finishing; no requeue. Run: $RUN_URL"
elif [ -n "$PR_URL" ] && [ "$PR_URL" != "null" ]; then
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "🤖 PR opened: $PR_URL — CI + critic review running. Run: $RUN_URL"
  python3 .bureau-pipeline/scripts/linear_ops.py advance "$CARD" "In Review" "In Progress,Todo"
elif [ "$DEATH_CLASS" != "turn_exhaustion" ] && [ -f /tmp/agent-handback.txt ] && [ -s /tmp/agent-handback.txt ]; then
  # The hand-back rule (DRE-2727). The agent opened what the card
  # describes as one piece of work and found an epic's worth: several
  # independent PRs, several owners, contracts between them. The two
  # existing exits are both wrong for it — an escalation asks the CEO
  # a question nobody needs to answer ("should this be planned?" has
  # one answer), and a blocker parks it inert in Backlog where the
  # planner never sees it. So it goes back to Planning, which is the
  # lane that owes a decomposition, with the pieces the agent already
  # found written down so the planner does not re-derive them.
  #
  # Checked FIRST of the three stop-before-a-PR branches: it is the
  # most specific, and an agent that wrote a hand-back note has
  # already decided the card cannot ship as one PR.
  #
  # `advance`, not `state`: cmd_state's DRE-1885 building-card guard
  # re-routes an In Progress card aimed at a backlog-type state back
  # to Todo, which would re-dispatch straight into the same sprawl.
  # cmd_advance writes the target directly and still refuses to drag
  # a card that went Done in the meantime (DRE-2316).
  #
  # NOT for a turn-cap death (DRE-4366). An agent that wrote its
  # hand-back and then ran out of turns is a death to be READ: the
  # death branch below sends it to Planning just the same when the
  # run stopped short of implementation green, and its replan
  # receipt carries this note — tagged, so the death is counted.
  #
  # Since DRE-4370 the note is a split proposal written by the
  # decide-by turn the prompt names, and the header's first words are
  # a return receipt: planning_classify reads the card afresh past
  # it rather than trusting the planner stamp it arrived with. A
  # hand stamp is the operator's override and still stands.
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
  # Escalate-by-exception (DRE-1655/1706): the agent hit GENUINE
  # uncertainty — ambiguous intent it can't safely resolve, a
  # risky/destructive change, or a business A-vs-B decision the CEO
  # should own — so it stopped BEFORE opening a PR and wrote a
  # plain-English question. Post the question and park the card in
  # Green Light — the CEO's "needs you" queue (DRE-2776). An
  # escalated card is NOT broken: it is correct, and waiting on a
  # judgement only the CEO can make. The broken-card lane is for a
  # card that went wrong — unroutable, held, bounced by the readiness
  # guard — and it rotted the first time (17 machine-created cards,
  # none ever moved) precisely by mixing those two meanings.
  # The CEO answers in-thread and moves the card → Todo to proceed
  # (a fresh run reads the guidance) or → Backlog to drop it. Checked
  # BEFORE the blocker branch: an escalation is a human DECISION that
  # unblocks the build, distinct from an impossible-as-specified card.
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
  # Backlog, NEVER Todo: the relay dispatches a fresh agent on every
  # Todo transition, and a blocker is deterministic — the next agent
  # reads the same card and blocks on the same wall, forever. (Origin:
  # DRE-1286 redispatched 6s after its blocker note, 2026-06-12.)
  # Backlog is inert until a human (or a description fix + the
  # dependency gate) releases it.
  EXIT_TAKEN="blocker note"
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "🛑 Agent blocked: $(cat /tmp/agent-blocker.txt) — parked in Backlog until the blocker is resolved (a Todo return here would redispatch agents into the same wall). Run: $RUN_URL"
  # --park: a blocker is a DELIBERATE Backlog hold (Todo would
  # redispatch into the same wall — DRE-1286). Without it the
  # DRE-1885 building-card guard would re-route this In Progress →
  # Backlog move to Todo and reintroduce that loop.
  python3 .bureau-pipeline/scripts/linear_ops.py state "$CARD" "Backlog" --park || true
elif [ "$FAILED_DELIVERY" = "failed" ] && [ -n "$RESCUE_PATCH" ] && [ -f "$RESCUE_PATCH" ]; then
  # THE RUN STILL DELIVERS (DRE-3262). The work is committed on a disk
  # that is about to be destroyed, GitHub refused the push on both of
  # the rescue's own fresh mints, and the patch is already uploaded as
  # a run artifact. Two things have to happen here, in this step,
  # because this is the last step that will ever run for this card:
  #
  #   1. SAY IT ON THE CARD, naming the artifact and this run. On
  #      2026-09-06 the card instead read "the push-rescue step
  #      delivers the branch and opens the PR" followed by "could not
  #      read this card's PR state … NOT being recorded as a dead
  #      agent", and a 93-minute green build sat in
  #      `rescue-DRE-3165.patch` until a person went looking at 11:05
  #      PT. A rescued patch only a human knows about is the DRE-3043
  #      loss with an extra step.
  #   2. HAND THE DELIVERY TO A JOB THAT CAN DO IT — a
  #      `deliver-rescue` workflow_dispatch carrying the run id, which
  #      downloads the artifact with its OWN freshly minted token,
  #      applies it on a fresh branch and opens the pull request. The
  #      comment goes out whether or not the dispatch is accepted (a
  #      repo with no such stub answers 404), so the artifact fact is
  #      on the card either way and the reconcile sweep can re-check
  #      off it.
  #
  # DELIBERATELY ABOVE THE UNREADABLE BRANCH. Both branches can be
  # true at once — it was the same expired token that refused the push
  # and 401'd the PR read — and this one is built on a fact from the
  # runner's disk while that one is built on a question GitHub
  # declined to answer. The card is NOT requeued to Todo here: a
  # requeue rebuilds from nothing the work the artifact is holding
  # (~90 minutes and the run's cost again), and if the delivery does
  # not land, the sweep's re-check spends the dead-run budget instead.
  #
  # AND ONLY WHEN THE PATCH REALLY EXISTS. `write_patch` can fail —
  # a root commit with no merge base, a full disk — and when it does,
  # push_rescue says plainly that "this run's disk is the only copy of
  # the work". There is nothing to deliver then and nothing to name on
  # the card, so this branch stands aside and DRE-3043's remedy takes
  # the card: the credential-expiry receipt and the requeue below,
  # which is the only remedy left when the work is genuinely gone.
  EXIT_TAKEN="rescue delivery"
  python3 .bureau-pipeline/scripts/deliver_rescue.py handoff \
    --card "$CARD" --repo "$DELIVERY_REPO" --run-id "$RUN_ID" \
    --artifact "$RESCUE_ARTIFACT" --status "$RESCUE_PUSH_STATUS" \
    --stderr "$RESCUE_ERROR" || true
elif [ "$PR_STATE" = "UNREADABLE" ] && [ "$FAILED_DELIVERY" != "failed" ]; then
  # …UNLESS THE RUN PLAINLY FAILED TO DELIVER (DRE-3262). Reached only
  # when there is no artifact to hand a delivery job — the branch
  # above took the deliverable case — and then this run's work is
  # genuinely gone, so a rebuild is the remedy and the death branch
  # below owns it. It was the SAME expired token that refused the push
  # and 401'd this lookup, so letting the failed lookup answer is
  # letting the cause of the problem certify that there isn't one.
  #
  # GitHub would not tell us whether a PR exists (403, rate limit,
  # network). "Could not tell" is NOT "no PR" — parsing a failed read
  # as emptiness is what yanked healthy cards around on 2026-06-28
  # (DRE-2034). Post the receipt, move nothing, count nothing: the
  # reconcile sweep re-asks off the run's real conclusion, with the
  # existing cap. Deliberately below the blocker/escalation branches
  # — an honest note the agent wrote is better evidence than a failed
  # lookup.
  EXIT_TAKEN="unreadable-PR-state receipt"
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "⚠️ Could not read this card's PR state from GitHub (API error), so this run is NOT being recorded as a dead agent — the reconcile sweep will re-check. Run: $RUN_URL"
else
  # No PR and no blocker. Three death classes share ONE hold cap
  # (turn exhaustion has its OWN — see the DRE-2312 note below),
  # counted by the 'dead-run-requeue' tag (DRE-1403): silent (here),
  # hung (reconcile sweep), and — new in DRE-1354 — is_error (an
  # API/model death mid-run). dead_run.decide() unifies requeue-vs-hold
  # and, for an is_error death, records a 'model-error:' marker so the
  # requeue's next attempt selects the NEXT model down the ladder
  # instead of re-dying on the same one. is_error deaths now COUNT
  # toward the cap (previously they failed the job and the medic re-ran
  # on the same model, bypassing it — DRE-1300 looped 18×).
  #
  # A CANCELLED agent step is NOT a death (DRE-2074): the job timeout
  # (or an external cancel) killed a still-working agent — the old
  # code counted each kill as a silent death and parked DRE-2070
  # after 4 of them. decide --cancelled answers "defer": post the
  # receipt, move nothing, count nothing; the reconcile sweep
  # requeues off the run's real conclusion (DRE-2032), with the
  # existing cap.
  #
  # --since "dead-run-budget-reset": the cap is a BUDGET, and a human
  # can refill it. Nothing used to reset the count — and the hold
  # comment itself carries the 'dead-run-requeue' tag — so a card an
  # operator released (label cleared, Backlog → Todo) walked back in
  # with its whole exhausted history and re-held on its very first
  # subsequent death. `linear_ops.py unpark <CARD>` posts the reset
  # marker; only deaths AFTER it count here.
  #
  # TURN EXHAUSTION IS ITS OWN BUDGET (DRE-2312): the agent ran out
  # of steps, which is a failed attempt, not a death of the service
  # and not a fault of the model. It reads and spends the
  # 'turn-exhaustion-requeue' count instead, and passes NO
  # --error-model, so no 'model-error:' marker is written and the
  # DRE-1354 fallback cannot switch models on a budget ceiling.
  # And it is READ before it is retried (DRE-4366): past
  # implementation green the first death requeues at the same budget
  # and the second parks; before it, `decide` answers "replan" and
  # the card goes to Planning with no retry at any count.
  # WHICH cap a hold came from, carried to the park receipt below.
  # `decide` reaches "hold" from two independent budgets and the park
  # step is uniform for either, so the branch that picked the budget
  # is the only place that still knows which one it was.
  PARK_TAG_FLAGS=""
  if [ "$AGENT_STARTED" = "no" ]; then
    # A PLATFORM FAULT, not a strike (DRE-2931). The run died before
    # the agent: no turn, no model, nothing spent. Checked FIRST of
    # the classes because none of them can be true of a run that
    # never started — DRE-2911 was told it had died of an API/model
    # error, twice, for 20-second runs that cost $0, and the third
    # one parked it as "a human must split/fix the card".
    #
    # No count is read at all: the cap is the CARD's budget and this
    # is not the card's fault, so the answer cannot change the
    # decision — and under the exhausted quota that caused this, the
    # count-comments call would fail too.
    PRIOR=0
    ERR_FLAGS="--pre-agent $RATE_FLAGS"
  elif [ "$DEATH_CLASS" = "turn_exhaustion" ]; then
    PRIOR=$(python3 .bureau-pipeline/scripts/linear_ops.py count-comments \
      "$CARD" "turn-exhaustion-requeue" --since "dead-run-budget-reset" || echo 0)
    # HOW FAR DID THIS RUN GET? (DRE-4366, after DRE-3097.) The
    # card's own thread answers it — the `⏳ n/5` phase receipts,
    # grouped by the `🧠 model-attempt` heartbeat that starts a run;
    # the dump is taken after the run, so the last group is the run
    # that just died. `decide` reads the furthest marker in it:
    # budget (retry, then park) past implementation green, size
    # (Planning, no retry) before it. Fail-soft at every step: an
    # unreadable thread writes an empty array, which reads as no
    # marker — and no evidence the work finished spends no run.
    COMMENTS_FILE="${RUNNER_TEMP:-/tmp}/card-comments.json"
    python3 .bureau-pipeline/scripts/linear_ops.py dump-comments "$CARD" \
      > "$COMMENTS_FILE" || echo '[]' > "$COMMENTS_FILE" || true
    ERR_FLAGS="--turn-exhaustion --execution-file $EXEC_FILE --comments-file $COMMENTS_FILE"
    PARK_TAG_FLAGS="--turn-exhaustion"
  elif [ "$DEATH_CLASS" = "credential_expiry" ]; then
    # DRE-3043. The re-mint above did not save this one — so say what
    # actually happened. The run finished the card and GitHub refused
    # the push; calling that "the agent died" sends whoever reads the
    # card to the model, the service and the card itself, none of
    # which is at fault. It spends the SHARED dead-run budget (the
    # rebuild is real) and passes NO --error-model, so no
    # `model-error:` marker arms the DRE-1354 fallback against a
    # model that never failed.
    PRIOR=$(python3 .bureau-pipeline/scripts/linear_ops.py count-comments \
      "$CARD" "dead-run-requeue" --since "dead-run-budget-reset" || echo 0)
    ERR_FLAGS="--credential-expiry"
    # DRE-3098: name what GitHub actually said, and where the work
    # is. The status is admitted only if it IS a status — ERR_FLAGS
    # is deliberately word-split, so nothing but three digits may
    # reach it.
    case "$RESCUE_PUSH_STATUS" in
      ''|*[!0-9]*) ;;
      *) ERR_FLAGS="$ERR_FLAGS --push-status $RESCUE_PUSH_STATUS" ;;
    esac
    # Same discipline for the artifact name: it carries the card
    # identifier, which arrives from the relay's payload.
    if [ -n "$RESCUE_PATCH" ] && [ -f "$RESCUE_PATCH" ]; then
      case "$RESCUE_ARTIFACT" in
        *[!A-Za-z0-9._-]*) ;;
        *) ERR_FLAGS="$ERR_FLAGS --artifact $RESCUE_ARTIFACT" ;;
      esac
    fi
  else
    PRIOR=$(python3 .bureau-pipeline/scripts/linear_ops.py count-comments \
      "$CARD" "dead-run-requeue" --since "dead-run-budget-reset" || echo 0)
    ERR_FLAGS=""
    if [ "$DEATH_CLASS" = "api_death" ]; then
      ERR_FLAGS="--is-error --error-model $MODEL_USED"
    fi
  fi
  if [ "$CLAUDE_OUTCOME" = "cancelled" ]; then
    ERR_FLAGS="$ERR_FLAGS --cancelled"
  fi
  # shellcheck disable=SC2086  # ERR_FLAGS is deliberately word-split
  DECISION=$(python3 .bureau-pipeline/scripts/dead_run.py decide \
    "${PRIOR:-0}" $ERR_FLAGS --failed-step "$FAILED_STEP" --run-url "$RUN_URL")
  ACTION=$(printf '%s\n' "$DECISION" | head -1)
  BODY=$(printf '%s\n' "$DECISION" | tail -n +3)
  if [ "$ACTION" = "hold" ] && ! python3 .bureau-pipeline/scripts/dead_run.py park "$CARD"; then ACTION="hold-unlanded"; fi
  if [ "$ACTION" = "hold-unlanded" ]; then BODY=$(python3 .bureau-pipeline/scripts/dead_run.py park-unlanded --run-url "$RUN_URL" "$PARK_TAG_FLAGS"); fi
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" "$BODY"
  # THE PARK IS ATOMIC OR IT IS NOTHING (DRE-2931). This used to be
  # two independent `|| true` writes — add the label, then move the
  # card — and DRE-2911's park landed the first and lost the second:
  # the card sat In Progress for seven and a half hours while its own
  # comment said it was in Backlog, and nothing retried it, because
  # the label is what tells every sweep a held card is already
  # handled. `dead_run.py park` retries each write and takes its OWN
  # label back off if the state never lands, so neither survives
  # alone; when it writes neither it exits nonzero and the receipt
  # above says so instead of claiming a hold that never happened.
  # The sweep re-attempts on its next pass.
  #
  # $PARK_TAG_FLAGS is why that receipt knows WHICH budget it is
  # spending. `decide` reaches "hold" from two independent caps and
  # the unlanded receipt REPLACES the body it wrote, so a tag
  # hardcoded in park-unlanded would lose the turn strike a turn-cap
  # hold actually spent and bill the dead-run budget for a death
  # that never happened — the same accounting corruption the atomic
  # park removes, one corner over. It is empty on every path but the
  # turn-exhaustion one, and quoted, so the call is always exactly
  # one extra argument the CLI ignores when there is nothing to say.
  if [ "$ACTION" = "hold-unlanded" ]; then
    echo "::error title=Park did not land::$CARD reached a hold cap but Linear refused the writes — the card is NOT parked and carries no label. The reconcile sweep re-attempts it."
  elif [ "$ACTION" = "requeue" ]; then
    python3 .bureau-pipeline/scripts/linear_ops.py state "$CARD" "Todo" || true
  elif [ "$ACTION" = "replan" ]; then
    # A turn-cap death before implementation green (DRE-4366): no
    # retry, no hold label, no Backlog. The hand-back path's own
    # write, copied — `advance`, not `state`, so the DRE-1885
    # building-card guard cannot re-route it to Todo and re-dispatch
    # the same card into the same ceiling.
    python3 .bureau-pipeline/scripts/linear_ops.py advance "$CARD" "Planning" "In Progress,Todo" || true
  elif [ "$ACTION" = "infra" ]; then
    # The fault is against the RUN, not the card, so it is reported
    # where run faults belong — on the run — and the card is left
    # exactly where it was for the reconcile sweep to pick up.
    echo "::warning title=Infrastructure fault before the agent started::${FAILED_STEP:-a step before the agent} failed; no agent ran and no model was called, so this is NOT charged to $CARD."
  fi
  # ACTION=defer (cancelled run): receipt only — the card stays
  # In Progress and reconcile owns the requeue after the run
  # actually concludes. A turn-cap death that was then killed still
  # opens its receipt with the tag (DRE-4366), so it is counted.
fi

# EVERY TURN-CAP DEATH LEAVES THE TAG (DRE-4366). The exit that took
# the card above owns it and nothing here moves it; this is the
# death's own record, so `count-comments` and the split ledger see
# it. Best-effort, like every report on a path already handling a
# failure.
if [ -n "$EXIT_TAKEN" ] && [ "$DEATH_CLASS" = "turn_exhaustion" ]; then
  TURN_NOTE=$(python3 .bureau-pipeline/scripts/dead_run.py turn-noted \
    --exit "$EXIT_TAKEN" --execution-file "$EXEC_FILE" --run-url "$RUN_URL") || TURN_NOTE=""
  if [ -n "$TURN_NOTE" ]; then
    python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" "$TURN_NOTE" || true
  fi
fi
