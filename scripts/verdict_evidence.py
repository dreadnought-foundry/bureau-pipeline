#!/usr/bin/env python3
"""A verdict's factual claims must carry their evidence (stdlib only).

DRE-3005. On 2026-09-02/03 two pull requests in two repos were blocked by
critic findings that are provably false, and both assert something about a
RUN — a command's exit code, a job's coverage — that the run itself
contradicts.

  * agent-bureau #2247. The verdict's only remaining blocker: *"I ran
    `python3 .bureau-pipeline/scripts/check_tdd_commits.py origin/main HEAD`
    myself against the current head and it exits 1 with 'no test commit
    precedes the implementation'"*. Run independently — same script at the
    ref the workflow pins, same head — it exits 0. THE TELL is what the
    verdict left out: check_tdd_commits.py prints one classification line
    per commit UNCONDITIONALLY, beside the reason, and the verdict
    reproduced the failure string verbatim and none of the listing. That
    omission is the whole reason the claim was checkable at all. ~22h
    blocked, one fix-loop attempt spent, one operator decision.
  * portico #407. *"This PR's own CI checks never ran the new spec through
    that job."* Job 100550113617 in run 33724409256 ran it: 194 passed
    (5.1m). One API read away. ~10h blocked.

Neither is a judgement call the critic is entitled to get wrong. Both are
checkable facts, and the estate already has the law they violate — *capture
the error body, not the status* — which had only ever been applied to
clients. A verdict claiming "I ran X and got Y" is the same shape and
carries the same obligation.

WHAT THIS MODULE DECIDES, and nothing else: does a BLOCKING verdict make a
claim about a run without carrying the run? Three rules, and each one is a
claim a command can settle:

  1. A verdict that cites a command must include that command's actual
     output — enough of it to be re-run and compared, which means the
     command AND its result in one fenced block, not a quoted fragment.
  2. A finding that a CI job did not run something must cite the job: a run
     id, a job id, and the line proving what it ran — a line that carries
     one of those ids or the run's own result, not any fence that happens
     to sit beside the claim.
  3. The verdict states the PR-body snapshot it read (body_snapshot_footer,
     stamped by the workflow — the critic cannot be trusted to timestamp
     itself, and the workflow already knows both times).

WHAT IT DELIBERATELY DOES NOT TOUCH:

  * THE CRITIC'S AUTHORITY ON JUDGEMENT. Scope, design and risk findings
    are where the critic is meant to be believed and are invisible to this
    module. Only claims a command can settle are in scope.
  * AN APPROVE. The harm is a verdict that BLOCKS correct work; an APPROVE
    blocks nothing, and spending a re-review to police its citation format
    would protect nobody. `defects()` returns nothing for one.
  * A CLAIM THAT SOMETHING PASSED. Same asymmetry one level down, and it is
    measured rather than assumed: run over the twelve verdicts this repo
    merged on between PRs #230 and #237, an un-narrowed gate held two
    correct reviews — one for listing the pipeline checks it had run with
    their exit-0 results inline, one for naming three test files beside "I
    ran all 128 of them" (tests/fixtures/critic-verdicts-2026-09.json).
    Neither could block anything by being wrong. Only an ADVERSE outcome —
    a non-zero exit, a failure, a denial — is in scope.
  * FALSE POSITIVES ARE THE REAL RISK HERE. This sits in front of every
    rejection the pipeline makes, so detection is narrow on purpose: a
    command reference AND an explicit assertion about a run's outcome. Bare
    prose naming a file, a function or a test is not a claim
    (tests/test_verdict_evidence.py::NoFalsePositiveTest pins eight shapes
    of ordinary review prose that must stay silent).

WHAT A DEFECT COSTS. qa-review.yml's result gates run `check` beside
check_critic_result.py: a defective REQUEST_CHANGES is not a real verdict,
so the existing machinery retries once and then posts `hold_message()` — a
NEUTRAL hold that carries the `QA Critic` marker (so it supersedes a stale
APPROVE) and NO `VERDICT:` line (so merge-gate holds and the fix agent is
not dispatched to fix findings nobody proved). The job then fails loudly,
medic-visible. It can only ever hold a merge, never grant one.

WHAT THE MEDIC THEN DOES, named rather than left to be discovered
(vendor-boundaries Q5). That loud fail is a `workflow_run` failure the medic
watches. This hold carries neither medic_classify's CRITIC_NEUTRAL_MARKER
nor a rate-limit signature, so the medic reads it as an ordinary failure and
re-runs the review once — which is the RIGHT answer here, and not the
DRE-1921 loop. That rule exists for a retry against a resource the retry
itself depletes; nothing is exhausted here, and a fresh review over the same
diff, under a prompt that now states these rules, can genuinely reach an
evidenced verdict where the last one did not.

CLI:

    verdict_evidence.py check <verdict-file> [--github-output <path>]
        Exit 0 when the verdict carries its evidence (or is an APPROVE, or
        declares no verdict at all — that is check_critic_result.py's
        question, not this one). Exit 1 when a claim is unevidenced; the
        defects go to stdout and the neutral hold message is written to
        --hold-file. With --github-output, appends `evidence=ok|defective`.

    verdict_evidence.py snapshot --reviewed-at <ts> --edited-at <ts>
        Prints the body-snapshot footer (rule 3) for the posting step, or
        nothing when there is no snapshot time to state. Always exits 0 — a
        missing timestamp must never fail a review that has a verdict.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verdict_cause import verdict_decision  # noqa: E402

#: Only a BLOCKING verdict is gated — see the module docstring.
_BLOCKING = "REQUEST_CHANGES"

#: Where the findings live. The `## Summary` section above it is CEO-facing
#: prose that is banned from carrying file paths and commands at all, and
#: the header carries the machine-readable verdict line. Scanning only the
#: findings keeps the gate off both.
_FINDINGS_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*for the fixing agent\b",
                               re.I)

#: A fenced block: ```…``` or ~~~…~~~, with an optional info string.
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*(\S*)", re.M)

#: A backticked span. The claim's command is read from here and nowhere
#: else: an unquoted word in prose is not a citation, and treating it as one
#: is how "the classifier returns the wrong strategy" becomes a run claim.
_BACKTICKED = re.compile(r"`([^`\n]{2,200})`")

#: What makes a backticked span a COMMAND rather than a symbol. Either it
#: opens with a runner the fleet actually invokes, or it is a script path
#: someone could paste into a shell — which a bare `foo.py` is not. The
#: fleet's critics name test FILES in prose constantly (bp #235: "the new
#: test suites (`test_act_emission.py`, …) are real — I ran all 128 of
#: them"), and reading those as commands invents claims out of a sentence
#: that makes none. Anchored at the start for the runners, so `` `npm` ``
#: in "the npm cache" is not a command line either.
_RUNNERS = (
    "python3", "python", "pytest", "npm", "npx", "node", "yarn", "pnpm",
    "make", "gh", "git", "bash", "sh", "ruff", "mypy", "cargo", "go",
    "docker", "terraform", "cdk", "alembic", "curl",
)

#: THE ASYMMETRY, and the whole reason this gate is safe to put in front of
#: every rejection the pipeline makes. A claim that a command SUCCEEDED
#: cannot block a pull request; a claim that it FAILED can, and twice did.
#: So only an adverse outcome is in scope — the same asymmetry that leaves
#: an APPROVE and a positive CI-coverage claim alone. Measured, not
#: assumed: run over the twelve verdicts this repo merged on between
#: PRs #230 and #237, the un-narrowed gate held two correct reviews, both
#: for reporting that a check had PASSED
#: (tests/fixtures/critic-verdicts-2026-09.json).
_ADVERSE = re.compile(
    r"\bexit(?:s|ed)?\s+(?:code\s+)?[1-9]\d*\b"
    r"|\bnon-?zero\b"
    r"|\bfail(?:s|ed|ing|ure|ures)?\b"
    r"|\berror(?:s|ed)?\b|\bexception\b|\btraceback\b"
    r"|\bcrash(?:es|ed)?\b|\bbroke(?:n)?\b|\bbreaks\b"
    r"|\breject(?:s|ed)?\b|\brefus(?:e|es|ed)\b|\bblock(?:s|ed)\b"
    r"|\b(?:red|not green)\b"
    r"|\b(?:does|did|do|is|are|was|were|has|have|can|could|will|would)"
    r"\s*n[o']?t\b"
    r"|\bnever\b|\bno\s+(?:such|test|output|match)\b",
    re.I,
)

#: An assertion about what a run DID. Deliberately about the outcome of an
#: execution, never about behaviour in general: "returns", "produces" and
#: "passes" are the vocabulary of ordinary review prose about a function and
#: are absent on purpose.
_RUN_ASSERTIONS = (
    r"\bi\s+(?:just\s+)?(?:ran|re-?ran|executed|invoked)\b",
    r"\bwhen\s+(?:i|you|we)\s+run\b",
    r"\brunning\s+it\b",
    r"\bran\s+it\b",
    r"\bexit(?:s|ed)?\s+(?:code\s+)?\d",
    r"\bexit(?:s|ed)\s+with\b",
    r"\bexit\s+code\s+is\b",
    r"\bfails?\s+with\b",
    r"\berrors?\s+(?:out\s+)?with\b",
    r"\bit\s+prints\b",
    r"\bit\s+outputs\b",
    r"\boutput\s+(?:is|was)\b",
    r"\blocally\s+(?:it|this)\s+\w+s\b",
)
_RUN_ASSERTION_RE = re.compile("|".join(_RUN_ASSERTIONS), re.I)

#: A NEGATIVE claim about what a CI job covered. The positive claim ("the
#: e2e job ran it") is not gated: it supports a finding about something
#: else, and it is not the shape that blocks a pull request.
_COVERAGE_SUBJECTS = re.compile(
    r"\b(ci|job|jobs|check|checks|workflow|pipeline|suite|runner)\b", re.I)
_COVERAGE_DENIALS = re.compile(
    r"\b(?:never|not|no|nothing|none)\s+(?:\w+\s+){0,3}?"
    r"(?:ran|run|runs|executed|exercised|covered|cover|covers)\b"
    r"|\b(?:did|does|do|has|have|was|were|is|are)\s*n[o']?t\s+(?:\w+\s+){0,3}?"
    r"(?:ran|run|runs|executed|exercised|covered)\b"
    r"|\bnever\s+(?:ran|run|executed|exercised)\b",
    re.I,
)

#: The two ids a coverage claim must carry. `run 33724409256` /
#: `/actions/runs/33724409256` and `job 100550113617` / `/job/100550113617`.
#: Six digits minimum so a sentence saying "run 2" cannot satisfy it.
_RUN_ID = re.compile(r"\b(?:runs?|actions/runs)[\s/#:]+(\d{6,})", re.I)
_JOB_ID = re.compile(r"\bjobs?[\s/#:]+(\d{6,})", re.I)

#: What makes a fenced block THE LINE PROVING WHAT THE JOB RAN, rather than
#: any block of text that happens to sit beside the claim. Same laundering
#: rule 1 refuses in _has_output_for, one rule down: the evidence has to
#: carry something of the thing it evidences, or "```python\ndef
#: unrelated(): ...\n```" pasted beside #407's sentence satisfies the gate
#: written to catch #407. Either the block names an id the section cites —
#: the API read itself — or it reads like a run's own result line.
_RAN_LINE = re.compile(
    r"(?i:\b\d+\s+(?:passed|failed|skipped|errors?|tests?|examples?|specs?|"
    r"assertions?|checks?)\b)"
    r"|(?i:\bran\s+\d+\s+\w+)"
    r"|(?i:\bno\s+tests?\s+(?:ran|were\s+run|to\s+run)\b)"
    r"|(?i:\bexit(?:s|ed)?\s+(?:code\s+)?\d+\b)"
    r"|\b(?:PASS|PASSED|FAIL|FAILED|OK|SUCCESS|SKIPPED)\b"
)

#: Sentence splitting. Crude on purpose — a claim and its evidence are
#: judged over the whole verdict, so a mis-split costs nothing but a
#: slightly wider quote in the hold message.
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n")

#: How much of a claim reaches the hold message a human reads.
_QUOTE_CHARS = 200


class Defect:
    """One unevidenced claim: the rule it broke, and the claim itself."""

    def __init__(self, rule: str, claim: str, missing: str):
        self.rule = rule
        self.claim = claim.strip()
        self.missing = missing

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Defect({self.rule!r}, {self.claim[:40]!r})"

    def line(self) -> str:
        quote = self.claim[:_QUOTE_CHARS]
        if len(self.claim) > _QUOTE_CHARS:
            quote += "…"
        return f"[{self.rule}] {self.missing}\n    claim: {quote}"


# ── reading the verdict ────────────────────────────────────────────────────

def is_blocking(text: str) -> bool:
    """True iff this verdict's own header declares REQUEST_CHANGES.

    Read through verdict_cause.verdict_decision, the same parser
    check_critic_result.py uses, so a `cause:` tag can never make the two
    disagree about what the verdict says.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("VERDICT:"):
            return verdict_decision(stripped) == _BLOCKING
    return False


def findings_section(text: str) -> str:
    """The `## For the fixing agent` section, or "" when there is none.

    Everything above it is the machine-readable header and the CEO summary,
    which the prompt bans from carrying a file path or a command at all.
    A verdict that blocks and writes no findings section has nothing for
    this gate to read — that is check_critic_result.py's problem.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _FINDINGS_HEADING.match(line):
            return "\n".join(lines[i + 1:])
    return ""


def fenced_blocks(text: str) -> list[str]:
    """Every fenced block's BODY, in order.

    Written as a scan rather than a single regex because an unterminated
    fence (a review cut off mid-thought) must yield the rest of the text as
    one block instead of raising or silently matching nothing.
    """
    blocks, opener, buf = [], None, []
    for line in text.splitlines():
        m = _FENCE.match(line)
        if opener is None:
            if m:
                opener, buf = m.group(1)[0], []
            continue
        if m and m.group(1)[0] == opener:
            blocks.append("\n".join(buf))
            opener = None
            continue
        buf.append(line)
    if opener is not None:
        blocks.append("\n".join(buf))
    return blocks


def prose(text: str) -> str:
    """The findings with every fenced block removed.

    A claim is made in prose. Scanning the fences too would let a pasted
    transcript that happens to contain the word "ran" invent claims out of
    the very evidence that answers them.
    """
    out, opener = [], None
    for line in text.splitlines():
        m = _FENCE.match(line)
        if opener is None:
            if m:
                opener = m.group(1)[0]
            else:
                out.append(line)
            continue
        if m and m.group(1)[0] == opener:
            opener = None
    return "\n".join(out)


def _commands(sentence: str) -> list[str]:
    """The backticked spans in `sentence` that read as command lines.

    A script path qualifies only when it is one someone could paste into a
    shell — it has arguments, or a directory in front of it. A bare
    `foo.py` in prose is a file being named, not a run being claimed.
    """
    found = []
    for span in _BACKTICKED.findall(sentence):
        words = span.strip().split()
        if not words:
            continue
        head = words[0].lstrip("$").rstrip(":")
        if head in _RUNNERS:
            found.append(span.strip())
        elif head.endswith((".py", ".sh")) and ("/" in head or len(words) > 1):
            found.append(span.strip())
    return found


def run_claims(text: str) -> list[str]:
    """Sentences asserting that running a command went BADLY.

    Three halves are required — a command reference, an assertion about a
    run's outcome, and an ADVERSE outcome — and every one of them narrows a
    gate that stands in front of every rejection the pipeline makes. A
    passing run is out of scope on purpose: it blocks nothing, so it cannot
    block correct work, and holding a review over how it was cited would
    spend a re-review to protect nobody (see _ADVERSE).
    """
    claims = []
    for sentence in _SENTENCE.split(prose(text)):
        if not sentence.strip() or not _RUN_ASSERTION_RE.search(sentence):
            continue
        if not _ADVERSE.search(sentence):
            continue
        if _commands(sentence):
            claims.append(sentence.strip())
    return claims


def job_claims(text: str) -> list[str]:
    """Sentences denying that a CI job ran something."""
    claims = []
    for sentence in _SENTENCE.split(prose(text)):
        if not sentence.strip():
            continue
        if _COVERAGE_SUBJECTS.search(sentence) and \
                _COVERAGE_DENIALS.search(sentence):
            claims.append(sentence.strip())
    return claims


def _has_output_for(command: str, blocks: list[str]) -> bool:
    """Is `command` pasted in a fenced block WITH something beside it?

    "Enough of it to be re-run and compared" is two halves: the command, so
    it can be re-run, and its result, so the answers can be compared. A
    fence carrying the command and nothing else is #2247 with extra
    punctuation — and a fence carrying only the failure STRING is #2247
    exactly.

    Matched on the command's own text (whitespace-normalised) rather than
    on position, so an unrelated diff snippet elsewhere in the verdict
    cannot launder the claim.
    """
    needle = " ".join(command.split())
    for block in blocks:
        flat = " ".join(block.split())
        if needle not in flat:
            continue
        rest = [ln for ln in block.splitlines()
                if ln.strip() and needle not in " ".join(ln.split())]
        if rest:
            return True
    return False


def _proves_the_job_ran(blocks: list[str], section: str) -> bool:
    """Does a fenced block carry evidence tied to the job being claimed?

    "The line proving what the job ran" is a line FROM the job, not a fence
    somewhere in the same section. Two shapes count, and both tie the paste
    to the claim: the block names one of the ids the section cites (the API
    read pasted whole), or it reads like a run's own result line (`194
    passed (5.1m)`). An unrelated snippet does neither — which is the point,
    because pasting one is otherwise a one-line defeat of the rule.
    """
    ids = set(_RUN_ID.findall(section)) | set(_JOB_ID.findall(section))
    for block in blocks:
        if not block.strip():
            continue
        if any(i in block for i in ids):
            return True
        if _RAN_LINE.search(block):
            return True
    return False


def defects(text: str) -> list[Defect]:
    """Every unevidenced factual claim in a blocking verdict.

    Empty for an APPROVE, for a verdict with no readable decision, and for
    a verdict whose findings are judgement — which is most of them.
    """
    if not is_blocking(text):
        return []
    section = findings_section(text)
    if not section.strip():
        return []
    blocks = fenced_blocks(section)
    found: list[Defect] = []
    for claim in run_claims(section):
        missing = [c for c in _commands(claim)
                   if not _has_output_for(c, blocks)]
        for command in missing:
            found.append(Defect(
                "command-output", claim,
                f"the verdict asserts what `{command}` did and does not "
                "carry that command's output — paste the command and its "
                "actual result in one fenced block so the run can be "
                "repeated and compared",
            ))
    if job_claims(section):
        whole = section
        gaps = []
        if not _RUN_ID.search(whole):
            gaps.append("a run id")
        if not _JOB_ID.search(whole):
            gaps.append("a job id")
        if not _proves_the_job_ran(blocks, whole):
            gaps.append("the line proving what the job ran")
        if gaps:
            for claim in job_claims(section):
                found.append(Defect(
                    "job-coverage", claim,
                    # Reads for one gap and for three: the common case is
                    # now a single missing half, not a citation-free claim.
                    "the verdict says a CI job did not run something and "
                    "does not cite " + " and ".join(gaps),
                ))
    return found


# ── rule 3: the snapshot the review read ───────────────────────────────────

#: Stamped by the workflow, never by the agent. The critic cannot timestamp
#: its own read honestly — that is the claim under audit — and the workflow
#: already holds both times.
_SNAPSHOT_PREFIX = "🕰️ PR description read at"


def body_snapshot_footer(reviewed_at: str, edited_at: str | None) -> str:
    """The freshness line that rides at the foot of the verdict comment.

    portico #407: the review snapshotted the body at 06:41:33's text, the
    author corrected it at 06:43:21, and the verdict posted at 06:48:41
    demanding a correction that had been there for five minutes. The race
    itself is not fixable — a review takes minutes and a body can be edited
    inside them — but a race with a signal costs a sentence instead of ten
    hours.

    Returns "" when the workflow could not read the snapshot time: an
    invented timestamp is worse than none (console-honesty rule 2 — unknown
    is shown as unknown). An `edited_at` that is empty, unparseable, or at
    or before the snapshot yields the plain line; only an edit provably
    AFTER the read is flagged, or every PR whose body was ever touched
    would carry a warning.
    """
    reviewed = (reviewed_at or "").strip()
    if not reviewed:
        return ""
    line = f"{_SNAPSHOT_PREFIX} {reviewed}."
    edited = (edited_at or "").strip()
    if edited and edited > reviewed:
        return (
            f"{line} ⚠️ It was edited at {edited}, AFTER this review read "
            "it — any finding about the description may be answering text "
            "that no longer exists. Re-read the description before acting "
            "on one."
        )
    return line


# ── the neutral hold a defective verdict becomes ───────────────────────────

def hold_message(found: list[Defect]) -> str:
    """The comment posted in place of a verdict the gate cannot believe.

    Same neutral contract as qa-review.yml's crash and oversize notices:
    carries `QA Critic` so merge-gate treats it as the latest word and it
    supersedes a stale APPROVE, carries NO `VERDICT:` line so the gate holds
    rather than merges and the fix agent is not woken to fix findings nobody
    proved. It claims nothing about credentials — the reviewer ran fine, and
    borrowing the crash wording is what cost a day of credential-hunting
    (DRE-2465).
    """
    if not found:
        return ""
    body = "\n".join(f"- {d.line()}" for d in found)
    return (
        "🔎 QA Critic produced a verdict that asserts a result it did not "
        "show — re-review needed, this is not a code rejection.\n\n"
        "The review blocked this pull request on a claim about what a "
        "command or a job actually did, and did not include the run itself. "
        "Two pull requests were blocked overnight on claims of exactly this "
        "shape that did not reproduce, so the pipeline no longer posts one "
        "as a rejection (DRE-3005). Nothing here says the change is wrong: "
        "no finding has been accepted or dismissed, and merge is held until "
        "a review lands its evidence.\n\n"
        "What was missing:\n\n" + body + "\n"
    )


# ── CLI ────────────────────────────────────────────────────────────────────

def _write_output(path: str | None, value: str) -> None:
    if not path:
        return
    try:
        with open(path, "a") as f:
            f.write(f"evidence={value}\n")
    except OSError as exc:
        # A gate that cannot write its outputs still has an answer.
        print(f"verdict evidence gate: could not write step outputs: {exc}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["check", "snapshot"])
    ap.add_argument("verdict_file", nargs="?")
    ap.add_argument("--github-output", default=None)
    ap.add_argument("--hold-file", default=None,
                    help="where to write the neutral hold comment body")
    ap.add_argument("--reviewed-at", default="",
                    help="snapshot mode: when the review read the PR body")
    ap.add_argument("--edited-at", default="",
                    help="snapshot mode: the body's lastEditedAt, if any")
    args = ap.parse_args(argv)
    if args.mode == "snapshot":
        # Always exit 0 and print at most one line: the footer is a
        # courtesy on a verdict that is already composed, and a workflow
        # that could not read a timestamp must not fail a posted review
        # over it.
        footer = body_snapshot_footer(args.reviewed_at, args.edited_at)
        if footer:
            print(footer)
        return 0
    if not args.verdict_file:
        ap.error("check needs a verdict file")
    try:
        with open(args.verdict_file, "rb") as f:
            text = f.read().decode("utf-8", "replace")
    except OSError:
        # No verdict file is check_critic_result.py's finding, not ours.
        # Saying "ok" here cannot approve anything: that gate has already
        # failed and the workflow is on the neutral path.
        print("verdict evidence gate: no verdict file to read — "
              "check_critic_result.py owns that answer")
        _write_output(args.github_output, "ok")
        return 0
    found = defects(text)
    if not found:
        print("verdict evidence gate: ok — no unevidenced run claim")
        _write_output(args.github_output, "ok")
        return 0
    print(f"verdict evidence gate: FAIL — {len(found)} unevidenced "
          "claim(s) in a blocking verdict:")
    for defect in found:
        print(f"  {defect.line()}")
    _write_output(args.github_output, "defective")
    if args.hold_file:
        try:
            with open(args.hold_file, "w", encoding="utf-8") as f:
                f.write(hold_message(found))
        except OSError as exc:
            print(f"verdict evidence gate: could not write the hold "
                  f"message: {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
