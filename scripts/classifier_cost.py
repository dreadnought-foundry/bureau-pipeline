#!/usr/bin/env python3
"""What the planning classifier cost over a window, read off its receipts (DRE-7196).

The card sorter (`scripts/planning_classify.py`) runs on the planner's models,
and every call it makes posts one heartbeat to the card it read:

    🧠 model-attempt: <asked> (asked) / <answered> (answered) · cost $0.0123 — planning classifier read the card.

The cost clause is the classifier's own (`planning_classify.model_receipt`,
`cost=`): the CLI envelope's `total_cost_usd`, the API usage priced off
`config/model-prices.yaml`, or ` · cost unknown` when neither could be read.
This adds those receipts up, so the daily cost is read rather than estimated.
It decides nothing — a trial of a cheaper sorter is owed only above $10 a day,
and that is a person's reading of this number.

One paginated Linear query through `linear_ops.gql_paged`, filtered by
creation time and body text, and the same filter again here: a receipt opens
`🧠 model-attempt:` and says `planning classifier read the card`, so a comment
quoting one is not one. The window is `[since, until)` — the end is exclusive,
so consecutive days never count a receipt twice.

A receipt with no readable cost — ` · cost unknown`, or one written before the
clause existed — is counted in `receipts` and in `uncosted`, and never added
as zero.

One line out, always exactly one on a run that read Linear:

    classifier-cost: receipts=<n> uncosted=<u> total_usd=<d.dd> since=<iso> until=<iso>

A Linear read that fails prints no line and exits 1: a zero tally would say the
sorter cost nothing.

CLI:

    python3 scripts/classifier_cost.py --since 2026-10-12T00:00:00Z --until 2026-10-13T00:00:00Z
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import linear_ops  # noqa: E402
import planning_classify  # noqa: E402

#: The tally line's tag. DRE-7197 declares the line in `config/proof-reach.json`
#: against this constant, and that file's check holds the declaration to it.
TAG = "classifier-cost:"

#: What opens a receipt, and what says the classifier wrote it — plan.yml's
#: heartbeat, word for word.
RECEIPT_OPENER = "🧠 model-attempt:"
RECEIPT_SAYS = "planning classifier read the card"

_COSTED = re.compile(
    re.escape(planning_classify.COST_CLAUSE) + r"(\d+(?:\.\d+)?)"
    + re.escape(" — " + RECEIPT_SAYS))

RECEIPTS_QUERY = """query($since: DateTimeOrDuration!, $until: DateTimeOrDuration!, $after: String) {
  comments(first: 100, after: $after, filter: {
    createdAt: {gte: $since, lt: $until},
    body: {startsWith: "%s", contains: "%s"}
  }) { nodes { createdAt body }
       pageInfo { hasNextPage endCursor } } }""" % (RECEIPT_OPENER, RECEIPT_SAYS)


def _moment(iso: str | None) -> datetime | None:
    """An ISO-8601 instant, read as UTC when it names no offset; None when it
    does not parse."""
    try:
        found = datetime.fromisoformat((iso or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def is_receipt(body: str | None) -> bool:
    """A classifier receipt: anchored at the start, like every marker this
    pipeline reads."""
    body = (body or "").strip()
    return body.startswith(RECEIPT_OPENER) and RECEIPT_SAYS in body


def receipt_cost(body: str | None) -> float | None:
    """The dollars one receipt carries, or None for an uncosted one."""
    found = _COSTED.search(body or "")
    return float(found.group(1)) if found else None


def tally(nodes, since: datetime, until: datetime) -> tuple[int, int, float]:
    """`(receipts, uncosted, total_usd)` over the comments created in
    `[since, until)` that are classifier receipts."""
    receipts = uncosted = 0
    total = 0.0
    for node in nodes or ():
        created = _moment((node or {}).get("createdAt"))
        if created is None or not since <= created < until:
            continue
        body = node.get("body")
        if not is_receipt(body):
            continue
        receipts += 1
        cost = receipt_cost(body)
        if cost is None:
            uncosted += 1
        else:
            total += cost
    return receipts, uncosted, total


def tally_line(receipts: int, uncosted: int, total: float, since: str, until: str) -> str:
    return (f"{TAG} receipts={receipts} uncosted={uncosted} "
            f"total_usd={total:.2f} since={since} until={until}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", required=True, help="ISO-8601, inclusive")
    parser.add_argument("--until", required=True, help="ISO-8601, exclusive")
    args = parser.parse_args(argv)
    since, until = _moment(args.since), _moment(args.until)
    if since is None or until is None:
        parser.error("--since and --until must be ISO-8601 instants")
    if until <= since:
        parser.error("--until must be later than --since")
    # Linear is sent the same UTC instants this filters on, so an instant
    # written with no offset means one thing to both.
    window = {name: moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
              for name, moment in (("since", since), ("until", until))}
    try:
        nodes = linear_ops.gql_paged(RECEIPTS_QUERY, window, connection="comments")
    except Exception as exc:  # noqa: BLE001 — any failed read prints no tally
        print(f"classifier_cost: the Linear read failed, so no tally: {exc}",
              file=sys.stderr)
        return 1
    print(tally_line(*tally(nodes, since, until), args.since, args.until))
    return 0


if __name__ == "__main__":
    sys.exit(main())
