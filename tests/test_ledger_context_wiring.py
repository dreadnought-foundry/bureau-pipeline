"""The ledger block, wired into the planner's context (DRE-3359).

`scripts/ledger_context.py` (DRE-3358) renders what the split ledger and the
mulch planning records say about cards that did not fit one run. Rendering it
reaches nobody: the planner reads standards because `plan.yml` assembles them
into `.bureau-pipeline/agent-context.md` before the agent starts, and the ledger
only becomes context when the same step APPENDS it there. These tests pin that
wiring and the brief section that tells the planner what to do with it.

What is pinned, and why each half could break silently:

  1. **ORDER and APPEND.** The renderer runs AFTER
     `assemble_context.py assemble planner` and appends with `>>`. A single `>`
     — one keystroke — truncates the standards the planner just had assembled
     and nothing goes red: the run still writes a file, the agent still reads
     one, and the operating rules are simply gone.
  2. **THE MULCH PATH.** `--mulch .mulch/expertise/planning.jsonl`, the PRODUCT
     checkout's records. The renderer's own default is that same CWD-relative
     path, so an omitted flag looks identical today and diverges the moment the
     default moves.
  3. **THE LOG.** The two STATUS lines are grepped back out into the step log,
     so a run's log answers "what was the planner given" without opening a file
     that is never committed. Bound to `ledger_context.LEDGER_STATUS` /
     `MULCH_STATUS` rather than to the strings typed twice.
  4. **THE STEP CANNOT DIE.** The whole point of the renderer's UNKNOWN block is
     that a missing ledger or a repo with no `.mulch/` is context the planner is
     told about, never a failed plan run. That is not read off the YAML — the
     step's own script is RUN, in a throwaway checkout with `split-ledger.json`
     and `.mulch/` absent, and asserted to exit 0 with both STATUS lines UNKNOWN.
  5. **THE BRIEF.** The section, where it sits, the four tells named from
     `split_ledger.TELLS`, the `ledger-check` block, its four keys, and the rule
     that an UNKNOWN status is written into every record rather than omitted —
     an omitted check reads as a check that passed.

The wave route is deliberately untouched: it decomposes into epics, not cards,
and sizes nothing. Test 6 holds that by counting the renderer's call sites.

Run: cd bureau-pipeline && python3 -m pytest tests/test_ledger_context_wiring.py -v
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404 — fixed-arg bash/python calls against a temp dir
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WF = os.path.join(ROOT, ".github", "workflows", "plan.yml")
BRIEF = os.path.join(ROOT, "briefs", "planner.md")
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import ledger_context as lc  # noqa: E402
import split_ledger as sl  # noqa: E402

STEP = "Assemble planner context"
ASSEMBLE = "assemble_context.py assemble planner"
RENDER = "ledger_context.py render"
CONTEXT = ".bureau-pipeline/agent-context.md"
MULCH = ".mulch/expertise/planning.jsonl"

#: The command the card's contract spells out, exactly.
COMMAND = (f"python3 .bureau-pipeline/scripts/{RENDER} --mulch {MULCH} "
           f">> {CONTEXT}")

#: The brief's new section, and the artifact block it tells the planner to
#: write. The four keys are the contract the artifact checker reads.
HEADING = "## Size against the ledger before you cut a card (DRE-3022)"
BLOCK = "ledger-check"
KEYS = ("card", "tells_checked", "ledger_match", "ledger_status")


def wf_src() -> str:
    return open(WF, encoding="utf-8").read()


def brief_src() -> str:
    return open(BRIEF, encoding="utf-8").read()


def planner_context_step() -> dict:
    """The `Assemble planner context` step, off the parsed workflow."""
    doc = yaml.safe_load(wf_src())
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if step.get("name") == STEP:
                return step
    raise AssertionError(f"plan.yml carries no {STEP!r} step")


def logical_lines(script: str) -> list[str]:
    """The step's shell script with backslash continuations joined up, so a
    command wrapped over three lines still reads as one command."""
    joined = re.sub(r"\\\n\s*", " ", script)
    return [line.strip() for line in joined.splitlines() if line.strip()]


def render_line(script: str) -> str:
    hits = [line for line in logical_lines(script) if RENDER in line]
    assert len(hits) == 1, f"expected one {RENDER} command, found {len(hits)}"
    return hits[0]


def fake_checkout(tmp: str) -> str:
    """A pipeline checkout at `tmp/.bureau-pipeline` with everything the step
    reads EXCEPT `config/split-ledger.json`, and no `.mulch/` beside it.

    Symlinked rather than copied so the scripts under test are this repo's, and
    `ledger_context` resolves its ledger off the symlinked path (`abspath` does
    not follow symlinks) — which is the whole point: the ledger is missing here
    and present in the real checkout.
    """
    pipeline = os.path.join(tmp, ".bureau-pipeline")
    os.makedirs(pipeline)
    for name in ("scripts", "standards", "briefs"):
        os.symlink(os.path.join(ROOT, name), os.path.join(pipeline, name))
    config = os.path.join(pipeline, "config")
    os.makedirs(config)
    for entry in os.listdir(os.path.join(ROOT, "config")):
        if entry == "split-ledger.json":
            continue
        os.symlink(os.path.join(ROOT, "config", entry),
                   os.path.join(config, entry))
    return pipeline


class LedgerReachesThePlannerTest(unittest.TestCase):
    def test_the_step_runs_the_renderer(self):
        script = planner_context_step()["run"]
        self.assertIn(RENDER, script,
                      f"the {STEP!r} step must render the ledger block")

    def test_the_renderer_runs_after_the_standards(self):
        # The block is APPENDED to the standards, so assembling them second
        # would overwrite it — order is the contract, not a preference.
        script = planner_context_step()["run"]
        self.assertLess(
            script.index(ASSEMBLE), script.index(RENDER),
            f"{RENDER} must run after {ASSEMBLE}")

    def test_the_block_is_appended_not_written(self):
        line = render_line(planner_context_step()["run"])
        self.assertIn(f">> {CONTEXT}", line,
                      f"the ledger block must be appended to {CONTEXT}")
        self.assertNotRegex(
            line, r"(?<!>)>\s*" + re.escape(CONTEXT),
            "a single `>` truncates the standards the step just assembled")

    def test_the_mulch_path_is_the_product_checkouts(self):
        line = render_line(planner_context_step()["run"])
        self.assertIn(f"--mulch {MULCH}", line,
                      "the mulch records are the product repo's, passed "
                      "explicitly rather than left to the renderer's default")

    def test_the_command_is_the_contracted_one(self):
        self.assertIn(COMMAND, re.sub(r"\\\n\s*", " ", wf_src()),
                      "the card's contract spells this command exactly")

    def test_the_status_lines_reach_the_step_log(self):
        # Bound to the renderer's own constants: rename them there and this
        # goes red rather than the log quietly emptying.
        lines = logical_lines(planner_context_step()["run"])
        hits = [line for line in lines
                if lc.LEDGER_STATUS in line and lc.MULCH_STATUS in line]
        self.assertEqual(
            len(hits), 1,
            f"one line must echo {lc.LEDGER_STATUS} and {lc.MULCH_STATUS} "
            "into the step log")
        echo = hits[0]
        self.assertIn(CONTEXT, echo,
                      "the status lines are grepped out of the appended text")
        self.assertNotRegex(echo, r">\s*" + re.escape(CONTEXT),
                            "the log line must not write to the context file")
        self.assertIn("||", echo,
                      "a grep that matched nothing must not fail the step")

    def test_the_wave_route_is_untouched(self):
        # A wave decomposes into epics, not cards, and sizes nothing.
        self.assertEqual(
            wf_src().count(RENDER), 1,
            "the renderer belongs to the plan route's context step alone")

    def test_the_step_is_on_the_plan_route(self):
        self.assertIn("steps.route.outputs.mode == 'plan'",
                      planner_context_step().get("if", ""))

    def test_missing_ledger_and_mulch_cannot_fail_the_step(self):
        # Not read off the YAML — the step's own script, run against a checkout
        # where both files are absent. GitHub's default shell, so a non-zero
        # command anywhere in the script fails the step here too.
        script = planner_context_step()["run"]
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = fake_checkout(tmp)
            self.assertFalse(
                os.path.exists(os.path.join(pipeline, "config",
                                            "split-ledger.json")))
            self.assertFalse(os.path.exists(os.path.join(tmp, ".mulch")))
            proc = subprocess.run(  # nosec B603 B607 — fixed args, temp cwd
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail",
                 "-c", script],
                cwd=tmp, capture_output=True, text=True)
            self.assertEqual(
                proc.returncode, 0,
                f"the step died with both files absent:\n{proc.stderr}")
            blob = open(os.path.join(tmp, CONTEXT), encoding="utf-8").read()
            for prefix in (lc.LEDGER_STATUS, lc.MULCH_STATUS):
                self.assertIn(f"{prefix} {lc.UNKNOWN}", blob,
                              f"{prefix} must report {lc.UNKNOWN}, never a guess")
                self.assertIn(prefix, proc.stdout,
                              f"{prefix} must reach the step log")
            # The standards are still there — the append did not truncate them.
            self.assertIn("===== BEGIN briefs/planner.md =====", blob)
            self.assertIn(f"===== BEGIN {lc.LEDGER_LABEL} =====", blob)
            self.assertIn(f"===== BEGIN {lc.MULCH_LABEL} =====", blob)


class BriefSizesAgainstTheLedgerTest(unittest.TestCase):
    def section(self) -> str:
        body = brief_src()
        self.assertIn(HEADING, body,
                      f"briefs/planner.md must carry {HEADING!r}")
        rest = body[body.index(HEADING) + len(HEADING):]
        end = rest.find("\n## ")
        return rest if end < 0 else rest[:end]

    def test_the_section_follows_the_execution_plan(self):
        headings = re.findall(r"^## .*$", brief_src(), re.M)
        previous = "## The execution plan — declare the files, then derive the order"
        self.assertIn(previous, headings)
        self.assertEqual(
            headings[headings.index(previous) + 1], HEADING,
            "the ledger section sits directly after the execution plan")

    def test_the_section_names_the_four_tells(self):
        section = self.section()
        for tell in sl.TELLS:
            self.assertIn(tell, section,
                          f"the section must name the {tell!r} tell")

    def test_the_section_names_the_artifact_block_and_its_keys(self):
        section = self.section()
        self.assertIn(BLOCK, section,
                      f"the section must name the {BLOCK} block")
        self.assertIn("## The cards", section,
                      "the block goes in the artifact's `## The cards` section")
        for key in KEYS:
            self.assertIn(f'"{key}"', section,
                          f"the {BLOCK} record must name the {key!r} key")

    def test_an_unknown_status_is_written_not_omitted(self):
        section = self.section()
        self.assertIn(lc.UNKNOWN, section,
                      "the section must say what an UNKNOWN status does")
        self.assertRegex(
            section, r"(?i)omit",
            "an omitted check reads as a check that passed — the section says so")

    def test_the_section_does_not_restate_the_standard(self):
        # One section, and the tells' meanings stay in card-quality.md: two
        # spellings of one rule are two answers waiting to disagree.
        section = self.section()
        self.assertLess(len(section.splitlines()), 30,
                        "keep it to one section — the standard's tells are not "
                        "restated")


if __name__ == "__main__":
    unittest.main()
