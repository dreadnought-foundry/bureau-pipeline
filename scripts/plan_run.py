#!/usr/bin/env python3
"""The planner's dispatch — ONE payload and ONE `repository_dispatch` for the
callers that genuinely make one.

Two of them remain:

  * `reconcile.redispatch` — the sweep re-firing a Todo card's dispatch, with
    the failure kept in its write ledger so the run goes red;
  * `review_rerun.py dispatch` — the retry of a dead post-approval review,
    which asks for the ACTIVATE route with `trigger_state` / `reason`
    (DRE-3286).

This module began as the ONE place that asked for an epic's planner run when
its turn came in a wave (DRE-2846, `note`), on the belief that nothing
dispatched off the lane that owes a plan artifact — it is not in
`reconcile.SWEEP_STATES` and has no nudge (DRE-2736). The relay does dispatch
`agent-plan` on every entry into that lane (agent-bureau's
lambda_function.py — DRE-1913, label or no label since DRE-3030), so the ask
was the second of two dispatches for one lane move: on the DRE-3530 approval
three epics drew six Agent Plan runs, and the duplicates each started a hosted
runner to learn they had nothing to do. `wave_commitment.advance` stopped
asking in DRE-3659 and `reconcile.advance_unblocked_epics` in DRE-3664, and
the ask went with its last caller — from the one place it was written. The
lane's stall is still watched by `flag_stalled_planning`, which after
`PLANNING_MINUTES` asks a HUMAN to look.

The repository_dispatch itself runs under the default App token on purpose: the
dispatches API needs contents:write, which the App token holds — the stub's
`github.token` (GH_DISPATCH_TOKEN) is contents:read and exists only for
`gh workflow run` (actions:write), so it would 403 here.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 — fixed-arg calls to the gh CLI only
import tempfile

# The epic a dispatch is about, read fresh: the payload is built from it.
# `review_rerun.py dispatch` reads the card with this query before it fires.
CARD_QUERY = """query($id: String!) { issue(id: $id) {
     id identifier title description
     labels { nodes { name } }
     children(first: 1) { nodes { identifier } } } }"""


def payload(card: dict, *, trigger_state: str | None = None,
            reason: str | None = None) -> dict:
    """The `client_payload` `fire` sends for `card`.

    Its own function since DRE-3286, because a second caller now needs the
    SAME payload with two extra keys, and a second copy of the six base fields
    is how two dispatchers come to describe one card differently.

    `trigger_state` is what `plan.yml`'s route step reads to choose ACTIVATE
    over PLAN, and `reason` is why the run was asked for. Both are added ONLY
    when given — not as explicit nulls — so a caller that asks for neither
    sends the payload this function has always sent, key for key. The route
    tests `client_payload.trigger_state == "in progress"`, and an epic reaching
    it with the key absent takes the plan route exactly as it does today.
    """
    body = {
        "card_id": card["id"],
        "identifier": card["identifier"],
        "title": card["title"],
        "description": card["description"] or "",
        "labels": [lbl["name"].lower() for lbl in card["labels"]["nodes"]],
        "url": f"https://linear.app/dreadnoughtfoundry/issue/{card['identifier']}",
    }
    if trigger_state is not None:
        body["trigger_state"] = trigger_state
    if reason is not None:
        body["reason"] = reason
    return body


def fire(card: dict, repo: str, *, trigger_state: str | None = None,
         reason: str | None = None) -> tuple[bool, str]:
    """Fire the card's repository_dispatch at `repo`.

    Returns `(True, "")` ONLY on a confirmed rc=0 dispatch, else `(False,
    <error line>)` — the caller decides how loud a failure is, and MUST gate
    its "the run started" receipt on the flag. The old silent gh() meant a
    403'd dispatch still told the CEO the card was restarted (the DRE-1254
    false-receipt class, DRE-2034).

    `trigger_state` / `reason` (DRE-3286) are how a caller asks for the
    ACTIVATE route instead of the PLAN one — `review_rerun.py dispatch` is the
    only one that does. Omit them and nothing about this call changes.
    """
    if not repo:
        return False, (f"plan run {card['identifier']}: no REPO to dispatch at "
                       "— the step that runs this must pass one")
    body = payload(card, trigger_state=trigger_state, reason=reason)
    event = "agent-plan" if "agent:planner" in body["labels"] else "agent-execute"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"event_type": event, "client_payload": body}, f)
        path = f.name
    try:
        p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
            ["gh", "api", f"repos/{repo}/dispatches", "--input", path],
            capture_output=True, text=True, check=False,
        )
    finally:
        os.unlink(path)
    if p.returncode != 0:
        return False, (
            f"redispatch {card['identifier']}: gh api repos/{repo}/dispatches "
            f"failed rc={p.returncode}: {p.stderr.strip()[:400]}"
        )
    return True, ""
