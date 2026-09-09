"""The seam reader — an observation-gated seam, found mechanically (DRE-3395).

One epic, one observation card, and everything past it: DRE-3164 was planned as
fifteen cards in which three of them waited on somebody watching DRE-3166 run
live. That is a second epic wearing the first one's number, and until this
module existed nothing could say so before the critic spent a turn thinking.

The rule both this suite and the gate card's (DRE-3398) are written against is
two sentences: **the waiting set is everything past the seam, and the seam
exists only when a build card is in it.** So the fifteen-card fixture yields
exactly one finding — DRE-3166, with DRE-3218 listed in its waiting set even
though DRE-3218 builds nothing, and DRE-3218 itself opening no seam because
nothing but the PROOF/DEMO pair follows it.

The fixture is the board's own text: `tests/fixtures/dre-3164-children-2026-09-05.json`
holds the fifteen children as they stood before the split, pulled from
`linear_ops.py children-detail` on DRE-3164 and DRE-3245 and joined into one
record per card. A synthetic epic would have proved the walk and not the
reading.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_seam.py -v
"""

from __future__ import annotations

import copy
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
sys.path.insert(0, os.path.abspath(SCRIPTS))

import linear_ops  # noqa: E402
import plan_seam  # noqa: E402

#: The sentence the whole card exists to produce, typed out ONCE in this suite
#: and nowhere else in the repo. `standards/card-quality.md` carries the same
#: words for humans; `plan_seam.FINDING` carries them for the pipeline.
DRE_3164_FINDING = (
    "DRE-3215, DRE-3217, DRE-3218: wait on observing DRE-3166 live — "
    "that is a second epic, not a later step."
)

#: The seven children DRE-3164 kept when DRE-3245 took the other eight. Named
#: rather than derived from a state field, because the split moved cards
#: between epics — it did not mark them delivered.
POST_SPLIT = ("DRE-3165", "DRE-3166", "DRE-3167", "DRE-3210", "DRE-3211",
              "DRE-3239", "DRE-3240")

#: What blocks what once the split has happened. The point of the read: with
#: DRE-3215 and DRE-3217 gone to the other epic, DRE-3166 blocks only the pair,
#: and an observation card the pair alone waits on is not a seam.
POST_SPLIT_BLOCKERS = {
    "DRE-3167": [],
    "DRE-3165": ["DRE-3167"],
    "DRE-3166": ["DRE-3165"],
    "DRE-3210": [],
    "DRE-3211": ["DRE-3167"],
    "DRE-3239": ["DRE-3211", "DRE-3210", "DRE-3167", "DRE-3166", "DRE-3165"],
    "DRE-3240": ["DRE-3211", "DRE-3210", "DRE-3167", "DRE-3166", "DRE-3165"],
}


def fixture(name: str):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


def dre_3164():
    """The fifteen pre-split children, as a fresh copy per test."""
    return fixture("dre-3164-children-2026-09-05.json")


def post_split():
    """The seven cards DRE-3164 kept, with the relations the board holds now."""
    out = [copy.deepcopy(c) for c in dre_3164() if c["identifier"] in POST_SPLIT]
    for card in out:
        card["blocked_by"] = list(POST_SPLIT_BLOCKERS[card["identifier"]])
    return out


def pair(blocked_by):
    """The PROOF and DEMO cards every epic ends with, as joined records."""
    return [
        {"identifier": "DRE-9990", "title": "PROOF: it was observed running",
         "body": "", "labels": ["agent:ops", "no-code"],
         "blocked_by": list(blocked_by), "state": "Backlog"},
        {"identifier": "DRE-9991", "title": "DEMO: the CEO watches it",
         "body": "", "labels": ["agent:ops", "no-code"],
         "blocked_by": list(blocked_by), "state": "Backlog"},
    ]


class TheFindingSentence(unittest.TestCase):
    """`FINDING` is the one copy of the words the standard fixes."""

    def test_the_constant_renders_the_standards_sentence(self):
        self.assertEqual(
            plan_seam.FINDING.format(
                waiting="DRE-3215, DRE-3217, DRE-3218",
                observation="DRE-3166"),
            DRE_3164_FINDING,
        )

    def test_the_sentence_names_a_second_epic_not_a_later_step(self):
        # Pinned separately from the format call: the two halves of the
        # sentence are what makes it a send-back rather than advice, and a
        # reword that keeps the shape would still change the meaning.
        self.assertIn("wait on observing", plan_seam.FINDING)
        self.assertIn("that is a second epic, not a later step.",
                      plan_seam.FINDING)


class TheThreeSets(unittest.TestCase):
    """`observation_cards`, `build_cards` and the pair they both exclude."""

    def test_the_three_operator_cards_are_the_observation_cards(self):
        self.assertEqual(plan_seam.observation_cards(dre_3164()),
                         ["DRE-3166", "DRE-3216", "DRE-3218"])

    def test_the_pair_is_never_an_observation_card(self):
        # Both wear `no-code`, and DEMO's whole job is that a person watches
        # it. The pair closes an epic; it never opens a seam inside one.
        cards = pair([])
        for card in cards:
            card["labels"] = ["agent:ops", "no-code", "needs-human"]
        self.assertEqual(plan_seam.observation_cards(cards), [])

    def test_an_operator_title_is_anchored_and_case_insensitive(self):
        records = [
            {"identifier": "DRE-1", "title": "[operator] flip the switch",
             "body": "", "labels": [], "blocked_by": [], "state": "Backlog"},
            {"identifier": "DRE-2", "title": "Rename the [OPERATOR] section",
             "body": "", "labels": [], "blocked_by": [], "state": "Backlog"},
        ]
        self.assertEqual(plan_seam.observation_cards(records), ["DRE-1"])

    def test_needs_human_plus_no_code_is_an_observation_card(self):
        records = [
            {"identifier": "DRE-1", "title": "Watch it run", "body": "",
             "labels": ["agent:ops", "needs-human", "no-code"],
             "blocked_by": [], "state": "Backlog"},
            # `no-code` alone is an ordinary non-build card, not an observation.
            {"identifier": "DRE-2", "title": "Write the standard", "body": "",
             "labels": ["agent:ops", "no-code"], "blocked_by": [],
             "state": "Backlog"},
        ]
        self.assertEqual(plan_seam.observation_cards(records), ["DRE-1"])

    def test_a_clean_days_count_in_the_criteria_is_an_observation_card(self):
        body = ("Turn the second surface on.\n\n"
                "## Acceptance criteria\n"
                "- [ ] Seven clean days on the first surface, recorded here\n")
        releases = ("Turn the third surface on.\n\n"
                    "## Acceptance criteria\n"
                    "- [ ] 10 clean releases observed before the flip\n")
        neither = ("Build the thing.\n\n"
                   "## Acceptance criteria\n"
                   "- [ ] The suite is green\n")
        records = [
            {"identifier": "DRE-1", "title": "Flip two", "body": body,
             "labels": ["agent:engineer"], "blocked_by": [], "state": "Backlog"},
            {"identifier": "DRE-2", "title": "Flip three", "body": releases,
             "labels": ["agent:engineer"], "blocked_by": [], "state": "Backlog"},
            {"identifier": "DRE-3", "title": "Build", "body": neither,
             "labels": ["agent:engineer"], "blocked_by": [], "state": "Backlog"},
        ]
        self.assertEqual(plan_seam.observation_cards(records),
                         ["DRE-1", "DRE-2"])

    def test_build_cards_are_the_roles_a_build_run_is_dispatched_for(self):
        # Derived from `proof_and_demo.build_roles()`, never restated: an
        # `agent:ops` card is not a build card however much text it carries.
        self.assertEqual(
            plan_seam.build_cards(dre_3164()),
            ["DRE-3165", "DRE-3167", "DRE-3210", "DRE-3211", "DRE-3212",
             "DRE-3213", "DRE-3214", "DRE-3215", "DRE-3217", "DRE-3238"],
        )

    def test_the_pair_is_never_a_build_card(self):
        cards = pair([])
        for card in cards:
            card["labels"] = ["agent:engineer"]
        self.assertEqual(plan_seam.build_cards(cards), [])


class TheWaitingSet(unittest.TestCase):
    """Everything past the seam — build cards and later observations alike."""

    def test_dre_3166_is_waited_on_by_three_cards(self):
        self.assertEqual(plan_seam.waiting_on(dre_3164(), "DRE-3166"),
                         ["DRE-3215", "DRE-3217", "DRE-3218"])

    def test_the_downstream_observation_card_waits_on_nothing(self):
        # DRE-3218 is on the far side of DRE-3166's seam, so it is IN that
        # waiting list — and nothing but the pair follows it, so its own list
        # is empty.
        self.assertEqual(plan_seam.waiting_on(dre_3164(), "DRE-3218"), [])

    def test_porticos_operator_card_waits_on_nothing(self):
        self.assertEqual(plan_seam.waiting_on(dre_3164(), "DRE-3216"), [])

    def test_the_walk_is_transitive_and_in_record_order(self):
        # DRE-3218 reaches DRE-3166 only through DRE-3215 and DRE-3217; a
        # direct-blocker read would list two cards and miss the third.
        cards = dre_3164()
        by_id = {c["identifier"]: c for c in cards}
        self.assertNotIn("DRE-3166", by_id["DRE-3218"]["blocked_by"])
        self.assertNotIn("DRE-3166", by_id["DRE-3217"]["blocked_by"])
        self.assertEqual(plan_seam.waiting_on(cards, "DRE-3166"),
                         ["DRE-3215", "DRE-3217", "DRE-3218"])

    def test_an_id_outside_the_epic_is_ignored(self):
        records = [
            {"identifier": "DRE-1", "title": "[OPERATOR] watch it", "body": "",
             "labels": [], "blocked_by": ["DRE-8888"], "state": "Backlog"},
            {"identifier": "DRE-2", "title": "Build", "body": "",
             "labels": ["agent:engineer"], "blocked_by": ["DRE-1", "DRE-7777"],
             "state": "Backlog"},
        ]
        self.assertEqual(plan_seam.waiting_on(records, "DRE-1"), ["DRE-2"])

    def test_an_unknown_observation_waits_on_nothing(self):
        self.assertEqual(plan_seam.waiting_on(dre_3164(), "DRE-0000"), [])


class TheSeamRule(unittest.TestCase):
    """A seam exists only when a BUILD card is past it."""

    def test_the_fixture_yields_exactly_one_finding(self):
        self.assertEqual(plan_seam.seam_findings(dre_3164()),
                         [DRE_3164_FINDING])

    def test_the_one_seam_is_dre_3166(self):
        self.assertEqual(
            plan_seam.observation_seams(dre_3164()),
            [{"observation": "DRE-3166",
              "waiting": ["DRE-3215", "DRE-3217", "DRE-3218"]}],
        )

    def test_a_waiting_list_with_no_build_card_is_not_a_seam(self):
        # An observation card blocking only a second observation card, which
        # blocks only the pair. Everything past the first seam is a switch-on,
        # so no build work crosses it and there is no second epic to cut.
        records = [
            {"identifier": "DRE-1", "title": "[OPERATOR] watch the first",
             "body": "", "labels": ["agent:ops", "needs-human", "no-code"],
             "blocked_by": [], "state": "Backlog"},
            {"identifier": "DRE-2", "title": "[OPERATOR] then flip the second",
             "body": "", "labels": ["agent:ops", "needs-human", "no-code"],
             "blocked_by": ["DRE-1"], "state": "Backlog"},
            {"identifier": "DRE-3", "title": "Build the thing", "body": "",
             "labels": ["agent:engineer"], "blocked_by": [], "state": "Backlog"},
        ] + pair(["DRE-1", "DRE-2", "DRE-3"])
        self.assertEqual(len(records), 5)
        self.assertEqual(plan_seam.waiting_on(records, "DRE-1"), ["DRE-2"])
        self.assertEqual(plan_seam.observation_seams(records), [])
        self.assertEqual(plan_seam.seam_findings(records), [])

    def test_the_post_split_seven_hold_no_seam(self):
        cards = post_split()
        self.assertEqual(len(cards), 7)
        self.assertEqual(plan_seam.waiting_on(cards, "DRE-3166"), [])
        self.assertEqual(plan_seam.seam_findings(cards), [])

    def test_the_dre_3019_children_hold_no_seam(self):
        # Joined with a detail read that knows no relations at all — the
        # under-reporting case, and the ordinary one for an epic nobody has to
        # watch run.
        cards = fixture("dre-3019-children.json")
        records = plan_seam.join(cards, [])
        records += pair([c["identifier"] for c in cards])
        self.assertEqual(plan_seam.seam_findings(records), [])

    def test_a_delivered_observation_is_not_a_seam(self):
        cards = dre_3164()
        for card in cards:
            if card["identifier"] == "DRE-3166":
                card["state"] = "Done"
        self.assertEqual(plan_seam.observation_cards(cards),
                         ["DRE-3216", "DRE-3218"])
        self.assertEqual(plan_seam.seam_findings(cards), [])


class TheJoin(unittest.TestCase):
    """Two board reads, merged on `identifier`, under-reporting on a gap."""

    def test_the_order_is_the_detail_reads(self):
        cards = [{"identifier": "DRE-2", "body": "b2", "labels": [],
                  "parent": "DRE-1", "state": "Todo"},
                 {"identifier": "DRE-1", "body": "b1", "labels": [],
                  "parent": "DRE-1", "state": "Backlog"}]
        details = [{"identifier": "DRE-1", "title": "one", "body": "b1",
                    "labels": [], "blocked_by": [], "created_at": "a"},
                   {"identifier": "DRE-2", "title": "two", "body": "b2",
                    "labels": [], "blocked_by": ["DRE-1"], "created_at": "b"}]
        joined = plan_seam.join(cards, details)
        self.assertEqual([r["identifier"] for r in joined],
                         ["DRE-1", "DRE-2"])
        self.assertEqual(joined[1]["blocked_by"], ["DRE-1"])
        self.assertEqual(joined[0]["state"], "Backlog")

    def test_a_child_only_in_children_json_is_appended_with_no_relations(self):
        cards = [{"identifier": "DRE-1", "body": "b1", "labels": ["agent:ops"],
                  "parent": "DRE-9", "state": "Todo"},
                 {"identifier": "DRE-2", "body": "b2", "labels": [],
                  "parent": "DRE-9", "state": "Todo"}]
        details = [{"identifier": "DRE-2", "title": "two", "body": "b2",
                    "labels": [], "blocked_by": [], "created_at": "a"}]
        joined = plan_seam.join(cards, details)
        self.assertEqual([r["identifier"] for r in joined], ["DRE-2", "DRE-1"])
        self.assertEqual(joined[1]["title"], "")
        self.assertEqual(joined[1]["blocked_by"], [])
        self.assertEqual(joined[1]["labels"], ["agent:ops"])
        self.assertEqual(joined[1]["state"], "Todo")

    def test_a_child_only_in_children_detail_has_no_state(self):
        details = [{"identifier": "DRE-1", "title": "one", "body": "b1",
                    "labels": ["agent:engineer"], "blocked_by": ["DRE-0"],
                    "created_at": "a"}]
        joined = plan_seam.join([], details)
        self.assertEqual(joined[0]["state"], "")
        self.assertEqual(joined[0]["title"], "one")
        self.assertEqual(joined[0]["blocked_by"], ["DRE-0"])

    def test_a_related_relation_contributes_no_blocker(self):
        # `child_detail_records` reads the FORMAL `blocks` relations and
        # nothing else (DRE-2676). The join inherits that and never widens it.
        nodes = [{
            "identifier": "DRE-1", "title": "one", "description": "b",
            "createdAt": "2026-09-05T00:00:00.000Z",
            "labels": {"nodes": []},
            "inverseRelations": {"nodes": [
                {"type": "related", "issue": {"identifier": "DRE-7"}},
                {"type": "blocks", "issue": {"identifier": "DRE-8"}},
            ]},
        }]
        details = linear_ops.child_detail_records(nodes)
        joined = plan_seam.join([], details)
        self.assertEqual(joined[0]["blocked_by"], ["DRE-8"])

    def test_a_malformed_read_under_reports_rather_than_raising(self):
        self.assertEqual(plan_seam.join(None, None), [])
        self.assertEqual(plan_seam.join([{}], [{}]), [])
        self.assertEqual(plan_seam.join("nonsense", {"a": 1}), [])


class TheCli(unittest.TestCase):
    """`findings`, driven the way DRE-3398 will drive it from the workflow."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._stdin = sys.stdin
        self.addCleanup(lambda: setattr(sys, "stdin", self._stdin))

    def path(self, name: str) -> str:
        return os.path.join(self.tmp.name, name)

    def halves(self, records):
        """The two board reads a joined fixture record came from."""
        cards = [{"identifier": r["identifier"], "body": r["body"],
                  "labels": r["labels"], "parent": "DRE-3164",
                  "state": r["state"]} for r in records]
        details = [{"identifier": r["identifier"], "title": r["title"],
                    "body": r["body"], "labels": r["labels"],
                    "blocked_by": r["blocked_by"], "created_at": ""}
                   for r in records]
        return cards, details

    def run_cli(self, records, **files):
        cards, details = self.halves(records)
        detail_file = files.pop("detail_file", None)
        if detail_file is None:
            detail_file = self.path("detail.json")
            with open(detail_file, "w", encoding="utf-8") as fh:
                json.dump(details, fh)
        argv = ["findings", "--detail-file", detail_file]
        for flag, path in files.items():
            argv += ["--" + flag.replace("_", "-"), path]
        sys.stdin = io.StringIO(json.dumps(cards))
        return plan_seam.main(argv)

    def test_the_finding_reaches_the_structural_file_and_the_note(self):
        note = self.path("note.md")
        with open(note, "w", encoding="utf-8") as fh:
            fh.write("🔎 **Mechanical plan checks** — 15 card(s)\n"
                     "No structural findings.\n")
        structural = self.path("structural.txt")
        code = self.run_cli(dre_3164(), structural_file=structural,
                            note_file=note)
        self.assertEqual(code, 0)
        with open(structural, encoding="utf-8") as fh:
            self.assertEqual(fh.read().splitlines(), [DRE_3164_FINDING])
        with open(note, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("🔎 **Mechanical plan checks** — 15 card(s)", text)
        self.assertIn("No structural findings.", text)
        self.assertIn("Seam (structural — a send-back at the first critic, "
                      "not advice):", text)
        self.assertIn(DRE_3164_FINDING, text)

    def test_no_seam_writes_an_empty_file_and_leaves_the_note_alone(self):
        note = self.path("note.md")
        original = "🔎 **Mechanical plan checks** — 7 card(s)\n"
        with open(note, "w", encoding="utf-8") as fh:
            fh.write(original)
        structural = self.path("structural.txt")
        code = self.run_cli(post_split(), structural_file=structural,
                            note_file=note)
        self.assertEqual(code, 0)
        # An ABSENT file and an EMPTY file are different facts to the gate.
        self.assertTrue(os.path.exists(structural))
        with open(structural, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "")
        with open(note, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), original)

    def test_a_missing_detail_file_is_one_line_on_stderr_and_exit_zero(self):
        structural = self.path("structural.txt")
        err = io.StringIO()
        stderr, sys.stderr = sys.stderr, err
        try:
            code = self.run_cli(dre_3164(),
                                detail_file=self.path("absent.json"),
                                structural_file=structural)
        finally:
            sys.stderr = stderr
        self.assertEqual(code, 0)
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        self.assertIn("absent.json", err.getvalue())
        with open(structural, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "")

    def test_the_no_seam_line_names_how_many_cards_were_read(self):
        out = io.StringIO()
        stdout, sys.stdout = sys.stdout, out
        try:
            self.run_cli(post_split())
        finally:
            sys.stdout = stdout
        self.assertEqual(out.getvalue().strip(), "no seam — 7 card(s)")

    def test_the_finding_is_printed(self):
        out = io.StringIO()
        stdout, sys.stdout = sys.stdout, out
        try:
            self.run_cli(dre_3164())
        finally:
            sys.stdout = stdout
        self.assertEqual(out.getvalue().strip(), DRE_3164_FINDING)

    def test_a_note_file_that_does_not_exist_is_not_created(self):
        note = self.path("absent-note.md")
        self.run_cli(dre_3164(), note_file=note)
        self.assertFalse(os.path.exists(note))


if __name__ == "__main__":
    unittest.main()
