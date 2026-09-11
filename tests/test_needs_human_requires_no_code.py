"""A card filed `needs-human` carries `no-code` with it, or it is refused (DRE-3512).

`standards/card-quality.md` pairs the two labels everywhere it names a card a
person finishes by hand — the OPERATOR verdict is `hand-built` + `no-code`, an
observation follow-up carries "`needs-human` and `no-code`" — and
`briefs/planner.md` says the same. Nothing enforced it. On 2026-09-09 the
console read `needs-human` + `agent:devops` on DRE-3356 and DRE-3357, two
ordinary build cards (a "What to build" section, a size, code to write), offered
the CEO a one-click hand-completion on each, and he took it: two unbuilt cards
marked Done. The console half is DRE-3510; this is the SOURCE half — the label
pair a planner-filed card is allowed to carry.

The rule is `needs-human` REQUIRES `no-code`: a card claiming a human finishes
it by hand may not also describe a diff. It is judged at the one seam where the
label can only mean what the planner meant by it — the create seam
(`linear_ops._reject_unless_creatable`, shared by `subissue` and `oneoff`),
BEFORE the card reaches the board. It is deliberately NOT judged by
`validate_card.missing` (the Todo gate) or `child_problems` (the post-plan
sweep): on the board the same word is the pipeline's own hold label
(`dead_run.HOLD_LABEL`, stamped by the stranded watchdog, the turn-cap park and
the fix loop on cards that describe a diff by design), and refusing that would
bounce a held build card to Planning or fail a re-plan over the pipeline's own
label. `plan_seam.py` states the two meanings; this test pins the placement.

The fixture is the exact label shape the console read on the two cards, so the
card that caused this is the regression case.
"""

from __future__ import annotations

import io
import os
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dead_run  # noqa: E402
import linear_ops  # noqa: E402
import validate_card  # noqa: E402

from test_subissue_valid_children import FakeLinear  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: What DRE-3356 / DRE-3357 carried when the console offered hand-completion
#: on 2026-09-09: a pipeline build card's labels plus the lone hold label.
DRE_3356_LABELS = ["repo:bureau-pipeline", "initiative:bureau", "agent:devops",
                   "size:S", "needs-human"]

BODY = ("# Card\n\n**What to build**\n\nA workflow that regenerates the ledger."
        "\n\n## Acceptance criteria\n- [ ] the workflow exists")


class TestTheLabelsAreTheOnesTheRestOfThePipelineWrites:
    def test_needs_human_is_the_hold_label_the_sweeps_stamp(self):
        # ONE word for the label, read from the module that owns it — a rename
        # of the hold label must move this rule with it or the two drift.
        assert validate_card.NEEDS_HUMAN_LABEL == dead_run.HOLD_LABEL == "needs-human"

    def test_no_code_is_the_operator_label_linear_ops_reads(self):
        # `linear_ops` imports this module lazily, so the literal lives here and
        # this assertion is the lockstep (the same shape as the relay's regex).
        assert validate_card.NO_CODE_LABEL == linear_ops.NO_CODE_LABEL == "no-code"


class TestNeedsHumanWithoutNoCode:
    def test_the_dre_3356_shape_is_refused_with_both_labels_named(self):
        problem = validate_card.needs_human_without_no_code(DRE_3356_LABELS)
        assert problem is not None
        assert "needs-human" in problem
        assert "no-code" in problem
        # The rule, so the planner's next attempt fixes itself, and where it
        # is written, so a reader can check the message against the standard.
        assert "requires" in problem.lower()
        assert "standards/card-quality.md" in problem

    def test_both_labels_pass_unchanged(self):
        assert validate_card.needs_human_without_no_code(
            ["repo:bureau-pipeline", "agent:devops", "needs-human", "no-code"]
        ) is None

    def test_neither_label_passes_unchanged(self):
        assert validate_card.needs_human_without_no_code(
            ["repo:bureau-pipeline", "initiative:bureau", "agent:devops", "size:S"]
        ) is None

    def test_no_code_alone_is_ordinary(self):
        # `no-code` rides on every standards card; only the other half is the
        # claim that needs its pair.
        assert validate_card.needs_human_without_no_code(
            ["repo:agent-bureau", "agent:ops", "no-code"]
        ) is None

    def test_case_and_spacing_do_not_hide_the_pair(self):
        assert validate_card.needs_human_without_no_code(
            ["Needs-Human", " NO-CODE "]
        ) is None
        assert validate_card.needs_human_without_no_code(["NEEDS-HUMAN"]) is not None

    def test_empty_labels_are_not_a_problem(self):
        assert validate_card.needs_human_without_no_code([]) is None
        assert validate_card.needs_human_without_no_code(None) is None


class TestTheCreateSeamRefusesTheLoneLabel:
    def test_subissue_is_rejected_and_nothing_is_created(self, tmp_path):
        fake = FakeLinear(
            parent_labels=["repo:bureau-pipeline", "initiative:bureau", "agent:planner"]
        )
        f = tmp_path / "card.md"
        f.write_text(BODY)
        with patch.object(linear_ops, "gql", side_effect=fake.gql):
            with pytest.raises(linear_ops.LinearError) as exc:
                with redirect_stdout(io.StringIO()):
                    linear_ops.cmd_subissue(
                        "DRE-EPIC", "The split ledger regenerates itself daily",
                        str(f), "--label", "size:S", "--label", "needs-human",
                    )
        message = str(exc.value)
        assert "needs-human" in message
        assert "no-code" in message
        assert fake.created is None

    def test_the_same_child_is_created_once_it_carries_the_pair(self, tmp_path):
        fake = FakeLinear(
            parent_labels=["repo:bureau-pipeline", "initiative:bureau", "agent:planner"]
        )
        f = tmp_path / "card.md"
        f.write_text(BODY)
        with patch.object(linear_ops, "gql", side_effect=fake.gql):
            with redirect_stdout(io.StringIO()):
                linear_ops.cmd_subissue(
                    "DRE-EPIC", "SIGN-OFF (OPERATOR): add the App to main's bypass list",
                    str(f), "--label", "needs-human", "--label", "no-code",
                )
        assert fake.created is not None
        assert {"needs-human", "no-code"} <= set(fake.label_create_names)

    def test_a_child_with_neither_label_is_unaffected(self, tmp_path):
        fake = FakeLinear(
            parent_labels=["repo:bureau-pipeline", "initiative:bureau", "agent:planner"]
        )
        f = tmp_path / "card.md"
        f.write_text(BODY)
        with patch.object(linear_ops, "gql", side_effect=fake.gql):
            with redirect_stdout(io.StringIO()):
                linear_ops.cmd_subissue(
                    "DRE-EPIC", "The split ledger regenerates itself daily", str(f),
                    "--label", "size:S",
                )
        assert fake.created is not None

    def test_oneoff_is_held_to_the_same_rule(self, tmp_path):
        fake = FakeLinear()
        f = tmp_path / "card.md"
        f.write_text(BODY)
        with patch.object(linear_ops, "gql", side_effect=fake.gql):
            with pytest.raises(linear_ops.LinearError) as exc:
                with redirect_stdout(io.StringIO()):
                    linear_ops.cmd_oneoff(
                        "Rotate the relay's key", str(f),
                        "--label", "repo:agent-bureau", "--label", "agent:devops",
                        "--label", "needs-human",
                    )
        assert "no-code" in str(exc.value)
        assert fake.created is None


class TestWhereTheRuleDoesNotLive:
    def test_the_todo_gate_does_not_bounce_a_held_build_card(self):
        # A build card the pipeline parked (`needs-human` = the hold label) is
        # a valid card at the Todo gate — the sweep's `held()` keeps it out of
        # dispatch, and bouncing it to Planning would strand it twice.
        assert validate_card.missing(BODY, DRE_3356_LABELS) == []

    def test_the_post_plan_sweep_does_not_fail_a_plan_over_a_hold(self):
        # `check-children` re-runs over EVERY child of an epic, including one
        # already parked by the turn cap; the pipeline's own label on such a
        # child is not a planner mistake, so the sweep does not report it.
        assert validate_card.child_problems(
            "The split ledger regenerates itself daily", BODY, DRE_3356_LABELS
        ) == []


class TestTheBriefAndTheStandardSayThePair:
    def test_every_needs_human_labelling_instruction_in_the_brief_names_no_code(self):
        # The brief IS the planner's filing path. Every place it tells the
        # planner to LABEL a card `needs-human` must name `no-code` in the same
        # sentence, or the gate above refuses what the brief instructed.
        # A sentence, not a line: the brief wraps at 80 columns, so the pair
        # can straddle a line break. Backticked labels carry no full stop.
        text = (ROOT / "briefs" / "planner.md").read_text()
        instructions = [
            " ".join(m.group(0).split()) for m in re.finditer(
                r"[^.]*`needs-human`\s*\+[^.]*", text)
        ]
        assert instructions, "the brief no longer tells the planner how to label operator work"
        for sentence in instructions:
            assert "`no-code`" in sentence, sentence

    def test_the_standard_states_the_pair_where_the_labels_are_listed(self):
        text = (ROOT / "standards" / "card-quality.md").read_text()
        labels_bullet = re.search(r"- \*\*Labels:\*\*[^\n]*(?:\n  [^\n]*)*", text)
        assert labels_bullet is not None
        assert "`needs-human`" in labels_bullet.group(0)
        assert "`no-code`" in labels_bullet.group(0)
