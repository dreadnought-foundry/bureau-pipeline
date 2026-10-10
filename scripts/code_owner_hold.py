#!/usr/bin/env python3
"""A merge GitHub refuses for a missing code-owner review is a HOLD — said
once, to the person named — not a "real failure" every pass (DRE-4341).

THE INCIDENT (2026-09-19, Portico #616, DRE-4177 — the portal favicons). The
pull request reworded one sentence under `docs/design/system/`, which
Portico's ruleset "Sid reviews atoms and the design system" (ruleset
23194028, `require_code_owner_review: true`) holds for the review of the
owner `.github/CODEOWNERS` names. CI was green and the critic's APPROVE was
bound to the head, so the gate decided `merge`; GitHub answered "the base
branch policy prohibits the merge", and the step printed "real failure" and
exited 1 — five times over about nine hours (runs 35465747760, 35465755335,
35472731884, 35479704383, 35485207923). Nothing was posted on the pull
request or the card, and the medic filed a wrong diagnosis into the CEO's
queue. It merged within two minutes of the review.

This module is everything the gate needs to see that coming BEFORE the
merge, and to say so:

  * `gather` (the one seam to the network, never raises) reads the base
    branch's rules, the pull request's reviews and the base branch's
    CODEOWNERS, and writes them beside the pull request's files into one
    record;
  * `read_owners` (pure) decides over that record: MET, UNMET,
    NOT_REQUIRED or UNKNOWN. `merge_gate.decide`'s condition O holds on
    UNMET and nothing else — every read that failed is UNKNOWN, which merges
    as before and so falls back to today's loud failure, never to a silent
    hold and never to a Green Light park;
  * `hold` posts ONE note on the pull request per head (through
    `gate_note.py`, like the gate's other hold arms) and parks the card
    `In Review` → `Green Light` once, with the same sentence — but only when
    the calling repo's merge-gate stub wakes the gate on a submitted review.
    The sweep dispatches nothing for a card in Green Light, so without that
    trigger the park would switch off the card's only wake;
  * `release` moves a card the gate parked back to `In Review` once the
    review has landed, before the merge path runs — only a card carrying the
    gate's own hold marker, written by the pipeline;
  * `explain` names what was and was not satisfied when some OTHER policy
    refusal fails the step, so the medic diagnoses from facts.

Owners record shape (what `gather` writes and `read_owners` reads):

    {"rules": [<GET rules/branches/{base} entries>] | null,
     "reviews": [<GET pulls/{pr}/reviews entries>] | null,
     "codeowners": {"path": ".github/CODEOWNERS" | null, "text": str} | null,
     "files": ["path", …] | null}

`null` is a read that failed. With no code-owner rule in force the other
three are never read — the ordinary case costs one call.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from dataclasses import dataclass

import console_escalation

# The gh seam is stranded_fix's — `(stdout, None)` or `(None, detail)`, never
# an exception — the same one stacked_prs.py gathers through.
from stranded_fix import SELF_HOST_REPO, _gh  # noqa: F401  (patched in tests)

# The check-runs record in every shape the gate writes it (DRE-6532).
from unfixable_checks import _check_runs

MET = "met"
UNMET = "unmet"
NOT_REQUIRED = "not_required"
UNKNOWN = "unknown"

#: Where GitHub looks for CODEOWNERS, in the order it looks (the first one
#: found is the only one read).
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")

#: GitHub's cap on the compare record's `files[]`, which it does not
#: paginate — a record at the cap may be missing a file an owner owns.
MAX_COMPARE_FILES = 300

#: The lanes the park and the release move between.
REVIEW_LANE = "In Review"
PARK_LANE = "Green Light"

#: The park's answer on the card's Recommendation line (DRE-5204). There is
#: nothing to choose between — the rule names the one review that releases it.
RECOMMENDATION = "approve the pull request"
RECOMMENDATION_WHY = (
    "its checks and its review have passed, and the only thing holding it is "
    "the code-owner rule this repository sets"
)

#: The PR note's marker, per head. The sha sits inside an HTML comment, so it
#: keys the note without showing in it.
_NOTE_MARKER = "Merge gate: code-owner hold <!-- head {sha}"

#: The card comment's first line. Visible on purpose — Linear shows it, and
#: the console reads it (a sibling card) — and it carries no sha.
_CARD_MARKER = "merge-gate: code-owner {kind} · PR #{pr}"
_CARD_MARKER_RE = re.compile(
    r"^\s*(?:[^\w\s>]{1,4}\s+)?merge-gate: code-owner (hold|release) · PR #(\d+)\b"
)


# --------------------------------------------------------------------------
# CODEOWNERS, as GitHub reads it
# --------------------------------------------------------------------------
def parse_codeowners(text: str) -> list:
    """`[(pattern, (owner, …)), …]` in file order. Comments and blank lines
    are skipped; a line with a pattern and no owner is kept, because a later
    line with no owner CLEARS ownership of what it matches."""
    entries = []
    for line in (text or "").splitlines():
        tokens = line.split()
        if not tokens or tokens[0].startswith("#"):
            continue
        owners = []
        for token in tokens[1:]:
            if token.startswith("#"):
                break
            owners.append(token)
        entries.append((tokens[0], tuple(owners)))
    return entries


def _pattern_re(pattern: str) -> re.Pattern:
    """gitignore-style matching, as GitHub applies it to CODEOWNERS.

    A leading `/`, or a `/` anywhere but the end, anchors the pattern to the
    repository root; otherwise it matches at any depth. A trailing `/` is a
    directory and owns everything under it. A pattern naming a directory
    without the slash owns everything under it too — except one whose last
    segment is a single `*`, which GitHub documents as not descending
    (`docs/*` owns `docs/a.md`, not `docs/b/c.md`)."""
    dir_only = pattern.endswith("/")
    body = pattern.strip("/")
    anchored = pattern.startswith("/") or "/" in body
    out, i = [], 0
    while i < len(body):
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif body.startswith("**", i):
            out.append(".*")
            i += 2
        elif body[i] == "*":
            out.append("[^/]*")
            i += 1
        elif body[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    if dir_only:
        tail = "/.*"
    elif body.endswith("/*"):
        tail = ""
    else:
        tail = "(?:/.*)?"
    return re.compile(("^" if anchored else "^(?:.*/)?") + "".join(out) + tail + "$")


def owners_of(path: str, entries) -> tuple | None:
    """`(folder, owners)` for the LAST line matching `path` — GitHub's rule —
    where `folder` is the pattern as a person reads it (no leading slash).
    `owners` is empty when that line clears ownership. None when no line
    matches."""
    hit = None
    for pattern, owners in entries:
        if _pattern_re(pattern).match(path):
            hit = (pattern.lstrip("/"), owners)
    return hit


# --------------------------------------------------------------------------
# the reading the gate decides on
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Reading:
    """`state` is MET / UNMET / NOT_REQUIRED / UNKNOWN. `groups` names, on
    UNMET, whose review is missing and for which folders:
    `(((owner, …), (folder, …)), …)`. `detail` says why on UNKNOWN."""

    state: str
    groups: tuple = ()
    detail: str = ""


def unknown(detail: str) -> Reading:
    return Reading(UNKNOWN, detail=detail)


def _code_owner_rule(rules) -> bool:
    return any(
        isinstance(rule, dict) and rule.get("type") == "pull_request"
        and (rule.get("parameters") or {}).get("require_code_owner_review") is True
        for rule in rules
    )


def _approvers(reviews, author: str) -> set:
    """Logins (lower-cased) whose LATEST standing review approves. A comment
    does not withdraw an approval; a later change request or a dismissal
    does. The author's own review never counts, as on GitHub."""
    latest = {}
    ordered = sorted(
        (r for r in reviews if isinstance(r, dict)),
        key=lambda r: r.get("id") or 0,
    )
    for r in ordered:
        login = ((r.get("user") or {}).get("login") or "").lower()
        state = (r.get("state") or "").upper()
        if login and state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[login] = state
    return {
        login for login, state in latest.items()
        if state == "APPROVED" and login != (author or "").lower()
    }


def _is_person(owner: str) -> bool:
    return owner.startswith("@") and "/" not in owner


def read_owners(record, author: str) -> Reading:
    """The pure decision over the record `gather` writes (see the module
    docstring for its shape)."""
    if not isinstance(record, dict):
        return unknown("the owners record is not an object")
    rules = record.get("rules")
    if not isinstance(rules, list):
        return unknown("the base branch's rules could not be read")
    if not _code_owner_rule(rules):
        return Reading(NOT_REQUIRED)
    reviews = record.get("reviews")
    if not isinstance(reviews, list):
        return unknown("the pull request's reviews could not be read")
    codeowners = record.get("codeowners")
    if not isinstance(codeowners, dict):
        return unknown("the base branch's CODEOWNERS could not be read")
    if not codeowners.get("path") or not (codeowners.get("text") or "").strip():
        return unknown("a code-owner review is required but no CODEOWNERS file was found")
    files = record.get("files")
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        return unknown("the pull request's changed files could not be read in full")

    entries = parse_codeowners(codeowners["text"])
    approvers = _approvers(reviews, author)
    unmet, unprovable = {}, []
    for path in files:
        hit = owners_of(path, entries)
        if not hit or not hit[1]:
            continue
        folder, owners = hit
        if any(_is_person(o) and o[1:].lower() in approvers for o in owners):
            continue
        # A team or an e-mail owner cannot be checked against the approvals
        # here: with someone's approval on the pull request it may be met.
        if approvers and not all(_is_person(o) for o in owners):
            unprovable.append(path)
            continue
        folders = unmet.setdefault(owners, [])
        if folder not in folders:
            folders.append(folder)
    if unmet:
        return Reading(UNMET, tuple((o, tuple(f)) for o, f in unmet.items()))
    if unprovable:
        return unknown(
            "an approval is on the pull request but whether it is a code "
            f"owner's cannot be proved here ({', '.join(unprovable[:3])})"
        )
    return Reading(MET)


# --------------------------------------------------------------------------
# the words — for a non-technical reader
# --------------------------------------------------------------------------
def _join(items, last: str) -> str:
    items = list(items)
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} {last} {items[-1]}"


def hold_sentence(pr, groups) -> str:
    """Who, which folder, what to press. No sha, no diff, no verdict text."""
    parts = [
        f"{_join(owners, 'or')} for {_join(folders, 'and')}"
        for owners, folders in groups
    ]
    return (
        f"Pull request #{pr} has passed its checks and its review, but this "
        f"repository's rules also need a review from {_join(parts, 'and from')} "
        "before GitHub will merge it. To release it, open the pull request, "
        "choose Review changes, pick Approve and submit — it then merges on its own."
    )


def pr_note(head_sha: str, sentence: str) -> tuple:
    """`(marker, body)` for the note on the pull request. The marker is per
    head and opens the first line, as `gate_note.py` requires."""
    marker = _NOTE_MARKER.format(sha=head_sha)
    return marker, f"⏸️ {marker} --> — {sentence}\n"


def card_comment(pr, sentence: str) -> str:
    """The hold marker, the sentence, then the three Green Light lines
    (DRE-5204). The Finding is the sentence's first sentence, cut from it
    rather than rebuilt, so the two can never say different things."""
    head, sep, _ = sentence.partition(". ")
    lines = console_escalation.render(console_escalation.Escalation(
        finding=head + "." if sep else sentence,
        question=(f"Open pull request #{pr} and submit an approving review "
                  "so it merges, or leave it waiting?"),
        recommendation=RECOMMENDATION,
        why=RECOMMENDATION_WHY,
    ))
    return f"⏸️ {_CARD_MARKER.format(kind='hold', pr=pr)}\n\n{sentence}\n\n{lines}"


def release_comment(pr) -> str:
    return (
        f"▶️ {_CARD_MARKER.format(kind='release', pr=pr)}\n\n"
        f"The review pull request #{pr} was waiting on has landed, so the card "
        "is back in review and the merge gate merges it on its own."
    )


def latest_gate_marker(comments, pr) -> str | None:
    """`hold` or `release` — the LATEST gate marker for this pull request
    among the comments the pipeline wrote — or None. A marker anyone else
    wrote is not the gate's."""
    latest = None
    for c in comments or ():
        if not isinstance(c, dict) or c.get("authored_by_pipeline") is not True:
            continue
        m = _CARD_MARKER_RE.match((c.get("body") or "").split("\n", 1)[0])
        if m and m.group(2) == str(pr):
            latest = m.group(1)
    return latest


# --------------------------------------------------------------------------
# does the calling repo's stub wake the gate on a review?
# --------------------------------------------------------------------------
def stub_path(repo: str) -> str:
    """The calling repo's merge-gate stub. In bureau-pipeline
    `merge-gate.yml` IS the reusable, so its stub is `self-merge-gate.yml`."""
    name = "self-merge-gate.yml" if repo == SELF_HOST_REPO else "merge-gate.yml"
    return f".github/workflows/{name}"


def _triggers(text: str) -> list:
    """The event names in a workflow's top-level `on:` block. A line reader,
    not a YAML parser, so the gate's step needs nothing beyond the standard
    library; it reads the three shapes a stub is written in."""
    lines = [ln.split(" #", 1)[0].rstrip() for ln in (text or "").splitlines()]
    for i, line in enumerate(lines):
        m = re.match(r"""^(?:on|"on"|'on')\s*:\s*(.*)$""", line)
        if not m:
            continue
        inline = m.group(1).strip()
        if inline:
            return re.findall(r"[A-Za-z_]+", inline.strip("[]{}"))
        # The block's own keys (or list items) sit at its shallowest indent;
        # anything deeper is an event's settings.
        names = []
        for sub in lines[i + 1:]:
            if not sub.strip() or sub.lstrip().startswith("#"):
                continue
            if not sub[0].isspace():
                break
            item = re.match(r"^(\s+)(?:-\s+)?([A-Za-z_]+)\b", sub)
            if item:
                names.append((len(item.group(1)), item.group(2)))
        indent = min((n for n, _ in names), default=0)
        return [name for n, name in names if n == indent]
    return []


def stub_wakes_on_review(text: str) -> bool:
    return "pull_request_review" in _triggers(text)


# --------------------------------------------------------------------------
# the gatherer — one seam to the network, never raises
# --------------------------------------------------------------------------
def _pages(out) -> list | None:
    try:
        data = json.loads(out)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    if all(isinstance(page, list) for page in data):
        return [item for page in data for item in page]
    return None


def _changed_files(compare) -> list | None:
    files = compare.get("files") if isinstance(compare, dict) else None
    if not isinstance(files, list) or len(files) >= MAX_COMPARE_FILES:
        return None
    names = [f.get("filename") for f in files if isinstance(f, dict)]
    if len(names) != len(files) or not all(isinstance(n, str) for n in names):
        return None
    return names


def _read_codeowners(repo: str, base: str) -> dict | None:
    for path in CODEOWNERS_PATHS:
        out, detail = _gh(["api", f"repos/{repo}/contents/{path}?ref={base}"])
        if out is None:
            if "404" in (detail or ""):
                continue
            print(f"code_owner_hold: reading {path} failed: {detail}", file=sys.stderr)
            return None
        try:
            payload = json.loads(out)
            text = base64.b64decode(payload["content"]).decode("utf-8")
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            return None
        return {"path": path, "text": text}
    return {"path": None, "text": ""}


def gather_owners(repo: str, pr, base: str, compare) -> dict:
    """The record merge-gate.yml hands `--owners-file`. Never raises."""
    record = {"rules": None, "reviews": None, "codeowners": None,
              "files": _changed_files(compare)}
    out, detail = _gh(["api", "--paginate", "--slurp",
                       f"repos/{repo}/rules/branches/{base}"])
    record["rules"] = _pages(out) if out is not None else None
    if record["rules"] is None:
        print(f"code_owner_hold: the rules for {base} are unreadable: {detail}",
              file=sys.stderr)
        return record
    if not _code_owner_rule(record["rules"]):
        return record
    out, detail = _gh(["api", "--paginate", "--slurp",
                       f"repos/{repo}/pulls/{pr}/reviews?per_page=100"])
    record["reviews"] = _pages(out) if out is not None else None
    if record["reviews"] is None:
        print(f"code_owner_hold: #{pr}'s reviews are unreadable: {detail}",
              file=sys.stderr)
        return record
    record["codeowners"] = _read_codeowners(repo, base)
    return record


# --------------------------------------------------------------------------
# the card: park once, release only what the gate parked
# --------------------------------------------------------------------------
def park(card: str, pr, sentence: str) -> None:
    """`In Review` → `Green Light`, with the sentence and the hold marker.
    The comment goes first, so a move that fails is retried on the next wake
    without a second comment; a card already in Green Light — parked by the
    gate or by anyone else — is left alone."""
    import linear_ops  # the Linear seam is only needed on this path

    state = linear_ops.get_issue(card)["state"]["name"]
    if state == PARK_LANE:
        print(f"code_owner_hold: {card} is already in {PARK_LANE} — nothing to do")
        return
    if state != REVIEW_LANE:
        print(f"code_owner_hold: {card} is in {state!r}, not {REVIEW_LANE!r} — not parking it")
        return
    if latest_gate_marker(linear_ops.comment_records(card), pr) != "hold":
        linear_ops.cmd_comment(card, card_comment(pr, sentence))
    linear_ops.cmd_advance(card, PARK_LANE, REVIEW_LANE)


def release(card: str, pr) -> None:
    """`Green Light` → `In Review` — only when the pipeline's own LATEST
    marker for this pull request is the hold. The move goes first: a comment
    that fails costs a line of history, a release marker with no move would
    strand the card where the sweep never looks."""
    import linear_ops

    state = linear_ops.get_issue(card)["state"]["name"]
    if state != PARK_LANE:
        return
    if latest_gate_marker(linear_ops.comment_records(card), pr) != "hold":
        print(f"code_owner_hold: {card} is in {PARK_LANE} but the gate did not "
              "park it — leaving it where it is")
        return
    linear_ops.cmd_advance(card, REVIEW_LANE, PARK_LANE)
    linear_ops.cmd_comment(card, release_comment(pr))


# --------------------------------------------------------------------------
# a refusal that is not this one: name what was and was not satisfied
# --------------------------------------------------------------------------
def explain(record, check_runs, author: str) -> list:
    """One line per requirement the base branch's rules set, and whether it
    is met — the facts a refused merge's log needs."""
    # Imported here, not above: merge_gate imports this module.
    from merge_gate import GREEN_CONCLUSIONS

    lines = []
    rules = record.get("rules") if isinstance(record, dict) else None
    if not isinstance(rules, list):
        lines.append("base branch rules: UNKNOWN (unreadable)")
        rules = []
    runs = {}
    for run in check_runs or ():
        if isinstance(run, dict) and run.get("name"):
            runs.setdefault(run["name"], run)
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        params = rule.get("parameters") or {}
        if rule.get("type") == "required_status_checks":
            for check in params.get("required_status_checks") or ():
                name = (check or {}).get("context")
                run = runs.get(name) or {}
                ok = (run.get("status") == "completed"
                      and run.get("conclusion") in GREEN_CONCLUSIONS)
                lines.append(f"required check {name!r}: "
                             f"{'satisfied' if ok else 'NOT satisfied'}")
        elif rule.get("type") == "pull_request":
            lines.append("approving reviews required: "
                         f"{params.get('required_approving_review_count', 0)}")
        elif rule.get("type"):
            lines.append(f"rule in force: {rule['type']}")
    reading = read_owners(record, author)
    words = {MET: "satisfied", UNMET: "NOT satisfied", NOT_REQUIRED: "not required"}
    lines.append(f"code-owner review: "
                 f"{words.get(reading.state) or f'UNKNOWN ({reading.detail})'}")
    return lines


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _cmd_gather(args) -> int:
    record = gather_owners(args.repo, args.pr, args.base, _load(args.compare_file))
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    return 0


def _cmd_hold(args) -> int:
    import gate_note

    groups = tuple(
        (tuple(g["owners"]), tuple(g["folders"])) for g in json.loads(args.groups)
    )
    sentence = hold_sentence(args.pr, groups)
    marker, body = pr_note(args.head_sha, sentence)
    result = gate_note.post_once(
        gate_note.GateComments(args.repo, args.pr), marker, body, args.author
    )
    print(f"code-owner hold note: {result['action']} (id={result['id']})")
    if not args.card:
        return 0
    stub = stub_path(args.repo)
    try:
        with open(stub, encoding="utf-8") as fh:
            wakes = stub_wakes_on_review(fh.read())
    except OSError:
        wakes = False
    if not wakes:
        print(f"code-owner hold: park skipped — {stub} declares no "
              "pull_request_review trigger, so it cannot wake the gate on a "
              f"review; {args.card} stays in {REVIEW_LANE}, where the sweep's "
              "nudge still reaches it")
        return 0
    try:
        park(args.card, args.pr, sentence)
    except Exception as exc:  # a Linear blip must not red the hold
        print(f"code-owner hold: parking {args.card} failed ({exc}) — "
              "it stays where it is and the next wake retries")
    return 0


def _cmd_release(args) -> int:
    try:
        release(args.card, args.pr)
    except Exception as exc:  # the merge path runs whatever happens here
        print(f"code-owner hold: releasing {args.card} failed ({exc})")
    return 0


def _cmd_explain(args) -> int:
    # Every page the gate's read wrote (DRE-6532); unreadable is no runs.
    try:
        check_runs = _check_runs(_load(args.check_runs_file))
    except ValueError:
        check_runs = None
    for line in explain(_load(args.owners_file), check_runs, args.author):
        print(f"requirement: {line}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("gather", help="write the merge gate's owners record")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--compare-file", required=True,
                   help="the compare record the gate already fetched — its "
                        "files[] are the pull request's changed paths")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=_cmd_gather)

    p = sub.add_parser("hold", help="note the hold on the PR and park the card")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True)
    p.add_argument("--author", required=True, help="the gate's own login")
    p.add_argument("--head-sha", required=True)
    p.add_argument("--card", default="")
    p.add_argument("--groups", required=True,
                   help="merge_gate.py's owner_hold= JSON")
    p.set_defaults(fn=_cmd_hold)

    p = sub.add_parser("release", help="release a card the gate parked")
    p.add_argument("--card", required=True)
    p.add_argument("--pr", required=True)
    p.set_defaults(fn=_cmd_release)

    p = sub.add_parser("explain", help="name what a policy refusal left unmet")
    p.add_argument("--owners-file", required=True)
    p.add_argument("--check-runs-file", required=True)
    p.add_argument("--author", default="")
    p.set_defaults(fn=_cmd_explain)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
