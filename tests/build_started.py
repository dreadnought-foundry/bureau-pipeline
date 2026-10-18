"""A card whose build STARTED, for the sweep tests of the dead-run path.

Since DRE-7108 the nudge loop asks every card in a start lane whether its
build started there — a `🧠 model-attempt` receipt newer than its entry into
the lane (`reconcile.build_started`). A card that never started is re-fired
and never read as a dead run, so a test of the dead-run path hands the loop a
card carrying the receipt, and answers the lane-history read the question
then costs with an entry older than it.
"""
from __future__ import annotations

from unittest.mock import patch

import lane_contract
import linear_ops

START_RECEIPT = "🧠 model-attempt: claude-opus-5-5 — engineer agent starting"
RECEIPT_AT = "2026-01-01T00:00:00.000Z"
ENTERED_AT = "2025-12-31T23:00:00.000Z"


def started(card: dict, at: str = RECEIPT_AT) -> dict:
    """`card` with the start receipt as the newest comment in its window
    (the API's order, newest first)."""
    nodes = list((card.get("comments") or {}).get("nodes") or [])
    card["comments"] = {"nodes": [{"body": START_RECEIPT, "createdAt": at}] + nodes}
    return card


def entered(at: str = ENTERED_AT):
    """Patch the lane-history read so every start lane was entered at `at`."""
    return patch.object(linear_ops, "lane_history", return_value=[
        {"createdAt": at, "toState": {"name": lane}} for lane in lane_contract.START_LANES
    ])
