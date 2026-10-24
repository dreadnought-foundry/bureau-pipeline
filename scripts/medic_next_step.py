#!/usr/bin/env python3
"""Every medic diagnosis class has one declared next step (DRE-7792).

The diagnosis agent in medic.yml writes one word, `resume` or `hold`
(DRE-7526), and its prompt asks it to tell apart four categories that nothing
downstream reads. The weekly stop report (agent-bureau, DRE-6691) reads every
diagnosis into five classes — the week ending 2026-10-17 read 201 diagnoses as
bad-code 178, platform-fault 18, transient-infrastructure 3, bad-card 2. This
module makes those five classes the medic's own vocabulary, and the next step
each one owes is data: `config/medic-next-steps.json`, one record per class,
each with its `step`, a plain-English `means`, and an optional `fallback` with
the one condition (`fallback_when`) under which it applies.

    class                     step              fallback, and when
    transient-infrastructure  requeue-once      ask-person, resume-spent
    bad-card                  send-to-planning  ask-person, planning-spent
    bad-code                  fix-path          ask-person, no-open-pr
    platform-fault            ask-person        none
    unreadable                ask-person        none

`bad-card`'s bound is what stops a card going round Planning forever: the
second time a card the medic already sent back is diagnosed `bad-card`, it is
a question for a person. `planning_spent()` reads that off the card's own
thread, from the medic's receipt.

This card changes no workflow. It is the contract the medic cards read
(DRE-7793, DRE-7799, DRE-7794, DRE-7805), which is why it ships first and
alone. The five class strings are agent-bureau's `weekly_stops.CONCLUSIONS`
too, so a change to `CLASSES` is a change in two repos.

The pipeline modules this reads — `limit_recovery`, `lane_contract`,
`planning_escalation` and `console_escalation` — are each imported inside the
one function that needs it, so `read`, `decide`, `finding` and `check` import
nothing beyond the standard library, and "what closes a limit marker", "which
lanes a build sits in" and "what is fit to show the CEO" each keep one reader.

    python3 scripts/medic_next_step.py read --file .bureau-pipeline/medic-verdict.txt
    python3 scripts/medic_next_step.py decide --class bad-code --kind card \\
        [--resume-spent] [--has-open-pr] [--planning-spent]
    python3 scripts/medic_next_step.py receipt --class bad-card --step send-to-planning \\
        --run-url <failed run url> --report .bureau-pipeline/medic-report.md \\
        [--finding "…"] [--reason "…"] [--moved-no --lane <lane>]
    python3 scripts/medic_next_step.py finding --report .bureau-pipeline/medic-report.md
    python3 scripts/medic_next_step.py from-lanes
    python3 scripts/medic_next_step.py waiting --comments-file <path> [--run-id <id>]
    python3 scripts/medic_next_step.py planning-spent --comments-file <path>
    python3 scripts/medic_next_step.py question --file .bureau-pipeline/medic-question.json \\
        --report .bureau-pipeline/medic-report.md
    python3 scripts/medic_next_step.py check [--file <path>]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
CONFIG_PATH = os.path.join(ROOT, "config", "medic-next-steps.json")

#: The five classes, in this order — agent-bureau's `weekly_stops.CONCLUSIONS`.
CLASSES = ("transient-infrastructure", "bad-card", "bad-code",
           "platform-fault", "unreadable")
UNREADABLE = "unreadable"

#: The four steps a class may declare, as its step or its fallback.
STEPS = ("requeue-once", "send-to-planning", "fix-path", "ask-person")

#: The conditions a `fallback_when` may name, each as the plain line a reason
#: says when it holds.
CONDITIONS = {
    "resume-spent": "the one re-run is already spent — the failed run is on "
                    "its third attempt",
    "planning-spent": "the medic has already sent this card to Planning once",
    "no-open-pr": "the card has no open pull request for the fix loop to "
                  "work on",
}

#: The only snapshot kind with a work card to move. A red-main repair card
#: and a console alert keep today's delivery.
CARD_KIND = "card"

#: The lane a card with an open pull request sits in.
REVIEW_LANE = "In Review"

# ── the receipt ─────────────────────────────────────────────────────────────
RECEIPT_TAG = "medic-next-step"
RECEIPT_MARK = f"🩺 {RECEIPT_TAG}:"
NOT_MOVED = " moved=no"
_RECEIPT_LINE = re.compile(
    rf"^{re.escape(RECEIPT_MARK)} class=(\S+) step=(\S+) run=(\S+)"
    rf"({re.escape(NOT_MOVED)})?$"
)
_RUN_ID = re.compile(r"/runs/(\d+)")

#: What the medic did, for the receipt's paragraph.
_DONE = {
    "requeue-once": "re-ran the failed jobs of the run once more",
    "send-to-planning": "sent the card back to Planning to be planned again",
    "fix-path": "left the card where it is, for the fix loop to correct on "
                "its open pull request",
    "ask-person": "moved the card to Green Light to ask a person what "
                  "happens next",
}
#: What the medic meant to do, and why it did not, when the move did not land.
_MEANT = {
    "requeue-once": ("re-run the failed jobs of the run once more",
                     "the re-run would not start"),
    "send-to-planning": ("send the card back to Planning",
                         "the move was refused"),
    "ask-person": ("move the card to Green Light to ask a person",
                   "the move was refused"),
}

# ── the finding and the Green Light question ────────────────────────────────
NO_REPORT_FINDING = (
    "The medic could not finish its diagnosis of this failure, so it wrote no "
    "report — the failed run is linked below."
)
TECHNICAL_FINDING = (
    "The medic's diagnosis is written in technical terms, so it is not "
    "repeated here — the full diagnosis is posted on this card and the failed "
    "run is linked below."
)
FALLBACK_QUESTION = (
    "What should happen to this card next: run it again, send it back to "
    "planning, or have the operator look at the pipeline?"
)
WHY_NOT_FINISHED = "the diagnosis did not finish"
WHY_NO_ANSWER = "the diagnosis named no answer"
QUESTION_FIELDS = ("finding", "question", "recommendation", "why")

#: The bound on a finding's one line — `console_escalation.ONE_LINE_LIMIT`.
FINDING_LIMIT = 300
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


@dataclass(frozen=True)
class NextStep:
    """The step the medic owes one diagnosis, and the plain line saying why."""

    cls: str
    step: str
    reason: str


def load(path: str | None = None) -> dict:
    """The table, class → record. Raises OSError or ValueError when the file
    cannot be read; `check()` names that, and the shipped file is tested."""
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def check(path: str | None = None) -> list[str]:
    """Every gap between the table and the contract, named. Empty means it
    holds: the class set is exactly `CLASSES`, every step and fallback is in
    `STEPS`, and every fallback names its one condition."""
    name = os.path.basename(path or CONFIG_PATH)
    try:
        table = load(path)
    except (OSError, ValueError) as e:
        return [f"{name} cannot be read: {e}"]
    if not isinstance(table, dict):
        return [f"{name} is not an object keyed by class"]
    problems = [f"class {cls} is in CLASSES and has no record in {name}"
                for cls in CLASSES if cls not in table]
    problems += [f"class {cls} has a record in {name} and is not in CLASSES"
                 for cls in table if cls not in CLASSES]
    for cls, record in table.items():
        if not isinstance(record, dict):
            problems.append(f"{cls}: the record is not an object")
            continue
        step = record.get("step")
        if step not in STEPS:
            problems.append(f"{cls}: step {step!r} is not one of {STEPS}")
        means = record.get("means")
        if not isinstance(means, str) or not means.strip():
            problems.append(f"{cls}: means is not a plain-English line")
        fallback, when = record.get("fallback"), record.get("fallback_when")
        if fallback is None:
            if when is not None:
                problems.append(f"{cls}: fallback_when {when!r} is set and "
                                f"there is no fallback")
            continue
        if fallback not in STEPS:
            problems.append(f"{cls}: fallback {fallback!r} is not one of "
                            f"{STEPS}")
        if not when:
            problems.append(f"{cls}: fallback {fallback!r} has no "
                            f"fallback_when")
        elif when not in CONDITIONS:
            problems.append(f"{cls}: fallback_when {when!r} is not one of "
                            f"{tuple(CONDITIONS)}")
    return problems


# --------------------------------------------------------------------------- #
# the class, and the step it owes                                              #
# --------------------------------------------------------------------------- #


def read_class(path: str) -> str:
    """The verdict file's one word, trimmed and lower-cased, when it is one of
    `CLASSES`. A missing, empty or undecodable file, more than one word, or
    any other word — the old `resume` and `hold` included — reads
    `unreadable`. Never raises."""
    try:
        words = Path(path).read_text(encoding="utf-8").split()
    except (OSError, UnicodeDecodeError, TypeError, ValueError):
        return UNREADABLE
    if len(words) != 1:
        return UNREADABLE
    word = words[0].lower()
    return word if word in CLASSES else UNREADABLE


def decide(cls: str, *, kind: str, resume_spent: bool, has_open_pr: bool,
           planning_spent: bool) -> NextStep | None:
    """The step `cls` owes on a `card` kind: its declared step, or its
    fallback when the fallback's one condition holds. None for any other
    kind — there is no work card to move. A class outside `CLASSES` decides
    as `unreadable`."""
    if kind != CARD_KIND:
        return None
    table = load()
    if cls not in CLASSES or cls not in table:
        cls = UNREADABLE
    record = table[cls]
    step, fallback = record["step"], record.get("fallback")
    when = record.get("fallback_when")
    holds = {"resume-spent": resume_spent,
             "planning-spent": planning_spent,
             "no-open-pr": not has_open_pr}
    if fallback and holds.get(when):
        return NextStep(cls, fallback,
                        f"{cls} falls back from {step} to {fallback}, because "
                        f"{CONDITIONS[when]}.")
    reason = f"{cls} takes its declared step, {step}"
    if fallback:
        reason += (f" — its fallback to {fallback} applies only when "
                   f"{CONDITIONS[when]}, and that does not hold")
    return NextStep(cls, step, reason + ".")


# --------------------------------------------------------------------------- #
# the receipt                                                                  #
# --------------------------------------------------------------------------- #


def _run_id(run_url: str) -> str:
    found = _RUN_ID.search(run_url or "")
    if found:
        return found.group(1)
    tail = (run_url or "").rstrip("/").rsplit("/", 1)[-1]
    return tail or "unknown"


def receipt(step: NextStep, *, run_url: str, finding: str, moved: bool = True,
            lane: str | None = None) -> str:
    """The one comment the medic writes for its step. Line one is the
    contract line, ending ` moved=no` when the step's move did not land;
    `fix-path` moves nothing by design and never carries it. Then one
    plain-English paragraph: the class, what the medic did, the finding."""
    not_moved = not moved and step.step != "fix-path"
    first = (f"{RECEIPT_MARK} class={step.cls} step={step.step} "
             f"run={_run_id(run_url)}")
    if not_moved:
        first += NOT_MOVED
    try:
        means = load()[step.cls]["means"]
    except (OSError, ValueError, KeyError, TypeError):
        means = ""
    said = [f"The medic diagnosed this failure as {step.cls}."]
    if means:
        said.append(means)
    if not_moved:
        meant, why = _MEANT.get(step.step, (f"take the step {step.step}",
                                            "the move was refused"))
        said.append(f"It meant to {meant}, but {why}, so the card was left "
                    f"in {lane or 'the lane it was in'}.")
    else:
        said.append(f"So it {_DONE.get(step.step, f'took the step {step.step}')}.")
    if step.reason:
        said.append(f"Why this step: {step.reason}")
    said.append(f"Finding: {' '.join(finding.split())}")
    said.append(f"The failed run: {run_url}")
    return f"{first}\n\n{' '.join(said)}\n"


def parse_receipt(body: str | None) -> dict | None:
    """`{"class", "step", "run", "moved"}` off a body whose FIRST line is the
    receipt's contract line, or None. The one parser: `planning_spent` and
    the tests both read through it."""
    first = (body or "").split("\n", 1)[0].strip()
    found = _RECEIPT_LINE.match(first)
    if not found:
        return None
    cls, step, run, not_moved = found.groups()
    return {"class": cls, "step": step, "run": run, "moved": not not_moved}


# --------------------------------------------------------------------------- #
# the card's thread                                                            #
# --------------------------------------------------------------------------- #


def comment_bodies(path: str | None) -> list[str] | None:
    """The comment bodies in a comments file, oldest first, or None.

    Two shapes, and only these: `linear_ops.py dump-comments <card>`'s JSON
    array of body strings, and the medic retry gate's snapshot exactly as
    `medic_retry.write_snapshot` writes it — an object whose `comments` is a
    list of `{"body", "created_at"}` objects. An entry there that is not an
    object, or whose body is not a string, gives "". Anything else answers
    None, so a comment object never reaches a reader that splits a string."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    if isinstance(data, list):
        return list(data) if all(isinstance(b, str) for b in data) else None
    if isinstance(data, dict) and isinstance(data.get("comments"), list):
        return [c["body"] if isinstance(c, dict)
                and isinstance(c.get("body"), str) else ""
                for c in data["comments"]]
    return None


def waiting(bodies, *, run_id: str | None = None) -> bool:
    """Is the card waiting out a limit or a vendor wait? True when
    `limit_recovery.waiting` finds a `🪦 limit-death:` marker no later
    pipeline receipt has superseded — and, with `run_id`, only when that
    marker's `run=` is `run_id`. The walk is `limit_recovery`'s, never
    copied."""
    import limit_recovery  # local: only this reader needs it

    marker = limit_recovery.waiting(bodies)
    if marker is None:
        return False
    return not run_id or str(marker.get("run")) == str(run_id)


def planning_spent(bodies) -> bool:
    """Has the medic already sent this card to Planning? True when any body's
    first line is a `send-to-planning` receipt that moved — a ` moved=no`
    receipt sent nothing."""
    for body in bodies or []:
        parsed = parse_receipt(body)
        if parsed and parsed["step"] == "send-to-planning" and parsed["moved"]:
            return True
    return False


def from_lanes() -> list[str]:
    """The lanes the medic moves a card out of: `lane_contract.START_LANES`,
    then `lane_contract.QUEUE_LANE`, then In Review, repeats dropped and the
    order kept. Both the Planning move and the Green Light move read it, so
    the two lists cannot drift apart."""
    import lane_contract  # local: only this reader needs the contract

    return list(dict.fromkeys([*lane_contract.START_LANES,
                               lane_contract.QUEUE_LANE, REVIEW_LANE]))


# --------------------------------------------------------------------------- #
# what a person is shown                                                       #
# --------------------------------------------------------------------------- #


def finding(report_path: str | None) -> str:
    """The report's first sentence on one line, past any leading Markdown
    heading. A missing or empty report answers `NO_REPORT_FINDING`. Never
    raises."""
    try:
        text = Path(report_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, TypeError, ValueError):
        return NO_REPORT_FINDING
    lines = text.splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("#")):
        lines.pop(0)
    flat = " ".join(" ".join(lines).split())
    if not flat:
        return NO_REPORT_FINDING
    first = _SENTENCE_END.split(flat, maxsplit=1)[0]
    if len(first) > FINDING_LIMIT:
        first = first[: FINDING_LIMIT - 1] + "…"
    return first


def _question_file(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def question(question_path: str | None, *, report_path: str | None) -> str:
    """The medic's Green Light question, guarded at the write seam.

    The diagnosis agent writes the question file after reading untrusted run
    logs and card text, so every line in it is data: each is kept only when
    it is a non-blank string `planning_escalation.refusal` passes, and the
    recommendation only together with its why. Every other line is replaced
    by its fallback, and one stderr line names each replaced field and why.
    The three lines are `console_escalation.render`'s own. Never raises."""
    import console_escalation
    import planning_escalation

    data = _question_file(question_path)
    kept: dict[str, str] = {}
    held: dict[str, str] = {}
    for field in QUESTION_FIELDS:
        value = data.get(field) if data is not None else None
        if not isinstance(value, str) or not value.strip():
            held[field] = ("missing" if data is not None
                           else "missing — the question file could not be read")
            continue
        refused = planning_escalation.refusal(value)
        if refused:
            held[field] = refused
        else:
            kept[field] = value.strip()
    if ("recommendation" in kept) != ("why" in kept):
        for field in ("recommendation", "why"):
            if kept.pop(field, None) is not None:
                held[field] = "kept only with its pair, which was replaced"

    from_report = finding(report_path)
    no_report = from_report == NO_REPORT_FINDING
    if "finding" not in kept:
        kept["finding"] = (from_report
                           if planning_escalation.refusal(from_report) is None
                           else TECHNICAL_FINDING)
    for field in QUESTION_FIELDS:
        if field in held:
            print(f"medic_next_step question: {field} replaced — "
                  f"{held[field]}", file=sys.stderr)
    return console_escalation.render(console_escalation.Escalation(
        kept["finding"],
        kept.get("question", FALLBACK_QUESTION),
        kept.get("recommendation"),
        kept.get("why", WHY_NOT_FINISHED if no_report else WHY_NO_ANSWER),
    ))


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("read", help="print the verdict file's class")
    p.add_argument("--file", required=True)

    p = sub.add_parser("decide", help="print step=<step> and the reason")
    p.add_argument("--class", dest="cls", required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--resume-spent", action="store_true")
    p.add_argument("--has-open-pr", action="store_true")
    p.add_argument("--planning-spent", action="store_true")

    p = sub.add_parser("receipt", help="print the medic's step receipt")
    p.add_argument("--class", dest="cls", required=True, choices=CLASSES)
    p.add_argument("--step", required=True, choices=STEPS)
    p.add_argument("--run-url", required=True)
    p.add_argument("--report", required=True)
    # The finding `finding --report` printed (DRE-7799); empty or absent, the
    # receipt reads it off --report itself.
    p.add_argument("--finding", default="")
    p.add_argument("--reason", default="")
    p.add_argument("--moved-no", action="store_true")
    p.add_argument("--lane", default=None)

    p = sub.add_parser("finding", help="print the report's first sentence")
    p.add_argument("--report", required=True)

    sub.add_parser("from-lanes", help="print the lanes the medic moves a "
                                      "card out of, comma-joined")

    p = sub.add_parser("waiting", help="print waiting=true or waiting=false")
    p.add_argument("--comments-file", required=True)
    p.add_argument("--run-id", default=None)

    p = sub.add_parser("planning-spent",
                       help="print planning_spent=true, false or unknown")
    p.add_argument("--comments-file", required=True)

    p = sub.add_parser("question", help="print the Green Light question")
    p.add_argument("--file", required=True)
    p.add_argument("--report", required=True)

    p = sub.add_parser("check", help="exit 1 naming every gap in the table")
    p.add_argument("--file", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "read":
        print(read_class(args.file))
    elif args.cmd == "decide":
        step = decide(args.cls, kind=args.kind, resume_spent=args.resume_spent,
                      has_open_pr=args.has_open_pr,
                      planning_spent=args.planning_spent)
        if step is None:
            print("step=none")
            print(f"A {args.kind} has no work card to move, so the diagnosis "
                  f"is delivered as before.")
        else:
            print(f"step={step.step}")
            print(step.reason)
    elif args.cmd == "receipt":
        step = NextStep(args.cls, args.step, args.reason.strip())
        sys.stdout.write(receipt(step, run_url=args.run_url,
                                 finding=args.finding.strip()
                                 or finding(args.report),
                                 moved=not args.moved_no, lane=args.lane))
    elif args.cmd == "finding":
        print(finding(args.report))
    elif args.cmd == "from-lanes":
        print(",".join(from_lanes()))
    elif args.cmd == "waiting":
        bodies = comment_bodies(args.comments_file)
        answer = bodies is not None and waiting(bodies, run_id=args.run_id)
        print(f"waiting={'true' if answer else 'false'}")
    elif args.cmd == "planning-spent":
        bodies = comment_bodies(args.comments_file)
        answer = ("unknown" if bodies is None
                  else "true" if planning_spent(bodies) else "false")
        print(f"planning_spent={answer}")
    elif args.cmd == "question":
        print(question(args.file, report_path=args.report))
    else:
        problems = check(args.file)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            return 1
        print(f"{os.path.basename(args.file or CONFIG_PATH)}: every class has "
              f"one declared next step")
    return 0


if __name__ == "__main__":
    sys.exit(main())
