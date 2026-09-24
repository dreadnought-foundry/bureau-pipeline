"""The groomer's drain is a declared writer of `Canceled` (DRE-4689).

The CEO decided on 2026-09-23 (DRE-4669) that the groomer PROPOSES
cancellations and that, once he agrees card by card, the agreed cards move to
`Canceled` with the reason as a comment. The clause's principle is unchanged —
**a human decided** — and what moved is who records the decision, exactly as it
already had for the dependabot class (DRE-3665): the decision is somebody
else's, the mechanism writes it down.

This is the CONTRACT half of that work, split out ahead of the drain (DRE-4677)
so the drain cites a declared answer instead of inventing one. These tests are
the thing that keeps it declared: an edit that drops `groomer.py` from the
`Canceled` lane's writers, or that rewrites the clause text back to "a human —
and the sweep, for exactly one class", turns the build red BEFORE the drain's
write becomes an undeclared path into a terminal lane.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_contract  # noqa: E402


ROOT = os.path.join(os.path.dirname(__file__), "..")
DOC = os.path.join(ROOT, "docs", "lane-contract.md")

# The writer key, spelled once. It is shared with the drain card, so it is a
# contract string: `groomer.py` is what the lane names and what the drain must
# answer to.
GROOMER = "groomer.py"


def _clause(kind):
    return lane_contract.lane("Canceled")["clauses"][kind]


class TestTheGroomerMayWriteCanceled:
    def test_the_lane_permits_the_groomer_beside_the_operator_and_the_sweep(self):
        who = lane_contract.lane_writers("Canceled")
        assert GROOMER in who, (
            "the Canceled lane does not permit groomer.py — the drain's write "
            "would be an undeclared path into a terminal lane"
        )
        # Beside, not instead of: the human and the dependabot class both stay.
        assert "operator" in who
        assert "reconcile.py" in who

    def test_that_is_the_whole_permitted_set(self):
        # Pinned as a tuple as well as by membership: a terminal lane gaining a
        # writer nobody named is the failure this clause exists to prevent, and
        # a membership test alone cannot see an addition.
        assert lane_contract.lane_writers("Canceled") == (
            "operator",
            "reconcile.py",
            GROOMER,
        )

    def test_the_writer_key_resolves_in_the_contracts_own_glossary(self):
        # `lane.writers_exist` is the assertion this clause carries; the key was
        # already a Planning writer, so the check reads the glossary and finds
        # it. Pinned here because the card's green-check criterion rests on it.
        glossary = lane_contract.writers()
        assert GROOMER in glossary
        assert os.path.exists(os.path.join(ROOT, glossary[GROOMER]["path"]))

    def test_the_conformance_run_passes_the_canceled_writers_clause(self):
        report = lane_contract.check(vocabulary=lane_contract.pipeline_vocabulary())
        canceled = [f for f in report.findings if f.clause_id == "Canceled.writers"]
        assert canceled, "the Canceled.writers clause was not evaluated at all"
        assert [f.status for f in canceled] == ["pass"], canceled


class TestTheClauseTextSaysWhoAndWhy:
    """The `who` list is the machine half. A reader — and the drain's own
    reviewer — gets the answer from the sentence, so the sentence is pinned
    too: who writes it, for which class, and on whose decision."""

    def test_the_writers_text_names_the_groomers_drain(self):
        text = _clause("writers")["text"]
        assert "drain" in text
        assert "groomer" in text.lower()

    def test_the_writers_text_says_the_ceo_agreed_card_by_card(self):
        text = _clause("writers")["text"]
        assert "CEO" in text
        # The narrow class: only the cards he agreed to, never the whole table.
        assert "excluded" in text or "did not exclude" in text
        assert "reason" in text

    def test_the_writers_text_keeps_the_dependabot_class_intact(self):
        text = _clause("writers")["text"]
        assert "dependabot" in text.lower()
        assert "DRE-3665" in text
        assert "sweep" in text

    def test_the_writers_text_still_says_a_human_decided(self):
        # The principle the clause states does not change. If this stops being
        # true the lane has quietly become one the pipeline may cancel into on
        # its own judgement, which is a different contract.
        assert "human" in _clause("writers")["text"]

    def test_the_entrance_text_names_the_same_second_class(self):
        text = _clause("entrance")["text"]
        assert "CEO" in text
        assert "groomer" in text.lower()
        assert "DRE-3665" in text, "the dependabot entrance must survive"

    def test_both_clauses_cite_the_card_the_drain_is_built_on(self):
        for kind in ("entrance", "writers"):
            assert "DRE-4677" in _clause(kind)["text"], (
                f"the {kind} clause does not cite the decision it records"
            )


class TestTheRenderedDocumentCarriesIt:
    def test_the_canceled_section_lists_the_groomer_as_a_permitted_writer(self):
        # `docs/lane-contract.md` is rendered, never hand-edited; this asserts
        # the committed render actually carries the new writer, so a contract
        # edit that was never re-rendered is caught here as well as by the
        # whole-file comparison in tests/test_lane_contract_doc.py.
        with open(DOC, encoding="utf-8") as fh:
            rendered = fh.read()
        section = rendered.split("### Canceled")[1].split("\n### ")[0]
        assert "Permitted writers:" in section
        assert f"`{GROOMER}`" in section
        assert "`operator`" in section and "`reconcile.py`" in section
