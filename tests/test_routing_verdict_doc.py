"""The routing vocabulary DOCUMENT renders from the data (DRE-2724).

Same discipline as the lane contract (DRE-2726): a table of five routes written
by hand in markdown is a second copy of the vocabulary, and a second copy
drifts. `docs/routing-verdicts.md` is rendered from
`config/routing-verdicts.json`, and this test fails the build when the
committed file and the render disagree.

The render must show, per route, the two fields the amendment insisted on — a
stated DESTINATION and a stated ACTOR — because "enters Backlog and awaits its
turn" is a dead end wearing a label, and a reader has to be able to see that it
is not what we do.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import routing_verdict  # noqa: E402

DOC = ROOT / "docs" / "routing-verdicts.md"


def committed() -> str:
    return DOC.read_text(encoding="utf-8")


def test_the_committed_document_is_the_render():
    assert committed() == routing_verdict.render_markdown(), (
        "docs/routing-verdicts.md is stale — regenerate it with "
        "`python3 scripts/routing_verdict.py render`"
    )


def test_the_document_is_generated_and_says_so():
    head = committed().splitlines()[:8]
    assert any("routing_verdict.py render" in line for line in head)


def test_every_route_appears_with_its_destination_and_actor():
    text = committed()
    for name in routing_verdict.verdicts():
        assert name in text
        assert routing_verdict.destination(name) in text
        assert routing_verdict.actor(name) in text


def test_every_title_convention_appears_with_its_adversarial_fixture():
    """The mutation fixture is part of the contract, not test trivia: a reader
    adding a convention has to see that each one ships a title that mentions
    the token without declaring it."""
    text = committed()
    for convention in routing_verdict.title_conventions():
        assert convention["pattern"] in text
        for title in convention["adversarial"]:
            assert title in text


def test_the_document_carries_the_revival_note_of_every_route_that_has_one():
    """DRE-2824: PARKED's actor is now the process writer that lands the card,
    so the rendered document is where a reader learns that only a human takes a
    card back out of it. If that sentence lives only in the JSON, the reader
    this vocabulary is written for never sees it."""
    text = committed()
    for name in routing_verdict.verdicts():
        note = routing_verdict.revival(name)
        if note:
            assert note in text, f"{name}'s revival note is missing from the document"
    assert routing_verdict.revival("PARKED"), "PARKED must state who revives it"


def test_the_document_binds_actors_to_the_destination_lane_not_the_glossary():
    """The weaker claim — "the actor is a writer somewhere" — is what let a
    human be named on a lane no human may write."""
    text = committed().lower()
    assert "permitted writer of that lane" in text


def test_the_document_says_epics_get_a_plan_test_not_a_verdict():
    text = committed().lower()
    assert "epic" in text and "plan test" in text
