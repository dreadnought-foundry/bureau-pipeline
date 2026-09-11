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

    def test_the_row_is_the_last_entry_so_it_never_races_the_sibling(self):
        """DRE-3428's `reviewer-environment-hold` had to land first — this card
        appends AFTER it, so the two never rewrite the same lines."""
        names = pipeline_act.acts()
        self.assertEqual(names[-1], rd.ACT)
        self.assertIn("reviewer-environment-hold", names)

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
        self.assertEqual(out[0].repo, "agent-bureau")
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
        out = rd.witness_from_comments("agent-bureau", "DRE-3409", comments)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].kind, rd.COULD_NOT_RUN)
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

    def test_a_verdict_does_not_reset_the_repo_spread_rule(self):
        """Two repositories is its own trigger: one crash each, a verdict in
        between, and the reviewer is still down in two places at once."""
        local = [
            _cnr("bureau-pipeline", "2026-09-08T22:20:00Z", "a"),
            _verdict("bureau-pipeline", "2026-09-08T22:23:00Z", "v"),
        ]
        witness = [_cnr("agent-bureau", "2026-09-08T22:26:00Z", "b")]
        d = rd.decide(local, witness, None, NOW, _threshold())
        self.assertEqual(d.action, rd.FILE)
        self.assertEqual((d.runs, d.repos), (2, 2))

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
        self.card = rd.OpenCard(identifier="DRE-3500",
                                filed_at="2026-09-08T22:27:00Z",
                                text="Reviewer down since 15:19 PT")

    def test_a_verdict_after_the_card_was_filed_closes_it(self):
        later = _verdict("bureau-pipeline", "2026-09-08T22:32:00Z",
                         "https://example.invalid/v")
        d = rd.decide([later], [], self.card, NOW, _threshold())
        self.assertEqual(d.action, rd.CLOSE)
        self.assertEqual(
            d.resolve_note,
            "reviewer back at 15:32 PT — first successful verdict after this "
            "card was filed (https://example.invalid/v)",
        )

    def test_a_verdict_before_the_card_was_filed_is_not_a_close(self):
        earlier = _verdict("bureau-pipeline", "2026-09-08T22:25:00Z", "v")
        d = rd.decide([earlier], [], self.card, NOW, _threshold())
        self.assertNotEqual(d.action, rd.CLOSE)


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
