#!/usr/bin/env python3
"""Should the medic retry this failed run? (DRE-2954, stdlib only.)

`medic.yml` re-runs a failed run once, for a transient infrastructure flake.
`dead_run.py` counts a card's deaths and, when a cap is spent, PARKS the card
for a human. Both are right on their own, and until this module nothing joined
them — so on 2026-09-01 they contradicted each other inside a minute:

    16:36  DRE-2937's second turn-cap death spends the `turn-exhaustion-requeue`
           cap. The card is parked in Backlog with `needs-human`: "this card
           does not fit inside one run; split it".
    16:37  the medic re-runs the FIRST dead run as attempt 2. The card returns
           to In Progress and a third ~$16 build starts on a card the pipeline
           itself declared unbuildable thirty seconds earlier.
    16:48  an operator kills it by hand.

The park says stop; the retry says go; the retry won. Two rules close it, and
this module is where the medic asks them:

  1. **THE CARD IS PARKED.** A card carrying `needs-human`, or sitting in
     Backlog with a `held-for-human` receipt NEWER than the run, is a card a
     person now owns. Nothing may dispatch at it — that is already the rule the
     reconcile sweep and `agent-fix.yml` both obey, and the medic was the one
     door left open.
  2. **THE DEATH WAS TURN EXHAUSTION.** A turn-cap death is deterministic on
     the card's SIZE. Re-running the same run with the same parameters cannot
     succeed, so it is not a transient failure at all: it belongs to the
     dead-run cap's path (which already classifies it — DRE-2312/DRE-2931) and
     nowhere else. Exactly the rule DRE-1921 wrote for a rate-limited critic —
     do not retry into the same wall.

  3. **THE MACHINE RAN OUT OF MEMORY** (DRE-4847). A run the kernel killed at
     the runner's memory limit — `##[error]Process completed with exit code
     137.`, read by `out_of_memory.from_log` off the same log — is refused a
     rerun on any machine that cannot be made bigger: a `heavy`, `hosted` or
     `unknown` runner, or the SECOND kill on a `light` one. A light runner's
     FIRST kill keeps the ordinary retry, because 2 GB can be one turn short
     on one card and ample on the next. The same wall, a fourth time.

Everything else keeps the retry it has always had. An infra error, a run that
died before the agent (`num_turns: 0`), a run with no execution record at all:
those are what the one retry is FOR, and this module must not take it away.

## Where each fact comes from

The two facts have different owners, and neither is inferred from the other
(`standards/console-honesty.md` rule 1):

  * **the park** is read from the CARD — its state, its labels, and the receipt
    `dead_run.decide()` wrote when the cap was spent;
  * **the death class** is read from the RUN. The medic holds no execution
    file, but it already fetches the failed run's log, and the agent-result
    gate prints that record into it (`execution_result.print_failure_detail`).
    `execution_from_log()` reconstructs it from there and hands it to
    `check_agent_result` — the shared predicate — rather than re-deriving an
    `is_error` test here. Re-deriving it is precisely how DRE-2695's
    turn-exhausted build run came to be reported as a model death.

A second witness for the death class comes free with the park read: the card's
own `turn-exhaustion-requeue` receipt from this run. It exists because
`gh run view --log-failed` can come back EMPTY — a 403, an outage — and an
empty log has always classified as `normal`, which means retry.

## The direction each unknown fails

  * **the card cannot be read** → RETRY. Fail-open on purpose. A retry into a
    dead Linear costs a cheap pre-agent death (DRE-2931 charges the card
    nothing for it), whereas a Linear blip that disabled every retry in the
    fleet would be a new stall of exactly the kind this pipeline exists to end.
    Rule 2 still holds without Linear, and rule 2 is the mechanism DRE-2937
    actually died of.
  * **the run carries no card** (the sweep, the test suite, the release gate)
    → RETRY, unchanged. There is no park to honour.

CLI:

    python3 medic_retry.py decide --branch <head-ref> --log <file> \
        [--run-started-at <iso>]
    python3 medic_retry.py post --card <DRE-N> --rule <rule> --detail <text> \
        [--run-url <url>]

`decide` prints four `key=value` lines for `$GITHUB_OUTPUT` — `retry`, `rule`,
`card`, `detail` — one line each, and exits 0 whatever it decides. `post`
composes the refusal through `pipeline_act.receipt()` and writes it to the
card: the body lives here, so the act composes here too, exactly as
`reconcile.py`'s ten sites do.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_agent_result  # noqa: E402
import dead_run  # noqa: E402
import execution_result  # noqa: E402
import out_of_memory  # noqa: E402
import pipeline_act  # noqa: E402
import stream_watchdog  # noqa: E402

# The act this decision is announced as, and its idempotency key. The tag is
# the key every reader counts on (`tag in body`), so it lives here — in the
# module that composes the body — and is declared in config/pipeline-acts.json.
DECLINED_ACT = "retry-declined"
DECLINED_TAG = "medic-retry-declined"

# What decide() answers.
RETRY = "retry"
DECLINE = "decline"

# WHICH rule was applied, named on the receipt so the next operator reading the
# run knows it was a decision rather than a miss.
RULE_NONE = "none"
RULE_PARKED = "card-parked"
RULE_TURN_EXHAUSTION = "turn-exhaustion"
# DRE-3991. The THIRD rule: this run went silent and was stopped, and so did
# the last one on this card. The first stall keeps its retry — a stall really
# can be a passing blip in the network path — but a second in a row is not a
# blip, and a third build would be DRE-1921's loop wearing a new name.
RULE_STALLED_REPEAT = "stalled-no-stream-repeat"
# DRE-4847. The FOURTH rule: the machine ran out of memory. A light runner's
# first kill keeps the ordinary retry — 2 GB can be one turn short on one card
# and fine on the next — but a heavy, GitHub-hosted or unrecognized machine is
# already as big as it gets here, and the second kill on a light one has spent
# the retry. Re-running the same work on the same size of machine dies the same
# way; it needs a bigger machine or a smaller card, and neither is a rerun.
RULE_OUT_OF_MEMORY = "out-of-memory"

# The park receipt's own marker — `dead_run.decide()`'s hold sentence, which
# both caps reach ("🚨 held-for-human (dead-run-requeue cap reached)" and
# "… (turn-exhaustion-requeue cap reached)"). Deliberately NOT the unlanded
# receipt's wording: `park_unlanded_comment()` says the park did not land, and
# a card that is not in Backlog and carries no label is not parked.
# tests/test_medic_retry_honours_the_park.py pins this against a real
# `dead_run.decide()` hold, so a reword there fails at the diff.
HELD_RECEIPT_MARK = "held-for-human ("

# The DRE-N a head ref carries. Same shape `reconcile.branch_card` reads and the
# same shape medic.yml's own back-off step greps for; a branch with no card
# (repair/*, main, a scheduled sweep's ref) has no park to consult.
_BRANCH_CARD = re.compile(r"DRE-[0-9]+", re.I)

# A GitHub Actions log line is `job\tstep\t<ISO timestamp> <content>`. Strip the
# prefix so the gate's own indented `  field: value` lines can be matched; a
# plain log with no prefix passes through untouched.
_LOG_PREFIX = re.compile(r"^.*?\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?")

# `  subtype: error_max_turns` — one whitelisted field of the result record as
# execution_result prints it. Lower-case identifier only, so the header line
# and ordinary prose cannot be read as a field.
_RESULT_FIELD = re.compile(r"^\s{1,4}(?P<field>[a-z][a-z0-9_]*):\s*(?P<value>.*\S)\s*$")


class Decision:
    """Whether to retry, which rule decided it, and the one line that says so.

    `detail` is a SINGLE line: it crosses a `$GITHUB_OUTPUT` boundary into the
    job that posts the receipt, and a newline there would write a stray key.
    """

    def __init__(self, action: str, rule: str, detail: str = ""):
        self.action = action
        self.rule = rule
        self.detail = " ".join((detail or "").split())

    @property
    def retry(self) -> bool:
        return self.action == RETRY

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Decision)
            and self.action == other.action
            and self.rule == other.rule
            and self.detail == other.detail
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Decision({self.action!r}, {self.rule!r}, {self.detail!r})"


# --------------------------------------------------------------------------- #
# the decision (no I/O)                                                        #
# --------------------------------------------------------------------------- #


def is_turn_exhaustion(execution: dict | None) -> bool:
    """Did this run die by running out of turns?

    A thin pass-through to the shared classifier, on purpose. There is exactly
    one `is_error` test in this codebase (`check_agent_result.classify_death`)
    and every caller goes through it; a local re-derivation here is how the two
    would quietly stop agreeing about what a turn-cap death looks like.
    """
    return check_agent_result.is_turn_exhaustion(execution)


#: Why a rerun cannot help THIS class of machine — the second half of the
#: refusal sentence, one per class that never gets the retry. `light` has no
#: row because a light runner's first kill is the one case that keeps it.
_NO_BIGGER_MACHINE = {
    out_of_memory.HEAVY: (
        "a heavy runner is already the biggest machine this pipeline asks "
        "for, so the same work meets the same limit"
    ),
    out_of_memory.HOSTED: (
        "a GitHub-hosted runner's size is not ours to raise, so the same work "
        "meets the same limit"
    ),
    out_of_memory.UNKNOWN: (
        "the run's log never named the machine, so there is no reason to "
        "believe a rerun lands on a bigger one"
    ),
}


def _attempt(value) -> int:
    """Which attempt of the failed run this is; 1 when it cannot be read.

    Unreadable reads as the FIRST attempt on purpose: the rule below would
    otherwise refuse a light runner's one retry on a missing workflow input,
    and `medic.yml`'s `retry` job is gated to attempt 1 anyway, so an
    unreadable attempt can never spend a second build.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 1


def out_of_memory_refusal(kill, *, attempt) -> str:
    """Why this out-of-memory kill gets no retry, or "" when it keeps its one.

    The kill is read from the RUN (`out_of_memory.from_log`, off the log the
    medic already fetched) and the rule is about the MACHINE: only a light
    runner's FIRST kill keeps the ordinary retry, because 2 GB can be one turn
    short on one card and ample on the next. A heavy, GitHub-hosted or
    unrecognized runner is already as big as this pipeline can ask for, and a
    second kill on a light one has spent the retry — in both cases the same
    work on the same size of machine dies the same way, which is DRE-1921's
    rule ("do not retry into the same wall") wearing a fourth name.
    """
    if kill is None:
        return ""
    number = _attempt(attempt)
    said = out_of_memory.describe(kill)
    if kill.runner_class == out_of_memory.LIGHT:
        if number <= 1:
            return ""
        return (
            f"{said}, and this is attempt {number}: the one retry a light "
            f"runner's kill is entitled to is already spent"
        )
    return f"{said}, and {_NO_BIGGER_MACHINE[kill.runner_class]}"


def decide(
    *,
    parked_because: str = "",
    execution: dict | None = None,
    turn_receipt: str = "",
    repeat_stall: str = "",
    out_of_memory: str = "",
) -> Decision:
    """Retry this failed run, or decline and say why.

    The park is read FIRST. Every rule declines, so the order changes only
    which one the receipt names — and when a card has been parked, "a person
    owns this card now" is the fact the next reader needs, with the turn cap
    or the stall as the reason it was parked rather than a finding of its own.
    """
    if parked_because:
        return Decision(
            DECLINE,
            RULE_PARKED,
            f"the card is parked for a human: {parked_because}",
        )
    if repeat_stall:
        return Decision(DECLINE, RULE_STALLED_REPEAT, repeat_stall)
    # `out_of_memory` here is the REFUSAL SENTENCE (empty when the kill still
    # has its retry), not the module of that name — `out_of_memory_refusal`
    # below is what reads the log and decides which it is.
    if out_of_memory:
        return Decision(DECLINE, RULE_OUT_OF_MEMORY, out_of_memory)
    if is_turn_exhaustion(execution):
        facts = check_agent_result.turn_exhaustion_facts(execution)
        return Decision(
            DECLINE,
            RULE_TURN_EXHAUSTION,
            f"this run ran out of steps: it hit {facts}, which is a budget "
            f"ceiling on the card's size and not a flake — the same run "
            f"re-run hits the same wall",
        )
    if turn_receipt:
        return Decision(
            DECLINE,
            RULE_TURN_EXHAUSTION,
            f"this run ran out of steps: the card's own "
            f"'{dead_run.TURN_TAG}' receipt from this run says so, and a "
            f"budget ceiling is not a flake to retry into",
        )
    return Decision(RETRY, RULE_NONE)


def declined_comment(decision: Decision, run_url: str = "") -> str:
    """The receipt a declined retry posts on the card.

    Raises on a RETRY decision rather than composing an empty refusal: a
    receipt announcing a refusal that did not happen is worse than none, and it
    would burn this act's idempotency key against a run that was retried.
    """
    if decision.retry:
        raise ValueError(
            "a retry is not an act — declined_comment() composes the refusal, "
            "and there is nothing to refuse when the run was retried"
        )
    run_suffix = f" Run: {run_url}" if run_url else ""
    return (
        f"🩺 {DECLINED_TAG}: not retried — {decision.detail}. "
        f"{_WHY_NOT.get(decision.rule, _WHY_NOT_DEFAULT)} "
        f"Rule applied: {decision.rule}.{run_suffix}"
    )


#: Why no retry was issued, per rule. The default is the sentence this receipt
#: has always carried, byte for byte; a rule for which it would be WRONG gets
#: its own, because a receipt that misdescribes the decision is worse than a
#: terse one. A repeat stall is not "not a transient flake" — it is a
#: transient-looking failure that already SPENT its one retry, and the next
#: reader needs to know a person is now the only thing that moves it.
_WHY_NOT_DEFAULT = (
    "The medic re-runs a failed run once, for a transient infrastructure "
    "flake; this failure is not one, so no second build was started and "
    "nothing was charged to this card."
)
_WHY_NOT = {
    RULE_STALLED_REPEAT: (
        "The medic re-runs a stalled run once, and it already did: this is "
        "the second run in a row on this card that started up and then went "
        "quiet. Two in a row is not a passing blip, so no third build was "
        "started and nothing more was charged to this card. Nothing is wrong "
        "with the work — the agent never got as far as reading the code — "
        "and nothing else is coming until someone looks at why the runs are "
        "going silent."
    ),
    RULE_OUT_OF_MEMORY: (
        "The machine this ran on hit its memory limit, so the system killed "
        "the job — it is not a fault in the work and nothing was rejected. "
        "Running it again on the same size of machine dies the same way, so "
        "no second build was started and nothing more was charged to this "
        "card. What moves it is a bigger machine or a smaller card, and "
        "neither of those is a retry."
    ),
}


# --------------------------------------------------------------------------- #
# reading the card's receipts                                                  #
# --------------------------------------------------------------------------- #


def _moment(value: str):
    """An ISO timestamp as a comparable instant, or None when it is not one."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _newest_after(receipts, needle: str, run_started_at: str) -> dict | None:
    """The newest receipt carrying `needle` that was posted after the run began.

    "After the run began" is what makes the receipt THIS run's: a park from a
    previous life of the card (since un-parked, the label cleared, the card back
    in Todo) says nothing about the failure in hand, and reading it as one would
    freeze every later retry. An unreadable timestamp on either side is treated
    as in range — the ordering is unknown, and the fact the receipt exists at
    all is the stronger signal.
    """
    started = _moment(run_started_at)
    found = None
    for receipt in receipts or ():
        if needle not in (receipt.get("body") or ""):
            continue
        posted = _moment(receipt.get("created_at") or "")
        if started is not None and posted is not None and posted < started:
            continue
        found = receipt
    return found


def park_reason(
    *,
    state: str = "",
    labels=(),
    receipts=(),
    run_started_at: str = "",
) -> str:
    """Why this card is parked for a human, or "" when it is not.

    Two signals, in the order the card's own evidence gets weaker:

      1. the `needs-human` label — the native "a person owns this now" marker
         every sweep already reads, and enough on its own, whatever lane the
         card is in (`dead_run.park()` writes the label first for exactly this
         reason: it is what stops the pipeline re-dispatching);
      2. the park LANE plus the hold receipt that put the card there, newer
         than the run. The receipt is required because Backlog is not by itself
         a hold — it is also where a blocked card and a PARKED routing verdict
         sit, and neither of those is this run's business.
    """
    hold = dead_run.HOLD_LABEL.lower()
    if any((name or "").strip().lower() == hold for name in labels or ()):
        return f"the '{dead_run.HOLD_LABEL}' label is on it"
    if (state or "").strip().lower() != dead_run.PARK_STATE.lower():
        return ""
    held = _newest_after(receipts, HELD_RECEIPT_MARK, run_started_at)
    if not held:
        return ""
    when = (held.get("created_at") or "").strip()
    at = f" at {when}" if when else ""
    return (
        f"it was moved to {dead_run.PARK_STATE}{at} by the pipeline's own "
        f"hold, after this run started"
    )


def repeat_stall(stall, receipt_bodies, *, run_id: str, attempt: str) -> str:
    """Why this stall is the SECOND one in a row on this card, or "".

    Two facts, from the two places that hold them and neither inferred from
    the other (`standards/console-honesty.md` rule 1): the RUN says it went
    silent (`stream_watchdog.stall_from_log`, off the log the medic already
    fetched), and the CARD says the last one did too (`prior_stalls`, off its
    own records). A run that did not stall is never a repeat, whatever the
    card carries, and a card with no earlier record keeps its one retry.

    The exclusion of this run-attempt lives in `prior_stalls` and matters:
    the card carries THIS stall's record within seconds of the classification,
    so counting it would have the first stall refuse its own retry.
    """
    if stall is None:
        return ""
    earlier = stream_watchdog.prior_stalls(
        receipt_bodies, run_id=run_id, attempt=attempt
    )
    if not earlier:
        return ""
    return (
        f"this run went silent too: “{stall.step}” emitted nothing for "
        f"{stall.silence_seconds} seconds after it started up, and this card "
        f"already carries {len(earlier)} stall record(s) from an earlier run"
    )


def turn_receipt(receipts, *, run_started_at: str = "") -> str:
    """This run's own turn-exhaustion receipt on the card, or "".

    The second witness for rule 2, and the one that survives an unreadable run
    log: whichever way the turn cap ended — a requeue or a hold — the receipt
    carries `dead_run.TURN_TAG`, and it was written by the run that just
    failed.
    """
    found = _newest_after(receipts, dead_run.TURN_TAG, run_started_at)
    return (found or {}).get("body") or ""


# --------------------------------------------------------------------------- #
# reading the failed run's own execution record out of its log                 #
# --------------------------------------------------------------------------- #


def execution_from_log(log_text: str) -> dict | None:
    """The failed run's execution record, as far as its log reports it.

    The agent-result gate prints the record's whitelisted fields under
    `execution_result.FAILURE_HEADER` whenever the run died, so the block is
    the run's OWN account of its death rather than an inference from the shape
    of the log. Only lines inside that block are read: the completion printer
    writes a `subtype:` line too, under a different header, for a run that did
    NOT die — and reading one as the other would report a healthy run as a
    death.

    None when the log carries no such block, which is what a pre-agent failure,
    an infra flake and an unreadable log all leave behind. Every one of those
    keeps its retry.
    """
    record: dict = {}
    collecting = False
    for raw in (log_text or "").splitlines():
        line = _LOG_PREFIX.sub("", raw, count=1)
        if execution_result.FAILURE_HEADER in line:
            collecting = True
            record = {}  # a rerun in the same log: the LAST block is this run's
            continue
        if not collecting:
            continue
        match = _RESULT_FIELD.match(line)
        if not match:
            collecting = False
            continue
        record[match.group("field")] = match.group("value")
    if not record:
        return None
    # The header is printed only for a death, so the record is one by
    # construction — `is_error` is the block's meaning, not a field it carries.
    record["is_error"] = True
    return record


# --------------------------------------------------------------------------- #
# the Linear seam                                                              #
# --------------------------------------------------------------------------- #


def card_from_branch(branch: str) -> str | None:
    """The DRE-N a head ref carries (upper-cased), or None."""
    match = _BRANCH_CARD.search(branch or "")
    return match.group(0).upper() if match else None


# The card a failed PLANNER run was working on (DRE-3223). A planner runs on
# the default branch — there is no card branch yet — so the head branch names
# nothing, and on 2026-09-05 every planner limit death (DRE-3162, 3130, 3072,
# 3169, 3168 ×2) had the medic's gate and no card to mark. plan.yml now says
# which card it is running for in two places the failed log carries: its job
# NAME, which `gh run view --log-failed` prints as the first field of every
# line (`call / bureau-card: DRE-3162<TAB>…` — survives a gh that prints only
# failed steps), and one echoed line at the top of the job (`…Z bureau-card:
# DRE-3162`). Matched ONLY in those structural positions — the job-name
# field, or the text right after the timestamp — or at the start of a bare
# line, never as a substring: a planner log quotes card bodies, and
# DRE-2923's lesson is that quoted prose must not classify. The echoed
# SCRIPT line (`\x1b[36;1mecho "bureau-card: …"`) has an escape code before
# the words and does not match either.
_LOG_CARD = re.compile(
    r"(?m)^(?:[^\t\n]*/ |[^\t\n]*\t[^\t\n]*\t\S+ )?bureau-card: (DRE-[0-9]+)\b",
    re.I,
)


def card_from_log(log_text: str) -> str | None:
    """The card a run's own log names (`bureau-card: DRE-n`), upper-cased, or None."""
    match = _LOG_CARD.search(log_text or "")
    return match.group(1).upper() if match else None


def card_for_run(branch: str, log_text: str = "") -> str | None:
    """THE card resolution for a failed run — one function, two sources.

    The head branch first, exactly as before (`agent/DRE-n-…` is the card for
    every build, fix, review and sync run); then the run's own log, for a
    planner run whose head is the default branch. The branch wins when both
    speak: a build run's log can quote any card, its branch names one.
    """
    return card_from_branch(branch) or card_from_log(log_text)


def card_facts(identifier: str) -> dict:
    """`{"state", "labels", "comments"}` for a card — one read, both rules.

    The comment window is `linear_ops.COMMENT_WINDOW_GQL` and the order is
    `linear_ops.window_nodes`'s: the card's fifty NEWEST comments, oldest→
    newest (DRE-3250). Both rules below read the NEWEST receipt carrying a
    marker — this run's park, this run's turn-cap witness — so a window taken
    from the other end of a busy card's thread would answer with a park from a
    previous life of the card, or with nothing at all, and the medic would
    retry a card the pipeline had just parked.

    Imported locally so the decision core above stays importable (and testable)
    with no Linear key and no network.
    """
    import linear_ops  # local: only this function needs the Linear seam

    data = linear_ops.gql(
        """query($id: String!) { issue(id: $id) {
             state { name } labels { nodes { name } }
             %s } }""" % linear_ops.COMMENT_WINDOW_GQL,
        {"id": identifier},
    )["issue"] or {}
    return {
        "state": (data.get("state") or {}).get("name") or "",
        "labels": [
            (label.get("name") or "")
            for label in (data.get("labels") or {}).get("nodes") or []
        ],
        "comments": [
            {"body": node.get("body") or "", "created_at": node.get("createdAt") or ""}
            for node in linear_ops.window_nodes(data.get("comments"))
        ],
    }


def post_declined(identifier: str, decision: Decision, run_url: str = "") -> None:
    """Write the refusal to the card, composed through the one receipt writer.

    The trailer is added HERE rather than at the CLI's `--act=` seam because
    this module owns the body: an act whose wording and whose composition live
    in one place is the shape every `reconcile.py` site has, and it is what
    lets `tests/test_act_emission.py` drive the real emission instead of
    proving only that a shell flag was spelled correctly.
    """
    import linear_ops  # local: only this function needs the Linear seam

    # The act name is a LITERAL here rather than `DECLINED_ACT`: the emission
    # guard reads this call statically (`check_act_receipts._receipt_act`) and
    # a constant reads back as "<computed>" — which proves the call is wrapped
    # but not WHICH act it wraps, so the registry's other direction (every
    # declared act is composed somewhere the guard can see) goes blind. Every
    # `reconcile.py` site spells it the same way. The two are pinned equal in
    # tests/test_medic_retry_honours_the_park.py.
    body = pipeline_act.receipt("retry-declined", declined_comment(decision, run_url))
    linear_ops.cmd_comment(identifier, body)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _decide_cli(args) -> int:
    log_text = _read(args.log)
    card = card_for_run(args.branch, log_text)
    stall = stream_watchdog.stall_from_log(log_text)
    parked, witness, repeat = "", "", ""
    if card:
        try:
            facts = card_facts(card)
        except Exception as e:  # noqa: BLE001 — any Linear/transport failure
            # Fail OPEN: see the module docstring. Loud, because a retry taken
            # without the park read is the one this card exists to prevent.
            print(
                f"::warning::medic retry gate: could not read {card} ({e}) — "
                f"deciding on the run's own log alone.",
                file=sys.stderr,
            )
        else:
            parked = park_reason(
                state=facts["state"],
                labels=facts["labels"],
                receipts=facts["comments"],
                run_started_at=args.run_started_at,
            )
            witness = turn_receipt(
                facts["comments"], run_started_at=args.run_started_at
            )
            repeat = repeat_stall(
                stall,
                [receipt.get("body") or "" for receipt in facts["comments"]],
                run_id=args.run_id,
                attempt=args.run_attempt,
            )
    decision = decide(
        parked_because=parked,
        execution=execution_from_log(log_text),
        turn_receipt=witness,
        repeat_stall=repeat,
        # DRE-4847: read off the SAME log, no second fetch and no card read —
        # the machine and its class are in the run's own set-up lines.
        out_of_memory=out_of_memory_refusal(
            out_of_memory.from_log(log_text), attempt=args.run_attempt
        ),
    )
    print(f"retry={'true' if decision.retry else 'false'}")
    print(f"rule={decision.rule}")
    print(f"card={card or ''}")
    print(f"detail={decision.detail}")
    if not decision.retry:
        print(
            f"medic retry gate: NOT retrying — {decision.detail} "
            f"(rule: {decision.rule}).",
            file=sys.stderr,
        )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")

    gate = sub.add_parser("decide")
    gate.add_argument("--branch", default="")
    gate.add_argument("--log", default="")
    gate.add_argument("--run-started-at", default="")
    # DRE-3991. Which run-attempt is asking — the one thing that tells this
    # run's own stall record from the last one's. `gh run rerun --failed`
    # reuses the run id, so the attempt is half of the answer, not a detail.
    gate.add_argument("--run-id", default="")
    gate.add_argument("--run-attempt", default="")

    note = sub.add_parser("post")
    note.add_argument("--card", required=True)
    note.add_argument("--rule", required=True)
    note.add_argument("--detail", required=True)
    note.add_argument("--run-url", default="")

    args = parser.parse_args(argv)
    if args.command == "decide":
        return _decide_cli(args)
    if args.command == "post":
        decision = Decision(DECLINE, args.rule, args.detail)
        print(declined_comment(decision, run_url=args.run_url))
        post_declined(args.card, decision, run_url=args.run_url)
        return 0
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
