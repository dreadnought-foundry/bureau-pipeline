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
import pipeline_act  # noqa: E402

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


def decide(
    *,
    parked_because: str = "",
    execution: dict | None = None,
    turn_receipt: str = "",
) -> Decision:
    """Retry this failed run, or decline and say why.

    The park is read FIRST. Both rules decline, so the order changes only which
    one the receipt names — and when a card has been parked, "a person owns
    this card now" is the fact the next reader needs, with the turn cap as the
    reason it was parked rather than a finding of its own.
    """
    if parked_because:
        return Decision(
            DECLINE,
            RULE_PARKED,
            f"the card is parked for a human: {parked_because}",
        )
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
        f"🩺 {DECLINED_TAG}: not retried — {decision.detail}. The medic re-runs "
        f"a failed run once, for a transient infrastructure flake; this failure "
        f"is not one, so no second build was started and nothing was charged to "
        f"this card. Rule applied: {decision.rule}.{run_suffix}"
    )


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


def card_facts(identifier: str) -> dict:
    """`{"state", "labels", "comments"}` for a card — one read, both rules.

    Imported locally so the decision core above stays importable (and testable)
    with no Linear key and no network.
    """
    import linear_ops  # local: only this function needs the Linear seam

    data = linear_ops.gql(
        """query($id: String!) { issue(id: $id) {
             state { name } labels { nodes { name } }
             comments(last: 50) { nodes { body createdAt } } } }""",
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
            for node in (data.get("comments") or {}).get("nodes") or []
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
    card = card_from_branch(args.branch)
    parked, witness = "", ""
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
    decision = decide(
        parked_because=parked,
        execution=execution_from_log(_read(args.log)),
        turn_receipt=witness,
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
