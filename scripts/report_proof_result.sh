#!/usr/bin/env bash
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
if [ -n "$ESCALATED" ]; then
  TOOK="${TOOK:+$TOOK, then }the park in Green Light"
  # Two reasons to park (DRE-6613): a press only the CEO can make, and
  # rows the run cannot observe as worded — the file's `cause:` lines.
  # A file with cause lines that names no press (no `needs the CEO's
  # press:` criterion it names, no line of its own beyond the three
  # declared lines and the cause lines, none of them saying the CEO's
  # press) parks on its cause rows; anything else parks as a press,
  # its cause lines set under the question's three lines.
  CAUSES=$(grep -E '^[[:space:]]*cause:' "$ESCALATION_FILE" | sed -E 's/^[[:space:]]+//' || true)
  PARK="press"
  RECOMMENDATION=""
  if [ -n "$CAUSES" ] && PARK_INFO=$(python3 - <<'PY'
import os, re, sys
sys.path.insert(0, ".bureau-pipeline/scripts")
import console_escalation
press_re = re.compile(r"needs the CEO['’]s press:\s*", re.IGNORECASE)
said_re = re.compile(r"the CEO['’]s press", re.IGNORECASE)
asked = open(os.environ["ESCALATION_FILE"], encoding="utf-8").read()
rest = [l.strip() for l in asked.splitlines()
        if l.strip() and not l.strip().startswith("cause:")]
declared = (console_escalation.FINDING_PREFIX, console_escalation.QUESTION_PREFIX,
            console_escalation.RECOMMENDATION_PREFIX)
presses = []
for line in os.environ.get("CARD_DESCRIPTION", "").splitlines():
    hit = press_re.search(line)
    if hit:
        presses.append(line[hit.end():].strip().rstrip(".").strip())
text = "\n".join(rest)
if (any(p and p.lower() in text.lower() for p in presses)
        or any(said_re.search(l) or not l.startswith(declared) for l in rest)):
    print("press")
    sys.exit(0)
# The file's own Recommendation, read the way the press path's
# `complete` reads it: `none given` recommends nothing.
said = console_escalation.parse(text)
value = ""
if said is not None and said.recommendation is not None:
    value = next(l for l in rest if l.startswith(declared[2]))[len(declared[2]):].strip()
print(f"cause\t{value}".replace("\n", " "))
PY
  ); then
    PARK=$(printf '%s' "$PARK_INFO" | cut -f1)
    RECOMMENDATION=$(printf '%s' "$PARK_INFO" | cut -s -f2-)
  fi
  CAUSE_PARKED=""
  if [ "$PARK" = "cause" ]; then
    # The hold's two halves and the question are proof_cause_park.py's
    # (DRE-6612); the step composes neither. A file whose cause lines
    # the module refuses parks the way every escalation parked before.
    REC_ARGS=()
    if [ -n "$RECOMMENDATION" ]; then
      REC_ARGS=(--recommendation "$RECOMMENDATION")
    fi
    if CAUSE_HOLD=$(python3 .bureau-pipeline/scripts/proof_cause_park.py hold --escalation-file "$ESCALATION_FILE") \
       && python3 .bureau-pipeline/scripts/proof_cause_park.py question --escalation-file "$ESCALATION_FILE" --run-url "$RUN_URL" "${REC_ARGS[@]}" > "${RUNNER_TEMP:-/tmp}/proof-escalation-comment.md"; then
      CAUSE_PARKED="yes"
    else
      echo "::warning::the cause rows could not be read; the escalation parks as the file reads"
      echo "proof park: the cause rows could not be read for $CARD" >> "$GITHUB_STEP_SUMMARY"
    fi
  fi
  # The hold first, one for either reason.
  if [ -n "$CAUSE_PARKED" ]; then
    OBSERVED=$(printf '%s' "$CAUSE_HOLD" | cut -f1)
    NEEDS=$(printf '%s' "$CAUSE_HOLD" | cut -f2-)
  else
    # The press hold: the criterion in the card's words and the press,
    # read off the criterion line the planner opens with `needs the
    # CEO's press:` (DRE-5923) — the one the escalation names, else
    # the first. A card that marks none holds on the escalation's own
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
  fi
  if ! python3 .bureau-pipeline/scripts/linear_ops.py proof-waiting "$CARD" "$OBSERVED" "$NEEDS"; then
    echo "::warning::the 🔬 proof-waiting hold could not be posted; the question still parks in Green Light"
    echo "proof park: the hold could not be posted for $CARD" >> "$GITHUB_STEP_SUMMARY"
  fi
  # Then the park: the question, posted before the move — for cause
  # rows the module's, already written above.
  if [ -n "$CAUSE_PARKED" ]; then
    echo "proof park: cause rows ($(printf '%s\n' "$CAUSES" | grep -c .))" >> "$GITHUB_STEP_SUMMARY"
  else
    # The cause lines, held in $CAUSES, are lifted out of the file
    # first and set under the three lines, so a file that declared its
    # three lines still reads them before its rows. This step is the
    # file's last reader.
    if [ -n "$CAUSES" ]; then
      grep -vE '^[[:space:]]*cause:' "$ESCALATION_FILE" > "${RUNNER_TEMP:-/tmp}/proof-escalation-press.txt" || true
      cat "${RUNNER_TEMP:-/tmp}/proof-escalation-press.txt" > "$ESCALATION_FILE"
    fi
    {
      echo "🙋 The proof run met a press only the CEO can make, and asks:"
      echo ""
      python3 .bureau-pipeline/scripts/console_escalation.py complete "$ESCALATION_FILE" --question "Make the press and say what you saw, or drop the criterion?" --who "the proof run" || cat "$ESCALATION_FILE"
      if [ -n "$CAUSES" ]; then
        echo ""
        printf '%s\n' "$CAUSES"
      fi
      echo ""
      echo "Run: $RUN_URL"
    } > "${RUNNER_TEMP:-/tmp}/proof-escalation-comment.md"
    if [ "$PARK" = "press" ]; then
      echo "proof park: press" >> "$GITHUB_STEP_SUMMARY"
    fi
  fi
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
