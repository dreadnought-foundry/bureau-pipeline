#!/usr/bin/env bash
# The proof run's result, reported to Linear: the record pull request, then
# the run's escalation file, sorted by what it names (DRE-6840).
#
#   1. A press — a `needs the CEO's press:` criterion the file quotes, or a
#      line of its own saying the CEO's press: the hold, the press's
#      question and the move to `Green Light`, as before.
#   2. Cause rows and nothing else — the three declared lines, the Question
#      `proof_cause_park.QUESTION` word for word, and `cause:` lines — with
#      the record pull request OPEN: handed to the one writer,
#      `proof_cause_park.py park`, which holds the card `In Review` for the
#      operator and asks the operator's question (DRE-6837). The step posts
#      no hold, no question and no move of its own, and its closing line
#      quotes the writer's result line. With no open record pull request —
#      PR_STATE empty (none found), UNREADABLE (GitHub's answer could not be
#      read) or MERGED (the record shipped) — the file is handed to nobody:
#      a warning, `proof park: cause rows with no open record pull request
#      for <CARD> — nothing held`, and the closing line `proof result: no
#      record` (with the no-record comment) or `proof result: record already
#      merged`. The card's clock is then the lane it stands in: Hand-work's
#      `hand-work-overdue` alarm and the dispatcher's first-run read, or,
#      where a pull request does exist, the dispatcher's own cause park.
#   3. Anything else — no press and no cause line, a Question of the run's
#      own beside cause rows, or prose of its own — is a decision for the
#      CEO: the press's hold, the question under the decision's own opening
#      (a park mark of `spoken_thread.PROOF_PARK_MARKS`) and the move to
#      `Green Light`.
set -e
RUN_URL="$BUREAU_SERVER_URL/$BUREAU_REPOSITORY/actions/runs/$BUREAU_RUN_ID"
# Exits 1 and 2: the one PR-existence predicate (DRE-2316). Exit 3
# from it is UNREADABLE, which is not "no PR" (DRE-2034).
PR_STATE=""
PR_URL=""
if PR_INFO=$(python3 .bureau-pipeline/scripts/card_pr.py find "$CARD" --branch "$RECORD_BRANCH"); then
  PR_STATE=$(printf '%s' "$PR_INFO" | cut -f1)
  PR_URL=$(printf '%s' "$PR_INFO" | cut -f2)
else
  PR_STATE="UNREADABLE"
fi
# Exit 3: the agent's note, read through check_agent_result.py's own
# predicate — present and non-empty — and nothing else: no branch,
# no PR, no execution record, so its only finding is the note.
ESCALATED=""
if python3 .bureau-pipeline/scripts/check_agent_result.py "" "" "" "" \
     --escalation-file "$ESCALATION_FILE" --ignore-is-error \
     | grep -qF "agent result gate: ok — an escalation note"; then
  ESCALATED="yes"
fi
TOOK=""
if [ "$PR_STATE" = "OPEN" ]; then
  TOOK="record pull request"
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "🤖 PR opened: $PR_URL — CI + critic review running. Run: $RUN_URL"
  # After a return the amended record went to the pull request that
  # was already open, and nothing else moves the card: qa-review.yml
  # moves it when a pull request OPENS, the sweep's hand-built move
  # once per card. On a first run the card has usually left
  # `Hand-work` already, and `advance` refuses the from-lane quietly.
  python3 .bureau-pipeline/scripts/linear_ops.py advance "$CARD" "In Review" "Hand-work" || true
elif [ "$PR_STATE" = "MERGED" ]; then
  TOOK="record already merged"
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "🤖 PR already merged: $PR_URL — this proof record shipped while the run was finishing; nothing moves from here. Run: $RUN_URL"
fi
PARK=""
if [ -n "$ESCALATED" ]; then
  # The sort, read once over the file (DRE-6840). A press is named by two
  # reads only: a `needs the CEO's press:` criterion the file's own lines
  # quote, or one of its own lines saying the CEO's press. A file with no
  # press is cause rows only when every line beside its `cause:` lines is
  # one of the three declared lines and its Question is the module's cause
  # question word for word; anything else is a decision. A sort that cannot
  # run parks as a decision, so the park is never lost to it.
  CAUSES=$(grep -E '^[[:space:]]*cause:' "$ESCALATION_FILE" | sed -E 's/^[[:space:]]+//' || true)
  if ! PARK=$(python3 - <<'PY'
import os, re, sys
press_re = re.compile(r"needs the CEO['’]s press:\s*", re.IGNORECASE)
said_re = re.compile(r"the CEO['’]s press", re.IGNORECASE)
asked = open(os.environ["ESCALATION_FILE"], encoding="utf-8").read()
lines = [l.strip() for l in asked.splitlines() if l.strip()]
rest = [l for l in lines if not l.startswith("cause:")]
presses = []
for line in os.environ.get("CARD_DESCRIPTION", "").splitlines():
    hit = press_re.search(line)
    if hit:
        presses.append(line[hit.end():].strip().rstrip(".").strip())
text = "\n".join(rest)
if (any(p and p.lower() in text.lower() for p in presses)
        or any(said_re.search(l) for l in rest)):
    print("press")
    sys.exit(0)
if len(rest) == len(lines):
    print("decision")
    sys.exit(0)
sys.path.insert(0, ".bureau-pipeline/scripts")
import console_escalation
import proof_cause_park
declared = (console_escalation.FINDING_PREFIX, console_escalation.QUESTION_PREFIX,
            console_escalation.RECOMMENDATION_PREFIX)
prefix = console_escalation.QUESTION_PREFIX
asks = [l[len(prefix):].strip() for l in rest if l.startswith(prefix)]
only = all(l.startswith(declared) for l in rest)
print("cause" if only and asks == [proof_cause_park.QUESTION] else "decision")
PY
  ); then
    echo "::warning::the escalation file could not be sorted; it parks as a decision"
    PARK="decision"
  fi
fi
if [ "$PARK" = "cause" ]; then
  if [ "$PR_STATE" = "OPEN" ]; then
    # The one writer holds the card and asks the operator, and moves no
    # card (DRE-6837): the card is `In Review`, where the move above put
    # it, and the writer reads the lane off the card. Its last line is
    # its result line, quoted below. Exit 2 is no cause row after all,
    # and the file is a decision; exit 1 posts nothing of the step's own,
    # because a press or decision park would put the operator's question
    # in the CEO's queue — the dispatcher's `_caused` finishes it.
    PARK_RC=0
    PARKED=$(python3 .bureau-pipeline/scripts/proof_cause_park.py park "$CARD" --escalation-file "$ESCALATION_FILE" --run-url "$RUN_URL") || PARK_RC=$?
    if [ -n "$PARKED" ]; then
      printf '%s\n' "$PARKED"
    fi
    if [ "$PARK_RC" = "0" ]; then
      TOOK="$TOOK, then $(printf '%s\n' "$PARKED" | tail -n 1)"
      echo "proof park: cause rows ($(printf '%s\n' "$CAUSES" | grep -c .)), held for the operator" >> "$GITHUB_STEP_SUMMARY"
    elif [ "$PARK_RC" = "2" ]; then
      PARK="decision"
    else
      echo "::warning::the cause park could not be completed for $CARD; nothing is posted and nothing moves"
      echo "proof park: the cause park could not be completed for $CARD — the dispatcher's next pass parks the record off its rows" >> "$GITHUB_STEP_SUMMARY"
    fi
  else
    echo "::warning::the cause rows were written with no open record pull request for $CARD; nothing is held — the dispatcher reads this run off the card"
    echo "proof park: cause rows with no open record pull request for $CARD — nothing held" >> "$GITHUB_STEP_SUMMARY"
  fi
fi
if [ "$PARK" = "press" ] || [ "$PARK" = "decision" ]; then
  TOOK="${TOOK:+$TOOK, then }the park in Green Light"
  # The press hold, for a decision too: the criterion in the card's words
  # and the press, read off the criterion line the planner opens with
  # `needs the CEO's press:` (DRE-5923) — the one the escalation names,
  # else the first. A card that marks none holds on the escalation's own
  # first line. The `the CEO's press:` phrase is what the dispatcher
  # matches to admit the return (DRE-5926).
  HOLD=$(python3 - <<'PY'
import os, re
press_re = re.compile(r"needs the CEO['’]s press:\s*", re.IGNORECASE)
asked = open(os.environ["ESCALATION_FILE"], encoding="utf-8").read()
found = []
for line in os.environ.get("CARD_DESCRIPTION", "").splitlines():
    hit = press_re.search(line)
    if not hit:
        continue
    press = line[hit.end():].strip().rstrip(".").strip()
    before = re.sub(r"^\s*(?:[-*+]|\d+[.)])?\s*(?:\[[ xX]\]\s*)?", "", line[:hit.start()])
    before = before.strip().rstrip("—–-:;,").strip()
    if press:
        found.append((before or f"needs the CEO's press: {press}", press))
named = [f for f in found if f[1].lower() in asked.lower()]
if named or found:
    criterion, press = (named or found)[0]
else:
    criterion = "the criterion the question below names"
    press = next((l.strip() for l in asked.splitlines() if l.strip()), "")
print(f"{criterion}\t{press}".replace("\n", " "))
PY
  )
  OBSERVED=$(printf '%s' "$HOLD" | cut -f1)
  PRESS=$(printf '%s' "$HOLD" | cut -f2-)
  NEEDS="the CEO's press: $PRESS"
  if ! python3 .bureau-pipeline/scripts/linear_ops.py proof-waiting "$CARD" "$OBSERVED" "$NEEDS"; then
    echo "::warning::the 🔬 proof-waiting hold could not be posted; the question still parks in Green Light"
    echo "proof park: the hold could not be posted for $CARD" >> "$GITHUB_STEP_SUMMARY"
  fi
  # Then the park: the question, posted before the move. The cause
  # lines, held in $CAUSES, are lifted out of the file first and set
  # under the three lines, so a file that declared its three lines still
  # reads them before its rows. This step is the file's last reader.
  if [ -n "$CAUSES" ]; then
    grep -vE '^[[:space:]]*cause:' "$ESCALATION_FILE" > "${RUNNER_TEMP:-/tmp}/proof-escalation-press.txt" || true
    cat "${RUNNER_TEMP:-/tmp}/proof-escalation-press.txt" > "$ESCALATION_FILE"
  fi
  {
    if [ "$PARK" = "press" ]; then
      echo "🙋 The proof run met a press only the CEO can make, and asks:"
    else
      echo "🙋 The proof run asks a decision only the CEO can make:"
    fi
    echo ""
    python3 .bureau-pipeline/scripts/console_escalation.py complete "$ESCALATION_FILE" --question "Make the press and say what you saw, or drop the criterion?" --who "the proof run" || cat "$ESCALATION_FILE"
    if [ -n "$CAUSES" ]; then
      echo ""
      printf '%s\n' "$CAUSES"
    fi
    echo ""
    echo "Run: $RUN_URL"
  } > "${RUNNER_TEMP:-/tmp}/proof-escalation-comment.md"
  echo "proof park: $PARK" >> "$GITHUB_STEP_SUMMARY"
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "$(cat "${RUNNER_TEMP:-/tmp}/proof-escalation-comment.md")"
  # Out of either lane: the record's pull request opened before the
  # escalation file was written, and qa-review.yml may already have
  # carried the card to `In Review`. Never Triage — the card is not
  # broken, it waits on his decision. A card in neither lane is
  # refused quietly, with the hold and the question already posted,
  # so the summary says it did not move.
  python3 .bureau-pipeline/scripts/linear_ops.py advance "$CARD" "Green Light" "Hand-work,In Review" \
    > "${RUNNER_TEMP:-/tmp}/proof-park-move.txt"
  cat "${RUNNER_TEMP:-/tmp}/proof-park-move.txt"
  if grep -qF "not advancing" "${RUNNER_TEMP:-/tmp}/proof-park-move.txt"; then
    echo "proof park: the hold and the question are posted, and the park did not move $CARD to Green Light" >> "$GITHUB_STEP_SUMMARY"
  fi
fi
if [ -z "$TOOK" ]; then
  TOOK="no record"
  UNREAD=""
  if [ "$PR_STATE" = "UNREADABLE" ]; then
    UNREAD=" (GitHub's answer on the record pull request could not be read, so one may exist.)"
  fi
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
    "🤖 proof run ended with no record — the dispatcher reads this run off the card and decides whether to dispatch again.$UNREAD Run: $RUN_URL"
fi
echo "proof result: $TOOK" | tee -a "$GITHUB_STEP_SUMMARY"
