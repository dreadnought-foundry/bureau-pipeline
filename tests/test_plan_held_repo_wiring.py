"""The held-repo check is wired where the proof-reach check is, and the
planner's context carries its repo's holds (DRE-7753).

`scripts/plan_held_repo.py` (DRE-7752) reads the plan's own repo's holds
through `sweep_holds.read` (DRE-7750). `plan.yml` uses it at four points:

  * `Assemble planner context` appends `plan_held_repo.py context` to the
    agent context, beside the ledger, spoken-thread and bound blocks, with the
    same kind of presence line — so the planner, and every re-plan reading the
    same file, sees a `REPO HOLDS:` block.
  * Every step that runs `plan_proof_reach.py findings` runs
    `plan_held_repo.py findings` after it, with the same `--note-file` and the
    same `--structural-file` — or none, on the review route, which writes
    none. The steps are DISCOVERED, never counted, so a fourth mechanical step
    added later fails here until it is wired too.

Each of those steps is handed the three readings `sweep_holds.read` takes,
threaded verbatim: the caller's `max_wip` input, and its `BUREAU_MAX_WIP` and
`INTAKE_HOLD` variables.

Run: GITHUB_ACTIONS=true LINEAR_API_KEY=test \
     python3 -m pytest tests/test_plan_held_repo_wiring.py -v
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess  # nosec B404 — runs this repo's own workflow line
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import plan_held_repo  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "plan.yml"
CONTEXT_STEP = "Assemble planner context"
REVIEW_MECHANICAL = "Mechanical findings — the revised plan (review)"
REVIEW_UPLOAD = "Plan artifact — upload source (review)"
AGENT_CONTEXT = ".bureau-pipeline/agent-context.md"
ARTIFACT = "${{ runner.temp }}/plan-artifact.md"

REACH = "plan_proof_reach.py findings"
HELD = "plan_held_repo.py findings"
HELD_CONTEXT = "plan_held_repo.py context"

#: The three readings, exactly as the card's contract writes them.
READINGS = {
    "MAX_WIP": "${{ inputs.max_wip }}",
    "BUREAU_MAX_WIP": "${{ vars.BUREAU_MAX_WIP }}",
    "INTAKE_HOLD": "${{ vars.INTAKE_HOLD }}",
}

#: The hold comment the card plants, the shape agent-bureau-demo's stub carries.
HOLD = (
    "HELD at 0 on 2026-09-08 by CEO decision — this sandbox builds nothing "
    "until the CEO lifts it. Restore it to resume building here."
)


def steps() -> list[dict]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return [s for job in (doc.get("jobs") or {}).values()
            for s in (job or {}).get("steps") or [] if isinstance(s, dict)]


def named(name: str) -> dict:
    hits = [s for s in steps() if s.get("name") == name]
    if len(hits) != 1:
        raise AssertionError(f"{len(hits)} steps named {name!r}")
    return hits[0]


def commands(run: str) -> list[str]:
    """The step's script as one logical line per command."""
    return [line.strip() for line in re.sub(r"\\\n\s*", " ", run).splitlines()
            if line.strip()]


def index_of(lines: list[str], needle: str) -> int:
    hits = [i for i, line in enumerate(lines) if needle in line]
    if len(hits) != 1:
        raise AssertionError(f"{len(hits)} lines run {needle!r}: {lines}")
    return hits[0]


def flag(line: str, name: str):
    words = shlex.split(line)
    return words[words.index(name) + 1] if name in words else None


def reach_steps() -> list[dict]:
    return [s for s in steps() if REACH in str(s.get("run") or "")]


class TheReadingsAreThreadedVerbatim(unittest.TestCase):

    def assert_readings(self, step: dict) -> None:
        env = step.get("env") or {}
        for variable, value in READINGS.items():
            self.assertIn(variable, env, f"{step.get('name')}: no {variable}")
            self.assertEqual(value, str(env[variable]).strip(),
                             f"{step.get('name')}: {variable}")

    def test_the_context_step(self):
        self.assert_readings(named(CONTEXT_STEP))

    def test_every_mechanical_step(self):
        for step in reach_steps():
            self.assert_readings(step)


class ThePlannerContextCarriesTheHolds(unittest.TestCase):

    def lines(self) -> list[str]:
        return commands(named(CONTEXT_STEP)["run"])

    def test_it_appends_the_block_to_the_agent_context(self):
        lines = self.lines()
        line = lines[index_of(lines, HELD_CONTEXT)]
        words = shlex.split(line)
        self.assertIn(">>", words, line)
        self.assertEqual(AGENT_CONTEXT, words[words.index(">>") + 1], line)

    def test_after_the_assembly_and_beside_the_other_blocks(self):
        lines = self.lines()
        here = index_of(lines, HELD_CONTEXT)
        self.assertLess(index_of(lines, "assemble_context.py assemble planner"), here)
        self.assertLess(index_of(lines, "plan_bound.py context"), here)

    def test_a_presence_line_follows_it(self):
        lines = self.lines()
        here = index_of(lines, HELD_CONTEXT)
        grep = index_of(lines, "^REPO HOLDS:")
        self.assertEqual(here + 1, grep, lines)
        self.assertTrue(lines[grep].startswith(
            f"grep -E '^{plan_held_repo.CONTEXT_HEADING}' {AGENT_CONTEXT}"), lines[grep])


class EveryProofReachStepRunsTheHeldRepoCheck(unittest.TestCase):

    def found(self) -> list[dict]:
        found = reach_steps()
        self.assertGreaterEqual(len(found), 1, "no step runs the proof-reach check")
        return found

    def test_the_review_step_is_among_them(self):
        self.assertIn(REVIEW_MECHANICAL, [s.get("name") for s in self.found()])

    def test_it_runs_after_the_proof_reach_check(self):
        for step in self.found():
            lines = commands(step["run"])
            self.assertLess(index_of(lines, REACH), index_of(lines, HELD),
                            step.get("name"))

    def test_on_the_plan_artifact(self):
        for step in self.found():
            lines = commands(step["run"])
            self.assertEqual(ARTIFACT, flag(lines[index_of(lines, HELD)], "--artifact"),
                             step.get("name"))

    def test_the_same_note_file(self):
        for step in self.found():
            lines = commands(step["run"])
            reach, held = lines[index_of(lines, REACH)], lines[index_of(lines, HELD)]
            self.assertTrue(flag(reach, "--note-file"), step.get("name"))
            self.assertEqual(flag(reach, "--note-file"), flag(held, "--note-file"),
                             step.get("name"))

    def test_the_same_structural_file_or_none(self):
        for step in self.found():
            lines = commands(step["run"])
            reach, held = lines[index_of(lines, REACH)], lines[index_of(lines, HELD)]
            self.assertEqual(flag(reach, "--structural-file"),
                             flag(held, "--structural-file"), step.get("name"))

    def test_before_the_note_is_posted(self):
        for step in self.found():
            lines = commands(step["run"])
            self.assertLess(index_of(lines, HELD), index_of(lines, "linear_ops.py comment"),
                            step.get("name"))


class TheReviewRoute(unittest.TestCase):

    def test_no_structural_file_and_the_note_before_the_comment(self):
        lines = commands(named(REVIEW_MECHANICAL)["run"])
        held = lines[index_of(lines, HELD)]
        self.assertEqual("$NOTE", flag(held, "--note-file"))
        self.assertIsNone(flag(held, "--structural-file"))
        self.assertLess(index_of(lines, HELD), index_of(lines, "linear_ops.py comment"))

    def test_the_artifact_is_the_one_the_route_uploads(self):
        lines = commands(named(REVIEW_MECHANICAL)["run"])
        uploaded = named(REVIEW_UPLOAD)["with"]["path"]
        self.assertEqual(uploaded, flag(lines[index_of(lines, HELD)], "--artifact"))


class TheContextLineQuotesTheStub(unittest.TestCase):
    """The line as `plan.yml` writes it, run over a scratch caller checkout
    whose reconcile stub holds `max_wip: "0"` under the hold comment — with no
    `max_wip` input and with the input "0". Both readings quote the stub."""

    STUB = (
        "name: Reconcile\n"
        "on:\n"
        "  schedule:\n"
        "    - cron: \"*/15 * * * *\"\n"
        "jobs:\n"
        "  call:\n"
        "    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/reconcile.yml@stable\n"
        "    with:\n"
        "      pipeline_ref: stable\n"
        f"      # {HOLD}\n"
        "      max_wip: \"0\"\n"
        "      intake_hold: ${{ vars.INTAKE_HOLD }}\n"
        "    secrets: inherit\n"
    )

    def line(self) -> str:
        lines = commands(named(CONTEXT_STEP)["run"])
        return lines[index_of(lines, HELD_CONTEXT)]

    def run_line(self, max_wip: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / ".bureau-pipeline").mkdir()
            (ws / ".bureau-pipeline" / "scripts").symlink_to(ROOT / "scripts")
            (ws / ".github" / "workflows").mkdir(parents=True)
            (ws / ".github" / "workflows" / "reconcile.yml").write_text(
                self.STUB, encoding="utf-8")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("BUREAU_MAX_WIP", "INTAKE_HOLD")}
            env.update(GITHUB_WORKSPACE=str(ws), MAX_WIP=max_wip,
                       BUREAU_MAX_WIP="", INTAKE_HOLD="")
            done = subprocess.run(  # nosec B603 B607 — the workflow's own line
                ["bash", "-e", "-c", self.line()], cwd=ws, env=env,
                capture_output=True, text=True, timeout=60)
            self.assertEqual(0, done.returncode, done.stderr)
            return (ws / AGENT_CONTEXT).read_text(encoding="utf-8")

    def assert_quotes_the_hold(self, text: str) -> None:
        block = text[text.index("REPO HOLDS:"):]
        self.assertIn("this plan's repo is held", block)
        self.assertIn(f'"{HOLD}"', block)
        self.assertIn(".github/workflows/reconcile.yml", block)

    def test_with_no_max_wip_input(self):
        self.assert_quotes_the_hold(self.run_line(""))

    def test_with_the_input_zero(self):
        self.assert_quotes_the_hold(self.run_line("0"))


if __name__ == "__main__":
    unittest.main()
