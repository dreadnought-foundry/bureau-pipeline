"""The critic reads the whole card, not the list API's first 500 characters
(DRE-2685).

Linear's list API truncates every description at 500 characters and says
nothing about it. Both 2026-08-22 sweeps read the population that way, and a
retraction appended to the END of a long card would have been missed by both —
63 cards were edited after creation and are the population at risk.

So the audit does a full `get_issue` per card, and REFUSES to score a body it
did not read that way. Refusing is the point: a truncated read scores the
critic against half a card and reports a number that looks like every other
number.

Run: cd bureau-pipeline && python3 -m pytest tests/test_critic_score_full_read.py -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import critic_score  # noqa: E402

# A real shape: prose, then the criteria, then — past character 500 — the
# amendment somebody appended a week later. The list API returns the first
# 500 characters of this and nothing says so.
LONG_BODY = (
    "The store keeps one answer per question and versions every write.\n\n"
    + ("Background nobody needs to re-read. " * 12)
    + "\n\n## Acceptance criteria\n\n"
    "- [ ] the store keeps one answer per question\n"
    "- [ ] every write is versioned\n\n"
    "## Amendment, 2026-08-22\n\n"
    "- [ ] somebody signs in as the customer and confirms the session\n"
)
TRUNCATED = LONG_BODY[:500]


def list_row(identifier="DRE-1"):
    """What `issues(first: 100)` actually hands back — no `read` marker,
    because nothing about the row says it is short."""
    return {
        "identifier": identifier,
        "title": f"{identifier} · suggested edits — the store",
        "description": TRUNCATED,
        "labels": [],
        "has_children": False,
    }


class FakeLinear:
    """Counts queries and records what was asked for."""

    def __init__(self, issue):
        self.issue = issue
        self.queries: list[tuple] = []

    def gql(self, query, variables=None):
        self.queries.append((query, dict(variables or {})))
        return {"issue": dict(self.issue)}


class TruncationTest(unittest.TestCase):
    def test_the_amendment_is_past_the_cut(self):
        """The fixture proves nothing unless the truncation actually hides
        something that changes the answer."""
        self.assertGreater(len(LONG_BODY), 500)
        self.assertNotIn("confirms the session", TRUNCATED)

    def test_the_full_read_and_the_truncated_read_route_differently(self):
        full = critic_score.judgement_from_body(LONG_BODY)
        short = critic_score.judgement_from_body(TRUNCATED)
        self.assertNotEqual(
            full, short,
            "the fixture no longer demonstrates what truncation costs",
        )

    def test_a_body_the_audit_did_not_read_in_full_is_refused(self):
        doc = critic_score.load()
        with self.assertRaises(critic_score.TruncatedRead) as caught:
            critic_score.score([list_row()], doc=doc)
        self.assertIn("DRE-1", str(caught.exception))
        self.assertIn("500", str(caught.exception))

    def test_read_card_marks_what_it_read(self):
        lops = FakeLinear({
            "identifier": "DRE-1",
            "title": "B1 · Suggested edits — the store",
            "description": LONG_BODY,
            "labels": {"nodes": [{"name": "repo:portico"}]},
            "children": {"nodes": []},
        })
        card = critic_score.read_card(lops, "DRE-1")
        self.assertEqual(card["read"], critic_score.FULL_READ)
        self.assertEqual(card["description"], LONG_BODY)
        self.assertEqual(card["labels"], ["repo:portico"])
        self.assertFalse(card["has_children"])

    def test_read_card_asks_for_one_card_at_a_time(self):
        """A full read per card, and only the critic pays for it. A list query
        would be cheaper and would return the truncated body this exists to
        avoid — so the query is asserted, not assumed."""
        lops = FakeLinear({
            "identifier": "DRE-1", "title": "t", "description": LONG_BODY,
            "labels": {"nodes": []}, "children": {"nodes": []},
        })
        critic_score.read_card(lops, "DRE-1")
        self.assertEqual(len(lops.queries), 1)
        query, variables = lops.queries[0]
        self.assertIn("issue(id:", query.replace(" ", ""))
        self.assertIn("description", query)
        self.assertNotIn("issues(", query)
        self.assertEqual(variables, {"id": "DRE-1"})

    def test_the_population_is_read_one_card_at_a_time(self):
        lops = FakeLinear({
            "identifier": "DRE-1", "title": "t", "description": LONG_BODY,
            "labels": {"nodes": []}, "children": {"nodes": []},
        })
        cards = critic_score.read_population(lops, ["DRE-1", "DRE-2", "DRE-3"])
        self.assertEqual(len(cards), 3)
        self.assertEqual(len(lops.queries), 3, "the reads were batched or skipped")
        self.assertTrue(all(c["read"] == critic_score.FULL_READ for c in cards))


if __name__ == "__main__":                      # pragma: no cover
    unittest.main()
