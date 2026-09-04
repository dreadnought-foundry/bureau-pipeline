#!/usr/bin/env python3
"""Mid-epic discovery — a finding made while building joins an approved epic
without a new green light (DRE-2739).

The intake design describes work arriving from OUTSIDE: idea → Intake →
Planning → green light → build. The highest-value findings this system produces
are made MID-BUILD, by whoever is already in the code, about an epic that is
already approved and already running. Under the design as written that finding
has nowhere to go. Send it to Intake and it queues behind a planning cycle,
losing the context that made it findable; add it silently and the approved plan
no longer describes the work. **People choose the second one**, because they are
mid-flow and the first is a wall.

## Two kinds of discovery

The distinguishing question is not size, effort or urgency. It is: **does the
approved plan still describe what we are doing?**

  * **Addition** — the plan holds; there is more of it than we knew (a second
    call site needs the same fix). Route: into the epic, with a verdict. **No new
    green light.**
  * **Amendment** — the plan no longer describes the work; a recommendation
    changed (the fix must be split and its order reversed). Route: back to
    Planning; the epic is re-green-lit.

Guessing wrong is cheap: the artifact update catches an amendment mislabelled as
an addition, because the plan will not absorb it. So the classification is one
line from whoever found it, required at creation — never a form.

## The mechanical hazard this exists to close

`reconcile.promote_ready` auto-promotes Backlog children of an active epic once
their blockers clear. A card added mid-epic therefore dispatches an agent on the
next sweep — within fifteen minutes — whether or not anyone has read it. Right
for a card the plan anticipated; wrong for one nobody has seen. So an addition
carries a **verdict** before it joins the epic: Layer 1 is not waived. What is
waived is the GREEN LIGHT — the human decision — because that was already made
for this epic, and re-asking on every second call site is how the queue becomes
the bottleneck again.

## How "added mid-epic" is DERIVED, not marked

A card is a mid-epic addition when it was created AFTER the epic's most recent
green light — both facts read live from Linear (the child's `createdAt`, the
epic's own state history). Nothing has to remember to stamp anything, which
matters because the hazard IS the card nobody stamped: the hand-add straight
into Linear that this module cannot see coming. A green light we cannot read is
reported as unknown and abstains — refusing every child on an unreadable history
would freeze the fleet, and console-honesty rule 1 says derive from truth rather
than infer from an adjacent signal.

Re-approval after an amendment is observed the same way: the epic is seen in an
active lane again. Not assumed from the return to Planning, and not inferred
from elapsed time.

## Growth has to be legible

The epic shows what it was green-lit at and what it is now: approved at nine
cards, running at fourteen. Nobody polices the number; it just has to be
visible, because silent accretion turns an approved scope into an unapproved one
with no single decision being wrong. That is the epic-growth KPI, and it lives
in a managed region of the epic's own description (`ARTIFACT_BEGIN`/`_END`) so
the CEO reads it where the plan is, not in a log.

## A card has no children

`validate_card.infer_agent_label` decides what a card IS from whether it has
children (`if "[epic]" in t or has_children: return "agent:planner"`), and
`reconcile.promote_ready` skips every card `is_epic()` answers yes for — which
is that same pair of facts when nothing has stamped a shape — because epics are
promoted by humans and never by the sweep. **Giving a card sub-issues silently
converts it into an epic and stops it ever being promoted.** So a mid-epic
discovery becomes a new SIBLING card under the same epic, with its own number —
not a `2716a`/`2716b` suffix, not a child. `subissue_refusal()` is the guard;
`linear_ops.cmd_subissue` calls it before it creates anything.

`is_epic()` answers that question from the SHAPE STAMP (DRE-2843) first and the
title/children second. It does not read `agent:planner` and is not given the
labels, so it cannot: that label says the planner OWNS the card, every card
dispatched to `plan.yml` from Planning carries it, and reading it as epic-ness
answered "epic, no verdict" for every one-off — which is how one fixed FLEET
sentence came to be stamped on cards whose criteria said otherwise (DRE-3038).

CLI:
  discovery <EPIC> --kind addition --because "<one line>" \\
                   --title "<title>" --body <file> [--label <name>]
  discovery <EPIC> --kind amendment --because "<one line>"
  audit <EPIC>     refresh the epic's growth artifact and report what it found
"""

from __future__ import annotations

import os
import re
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane_scope  # noqa: E402 — the ONE place the retired lane name survives

# The two kinds, and the question that tells them apart.
ADDITION = "addition"
AMENDMENT = "amendment"
KINDS = (ADDITION, AMENDMENT)
THE_QUESTION = "does the approved plan still describe what we are doing?"

# Machine-readable comment tags, the dead_run.DEAD_TAG shape reconcile already
# counts occurrences of (reuse the pattern, don't invent one).
VERDICT_TAG = "mid-epic-verdict"        # on the SIBLING: the judgement it carries
AMENDMENT_TAG = "mid-epic-amendment"    # on the EPIC: the plan no longer holds
REAPPROVAL_TAG = "mid-epic-re-green-lit"  # on the EPIC: the re-approval, observed
NO_VERDICT_TAG = "mid-epic-no-verdict"  # on the SIBLING: promotion refused
UNRECORDED_TAG = "mid-epic-unrecorded"  # on the EPIC: it grew, its plan did not

# Where an amendment sends the epic. Planning, not Intake: the epic exists, it
# is the RECOMMENDATION that changed.
AMENDMENT_STATE = "Planning"

# The planning shape (DRE-2843) that means "this card is an epic". Named here
# rather than derived: the vocabulary carries no "which of these is the epic"
# marker to read, and `planning_shape` would have to be imported and its config
# parsed to answer a question this module asks on every subissue create. So the
# word is written once and BOUND by `tests/test_mid_epic.py`, which checks it
# against the vocabulary and against the one route that stops for a human — a
# shape renamed in the file fails there rather than drifting into a stamp that
# means nothing.
EPIC_SHAPE = "epic"

# The lanes that mean a human has green-lit this epic — the same pair
# reconcile.EPIC_ACTIVE_STATES treats as activated (DRE-1893: the CEO's
# activation action is moving an approved epic to Todo; In Progress is the
# downstream progression).
EPIC_ACTIVE_LANES = ("Todo", "In Progress")

# The human-decision lane an ADDITION deliberately does not pass through. Both
# names, because the live board may still answer with the retired one — a rename
# must not quietly re-route an addition through a gate it was designed to skip.
#
# DERIVED, not restated. `lane_scope.LANE_ALIASES` is the ONE place the retired
# name survives (DRE-2722), and `linear_ops` inverts that same dict for its
# write direction rather than repeating the literal. Spelling the old name here
# would make this the second source of a fact that has exactly one, and the
# rename would then have to find both. Delete nothing here when the board is
# renamed: emptying LANE_ALIASES collapses this to the new name on its own.
GREEN_LIGHT_LANES = ("Green Light",) + tuple(
    old for old, new in lane_scope.LANE_ALIASES.items() if new == "Green Light"
)

# The managed region in the epic's description. Fenced by HTML comments so it is
# invisible in rendered Linear and unambiguous to parse.
ARTIFACT_BEGIN = "<!-- BEGIN epic-growth (managed by scripts/mid_epic.py) -->"
ARTIFACT_END = "<!-- END epic-growth -->"

ADDITIONS_HEADING = "Added since green light:"
AMENDMENTS_HEADING = "Amendments:"
AWAITING_REAPPROVAL = "awaiting re-approval"
UNKNOWN_GREEN_LIGHT = "unknown — Linear has no readable green light for this epic"

_ADDITION_LINE = re.compile(r"^-\s+(DRE-\d+)\s+—\s+(.*)$")
# GREEDY on the justification, anchored on the settled field: the artifact is
# parsed back on every sweep, and a justification that happens to contain the
# field separator must not shear the record it was written into.
_AMENDMENT_LINE = re.compile(
    r"^-\s+(\S+)\s+—\s+(.*)\s+—\s+("
    + re.escape(AWAITING_REAPPROVAL)
    + r"|re-green-lit\s+.*)$"
)
_REGION = re.compile(
    re.escape(ARTIFACT_BEGIN) + r".*?" + re.escape(ARTIFACT_END), re.DOTALL
)


class DiscoveryRefused(Exception):
    """The motion was refused before it touched Linear.

    Every refusal in this module is pre-creation on purpose: a sibling created
    and then questioned is already in Backlog, already promotable, and already
    the silent-accretion problem it was supposed to avoid.
    """


# --- the classification: one line, required at creation ----------------------


def classification_problem(kind, because) -> str | None:
    """Why this discovery cannot be filed, or None when it can.

    The one line is REQUIRED, not optional, and it is required at creation — the
    moment the person still has in their head why the plan does or does not
    still describe the work. Asking later gets a reconstruction.
    """
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        return (
            f"a discovery must be classified {ADDITION!r} or {AMENDMENT!r} "
            f"(got {kind or 'nothing'!r}). The distinguishing question is not "
            f"size, effort or urgency — it is: {THE_QUESTION} If it still does, "
            f"this is an {ADDITION}; if a recommendation changed, it is an "
            f"{AMENDMENT}."
        )
    if not (because or "").strip():
        return (
            f"a one-line justification for {ADDITION}-vs-{AMENDMENT} is required "
            "at creation. Whoever found it decides, in one line — guessing wrong "
            "is cheap, because the artifact update catches an amendment "
            "mislabelled as an addition when the plan will not absorb it."
        )
    return None


def verdict_comment(kind: str, because: str, epic: str) -> str:
    """The verdict an addition carries when it joins the epic.

    Layer 1 is not waived — this IS the layer-1 judgement, recorded on the card
    before anything can promote it. What is waived is the green light.
    """
    return (
        f"🔎 {VERDICT_TAG}: filed as an **{kind}** to {epic} by whoever found it, "
        "mid-build.\n\n"
        f"**Why it is an {kind}:** {because}\n\n"
        f"**What this verdict is:** the layer-1 judgement this card carries into "
        f"an already-approved epic. It is NOT a new green light — that human "
        f"decision was already made for {epic}, and re-asking on every second "
        "call site is how the queue becomes the bottleneck again. Without this "
        "verdict the sweep refuses to promote the card."
    )


def carries_verdict(comment_bodies) -> bool:
    """Does this card carry a mid-epic verdict? Reads the marker, never "some
    human said something" — a chatty card is not an approved one."""
    return any(VERDICT_TAG in (b or "") for b in (comment_bodies or []))


# --- the derivation: what counts as added mid-epic ---------------------------


def added_after_green_light(created_at, green_lit_at) -> bool | None:
    """Was this child created after the epic's most recent green light?

    None means UNKNOWN — either timestamp missing. Unknown is never silently
    converted to True or False; the callers decide what to do about not knowing,
    and both of them abstain.
    """
    if not created_at or not green_lit_at:
        return None
    return _ts(created_at) > _ts(green_lit_at)


def promotion_refusal(identifier, created_at, green_lit_at, comment_bodies) -> str | None:
    """Why `identifier` must not promote yet, or None to let it through.

    The whole rule: a card added to an already-green-lit epic carries a verdict
    before it joins. A card the plan anticipated is untouched, and an epic whose
    green light cannot be read abstains rather than refusing — refusing on an
    unreadable read would freeze every child of every epic.
    """
    if added_after_green_light(created_at, green_lit_at) is not True:
        return None
    if carries_verdict(comment_bodies):
        return None
    return (
        f"🚨 {NO_VERDICT_TAG}: {identifier} was added to this epic AFTER it was "
        "green-lit, and carries no verdict — so the sweep is not promoting it. "
        "A card added mid-epic dispatches an agent on the next sweep, within "
        "fifteen minutes, whether or not anyone has read it. That is right for a "
        "card the plan anticipated and wrong for one nobody has seen.\n\n"
        "**To let it through:** file it the way the route intends —\n"
        "`python3 scripts/mid_epic.py discovery <EPIC> --kind addition "
        '--because "<one line>" --title "…" --body <file>`\n\n'
        "That records the verdict on the card and the growth on the epic in one "
        "motion. It does NOT need a new green light: that decision was already "
        "made for this epic. If the plan no longer describes the work, file an "
        f"`--kind {AMENDMENT}` instead and the epic goes back to Planning."
    )


# --- a card has no children --------------------------------------------------


def is_epic(parent_title, has_children: bool, shape: str | None = None) -> bool:
    """Is this card already an epic?

    Reads the SHAPE STAMP first (DRE-2843) and the title/children second. The
    stamp is what says what a card IS; a caller that has read it passes it, and
    a card nothing has classified falls back to the two facts that reclassify a
    card on their own — `validate_card.infer_agent_label` returns
    `agent:planner` for `[EPIC]` in the title or ANY children at all.

    It does NOT read `agent:planner`, and it is not given the labels so that it
    cannot (DRE-3038). That label says the planner OWNS the card, which is a
    different question — and every card the relay dispatches to `plan.yml` from
    Planning carries it, so reading it as epic-ness answered "epic, no verdict"
    for every ONE-OFF. `routing_verdict.route()` then never ran its own
    precedence, and `planning_route._one_off_check()` stamped a fixed FLEET
    sentence on cards whose acceptance criteria said otherwise (observed on
    DRE-3018 and DRE-3020, both with zero criteria, both stamped FLEET).
    """
    if shape:
        return shape.strip().lower() == EPIC_SHAPE
    return "[epic]" in (parent_title or "").lower() or bool(has_children)


def subissue_refusal(parent_title, has_children: bool,
                     shape: str | None = None) -> str | None:
    """Why a sub-issue must not be created under this parent, or None.

    The refusal fires when the parent is NOT already an epic — because that is
    exactly when adding a child CHANGES what the parent is.
    """
    if is_epic(parent_title, has_children, shape):
        return None
    return (
        f"a card has no children — refusing to create a sub-issue of "
        f"{parent_title!r}.\n\n"
        "Giving a card sub-issues silently converts it INTO an epic: "
        "validate_card.infer_agent_label reads children as epic-ness and "
        "classifies the parent agent:planner, and reconcile.promote_ready skips "
        "every card this same helper reads as an epic — children included — "
        "because epics are promoted by humans and never by the sweep. The "
        "parent would stop being promoted, permanently, with nothing anywhere "
        "saying so.\n\n"
        "A mid-epic discovery becomes a new SIBLING card under the same epic, "
        "with its own number — not a sub-issue, and not a 'DRE-1234a' suffix. "
        "Use: python3 scripts/mid_epic.py discovery <EPIC> --kind "
        'addition --because "<one line>" --title "…" --body <file>'
    )


# --- the artifact: growth, on the epic, where the plan is --------------------


def growth_line(epic: str, green_lit, current) -> str:
    """The KPI in one line. An unreadable green light renders UNKNOWN, never 0 —
    "green-lit at 0 cards" invents an approval that never happened, which is the
    one thing this number must not fabricate (console-honesty rule 2)."""
    at = f"{green_lit} cards" if green_lit is not None else UNKNOWN_GREEN_LIGHT
    return f"epic-growth: {epic} green-lit at {at}, now running {current} cards"


def render_artifact(green_lit, current, additions, amendments) -> str:
    """The managed region, rendered from what was observed."""
    at = f"{green_lit} cards" if green_lit is not None else UNKNOWN_GREEN_LIGHT
    lines = [
        ARTIFACT_BEGIN,
        "",
        "## Epic growth",
        "",
        f"**Green-lit at:** {at} · **Now running:** {current} cards",
        "",
        ADDITIONS_HEADING,
    ]
    lines += (
        [f"- {a['id']} — {a['because']}" for a in additions]
        if additions
        else ["- (none)"]
    )
    lines += ["", AMENDMENTS_HEADING]
    if amendments:
        for am in amendments:
            settled = (
                f"re-green-lit {am['re_green_lit']}"
                if am["re_green_lit"]
                else AWAITING_REAPPROVAL
            )
            lines.append(f"- {am['at']} — {am['because']} — {settled}")
    else:
        lines.append("- (none)")
    lines += ["", ARTIFACT_END]
    return "\n".join(lines)


def parse_artifact(description: str) -> dict:
    """Read the managed region back. Empty/absent region parses to empty lists
    and unknown counts, so a first motion and a re-read take the same path."""
    out = {"green_lit": None, "current": None, "additions": [], "amendments": []}
    match = _REGION.search(description or "")
    if not match:
        return out
    section = None
    for raw in match.group(0).splitlines():
        line = raw.strip()
        if line.startswith("**Green-lit at:**"):
            out["green_lit"] = _leading_int(line.split("**Green-lit at:**", 1)[1]
                                            .split("·")[0])
            out["current"] = _leading_int(line.split("**Now running:**", 1)[1]) \
                if "**Now running:**" in line else None
            continue
        if line == ADDITIONS_HEADING:
            section = "additions"
            continue
        if line == AMENDMENTS_HEADING:
            section = "amendments"
            continue
        if not line.startswith("-") or line == "- (none)":
            continue
        if section == "additions":
            m = _ADDITION_LINE.match(line)
            if m:
                out["additions"].append({"id": m.group(1), "because": m.group(2)})
        elif section == "amendments":
            m = _AMENDMENT_LINE.match(line)
            if m:
                settled = m.group(3).strip()
                out["amendments"].append({
                    "at": m.group(1),
                    "because": m.group(2),
                    "re_green_lit": (
                        None if settled == AWAITING_REAPPROVAL
                        else settled.replace("re-green-lit", "").strip()
                    ),
                })
    return out


def merge_artifact(description: str, block: str) -> str:
    """Splice the managed region into the epic's description, replacing any
    previous one. The CEO-readable plan above it is never touched — that first
    prose paragraph is the plan summary (standards/card-quality.md)."""
    body = description or ""
    if _REGION.search(body):
        return _REGION.sub(lambda _: block, body, count=1)
    return (body.rstrip() + "\n\n" + block + "\n") if body.strip() else block + "\n"


def unrecorded_additions(children, green_lit_at, recorded_ids) -> list[str]:
    """Children added after the green light that the artifact does not account
    for — the epic grew and its plan did not move.

    An unreadable green light yields NOTHING: without the approval timestamp
    every child looks equally new, and accusing the whole roster of silent
    accretion is a confident wrong answer.
    """
    return [
        c["identifier"]
        for c in children
        if added_after_green_light(c.get("createdAt"), green_lit_at) is True
        and c["identifier"] not in set(recorded_ids)
    ]


# --- Linear-touching seams ---------------------------------------------------
#
# Every function below takes the `linear_ops` MODULE as its first argument — the
# convention break_glass and validate_card._bounce already use, so the pure core
# above needs no API key and the tests need no network.

_EPIC_QUERY = """query($id: String!) { issue(id: $id) {
     identifier description state { name }
     children(first: 250) { nodes { identifier createdAt } }
     history(last: 50) { nodes { createdAt toState { name } } }
   } }"""


def read_epic(linear_ops, epic: str) -> dict:
    """The epic as Linear has it: body, lane, children (with creation times) and
    the state history the green light is read out of. One read per epic."""
    data = linear_ops.gql(_EPIC_QUERY, {"id": epic})
    return (data or {}).get("issue") or {}


def green_light_from(history_nodes) -> str | None:
    """The epic's most recent entry into an active lane — its green light.

    Most recent on purpose: an epic returned to Planning by an amendment and
    re-approved was green-lit at the SECOND timestamp, and every card that
    existed by then is part of what was approved.
    """
    stamps = [
        n.get("createdAt")
        for n in (history_nodes or [])
        if ((n.get("toState") or {}).get("name")) in EPIC_ACTIVE_LANES
        and n.get("createdAt")
    ]
    return max(stamps, key=_ts) if stamps else None


def last_green_light(linear_ops, epic: str) -> str | None:
    """When `epic` was last green-lit, or None when Linear cannot say.

    Never raises: the promotion gate calls this per epic per sweep, and an
    unreadable history must abstain (see promotion_refusal), not kill the sweep.
    """
    try:
        return green_light_from(read_epic(linear_ops, epic).get("history", {}).get("nodes"))
    except Exception as exc:  # noqa: BLE001 — an unreadable green light is unknown
        print(f"mid-epic: could not read {epic}'s green light ({exc})", file=sys.stderr)
        return None


def discovery(linear_ops, epic: str, *, kind, because, title=None, body=None,
              labels=()) -> str | None:
    """File a mid-build finding against an already-approved epic.

    Returns the new sibling's identifier for an addition, None for an amendment
    (which creates no card — it sends the epic back for a decision).

    Refuses BEFORE touching Linear when the classification is missing: a sibling
    created and then questioned is already promotable.
    """
    problem = classification_problem(kind, because)
    if problem is not None:
        raise DiscoveryRefused(problem)
    kind = kind.strip().lower()
    # It is a ONE-LINE justification, and it is written into a line-oriented
    # record on the epic: a pasted paragraph must not break the record open.
    because = _one_line(because)
    if kind == AMENDMENT:
        return _amend(linear_ops, epic, because)
    if not (title or "").strip() or not (body or "").strip():
        raise DiscoveryRefused(
            f"an {ADDITION} joins the epic as a new SIBLING card, so it needs a "
            "--title and a --body. (An amendment needs neither — it creates no "
            "card.)"
        )
    return _add(linear_ops, epic, because, title, body, labels)


def _add(linear_ops, epic, because, title, body, labels) -> str:
    """The addition: a sibling under the same epic, its verdict, and the growth
    recorded on the epic — one motion.

    ORDER IS LOAD-BEARING. The card is created, THEN the epic's artifact is
    updated, THEN the verdict is posted. A crash between any two leaves the safe
    half: a card with no verdict cannot promote (promotion_refusal), and a card
    the artifact never recorded is surfaced on the next sweep. The reverse order
    would leave a promotable card the plan never mentioned — the exact silent
    accretion this route exists to replace.
    """
    flags: list[str] = []
    for label in labels or []:
        flags += ["--label", label]
    issue = linear_ops.cmd_subissue(epic, title, body, *flags)
    identifier = issue["identifier"]

    record = {"id": identifier, "because": because}
    refresh_epic_growth(linear_ops, epic, add=record)
    linear_ops.cmd_comment(identifier, verdict_comment(ADDITION, because, epic))
    print(
        f"mid-epic: {identifier} joined {epic} as an {ADDITION} — verdict "
        "recorded, growth recorded, no new green light needed"
    )
    return identifier


def _amend(linear_ops, epic, because) -> None:
    """The amendment: the plan no longer describes the work, so the epic goes
    back to Planning and is re-green-lit. No card is created — there is nothing
    to add until the plan says what it is."""
    linear_ops.cmd_comment(
        epic,
        f"🔁 {AMENDMENT_TAG}: a finding made mid-build says the approved plan no "
        "longer describes the work.\n\n"
        f"**What changed:** {because}\n\n"
        f"**What happens now:** this epic returns to `{AMENDMENT_STATE}` and is "
        "re-green-lit. This is not an addition — an addition is more of a plan "
        "that still holds, and this plan does not. Its growth record on the epic "
        "shows the amendment and, once observed, the re-approval.",
    )
    linear_ops.cmd_state(epic, AMENDMENT_STATE)
    refresh_epic_growth(
        linear_ops, epic,
        amend={"at": _now(), "because": because, "re_green_lit": None},
    )
    print(f"mid-epic: {epic} returned to {AMENDMENT_STATE} on an {AMENDMENT}")
    return None


def refresh_epic_growth(linear_ops, epic: str, *, add=None, amend=None) -> dict:
    """Re-derive the epic's growth artifact from what is live, and report it.

    Does four things, all from truth rather than memory:
      * recounts green-lit vs current cards (children before/after the green
        light read out of the epic's own history);
      * appends the addition/amendment this motion is recording, if any;
      * OBSERVES a re-approval — a pending amendment is settled when the epic is
        seen in an active lane again, never assumed from the return to Planning;
      * surfaces every mid-epic child the artifact does not account for.

    Returns {"green_lit", "current", "unrecorded", "re_approved"}.
    """
    issue = read_epic(linear_ops, epic)
    description = issue.get("description") or ""
    children = (issue.get("children") or {}).get("nodes") or []
    lane = (issue.get("state") or {}).get("name")
    green_lit_at = green_light_from((issue.get("history") or {}).get("nodes"))

    artifact = parse_artifact(description)
    additions = list(artifact["additions"])
    amendments = list(artifact["amendments"])
    if add and add["id"] not in {a["id"] for a in additions}:
        additions.append(add)
    if amend:
        amendments.append(amend)

    # Re-approval, OBSERVED: the epic is in an active lane again. The timestamp
    # recorded is the green light Linear reports; when the history is unreadable
    # the observation still stands and says so rather than inventing a time.
    re_approved: list[str] = []
    if lane in EPIC_ACTIVE_LANES:
        for am in amendments:
            if am["re_green_lit"] is None:
                am["re_green_lit"] = green_lit_at or "observed (time unreadable)"
                re_approved.append(epic)

    green_lit = (
        sum(
            1 for c in children
            if added_after_green_light(c.get("createdAt"), green_lit_at) is False
        )
        if green_lit_at
        else None
    )
    unrecorded = unrecorded_additions(
        children, green_lit_at, [a["id"] for a in additions]
    )

    block = render_artifact(green_lit, len(children), additions, amendments)
    merged = merge_artifact(description, block)
    if merged != description:
        linear_ops.set_description(epic, merged)

    if re_approved and not linear_ops.count_comments(epic, REAPPROVAL_TAG):
        linear_ops.cmd_comment(
            epic,
            f"✅ {REAPPROVAL_TAG}: this epic went back to `{AMENDMENT_STATE}` on "
            "an amendment and has been green-lit again — observed in an active "
            "lane, recorded on the epic's growth record. Its children promote "
            "against this approval from here.",
        )
    for ident in unrecorded:
        needle = f"{UNRECORDED_TAG}: {ident}"
        if linear_ops.count_comments(epic, needle):
            continue
        linear_ops.cmd_comment(
            epic,
            f"🚨 {needle} was added to this epic after it was green-lit, and the "
            "epic's plan did not change with it. Silent accretion turns an "
            "approved scope into an unapproved one with no single decision being "
            "wrong — so the growth is named here rather than policed.\n\n"
            f"This epic was green-lit at "
            f"{green_lit if green_lit is not None else 'an unreadable count of'} "
            f"cards and is running {len(children)}. If the plan still describes "
            "the work, file the card as an addition (`scripts/mid_epic.py "
            f"discovery {epic} --kind {ADDITION} --because \"…\"`) so it carries "
            f"a verdict; if it does not, file an {AMENDMENT}.",
        )

    print(growth_line(epic, green_lit, len(children)))
    return {
        "green_lit": green_lit,
        "current": len(children),
        "unrecorded": unrecorded,
        "re_approved": re_approved,
    }


# --- helpers -----------------------------------------------------------------


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))


def _one_line(text) -> str:
    """Collapse any run of whitespace to a single space. Nothing is dropped —
    the whole justification survives, on one line."""
    return " ".join((text or "").split())


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _leading_int(text: str) -> int | None:
    m = re.search(r"\d+", text or "")
    return int(m.group(0)) if m else None


# --- CLI ---------------------------------------------------------------------


def _parse_argv(argv) -> dict:
    """--kind/--because/--title/--body/--label. Hand-rolled to match the rest of
    scripts/ (linear_ops._parse_flags), which takes no argparse dependency."""
    out: dict = {"labels": []}
    keys = {"--kind": "kind", "--because": "because", "--title": "title",
            "--body": "body"}
    it = iter(argv)
    for tok in it:
        if tok == "--label":
            out["labels"].append(next(it))
        elif tok in keys:
            out[keys[tok]] = next(it)
    return out


def cmd_discovery(epic: str, *argv) -> None:
    import os

    import linear_ops

    args = _parse_argv(argv)
    body = args.get("body")
    if body and os.path.isfile(body):
        with open(body) as f:
            body = f.read()
    try:
        ident = discovery(
            linear_ops, epic,
            kind=args.get("kind"), because=args.get("because"),
            title=args.get("title"), body=body, labels=args["labels"],
        )
    except DiscoveryRefused as exc:
        raise SystemExit(f"❌ discovery REFUSED: {exc}")
    if ident:
        print(ident)


def cmd_audit(epic: str) -> None:
    import linear_ops

    report = refresh_epic_growth(linear_ops, epic)
    if report["unrecorded"]:
        print("added without the plan changing: " + ", ".join(report["unrecorded"]))


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"discovery": cmd_discovery, "audit": cmd_audit}[cmd](*args)
