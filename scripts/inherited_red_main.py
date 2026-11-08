#!/usr/bin/env python3
"""A card the medic parked because `main` was red goes back once it is green (DRE-8849).

Origin (live, 2026-11-01). Four foundry cards — DRE-8069, DRE-8064, DRE-8068
and DRE-8074 — sat in Triage for four to eight hours with nothing wrong with
any of them. Each had an approved pull request that went red because
foundry's own `main` was broken. On DRE-8069 the medic's diagnosis came back
`unreadable`, its question was housekeeping, and the card was parked in Triage
under a `planning-stall-park` note. A card in Triage is human-parked to the
sweep, so the fix loop and the stale-merge-ref refresh stayed off its pull
request. `main` was green again by 9:22 PM PT and nothing moved the cards
back; an operator moved each one by hand at about 12:20 AM PT on 11-02.

A card parked because `main` was red is waiting on `main`, not on a person.
This module holds the two halves that say so, and they ship together — a hold
with no lifter would strand cards for good:

  * the MEDIC's read (`medic_read`, the `medic-read` command). `medic.yml`'s
    `Hold the card on an inherited red main` step reads the failed run's head
    and its merge base the way `agent-fix.yml`'s `Label failures inherited
    from the merge base` does. The answer is `hold` only when the head has at
    least one failing check (`stale_merge_ref.failing_set`, so review checks
    are left out) and every one of them is in
    `inherited_failures.inherited(head, base)` — the fix loop's own rule,
    with its strict merge-base read (DRE-8838). Anything else is `ask`, and
    the medic asks a person as it always has. On `hold` the step stamps
    `inherited-red-main` at the head and moves the card to Triage.
  * the SWEEP's return. `reconcile.return_inherited_red_main_holds` reads this
    repo's Triage cards wearing the hold (`held_cards`), and lifts the stamp
    when `stale_merge_ref.decide` answers `refresh` — every failing check was
    red on `main` and is green on a newer `main` commit — or when the pull
    request has a new head, which the medic's reading no longer describes.
    The card goes to In Review, and one note (`return_note`) says why.

Import-safe: no I/O at import, and no REPO or LINEAR_API_KEY read.

CLI:

    python3 inherited_red_main.py medic-read --checks-file <head check runs> \\
        --base-checks-file <merge-base check runs> \\
        [--base-sha SHA] [--repo OWNER/NAME] [--reason-out FILE]

Line 1 of stdout is `hold` or `ask`, and one line on stderr names the checks
and the merge base. Exit 0 on every answer: an unreadable payload is `ask`,
which is what the medic did before this step existed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hold  # noqa: E402 — the stamp is read through the hold's one reader
import inherited_failures  # noqa: E402 — the fix loop's "fails on both sides"
import linear_ops  # noqa: E402 — import-safe: no key read at import
import repo_held_epics  # noqa: E402 — the held-card read and the whole thread
import stale_merge_ref  # noqa: E402 — the failing set and the sweep's decision

#: The hold (config/holds.json): the reason, its lift kind and its writers.
HOLD_REASON = "inherited-red-main"
LIFT_KIND = "inherited-checks-green"
#: The other lift: something pushed to the branch since the medic read it.
NEW_HEAD = "new-head"
MEDIC_WRITER = "medic.yml"
WRITER = "scripts/reconcile.py"

#: The act and its tag (config/pipeline-acts.json). Written as a literal: the
#: act registry reads `X_TAG = "..."` constants straight off the emitter file.
ACT = "inherited-red-main-returned"
RETURN_TAG = "inherited-red-main-return"
NOTE_PREFIX = f"↩️ {RETURN_TAG}:"

#: The phrase config/pipeline-acts.json pins as this emitter's anchor. Named
#: once and used once, in `_closing`.
ANCHOR_PHRASE = "waited on `main`, not on a person"

#: Every line the sweep prints for this path opens with this.
LOG_PREFIX = "inherited-red-main:"

#: The two answers of the medic's read.
HOLD = "hold"
ASK = "ask"

#: Where the card waits, and where the sweep returns it.
TRIAGE = "Triage"
REVIEW = "In Review"


# --------------------------------------------------------------------------- #
# the medic's read                                                             #
# --------------------------------------------------------------------------- #


def medic_read(head_payload, base_payload) -> tuple[str, list, str]:
    """`(answer, failing check names, why)` for one failed run's head and its
    merge base. Pure. `hold` only when the head has a failing check and every
    one of them failed on the merge base too; `ask` for anything else."""
    try:
        names = stale_merge_ref.failing_set(head_payload)
    except ValueError as exc:
        return ASK, [], f"the head's check runs could not be read ({exc})"
    if not names:
        return ASK, [], "no failing check on the head"
    try:
        carried = inherited_failures.inherited(
            {"check_runs": [{"name": n, "conclusion": "failure"} for n in names]},
            base_payload)
    except ValueError as exc:
        return ASK, names, f"the merge base's check runs could not be read ({exc})"
    inherited = {_key(n) for n in carried}
    own = [n for n in names if _key(n) not in inherited]
    if own:
        return ASK, names, (f"{', '.join(own)} did not fail on the merge base — "
                            "this pull request's own")
    return HOLD, names, f"{', '.join(names)} failed on the merge base too"


def _key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def medic_reason(repo: str, names, base_sha: str) -> str:
    """The receipt's reason sentence on a hold: the repo, the failed checks,
    the merge base, and what brings the card back. One line."""
    checks = ", ".join(names)
    return (f"every failing check on the pull request — {checks} — failed on "
            f"{repo or 'the repository'}'s main at the merge base "
            f"`{(base_sha or 'unread')[:8]}` too, so the card waits in {TRIAGE} "
            f"on main, not on a person, and the sweep returns it to {REVIEW} "
            "once main is green again on those checks.")


# --------------------------------------------------------------------------- #
# the sweep's read                                                             #
# --------------------------------------------------------------------------- #


def held_cards(slug: str, *, gql=linear_ops.gql) -> list[dict]:
    """This repo's Triage cards wearing the hold whose live stamp is this
    one's, in one read plus one whole-thread read per partial window.

    `[{"identifier", "stamp"}]`, or `{"identifier", "unread"}` for a card
    whose thread could not be read whole. A card held for any other reason
    is not returned. A failed board read raises `linear_ops.LinearError`."""
    found = repo_held_epics._paged(gql, repo_held_epics.HELD_QUERY,
                                   {"needle": slug, "states": [TRIAGE]})
    cards = sorted((c for c in found if repo_held_epics._repo_of(c) == slug),
                   key=lambda c: repo_held_epics._number(c.get("identifier")))
    out: list[dict] = []
    for card in cards:
        identifier = card.get("identifier") or ""
        try:
            bodies = repo_held_epics._bodies(repo_held_epics._thread(card))
        except repo_held_epics._Unreadable as unread:
            out.append({"identifier": identifier, "unread": unread.what})
            continue
        stamp = hold_stamp(linear_ops._label_names(card), bodies)
        if stamp is not None:
            out.append({"identifier": identifier, "stamp": stamp})
    return out


def hold_stamp(labels, bodies) -> dict | None:
    """The card's live stamp when the card wears the hold for this reason."""
    if hold.reason_of(labels, bodies) != HOLD_REASON:
        return None
    return hold.read_stamp(bodies)


# --------------------------------------------------------------------------- #
# the note                                                                     #
# --------------------------------------------------------------------------- #


def return_note(card: str, *, pr_number, old_head: str, decision=None,
                new_head: str | None = None) -> str:
    """The body the sweep hands to `pipeline_act.receipt()` when it returns a
    held card to In Review. On a `refresh` decision it names each check, the
    merge base it was red on and the `main` commit it is green on; on a new
    head it names the old head and the new one."""
    old = (old_head or "")[:8]
    if new_head:
        new = new_head[:8]
        lines = [
            f"{NOTE_PREFIX} {card} is back in {REVIEW} — its pull request "
            f"#{pr_number} moved from `{old}` to `{new}`.",
            "",
            f"The medic held this card at `{old_head}` because every failing "
            "check on that head was red on `main` too. Something was pushed "
            f"since, and the head is now `{new_head}`, so that reading no "
            "longer describes the pull request. The hold is lifted "
            f"(`because={NEW_HEAD}`), and the new head's own checks, review and "
            "medic decide it from here.",
        ]
    else:
        base = (getattr(decision, "base_sha", "") or "")[:8]
        evidence = list(getattr(decision, "evidence", None) or [])
        lines = [
            f"{NOTE_PREFIX} {card} is back in {REVIEW} — `main` is green again "
            "on the checks it was held for.",
            "",
            f"Pull request #{pr_number} was red at `{old}` only on checks "
            "`main` was red on too:",
            "",
            *[_evidence_bullet(item, base) for item in evidence],
        ]
    lines += ["", _closing(bool(new_head))]
    return "\n".join(lines).rstrip() + "\n"


def _evidence_bullet(item: dict, base: str) -> str:
    name = item.get("check") or ""
    green = (item.get("green_sha") or "")[:8]
    if item.get("base_red", True):
        return f"- **{name}** — red on the merge base `{base}`, green on `main` at `{green}`."
    red = (item.get("red_sha") or "")[:8]
    return (f"- **{name}** — `main` went red on it at `{red}`, after the merge "
            f"base `{base}`, and is green on it at `{green}`.")


def _closing(new_head: bool) -> str:
    then = ("" if new_head else
            " The hold is lifted (`because=" + LIFT_KIND + "`), and the sweep's "
            "stale-merge-ref refresh brings the branch up to date with `main` "
            "next, so its checks and review run again on a new head.")
    return (f"The card {ANCHOR_PHRASE}, so the sweep returned it to {REVIEW}, "
            "where its open pull request is checked like any other." + then)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


class _Unread:
    """A payload file that would not load; every reader refuses it."""


def _load(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return _Unread()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    read = sub.add_parser("medic-read", help="hold or ask, for one failed run")
    read.add_argument("--checks-file", required=True,
                      help="the head's check runs (`gh api --paginate --slurp`)")
    read.add_argument("--base-checks-file", required=True,
                      help="the merge base's check runs; empty when unread")
    read.add_argument("--base-sha", default="")
    read.add_argument("--repo", default="")
    read.add_argument("--reason-out", default=None,
                      help="on hold, the receipt's reason sentence is written here")
    args = parser.parse_args(argv)

    answer, names, why = medic_read(_load(args.checks_file), _load(args.base_checks_file))
    print(answer)
    print(f"inherited_red_main: {answer} — {why}; merge base "
          f"{(args.base_sha or 'unnamed')[:8]}", file=sys.stderr)
    if answer == HOLD and args.reason_out:
        with open(args.reason_out, "w", encoding="utf-8") as fh:
            fh.write(medic_reason(args.repo, names, args.base_sha) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
