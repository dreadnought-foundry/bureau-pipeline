#!/usr/bin/env python3
"""A stored groom proposal, made into a run's input — and the run's verified
proposal, read back as one line per card (DRE-7239).

The fixtures replay `groom_verify_agent.apply` in tests, but nothing could run
the real verify leg — its model, its lookups, its board reads — over a stored
morning. DRE-6646's first acceptance criterion is that replay: proposal
`a4f198411b02`, the 2026-10-10 morning, re-checked with today's inputs and
proof rules. This module is its two ends; the groomer workflow runs the leg
between them (DRE-7242).

  * `base --stored <path> --out proposal.json` — reads a JSON file in the
    pipeline checkout, named by a path relative to it, and takes the proposal
    under its `proposal` key, or the file itself when the file is one. It
    strips what the verify leg wrote — each row's `verify` field, the
    top-level `verify` and `verify_targets` — and leaves every list as it
    stood, so `groom_verify_agent.window` over the result is the window the
    stored record names. An absolute path, a path with `..`, a link that
    leaves the checkout, a missing file, or a file holding no proposal is
    refused with exit 2 and the reason, and nothing is written.

    `verification` is kept. `groomer.py propose` writes it, not the verify
    leg (`groom_verify.settle`), and a groom run's input carries it today.
    Its spare marks are how `window` reads the spares behind the Planning
    list: without them the spares fall back to the first ten waiting cards
    in the rules' order, and the window is no longer the stored one — on the
    2026-10-10 record, 28 cards instead of 20.

  * `report --proposal proposal-verified.json` — one line per card in the
    window, in the rules' order, then one count line:

        groom-replay: <card> <list> <verdict> — <summary> — proof: <proof lines> — rested on: <words>
        groom-replay: <n> cards — <k> planning, <c> cancel, <r> rewrite, <u> not verified, <x> excluded, <w> not now

    The window is every card carrying a verify record, and every card
    `groom_verify_agent.window` lists over the verified proposal — so a
    Planning card no verify record covers is named, as `unverified — no
    verify record`, rather than missed. `<list>` is where the card ended:
    `planning`, `cancel`, `rewrite`, `not-verified` or `excluded` — and
    `not-now` for a spare that took no slot and waits where it was, since
    a spare answered `partly-solved` or `still-needed` with no slot to take
    is on none of the other five. An `unverified` or `excluded` verdict's
    summary is followed by its reason, in brackets. The proof lines are
    `groomer.proof_line`'s.

Every line either subcommand prints opens `TAG`, a refusal too, and a proof
run reads them from the run's log (`config/proof-reach.json`). It imports
`groomer` and `groom_verify_agent` and edits neither.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path, PurePath

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import groom_verify_agent  # noqa: E402 — the window, the reason prefixes
import groomer  # noqa: E402 — proof_line, defang_reason
import sanitize_untrusted  # noqa: E402 — the fence, made mechanical

ROOT = Path(__file__).resolve().parent.parent

#: Every line this module prints opens with it — declared in
#: `config/proof-reach.json`'s `log_lines`.
TAG = "groom-replay:"

#: What the verify leg writes on a proposal, at the top level.
VERIFY_KEYS = ("verify", "verify_targets")

#: Where a card ended, exact — the report's `<list>`.
(PLANNING, CANCEL, REWRITE, NOT_VERIFIED, EXCLUDED,
 NOT_NOW) = ("planning", "cancel", "rewrite", "not-verified", "excluded",
             "not-now")
#: The count line's words for each list, in its order.
COUNT_WORDS = ((PLANNING, "planning"), (CANCEL, "cancel"),
               (REWRITE, "rewrite"), (NOT_VERIFIED, "not verified"),
               (EXCLUDED, "excluded"), (NOT_NOW, "not now"))
NO_RECORD = "unverified — no verify record"
NONE = "—"


class ReplayError(Exception):
    """An input the replay cannot act on — said, and exit 2."""


# --------------------------------------------------------------------------- #
# base                                                                         #
# --------------------------------------------------------------------------- #

def _is_proposal(doc) -> bool:
    return (isinstance(doc, dict) and isinstance(doc.get("outcomes"), dict)
            and isinstance(doc.get("sequence"), list))


def stored_path(stored: str, root: Path = ROOT) -> Path:
    """The file `--stored` names, inside the checkout, or the reason not."""
    if not stored:
        raise ReplayError("--stored is empty")
    if PurePath(stored).is_absolute() or stored.startswith(("/", "\\")):
        raise ReplayError(f"{stored} is an absolute path; name a file "
                          "relative to the pipeline checkout")
    if ".." in PurePath(stored.replace("\\", "/")).parts:
        raise ReplayError(f"{stored} holds a `..`; name a file inside the "
                          "pipeline checkout")
    path = root / stored
    if not path.is_file():
        raise ReplayError(f"{stored}: no such file in the pipeline checkout")
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ReplayError(f"{stored} resolves outside the pipeline checkout")
    return path


def load_stored(stored: str, root: Path = ROOT) -> dict:
    """The proposal the stored file holds — under its `proposal` key, or the
    file itself."""
    path = stored_path(stored, root)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ReplayError(f"{stored} is not JSON: {e}")
    if isinstance(doc, dict) and _is_proposal(doc.get("proposal")):
        return doc["proposal"]
    if _is_proposal(doc):
        return doc
    raise ReplayError(f"{stored} holds no proposal: no `proposal` key, and "
                      "the file has no `outcomes` and `sequence` of its own")


def _rows(proposal: dict):
    for rows in proposal["outcomes"].values():
        if isinstance(rows, list):
            yield from (r for r in rows if isinstance(r, dict))
    yield from (r for r in proposal["sequence"] if isinstance(r, dict))


def base(proposal: dict) -> dict:
    """The proposal with what the verify leg wrote stripped off, in place."""
    for key in VERIFY_KEYS:
        proposal.pop(key, None)
    for row in _rows(proposal):
        row.pop("verify", None)
    return proposal


# --------------------------------------------------------------------------- #
# report                                                                       #
# --------------------------------------------------------------------------- #

def _one_line(text) -> str:
    """One line, and defanged — a fence sentinel and a pipeline marker alike:
    the summary and proof are the agent's words about card text and code,
    and a proof run's agent reads this log."""
    flat = sanitize_untrusted.sanitize_line(str(text or ""))
    return groomer.defang_reason(flat)[0] if flat else ""


def _records(proposal: dict) -> dict[str, dict]:
    marks: dict[str, dict] = {}
    for row in _rows(proposal):
        if isinstance(row.get("verify"), dict):
            marks.setdefault(row.get("identifier"), row["verify"])
    return marks


def window(proposal: dict) -> list[str]:
    """Every card a verify record covers, and every card
    `groom_verify_agent.window` lists, in the rules' order."""
    wanted = set(_records(proposal))
    wanted |= {card for card, _ in groom_verify_agent.window(proposal)}
    order = [r.get("identifier") for r in proposal["sequence"]
             if isinstance(r, dict)]
    rest = sorted(wanted - set(order))
    return [i for i in order if i in wanted] + rest


def ended(proposal: dict, card: str) -> str:
    """The list the card ended on in the verified proposal."""
    outcomes = proposal["outcomes"]

    def on(name: str):
        return next((r for r in outcomes.get(name) or []
                     if r.get("identifier") == card), None)

    if on("now"):
        return PLANNING
    if on("dead"):
        return CANCEL
    if on(groom_verify_agent.REWRITE):
        return REWRITE
    waiting = on("not-now") or {}
    reason = str(waiting.get("reason") or "")
    if reason.startswith(groom_verify_agent.EXCLUDED_PREFIX):
        return EXCLUDED
    if reason.startswith(groom_verify_agent.UNVERIFIED_PREFIX):
        return NOT_VERIFIED
    return NOT_NOW


def _rested_on(value) -> str:
    if isinstance(value, (list, tuple)):
        words = [_one_line(v) for v in value if _one_line(v)]
        return ", ".join(words) or NONE
    return _one_line(value) or NONE


def line(card: str, where: str, mark: dict | None) -> str:
    if mark is None:
        return f"{TAG} {card} {where} {NO_RECORD}"
    verdict = _one_line(mark.get("verdict")) or groom_verify_agent.UNVERIFIED
    said = _one_line(mark.get("summary"))
    # An unproved verdict's reason is the why — `no proof`, or which
    # exclusion — and the summary alone does not say it.
    reason = _one_line(mark.get("reason"))
    if reason and verdict in (groom_verify_agent.UNVERIFIED,
                              groom_verify_agent.EXCLUDED):
        said = f"{said} ({reason})" if said else reason
    said = said or reason
    proofs = [_one_line(groomer.proof_line(item))
              for item in mark.get("proof") or [] if isinstance(item, dict)]
    return (f"{TAG} {card} {where} {verdict} — {said or NONE} "
            f"— proof: {'; '.join(proofs) or NONE} "
            f"— rested on: {_rested_on(mark.get('rested_on'))}")


def report(proposal: dict) -> list[str]:
    marks = _records(proposal)
    lines, counts = [], {key: 0 for key, _ in COUNT_WORDS}
    cards = window(proposal)
    for card in cards:
        where = ended(proposal, card)
        counts[where] += 1
        lines.append(line(card, where, marks.get(card)))
    lines.append(f"{TAG} {len(cards)} cards — "
                 + ", ".join(f"{counts[key]} {word}" for key, word in COUNT_WORDS))
    return lines


# --------------------------------------------------------------------------- #
# the command line                                                             #
# --------------------------------------------------------------------------- #

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("base", help="a stored proposal, as a run's input")
    p.add_argument("--stored", required=True,
                   help="a JSON file, relative to the pipeline checkout")
    p.add_argument("--out", required=True)
    p = sub.add_parser("report", help="where each window card ended")
    p.add_argument("--proposal", required=True)
    return parser


def main(argv=None, *, root: Path = ROOT) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "base":
            proposal = base(load_stored(args.stored, root))
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(proposal, fh, indent=2)
                fh.write("\n")
            n = len(groom_verify_agent.window(proposal))
            print(f"{TAG} base {proposal.get('id')} — {n} cards in the verify "
                  f"window, from {args.stored}")
            return 0
        try:
            proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as e:
            raise ReplayError(f"{args.proposal} could not be read: {e}")
        if not _is_proposal(proposal):
            raise ReplayError(f"{args.proposal} is not a proposal")
        print("\n".join(report(proposal)))
        return 0
    except ReplayError as e:
        print(f"{TAG} {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
