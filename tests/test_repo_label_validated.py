"""EVERY repo: label is checked against the routing map, not only inferred ones
(DRE-2681).

`_has_repo()` checked that a `repo:<slug>` label's slug was non-empty and
nothing else — `VALID_SLUGS` was applied solely to slugs the gate INFERRED. So a
card labelled `repo:nonsense` passed validation, including at the planner's
create seam, which is where it costs a round trip.

The relay does catch it, late and loudly: `_escalate_unknown_slug()` posts a
comment naming the bad value and the full valid set, then parks the card.
Catching it at intake is an improvement that saves a round trip, not a new
safety property.

The one thing this must not do is bounce a card whose slug is real but younger
than this checkout's bundled snapshot — fleet repos run PINNED checkouts, and
that exact mistake parked nine live cards under DRE-2260. So an unknown slug is
confirmed against the canonical snapshot at bureau-pipeline@main before the gate
claims it is unknown, and an unreadable snapshot defers rather than guesses.

Run: cd bureau-pipeline && python3 -m pytest tests/test_repo_label_validated.py -v
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import validate_card  # noqa: E402


# --- the pure core -----------------------------------------------------------


class UnknownSlugCoreTest(unittest.TestCase):
    def test_a_bogus_slug_is_reported(self):
        self.assertEqual(
            validate_card.unknown_repo_slugs(["repo:nonsense", "agent:engineer"]),
            ["nonsense"],
        )

    def test_a_real_slug_is_not_reported(self):
        self.assertEqual(validate_card.unknown_repo_slugs(["repo:atlas"]), [])

    def test_slug_matching_ignores_case(self):
        self.assertEqual(validate_card.unknown_repo_slugs(["repo:Atlas"]), [])

    def test_owner_prefixed_slug_resolves_to_its_basename(self):
        # card_repo() (reconcile) strips the owner — validation must agree, or
        # a card that routes fine is called unroutable.
        self.assertEqual(validate_card.unknown_repo_slugs(["repo:EveryBite/atlas"]), [])

    def test_no_repo_label_reports_nothing(self):
        self.assertEqual(validate_card.unknown_repo_slugs(["agent:engineer"]), [])

    def test_missing_flags_the_unknown_slug(self):
        """The gate used to call this card clean. MUTATION CHECK: revert
        `missing` to the non-empty-slug test and this comes back []."""
        self.assertEqual(
            validate_card.missing("Do the thing.", ["repo:nonsense", "agent:engineer"]),
            [validate_card.WANT_KNOWN_REPO],
        )

    def test_missing_still_passes_a_real_slug(self):
        self.assertEqual(
            validate_card.missing("Do the thing.", ["repo:atlas", "agent:engineer"]),
            [],
        )

    def test_a_bogus_slug_beside_a_real_one_is_still_flagged(self):
        # Two repo labels is itself a routing ambiguity — never treat the good
        # one as the answer.
        self.assertIn(
            validate_card.WANT_KNOWN_REPO,
            validate_card.missing("x", ["repo:atlas", "repo:nonsense", "agent:engineer"]),
        )

    def test_a_missing_repo_label_still_reads_as_missing_not_unknown(self):
        self.assertEqual(
            validate_card.missing("Do the thing.", ["agent:engineer"]),
            [validate_card.WANT_REPO],
        )

    def test_known_slugs_override_widens_the_map(self):
        """The seam the stale-pin deferral uses: a slug the canonical snapshot
        knows is treated as known, without editing the bundled map."""
        self.assertEqual(
            validate_card.missing(
                "x", ["repo:newrepo", "agent:engineer"],
                known_slugs=validate_card.VALID_SLUGS | {"newrepo"},
            ),
            [],
        )

    def test_the_create_seam_rejects_an_unknown_slug(self):
        """child_problems shares `missing`, so the planner's create seam and the
        post-plan sweep refuse the card at intake instead of after a relay
        round trip."""
        probs = validate_card.child_problems(
            "A child", "Real body with enough detail to be a card.",
            ["repo:nonsense", "agent:engineer", "initiative:bureau"],
        )
        self.assertIn(validate_card.WANT_KNOWN_REPO, probs)


# --- cmd_gate ---------------------------------------------------------------


class FakeLinear:
    def __init__(self, labels, state="Todo", description="Do the thing."):
        self._labels = list(labels)
        self._state = state
        self._description = description
        self.comments: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []
        self.added_labels: list[tuple[str, str]] = []

    def get_issue(self, identifier):
        return {"id": "x", "identifier": identifier, "state": {"name": self._state}}

    def gql(self, query, variables=None):
        return {
            "issue": {
                "title": "A card",
                "description": self._description,
                "labels": {"nodes": [{"name": n} for n in self._labels]},
                "children": {"nodes": []},
                "project": None,
            }
        }

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))

    def cmd_state(self, identifier, state):
        self.states.append((identifier, state))

    def add_label(self, identifier, label):
        self.added_labels.append((identifier, label))
        self._labels.append(label)

    def set_description(self, identifier, body):
        self._description = body


class GateUnknownSlugTest(unittest.TestCase):
    def _run(self, fake, canonical) -> bool:
        emitted = {}
        with mock.patch.dict(sys.modules, {"linear_ops": fake}), mock.patch.object(
            validate_card, "_emit", lambda b: emitted.__setitem__("bounced", b)
        ), mock.patch.object(
            validate_card, "canonical_rail_slugs", lambda: canonical
        ):
            validate_card.cmd_gate("DRE-999")
        return emitted["bounced"]

    def test_confirmed_bogus_slug_is_bounced(self):
        fake = FakeLinear(["repo:nonsense", "agent:engineer"])
        self.assertTrue(self._run(fake, canonical={"atlas", "agent-bureau"}))
        self.assertEqual(fake.states, [("DRE-999", "Planning")])

    def test_the_bounce_names_the_bad_value_and_the_valid_set(self):
        """Same courtesy the relay's escalation extends — the reader must not
        have to go find the list."""
        fake = FakeLinear(["repo:nonsense", "agent:engineer"])
        self._run(fake, canonical={"atlas"})
        body = fake.comments[0][1]
        self.assertIn("repo:nonsense", body)
        for slug in validate_card.VALID_SLUGS:
            self.assertIn(slug, body)

    def test_the_bounce_never_adds_a_second_repo_label(self):
        """An inferable card with a bogus explicit label must not end up with
        two repo labels — which one routes is then decided by label order."""
        fake = FakeLinear(["repo:nonsense", "agent:engineer", "initiative:bureau"])
        self.assertTrue(self._run(fake, canonical={"atlas"}))
        self.assertEqual(fake.added_labels, [])

    def test_a_slug_the_canonical_snapshot_knows_is_not_bounced(self):
        """DRE-2260's lesson: a pinned checkout cannot tell a dead route from an
        onboarding younger than its snapshot."""
        fake = FakeLinear(["repo:newrepo", "agent:engineer"])
        self.assertFalse(self._run(fake, canonical={"newrepo", "atlas"}))
        self.assertEqual(fake.states, [])

    def test_an_unreadable_canonical_snapshot_defers_instead_of_bouncing(self):
        fake = FakeLinear(["repo:newrepo", "agent:engineer"])
        self.assertFalse(self._run(fake, canonical=None))
        self.assertEqual(fake.states, [])

    def test_a_clean_card_is_untouched(self):
        fake = FakeLinear(["repo:atlas", "agent:engineer"])
        self.assertFalse(self._run(fake, canonical=None))
        self.assertEqual(fake.states, [])
        self.assertEqual(fake.added_labels, [])

    def test_break_glass_carries_a_bogus_slug_past_the_gate_and_records_it(self):
        """The ONE sanctioned way past this gate stays the one way past THIS
        bounce too — recorded, not undone."""
        fake = FakeLinear(["repo:nonsense", "agent:engineer", "break-glass"])
        with mock.patch.object(
            validate_card.break_glass, "bounce_suppressed", return_value=True
        ) as suppressed:
            self.assertFalse(self._run(fake, canonical={"atlas"}))
        suppressed.assert_called_once()
        self.assertEqual(fake.states, [])


class CanonicalSnapshotReadTest(unittest.TestCase):
    def test_a_failed_read_returns_none_not_an_empty_set(self):
        """An empty set would read as "nothing is on the rail" and bounce every
        card — the failure mode this whole deferral exists to avoid."""
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
            self.assertIsNone(validate_card.canonical_rail_slugs())

    def test_unparseable_json_returns_none(self):
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="not json", stderr="")
            self.assertIsNone(validate_card.canonical_rail_slugs())

    def test_a_good_read_returns_the_slug_set(self):
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(
                returncode=0, stdout='{"atlas": "EveryBite/atlas"}', stderr=""
            )
            self.assertEqual(validate_card.canonical_rail_slugs(), {"atlas"})

    def test_reconcile_reads_the_same_endpoint_literal(self):
        """One literal for one fact — the canonical snapshot's location."""
        import reconcile

        self.assertEqual(
            reconcile._CANONICAL_SNAPSHOT_ENDPOINT,
            validate_card.CANONICAL_SNAPSHOT_ENDPOINT,
        )


if __name__ == "__main__":
    unittest.main()
