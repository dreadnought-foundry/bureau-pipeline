"""A merge into a NON-DEFAULT base does not move its card to Done (DRE-4647).

Observed 2026-09-21 17:46 PT. The merge gate merged agent-bureau #2691 and
#2692 into `agent/DRE-4534-record-reads` — their parent's branch, not `main` —
and `linear-sync` moved DRE-4535 and DRE-4536 to Done. Their code was on no
default branch: the parent (#2690) and its own parent (#2688) were still open
drafts and did not land until 2026-09-22 09:57 PT. A card is Done only when
its code is on the default branch.

The hole is narrow and was always there. `card-done` keys on
`github.event.pull_request.merged == true` and the HEAD ref alone — it never
reads the base — so "merged" and "landed" were the same word. Stacked pull
requests are the case where they are not.

What this file pins:

1. **The gate** — with a base that is not the repository's default branch the
   executed step calls no `card-done`, runs no sweep, and posts ONE plain
   comment on the card instead, opening with the literal `🪜 Merged into` and
   naming both branches and the pull request. The state is left alone: the
   follow-on that closes these cards when the parent lands is a sibling card,
   and it finds them by that receipt.
2. **The unchanged path** — with `base.ref` equal to the default branch the
   behaviour is today's, card id and URL intact, merge-sweep gate still run.
   A gate that also stopped ordinary merges would close nothing at all.
3. **The untrusted-text rule** — both values reach the shell through `env:`,
   never `${{ }}` inside `run:`, which is the rule the step already follows
   for `PR_BODY`.
4. **The conflict sweep** — gated the same way. A merge into a side branch is
   not "the exact moment sibling PRs go DIRTY", because nothing this merge
   changed is on the default branch.

Live-extraction style (pattern: tests/test_linear_sync_done_gate.py and
tests/test_merge_sweep_gate.py): the scenario tests EXECUTE the shipped `run:`
script of the `Card → Done` step against stub scripts that record their argv,
so what is counted is what the workflow would really call — nothing is
re-implemented here.

Run: python3 -m pytest tests/test_linear_sync_stacked_base.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"
LINEAR_SYNC = WORKFLOWS / "linear-sync.yml"

#: The step under repair, by its own name in the workflow.
DONE_STEP = "Card → Done"

#: The conflict sweep's job key — gated on the same question (DRE-4647).
CONFLICT_JOB = "conflict-sweep"

#: The contract with the sibling card that closes these cards when the parent
#: lands: the comment OPENS with this, so the landing card can find the
#: receipt, and the base branch is named in backticks.
RECEIPT_OPENER = "🪜 Merged into"

#: The 2026-09-21 case, verbatim.
INCIDENT_HEAD = "agent/DRE-4535-runner-day-from-record"
INCIDENT_BASE = "agent/DRE-4534-record-reads"
INCIDENT_CARD = "DRE-4535"
INCIDENT_PR = "https://github.com/dreadnought-foundry/agent-bureau/pull/2691"

#: A stand-in for every pipeline script the step can call. It records its own
#: name and argv and answers nothing, so a call is visible and inert.
RECORDING_STUB = (
    "import json, os, sys\n"
    "with open(os.environ['CALL_LOG'], 'a', encoding='utf-8') as fh:\n"
    "    fh.write(json.dumps({'script': os.path.basename(sys.argv[0]),\n"
    "                         'args': sys.argv[1:]}) + '\\n')\n"
)

#: The merge-sweep gate, stubbed to emit one flag — so a sweep that runs is
#: visible in the log as a `reconcile.py` call, and a sweep that does not run
#: is visible as its absence.
SWEEP_GATE_STUB = RECORDING_STUB + "print('--promote-only')\n"


def done_step() -> dict:
    for job in yaml.safe_load(LINEAR_SYNC.read_text())["jobs"].values():
        for step in job.get("steps") or []:
            if step.get("name") == DONE_STEP:
                return step
    raise AssertionError(f"{DONE_STEP!r} is gone from linear-sync.yml")


def conflict_job() -> dict:
    jobs = yaml.safe_load(LINEAR_SYNC.read_text())["jobs"]
    assert CONFLICT_JOB in jobs, f"{CONFLICT_JOB!r} is gone from linear-sync.yml"
    return jobs[CONFLICT_JOB]


class MergeScenario:
    """One execution of the shipped `Card → Done` script."""

    def __init__(self, td: str):
        self.dir = Path(td)
        self.scripts = self.dir / ".bureau-pipeline" / "scripts"
        self.scripts.mkdir(parents=True)
        for name in ("linear_ops.py", "dependabot_card.py", "reconcile.py"):
            (self.scripts / name).write_text(RECORDING_STUB, encoding="utf-8")
        (self.scripts / "merge_sweep_gate.py").write_text(
            SWEEP_GATE_STUB, encoding="utf-8"
        )
        self.log = self.dir / "calls.jsonl"

    def run(self, *, head_ref: str, base_ref: str, default_branch: str,
            pr_url: str = INCIDENT_PR) -> subprocess.CompletedProcess:
        script = self.dir / "card-done.sh"
        script.write_text(done_step()["run"], encoding="utf-8")
        # `bash -e`, the runner's own flags for a `run:` block.
        self.proc = subprocess.run(  # nosec B603 — fixed argv, test-local script
            ["bash", "-e", str(script)],
            cwd=self.dir, capture_output=True, text=True,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(self.dir),
                "GITHUB_REPOSITORY": "dreadnought-foundry/agent-bureau",
                "LINEAR_API_KEY": "test-key",
                "HEAD_REF": head_ref,
                "PR_URL": pr_url,
                "PR_BODY": "",
                "PR_AUTHOR": "agent-bureau-bot",
                "MAX_WIP": "8",
                "BASE_REF": base_ref,
                "DEFAULT_BRANCH": default_branch,
                "CALL_LOG": str(self.log),
            },
        )
        return self.proc

    @property
    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(ln) for ln in self.log.read_text().splitlines() if ln]

    def calls_to(self, script: str, subcommand: str | None = None) -> list[dict]:
        return [
            c for c in self.calls
            if c["script"] == script
            and (subcommand is None or c["args"][:1] == [subcommand])
        ]


class AStackedMergeClosesNothing(unittest.TestCase):
    """The card: merged into a side branch is not landed on the default one."""

    def _stacked(self, td):
        scenario = MergeScenario(td)
        proc = scenario.run(
            head_ref=INCIDENT_HEAD, base_ref=INCIDENT_BASE, default_branch="main"
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        return scenario, proc

    def test_no_card_done_is_called(self):
        """THE false Done: DRE-4535 went Done with its code on no default
        branch, and the cloud era has no auto-revert."""
        with tempfile.TemporaryDirectory() as td:
            scenario, _ = self._stacked(td)
            self.assertEqual([], scenario.calls_to("linear_ops.py", "card-done"))

    def test_the_2026_09_21_case_does_not_move_dre_4535(self):
        """The incident replayed: nothing this step calls names DRE-4535 as a
        card to transition."""
        with tempfile.TemporaryDirectory() as td:
            scenario, _ = self._stacked(td)
            transitions = [
                c for c in scenario.calls
                if c["script"] == "linear_ops.py" and c["args"][:1] != ["comment"]
            ]
            self.assertEqual([], transitions, scenario.calls)
            self.assertNotIn(
                INCIDENT_CARD,
                " ".join(a for c in scenario.calls_to("reconcile.py")
                         for a in c["args"]),
            )

    def test_no_sweep_runs(self):
        """Nothing this merge changed is on the default branch, so there is
        nothing to promote and no epic to close."""
        with tempfile.TemporaryDirectory() as td:
            scenario, _ = self._stacked(td)
            self.assertEqual([], scenario.calls_to("reconcile.py"))
            self.assertEqual([], scenario.calls_to("merge_sweep_gate.py"))

    def test_exactly_one_comment_is_posted_on_the_card(self):
        with tempfile.TemporaryDirectory() as td:
            scenario, _ = self._stacked(td)
            comments = scenario.calls_to("linear_ops.py", "comment")
        self.assertEqual(1, len(comments), scenario.calls)
        self.assertEqual(INCIDENT_CARD, comments[0]["args"][1])

    def test_the_comment_carries_the_shared_contract(self):
        """The sibling card that closes these cards when the parent lands
        finds them by this receipt: it opens with the literal marker and names
        the base branch in backticks."""
        with tempfile.TemporaryDirectory() as td:
            scenario, _ = self._stacked(td)
            body = scenario.calls_to("linear_ops.py", "comment")[0]["args"][2]
        self.assertTrue(body.startswith(RECEIPT_OPENER), body)
        self.assertIn(f"`{INCIDENT_BASE}`", body)
        self.assertIn("`main`", body)
        self.assertIn(INCIDENT_PR, body)

    def test_the_decision_is_logged(self):
        """Loudly, on the run that made it — the only place a person looking
        at a card that did NOT move will find out why."""
        with tempfile.TemporaryDirectory() as td:
            _, proc = self._stacked(td)
        self.assertIn(INCIDENT_BASE, proc.stdout)
        self.assertIn("main", proc.stdout)


class AMergeToTheDefaultBranchIsUnchanged(unittest.TestCase):
    """The behaviour that must not regress. A gate that also stopped ordinary
    merges would close no card at all."""

    def _landed(self, td):
        scenario = MergeScenario(td)
        proc = scenario.run(
            head_ref="agent/DRE-100-fix-thing", base_ref="main",
            default_branch="main",
            pr_url="https://github.com/dreadnought-foundry/agent-bureau/pull/2690",
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        return scenario

    def test_card_done_runs_with_the_card_and_the_url(self):
        with tempfile.TemporaryDirectory() as td:
            done = self._landed(td).calls_to("linear_ops.py", "card-done")
        self.assertEqual(1, len(done), done)
        self.assertEqual(
            ["card-done", "DRE-100",
             "https://github.com/dreadnought-foundry/agent-bureau/pull/2690"],
            done[0]["args"],
        )

    def test_the_merge_sweep_gate_still_runs(self):
        with tempfile.TemporaryDirectory() as td:
            scenario = self._landed(td)
            gate = scenario.calls_to("merge_sweep_gate.py")
            sweeps = scenario.calls_to("reconcile.py")
        self.assertEqual([["DRE-100"]], [c["args"] for c in gate])
        self.assertEqual([["--promote-only"]], [c["args"] for c in sweeps])

    def test_no_stacked_receipt_is_posted(self):
        """The receipt is the stacked case's alone — posting it on an ordinary
        merge would give the landing card a card that is already Done."""
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual([], self._landed(td).calls_to("linear_ops.py", "comment"))

    def test_a_branch_that_is_no_card_s_own_still_closes_nothing(self):
        """DRE-2027 is untouched: a hand-named branch merged to the default
        branch carries no card id, so there is nothing to comment on either."""
        with tempfile.TemporaryDirectory() as td:
            scenario = MergeScenario(td)
            proc = scenario.run(
                head_ref="ops/tidy-things", base_ref="main", default_branch="main"
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual([], scenario.calls_to("linear_ops.py"))


class TheBaseReachesTheShellThroughEnv(unittest.TestCase):
    """The untrusted-text rule the step already follows for `PR_BODY`: a branch
    name is attacker-nameable, so it is bound to a variable and never
    interpolated into the shell."""

    def test_both_values_are_declared_in_the_step_env(self):
        env = done_step().get("env") or {}
        self.assertEqual(
            "${{ github.event.pull_request.base.ref }}", env.get("BASE_REF")
        )
        self.assertEqual(
            "${{ github.event.repository.default_branch }}",
            env.get("DEFAULT_BRANCH"),
        )

    def test_the_run_block_interpolates_nothing(self):
        self.assertNotIn("${{", done_step()["run"])

    def test_the_shell_reads_the_variables(self):
        run = done_step()["run"]
        self.assertIn("$BASE_REF", run)
        self.assertIn("$DEFAULT_BRANCH", run)


class TheConflictSweepIsGatedTheSameWay(unittest.TestCase):
    """A merge into a side branch is not "the exact moment sibling PRs go
    DIRTY" — the sweep's own comment is the reason it runs, and it does not
    hold here."""

    def test_the_job_reads_the_base_and_the_default_branch(self):
        condition = str(conflict_job().get("if", ""))
        self.assertIn("github.event.pull_request.base.ref", condition)
        self.assertIn("github.event.repository.default_branch", condition)

    def test_the_job_still_requires_a_merge(self):
        self.assertIn(
            "github.event.pull_request.merged", str(conflict_job().get("if", ""))
        )


if __name__ == "__main__":
    unittest.main()
