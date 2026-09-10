"""Every Red-Main Repair PR files its card first and names it (DRE-3533).

A repair PR rides `repair/<failing-sha>`. That ref carries no card id, and the
card id in the head ref is what the rest of the pipeline reads — `linear-sync`
closes a card on merge by finding `DRE-<n>` there, and the console shows a pull
request's card the same way. So a repair PR showed no card, closed no card, and
nothing on the board recorded that the repair happened (PR #340, 2026-09-09).

What these tests pin, one class per acceptance criterion:

  * the loop files the card BEFORE the agent runs, through the existing
    `linear_ops` create seam, in `In Progress` (never Todo — a card entering
    Todo is dispatched by the relay, and this work is already being done), with
    the routing verdict stamped through `routing_verdict.stamp_card`, the ONE
    writer of that vocabulary;
  * the branch is `repair/DRE-<n>-<sha12>` and every `repair/*` matcher in the
    fleet still matches it — executed, not asserted from memory;
  * the attempt record, the debounce and the budget still see a repair at that
    commit whichever shape its branch has;
  * the PR body carries the card link;
  * a Linear failure NEVER blocks a repair: the loop falls back to
    `repair/<sha>` and says the card is owed.

Run: cd bureau-pipeline && python3 -m pytest tests/test_repair_card.py -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
WF_DIR = os.path.join(ROOT, ".github", "workflows")
sys.path.insert(0, SCRIPTS)
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import red_main_repair  # noqa: E402
import repair_card  # noqa: E402
import repair_context  # noqa: E402
import routing_verdict  # noqa: E402
import should_review_pr  # noqa: E402
import validate_card  # noqa: E402

SHA = "44891f372381773127f8d6dc1a82205f6565517b"
SHA12 = SHA[:12]
CARD = "DRE-3533"
RUN_URL = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34417968508"
WF_NAME = "Integration harness"
SLUG = "bureau-pipeline"


def wf(name: str) -> str:
    return open(os.path.join(WF_DIR, name)).read()


class FakeOps:
    """The Linear seam, recorded. Every method mirrors the real signature."""

    def __init__(self, *, open_card=None, fail_create=False, fail_stamp=False):
        self.open_card = open_card
        self.fail_create = fail_create
        self.fail_stamp = fail_stamp
        self.created: list[dict] = []
        self.comments: list[tuple] = []
        self.stamped: list[tuple] = []
        self.searched: list[str] = []

    # linear_ops
    def find_open(self, title):
        self.searched.append(title)
        return self.open_card

    def create_card(self, title, description, *, repo_slug, labels=(), lane="Planning"):
        if self.fail_create:
            raise RuntimeError("Linear says 429: rate limited")
        self.created.append({"title": title, "description": description,
                             "repo_slug": repo_slug, "labels": list(labels),
                             "lane": lane})
        return {"identifier": CARD, "url": f"https://linear.app/x/issue/{CARD}"}

    def cmd_comment(self, identifier, body, *flags):
        self.comments.append((identifier, body))

    # routing_verdict
    def stamp_card(self, identifier, name, why):
        if self.fail_stamp:
            raise RuntimeError("Linear says 500")
        self.stamped.append((identifier, name, why))
        return 0


class CardShapeTest(unittest.TestCase):
    """What the card says, and that the live gate accepts it."""

    def test_title_is_anchored_on_the_workflow_and_the_failing_commit(self):
        # NOT the run id: attempt 2 is a DIFFERENT failed run at the SAME
        # commit, and it must find this card rather than mint a second one.
        title = repair_card.card_title(WF_NAME, SHA)
        self.assertIn(SHA12, title)
        self.assertIn(WF_NAME, title)
        self.assertEqual(title, repair_card.card_title(WF_NAME, SHA))
        self.assertNotIn("34417968508", title)

    def test_labels_carry_the_repo_the_role_and_the_marks(self):
        labels = repair_card.card_labels(SLUG)
        for want in (f"repo:{SLUG}", "initiative:bureau", "Bug", "hand-built"):
            self.assertIn(want, labels)
        self.assertTrue(any(l.startswith("agent:") for l in labels),
                        f"a card needs a role label: {labels}")

    def test_the_card_passes_the_live_card_gate(self):
        body = repair_card.card_body(workflow_name=WF_NAME, run_url=RUN_URL,
                                     head_sha=SHA, repo_slug=SLUG, attempt=1)
        labels = repair_card.card_labels(SLUG)
        self.assertEqual([], validate_card.missing(body, labels))
        self.assertIsNone(validate_card.repo_title_mismatch(
            repair_card.card_title(WF_NAME, SHA), labels))

    def test_the_body_names_the_run_the_commit_and_what_happens_next(self):
        body = repair_card.card_body(workflow_name=WF_NAME, run_url=RUN_URL,
                                     head_sha=SHA, repo_slug=SLUG, attempt=1)
        self.assertIn(RUN_URL, body)
        self.assertIn(SHA, body)
        self.assertIn(WF_NAME, body)


class BranchNamesTheCardTest(unittest.TestCase):
    """`repair/DRE-<n>-<sha12>` — and every repair matcher still matches it."""

    def test_branch_carries_the_card_and_the_short_sha(self):
        self.assertEqual(f"repair/{CARD}-{SHA12}",
                         red_main_repair.repair_branch(SHA, 1, card=CARD))

    def test_second_attempt_is_suffixed(self):
        self.assertEqual(f"repair/{CARD}-{SHA12}-2",
                         red_main_repair.repair_branch(SHA, 2, card=CARD))

    def test_without_a_card_the_old_shape_survives(self):
        # The fallback when Linear is unreachable — and every existing repair
        # branch in every repo already has this shape.
        self.assertEqual(f"repair/{SHA}", red_main_repair.repair_branch(SHA, 1))
        self.assertEqual(f"repair/{SHA}-2", red_main_repair.repair_branch(SHA, 2))

    def test_the_fleets_repair_matchers_all_match_the_new_branch(self):
        import reconcile

        branch = red_main_repair.repair_branch(SHA, 1, card=CARD)
        self.assertTrue(should_review_pr.should_review(branch))
        self.assertTrue(reconcile.fix_eligible(branch))
        self.assertTrue(reconcile.pipeline_owns(branch))
        self.assertEqual(SHA12, repair_context.failing_sha(branch))
        self.assertEqual(SHA12, repair_context.failing_sha(branch + "-2"))
        self.assertIn("REPAIR PR:", repair_context.build_context(branch, [], ""))

    def test_the_shell_case_matchers_accept_it(self):
        """merge-gate.yml and agent-fix.yml gate on a shell `case` — run it."""
        branch = red_main_repair.repair_branch(SHA, 1, card=CARD)
        for pattern in ("agent/*|repair/*|dependabot/*|bot/standards-sync",
                        "agent/*|repair/*"):
            script = (f'B="{branch}"; case "$B" in {pattern}) echo yes;; '
                      '*) echo no;; esac')
            out = subprocess.run(["bash", "-c", script], capture_output=True,
                                 text=True, check=True).stdout.strip()
            self.assertEqual("yes", out, f"{pattern} rejected {branch}")

    def test_both_patterns_are_the_ones_the_workflows_use(self):
        # The strings above are only proof if the workflows still spell them.
        self.assertIn("agent/*|repair/*|dependabot/*|bot/standards-sync",
                      wf("merge-gate.yml"))
        self.assertIn("agent/*|repair/*", wf("agent-fix.yml"))


class AttemptRecordTest(unittest.TestCase):
    """The debounce and the 2-attempt budget read branches. Both shapes count."""

    def _decide(self, refs, pulls):
        return red_main_repair.decide(
            conclusion="failure", head_branch="main", default_branch="main",
            head_sha=SHA, log_text="assert 4 == 3", refs=refs, pulls=pulls,
        )

    def test_a_card_named_branch_debounces_a_duplicate_event(self):
        d = self._decide([f"repair/{CARD}-{SHA12}"], [])
        self.assertFalse(d["go"])
        self.assertEqual("duplicate-event", d["reason"])

    def test_a_card_named_attempt_1_earns_attempt_2(self):
        d = self._decide(
            [f"repair/{CARD}-{SHA12}"],
            [{"head_ref": f"repair/{CARD}-{SHA12}", "state": "closed",
              "merged": False}],
        )
        self.assertTrue(d["go"])
        self.assertEqual(2, d["attempt"])

    def test_two_card_named_attempts_exhaust_the_budget(self):
        d = self._decide(
            [f"repair/{CARD}-{SHA12}", f"repair/{CARD}-{SHA12}-2"],
            [{"head_ref": f"repair/{CARD}-{SHA12}", "state": "closed", "merged": False},
             {"head_ref": f"repair/{CARD}-{SHA12}-2", "state": "closed", "merged": False}],
        )
        self.assertFalse(d["go"])
        self.assertTrue(d["escalate"])

    def test_a_merged_card_named_repair_makes_the_old_event_inert(self):
        d = self._decide(
            [f"repair/{CARD}-{SHA12}"],
            [{"head_ref": f"repair/{CARD}-{SHA12}", "state": "closed", "merged": True}],
        )
        self.assertFalse(d["go"])
        self.assertEqual("already-repaired", d["reason"])

    def test_another_shas_card_branch_does_not_consume_this_budget(self):
        other = "b" * 40
        d = self._decide([f"repair/DRE-1-{other[:12]}"], [])
        self.assertTrue(d["go"])
        self.assertEqual(1, d["attempt"])


class OpenCardTest(unittest.TestCase):
    """The step that runs between the decision and the agent."""

    def _open(self, ops, attempt=1):
        return repair_card.open_card(
            repo_slug=SLUG, workflow_name=WF_NAME, head_sha=SHA,
            run_url=RUN_URL, attempt=attempt,
            fallback_branch=red_main_repair.repair_branch(SHA, attempt),
            ops=ops,
        )

    def test_files_the_card_in_progress_with_its_labels(self):
        ops = FakeOps()
        result = self._open(ops)
        self.assertEqual(1, len(ops.created))
        made = ops.created[0]
        self.assertEqual("In Progress", made["lane"])
        self.assertEqual(SLUG, made["repo_slug"])
        for want in ("initiative:bureau", "Bug", "hand-built"):
            self.assertIn(want, made["labels"])
        self.assertEqual(CARD, result["card"])
        self.assertFalse(result["card_owed"])

    def test_never_todo(self):
        # A card entering Todo is dispatched by the relay — a SECOND agent on
        # work an agent is already doing.
        ops = FakeOps()
        self._open(ops)
        self.assertNotEqual("Todo", ops.created[0]["lane"])

    def test_stamps_the_routing_verdict_through_the_one_writer(self):
        ops = FakeOps()
        self._open(ops)
        self.assertEqual(1, len(ops.stamped))
        ident, name, why = ops.stamped[0]
        self.assertEqual(CARD, ident)
        self.assertIn(name, routing_verdict.verdicts())
        self.assertEqual("WORKBENCH", name)
        self.assertIn(RUN_URL, why)
        # The comment that stamp writes must read back as this verdict.
        self.assertEqual(
            name, routing_verdict.verdict_on([routing_verdict.verdict_comment(name, why)])
        )

    def test_the_branch_names_the_card_it_just_filed(self):
        ops = FakeOps()
        self.assertEqual(f"repair/{CARD}-{SHA12}", self._open(ops)["branch"])

    def test_a_second_attempt_reuses_the_open_card(self):
        ops = FakeOps(open_card=CARD)
        result = self._open(ops, attempt=2)
        self.assertEqual([], ops.created, "attempt 2 must not mint a second card")
        self.assertEqual(CARD, result["card"])
        self.assertEqual(f"repair/{CARD}-{SHA12}-2", result["branch"])
        self.assertTrue(ops.comments, "the reused card must record the new attempt")
        self.assertIn(RUN_URL, ops.comments[0][1])

    def test_a_reused_card_is_not_re_stamped(self):
        ops = FakeOps(open_card=CARD)
        self._open(ops, attempt=2)
        self.assertEqual([], ops.stamped)

    def test_a_linear_failure_never_blocks_the_repair(self):
        ops = FakeOps(fail_create=True)
        result = self._open(ops)
        self.assertEqual("", result["card"])
        self.assertTrue(result["card_owed"])
        self.assertEqual(f"repair/{SHA}", result["branch"])
        self.assertTrue(result["note"], "the reason must travel with the refusal")

    def test_a_stamp_failure_still_keeps_the_card(self):
        # The card exists; only the verdict comment failed. Using the fallback
        # branch here would open a PR whose card nothing can close.
        ops = FakeOps(fail_stamp=True)
        result = self._open(ops)
        self.assertEqual(CARD, result["card"])
        self.assertEqual(f"repair/{CARD}-{SHA12}", result["branch"])

    def test_outputs_are_github_output_lines(self):
        ops = FakeOps()
        lines = repair_card.outputs(self._open(ops)).splitlines()
        self.assertIn(f"card={CARD}", lines)
        self.assertIn(f"branch=repair/{CARD}-{SHA12}", lines)
        self.assertIn("card_owed=false", lines)
        self.assertTrue(any(l.startswith("card_url=") for l in lines))

    def test_outputs_never_break_the_github_output_format(self):
        # A multi-line value would let the note leak into another key.
        ops = FakeOps(fail_create=True)
        for line in repair_card.outputs(self._open(ops)).splitlines():
            self.assertIn("=", line)
            self.assertNotIn("\n", line)


class WorkflowWiringTest(unittest.TestCase):
    """red-main-repair.yml: card first, then the agent, on the card's branch."""

    def setUp(self):
        self.body = wf("red-main-repair.yml")

    def test_the_card_step_runs_before_the_agent(self):
        self.assertIn("repair_card.py", self.body)
        self.assertLess(self.body.index("repair_card.py"),
                        self.body.index("anthropics/claude-code-action"),
                        "the card is filed BEFORE the agent starts")

    def test_the_card_step_is_gated_on_the_dispatch_decision(self):
        step = self.body[self.body.index("repair_card.py") - 900:
                         self.body.index("repair_card.py")]
        self.assertIn("steps.decide.outputs.go == 'true'", step)

    def test_the_agent_and_the_report_use_the_card_named_branch(self):
        # The decide step's branch is the FALLBACK; the branch the agent
        # creates and the branch the Report step looks for must be the same
        # one, or the run reports a PR that was never opened on it.
        self.assertIn(
            "steps.card.outputs.branch || steps.decide.outputs.branch", self.body
        )
        self.assertEqual(
            2, self.body.count("steps.card.outputs.branch || steps.decide.outputs.branch"),
            "both the agent prompt and the Report step read the card's branch",
        )

    def test_the_agent_is_told_to_link_the_card_from_the_pr_body(self):
        self.assertIn("steps.card.outputs.card_url", self.body)
        self.assertIn("card is owed", self.body)

    def test_a_failed_card_never_fails_the_repair_job(self):
        step = self.body[self.body.index("repair_card.py") - 900:
                         self.body.index("repair_card.py")]
        self.assertIn("continue-on-error: true", step)

    def test_the_workflow_no_longer_claims_repair_prs_are_cardless(self):
        self.assertNotIn("A repair PR is cardless by design", self.body)


class CriticStillReadsTheFailingLogTest(unittest.TestCase):
    """qa-review.yml fetches the ORIGINAL failing run by sha. A 12-character
    sha is not a head_sha the runs API answers (it returns 0), so the short
    sha must be resolved to the full one before that query."""

    def setUp(self):
        self.body = wf("qa-review.yml")

    def test_the_extraction_accepts_a_card_named_repair_branch(self):
        line = [ln for ln in self.body.splitlines()
                if "SHA=$(" in ln and "$BRANCH" in ln]
        self.assertEqual(1, len(line), "one sha extraction in qa-review.yml")
        script = (f'BRANCH="repair/{CARD}-{SHA12}-2"; {line[0].strip()}; '
                  'printf "%s" "$SHA"')
        out = subprocess.run(["bash", "-c", script], capture_output=True,
                             text=True, check=True).stdout
        self.assertEqual(SHA12, out)

    def test_the_extraction_still_accepts_the_old_shape(self):
        line = [ln for ln in self.body.splitlines()
                if "SHA=$(" in ln and "$BRANCH" in ln][0]
        script = (f'BRANCH="repair/{SHA}"; {line.strip()}; printf "%s" "$SHA"')
        out = subprocess.run(["bash", "-c", script], capture_output=True,
                             text=True, check=True).stdout
        self.assertEqual(SHA, out)

    def test_a_short_sha_is_resolved_before_the_runs_query(self):
        self.assertIn("/commits/", self.body)
        self.assertLess(self.body.index("/commits/"),
                        self.body.index("actions/runs?head_sha="),
                        "resolve the abbreviated sha, THEN ask for its runs")


if __name__ == "__main__":
    unittest.main()
