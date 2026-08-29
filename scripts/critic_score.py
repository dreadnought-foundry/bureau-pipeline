#!/usr/bin/env python3
"""Score the critic's verdicts against a review it has never seen (DRE-2685).

A critic nobody has audited is a critic nobody should trust, and nothing else
in the system says how to find out. This module is the audit: a **per-card**
comparison of what the critic says against DRE-2649 — the independent Forms
review, read by a human on 2026-08-22, recorded on the card, and deliberately
not shown to the classifier.

## The exclusion is the whole thing

The review judged the set on more than one axis, and one of them is
**contaminated**: whether a card must be looked at by a person — the
`hand-built` question — was read during planning and quoted in the plan. The
labels and title conventions the critic resolves that question from were set by
the answer. Scoring it grades the critic on a card it was handed face-up.

`config/critic-audit-dre2649.json` therefore marks that dimension unscored,
this module refuses to score it, and the excluded rows are REPORTED rather than
dropped, because a number with rows silently removed is worse than no number.

On the real population the exclusion is the entire difference: every card the
mechanical precedence resolves without a model is resolved by a label or an
anchored title convention, and every one of those is a `hand-built` judgement.
Without the exclusion the audit reports a perfect score composed entirely of
answers planning had already written down.

## Agreement or disagreement — both are the result

The groomer's audit (`docs/groomer-first-batch.md`) reported four reproduced
pairs and five it missed, and the misses are the more useful half: they say
where the cheap check has no cover. This does the same. `render_report` always
prints both sections, empty or not.

## The two mechanical points nothing else records

  * **The full read.** Linear's list API truncates every description at 500
    characters. Both 2026-08-22 sweeps read the population that way, and a
    retraction appended to the END of a long card would have been missed by
    both — 63 cards were edited after creation. So the critic, and only the
    critic, does a full `get_issue` per card, and `score()` REFUSES a body it
    did not read that way rather than scoring half a card.
  * **No silent hold (D3, approved by the operator 2026-08-23).** A card the
    critic could not classify raises an alert naming it and MOVES. Every
    expensive failure of 2026-08-22 looked like nothing happening;
    blocked-and-visible is acceptable, blocked-and-silent is the thing being
    fixed. The alert is posted BEFORE the move, so a move that fails still
    leaves the reason on the card.

The audit never rewrites a card. It names the missing thing and the card waits
— rewriting acceptance criteria is inventing the requirement the card exists to
carry from a human.

## Bounded, and on demand

Reads are serial through the one `LINEAR_API_KEY`: one card at a time, no
fan-out. On 2026-08-22 two processes contending for the same credential killed
a paid run 23 turns in. There is no schedule — like the groomer (D5), this runs
when someone asks for it, which is the only way to run it while the fleet is
quiet.

CLI:

    python3 scripts/critic_score.py check              # validate the reference
    python3 scripts/critic_score.py score [--out J] [--report M]
    python3 scripts/critic_score.py escalate [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane_contract  # noqa: E402
import routing_verdict  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
REFERENCE_PATH = os.path.join(ROOT, "config", "critic-audit-dre2649.json")

#: The marker on a card body that says it was read in full, one card at a time.
FULL_READ = "full"

#: What Linear's list API silently cuts every description down to.
LIST_API_CAP = 500

#: The contaminated dimension, named after the label the routing vocabulary
#: marks for it. The identity is checked, not assumed: `reference_problems`
#: refuses a contaminated dimension no verdict marks, so the exclusion can
#: never drift away from the thing it excludes.
CONTAMINATED_MARK = "hand-built"

#: The axis everything else is scored on.
DEFAULT_DIMENSION = "buildability"

#: The alert tag for a card the critic could not classify. Its own tag, not the
#: verdict marker: this notice NAMES a card that has no verdict, and a reader
#: matching the verdict marker anywhere would read the alert as one.
ESCALATE_TAG = "critic-unclassified"

#: This module, as the lane contract names writers.
WRITER = "critic_score.py"

#: Values `judgement_of` emits that answer a DIFFERENT question than the axis
#: asks, so no dimension declares them. PARKED says "deliberately not to be
#: built" — a decision about whether to spend the effort, not a reading of
#: whether the card can be worked from what it says. The review was never asked
#: it, so comparing the two can only ever come out unequal and would book a
#: disagreement neither side made. `score()` reports these rows as `off-axis`
#: and leaves them out of the number.
OFF_AXIS_VALUES = ("deliberately-not-built",)

OUTCOMES = ("agree", "disagree", "unclassified", "off-axis", "excluded", "unread")

_CARD_ID = re.compile(r"^[A-Z]+-\d+$")

CARD_QUERY = """query($id: String!) {
  issue(id: $id) {
    identifier title description
    labels { nodes { name } }
    children { nodes { identifier } }
  }
}"""


class AuditError(RuntimeError):
    """The reference file is malformed. Raised rather than defaulted: a
    reference that silently loses a judgement is a reference that silently
    scores a smaller set and reports the same shape of number."""


class TruncatedRead(RuntimeError):
    """A card body the audit did not read in full.

    Refused rather than scored. A truncated description routes on the half of
    the card that fits, and the amendment that changes the answer is exactly
    the part that gets cut (DRE-2685).
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
            raise AuditError(f"cannot read the held-back review at {path}: {e}") from e
    return _CACHE[path]


def source(doc: dict | None = None) -> dict:
    """The review being scored against — which card, read when, recorded where.
    A comparison whose other half cannot be found is not a comparison."""
    return dict((doc or load())["source"])


def dimensions(doc: dict | None = None) -> dict:
    return dict((doc or load())["dimensions"])


def judgements(doc: dict | None = None) -> tuple:
    return tuple((doc or load())["judgements"])


def is_scored(dimension: str, doc: dict | None = None) -> bool:
    return bool(dimensions(doc).get(dimension, {}).get("scored"))


def reference_problems(doc: dict | None = None) -> list:
    """Everything wrong with the held-back review, or an empty list.

    Two of these are load-bearing rather than hygienic: the contaminated
    dimension must name a label the routing vocabulary actually marks, and
    every verdict carrying that mark must collapse to the same scored value as
    FLEET. Together they say the exclusion drops exactly one distinction — the
    one planning had already made — and nothing else.
    """
    doc = doc if doc is not None else load()
    problems: list[str] = []

    for key in ("card", "record"):
        if not (source(doc).get(key) or "").strip():
            problems.append(f"the review says nothing for {key!r} — a held-back "
                            "judgement nobody can go and read is not evidence")

    marked = [
        name for name in routing_verdict.verdicts()
        if CONTAMINATED_MARK in routing_verdict.marks(name)
    ]
    if not marked:
        problems.append(
            f"no routing verdict marks {CONTAMINATED_MARK!r}, so the excluded "
            "dimension names nothing the critic actually reads"
        )
    for name in marked:
        if judgement_of(name) != judgement_of("FLEET"):
            problems.append(
                f"the verdict {name!r} marks {CONTAMINATED_MARK!r} but does not "
                f"collapse with FLEET on the {DEFAULT_DIMENSION!r} axis — the "
                "exclusion would be scoring the contaminated dimension under "
                "another name"
            )

    axis = dimensions(doc).get(DEFAULT_DIMENSION) or {}
    declared = tuple(axis.get("values") or ())
    for name in routing_verdict.verdicts() if axis.get("scored") else ():
        value = judgement_of(name)
        if value in declared or value in OFF_AXIS_VALUES:
            continue
        problems.append(
            f"the verdict {name!r} reads as {value!r} on the "
            f"{DEFAULT_DIMENSION!r} axis, which that dimension does not carry "
            "and nothing marks off-axis — a value the review can never hold "
            "comes out as a disagreement the critic never expressed"
        )

    for name, block in dimensions(doc).items():
        if not (block.get("question") or "").strip():
            problems.append(f"dimension {name!r} states no question")
        if not block.get("values"):
            problems.append(f"dimension {name!r} carries no values")
        if not block.get("scored"):
            if not (block.get("contaminated") or "").strip():
                problems.append(
                    f"dimension {name!r} is excluded and does not say why — an "
                    "unexplained exclusion is indistinguishable from a "
                    "convenient one"
                )
            if name != CONTAMINATED_MARK and name not in marked:
                problems.append(
                    f"dimension {name!r} is excluded but names no label the "
                    "routing vocabulary marks"
                )

    for row in judgements(doc):
        card = row.get("card") or ""
        if not _CARD_ID.match(card):
            problems.append(f"{card!r} is not a card identifier")
        dimension = row.get("dimension")
        block = dimensions(doc).get(dimension)
        if block is None:
            problems.append(
                f"{card} is judged on {dimension!r}, which the reference does "
                "not declare as a dimension"
            )
            continue
        if row.get("judgement") not in (block.get("values") or ()):
            problems.append(
                f"{card} carries the judgement {row.get('judgement')!r}, which "
                f"the {dimension!r} dimension does not carry"
            )
        if not (row.get("quote") or "").strip():
            problems.append(
                f"{card} carries no quote from the review — a transcribed "
                "judgement nobody can check against the source is a claim"
            )
        if row.get("basis") not in ("named", "blanket"):
            problems.append(
                f"{card} does not say whether the review named it or covered it "
                "by a blanket statement"
            )
        if not (row.get("finding") or "").strip():
            problems.append(f"{card} cites no finding")

    lane = escalation_lane(doc)
    if lane not in lane_contract.lane_names(status="live"):
        problems.append(
            f"an unclassifiable card would move to {lane!r}, which is not a live "
            "lane — a destination nothing can reach is a silent hold with extra "
            "steps"
        )
    elif WRITER not in lane_contract.lane_writers(lane):
        problems.append(
            f"an unclassifiable card would move to {lane!r}, which permits only "
            f"{', '.join(lane_contract.lane_writers(lane))} — this writer cannot "
            "legally make the move it is the whole point of"
        )
    return problems


# --------------------------------------------------------------------------- #
# reading a card — in full, one at a time                                      #
# --------------------------------------------------------------------------- #


def read_card(lops, identifier: str) -> dict:
    """One card, read whole.

    A single-issue query on purpose. The list API returns the first
    `LIST_API_CAP` characters of a description and says nothing about it, so
    every cheap reader in the pipeline has been routing on the top of the card.
    This is the one place that pays for the rest.
    """
    issue = lops.gql(CARD_QUERY, {"id": identifier})["issue"]
    return {
        "identifier": issue.get("identifier") or identifier,
        "title": issue.get("title") or "",
        "description": issue.get("description") or "",
        "labels": [n["name"] for n in (issue.get("labels") or {}).get("nodes", [])],
        "has_children": bool((issue.get("children") or {}).get("nodes")),
        "read": FULL_READ,
    }


def read_population(lops, identifiers) -> list:
    """Every named card, read in full, **serially**.

    No fan-out: the whole pipeline shares one `LINEAR_API_KEY`, and on
    2026-08-22 two processes contending for one credential killed a paid run 23
    turns in. A bounded pass that takes longer is the cheaper failure.
    """
    return [read_card(lops, identifier) for identifier in identifiers]


def population(doc: dict | None = None) -> tuple:
    """The cards the review judged, in the reference's own order."""
    seen: list[str] = []
    for row in judgements(doc):
        if row["card"] not in seen:
            seen.append(row["card"])
    return tuple(seen)


# --------------------------------------------------------------------------- #
# what the critic says                                                         #
# --------------------------------------------------------------------------- #


def judgement_of(verdict: str | None, dimension: str = DEFAULT_DIMENSION,
                 *, source: str | None = None) -> str | None:
    """A routing verdict, read on one axis. `None` means the critic did not
    answer — a judgement call with nothing recorded.

    On the default axis FLEET, WORKBENCH and OPERATOR collapse into one value:
    what separates them is WHO builds the card, which is the contaminated
    dimension. That collapse IS the exclusion, and `reference_problems` checks
    it covers exactly the verdicts the vocabulary marks `hand-built`.

    PARKED is the one verdict that answers something else entirely, so it
    returns a value no dimension declares (`OFF_AXIS_VALUES`) and `score()`
    reports the row rather than comparing it. `reference_problems` checks that
    every OTHER verdict lands inside the axis it will be compared against — an
    undeclared value can only ever come out as a disagreement the critic never
    expressed, in the one number this module exists to get right.
    """
    if source == "epic":
        return "plan-test"
    if verdict is None:
        return None
    if dimension == CONTAMINATED_MARK:
        return ("needs-a-person"
                if CONTAMINATED_MARK in routing_verdict.marks(verdict)
                else "dispatchable")
    if verdict == "NEEDS WORK":
        return "not-buildable"
    if verdict == "PARKED":
        return "deliberately-not-built"
    return "buildable"


def observe(card: dict, comment_bodies=()) -> dict:
    """What the critic said about this card — its own stamped verdict if it has
    one, otherwise the classification the mechanical precedence produces.

    The stamp comes first because the audit grades what the critic SAID. A card
    carrying two verdicts is unclassified, not arbitrated: picking between them
    would be inventing the decision rather than reading it.
    """
    try:
        stamped = routing_verdict.verdict_on(comment_bodies or ())
    except routing_verdict.ConflictingVerdicts as e:
        return {"verdict": None, "source": "stamped", "reason": str(e)}
    if stamped:
        return {
            "verdict": stamped,
            "source": "stamped",
            "reason": "the card carries the critic's own routing-verdict comment",
        }
    decision = routing_verdict.route(
        card["title"], card["description"], card.get("labels") or (),
        has_children=bool(card.get("has_children")),
    )
    return {
        "verdict": decision.verdict,
        "source": decision.source,
        "reason": decision.reason,
    }


def judgement_from_body(description: str, *, title: str = "", labels=(),
                        has_children: bool = False,
                        dimension: str = DEFAULT_DIMENSION) -> str | None:
    """The scored value a body alone produces. The truncation tests read this
    twice — once on the whole card, once on what the list API would have
    returned — and the two answers differ."""
    decision = routing_verdict.route(title, description, labels,
                                     has_children=has_children)
    return judgement_of(decision.verdict, dimension, source=decision.source)


# --------------------------------------------------------------------------- #
# the score                                                                    #
# --------------------------------------------------------------------------- #


def score(cards, *, doc: dict | None = None, comments: dict | None = None) -> dict:
    """Compare the critic, card by card, against the held-back review.

    Every reference row comes out carrying exactly one outcome — agree,
    disagree, unclassified, off-axis, excluded or unread — because a row that
    quietly fell out of the population is the same failure as a truncated read:
    a smaller set reported in the same shape.
    """
    doc = doc if doc is not None else load()
    by_id: dict = {}
    for card in cards:
        if card.get("read") != FULL_READ:
            raise TruncatedRead(
                f"{card.get('identifier')} was not read in full — Linear's list "
                f"API truncates a description at {LIST_API_CAP} characters and "
                "says nothing about it, so this body may be the top of the card. "
                "Read it with read_card() before scoring it."
            )
        by_id[card["identifier"]] = card

    rows: list[dict] = []
    for entry in judgements(doc):
        dimension = entry["dimension"]
        block = dimensions(doc).get(dimension, {})
        row = {
            "card": entry["card"],
            "dimension": dimension,
            "reference": entry["judgement"],
            "basis": entry.get("basis"),
            "finding": entry.get("finding"),
            "quote": entry.get("quote"),
            "observed": None,
            "observed_source": None,
        }
        if not is_scored(dimension, doc):
            rows.append({**row, "outcome": "excluded", "why": (
                f"the {dimension!r} dimension is excluded as contaminated — "
                f"{block.get('contaminated', '')}")})
            continue

        card = by_id.get(entry["card"])
        if card is None:
            rows.append({**row, "outcome": "unread", "why": (
                "the review judged this card and no full read of it was in the "
                "population, so nothing was compared")})
            continue

        seen = observe(card, (comments or {}).get(entry["card"], ()))
        observed = judgement_of(seen["verdict"], dimension, source=seen["source"])
        row["observed"] = observed
        row["observed_source"] = seen["source"]
        if observed is None:
            rows.append({**row, "outcome": "unclassified", "why": (
                f"the critic did not answer — {seen['reason']}")})
            continue
        if observed in OFF_AXIS_VALUES:
            rows.append({**row, "outcome": "off-axis", "why": (
                f"the critic answered {observed!r} — a decision not to build "
                f"the card, not a reading of the {dimension!r} question the "
                "review answered, so there is nothing here to agree or "
                f"disagree with. {seen['reason']}")})
            continue
        rows.append({**row,
                     "outcome": "agree" if observed == entry["judgement"]
                     else "disagree",
                     "why": seen["reason"]})

    counts = {name: sum(1 for r in rows if r["outcome"] == name)
              for name in OUTCOMES}
    return {
        "source": source(doc),
        "population": len(by_id),
        "rows": rows,
        "counts": counts,
        "scored": counts["agree"] + counts["disagree"] + counts["unclassified"],
    }


def unclassified(result: dict) -> list:
    return [row for row in result["rows"] if row["outcome"] == "unclassified"]


# --------------------------------------------------------------------------- #
# D3 — an alert and a move, never a silent hold                                #
# --------------------------------------------------------------------------- #


def escalation_lane(doc: dict | None = None) -> str:
    """Where a card the critic could not classify goes.

    Read from the routing vocabulary, never written here: a card nobody could
    route owes exactly what a NEEDS WORK card owes — a planner's attention —
    and one literal for one lane means one place to change when the board does.

    `doc` is accepted and deliberately ignored. Callers hold the audit
    reference and pass it by habit, but it is a different schema from the
    routing vocabulary this reads: threading it through would let the reference
    file redefine a lane the vocabulary owns.
    """
    return routing_verdict.destination("NEEDS WORK")


def alert(row: dict, doc: dict | None = None) -> str:
    """The notice that names the card. Opens with its own tag so the sweep can
    count it, and says where the card went and why."""
    lane = escalation_lane(doc)
    return (
        f"🚨 {ESCALATE_TAG}: {row['card']} — the critic could not classify it, "
        f"so it is moving to **{lane}** rather than waiting where nobody would "
        "look.\n\n"
        f"**Why:** {row['why']}\n\n"
        "**What happens next:** a planner reads it and the card gets the one "
        "thing it is missing. Nothing here rewrites the card — naming the gap "
        "is the job; writing the acceptance criteria would invent the "
        "requirement the card exists to carry.\n\n"
        "This is an alert and not a hold, on purpose (D3, approved 2026-08-23). "
        "Every expensive failure of 2026-08-22 looked like nothing happening."
    )


def already_alerted(comment_bodies) -> bool:
    """Has this card already been alerted? Read off the card's own comments,
    matched at the START of a body for the same reason every other marker in
    this pipeline is anchored: a notice that QUOTES the tag is not the tag."""
    return any(
        (body or "").lstrip().startswith(f"🚨 {ESCALATE_TAG}:")
        for body in comment_bodies or ()
    )


def escalate(lops, result: dict, *, to: str | None = None,
             comments: dict | None = None) -> list:
    """Alert on every card the critic could not classify, and move it.

    The comment is posted BEFORE the move. If the move then fails, the card
    still carries the reason — which is the whole difference between this and
    the silent hold it replaces.

    A card that already carries the alert is skipped (vendor boundary Q3). The
    population is the reference set rather than a lane, so a re-run sees every
    card again, and an alarm that fires on every pass is one nobody reads —
    which is the cost the operator accepted with D3 and asked us to bound.
    """
    lane = to or escalation_lane()
    moved: list[str] = []
    for row in unclassified(result):
        if already_alerted((comments or {}).get(row["card"], ())):
            continue
        lops.cmd_comment(row["card"], alert(row))
        lops.cmd_state(row["card"], lane)
        moved.append(row["card"])
    return moved


# --------------------------------------------------------------------------- #
# the report                                                                   #
# --------------------------------------------------------------------------- #


def _table(rows: list, columns) -> list:
    out = ["| " + " | ".join(head for head, _ in columns) + " |",
           "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(get(row)) for _, get in columns) + " |")
    return out


def render_report(result: dict, doc: dict | None = None) -> str:
    """Both directions, always. An audit that prints only its hits is a
    marketing document, and the misses are the half that says where the cheap
    check has no cover."""
    doc = doc if doc is not None else load()
    counts = result["counts"]
    out: list[str] = []
    w = out.append
    review = result["source"]
    w(f"# The critic, scored against {review['card']}")
    w("")
    w("Compared per card against **{card}** — {title} — read on {when} and "
      "recorded at {where}. The classifier has never seen it.".format(
          card=review["card"],
          title=review.get("title", ""),
          when=review.get("reviewed_on", "an earlier date"),
          where=review.get("record", ""),
      ))
    w("")
    w(f"{result['scored']} row(s) scored: **{counts['agree']} agree**, "
      f"**{counts['disagree']} disagree**, {counts['unclassified']} the critic "
      f"could not classify. {counts['excluded']} excluded as contaminated, "
      f"{counts['off-axis']} answered off-axis, {counts['unread']} unread.")
    w("")

    sections = [
        ("## Agreement", "agree",
         "The critic and the review reached the same answer."),
        ("## Disagreement", "disagree",
         "Different answers on the same card. Both are reported; neither side "
         "is assumed right."),
        ("## Could not classify", "unclassified",
         "No verdict, so nothing to compare. Each of these raises an alert and "
         "moves — never a silent hold (D3)."),
        ("## Answered a different question", "off-axis",
         "The critic answered on an axis the review was never asked about — a "
         "decision not to build a card is not a reading of whether it can be. "
         "Named and not counted, rather than booked as a disagreement neither "
         "side made."),
        ("## Excluded as contaminated", "excluded",
         "Judgements the critic was handed face-up during planning. Scoring "
         "them would flatter the audit, so they are named and not counted."),
        ("## Unread", "unread",
         "Judged by the review and not in this population."),
    ]
    for heading, outcome, blurb in sections:
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
            ("Review", lambda r: r["reference"]),
            ("Critic", lambda r: r["observed"] or "—"),
            ("Read from", lambda r: r["observed_source"] or "—"),
            ("Finding", lambda r: r.get("finding") or "—"),
        ]))
        w("")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _live_score(doc: dict) -> tuple:
    """`(result, comments)` — the comments are handed back rather than
    re-fetched, because the escalation reads the same thread to decide whether
    it has already alerted this card."""
    import linear_ops

    cards = read_population(linear_ops, population(doc))
    comments = {c["identifier"]: linear_ops.comment_bodies(c["identifier"])
                for c in cards}
    return score(cards, doc=doc, comments=comments), comments


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check")

    scoring = sub.add_parser("score")
    scoring.add_argument("--out", help="write the result as JSON")
    scoring.add_argument("--report", help="write the markdown report")

    escalating = sub.add_parser("escalate")
    escalating.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    command = args.command or "check"
    doc = load()

    if command == "check":
        problems = reference_problems(doc)
        for problem in problems:
            print(f"  [FAIL] {problem}")
        print(f"{len(judgements(doc))} judgement(s) from {source(doc)['card']}, "
              f"{len(problems)} problem(s)")
        return 1 if problems else 0

    if command == "score":
        result, _ = _live_score(doc)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=2)
        report = render_report(result, doc)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as fh:
                fh.write(report)
        print(report)
        return 0

    if command == "escalate":
        import linear_ops

        result, comments = _live_score(doc)
        rows = [row for row in unclassified(result)
                if not already_alerted(comments.get(row["card"], ()))]
        if args.dry_run:
            for row in rows:
                print(f"would alert and move {row['card']} → "
                      f"{escalation_lane(doc)}")
            return 0
        moved = escalate(linear_ops, result, comments=comments)
        print(f"alerted and moved {len(moved)} card(s) to {escalation_lane(doc)}: "
              + ", ".join(moved))
        return 0

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
