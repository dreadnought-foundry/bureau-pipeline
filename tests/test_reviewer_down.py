"""Is the reviewer down FLEET-WIDE, and what should the ONE card say (DRE-3433).

Origin (live, 2026-09-08 15:19–16:27 PT, DRE-3416). The floating
`anthropics/claude-code-action@v1` tag moved and every critic run in the fleet
died in about thirteen seconds with `ReferenceError: Claude Code native binary
not found at /home/runner/.local/bin/claude`. Per-run detection worked
perfectly — each pull request got the neutral could-not-run receipt — and
NOTHING aggregated it. Seven runs across two repositories failed the same way
in half an hour and no single surface said "the reviewer is down".

`scripts/reviewer_down.py` is the pure decision behind that one card, in the
shape `scripts/stale_merge_ref.py` (DRE-3138) already carries: pure functions
over payloads, no I/O, a CLI for humans. The reconcile wiring is a sibling
card.

The two things this file pins hardest, because they are the two seams that
would go silently blind:

  * the THRESHOLD is data on the act row, never a literal in code, so the
    numbers an operator would want to change live where they can be read;
  * the witness reads BOTH shapes of medic note. DRE-3430 rewires the medic's
    `backoff` job so an `environment_crash` posts
    `reviewer_environment.evidence_note(...)` instead of the generic
    `🔌 The code reviewer was temporarily unavailable` line, and a detector
    that read only the generic one would be blind to exactly the outage this
    epic exists to catch. The environment half is pinned against the WRITER of
    the note — `reviewer_environment` itself — never against the workflow YAML.
"""

import contextlib
import io
import json
import re
import sys
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import medic_classify  # noqa: E402
import merge_gate  # noqa: E402
import pipeline_act  # noqa: E402
import reviewer_down as rd  # noqa: E402
import reviewer_environment  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "reviewer-down-2026-09-08.json"
SOURCE = ROOT / "scripts" / "reviewer_down.py"
MEDIC = ROOT / ".github" / "workflows" / "medic.yml"

SHA = "d34db33fcafe1234d34db33fcafe1234d34db33f"
RUN_URL = "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/34512000001"

NATIVE_BINARY_LOG = (
    "2026-09-08T22:19:03.1234567Z ReferenceError: Claude Code native binary "
    "not found at /home/runner/.local/bin/claude"
)


def _cnr(repo, at, src, pr=None, detail=""):
    return rd.Outcome(repo=repo, at=at, kind=rd.COULD_NOT_RUN, src=src, pr=pr,
                      detail=detail)


def _verdict(repo, at, src, pr=None, detail=""):
    return rd.Outcome(repo=repo, at=at, kind=rd.VERDICT, src=src, pr=pr,
                      detail=detail)


def _threshold():
    return rd.Threshold(window_s=1800, consecutive=3, repos=2)


# --------------------------------------------------------------------------- #
# 1. the act row — the threshold is DATA                                       #
# --------------------------------------------------------------------------- #


class TestTheActRow(unittest.TestCase):
    def setUp(self):
        self.row = pipeline_act.record(rd.ACT)

    def test_the_row_is_declared_with_the_contracted_fields(self):
        self.assertEqual(self.row["tag"], rd.OUTAGE_TAG)
        self.assertEqual(self.row["kind"], "hold")
        self.assertEqual(self.row["state"], "escalated")
        self.assertEqual(self.row["next_actor"], "operator")
        self.assertEqual(self.row["subscriber"], "reconcile.yml")
        self.assertIsNone(self.row["discharges"])
        self.assertIs(self.row["adopted"], True)
        self.assertIsNone(self.row["cadence_s"])
        self.assertTrue((self.row["cadence_why"] or "").strip())

    def test_the_row_lands_after_the_sibling_so_it_never_races_it(self):
        """DRE-3428's `reviewer-environment-hold` had to land first — this card
        appends AFTER it, so the two never rewrite the same lines. Later cards
        append after this row in turn, so it is pinned after its sibling, not
        as the registry's last entry forever."""
        names = pipeline_act.acts()
        self.assertIn("reviewer-environment-hold", names)
        self.assertGreater(names.index(rd.ACT),
                           names.index("reviewer-environment-hold"))

    def test_the_emits_anchor_pins_the_receipt_writer(self):
        self.assertEqual(self.row["emits"],
                         {"file": "scripts/reviewer_down.py",
                          "anchor": "def outage_receipt("})

    def test_the_threshold_is_data_on_the_row_not_a_literal_in_code(self):
        self.assertEqual(self.row["threshold"],
                         {"window_s": 1800, "consecutive": 3, "repos": 2})
        self.assertTrue((self.row["threshold_why"] or "").strip())

    def test_threshold_from_registry_reads_that_row(self):
        self.assertEqual(rd.threshold_from_registry(), _threshold())

    def test_the_registry_stays_clean_with_the_row_on_it(self):
        self.assertEqual(pipeline_act.problems(), [])

    def test_neither_name_nor_tag_collides_with_anything_declared(self):
        """`tag in body` is how every receipt here is counted, so a tag that is
        a substring of another tag — or of an act NAME — is a budget counter
        reading somebody else's receipts."""
        rows = pipeline_act.rows()
        tags = [r["tag"] for r in rows]
        for other in tags:
            if other != rd.OUTAGE_TAG:
                self.assertNotIn(other, rd.ACT)
                self.assertNotIn(other, rd.OUTAGE_TAG)
                self.assertNotIn(rd.OUTAGE_TAG, other)
        for row in rows:
            self.assertNotIn(rd.OUTAGE_TAG, row["name"])

    def test_the_documentation_row_exists(self):
        text = (ROOT / "docs" / "pipeline-acts.md").read_text(encoding="utf-8")
        self.assertIn(rd.OUTAGE_TAG, text)
        self.assertIn(rd.ACT, text)
        self.assertIn("DRE-3433", text)


# --------------------------------------------------------------------------- #
# 2. reading the outcomes a sweep can see                                      #
# --------------------------------------------------------------------------- #


class TestOutcomesFromPullRequest(unittest.TestCase):
    def _pr(self, *comments):
        return {"number": 351, "comments": list(comments)}

    def test_the_neutral_receipt_is_a_could_not_run_outcome(self):
        pr = self._pr({
            "body": f"🔎 {medic_classify.CRITIC_NEUTRAL_MARKER} @{SHA}\n\nmore",
            "createdAt": "2026-09-08T22:20:00Z",
            "url": "https://example.invalid/c1",
        })
        (one,) = rd.outcomes_from_pr(pr, "bureau-pipeline")
        self.assertEqual(one.kind, rd.COULD_NOT_RUN)
        self.assertEqual(one.repo, "bureau-pipeline")
        self.assertEqual(one.at, "2026-09-08T22:20:00Z")
        self.assertEqual(one.src, "https://example.invalid/c1")
        self.assertEqual(one.pr, 351)

    def test_a_structured_verdict_is_a_verdict_outcome(self):
        pr = self._pr({
            "body": f"🔎 {merge_gate.CRITIC_MARKER} — VERDICT: APPROVE @{SHA}",
            "createdAt": "2026-09-08T23:40:00Z",
            "url": "https://example.invalid/c2",
        })
        (one,) = rd.outcomes_from_pr(pr, "bureau-pipeline")
        self.assertEqual(one.kind, rd.VERDICT)

    def test_only_the_first_line_counts_so_a_quotation_is_inert(self):
        pr = self._pr({
            "body": "I am quoting it:\n\n> " + medic_classify.CRITIC_NEUTRAL_MARKER,
            "createdAt": "2026-09-08T22:20:00Z",
            "url": "https://example.invalid/c3",
        })
        self.assertEqual(rd.outcomes_from_pr(pr, "bureau-pipeline"), [])

    def test_an_ordinary_comment_yields_nothing(self):
        pr = self._pr({"body": "looks good to me", "createdAt": "x",
                       "url": "https://example.invalid/c4"})
        self.assertEqual(rd.outcomes_from_pr(pr, "bureau-pipeline"), [])


class TestWitnessGenericShape(unittest.TestCase):
    """The medic's generic infra-crash note — a fleet-wide 429 is still seen."""

    def test_one_outcome_for_the_generic_note_and_none_for_prose(self):
        comments = [
            {"body": rd.MEDIC_BACKOFF_MARKER + " (an infrastructure rate-limit).",
             "createdAt": "2026-09-08T22:19:00Z"},
            {"body": "the plan looks fine", "createdAt": "2026-09-08T22:21:00Z"},
        ]
        out = rd.witness_from_comments("agent-bureau", "DRE-3409", comments)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].kind, rd.COULD_NOT_RUN)
        # The generic note carries no run link, so it names no repository
        # (DRE-5291) — never the card's `repo:` label.
        self.assertEqual(out[0].repo, rd.UNKNOWN_REPO)
        self.assertEqual(out[0].src, "linear:DRE-3409:2026-09-08T22:19:00Z")
        self.assertIn(rd.MEDIC_BACKOFF_MARKER, out[0].detail)

    def test_the_generic_literal_is_still_in_the_medic_workflow(self):
        """True before DRE-3430 and after it: that card keeps the literal
        exactly once, for a NON-environment infra crash. If it ever leaves the
        file, the 429 half of this witness has gone blind."""
        text = MEDIC.read_text(encoding="utf-8")
        self.assertIn(rd.MEDIC_BACKOFF_MARKER, text)


class TestWitnessEnvironmentShape(unittest.TestCase):
    """Pinned against the WRITER of the note, never against the workflow YAML."""

    def setUp(self):
        self.signature = reviewer_environment.detect(NATIVE_BINARY_LOG)
        self.assertIsNotNone(self.signature, "the log must carry the class")

    def test_a_real_evidence_note_is_exactly_one_could_not_run_outcome(self):
        note = reviewer_environment.evidence_note(self.signature, SHA, RUN_URL)
        comments = [{"body": note, "createdAt": "2026-09-08T22:19:00Z"}]
        out = rd.witness_from_comments("bureau-pipeline", "DRE-3409", comments)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].kind, rd.COULD_NOT_RUN)
        self.assertEqual(out[0].repo, "agent-bureau")
        self.assertEqual(out[0].src, "linear:DRE-3409:2026-09-08T22:19:00Z")

    def test_the_hold_receipt_is_not_a_witness(self):
        """It records a second crash whose evidence note is already counted,
        and for a non-review workflow it is not about the reviewer at all."""
        hold = reviewer_environment.hold_receipt(self.signature, SHA, "twice")
        out = rd.witness_from_comments("agent-bureau", "DRE-3409",
                                       [{"body": hold,
                                         "createdAt": "2026-09-08T22:40:00Z"}])
        self.assertEqual(out, [])

    def test_the_marker_is_imported_from_the_writer(self):
        self.assertEqual(rd.ENVIRONMENT_EVIDENCE_MARKER,
                         reviewer_environment.EVIDENCE_MARKER + " @")
        self.assertEqual(rd.WITNESS_MARKERS,
                         (rd.MEDIC_BACKOFF_MARKER, rd.ENVIRONMENT_EVIDENCE_MARKER))

    def test_the_sweeps_own_reviewer_down_note_is_not_counted(self):
        note = ("🚨 reviewer-down PR #7 @%s: the adversarial reviewer is DOWN "
                "for open PR #7." % SHA)
        self.assertEqual(
            rd.witness_from_comments("agent-bureau", "DRE-3409",
                                     [{"body": note, "createdAt": "t"}]),
            [],
        )


# --------------------------------------------------------------------------- #
# 3. the ledger                                                                #
# --------------------------------------------------------------------------- #


class TestLedger(unittest.TestCase):
    def test_a_line_round_trips(self):
        one = _cnr("agent-bureau", "2026-09-08T22:19:00Z", "linear:DRE-1:t", pr=None)
        line = rd.ledger_line(one)
        self.assertTrue(line.startswith(rd.LEDGER_PREFIX))
        self.assertEqual(
            line,
            "run repo=agent-bureau pr=- at=2026-09-08T22:19:00Z src=linear:DRE-1:t",
        )
        (back,) = rd.ledger_from_text(line)
        self.assertEqual(back.repo, "agent-bureau")
        self.assertEqual(back.at, "2026-09-08T22:19:00Z")
        self.assertEqual(back.src, "linear:DRE-1:t")
        self.assertIsNone(back.pr)
        self.assertEqual(back.kind, rd.COULD_NOT_RUN)

    def test_a_numbered_pull_request_round_trips(self):
        one = _cnr("bureau-pipeline", "2026-09-08T22:20:00Z", "https://x/1", pr=351)
        (back,) = rd.ledger_from_text("prose\n" + rd.ledger_line(one) + "\nmore")
        self.assertEqual(back.pr, 351)

    def test_lines_are_found_anywhere_in_a_description_or_comment(self):
        text = "\n".join([
            "Reviewer down since 15:19 PT — 2 runs, 2 repos",
            "",
            "- " + rd.ledger_line(_cnr("agent-bureau", "2026-09-08T22:19:00Z", "a")),
            "- " + rd.ledger_line(_cnr("bureau-pipeline", "2026-09-08T22:20:00Z", "b")),
        ])
        self.assertEqual([o.src for o in rd.ledger_from_text(text)], ["a", "b"])

    def test_prose_that_is_not_a_ledger_line_is_ignored(self):
        self.assertEqual(rd.ledger_from_text("we had a run repo= problem"), [])


# --------------------------------------------------------------------------- #
# 4. the decision                                                              #
# --------------------------------------------------------------------------- #


NOW = "2026-09-08T22:49:00Z"


class TestDecideFiles(unittest.TestCase):
    def test_three_outcomes_across_two_repos_file(self):
        local = [
            _cnr("bureau-pipeline", "2026-09-08T22:20:00Z", "a"),
            _cnr("bureau-pipeline", "2026-09-08T22:23:00Z", "b"),
        ]
        witness = [_cnr("agent-bureau", "2026-09-08T22:19:00Z", "c")]
        d = rd.decide(local, witness, None, NOW, _threshold())
        self.assertEqual(d.action, rd.FILE)
        self.assertEqual((d.runs, d.repos), (3, 2))

    def test_three_in_one_repo_with_no_verdict_between_them_file(self):
        local = [
            _cnr("bureau-pipeline", "2026-09-08T22:20:00Z", "a"),
            _cnr("bureau-pipeline", "2026-09-08T22:23:00Z", "b"),
            _cnr("bureau-pipeline", "2026-09-08T22:26:00Z", "c"),
        ]
        d = rd.decide(local, [], None, NOW, _threshold())
        self.assertEqual(d.action, rd.FILE)
        self.assertEqual((d.runs, d.repos), (3, 1))

    def test_two_in_one_repo_with_a_verdict_between_them_is_nothing(self):
        local = [
            _cnr("bureau-pipeline", "2026-09-08T22:20:00Z", "a"),
            _verdict("bureau-pipeline", "2026-09-08T22:23:00Z", "v"),
            _cnr("bureau-pipeline", "2026-09-08T22:26:00Z", "b"),
        ]
        d = rd.decide(local, [], None, NOW, _threshold())
        self.assertEqual(d.action, rd.NOTHING)

    def test_two_repos_with_no_verdict_after_either_still_file(self):
        """Two repositories is its own trigger: one crash each, nothing has
        succeeded in either since, and the reviewer is down in two places."""
        local = [_cnr("bureau-pipeline", "2026-09-08T22:20:00Z", "a")]
        witness = [_cnr("agent-bureau", "2026-09-08T22:26:00Z", "b")]
        d = rd.decide(local, witness, None, NOW, _threshold())
        self.assertEqual(d.action, rd.FILE)
        self.assertEqual((d.runs, d.repos), (2, 2))

    def test_a_verdict_before_the_repos_newest_crash_does_not_uncount_it(self):
        local = [
            _verdict("bureau-pipeline", "2026-09-08T22:19:00Z", "v"),
            _cnr("bureau-pipeline", "2026-09-08T22:20:00Z", "a"),
        ]
        witness = [_cnr("agent-bureau", "2026-09-08T22:26:00Z", "b")]
        self.assertEqual(rd.decide(local, witness, None, NOW, _threshold()).action,
                         rd.FILE)


class TestTheSpreadRuleSeesTheReviewerComeBack(unittest.TestCase):
    """DRE-6576: the spread rule counts a repository only while its newest
    could-not-run has no later verdict this sweep can see in that repository.
    DRE-6568 was filed at 22:07 PT over two crashes in two repositories, with
    six agent-bureau verdicts newer than agent-bureau's crash already in the
    filing sweep's own listing."""

    def test_a_repo_whose_newest_crash_has_a_later_local_verdict_is_not_counted(self):
        local = [
            _cnr("agent-bureau", "2026-09-08T22:20:00Z", "a"),
            _verdict("agent-bureau", "2026-09-08T22:23:00Z", "v"),
        ]
        witness = [_cnr("bureau-pipeline", "2026-09-08T22:26:00Z", "b")]
        d = rd.decide(local, witness, None, NOW, _threshold())
        self.assertEqual(d.action, rd.NOTHING)

    def test_a_repo_read_only_through_a_witness_note_still_counts(self):
        """The sweep cannot see bureau-pipeline's verdicts from agent-bureau,
        so a later agent-bureau crash plus that note is still two repos."""
        local = [
            _verdict("agent-bureau", "2026-09-08T22:19:00Z", "v"),
            _cnr("agent-bureau", "2026-09-08T22:24:00Z", "a"),
        ]
        witness = [_cnr("bureau-pipeline", "2026-09-08T22:20:00Z", "b")]
        d = rd.decide(local, witness, None, NOW, _threshold())
        self.assertEqual(d.action, rd.FILE)
        self.assertEqual((d.runs, d.repos), (2, 2))

    def test_the_run_rule_is_unchanged(self):
        local = [
            _cnr("agent-bureau", "2026-09-08T22:20:00Z", "a"),
            _verdict("agent-bureau", "2026-09-08T22:21:00Z", "v"),
            _cnr("agent-bureau", "2026-09-08T22:22:00Z", "b"),
            _cnr("agent-bureau", "2026-09-08T22:23:00Z", "c"),
            _cnr("agent-bureau", "2026-09-08T22:24:00Z", "d"),
        ]
        d = rd.decide(local, [], None, NOW, _threshold())
        self.assertEqual(d.action, rd.FILE)
        self.assertEqual((d.runs, d.repos), (4, 1))

    def test_outcomes_older_than_the_window_never_count(self):
        old = "2026-09-08T21:00:00Z"
        local = [
            _cnr("bureau-pipeline", old, "a"),
            _cnr("bureau-pipeline", old, "b"),
            _cnr("agent-bureau", old, "c"),
        ]
        d = rd.decide(local, [], None, NOW, _threshold())
        self.assertEqual(d.action, rd.NOTHING)

    def test_the_window_edge_is_inclusive(self):
        edge = "2026-09-08T22:19:00Z"  # exactly now - 1800s
        local = [
            _cnr("bureau-pipeline", edge, "a"),
            _cnr("bureau-pipeline", "2026-09-08T22:20:00Z", "b"),
            _cnr("bureau-pipeline", "2026-09-08T22:21:00Z", "c"),
        ]
        self.assertEqual(rd.decide(local, [], None, NOW, _threshold()).action,
                         rd.FILE)


class TestDecideAppends(unittest.TestCase):
    def _card(self, *outcomes):
        lines = "\n".join(rd.ledger_line(o) for o in outcomes)
        return rd.OpenCard(identifier="DRE-3500",
                           filed_at="2026-09-08T22:27:00Z",
                           text="Reviewer down\n\n" + lines)

    def setUp(self):
        # Real-shaped srcs on purpose: `src in card.text` is the idempotency
        # test, and a one-letter key would match the prose around the ledger.
        self.on_card = (
            _cnr("bureau-pipeline", "2026-09-08T22:20:00Z",
                 "https://example.invalid/pull/351#issuecomment-1"),
            _cnr("bureau-pipeline", "2026-09-08T22:23:00Z",
                 "https://example.invalid/pull/352#issuecomment-2"),
            _cnr("agent-bureau", "2026-09-08T22:26:00Z",
                 "linear:DRE-3409:2026-09-08T22:26:00Z"),
        )
        self.card = self._card(*self.on_card)
        self.fourth = _cnr("agent-bureau", "2026-09-08T22:31:00Z",
                           "linear:DRE-3411:2026-09-08T22:31:00Z")

    def test_a_fourth_outcome_appends_exactly_one_line(self):
        d = rd.decide([], [self.fourth], self.card, NOW, _threshold())
        self.assertEqual(d.action, rd.APPEND)
        self.assertEqual(d.lines, [rd.ledger_line(self.fourth)])
        self.assertEqual((d.runs, d.repos), (4, 2))
        self.assertEqual(d.title, "Reviewer down since 15:20 PT — 4 runs, 2 repos")

    def test_the_same_outcome_re_presented_is_nothing(self):
        appended = rd.decide([], [self.fourth], self.card, NOW, _threshold())
        grown = rd.OpenCard(
            identifier=self.card.identifier,
            filed_at=self.card.filed_at,
            text=self.card.text + "\n" + "\n".join(appended.lines),
        )
        again = rd.decide([], [self.fourth], grown, NOW, _threshold())
        self.assertEqual(again.action, rd.NOTHING)


class TestDecideCloses(unittest.TestCase):
    def setUp(self):
        crashed = _cnr("bureau-pipeline", "2026-09-08T22:20:00Z",
                       "https://example.invalid/pull/351#issuecomment-1", pr=351)
        self.card = rd.OpenCard(identifier="DRE-3500",
                                filed_at="2026-09-08T22:27:00Z",
                                text="Reviewer down since 15:19 PT\n\n- "
                                     + rd.ledger_line(crashed))

    def test_a_verdict_after_the_card_was_filed_closes_it(self):
        later = _verdict("bureau-pipeline", "2026-09-08T22:32:00Z",
                         "https://example.invalid/v")
        d = rd.decide([later], [], self.card, NOW, _threshold())
        self.assertEqual(d.action, rd.CLOSE)
        self.assertEqual(
            d.resolve_note,
            "reviewer back at 15:32 PT — first successful verdict in "
            "bureau-pipeline after its last counted crash "
            "(https://example.invalid/v)",
        )

    def test_a_verdict_before_the_card_was_filed_closes_it(self):
        """DRE-6576: after the repository's last counted crash is the whole
        rule. Every verdict that said DRE-6568's reviewer was back landed
        before the card was filed, so the filed-at rule kept it open."""
        earlier = _verdict("bureau-pipeline", "2026-09-08T22:25:00Z", "v")
        d = rd.decide([earlier], [], self.card, NOW, _threshold())
        self.assertEqual(d.action, rd.CLOSE)
        self.assertEqual(d.first_at, "2026-09-08T22:25:00Z")

    def test_a_card_whose_crashes_have_all_aged_out_still_closes(self):
        """`now` two hours past the crashes: the ledger is read whole, and
        the window only ever applied to crashes not yet written on the card."""
        later = _verdict("bureau-pipeline", "2026-09-08T22:32:00Z",
                         "https://example.invalid/v")
        d = rd.decide([later], [], self.card, "2026-09-09T00:20:00Z", _threshold())
        self.assertEqual(d.action, rd.CLOSE)
        self.assertEqual(d.first_at, "2026-09-08T22:32:00Z")
        self.assertEqual(
            d.resolve_note,
            "reviewer back at 15:32 PT — first successful verdict in "
            "bureau-pipeline after its last counted crash "
            "(https://example.invalid/v)",
        )

    def test_a_card_whose_ledger_names_no_repository_closes_on_any_verdict(self):
        """No line names a repository, so there is no repository to be wrong
        about: the first local verdict after the last counted crash says it
        is back — or the card could never close itself (DRE-5291)."""
        bare = rd.OpenCard(identifier="DRE-3500",
                           filed_at="2026-09-08T22:27:00Z",
                           text="Reviewer down since 15:19 PT")
        later = _verdict("bureau-pipeline", "2026-09-08T22:32:00Z", "v")
        d = rd.decide([later], [], bare, NOW, _threshold())
        self.assertEqual(d.action, rd.CLOSE)
        self.assertEqual(
            d.resolve_note,
            "reviewer back at 15:32 PT — first successful verdict in "
            "bureau-pipeline after the last counted crash (v)",
        )


# --------------------------------------------------------------------------- #
# 4b. DRE-5291 — one crash counted once, and closed only where it crashed      #
# --------------------------------------------------------------------------- #

# DRE-5273's inputs, live on 2026-09-29 (bureau-pipeline PR #577,
# `docs/reviewer-environment-hold-proof.md` §3): ONE crash, QA Review run
# 36656565242 on agent-bureau-demo #25, seen twice — once as the review
# workflow's could-not-run notice on the pull request, once as the medic's
# evidence note on DRE-4570, a card labelled `repo:bureau-pipeline`.
DEMO_RUN_URL = ("https://github.com/dreadnought-foundry/agent-bureau-demo/"
                "actions/runs/36656565242")
DEMO_NOTICE_SRC = ("https://github.com/dreadnought-foundry/agent-bureau-demo/"
                   "pull/25#issuecomment-5902476607")
DEMO_NOTICE_AT = "2026-09-30T01:49:51Z"      # 18:49:51 PT
MEDIC_NOTE_AT = "2026-09-30T01:50:33.766Z"   # 18:50:33 PT
DRE_5273_FILED = "2026-09-30T02:04:59Z"      # 19:04:59 PT
DRE_5273_WRONG_CLOSE = "2026-09-30T02:05:21Z"  # 19:05:21 PT
DEMO_RETRY_CRASH_AT = "2026-09-30T02:07:55Z"   # 19:07:55 PT
DEMO_FIRST_VERDICT_AT = "2026-09-30T03:26:50Z"  # 20:26:50 PT

#: DRE-5273's ledger, verbatim — the OLD shape, which nothing rewrites.
DRE_5273_LEDGER = (
    "run repo=agent-bureau-demo pr=#25 at=2026-09-30T01:49:51Z "
    "src=https://github.com/dreadnought-foundry/agent-bureau-demo/pull/25"
    "#issuecomment-5902476607\n"
    "run repo=bureau-pipeline pr=- at=2026-09-30T01:50:33.766Z "
    "src=linear:DRE-4570:2026-09-30T01:50:33.766Z"
)


def _medic_note(run_url=DEMO_RUN_URL):
    """The note the medic left on DRE-4570, built by its own WRITER."""
    signature = reviewer_environment.by_slug("native-binary-missing")
    return reviewer_environment.evidence_note(signature, SHA, run_url)


def _demo_notice():
    return _cnr("agent-bureau-demo", DEMO_NOTICE_AT, DEMO_NOTICE_SRC, pr=25)


class TestWitnessAttribution(unittest.TestCase):
    """A witness note belongs to the repository its run link names, never to
    the `repo:` label of the card it happens to be posted on."""

    def test_the_note_is_attributed_to_the_repository_in_its_run_url(self):
        comments = [{"body": _medic_note(), "createdAt": MEDIC_NOTE_AT}]
        # Read by bureau-pipeline's sweep, off a `repo:bureau-pipeline` card.
        (one,) = rd.witness_from_comments("bureau-pipeline", "DRE-4570", comments)
        self.assertEqual(one.repo, "agent-bureau-demo")
        self.assertEqual(one.kind, rd.COULD_NOT_RUN)
        self.assertEqual(one.src, f"linear:DRE-4570:{MEDIC_NOTE_AT}")

    def test_a_sweep_skips_a_note_naming_its_own_repository(self):
        """It already counts its own crashes off its pull requests' notices,
        and the notice carries no run link to pair the two by."""
        comments = [{"body": _medic_note(), "createdAt": MEDIC_NOTE_AT}]
        self.assertEqual(
            rd.witness_from_comments("agent-bureau-demo", "DRE-4570", comments),
            [],
        )

    def test_the_repository_is_read_case_insensitively_as_the_sweep_slugs_it(self):
        """reconcile.yml slugs its own repository as the lowercased basename."""
        url = ("https://github.com/Dreadnought-Foundry/Agent-Bureau-Demo/"
               "actions/runs/36656565242")
        comments = [{"body": _medic_note(url), "createdAt": MEDIC_NOTE_AT}]
        self.assertEqual(
            rd.witness_from_comments("agent-bureau-demo", "DRE-4570", comments),
            [],
        )

    def test_a_link_in_linear_markdown_still_names_the_repository(self):
        """Linear stores a pasted url as `[url](<url>)`."""
        body = (rd.MEDIC_BACKOFF_MARKER + " — crashed.\n\nThe failed run: "
                f"[{DEMO_RUN_URL}](<{DEMO_RUN_URL}>)")
        (one,) = rd.witness_from_comments(
            "bureau-pipeline", "DRE-4570",
            [{"body": body, "createdAt": MEDIC_NOTE_AT}])
        self.assertEqual(one.repo, "agent-bureau-demo")

    def test_a_note_with_no_readable_run_url_names_no_repository(self):
        body = rd.MEDIC_BACKOFF_MARKER + " (an infrastructure rate-limit)."
        (one,) = rd.witness_from_comments(
            "agent-bureau-demo", "DRE-4570",
            [{"body": body, "createdAt": MEDIC_NOTE_AT}])
        self.assertEqual(one.repo, rd.UNKNOWN_REPO)


class TestTheDRE5273Replay(unittest.TestCase):
    """One crash, counted once — the filing the sandbox's sweep got wrong."""

    def _witness(self, sweep_repo):
        return rd.witness_from_comments(
            sweep_repo, "DRE-4570",
            [{"body": _medic_note(), "createdAt": MEDIC_NOTE_AT}])

    def test_the_sandboxs_sweep_counts_one_run_in_one_repo(self):
        local = [_demo_notice()]
        witness = self._witness("agent-bureau-demo")
        window = rd._window(local + witness, DRE_5273_FILED, _threshold().window_s)
        runs, repos, _ = rd._counts([o for o in window
                                     if o.kind == rd.COULD_NOT_RUN])
        self.assertEqual((runs, repos), (1, 1))
        self.assertFalse(rd._threshold_met(window, _threshold()))
        self.assertEqual(
            rd.decide(local, witness, None, DRE_5273_FILED, _threshold()).action,
            rd.NOTHING,
            "one crash is not a fleet outage — DRE-5273 is never filed",
        )

    def test_another_repos_sweep_counts_the_same_crash_in_the_sandbox(self):
        """bureau-pipeline's sweep has no notice of its own: the note is one
        run in agent-bureau-demo there, and still one repository."""
        witness = self._witness("bureau-pipeline")
        runs, repos, _ = rd._counts(witness)
        self.assertEqual((runs, repos), (1, 1))
        self.assertEqual(
            rd.decide([], witness, None, DRE_5273_FILED, _threshold()).action,
            rd.NOTHING,
        )


class TestUnknownRepository(unittest.TestCase):
    """A note naming no repository is a run, never a second repository."""

    def test_one_unknown_plus_one_real_crash_is_two_runs_one_repo(self):
        generic = rd.witness_from_comments(
            "bureau-pipeline", "DRE-4570",
            [{"body": rd.MEDIC_BACKOFF_MARKER + " (a rate limit).",
              "createdAt": MEDIC_NOTE_AT}])
        local = [_demo_notice()]
        window = rd._window(local + generic, DRE_5273_FILED, _threshold().window_s)
        runs, repos, _ = rd._counts(window)
        self.assertEqual((runs, repos), (2, 1))
        self.assertFalse(rd._threshold_met(window, _threshold()),
                         "the spread rule does not fire on one real repository")
        self.assertEqual(
            rd.decide(local, generic, None, DRE_5273_FILED, _threshold()).action,
            rd.NOTHING,
        )

    def test_the_unknown_repository_round_trips_through_the_ledger(self):
        one = _cnr(rd.UNKNOWN_REPO, MEDIC_NOTE_AT, f"linear:DRE-4570:{MEDIC_NOTE_AT}")
        (back,) = rd.ledger_from_text(rd.ledger_line(one))
        self.assertEqual(back.repo, rd.UNKNOWN_REPO)
        self.assertEqual(rd._counts([back, _demo_notice()])[:2], (2, 1))

    def test_two_unknowns_alone_are_zero_repositories(self):
        a = _cnr(rd.UNKNOWN_REPO, "2026-09-30T01:50:00Z", "linear:DRE-1:a")
        b = _cnr(rd.UNKNOWN_REPO, "2026-09-30T01:51:00Z", "linear:DRE-2:b")
        self.assertEqual(rd._counts([a, b])[:2], (2, 0))
        self.assertFalse(rd._threshold_met([a, b], _threshold()))

    def _unknown_card(self):
        """Three generic medic notes — no run link, so no repository — meet
        the run rule on their own, and the card they file names none."""
        notes = [_cnr(rd.UNKNOWN_REPO, f"2026-09-08T22:2{i}:00Z",
                      f"linear:DRE-{4600 + i}:2026-09-08T22:2{i}:00Z")
                 for i in range(3)]
        filed = rd.decide([], notes, None, NOW, _threshold())
        self.assertEqual(filed.action, rd.FILE)
        self.assertEqual((filed.runs, filed.repos), (3, 0))
        return rd.OpenCard(identifier="DRE-3500",
                           filed_at="2026-09-08T22:27:00Z",
                           text=filed.title + "\n\n" + filed.body)

    def test_a_card_filed_from_unknown_notes_alone_closes_itself(self):
        """The critic's repro on PR #583: this card used to stay open for
        good, and every later sweep could only append to it."""
        later = _verdict("bureau-pipeline", "2026-09-08T22:32:00Z",
                         "https://example.invalid/v")
        d = rd.decide([later], [], self._unknown_card(), NOW, _threshold())
        self.assertEqual(d.action, rd.CLOSE)
        self.assertEqual(d.first_at, "2026-09-08T22:32:00Z")

    def test_a_verdict_before_the_last_unknown_crash_is_not_a_close(self):
        card = self._unknown_card()
        late_crash = _cnr(rd.UNKNOWN_REPO, "2026-09-08T22:33:00Z",
                          "linear:DRE-4700:2026-09-08T22:33:00Z")
        between = _verdict("bureau-pipeline", "2026-09-08T22:30:00Z", "v")
        d = rd.decide([between], [late_crash], card, NOW, _threshold())
        self.assertEqual(d.action, rd.APPEND)


class TestOldLedgerLines(unittest.TestCase):
    """Lines already written on open or closed cards are left as they are."""

    def test_the_old_shape_still_reads(self):
        old = rd.ledger_from_text("Reviewer down\n\n- " +
                                  DRE_5273_LEDGER.replace("\n", "\n- "))
        self.assertEqual([(o.repo, o.pr, o.at, o.src) for o in old], [
            ("agent-bureau-demo", 25, DEMO_NOTICE_AT, DEMO_NOTICE_SRC),
            ("bureau-pipeline", None, MEDIC_NOTE_AT,
             f"linear:DRE-4570:{MEDIC_NOTE_AT}"),
        ])

    def test_an_append_writes_only_new_lines_and_never_restates_old_ones(self):
        card = rd.OpenCard("DRE-5273", DRE_5273_FILED, DRE_5273_LEDGER)
        retry = _cnr("agent-bureau-demo", DEMO_RETRY_CRASH_AT,
                     "https://example.invalid/pull/25#issuecomment-2", pr=25)
        d = rd.decide([_demo_notice(), retry], [], card,
                      "2026-09-30T02:10:00Z", _threshold())
        self.assertEqual(d.action, rd.APPEND)
        self.assertEqual(d.lines, [rd.ledger_line(retry)])
        # The old mis-attributed line still counts as it was written.
        self.assertEqual((d.runs, d.repos), (3, 2))


class TestTheDRE5273Close(unittest.TestCase):
    """Closed only by a verdict in a repository that crashed."""

    def setUp(self):
        ledger = "Reviewer down\n\n- " + rd.ledger_line(_demo_notice())
        self.card = rd.OpenCard("DRE-5273", DRE_5273_FILED, ledger)

    def test_a_verdict_in_a_repository_that_never_crashed_does_not_close(self):
        healthy = _verdict(
            "bureau-pipeline", DRE_5273_WRONG_CLOSE,
            "https://github.com/dreadnought-foundry/bureau-pipeline/pull/564"
            "#issuecomment-5902630938", pr=564)
        d = rd.decide([healthy], [], self.card, DRE_5273_WRONG_CLOSE, _threshold())
        self.assertNotEqual(d.action, rd.CLOSE)

    def test_the_first_sandbox_verdict_after_its_last_crash_closes_it(self):
        back = _verdict("agent-bureau-demo", DEMO_FIRST_VERDICT_AT,
                        "https://example.invalid/pull/25#issuecomment-9", pr=25)
        later = _verdict("agent-bureau-demo", "2026-09-30T03:40:00Z",
                         "https://example.invalid/pull/26#issuecomment-9", pr=26)
        d = rd.decide([later, back], [], self.card, "2026-09-30T03:45:00Z",
                      _threshold())
        self.assertEqual(d.action, rd.CLOSE)
        self.assertEqual(d.first_at, DEMO_FIRST_VERDICT_AT)
        self.assertIn("agent-bureau-demo", d.resolve_note)
        self.assertIn(back.src, d.resolve_note)

    def test_a_verdict_before_that_repositorys_last_crash_does_not_close(self):
        """Filed at 19:04:59, a verdict at 19:06, and the retry crashed again
        at 19:07:55 — the reviewer was not back."""
        retry = _cnr("agent-bureau-demo", DEMO_RETRY_CRASH_AT,
                     "https://example.invalid/pull/25#issuecomment-2", pr=25)
        card = rd.OpenCard("DRE-5273", DRE_5273_FILED,
                           self.card.text + "\n- " + rd.ledger_line(retry))
        early = _verdict("agent-bureau-demo", "2026-09-30T02:06:00Z",
                         "https://example.invalid/pull/26#issuecomment-1", pr=26)
        d = rd.decide([early], [], card, "2026-09-30T02:30:00Z", _threshold())
        self.assertNotEqual(d.action, rd.CLOSE)

    def test_a_crash_this_sweep_has_not_yet_appended_still_counts_as_last(self):
        retry = _cnr("agent-bureau-demo", DEMO_RETRY_CRASH_AT,
                     "https://example.invalid/pull/25#issuecomment-2", pr=25)
        early = _verdict("agent-bureau-demo", "2026-09-30T02:06:00Z",
                         "https://example.invalid/pull/26#issuecomment-1", pr=26)
        d = rd.decide([early, retry], [], self.card, "2026-09-30T02:10:00Z",
                      _threshold())
        self.assertEqual(d.action, rd.APPEND)


# --------------------------------------------------------------------------- #
# 5. what the card says                                                        #
# --------------------------------------------------------------------------- #


class TestTheCardText(unittest.TestCase):
    def test_pt_formats_pacific(self):
        self.assertEqual(rd.pt("2026-09-08T22:19:00Z"), "15:19 PT")

    def test_the_title_is_always_plural_and_always_integers(self):
        self.assertEqual(
            rd.card_title("2026-09-08T22:19:00Z", 1, 1),
            "Reviewer down since 15:19 PT — 1 runs, 1 repos",
        )
        self.assertTrue(rd.card_title("2026-09-08T22:19:00Z", 7, 2)
                        .startswith(rd.TITLE_PREFIX))

    def test_the_body_carries_the_three_suspects_in_order_with_commands(self):
        run = rd.FirstRun(
            repo="agent-bureau", pr=2367, at="2026-09-08T22:19:00Z",
            run_url=RUN_URL,
            log_line="ReferenceError: Claude Code native binary not found at "
                     "/home/runner/.local/bin/claude",
            action_ref="anthropics/claude-code-action@v1",
        )
        body = rd.card_body(run, ["run repo=agent-bureau pr=#2367 "
                                  "at=2026-09-08T22:19:00Z src=linear:DRE-1:t"])
        self.assertIn("native binary not found", body)
        self.assertIn("```", body)
        self.assertIn("anthropics/claude-code-action@v1", body)
        positions = [
            body.index("gh api repos/anthropics/claude-code-action/git/ref/tags/v1"),
            body.index("make cred-doctor --account"),
            body.index("status.anthropic.com"),
        ]
        self.assertEqual(positions, sorted(positions),
                         "vendor action release, then the credential, then the "
                         "status page — that order is the diagnosis order")
        self.assertIn("run repo=agent-bureau", body)
        self.assertIn("first successful verdict", body)

    def test_the_receipt_names_the_act_and_the_counts(self):
        d = rd.decide(
            [_cnr("bureau-pipeline", "2026-09-08T22:20:00Z", "a"),
             _cnr("bureau-pipeline", "2026-09-08T22:23:00Z", "b")],
            [_cnr("agent-bureau", "2026-09-08T22:26:00Z", "c")],
            None, NOW, _threshold(),
        )
        detail = rd.outage_receipt(d)
        self.assertIn(rd.OUTAGE_TAG, detail)
        self.assertIn("3 runs", detail)
        self.assertIn("2 repos", detail)
        # It composes cleanly through the one writer the wiring will use.
        composed = pipeline_act.receipt(rd.ACT, detail)
        self.assertTrue(composed.startswith(detail))
        self.assertIn(f"tag: {rd.OUTAGE_TAG}", composed)


# --------------------------------------------------------------------------- #
# 6. the two log readers                                                       #
# --------------------------------------------------------------------------- #


class TestErrorLine(unittest.TestCase):
    EXCERPT = (
        "critic\tRun the critic\t2026-09-08T22:19:02.4410000Z Running Claude Code\n"
        "critic\tRun the critic\t2026-09-08T22:19:03.1234567Z ReferenceError: "
        "Claude Code native binary not found at /home/runner/.local/bin/claude\n"
        "critic\tRun the critic\t2026-09-08T22:19:03.9990000Z ##[error]Process "
        "completed with exit code 1.\n"
    )

    def test_it_returns_the_error_line_without_the_prefix(self):
        self.assertEqual(
            rd.error_line(self.EXCERPT),
            "ReferenceError: Claude Code native binary not found at "
            "/home/runner/.local/bin/claude",
        )

    def test_empty_input_says_so_rather_than_guessing(self):
        self.assertEqual(rd.error_line(""), "log tail unreadable")
        self.assertEqual(rd.error_line(None), "log tail unreadable")

    def test_with_no_error_phrase_it_falls_back_to_the_last_non_empty_line(self):
        text = ("job\tstep\t2026-09-08T22:19:02.0000000Z first\n"
                "job\tstep\t2026-09-08T22:19:03.0000000Z last\n\n")
        self.assertEqual(rd.error_line(text), "last")


class TestActionRef(unittest.TestCase):
    def test_the_first_reference_and_its_trailing_comment(self):
        text = (
            "      - name: critic\n"
            "        uses: anthropics/claude-code-action@v1  # floating, DRE-3417\n"
            "      - uses: anthropics/claude-code-action@v2\n"
        )
        self.assertEqual(
            rd.action_ref(text),
            "anthropics/claude-code-action@v1  # floating, DRE-3417",
        )

    def test_a_bare_reference_has_no_comment(self):
        self.assertEqual(rd.action_ref("uses: anthropics/claude-code-action@v1\n"),
                         "anthropics/claude-code-action@v1")

    def test_a_workflow_that_never_names_it_reads_empty(self):
        self.assertEqual(rd.action_ref("uses: actions/checkout@v4\n"), "")


# --------------------------------------------------------------------------- #
# 7. the incident replay                                                       #
# --------------------------------------------------------------------------- #


class TestTheIncidentReplay(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_the_fixture_says_where_it_came_from(self):
        header = " ".join(self.doc["_header"])
        self.assertIn("PROVENANCE", header)
        self.assertIn("DRE-3416", header)

    def test_it_records_seven_could_not_run_outcomes_across_two_repos(self):
        every = self.doc["local"] + self.doc["witness"]
        self.assertEqual(len(every), 7)
        self.assertEqual({o["kind"] for o in every}, {"could-not-run"})
        self.assertEqual({o["repo"] for o in every},
                         {"agent-bureau", "bureau-pipeline"})

    def test_the_first_run_carries_the_verbatim_error_and_action_ref(self):
        run = self.doc["first_run"]
        self.assertIn("Claude Code native binary not found", run["log_line"])
        self.assertEqual(run["action_ref"], "anthropics/claude-code-action@v1")

    def test_replaying_it_files_the_one_card(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rd.main(["replay", str(FIXTURE), "--now", NOW])
        self.assertEqual(code, 0)
        decision = json.loads(out.getvalue())
        self.assertEqual(decision["action"], rd.FILE)
        self.assertEqual(decision["title"],
                         "Reviewer down since 15:19 PT — 7 runs, 2 repos")
        body = decision["body"]
        self.assertIn("native binary not found", body)
        self.assertIn("anthropics/claude-code-action@v1", body)
        positions = [
            body.index("gh api repos/anthropics/claude-code-action/git/ref/tags/v1"),
            body.index("make cred-doctor --account"),
            body.index("status.anthropic.com"),
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(decision["lines"]), 7)

    def test_the_fixture_now_is_the_one_the_card_replays_with(self):
        self.assertEqual(self.doc["now"], NOW)

    def test_it_files_at_the_same_moment_under_the_verdict_aware_spread_rule(self):
        """DRE-6576 teaches the spread rule to read verdicts, and this fixture
        holds none — so the outcomes as they stood at each instant file at
        22:20 UTC, the second repository's first crash, and not a minute
        before, exactly as before that card."""
        every = self.doc["local"] + self.doc["witness"]
        self.assertNotIn(rd.VERDICT, {o["kind"] for o in every})

        def as_of(instant):
            seen = {key: [o for o in self.doc[key] if o["at"] <= instant]
                    for key in ("local", "witness")}
            return rd.replay({**self.doc, **seen}, instant).action

        self.assertEqual(as_of("2026-09-08T22:19:59Z"), rd.NOTHING)
        self.assertEqual(as_of("2026-09-08T22:20:00Z"), rd.FILE)


# --------------------------------------------------------------------------- #
# 7b. the 2026-10-09 replay — DRE-6568, filed after the reviewer was back       #
# --------------------------------------------------------------------------- #

FIXTURE_1009 = ROOT / "tests" / "fixtures" / "reviewer-down-2026-10-09.json"
AB_CRASH_AT = "2026-10-10T04:40:27Z"
DRE_6568_FILED = "2026-10-10T05:07:34Z"
DRE_6568_CLOSED_BY_HAND = "2026-10-10T06:37:00Z"


class TestTheDRE6568Replay(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(FIXTURE_1009.read_text(encoding="utf-8"))

    def _local_verdicts_after_the_crash(self):
        return sorted(o["at"] for o in self.doc["local"]
                      if o["kind"] == rd.VERDICT and o["at"] > AB_CRASH_AT)

    def test_the_fixture_says_where_it_came_from(self):
        header = " ".join(self.doc["_header"])
        self.assertIn("PROVENANCE", header)
        self.assertIn("DRE-6568", header)
        self.assertEqual(self.doc["now"], DRE_6568_FILED)

    def test_it_records_what_the_agent_bureau_sweep_saw_at_22_07(self):
        crashes = [(o["repo"], o["at"]) for o in self.doc["local"]
                   if o["kind"] == rd.COULD_NOT_RUN]
        self.assertEqual(crashes, [("agent-bureau", AB_CRASH_AT)])
        verdicts = self._local_verdicts_after_the_crash()
        self.assertEqual(verdicts[0], "2026-10-10T04:44:00Z")
        self.assertIn("2026-10-10T04:56:34Z", verdicts, "#3503's own re-review")
        (witness,) = self.doc["witness"]
        self.assertEqual((witness["repo"], witness["at"], witness["src"]), (
            "bureau-pipeline", "2026-10-10T04:40:51Z",
            "linear:DRE-6533:2026-10-10T04:40:51Z"))

    def test_replaying_it_at_the_filing_instant_files_nothing(self):
        """RED before DRE-6576: two crashes in two repos met the spread rule
        however many agent-bureau verdicts had landed since."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rd.main(["replay", str(FIXTURE_1009)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["action"], rd.NOTHING)

    def test_dre_6568_closes_after_its_crashes_aged_out(self):
        """The card as filed, read at 23:37 PT, when a person closed it by
        hand: the first agent-bureau verdict after agent-bureau's crash."""
        card = self.doc["dre_6568"]
        self.assertEqual(card["filed_at"], DRE_6568_FILED)
        self.assertEqual(len(rd.ledger_from_text(card["text"])), 2)
        d = rd.replay({**self.doc, "open_card": card}, DRE_6568_CLOSED_BY_HAND)
        self.assertEqual(d.action, rd.CLOSE)
        self.assertEqual(d.first_at, self._local_verdicts_after_the_crash()[0])
        self.assertTrue(d.resolve_note.startswith(
            "reviewer back at 21:44 PT — first successful verdict in "
            "agent-bureau after its last counted crash ("), d.resolve_note)


# --------------------------------------------------------------------------- #
# 7c. what the card and its receipt promise, beside what decide does           #
# --------------------------------------------------------------------------- #

BODY_CLOSE_PROMISE = (
    "The sweep closes this card by itself on the first successful verdict it "
    "can see on an open pull request in a repository listed above, posted "
    "after that repository's last counted crash — or, when no line names a "
    "repository, in any repository after the last counted crash. It reads "
    "this card on every sweep for as long as the card is open in Triage or in "
    "another lane the sweep reads, however long ago the crashes were. It "
    "cannot see a verdict on a pull request that has since merged or closed, "
    "and it does not read Backlog or Green Light; in those cases a person "
    "closes this card."
)
RECEIPT_CLOSE_PROMISE = (
    "The sweep closes it on the first successful verdict it can see in a "
    "repository it counted, after that repository's last counted crash."
)


class TestThePromiseMatchesTheBehavior(unittest.TestCase):
    """Each sentence is asserted, and then every case it promises — a close,
    or a person — is driven through `decide`."""

    FILED = "2026-09-08T22:27:00Z"
    CRASH = _cnr("bureau-pipeline", "2026-09-08T22:20:00Z",
                 "https://example.invalid/pull/351#issuecomment-1", pr=351)

    def _card(self, text=None):
        return rd.OpenCard("DRE-3500", self.FILED,
                           text if text is not None
                           else rd.card_body(None, [rd.ledger_line(self.CRASH)]))

    def test_the_body_says_it(self):
        body = rd.card_body(None, [rd.ledger_line(self.CRASH)])
        self.assertIn(BODY_CLOSE_PROMISE, body)
        self.assertNotIn("Nothing else needs to happen here", body)
        self.assertNotIn("after it was filed", body)

    def test_the_receipt_says_it(self):
        d = rd.decide([self.CRASH], [_cnr("agent-bureau", "2026-09-08T22:21:00Z",
                                          "linear:DRE-1:x")],
                      None, NOW, _threshold())
        receipt = rd.outage_receipt(d)
        self.assertTrue(receipt.endswith(RECEIPT_CLOSE_PROMISE), receipt)
        self.assertNotIn("after it was filed", receipt)

    def test_every_row_of_the_table(self):
        v = "https://example.invalid/v"
        in_window = _verdict("bureau-pipeline", "2026-09-08T22:32:00Z", v)
        before_filing = _verdict("bureau-pipeline", "2026-09-08T22:25:00Z", v)
        before_crash = _verdict("bureau-pipeline", "2026-09-08T22:19:00Z", v)
        elsewhere = _verdict("agent-bureau", "2026-09-08T22:32:00Z", v)
        unappended = _cnr("bureau-pipeline", "2026-09-08T22:30:00Z",
                          "https://example.invalid/pull/352#issuecomment-2")
        between = _verdict("bureau-pipeline", "2026-09-08T22:28:00Z", v)
        two_hours_on = "2026-09-09T00:20:00Z"
        bare = self._card("Reviewer down since 15:19 PT\n\n- "
                          + rd.ledger_line(_cnr(rd.UNKNOWN_REPO, self.CRASH.at,
                                                "linear:DRE-1:x")))
        rows = [
            ("a counted repo's verdict, crashes in the window",
             [in_window], self._card(), NOW, True),
            ("the same, every crash older than window_s",
             [in_window], self._card(), two_hours_on, True),
            ("the same, posted before the card was filed",
             [before_filing], self._card(), NOW, True),
            ("a ledger naming no repo, a verdict anywhere after its crash",
             [elsewhere], bare, NOW, True),
            ("a verdict in a repo the ledger does not name",
             [elsewhere], self._card(), NOW, False),
            ("a verdict before that repo's last counted crash",
             [before_crash], self._card(), NOW, False),
            ("a verdict before a crash this sweep sees, not yet appended",
             [between, unappended], self._card(), NOW, False),
            ("its pull request is no longer in the listing — not seen",
             [], self._card(), two_hours_on, False),
            ("the card is in Backlog or Green Light — not read",
             [in_window], None, two_hours_on, False),
        ]
        for name, local, card, now, closes in rows:
            with self.subTest(name):
                d = rd.decide(local, [], card, now, _threshold())
                self.assertEqual(d.action == rd.CLOSE, closes, (name, d))


# --------------------------------------------------------------------------- #
# 8. the module's shape                                                        #
# --------------------------------------------------------------------------- #


class TestTheModuleShape(unittest.TestCase):
    def setUp(self):
        self.source = SOURCE.read_text(encoding="utf-8")

    def test_the_environment_marker_is_never_restated(self):
        """It is DRE-3428's contract and lives in `reviewer_environment`. A
        second copy here is a second thing to reword, and a reword on one side
        blinds this detector for the crash class the epic is named after."""
        self.assertNotIn(reviewer_environment.EVIDENCE_MARKER, self.source)

    def test_no_network_and_no_subprocess(self):
        self.assertNotIn("subprocess", self.source)
        self.assertNotIn("urllib", self.source)

    def test_the_only_gh_in_the_file_is_the_operator_command_it_prints(self):
        """`gh` may appear as TEXT — one of the three suspects an operator
        checks by hand is a vendor-tag read — but never as a command this
        module runs."""
        allowed = "gh api repos/anthropics/claude-code-action/git/ref/tags/v1"
        word = re.compile(r"\bgh\b")
        offenders = [line for line in self.source.splitlines()
                     if word.search(line) and allowed not in line]
        self.assertEqual(offenders, [])

    def test_the_receipt_anchor_appears_exactly_once(self):
        self.assertEqual(self.source.count("def outage_receipt("), 1)

    def test_the_outcome_is_frozen(self):
        one = _cnr("bureau-pipeline", "2026-09-08T22:20:00Z", "a")
        with self.assertRaises(FrozenInstanceError):
            one.repo = "agent-bureau"
        self.assertEqual([f.name for f in fields(rd.Outcome)],
                         ["repo", "at", "kind", "src", "pr", "detail"])

    def test_the_decision_carries_the_contracted_fields(self):
        self.assertEqual(
            [f.name for f in fields(rd.Decision)],
            ["action", "title", "body", "lines", "runs", "repos", "first_at",
             "resolve_note"],
        )


if __name__ == "__main__":
    unittest.main()
