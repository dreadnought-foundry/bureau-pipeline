"""An Agent Fix run names its pull request in its run-name (DRE-4845).

Live on Portico, 2026-09-24 ~21:50 PT: four approved, CLEAN, all-green pull
requests (#697, #702, #703, #708) sat unmerged for over an hour across 29
Merge Gate runs. Run 36095010658 on #708 said why:

    decision=wait / reason=1 Agent Fix run(s) are in flight that GitHub
    attributes to no pull request (36094324746) ...

Condition F (DRE-4486) is right to wait on a fix run for THIS pull request,
but it could not attribute most of them: a `workflow_dispatch` run's title is
the bare "Agent Fix", an `issue_comment` run hangs off `main`, and a pending
run has no resolved job name yet. So one unattributed fix held every PR.

A stub `run-name:` is the one attribution GitHub resolves when the run is
CREATED, for both triggers the stubs declare. These tests pin the two halves
together: the parser (`fix_concurrency.pr_of_run_name`) and the producer
(bureau-pipeline's own stub, `self-agent-fix.yml`), rendered through the same
expression evaluator the concurrency audit uses.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fix_concurrency as fc  # noqa: E402

STUB = ROOT / ".github" / "workflows" / "self-agent-fix.yml"


class PrOfRunName(unittest.TestCase):
    def test_the_run_name_reads_back_as_its_number(self):
        self.assertEqual(fc.pr_of_run_name("Agent Fix #713"), 713)

    def test_the_prefix_is_the_one_constant(self):
        self.assertEqual(fc.pr_of_run_name(f"{fc.RUN_NAME_PREFIX}708"), 708)

    def test_a_title_that_names_no_pull_request_is_unattributed(self):
        for title in (
            "Agent Fix",       # a dispatch run on a stub without the run-name
            "Agent Fix #",     # the expression resolved to nothing
            # a PR title (an issue_comment run's display_title on a stub
            # without the run-name) that merely CONTAINS the shape
            "feat(DRE-12): stop Agent Fix #12 holding every PR",
            "Agent Fix #12 and more",
            "Re: Agent Fix #12",
            "Agent Fix #12a",
            "",
            None,
            713,
        ):
            with self.subTest(title=title):
                self.assertIsNone(fc.pr_of_run_name(title))


def _stub() -> dict:
    return yaml.safe_load(STUB.read_text())


def _triggers(doc: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True (YAML 1.1).
    return doc.get("on", doc.get(True)) or {}


class TheSelfHostStubNamesItsPullRequest(unittest.TestCase):
    """The producer. `run-name:` is read off the TOP-LEVEL workflow, so it
    lives in the stub, never in the `workflow_call` reusable."""

    def test_the_stub_declares_exactly_the_two_triggers_the_expression_covers(self):
        self.assertEqual(set(_triggers(_stub())),
                         {"issue_comment", "workflow_dispatch"})

    def test_the_stub_carries_a_run_name(self):
        self.assertIn("run-name", _stub())

    def test_both_triggers_render_a_run_name_that_parses_back_to_the_pr(self):
        template = str(_stub()["run-name"])
        for number in (713, 7):
            for label, event in (
                ("issue_comment", fc.comment_event(
                    number, fc.QA_BOT_LOGIN, "VERDICT: REQUEST_CHANGES")),
                ("workflow_dispatch", fc.dispatch_event(number)),
            ):
                with self.subTest(trigger=label, pr=number):
                    rendered = fc.interpolate(template, event)
                    self.assertEqual(fc.pr_of_run_name(rendered), number,
                                     rendered)

    def test_the_run_name_is_quoted_so_the_number_survives_yaml(self):
        """Unquoted, YAML reads ` #` as the start of a comment and the
        number silently disappears — the hazard agent-fix.yml's job name
        already documents. Parsed, the value must still hold the whole
        expression."""
        self.assertIn("${{", str(_stub()["run-name"]))
        line = next(line for line in STUB.read_text().splitlines()
                    if line.startswith("run-name:"))
        value = line.split(":", 1)[1].strip()
        self.assertTrue(value[:1] in ("'", '"') and value[-1:] == value[:1],
                        line)

    def test_the_pr_half_is_the_concurrency_groups_pr_half(self):
        """One expression for "which PR is this run", in two places — the
        group and the name — so they cannot drift apart."""
        pr_half = "${{ github.event.issue.number || inputs.pr_number }}"
        self.assertIn(pr_half, fc.group_of(_stub()))
        self.assertEqual(str(_stub()["run-name"]),
                         f"{fc.RUN_NAME_PREFIX}{pr_half}")


if __name__ == "__main__":
    unittest.main()
