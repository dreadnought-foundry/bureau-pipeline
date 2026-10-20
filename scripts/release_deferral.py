#!/usr/bin/env python3
"""A deferred release is loud (DRE-5235, epic DRE-5234).

THE GAP. A surface script that exits 0 printing `deferred: …` is a no-op, and
a no-op concludes the job green (`standards/release-train.md`, rule 6). On
2026-09-22 Portico's `portals` surface deferred seven times, every run green,
and the CEO found out by asking why a portal was behind. The surface script
cannot file anything itself — Portico's `release-portals.sh` unsets
`LINEAR_API_KEY` for its own process — but the train's process holds the key:
`release-train.yml`'s "Run the surface" step passes it through.

WHAT THIS DOES. `report` is called from `release_train._cmd_release` alone,
once the decision is final, beside the decision record. Two decisions reach it:

  * **A deferral with no re-arm minute** (code `deferred`, `re_arm_at` None).
    It prints a `Release deferred` warning, appends a `## Deferred` block to
    the run's summary page and prints one `release-deferred:` line carrying
    the same facts, because a proof run reads a run's log and cannot read its
    summary page. Then `file_deferral` files, best effort, ONE card per repo
    and surface in Green Light — created there in the one write that mints
    it, as `reconcile.ask_epic_growth_question` creates its question — or
    comments on the open one when the reason's fingerprint changed, or files
    nothing when the surface's own train already filed a card for it.
  * **A verified tag** (act `release`, a tag). `close_deferral` finds the open
    card by the same prefix, comments `Released as <tag>` and moves it to
    Done, whatever lane it sits in by then.

A deferral that names its minute (DRE-6005, ` — re-arm in <N> minutes`) is the
train's own to re-arm, owed to nobody, and gets none of this. Neither does a
window or spacing no-op, which `decide()` emits itself.

WHAT IT NEVER DOES. Change the decision, its receipt, its record or the job's
conclusion. Every Linear failure — no key, an API error, the budget — and a
repository `config/repo-map.json` does not name is one `::warning::` and one
`release-deferred: card not filed — <why>` line. A deferral is not a pipeline
failure, and the medic must not retry it.

DEFERRALS THAT ALREADY HAVE A CARD (DRE-5464). Portico's train defers on two
causes of its own and files its own card for them: a `RELEASE ROLLED BACK:`
or `RELEASE BROKEN:` card, either of which holds every account's release
until a person closes it (DRE-5584). While one is open on the repo the
deferral is that card's: the run is still loud, and no card is filed and no
comment added. The card is found by its title and its `repo:` label, never by
the deferred line's wording, which is not readable from this repository.

THE FINGERPRINT. `deferral_fingerprint` decides whether a reason changed, with
times, commit ids and every other free-standing number replaced, and Linear's
markdown escapes undone (DRE-7744). The cost is named on the card: two reasons
that differ only in a number are one reason — a count that grew included —
and each lap's `release-deferred:` line still carries the live reason verbatim.

NO IMPORT OF `release_train`. The train imports this module; importing it
back would be a cycle. The three strings shared with the train are written
here and bound to the train's own constants by
`tests/test_release_deferral.py`, and the train hands `report` its own
`_warning` and `_step_summary` helpers.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear_ops  # noqa: E402

#: The tag every line this module prints opens with — the one a proof run
#: reads off the run's log (`config/proof-reach.json`).
TAG = "release-deferred"
#: The warning's title on the run.
WARNING_TITLE = "Release deferred"
#: The heading of the block on the run's summary page.
SUMMARY_HEADING = "Deferred"

#: The lane the card is created in — the CEO's "needs you" queue, as a notice
#: (the `deferred-release` kind, DRE-7234). A module constant so the lane
#: writers' discovery reads the destination (`ready_lane_writers.writes()`).
DEFERRAL_LANE = "Green Light"
#: Where the next verified tag closes it.
CLOSED_LANE = "Done"
#: The labels after `repo:<repo>`: a person finishes it, nothing builds it.
CARD_LABELS = (linear_ops.NO_CODE_LABEL, "needs-human", "agent:ops")
#: How much of the reason the title carries.
TITLE_REASON = 80

#: The titles of the surface's own cards that stand in for this one. Portico's
#: `infra/scripts/release-hold.ts` files both (DRE-5469, its "lookup keys"),
#: and either holds every account's release until a person closes it
#: (DRE-5584). `RELEASE HOLD:` and `RELEASE UNCHECKED:` are not here: neither
#: holds the whole fleet, so a deferral beside one may have another cause.
SURFACE_CARD_PREFIXES = ("RELEASE ROLLED BACK:", "RELEASE BROKEN:")
#: The day DRE-5464 and DRE-5469 were filed. No card under either title is
#: older than the card that specified it, so the read has no age window to
#: fall out of: a rollback card open for months is still found.
SURFACE_CARD_SINCE = datetime(2026, 10, 1, tzinfo=UTC)
#: The states a surface card is closed in.
CLOSED_STATES = ("Done", "Canceled", "Duplicate")

#: The one fixed form this module writes a deferred line in, on the card's
#: description and on every comment it adds, and alone parses back.
LINE_MARK = "Deferred line: "

#: Shared with `release_train` (no import — see the docstring).
DEFERRAL_PREFIX = "deferred:"
DEFERRED_CODE = "deferred"
RELEASE_ACT = "release"

REPO_MAP = Path(__file__).resolve().parent.parent / "config" / "repo-map.json"

_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"
                   r"(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")
_SHA = re.compile(r"(?<![A-Za-z0-9])[0-9a-fA-F]{7,40}(?![A-Za-z0-9])")
_NUMBER = re.compile(r"(?<![A-Za-z0-9_-])\d+(?:\.\d+)?(?:ms|s|m|h|d)?"
                     r"(?![A-Za-z0-9_-])")


def deferral_fingerprint(reason: str) -> str:
    """The reason with what changes lap to lap replaced, in this order: a
    markdown backslash escape — a `\\` before an ASCII punctuation character
    — is undone, an ISO-8601 timestamp is `<time>`, a run of 7 to 40 hex
    characters with no letter or digit on either side is `<sha>`, and every
    other free-standing number — digits, an optional decimal part, an
    optional unit `ms`, `s`, `m`, `h` or `d`, with no letter, digit, `_` or
    `-` on either side — is `<n>`. A number joined to a word by a hyphen
    stays: `d-2024-plan` and `d-2025-plan` are two documents.

    The escapes are Linear's (DRE-7744): it stores a description as markdown,
    so the line written as `/home/runner/_work/…` reads back as
    `/home/runner/\\_work/…`, while a comment reads back as written. Both
    sides of the comparison pass through here, so a reason read off either
    matches the lap's."""
    text = _ESCAPE.sub(r"\1", reason)
    text = _TIME.sub("<time>", text)
    text = _SHA.sub("<sha>", text)
    return _NUMBER.sub("<n>", text)


def reason_of(line: str) -> str:
    """The deferred line with its `deferred:` and the whitespace after it
    removed."""
    line = line.strip()
    if line.startswith(DEFERRAL_PREFIX):
        line = line[len(DEFERRAL_PREFIX):]
    return line.strip()


def repo_key(full: str, path=None) -> str | None:
    """The `config/repo-map.json` key whose value is `full` (`owner/repo`), or
    None for a repository the map does not name."""
    mapping = json.loads(Path(path or REPO_MAP).read_text(encoding="utf-8"))
    for key, value in mapping.items():
        if str(value).casefold() == (full or "").casefold():
            return key
    return None


def run_url(repo: str, env=None) -> str:
    """`$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID`."""
    env = os.environ if env is None else env
    server = env.get("GITHUB_SERVER_URL") or "https://github.com"
    run = env.get("GITHUB_RUN_ID") or "unknown"
    return f"{server}/{env.get('GITHUB_REPOSITORY') or repo}/actions/runs/{run}"


def title_prefix(repo: str, surface: str) -> str:
    """What the open card is found by."""
    return f"{repo}: {surface} release deferred"


def title(repo: str, surface: str, reason: str) -> str:
    return f"{title_prefix(repo, surface)} — {reason[:TITLE_REASON]}"


def _facts(line: str, sha: str, url: str) -> str:
    return f"{LINE_MARK}{line}\nCommit: {sha}\nRun: {url}"


def body(line: str, sha: str, url: str) -> str:
    """The card's description: the deferred line verbatim, the commit and the
    run, and what a person does."""
    return (
        f"{_facts(line, sha, url)}\n\n"
        "Read the reason, run the tool it names, and the next lap releases.\n\n"
        "The release train closes this card itself on the next verified tag."
    )


def last_line(card: dict) -> str | None:
    """The newest deferred line the card records, read off its description
    and its comments (oldest to newest) in the one form this module writes."""
    found = None
    texts = [card.get("description") or ""]
    texts += [n.get("body") or "" for n in linear_ops.window_nodes(card.get("comments"))]
    for text in texts:
        for row in text.splitlines():
            if row.startswith(LINE_MARK):
                found = row[len(LINE_MARK):]
    return found


def surface_card(repo: str) -> dict | None:
    """The oldest open card of the surface's own train on this repo, or None.
    One paged read per prefix, back to `SURFACE_CARD_SINCE`."""
    open_cards = []
    for prefix in SURFACE_CARD_PREFIXES:
        for card in linear_ops.repo_cards_by_title_prefix(
                prefix, repo_slug=repo, since=SURFACE_CARD_SINCE):
            if card.get("state") not in CLOSED_STATES:
                open_cards.append(card)
    open_cards.sort(key=lambda c: c.get("createdAt") or "")
    return open_cards[0] if open_cards else None


def _open_cards(repo: str, surface: str, out) -> dict | None:
    """The oldest open card by the prefix, after printing every open match."""
    found = linear_ops.find_open_prefix(title_prefix(repo, surface))
    named = ([found["identifier"]]
             + [d["identifier"] for d in found.get("duplicates") or []]
             if found else [])
    out(f"{TAG}: open cards for {repo} {surface} — {', '.join(named) or 'none'}")
    return found


def file_deferral(*, repo: str, surface: str, line: str, sha: str, url: str,
                  out=print) -> None:
    """File the deferral: one card per repo and surface, created in Green
    Light — the one write site of the `deferred-release` kind. Raises on a
    Linear failure; `report` turns that into one warning and one line."""
    reason = reason_of(line)
    found = _open_cards(repo, surface, out)
    if found is not None:
        recorded = last_line(found)
        if recorded is not None and (deferral_fingerprint(reason_of(recorded))
                                     == deferral_fingerprint(reason)):
            out(f"{TAG}: card {found['identifier']} already open — same reason")
            return
    owner = surface_card(repo)
    if owner is not None:
        # A decision, not a failure: no second warning.
        out(f"{TAG}: card not filed — {owner['identifier']} ({owner['state']}) "
            "is open for this deferral")
        return
    out(f"{TAG}: surface-card check — none open on repo:{repo}")
    if found is not None:
        failed = linear_ops.cmd_comment(
            found["identifier"], f"The reason changed.\n\n{_facts(line, sha, url)}")
        if failed:
            raise linear_ops.LinearError(
                f"{found['identifier']} took no comment: {failed}")
        out(f"{TAG}: card {found['identifier']} commented — the reason changed")
        return
    named = title(repo, surface, reason)
    labels = (f"repo:{repo}", *CARD_LABELS)
    issue = linear_ops.create_card(named, body(line, sha, url), repo_slug=repo,
                                   labels=labels, lane=DEFERRAL_LANE)
    out(f'{TAG}: card {issue["identifier"]} created in {DEFERRAL_LANE} — '
        f'title "{named}" — labels {", ".join(labels)}')


def close_deferral(*, repo: str, surface: str, tag: str, out=print) -> None:
    """The surface cut a verified tag: close the open card, wherever it sits.
    With none open, nothing is written — the read is made all the same."""
    found = _open_cards(repo, surface, out)
    if found is None:
        return
    ident = found["identifier"]
    failed = linear_ops.cmd_comment(ident, f"Released as {tag}")
    if failed:
        raise linear_ops.LinearError(f"{ident} took no comment: {failed}")
    if not linear_ops.cmd_state(ident, CLOSED_LANE):
        raise linear_ops.LinearError(f"{ident} was not moved to {CLOSED_LANE}")
    out(f"{TAG}: card {ident} closed — Released as {tag}")


def _why(error: BaseException) -> str:
    return " ".join(f"{type(error).__name__}: {error}".split())


def report(decision, *, repo: str, surface: str, sha: str, warning,
           step_summary, out=print, env=None) -> None:
    """The train's one call, beside the decision record. Never raises.

    `warning(title, text, out=)` and `step_summary(heading, lines)` are the
    train's own helpers (`release_train._warning`, `_step_summary`)."""
    try:
        if (decision.code == DEFERRED_CODE and decision.re_arm_at is None):
            _deferred(decision, repo=repo, surface=surface, sha=sha,
                      warning=warning, step_summary=step_summary, out=out,
                      env=env)
        elif decision.act == RELEASE_ACT and decision.tag:
            _released(decision, repo=repo, surface=surface, warning=warning,
                      out=out)
    except Exception as error:  # noqa: BLE001 — a notice never fails a run
        out(f"{TAG}: [{surface}] WARNING the deferral notice failed "
            f"({_why(error)}) — the decision itself is unaffected")


def _deferred(decision, *, repo, surface, sha, warning, step_summary, out, env):
    line = decision.reason
    reason = reason_of(line)
    url = run_url(repo, env)
    key = repo_key(repo)
    named = key or repo
    warning(WARNING_TITLE, f"{surface} release deferred — {reason}", out=out)
    step_summary(SUMMARY_HEADING, [
        f"- Surface: `{surface}`",
        f"- Commit: `{sha}`",
        f"- Reason: {reason}",
        f"- Run: {url}",
    ])
    out(f"{TAG}: {named} {surface} at {sha[:7]} — {reason} — {url}")
    try:
        if key is None:
            raise LookupError(f"{repo} is not named in config/repo-map.json")
        file_deferral(repo=key, surface=surface, line=line, sha=sha, url=url,
                      out=out)
    except Exception as error:  # noqa: BLE001 — best effort, never a red run
        why = _why(error)
        warning(f"{WARNING_TITLE} card not filed", why, out=out)
        out(f"{TAG}: card not filed — {why}")


def _released(decision, *, repo, surface, warning, out):
    key = repo_key(repo)
    if key is None:
        # No card can exist for a repository the map does not name.
        return
    try:
        close_deferral(repo=key, surface=surface, tag=decision.tag, out=out)
    except Exception as error:  # noqa: BLE001 — the surface is already live
        why = _why(error)
        warning(f"{WARNING_TITLE} card not closed", why, out=out)
        out(f"{TAG}: card not closed — {why}")
