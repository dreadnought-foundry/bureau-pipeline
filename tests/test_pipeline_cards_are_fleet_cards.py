"""No brief or standard still says pipeline work is operator-only (DRE-3278).

bureau-pipeline has been a dispatch target since DRE-1929 Option A, and
`standards/engineering.md` has carried the self-hosting convention — "the
Operator-card convention is RETIRED" — since 2026-07-11. `briefs/planner.md`
did not move with it: it still told the planner that a card whose changes land
in bureau-pipeline "cannot be executed by a product-repo agent", to title it
`bureau-pipeline: ...` and to open it with "OPERATOR CARD — agents cannot push
to bureau-pipeline; the operator implements this".

That is what the planner does, because the brief IS the planner's operating
instructions. On 2026-09-06 DRE-3257's plan filed child DRE-3275 — a
`config/pipeline-acts.json` row and a `linear_ops.py` helper, both files in
bureau-pipeline — exactly that way: an OPERATOR card carrying `needs-human` and
labelled `repo:agent-bureau`. The operator corrected it by hand before
approval. Five bureau-pipeline cards were built by agents through a PR, the
critic and the gate that same day (DRE-3241, DRE-3236, DRE-3263, DRE-3266,
DRE-3148), so both halves of the instruction were false when it was followed.

A retired rule that survives in a brief is not retired, and nothing but a test
notices — the producer/consumer drift `standards/engineering.md` names, where a
document is the consumer nothing else checks (the same reason
tests/test_agent_docs_lane_vocabulary.py exists). The banned phrasings are
spelled out HERE rather than derived, for that test's reason: no live document
carries them any more, so a scan keyed off what the documents say could not
find a phrase they have all forgotten.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEFS = sorted((ROOT / "briefs").glob("*.md"))
STANDARDS = sorted((ROOT / "standards").glob("*.md"))
AGENT_DOCS = BRIEFS + STANDARDS

PLANNER = ROOT / "briefs" / "planner.md"

# Each entry is (name, compiled pattern). A document matching any of them tells
# an agent something the pipeline stopped doing on 2026-07-11.
RETIRED_CLAIMS = [
    (
        "agents cannot push to bureau-pipeline",
        re.compile(r"agents?\s+(?:can\s?not|cannot|can't)\s+push\s+to\s+bureau-pipeline",
                   re.IGNORECASE),
    ),
    (
        "the OPERATOR CARD prefix",
        re.compile(r"OPERATOR CARD"),
    ),
    (
        "bureau-pipeline work is the operator's",
        re.compile(
            r"bureau-pipeline[^.\n]{0,240}?"
            r"(?:operator implements|operator-only|only the operator|"
            r"cannot be executed by a product-repo agent)",
            re.IGNORECASE,
        ),
    ),
    (
        "the operator's, because it is bureau-pipeline",
        re.compile(
            r"(?:operator implements|operator-only|only the operator|"
            r"cannot be executed by a product-repo agent)[^.\n]{0,240}?bureau-pipeline",
            re.IGNORECASE,
        ),
    ),
]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestNoDocumentStillCallsPipelineWorkOperatorOnly:
    def test_no_brief_or_standard_carries_a_retired_claim(self):
        offenders = []
        for doc in AGENT_DOCS:
            body = _text(doc)
            for name, pattern in RETIRED_CLAIMS:
                m = pattern.search(body)
                if m:
                    offenders.append(
                        f"{doc.relative_to(ROOT)} says {name!r}: {m.group(0)!r}"
                    )
        assert not offenders, (
            "retired operator-card claims still in the agents' operating "
            "instructions: " + "; ".join(offenders)
        )

    def test_the_scan_would_catch_the_phrasing_it_exists_for(self):
        # Non-vacuous: the sentence the brief actually carried on 2026-09-06 is
        # caught by the patterns above, so a green run means the documents are
        # clean rather than the scan being blind.
        was = (
            "Title such cards `bureau-pipeline: ...` and state in the first "
            'line: "OPERATOR CARD — agents cannot push to bureau-pipeline; the '
            'operator implements this."'
        )
        caught = [name for name, pattern in RETIRED_CLAIMS if pattern.search(was)]
        assert len(caught) >= 3, caught


class TestThePlannerIsTaughtTheRuleThatReplacedIt:
    def test_the_brief_names_the_label_a_pipeline_card_carries(self):
        # The positive half: removing the wrong instruction is not enough — the
        # planner has to be told what to write instead, or it invents one.
        body = _text(PLANNER)
        assert "repo:bureau-pipeline" in body, (
            "briefs/planner.md never names the label a card whose files live in "
            "bureau-pipeline must carry"
        )

    def test_the_brief_says_a_pipeline_card_is_an_ordinary_fleet_card(self):
        body = _text(PLANNER).lower()
        assert "dispatch target" in body, (
            "briefs/planner.md never says bureau-pipeline is a dispatch target — "
            "the fact that makes a pipeline card an ordinary fleet card"
        )

    def test_the_brief_still_names_what_IS_operator_only(self):
        # The retirement narrows the operator-only set; it does not empty it.
        # `config/routing-verdicts.json` owns the definition, so read the
        # OPERATOR verdict's own words rather than restating them here.
        import json

        verdicts = json.loads(
            (ROOT / "config" / "routing-verdicts.json").read_text(encoding="utf-8")
        )
        operator = next(
            v for v in verdicts["verdicts"] if v["name"].upper() == "OPERATOR"
        )
        body = _text(PLANNER)
        # The table cell omits the sentence's full stop; the boundary is the
        # clause, not the punctuation.
        assert operator["means"].rstrip(".") in body, (
            "briefs/planner.md no longer states what OPERATOR means "
            f"({operator['means']!r}) — the planner needs the boundary that "
            "survived the retirement, not just the rule that did not"
        )
