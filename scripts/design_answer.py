#!/usr/bin/env python3
"""The critic's design question, the CEO's answer to it, and the one hand-back
of that answer to the critic (DRE-8722, rule 4c of DRE-8708).

When the critic's REQUEST_CHANGES verdict carries a design question, the fix
loop parks it on the card in Green Light (DRE-8735). The CEO answers from the
console, his words land on the card under the console's signature, and the
card returns to In Progress — the Green Light exit for an answered escalation,
where the move is a build dispatch. That build run's duplicate-dispatch guard
skips the build, because the card's pull request is open, and nothing else
ran. The critic's context carried no card comment, so the same verdict stood
at the same head, and a fix run on it would park the card and ask him again.

This is the one reader of the question and his answer, and the one writer that
hands his answer back. It follows `escalation_answers.py`'s shape: the question
a card was parked on, and the answer after it.

## What it reads

The card's thread, through `spoken_thread.read`:

  * **the question** — a comment the PIPELINE posted whose first line is
    `` 🎨 design-question: `<Name>` at `<path>` `` (`QUESTION_MARK`), followed
    by the three lines `console_escalation.render` writes. A person's comment
    opening with the mark is not the question, so a copy can neither raise one
    nor hide one.
  * **his answer** — a comment `spoken_thread` labels `ceo-via-console`: one
    whose console signature verifies. Nothing else is his answer, whoever
    posted it and whatever it says.
  * **the hand-back record** — a pipeline comment opening
    `` 🎨 design-question-answered: `<Name>` at `<path>` `` (`HANDBACK_MARK`).
    It is the counter: one hand-back per answer.

With the viewer unread, nobody can be told from the pipeline, so the thread
reads as `unknown` — never as "no question".

## The CLI

    design_answer.py state <CARD> --part <Name> --path <path>
    design_answer.py context <CARD>
    design_answer.py handback <CARD> --repo <owner/name>

`state` prints one word for the newest question naming that part and path:
`none`, `asked`, `answered`, `handed-back` or `unknown`. `context` prints one
`DESIGN ANSWER:` line for each part a question names, his words under an
answered one as `> ` lines through `sanitize_untrusted.sanitize_body`; a
`handed-back` part reads as answered, because the critic still needs his
words. `handback`, for a part in state `answered`, makes three writes in this
order: it dispatches one re-review of the card's open pull request
(`pr-review.yml` in this repo, where `qa-review.yml` cannot be dispatched,
DRE-2056 — the choice `report_fix_result.sh` makes), posts the record, and
moves the card from In Progress to In Review. A card already in In Review is
not moved. In any other state, or when the dispatch is refused, it writes
nothing.

The dispatch rides `DISPATCH_TOKEN` when the step sets one — the workflow's
own `github.token`, because the App token holds no Actions permission
(DRE-1254) — and the ambient `GH_TOKEN` otherwise. A stub that does not grant
`actions: write` is refused with HTTP 403, and the refusal is named on the
status line rather than hidden.

A crash between the dispatch and the record dispatches once more on the next
run: one extra review, never a lost answer.

An empty `<CARD>` — `qa-review.yml`'s `Resolve PR` step writes one for a pull
request whose branch names no card — reads nothing: `context` prints
`DESIGN ANSWER: none — this pull request's branch names no card`, `state`
prints `none`, and `handback` writes nothing.

Every command prints exactly one `design-answer:` line (`TAG`) on stderr,
saying what it read and what it wrote, and always exits 0.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess  # nosec B404 — fixed-arg gh calls, shell=False
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card_pr  # noqa: E402 — ONE finder of a card's pull request
import escalation_answers  # noqa: E402 — ONE quoting of what a voice said
import github_output  # noqa: E402 — ONE shut stdout while the work runs
import linear_ops  # noqa: E402 — ONE Linear client
import sanitize_untrusted  # noqa: E402 — ONE defang of the card-text fence
import spoken_thread  # noqa: E402 — ONE reader of who said what

#: Opens the one status line every command prints on stderr.
TAG = "design-answer:"
#: The question's first line opens with this, then `` `<Name>` at `<path>` ``.
QUESTION_MARK = "🎨 design-question:"
#: The hand-back record opens with this, then the same part and path.
HANDBACK_MARK = "🎨 design-question-answered:"

NONE = "none"
ASKED = "asked"
ANSWERED = "answered"
HANDED_BACK = "handed-back"
UNKNOWN = "unknown"

#: Opens every line `context` prints.
CONTEXT = "DESIGN ANSWER:"
NO_CARD = "this pull request's branch names no card"
#: Why a part whose answer could not be checked reads UNKNOWN.
UNCHECKED_ANSWER = ("an answer after the question could not be checked, "
                    "because the console's key could not be read")

#: The repository whose re-review is `pr-review.yml` (DRE-2056).
SELF_REPO = "dreadnought-foundry/bureau-pipeline"
IN_PROGRESS = "In Progress"
IN_REVIEW = "In Review"

_PART = r"`([^`\n]+)` at `([^`\n]+)`"
_QUESTION_LINE = re.compile(rf"^{re.escape(QUESTION_MARK)} {_PART}\s*$")
_RECORD_LINE = re.compile(rf"^{re.escape(HANDBACK_MARK)} {_PART}(?:\s|$)")

#: The fields `handback` reads off the card's pull request: its head, for the
#: record.
_PR_FIELDS = f"{card_pr.PR_FIELDS},headRefOid"


def _first_line(voice: spoken_thread.Voice) -> str:
    return (voice.body or "").lstrip().split("\n", 1)[0].strip()


def _marked(voice: spoken_thread.Voice,
            line: re.Pattern) -> tuple[str, str] | None:
    """(part, path) when the pipeline posted `voice` and its first line is
    `line`'s, else None."""
    if voice.kind != spoken_thread.PIPELINE:
        return None
    found = line.match(_first_line(voice))
    return (found.group(1), found.group(2)) if found else None


def _unreadable(all_voices: list[spoken_thread.Voice]) -> bool:
    return any(v.kind == spoken_thread.UNKNOWN for v in all_voices)


def _reading(all_voices: list[spoken_thread.Voice], part: str, path: str
             ) -> tuple[str, spoken_thread.Voice | None,
                        spoken_thread.Voice | None]:
    """(state, the newest question, the newest verified answer after it)."""
    if _unreadable(all_voices):
        return UNKNOWN, None, None
    key = (part, path)
    asked = max((i for i, v in enumerate(all_voices)
                 if _marked(v, _QUESTION_LINE) == key), default=None)
    if asked is None:
        return NONE, None, None
    question = all_voices[asked]
    # A receipt whose check could not run may be his answer, or a newer one:
    # nothing is known, and that is never "not answered yet" (DRE-4153).
    if any(v.kind == spoken_thread.UNCHECKED for v in all_voices[asked + 1:]):
        return UNKNOWN, question, None
    answered = max((i for i, v in enumerate(all_voices) if i > asked
                    and v.kind == spoken_thread.CEO_VIA_CONSOLE), default=None)
    if answered is None:
        return ASKED, question, None
    answer = all_voices[answered]
    if any(_marked(v, _RECORD_LINE) == key
           for v in all_voices[answered + 1:]):
        return HANDED_BACK, question, answer
    return ANSWERED, question, answer


def state_of(all_voices: list[spoken_thread.Voice], part: str,
             path: str) -> str:
    """The one word for the newest question naming `part` at `path`."""
    return _reading(all_voices, part, path)[0]


def parts(all_voices: list[spoken_thread.Voice]) -> list[tuple[str, str]]:
    """Every (part, path) a question on the thread names, in the order of its
    newest question."""
    newest: dict[tuple[str, str], int] = {}
    for i, voice in enumerate(all_voices):
        key = _marked(voice, _QUESTION_LINE)
        if key:
            newest[key] = i
    return sorted(newest, key=newest.__getitem__)


def handback_record(part: str, path: str, number: int, sha: str) -> str:
    """The hand-back record, as the contract writes it."""
    return (f"{HANDBACK_MARK} `{part}` at `{path}` — re-review of PR "
            f"#{number} asked at {(sha or '')[:8]}")


def review_workflow(repo: str) -> str:
    """The review a re-review dispatches — `report_fix_result.sh`'s choice."""
    return "pr-review.yml" if repo == SELF_REPO else "qa-review.yml"


def _why(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _read(card: str) -> tuple[list[spoken_thread.Voice] | None, str]:
    """(the thread, "") or (None, why it could not be read)."""
    if not os.environ.get("LINEAR_API_KEY"):
        return None, "this step has no Linear key"
    try:
        heard = spoken_thread.read(card)
    except Exception as exc:  # noqa: BLE001 — said, never read as no question
        return None, f"could not read {card}'s comments — {_why(exc)}"
    if _unreadable(heard):
        return None, ("Linear did not say who this key is, so the pipeline's "
                      "question cannot be told from a copy of it")
    return heard, ""


def _gh(args: list[str], *, token: str | None = None) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) of one `gh` call."""
    env = dict(os.environ, GH_TOKEN=token) if token else None
    done = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", *args], capture_output=True, text=True, check=False, env=env)
    return done.returncode, done.stdout, done.stderr


def _pr_list(cmd: list[str]) -> str:
    code, out, err = _gh(cmd)
    if code != 0:
        raise card_pr.PrLookupError(
            f"gh {' '.join(cmd)} failed rc={code}: {err.strip()[:400]}")
    return out


def _flat(text: str, limit: int = 300) -> str:
    return " ".join((text or "").split())[:limit]


# --------------------------------------------------------------------------- #
# the three commands — each returns (stdout, the status line after the tag)   #
# --------------------------------------------------------------------------- #

def read_state(card: str, part: str, path: str) -> tuple[str, str]:
    where = f"`{part}` at `{path}`"
    if not card:
        return NONE, f"no card — {NO_CARD}; read nothing, wrote nothing"
    heard, why = _read(card)
    if heard is None:
        return UNKNOWN, f"{card}: {where} is {UNKNOWN} — {why}; wrote nothing"
    word = state_of(heard, part, path)
    return word, (f"{card}: {where} is {word} — read {len(heard)} "
                  "comment(s); wrote nothing")


def _context_lines(card: str, heard: list[spoken_thread.Voice]) -> list[str]:
    found = parts(heard)
    if not found:
        return [f"{CONTEXT} none — no design question on {card}"]
    out: list[str] = []
    for part, path in found:
        word, question, answer = _reading(heard, part, path)
        where = f"`{part}` at `{path}`"
        if word == ASKED:
            asked = spoken_thread.pacific_label(question.created_at)
            out.append(f"{CONTEXT} asked — {where}, asked {asked}, not "
                       "answered yet")
        elif word == UNKNOWN:
            out.append(f"{CONTEXT} UNKNOWN — {where}: {UNCHECKED_ANSWER}")
        else:
            out.append(f"{CONTEXT} answered — {where}, {answer.label}")
            out += escalation_answers._quoted(escalation_answers._said(answer))
    return out


def read_context(card: str) -> tuple[str, str]:
    if not card:
        return (f"{CONTEXT} none — {NO_CARD}",
                f"no card — {NO_CARD}; read nothing, wrote nothing")
    heard, why = _read(card)
    if heard is None:
        return (sanitize_untrusted.sanitize_body(f"{CONTEXT} UNKNOWN — {why}"),
                f"{card}: UNKNOWN — {why}; wrote nothing")
    lines = _context_lines(card, heard)
    said = "; ".join(f"`{p}` at `{q}` {state_of(heard, p, q)}"
                     for p, q in parts(heard)) or "no design question"
    return (sanitize_untrusted.sanitize_body("\n".join(lines)),
            f"{card}: {said} — read {len(heard)} comment(s); wrote nothing")


def hand_back(card: str, repo: str) -> str:
    if not card:
        return f"no card — {NO_CARD}; read nothing, wrote nothing"
    heard, why = _read(card)
    if heard is None:
        return f"{card}: UNKNOWN — {why}; wrote nothing"
    states = {key: state_of(heard, *key) for key in parts(heard)}
    answered = [key for key, word in states.items() if word == ANSWERED]
    if not answered:
        said = "; ".join(f"`{p}` at `{q}` {w}"
                         for (p, q), w in states.items()) or "no design question"
        return f"{card}: {said} — nothing to hand back; wrote nothing"
    if not repo:
        return f"{card}: answered, but no repository was named; wrote nothing"
    try:
        pr = card_pr.find(card, repo=repo, fields=_PR_FIELDS, run=_pr_list)
    except card_pr.PrLookupError as exc:
        return (f"{card}: answered, but its pull request could not be looked "
                f"up — {_flat(str(exc))}; wrote nothing")
    if card_pr.pr_state(pr) != card_pr.OPEN:
        return (f"{card}: answered, but there is no open pull request for it "
                f"in {repo}; wrote nothing")
    number, sha = pr.get("number"), pr.get("headRefOid") or ""
    workflow = review_workflow(repo)
    code, _, err = _gh(["workflow", "run", workflow, "--repo", repo, "-f",
                        f"pr_number={number}"],
                       token=os.environ.get("DISPATCH_TOKEN") or None)
    if code != 0:
        return (f"{card}: answered, but the dispatch of {workflow} for PR "
                f"#{number} was refused — {_flat(err) or f'gh exit {code}'} "
                "(the calling stub needs `actions: write`); wrote nothing")
    wrote = [f"dispatched {workflow} for PR #{number}"]
    for part, path in answered:
        body = handback_record(part, path, number, sha)
        try:
            capped = linear_ops.cmd_comment(card, body)
        except Exception as exc:  # noqa: BLE001 — named; the next run re-reads
            wrote.append(f"could not post the record for `{part}` — "
                         f"{_flat(_why(exc))}")
            continue
        wrote.append(f"could not post the record for `{part}` — {capped}"
                     if capped else f"posted the record for `{part}`")
    try:
        moved = linear_ops.cmd_advance(card, IN_REVIEW, IN_PROGRESS)
    except Exception as exc:  # noqa: BLE001 — named, never a red run
        wrote.append(f"could not move it to {IN_REVIEW} — {_flat(_why(exc))}")
    else:
        wrote.append(f"moved it from {IN_PROGRESS} to {IN_REVIEW}" if moved
                     else f"moved nothing — it is not in {IN_PROGRESS}")
    return f"{card}: answered at {sha[:8]} — " + "; ".join(wrote)


HELP = ("Every command prints one `design-answer:` line on stderr and always "
        "exits 0: a card with no question is the common case, and a thread "
        "that cannot be read says so.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="design_answer.py",
        description="The critic's design question on a card, the CEO's "
                    f"answer to it, and its one hand-back. {HELP}")
    sub = parser.add_subparsers(dest="command", required=True)
    state = sub.add_parser("state", help="one word for one part's question")
    state.add_argument("card")
    state.add_argument("--part", required=True)
    state.add_argument("--path", required=True)
    context = sub.add_parser("context", help="the critic's DESIGN ANSWER lines")
    context.add_argument("card")
    handback = sub.add_parser("handback", help="hand an answer back as one "
                                               "re-review")
    handback.add_argument("card")
    handback.add_argument("--repo", default="")
    args = parser.parse_args(argv)
    card = args.card.strip()
    out = ""
    with github_output.only_outputs():
        try:
            if args.command == "state":
                out, did = read_state(card, args.part, args.path)
            elif args.command == "context":
                out, did = read_context(card)
            else:
                did = hand_back(
                    card, args.repo.strip()
                    or os.environ.get("GITHUB_REPOSITORY", ""))
        except Exception as exc:  # noqa: BLE001 — said, never a red run
            if args.command == "state":
                out = UNKNOWN
            elif args.command == "context":
                out = f"{CONTEXT} UNKNOWN — {_flat(_why(exc))}"
            did = f"{card or 'no card'}: failed — {_flat(_why(exc))}"
    if out:
        print(out)
    print(f"{TAG} {did}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
