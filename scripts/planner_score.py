#!/usr/bin/env python3
"""Score the planner against plans it has never seen (DRE-3016).

The critic has an audit — DRE-2685's `critic_score.py` compares its verdicts
against a human review it was never shown, refuses to score a contaminated
dimension, and reports agreement and disagreement as equal results. The planner
has had nothing. It decomposes an epic, the CEO green-lights it, and whether the
decomposition was any good is learned one fix loop at a time.

This is the same instrument, pointed at the planner.

## The held-out answer already exists, mechanically

Every planner-created child that has since merged left a record saying whether
the plan was right about it, and none of it needs a human to re-read a card:

  * the **file footprint** the card declared, against the files its merged PR
    actually touched;
  * which cards the plan said **collide** (a `blockedBy` edge), against which
    pairs really shared a file when they merged;
  * the card's **size**, against whether the run died at the turn cap;
  * the card was **build-ready** at creation, against the readiness guard's own
    return receipt;
  * the **routing verdict**, against what the card turned out to need — an
    escalation from a FLEET card is a mis-route;
  * a **proof card and a demo card** exist;
  * the plan was **approved as written**, against the plan critic's send-backs
    and the mid-epic amendment markers.

The planner could not see any of it. History happened afterwards, which is what
makes it a review the planner has never seen.

## The exclusion is the whole thing (DRE-2685's rule, one role over)

`proof-and-demo` is **contaminated and never scored**. `plan.yml` runs
`proof_and_demo.py check` and bounces the epic back to Planning until the pair
exists — the planner cannot leave the workflow without the answer. Scoring it
reports a perfect row composed entirely of what the gate refused to let
through, exactly the way `hand-built` flattered the critic's audit.

That exclusion is CHECKED, not asserted: `reference_problems` reads `plan.yml`
and refuses a file whose contaminated dimension names a gate nothing runs. If
the gate is ever taken out, the dimension stops being contaminated and this
module says so instead of quietly keeping a stale exclusion.

## A row nobody could read is UNKNOWN

Never `0`, never "clean". A child whose PR cannot be read reports `unknown` and
is left out of the number — the absence of evidence is not evidence of a good
plan. Unknown rows are printed under their own heading, the same way the
excluded ones are.

## Both halves, empty or not

`render_report` always prints Agreement AND Disagreement. An audit that prints
only its hits is a marketing document.

## The replay harness — what must not happen

A replay freezes an already-planned epic at its **pre-plan text**, files it as a
throwaway `PROOF-PL-<n>` epic labelled `repo:agent-bureau-demo` so nothing ships,
lets `plan.yml` plan it, and scores the new plan against the same history. Two
rules bound it, both enforced here rather than remembered:

  * `replay_problems` refuses a replay card that is not labelled for the demo
    repo, or whose title is not a throwaway.
  * `plan_leaks` refuses a replay whose context contains the historical plan. A
    replay that was shown the answer is not a replay; it is DISCARDED and the
    leak is recorded (`leak_record`), never silently scored.

Pure functions over records with one thin CLI seam — no Linear client and no
GitHub calls inside the scoring — so the workflow, the tests and a hand run all
exercise the same code.

CLI:

    python3 scripts/planner_score.py check                     # the reference
    python3 scripts/planner_score.py score --epic DRE-N [--out J] [--report M]
    python3 scripts/planner_score.py replay-card --epic DRE-N --n 1 [--out J]
    python3 scripts/planner_score.py leak-check --plan P --context C
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dead_run  # noqa: E402
import mid_epic  # noqa: E402
import plan_critic  # noqa: E402
import proof_and_demo  # noqa: E402
import routing_verdict  # noqa: E402
import validate_card  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
REFERENCE_PATH = os.path.join(ROOT, "config", "planner-audit.json")
PLAN_WORKFLOW = os.path.join(ROOT, ".github", "workflows", "plan.yml")

#: The dimension the planner is handed face-up. Named once; the identity is
#: checked against the workflow that hands it over.
CONTAMINATED_DIMENSION = "proof-and-demo"

#: Every dimension this module can emit a row for. `reference_problems` pins
#: this against the file, in both directions: a dimension declared and never
#: scored is as wrong as a row for a dimension nobody declared.
DIMENSIONS = (
    "file-footprint",
    "collision",
    "size",
    "readiness",
    "routing",
    "approval",
    CONTAMINATED_DIMENSION,
)

#: Every value each dimension's readers can produce. Checked against the
#: declared vocabulary for DRE-2685's reason: a value the reference cannot hold
#: comes out as a disagreement neither side ever expressed.
EMITTED_VALUES = {
    "file-footprint": ("within-footprint", "outside-footprint"),
    "collision": ("collides", "independent"),
    "size": ("one-pr", "too-big"),
    "readiness": ("build-ready", "bounced"),
    "routing": ("dispatchable", "needs-a-person"),
    "approval": ("as-written", "revised"),
    CONTAMINATED_DIMENSION: ("both-present", "missing"),
}

OUTCOMES = ("agree", "disagree", "unknown", "unclaimed", "excluded")

# --------------------------------------------------------------------------- #
# the receipts — the pipeline's own words, never a second spelling             #
# --------------------------------------------------------------------------- #
#
# Each of these is written somewhere else in the repo, and
# tests/test_planner_score.py asserts that the writer still spells it this way.
# A marker that drifts reads here as "nothing ever happened", which is the
# silent zero this module exists to refuse.

#: `validate_card._bounce` — the readiness guard returning a card to Planning.
READINESS_BOUNCE_PREFIX = "🚧 Not ready for build — missing:"

#: `agent-task.yml` — the agent stopped and asked the CEO for a decision.
ESCALATION_RECEIPT_PREFIX = (
    "🙋 The agent paused for a decision before building"
)

#: `agent-task.yml` — the agent found an epic inside a one-off card.
HANDBACK_RECEIPT_PREFIX = "🤖 Handed back to Planning:"

#: `dead_run.TURN_TAG` — the run died at the turn cap and the card was requeued.
TURN_CAP_TAG = dead_run.TURN_TAG

#: A turn-cap receipt in the shape `dead_run` posts it, for the tests and for
#: anyone reading what this module matches on.
TURN_CAP_RECEIPT_SAMPLE = (
    f"🪦 {TURN_CAP_TAG}: the run hit its turn cap — requeued once"
)

#: `mid_epic.AMENDMENT_TAG` — the approved plan no longer describes the work.
AMENDMENT_TAG = mid_epic.AMENDMENT_TAG

#: The `**Files:**` line the planner brief makes the INPUT to the ordering.
#: Anchored at the start of a line, optionally bold, optionally inside a list
#: item — the same anchoring rule every other marker in this pipeline follows,
#: because a sentence mentioning files declares nothing.
_FILES_LINE = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+|\d+\.[ \t]+)?\*{0,2}Files:\*{0,2}[ \t]*(?P<rest>.*)$",
    re.MULTILINE | re.IGNORECASE,
)

#: A path-shaped token inside that line. `none yet` is not an answer
#: (briefs/planner.md), and it does not match this either.
_PATH = re.compile(r"[A-Za-z0-9_.@/-]*[A-Za-z0-9_-]+\.[A-Za-z0-9]+|[A-Za-z0-9_./-]+/")

_CARD_ID = re.compile(r"^[A-Z]+-\d+$")


class AuditError(RuntimeError):
    """The reference file is malformed. Raised rather than defaulted: a
    reference that silently loses a dimension reports a smaller audit in the
    same shape of number."""


class LeakedPlan(RuntimeError):
    """The replay was shown the plan it is being scored against.

    Refused rather than scored. A replay that can read the historical plan is
    not "a review it has never seen" — it is a transcription exercise, and its
    agreement means nothing.
    """


# --------------------------------------------------------------------------- #
# the reference                                                                #
# --------------------------------------------------------------------------- #

_CACHE: dict = {}


def load(path: str | None = None) -> dict:
    path = path or REFERENCE_PATH
    if path not in _CACHE:
        try:
            with open(path, encoding="utf-8") as fh:
                _CACHE[path] = json.load(fh)
        except (OSError, ValueError) as e:
            raise AuditError(f"cannot read the audit reference at {path}: {e}") from e
    return _CACHE[path]


def source(doc: dict | None = None) -> dict:
    """Which epics were audited, when, and where the run is recorded. An audit
    whose other half cannot be found is not a comparison."""
    return dict((doc or load())["source"])


def dimensions(doc: dict | None = None) -> dict:
    return dict((doc or load())["dimensions"])


def is_scored(dimension: str, doc: dict | None = None) -> bool:
    return bool(dimensions(doc).get(dimension, {}).get("scored"))


def gate_is_enforced(script: str, workflow: str | None = None) -> bool:
    """Does `plan.yml` actually run `script`?

    The load-bearing half of the exclusion. `proof-and-demo` is contaminated
    BECAUSE the plan workflow refuses to let the planner out without the pair;
    take the gate away and the dimension stops being contaminated, and an
    exclusion nobody re-checked would keep a real result out of the number.
    """
    try:
        with open(workflow or PLAN_WORKFLOW, encoding="utf-8") as fh:
            return script in fh.read()
    except OSError:
        return False


def reference_problems(doc: dict | None = None) -> list:
    """Everything wrong with the audit reference, or an empty list."""
    doc = doc if doc is not None else load()
    problems: list[str] = []

    src = source(doc)
    epics = list(src.get("epics") or ())
    if not epics:
        problems.append(
            "the reference names no epic — an audit with no population is a "
            "shape, not a result"
        )
    for identifier in epics:
        if not _CARD_ID.match(str(identifier)):
            problems.append(f"{identifier!r} is not an epic identifier")
    if not (src.get("record") or "").strip():
        problems.append(
            "the reference says nothing for 'record' — a run nobody can go and "
            "read is not evidence"
        )

    declared = dimensions(doc)
    for name in DIMENSIONS:
        if name not in declared:
            problems.append(
                f"the scorer emits rows for {name!r} and the reference does not "
                "declare it, so those rows would be scored against nothing"
            )
    for name in declared:
        if name not in DIMENSIONS:
            problems.append(
                f"dimension {name!r} is declared and nothing scores it — a "
                "dimension no reader answers is a promise, not an audit"
            )

    for name, block in declared.items():
        if not (block.get("question") or "").strip():
            problems.append(f"dimension {name!r} states no question")
        if not block.get("values"):
            problems.append(f"dimension {name!r} carries no values")
        emitted = set(EMITTED_VALUES.get(name, ()))
        unknown = sorted(emitted - set(block.get("values") or ()))
        if unknown:
            problems.append(
                f"dimension {name!r} can be answered {unknown}, which it does "
                "not carry — a value the reference cannot hold comes out as a "
                "disagreement neither side ever expressed"
            )
        if block.get("scored"):
            if not (block.get("why") or "").strip():
                problems.append(
                    f"dimension {name!r} is scored and does not say what "
                    "history answers it with"
                )
            continue
        if not (block.get("contaminated") or "").strip():
            problems.append(
                f"dimension {name!r} is excluded and does not say why — an "
                "unexplained exclusion is indistinguishable from a convenient one"
            )
        gate = (block.get("enforced_by") or "").strip()
        if not gate:
            problems.append(
                f"dimension {name!r} is excluded and names no gate that hands "
                "the planner the answer"
            )
        elif not gate_is_enforced(gate):
            problems.append(
                f"dimension {name!r} is excluded because {gate} hands the "
                "planner the answer, and the plan workflow does not run "
                f"{gate} — the exclusion is keeping a real result out of the "
                "number"
            )

    if CONTAMINATED_DIMENSION in declared and is_scored(CONTAMINATED_DIMENSION, doc):
        problems.append(
            f"{CONTAMINATED_DIMENSION!r} is scored — plan.yml bounces the epic "
            "until the pair exists, so every row it produces was handed over "
            "face-up (DRE-2685)"
        )

    if REPLAY_REPO not in validate_card.VALID_SLUGS:
        problems.append(
            f"the replay files its epics in {REPLAY_REPO!r}, which the rail "
            "does not route — a throwaway card nothing picks up is a card "
            "nobody notices, in the wrong place"
        )
    return problems


# --------------------------------------------------------------------------- #
# what the plan claimed                                                        #
# --------------------------------------------------------------------------- #


def declared_files(body: str) -> list:
    """The footprint a card declares, in the order it declares it.

    `briefs/planner.md`: "Every card you create declares the files it will
    create or edit, as a `**Files:**` line in its body. That line is not
    documentation added at the end; it is the INPUT to the ordering."

    So the absence of one is not a small omission — it is the plan declining to
    make the claim the ordering was supposed to be derived from, and `score`
    reports it as `unclaimed` rather than letting it pass as agreement.
    """
    match = _FILES_LINE.search(body or "")
    if not match:
        return []
    # The template wraps: the declaration continues on indented lines until a
    # blank line or the next block.
    tail = (body or "")[match.end():]
    text = match.group("rest")
    # `.*$` stops before the newline, so the first split element is the empty
    # remainder of the matched line — the continuation starts after it.
    for line in tail.split("\n")[1:]:
        if not line.strip() or not line.startswith((" ", "\t")):
            break
        text += " " + line.strip()
    out: list[str] = []
    for token in _PATH.findall(text.replace("`", "")):
        token = token.strip(" ,;")
        if token and token not in out:
            out.append(token)
    return out


def claimed_verdict(comment_bodies) -> str | None:
    """The routing verdict the planning segment stamped on a card, or None.

    Read off the card's own comment, never re-derived: what is being audited is
    what the plan SAID, and re-deriving it would score the classifier against
    itself. A card carrying two verdicts is read as carrying none.
    """
    try:
        return routing_verdict.verdict_on(comment_bodies or ())
    except routing_verdict.ConflictingVerdicts:
        return None


def claimed_route(verdict: str | None) -> str | None:
    """A routing verdict read as *who did this card turn out to need*.

    FLEET is the one verdict that is dispatched; every other one is marked
    `hand-built` by the vocabulary and means a person. Derived from the marks
    rather than listed here, so a sixth verdict cannot quietly read as
    dispatchable.
    """
    if not verdict:
        return None
    return ("needs-a-person"
            if "hand-built" in routing_verdict.marks(verdict)
            else "dispatchable")


# --------------------------------------------------------------------------- #
# what history says                                                            #
# --------------------------------------------------------------------------- #


def _opens_with(bodies, prefix: str) -> bool:
    """Anchored at the START of a comment, the same rule `critic_score`'s
    `already_alerted` follows: a comment that QUOTES a receipt is not one."""
    return any((body or "").lstrip().startswith(prefix) for body in bodies or ())


def bounced_at_readiness(comment_bodies) -> bool:
    return _opens_with(comment_bodies, READINESS_BOUNCE_PREFIX)


def needed_a_person(comment_bodies) -> bool:
    """Did the card turn out to need a human? An escalation or a hand-back from
    a card the plan routed FLEET is a mis-route — the plan said an unattended
    agent could satisfy the criteria and it could not."""
    return (_opens_with(comment_bodies, ESCALATION_RECEIPT_PREFIX)
            or _opens_with(comment_bodies, HANDBACK_RECEIPT_PREFIX))


def died_at_the_turn_cap(comment_bodies) -> bool:
    """Two turn-cap deaths on one card means SPLIT (standards/card-quality.md).
    One is already the plan being wrong about the size."""
    return any(TURN_CAP_TAG in (body or "") for body in comment_bodies or [])


def plan_was_revised(comment_bodies) -> bool:
    """Was the plan approved as written?

    Two records say no, and both are the pipeline's own: a plan-critic round
    whose result is SEND_BACK, and a mid-epic AMENDMENT — the finding that says
    the approved plan no longer describes the work. The critic's markers are
    read through `plan_critic.parse_markers`, which is what makes a marker
    QUOTED in prose not count.
    """
    if any(row["result"] == plan_critic.SEND_BACK
           for row in plan_critic.parse_markers(list(comment_bodies or []))):
        return True
    return any(AMENDMENT_TAG in (body or "") for body in comment_bodies or [])


def merged_files(child: dict):
    """The files the card's merged PR touched, or None when GitHub would not
    say. None is the UNKNOWN input — never an empty list, which would read as
    "it touched nothing" and score as a clean footprint."""
    pr = child.get("pr")
    if not pr:
        return None
    files = pr.get("files")
    return None if files is None else list(files)


def shipped(child: dict) -> bool:
    """Did this card actually go through the build path? A card that never ran
    answers none of the history questions, and saying so is the point."""
    return bool((child.get("pr") or {}).get("merged"))


# --------------------------------------------------------------------------- #
# the rows                                                                     #
# --------------------------------------------------------------------------- #


def _row(card, dimension, claimed, observed, outcome, why, evidence=""):
    return {
        "card": card,
        "dimension": dimension,
        "claimed": claimed,
        "observed": observed,
        "outcome": outcome,
        "why": why,
        "evidence": evidence,
    }


def _compare(card, dimension, claimed, observed, why, evidence=""):
    return _row(card, dimension, claimed, observed,
                "agree" if claimed == observed else "disagree", why, evidence)


def _footprint_rows(children) -> list:
    rows = []
    for child in children:
        identifier = child["identifier"]
        declared = declared_files(child.get("body") or "")
        if not declared:
            rows.append(_row(
                identifier, "file-footprint", None, None, "unclaimed",
                "the card declares no `**Files:**` line, so the plan made no "
                "footprint claim — and the ordering had nothing to be derived "
                "from (briefs/planner.md)",
            ))
            continue
        touched = merged_files(child)
        if touched is None:
            rows.append(_row(
                identifier, "file-footprint", "within-footprint", None, "unknown",
                "this card's pull request could not be read, so what it touched "
                "is unknown — reported rather than scored as clean",
            ))
            continue
        undeclared = [path for path in touched if path not in declared]
        rows.append(_compare(
            identifier, "file-footprint", "within-footprint",
            "within-footprint" if not undeclared else "outside-footprint",
            f"declared {len(declared)} file(s), the merged PR touched "
            f"{len(touched)}",
            ", ".join(undeclared) if undeclared else "—",
        ))
    return rows


def _pair_name(a: str, b: str) -> str:
    return " ↔ ".join(sorted((a, b)))


def _collision_rows(children) -> list:
    """One row per pair the plan or history had something to say about.

    The population is deliberately NOT every pair. n cards have n(n-1)/2 pairs
    and almost all of them are trivially independent; booking those as
    agreements would hand the planner a score that grows with the size of the
    epic and says nothing. A pair is reported when the plan wired an edge
    between the two, when their declared footprints intersect, or when their
    merged PRs really did share a file — which is exactly the set where being
    wrong costs something (DRE-2837/2838: three PRs, full review each, all
    DIRTY within an hour, purely on merge order).
    """
    by_id = {c["identifier"]: c for c in children}
    rows = []
    for a, b in itertools.combinations(sorted(by_id), 2):
        first, second = by_id[a], by_id[b]
        edge = (b in (first.get("blocked_by") or [])
                or a in (second.get("blocked_by") or []))
        declared_a = set(declared_files(first.get("body") or ""))
        declared_b = set(declared_files(second.get("body") or ""))
        touched_a, touched_b = merged_files(first), merged_files(second)
        really_shared = (set(touched_a) & set(touched_b)
                         if touched_a is not None and touched_b is not None
                         else set())
        if not (edge or (declared_a & declared_b) or really_shared):
            continue
        claimed = "collides" if edge else "independent"
        pair = _pair_name(a, b)
        if touched_a is None or touched_b is None:
            rows.append(_row(
                pair, "collision", claimed, None, "unknown",
                "one of the two pull requests could not be read, so whether "
                "they really shared a file is unknown",
            ))
            continue
        rows.append(_compare(
            pair, "collision", claimed,
            "collides" if really_shared else "independent",
            "the plan wired a blockedBy edge between them" if edge else
            "the plan left them parallel",
            ", ".join(sorted(really_shared)) if really_shared else "—",
        ))
    return rows


def _size_rows(children) -> list:
    rows = []
    for child in children:
        identifier = child["identifier"]
        comments = child.get("comments") or []
        if died_at_the_turn_cap(comments):
            rows.append(_compare(
                identifier, "size", "one-pr", "too-big",
                "the run died at the turn cap and the card was requeued — the "
                f"card's own `{TURN_CAP_TAG}` receipt",
            ))
            continue
        if not shipped(child):
            rows.append(_row(
                identifier, "size", "one-pr", None, "unknown",
                "the card has no merged pull request and no turn-cap receipt, "
                "so whether it was one PR's worth was never put to the test",
            ))
            continue
        rows.append(_compare(
            identifier, "size", "one-pr", "one-pr",
            "it merged as one pull request without hitting the turn cap",
        ))
    return rows


def _readiness_rows(children) -> list:
    rows = []
    for child in children:
        identifier = child["identifier"]
        comments = child.get("comments") or []
        if bounced_at_readiness(comments):
            rows.append(_compare(
                identifier, "readiness", "build-ready", "bounced",
                "the readiness guard returned this card rather than letting it "
                "into the build path",
            ))
            continue
        if not shipped(child):
            rows.append(_row(
                identifier, "readiness", "build-ready", None, "unknown",
                "the card never reached a merged pull request, so the readiness "
                "guard's answer for it cannot be read",
            ))
            continue
        rows.append(_compare(
            identifier, "readiness", "build-ready", "build-ready",
            "it passed the readiness guard and shipped",
        ))
    return rows


def _routing_rows(children) -> list:
    rows = []
    for child in children:
        identifier = child["identifier"]
        comments = child.get("comments") or []
        claimed = claimed_route(claimed_verdict(comments))
        if claimed is None:
            rows.append(_row(
                identifier, "routing", None, None, "unclaimed",
                "the card carries no routing verdict, so the planning segment "
                "made no claim about who builds it — a gap to report, never a "
                "verdict to invent (briefs/engineer.md)",
            ))
            continue
        if needed_a_person(comments):
            rows.append(_compare(
                identifier, "routing", claimed, "needs-a-person",
                "the agent stopped and asked for a decision, or handed the card "
                "back — the card needed a person",
            ))
            continue
        if not shipped(child):
            rows.append(_row(
                identifier, "routing", claimed, None, "unknown",
                "the card neither shipped nor escalated, so what it turned out "
                "to need is unknown",
            ))
            continue
        rows.append(_compare(
            identifier, "routing", claimed, "dispatchable",
            "an unattended agent built it and it merged",
        ))
    return rows


def _approval_rows(epic, children) -> list:
    identifier = epic["identifier"]
    comments = epic.get("comments") or []
    if plan_was_revised(comments):
        return [_compare(
            identifier, "approval", "as-written", "revised",
            "the plan critic sent this plan back, or the epic carries a "
            f"`{AMENDMENT_TAG}` marker — the plan did not survive as written",
        )]
    if not any(shipped(child) for child in children):
        return [_row(
            identifier, "approval", "as-written", None, "unknown",
            "no child of this epic has a merged pull request, so whether the "
            "plan held is unknown",
        )]
    return [_compare(
        identifier, "approval", "as-written", "as-written",
        "no send-back and no amendment: the plan the CEO approved is the plan "
        "that ran",
    )]


def _proof_and_demo_rows(epic, children) -> list:
    """The contaminated row. Computed so it can be REPORTED — never dropped,
    because a number with rows silently removed is worse than no number."""
    titles = [child.get("title") or "" for child in children]
    present = (any(proof_and_demo.is_proof(t) for t in titles)
               and any(proof_and_demo.is_demo(t) for t in titles))
    return [_row(
        epic["identifier"], CONTAMINATED_DIMENSION,
        "both-present", "both-present" if present else "missing", "excluded",
        "",  # filled in by score(), which holds the reference's own wording
        f"{proof_and_demo.PROOF_PREFIX} and {proof_and_demo.DEMO_PREFIX} "
        "children: " + ("both" if present else "not both"),
    )]


# --------------------------------------------------------------------------- #
# the score                                                                    #
# --------------------------------------------------------------------------- #


def score(epic: dict, children: list, *, doc: dict | None = None,
          replay: dict | None = None) -> dict:
    """Score one epic's plan against what its children actually did.

    Every row comes out carrying exactly one outcome — agree, disagree,
    unknown, unclaimed or excluded — because a row that quietly fell out of the
    population is the same failure as a silent zero: a smaller set reported in
    the same shape.

    `replay`, when given, is `{"epic", "context", "plan"}` from a replay run.
    A replay whose context contains the historical plan is DISCARDED: `score`
    raises `LeakedPlan` rather than reporting a number it cannot stand behind.
    """
    doc = doc if doc is not None else load()
    leaks: list[str] = []
    if replay is not None:
        leaks = plan_leaks(replay.get("context") or "", replay.get("plan") or "")
        if leaks:
            raise LeakedPlan(leak_record(replay.get("epic") or epic["identifier"],
                                         leaks))

    rows = (_footprint_rows(children) + _collision_rows(children)
            + _size_rows(children) + _readiness_rows(children)
            + _routing_rows(children) + _approval_rows(epic, children)
            + _proof_and_demo_rows(epic, children))

    declared = dimensions(doc)
    final: list[dict] = []
    for row in rows:
        block = declared.get(row["dimension"], {})
        if not block.get("scored"):
            final.append({**row, "outcome": "excluded", "why": (
                f"the {row['dimension']!r} dimension is excluded as "
                f"contaminated — {block.get('contaminated', '')}")})
            continue
        if row["outcome"] == "excluded":
            # Declared scored after all: compare it like any other row rather
            # than carrying the exclusion the reader asked us to drop.
            final.append(_compare(row["card"], row["dimension"], row["claimed"],
                                  row["observed"],
                                  "scored because the reference marks this "
                                  "dimension scored", row["evidence"]))
            continue
        final.append(row)

    counts = {name: sum(1 for r in final if r["outcome"] == name)
              for name in OUTCOMES}
    return {
        "epic": epic["identifier"],
        "title": epic.get("title") or "",
        "source": source(doc),
        "children": len(children),
        "rows": final,
        "counts": counts,
        "scored": counts["agree"] + counts["disagree"],
        "replay": {"leaks": leaks, **({"epic": replay.get("epic")}
                                      if replay else {})} if replay else None,
    }


# --------------------------------------------------------------------------- #
# the replay harness                                                           #
# --------------------------------------------------------------------------- #

#: The one repo a replay may file a card in. Nothing ships from it and no
#: product sweep sees it.
REPLAY_REPO = "agent-bureau-demo"

#: The throwaway title prefix, so a replay epic is recognisable at a glance and
#: cannot be mistaken for real work.
REPLAY_PREFIX = "PROOF-PL-"

#: The labels a replay epic carries. `agent:planner` because the point is to
#: make `plan.yml` plan it; the repo label is the routing source of truth.
REPLAY_LABELS = (f"repo:{REPLAY_REPO}", "agent:planner")


def replay_title(source_epic: str, n: int) -> str:
    return f"{REPLAY_PREFIX}{int(n)}: replay of {source_epic} (throwaway)"


def replay_card(source_epic: str, n: int, body: str) -> dict:
    """The throwaway epic a replay files: the source epic frozen at its
    pre-plan text, in the demo repo, under a throwaway title."""
    return {
        "title": replay_title(source_epic, n),
        "body": pre_plan_text(body),
        "labels": list(REPLAY_LABELS),
        "source_epic": source_epic,
    }


def replay_problems(card: dict) -> list:
    """Everything that would let a replay escape the demo repo, or an empty list.

    The card's one hard rule: the harness never writes to a product repo and
    never files a card outside `repo:agent-bureau-demo`. Checked here, before
    anything is created, because the cheapest place to stop a card being filed
    in the wrong repo is before it exists.
    """
    problems: list[str] = []
    labels = list(card.get("labels") or ())
    slugs = [label.split(":", 1)[1] for label in labels
             if label.startswith("repo:") and ":" in label]
    if not slugs:
        problems.append(
            "the replay card carries no repo: label, so the relay would route "
            f"it by inference — it must name {REPLAY_REPO} explicitly"
        )
    for slug in slugs:
        if slug != REPLAY_REPO:
            problems.append(
                f"the replay card is labelled repo:{slug} — a replay may only "
                f"file cards in {REPLAY_REPO}, and never in a product repo"
            )
    if not (card.get("title") or "").startswith(REPLAY_PREFIX):
        problems.append(
            f"the replay card's title does not open with {REPLAY_PREFIX} — a "
            "throwaway epic that does not say so is a real epic to everyone "
            "who reads the board"
        )
    if not (card.get("body") or "").strip():
        problems.append("the replay card has no body — there is nothing to plan")
    return problems


def pre_plan_text(description: str) -> str:
    """The epic as the CEO wrote it: the description with the planner's own
    managed region taken out.

    `mid_epic` splices the epic's growth record into the description, and that
    record is planner output — how many cards the plan was green-lit at, which
    ones joined later. Handing it to a replay is handing back a piece of the
    plan the replay is supposed to reproduce.
    """
    text = description or ""
    begin, end = mid_epic.ARTIFACT_BEGIN, mid_epic.ARTIFACT_END
    while begin in text and end in text:
        head, _, rest = text.partition(begin)
        _, _, tail = rest.partition(end)
        text = head.rstrip() + "\n" + tail.lstrip("\n")
    return text.strip() + "\n"


#: Lines short enough, or common enough, that finding them in a replay's
#: context says nothing. `## The cards` is in every plan artifact AND in the
#: planner's brief; reporting it would make every replay a leak, which is
#: exactly as useful as reporting none.
_LEAK_MIN_WORDS = 6


def _leak_candidates(plan: str) -> list:
    out = []
    for line in (plan or "").splitlines():
        stripped = line.strip().lstrip("#-*+ ").strip()
        if len(stripped.split()) < _LEAK_MIN_WORDS:
            continue
        out.append(stripped)
    return out


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def plan_leaks(context: str, plan: str) -> list:
    """Lines of the historical plan that appear in the replay's context.

    A replay is "a review it has never seen" only while the planner cannot read
    the answer. This is the mechanical check on that claim: distinctive lines of
    the historical plan, looked for in whatever the replay run was handed.

    Deliberately conservative in one direction and not the other. Short and
    boilerplate lines are ignored, because a check that fires on `## The cards`
    fires on every replay and gets switched off. Anything longer is reported,
    because a false leak costs one discarded replay and a missed one costs the
    whole result.
    """
    haystack = _normalise(context)
    if not haystack:
        return []
    return [line for line in _leak_candidates(plan)
            if _normalise(line) in haystack]


def leak_record(source_epic: str, leaks: list) -> str:
    """The record of a discarded replay. The leak is written down, not just
    refused — a replay quietly re-run until it comes out clean is the same
    failure as an audit that prints only its hits."""
    lines = [
        f"🧪 planner-replay-leak: the replay of {source_epic} was DISCARDED — "
        "the historical plan reached the planner's context, so this run was "
        "not a plan it had never seen.",
        "",
        "**What leaked:**",
        "",
    ]
    lines += [f"- `{line}`" for line in leaks]
    lines += [
        "",
        "Nothing from this replay is scored. Fix the context the replay is "
        "handed, then run it again — a replay that saw the answer cannot be "
        "salvaged by scoring it more carefully.",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# diffing a replay's plan against the historical one                           #
# --------------------------------------------------------------------------- #

#: The columns of a plan's shape, in the order the diff prints them, each with
#: the one sentence that says how to read it.
SHAPE_COLUMNS = (
    ("cards", "how many cards the epic was cut into"),
    ("with-footprint", "cards declaring a `**Files:**` line"),
    ("footprint-collisions", "pairs whose DECLARED footprints intersect"),
    ("serialized-pairs", "pairs wired with a real `blockedBy` relation"),
    ("with-verdict", "cards carrying a routing verdict"),
    ("proof-and-demo", "the epic ends with both cards"),
)


def plan_shape(children: list) -> dict:
    """What a plan DECLARED, counted — with no reference to what happened next.

    This is the half of a replay that can be compared without inventing
    anything. A per-card diff would need a correspondence between the replay's
    new cards and the historical children; nothing can establish that
    mechanically, and with the footprint line missing from the population there
    is not even a file list to match on.

    Every count reads the planner's OUTPUT the way `proof_and_demo` does — the
    cards themselves, out of Linear. `serialized-pairs` counts formal
    `blockedBy` relations only: a `**Blocked by:**` prose line documents a
    relation and cannot create one (DRE-2670, DRE-2676), so counting the
    sentence would report a plan as serialized that the board never was.
    """
    footprints = {c["identifier"]: set(declared_files(c.get("body") or ""))
                  for c in children}
    titles = [c.get("title") or "" for c in children]
    edges = 0
    intersecting = 0
    for a, b in itertools.combinations(sorted(footprints), 2):
        by_id = {c["identifier"]: c for c in children}
        if (b in (by_id[a].get("blocked_by") or [])
                or a in (by_id[b].get("blocked_by") or [])):
            edges += 1
        if footprints[a] & footprints[b]:
            intersecting += 1
    return {
        "cards": len(children),
        "with-footprint": sum(1 for f in footprints.values() if f),
        "footprint-collisions": intersecting,
        "serialized-pairs": edges,
        "with-verdict": sum(1 for c in children
                            if claimed_verdict(c.get("comments") or ())),
        "proof-and-demo": (any(proof_and_demo.is_proof(t) for t in titles)
                           and any(proof_and_demo.is_demo(t) for t in titles)),
    }


def render_diff(before_epic: str, before: dict,
                after_epic: str, after: dict) -> str:
    """The replay's plan beside the plan it is replaying.

    Deliberately says what it is NOT, in the document itself: a shape diff is
    not a per-card comparison, and reading it as one would turn "the replay cut
    five cards where the original cut three" into a claim about which cards.
    """
    out = [
        f"# {after_epic} replaying {before_epic} — the two plans, side by side",
        "",
        f"**{before_epic}** is the plan that ran; **{after_epic}** is the "
        "replay, planned from the same pre-plan text with the historical plan "
        "held out.",
        "",
        "This is **not a per-card comparison.** Nothing can mechanically say "
        "which replay card corresponds to which historical child, so what is "
        "diffed is the SHAPE of the decomposition. Read a moved number as a "
        "question to go and look at, never as a verdict on a card.",
        "",
        "| | " + before_epic + " | " + after_epic + " | how to read it |",
        "| --- | --- | --- | --- |",
    ]
    for key, blurb in SHAPE_COLUMNS:
        out.append(f"| `{key}` | {before.get(key)} | {after.get(key)} | {blurb} |")
    out += [
        "",
        f"`{CONTAMINATED_DIMENSION}` is on this table for completeness and is "
        "still excluded from every score: plan.yml bounces an epic until the "
        "pair exists, so both columns are the gate's answer rather than either "
        "planner's.",
        "",
        f"What each plan then did is the other half — run `score` on "
        f"{before_epic}. A replay's own children never merge (nothing ships "
        "from the demo repo), so scoring the replay reports UNKNOWN on every "
        "history row, which is correct and is why the shape is what moves.",
    ]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# the report                                                                   #
# --------------------------------------------------------------------------- #


def _table(rows: list, columns) -> list:
    out = ["| " + " | ".join(head for head, _ in columns) + " |",
           "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(get(row)) for _, get in columns) + " |")
    return out


SECTIONS = (
    ("## Agreement", "agree",
     "The plan and history reached the same answer."),
    ("## Disagreement", "disagree",
     "The plan said one thing and history says another. Both halves are "
     "reported; neither side is assumed right."),
    ("## Could not be read", "unknown",
     "A pull request the audit could not read. Reported as UNKNOWN and left "
     "out of the number — never 0 and never 'clean', because the absence of "
     "evidence is not evidence of a good plan."),
    ("## Never claimed", "unclaimed",
     "The plan made no claim here at all — a card with no `**Files:**` line, "
     "or one carrying no routing verdict. A claim nobody made cannot be right, "
     "so it is named rather than counted."),
    ("## Excluded as contaminated", "excluded",
     "Answers the planner was handed face-up by a gate it had to pass. Scoring "
     "them would flatter the audit, so they are named and not counted."),
)


def render_report(result: dict, doc: dict | None = None) -> str:
    """Both directions, always. The misses are the half that says where the
    plan had no cover."""
    doc = doc if doc is not None else load()
    counts = result["counts"]
    out: list[str] = []
    w = out.append
    w(f"# The planner, scored against {result['epic']}")
    w("")
    w(f"**{result['epic']}** — {result.get('title', '')} — {result['children']} "
      "child card(s). Every answer below is what history did with those cards "
      "AFTER the plan was written, so none of it was visible to the planner.")
    w("")
    w(f"{result['scored']} row(s) scored: **{counts['agree']} agree**, "
      f"**{counts['disagree']} disagree**. {counts['unknown']} could not be "
      f"read, {counts['unclaimed']} were never claimed, {counts['excluded']} "
      "excluded as contaminated.")
    w("")
    if (result.get("replay") or {}).get("leaks"):
        w("**This replay leaked and is not a result.**")
        w("")

    for heading, outcome, blurb in SECTIONS:
        rows = [r for r in result["rows"] if r["outcome"] == outcome]
        w(heading)
        w("")
        w(blurb)
        w("")
        if not rows:
            w("*(none)*")
            w("")
            continue
        out.extend(_table(rows, [
            ("Card", lambda r: r["card"]),
            ("Dimension", lambda r: f"`{r['dimension']}`"),
            ("Plan said", lambda r: r["claimed"] or "—"),
            ("History says", lambda r: r["observed"] or "—"),
            ("Evidence", lambda r: r.get("evidence") or r.get("why") or "—"),
        ]))
        w("")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# collecting the history — the one live seam                                   #
# --------------------------------------------------------------------------- #

#: The fields `gh pr list` is asked for. `files` is what the footprint and the
#: collision dimensions are read from; `state` says whether the card shipped.
PR_FIELDS = "number,url,headRefName,state,files"

_CHILDREN_QUERY = """query($id: String!) {
  issue(id: $id) {
    identifier title description
    children { nodes {
      identifier title description createdAt
      labels { nodes { name } }
      inverseRelations(first: 50) { nodes { type issue { identifier } } }
    } }
  }
}"""


def _repo_for(labels) -> str | None:
    """The GitHub repo a card's `repo:<slug>` label names, or None.

    The label is the routing source of truth (DRE-1699), and the map is the
    same snapshot the relay routes on — never a list restated here.
    """
    for label in labels or ():
        if label.startswith("repo:"):
            slug = label.split(":", 1)[1]
            if slug in validate_card.VALID_SLUGS:
                return validate_card._REPO_MAP[slug]
    return None


def repo_is_readable(repo: str, run=None) -> bool:
    """Can this token see `repo` at all?

    ASKED BEFORE ANY PR LOOKUP IS BELIEVED, and the reason is measured, not
    theoretical. `gh pr list --repo <invisible> --search head:agent/DRE-N`
    exits **0 and prints `[]`** — GitHub's search API answers "no results" for
    a repository the token cannot see, and it looks exactly like a card that
    never produced a pull request. `card_pr` guards the rc != 0 case (DRE-2034)
    and cannot see this one: nothing failed.

    Left unguarded, an audit run with a repo-scoped token reports every child
    of a cross-repo epic as `within-footprint` on an empty file list — a clean
    sheet composed entirely of reads that never happened. `gh repo view` fails
    loudly for an invisible repo, so it is the probe.
    """
    if run is None:
        import subprocess  # noqa: PLC0415 - live seam only  # nosec B404

        def run(args):
            return subprocess.run(  # nosec B603 B607 — fixed-arg gh, shell=False
                ["gh", *args], capture_output=True, text=True, check=False
            ).returncode == 0
    return bool(run(["repo", "view", repo, "--json", "nameWithOwner"]))


def collect(epic_identifier: str, lops=None, finder=None, readable=None) -> dict:
    """One epic and every child, with the history that answers the plan.

    Reads are SERIAL through the one `LINEAR_API_KEY` — no fan-out, the same
    bound `critic_score.read_population` takes: on 2026-08-22 two processes
    contending for one credential killed a paid run 23 turns in.

    A PR lookup that fails — or that lands in a repo this token cannot see —
    leaves `pr: None`, which every reader turns into UNKNOWN. It is never an
    empty file list: "GitHub would not say" and "the PR touched nothing" are
    different facts, and collapsing them is exactly the silent zero this module
    refuses (DRE-2034).
    """
    if lops is None:
        import linear_ops as lops  # noqa: PLC0415 - live seam only
    if finder is None:
        import card_pr

        finder = card_pr.find
    if readable is None:
        readable = repo_is_readable
    seen_repos: dict = {}

    data = lops.gql(_CHILDREN_QUERY, {"id": epic_identifier})
    issue = data.get("issue") or {}
    nodes = ((issue.get("children") or {}).get("nodes")) or []
    epic = {
        "identifier": issue.get("identifier") or epic_identifier,
        "title": issue.get("title") or "",
        "body": issue.get("description") or "",
        "comments": lops.comment_bodies(epic_identifier),
    }

    children = []
    for record in lops.child_detail_records(nodes):
        identifier = record["identifier"]
        repo = _repo_for(record.get("labels"))
        found, unreadable = None, None
        if repo is not None and repo not in seen_repos:
            # Probed once per REPO, not once per card: the answer cannot change
            # inside one run, and an epic of fifty cards would otherwise pay
            # fifty times for it.
            seen_repos[repo] = readable(repo)
        if repo is None:
            unreadable = ("the card names no repo this rail routes, so there "
                          "is nowhere to look for its pull request")
        elif not seen_repos[repo]:
            unreadable = (f"this token cannot read {repo}, and an empty PR "
                          "search there is indistinguishable from a card that "
                          "never produced one")
        else:
            try:
                found = finder(identifier, repo=repo, fields=PR_FIELDS)
            except Exception as e:                  # noqa: BLE001 - see docstring
                unreadable = str(e)
        pr = None
        if found:
            pr = {
                "number": found.get("number"),
                "merged": found.get("state") == "MERGED",
                "files": [f.get("path") for f in (found.get("files") or [])
                          if f.get("path")] if found.get("files") is not None
                else None,
            }
        children.append({
            "identifier": identifier,
            "title": record.get("title") or "",
            "body": record.get("body") or "",
            "labels": record.get("labels") or [],
            "blocked_by": record.get("blocked_by") or [],
            "comments": lops.comment_bodies(identifier),
            "pr": pr,
            "pr_unreadable": unreadable,
        })
    return {"epic": epic, "children": children}


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _stdin_json(default):
    if sys.stdin is None or sys.stdin.isatty():
        return default
    raw = sys.stdin.read().strip()
    return json.loads(raw) if raw else default


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check")

    gathering = sub.add_parser(
        "collect", help="read one epic, its children and their PRs, as JSON")
    gathering.add_argument("--epic", required=True)

    scoring = sub.add_parser(
        "score", help="score one epic; the epic and its children arrive on stdin")
    scoring.add_argument("--epic", required=True)
    scoring.add_argument("--out", help="write the result as JSON")
    scoring.add_argument("--report", help="write the markdown report")

    card = sub.add_parser("replay-card",
                          help="build and CHECK the throwaway replay epic")
    card.add_argument("--epic", required=True)
    card.add_argument("--n", type=int, required=True)
    card.add_argument("--body-file", required=True,
                      help="the source epic's description")
    card.add_argument("--out", help="write the card as JSON")
    card.add_argument("--body-out", help="write the frozen pre-plan text")

    diffing = sub.add_parser(
        "diff", help="the replay's plan beside the plan it is replaying")
    diffing.add_argument("--before", required=True,
                         help="the historical epic's collect JSON")
    diffing.add_argument("--after", required=True,
                         help="the replay epic's collect JSON")
    diffing.add_argument("--out", help="write the markdown diff")

    leak = sub.add_parser("leak-check")
    leak.add_argument("--plan", required=True, help="the historical plan")
    leak.add_argument("--context", required=True, help="what the replay was handed")
    leak.add_argument("--epic", required=True)
    leak.add_argument("--record", help="write the leak record here")

    args = parser.parse_args(argv)
    command = args.command or "check"

    if command == "check":
        problems = reference_problems()
        for problem in problems:
            print(f"  [FAIL] {problem}")
        print(f"{len(dimensions())} dimension(s) over "
              f"{len(source()['epics'])} epic(s), {len(problems)} problem(s)")
        return 1 if problems else 0

    if command == "collect":
        print(json.dumps(collect(args.epic), indent=2))
        return 0

    if command == "score":
        payload = _stdin_json({})
        result = score(payload.get("epic") or {"identifier": args.epic},
                       payload.get("children") or [],
                       replay=payload.get("replay"))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=2)
        report = render_report(result)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as fh:
                fh.write(report)
        print(report)
        return 0

    if command == "replay-card":
        with open(args.body_file, encoding="utf-8") as fh:
            body = fh.read()
        built = replay_card(args.epic, args.n, body)
        problems = replay_problems(built)
        for problem in problems:
            print(f"  [FAIL] {problem}", file=sys.stderr)
        if problems:
            return 1
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(built, fh, indent=2)
        if args.body_out:
            with open(args.body_out, "w", encoding="utf-8") as fh:
                fh.write(built["body"])
        print(built["title"])
        return 0

    if command == "diff":
        with open(args.before, encoding="utf-8") as fh:
            before = json.load(fh)
        with open(args.after, encoding="utf-8") as fh:
            after = json.load(fh)
        report = render_diff(
            before["epic"]["identifier"], plan_shape(before["children"]),
            after["epic"]["identifier"], plan_shape(after["children"]),
        )
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(report)
        print(report)
        return 0

    if command == "leak-check":
        with open(args.plan, encoding="utf-8") as fh:
            plan = fh.read()
        with open(args.context, encoding="utf-8") as fh:
            context = fh.read()
        leaks = plan_leaks(context, plan)
        if not leaks:
            print(f"no leak: the replay of {args.epic} never saw the plan")
            return 0
        record = leak_record(args.epic, leaks)
        if args.record:
            with open(args.record, "w", encoding="utf-8") as fh:
                fh.write(record)
        print(record, file=sys.stderr)
        return 1

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
