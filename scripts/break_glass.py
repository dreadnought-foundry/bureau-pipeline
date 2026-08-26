#!/usr/bin/env python3
"""Break glass — the one sanctioned way past the intake gate (DRE-2737).

Production breaks at 02:00 and every route the intake design offers ends in a
queue that waits on a person: the sanctioned path is Intake → Planning →
one-off → Backlog → the promoter, and the escalation route is the CEO's own
queue — at 2am, the person the wave exists to stop interrupting.

So there is exactly ONE marker, `break-glass`, applied BY HAND by the
operator, which satisfies the entrance condition for `Todo`. Three properties
make it a designed exception rather than a hole:

  1. **The guard records it and does not undo it.** A rule that fights an
     emergency loses the emergency and keeps the rule. The Layer-1 bounce is
     suppressed and the event is written to the card — a notice naming what
     was skipped, by whom, when, and what the card still owes, plus the
     `break-glass:used` RECEIPT LABEL, which is queryable and therefore
     countable (the `guard:returned` shape DRE-2725 uses for the same reason).
  2. **Every use is repaid.** When the work merges, the card returns to
     `Planning` for the classification it skipped instead of going Done. The
     shortcut is repaid, not forgiven, and the review happens when it is cheap
     rather than when it is expensive.
  3. **It is counted, and the count is read a specific way.** Frequent use is
     not people cheating — it means the front door is too slow, and THAT is
     the finding.

Deliberately absent: no expiry, no auto-revoke, no approval step. Every one of
those is a way for the emergency path to fail during an emergency. The control
is that it is loud and counted, not that it is hard to use — which is why an
unreadable provenance read is reported in the notice and honored, never
converted into a refusal.

WHY THE COUNT COMES OFF THE RECEIPT, NOT THE MARKER: the operator may tidy the
marker off the card at any point. Reading the live label would then un-record a
bypass that really happened (console-honesty rule 1: derive from what actually
happened) AND strand the card at merge time, because the debt would vanish
between the bypass and the merge. The receipt is written once, by us, and is
never removed by the pipeline.

NO AGENT MAY APPLY THE MARKER. The relay, reconcile, the planner and every
agent share one LINEAR_API_KEY, so every automated write resolves to the
operator's own Linear user — verified live on DRE-2737, where the Todo gate's
own `agent:engineer` write reads `actor: Frederick Conklin, botActor: None`.
Actor identity therefore cannot tell an agent from the operator (the same
conclusion DRE-2725 reached about "who moved the card"), so the load-bearing
control is the WRITE SEAM: `linear_ops.add_label` and the planner's child-label
path refuse to apply the marker at all. `refusal_reason()` below is the second
layer, for writers we do not own — a Linear integration or automation app
writes as a `botActor`, and its bypass is not honored.

CLI:
  count   print the fleet-wide bypass count (recorded all-time, and how many
          still owe the classification they skipped). Read the number as a
          measure of the front door, not of the people using it.
"""

from __future__ import annotations

import sys

# The operator's marker. Applied by hand, in Linear, by a person.
MARKER = "break-glass"

# The receipt the pipeline stamps when it actually suppresses a bounce. This
# is the queryable population — the count, and the merge-time debt, are both
# read from it, never from MARKER (see the module header).
RECEIPT_LABEL = "break-glass:used"

# Machine-readable comment tags, same shape as dead_run.DEAD_TAG (which
# reconcile already counts occurrences of — reuse the pattern, don't invent
# one). BYPASS_TAG is the bypass receipt; REVIEW_TAG is the merge-time
# return-for-classification receipt and the idempotency key for it;
# REFUSED_TAG marks a marker we would not honor.
BYPASS_TAG = "break-glass-bypass"
REVIEW_TAG = "break-glass-review"
REFUSED_TAG = "break-glass-refused"

# Where a bypassed card goes once its work has merged: back to Planning, for
# the classification it skipped. Not before the merge — the fix ships first.
REVIEW_STATE = "Planning"

# Linear lifecycle buckets that mean the card is finished (mirrors
# linear_ops._TERMINAL_TYPES; kept local so the pure core imports nothing).
_TERMINAL_TYPES = ("completed", "canceled")


def _low(labels) -> list[str]:
    return [(l or "").lower() for l in (labels or [])]


def marked(labels) -> bool:
    """Does the card carry the operator's break-glass marker?"""
    return MARKER in _low(labels)


def owes_review(labels) -> bool:
    """Was a bounce actually suppressed on this card — i.e. does it still owe
    the classification it skipped? Reads the RECEIPT, so it stays true after
    the marker is removed and stays false for a marker that never bypassed
    anything."""
    return RECEIPT_LABEL in _low(labels)


def refusal_reason(provenance: dict) -> str | None:
    """Why this marker must not be honored, or None to honor it.

    The only refusal is a marker applied by a BOT actor — an integration or
    automation app, which Linear reports separately from a user. A human
    actor is honored; an unreadable history is honored and said out loud in
    the notice (a provenance read that fails must not become an approval
    step — see the module header).
    """
    if provenance.get("bot"):
        return (
            f"the marker was applied by {provenance.get('who') or 'an automation'}, "
            "not by the operator — break-glass is an operator action, and an "
            "agent that could bypass the gate would eventually bypass it for a "
            "reason that seemed good at the time"
        )
    return None


def bypass_notice(gaps: list[str], provenance: dict) -> str:
    """The notice posted on the card when a bounce is suppressed: what was
    bypassed, by whom, when, and what the card still owes."""
    who = provenance.get("who") or "unknown (Linear history unreadable)"
    when = provenance.get("at") or "unknown (Linear history unreadable)"
    return (
        f"🔓 {BYPASS_TAG}: this card carried the `{MARKER}` marker, so the "
        "intake gate let it through instead of returning it.\n\n"
        f"**What was skipped:** {', '.join(gaps) if gaps else 'the entrance check'}\n"
        f"**Applied by:** {who}\n"
        f"**When:** {when}\n"
        f"**What this card still owes:** the classification it skipped. When "
        f"its work merges, the card returns to `{REVIEW_STATE}` for that "
        "review — the shortcut is repaid, not forgiven.\n\n"
        f"This bypass has been recorded (`{RECEIPT_LABEL}`) and counted. It is "
        "not undone, and removing the marker does not remove the record."
    )


def refusal_notice(provenance: dict) -> str:
    """The notice posted when a marker is present but not honored."""
    return (
        f"🚫 {REFUSED_TAG}: the `{MARKER}` marker on this card was NOT honored — "
        f"{refusal_reason(provenance)}. The gate acted as if the marker were "
        "absent. An operator who needs this card through should apply the "
        "marker by hand."
    )


def review_notice(pr_url: str, *, moved: bool = True) -> str:
    """The merge-time notice: the work shipped, the debt comes due."""
    tail = (
        f"Returning it to `{REVIEW_STATE}` for that classification now."
        if moved
        else (
            "This card is operator-closed and its state is deliberately "
            f"untouched, so the classification is owed to `{REVIEW_STATE}` by "
            "hand when the operator closes it."
        )
    )
    return (
        f"🔓 {REVIEW_TAG}: this card went through the gate on `{MARKER}` and "
        f"its work has merged ({pr_url}). It still owes the classification it "
        f"skipped. {tail}"
    )


def count_line(recorded: int | None, owing: int | None, error: str | None = None) -> str:
    """The KPI line. Unknown is rendered as unknown, never as 0 — a
    break-glass count that reads 0 because the query failed says "the front
    door is fine", which is the one conclusion it must never invent
    (console-honesty rule 2)."""
    if recorded is None or owing is None:
        return (
            f"{MARKER}: count unknown — Linear did not answer"
            + (f" ({error})" if error else "")
        )
    return (
        f"{MARKER}: {recorded} bypass(es) recorded fleet-wide, {owing} still "
        "owing the classification skipped. A rising number is a finding about "
        "the front door, not about the people using it."
    )


# --- Linear-touching helpers (the pure core above takes no I/O) --------------
#
# Every function here takes the `linear_ops` MODULE as its first argument, the
# same convention validate_card._bounce uses: the Todo gate imports linear_ops
# lazily (so its pure core needs no API key), and passing the module through
# keeps one instance in play for both.


def marker_provenance(linear_ops, identifier: str) -> dict:
    """Who applied the marker, and when, read from Linear's issue history.

    Returns {"who", "bot", "at", "readable"}. A history we cannot read is
    reported as unreadable and honored — see refusal_reason().
    """
    try:
        data = linear_ops.gql(
            """query($id: String!) { issue(id: $id) {
                 history(last: 50) { nodes {
                   createdAt actor { name } botActor { name }
                   addedLabels { name } } } } }""",
            {"id": identifier},
        )
        nodes = (((data.get("issue") or {}).get("history") or {}).get("nodes")) or []
    except Exception as exc:  # noqa: BLE001 — provenance is advisory, never a gate
        print(f"break-glass: could not read {identifier} history ({exc})", file=sys.stderr)
        return {"who": None, "bot": False, "at": None, "readable": False}
    for node in reversed(nodes):  # newest last in Linear's history(last: n)
        added = [(l.get("name") or "").lower() for l in (node.get("addedLabels") or [])]
        if MARKER not in added:
            continue
        bot = node.get("botActor") or {}
        actor = node.get("actor") or {}
        return {
            "who": bot.get("name") or actor.get("name"),
            "bot": bool(bot.get("name")),
            "at": node.get("createdAt"),
            "readable": True,
        }
    # The marker is on the card but no add event is in the window we read —
    # the same class as an unreadable history, and treated the same way.
    return {"who": None, "bot": False, "at": None, "readable": False}


def bounce_suppressed(linear_ops, identifier: str, labels: list[str], gaps: list[str]) -> bool:
    """The one seam a guard calls: may this card go through, and record it.

    True  — the marker is present and honored: the notice is posted, the
            receipt label is stamped, and the caller must NOT return the card.
    False — no marker, or a marker we refuse (a refusal notice is posted).
    """
    if not marked(labels):
        return False
    provenance = marker_provenance(linear_ops, identifier)
    refusal = refusal_reason(provenance)
    if refusal is not None:
        linear_ops.cmd_comment(identifier, refusal_notice(provenance))
        print(f"{identifier}: break-glass marker NOT honored — {refusal}")
        return False
    # The notice lands BEFORE the receipt label: the notice is the record a
    # human reads, and a crash between the two must leave the loud half.
    linear_ops.cmd_comment(identifier, bypass_notice(gaps, provenance))
    linear_ops.add_label(identifier, RECEIPT_LABEL)
    print(
        f"{identifier}: break glass — gate bypassed and RECORDED "
        f"(applied by {provenance.get('who') or 'unknown'}); the card owes the "
        f"classification it skipped and returns to {REVIEW_STATE} once merged"
    )
    return True


def counts(linear_ops) -> dict:
    """Fleet-wide bypass counts, keyed on the receipt label.

    Raises whatever the API raises — callers render unknown, never 0.
    """
    data = linear_ops.gql(
        """query($label: String!) { issues(
             filter: { labels: { name: { eq: $label } } }, first: 250
           ) { nodes { identifier state { name type } } } }""",
        {"label": RECEIPT_LABEL},
    )
    nodes = ((data.get("issues") or {}).get("nodes")) or []
    owing = [
        n for n in nodes
        if ((n.get("state") or {}).get("type")) not in _TERMINAL_TYPES
    ]
    return {
        "recorded": len(nodes),
        "owing": len(owing),
        "owing_cards": [n.get("identifier") for n in owing],
    }


def cmd_count() -> None:
    import linear_ops

    try:
        c = counts(linear_ops)
    except Exception as exc:  # noqa: BLE001 — a KPI read never aborts a caller
        print(count_line(None, None, error=str(exc)))
        return
    print(count_line(c["recorded"], c["owing"]))
    if c["owing_cards"]:
        print("still owing: " + ", ".join(c["owing_cards"]))


if __name__ == "__main__":
    cmd, *args = sys.argv[1:] or ["count"]
    {"count": cmd_count}[cmd](*args)
