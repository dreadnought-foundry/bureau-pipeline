#!/usr/bin/env python3
"""Ask for an epic's planner run, explicitly — the ONE place that asks
(DRE-2846).

Two paths send an epic to the lane that owes a plan artifact:

  * `reconcile.advance_unblocked_epics` — the predecessor epic reached Done, so
    the next one's turn has come;
  * `wave_commitment.advance` — an approved wave's FIRST epic, which has no
    predecessor to wait for and therefore never passes through the sweep path
    at all.

For both of them the lane move is NOT the trigger. Nothing dispatches off that
lane: it is not in `reconcile.SWEEP_STATES` and it has no nudge (DRE-2736). Its
only other automated attention is `flag_stalled_planning`, which after
`PLANNING_MINUTES` asks a HUMAN to look — well past the point anyone expected
work to have started. So the turn asks for the run, and it asks HERE, because
the alternative is two copies of the ask and one of them quietly not being
made: that is exactly what happened — the sweep asked, the wave's own turn did
not, and every wave's first epic sat silently in the lane it had just been
moved to.

The repository_dispatch itself runs under the default App token on purpose: the
dispatches API needs contents:write, which the App token holds — the stub's
`github.token` (GH_DISPATCH_TOKEN) is contents:read and exists only for
`gh workflow run` (actions:write), so it would 403 here.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 — fixed-arg calls to the gh CLI only
import sys
import tempfile

# The epic the ask is about, read fresh: the dispatch payload is built from it,
# and `children` is the one thing that makes the ask illegal (see `note`).
CARD_QUERY = """query($id: String!) { issue(id: $id) {
     id identifier title description
     labels { nodes { name } }
     children(first: 1) { nodes { identifier } } } }"""

STARTED = "\n\nThe planner run has been started."

NOT_STARTED = ("\n\n🚨 The planner run could NOT be started — the dispatch did "
               "not go through.")

ALREADY_PLANNED = ("\n\nThis epic already carries a plan, so no planner run "
                   "was started: re-planning it from here would activate it "
                   "instead, and its green light is the CEO's. Read the plan "
                   "and move it on when you are ready.")


def fire(card: dict, repo: str) -> tuple[bool, str]:
    """Fire the card's repository_dispatch at `repo`.

    Returns `(True, "")` ONLY on a confirmed rc=0 dispatch, else `(False,
    <error line>)` — the caller decides how loud a failure is, and MUST gate
    its "the run started" receipt on the flag. The old silent gh() meant a
    403'd dispatch still told the CEO the card was restarted (the DRE-1254
    false-receipt class, DRE-2034).
    """
    if not repo:
        return False, (f"plan run {card['identifier']}: no REPO to dispatch at "
                       "— the step that runs this must pass one")
    labels = [lbl["name"].lower() for lbl in card["labels"]["nodes"]]
    event = "agent-plan" if "agent:planner" in labels else "agent-execute"
    payload = {
        "card_id": card["id"],
        "identifier": card["identifier"],
        "title": card["title"],
        "description": card["description"] or "",
        "labels": labels,
        "url": f"https://linear.app/dreadnoughtfoundry/issue/{card['identifier']}",
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"event_type": event, "client_payload": payload}, f)
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


def dispatch(card: dict) -> bool:
    """`fire` at the repo this run is for, printing a failure rather than
    swallowing it. The default ask for a caller with no failure ledger of its
    own — the sweep passes its own, so its failures still turn the run red."""
    ok, err = fire(card, os.environ.get("REPO", ""))
    if not ok:
        print(f"ERROR: {err}", file=sys.stderr)
    return ok


def note(linear_ops, identifier: str, ask=dispatch, *,
         if_not_started: str = "", record_failure=None) -> str:
    """Ask for `identifier`'s planner run, and return the sentence to append to
    its arrival note — saying honestly whether the run started.

    Childless epics only. `plan.yml` routes an epic WITH children to ACTIVATE,
    which green-lights it and promotes its children — the exact blank cheque
    DRE-2846 removes. An epic committed in sequence is created childless, so
    that is the whole of the normal path; one that somehow has children already
    is moved and left for a human, and says so.

    Never raises: a failed dispatch is a receipt to be honest about, not a
    reason to stop advancing the rest of the chain. `record_failure` is the
    caller's ledger for the error (the sweep's, which turns the run red);
    `if_not_started` is the caller's own sentence about what happens next,
    because what happens next is not the same on both paths.
    """
    try:
        card = linear_ops.gql(CARD_QUERY, {"id": identifier})["issue"]
        if (card.get("children") or {}).get("nodes"):
            return ALREADY_PLANNED
        started = ask(card)
    except Exception as e:  # noqa: BLE001 — an unreadable card is not a crash
        if record_failure is not None:
            record_failure(f"{identifier} plan dispatch: {e}")
        started = False
    if started:
        return STARTED
    return NOT_STARTED + (f" {if_not_started}" if if_not_started else "")
