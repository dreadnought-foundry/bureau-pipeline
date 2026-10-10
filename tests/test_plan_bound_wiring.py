"""The six epic park steps call the bound exit before they park (DRE-6455).

DRE-6452 landed `scripts/plan_bound.py`: it decides what a plan-critic bound
does BEFORE anything parks the card — one Green Light question, one more
planner rewrite on a fresh attempt, or `park`. This card wires it into the six
`plan.yml` steps that stamp `plan-critic-bound`, and appends its `context`
section to the epic planner's assembled context, so a fresh attempt opened by
a `bound-rewrite` dispatch reads every finding it must answer.

What is held here, in the two halves the card asks for:

  * The wiring, read out of plan.yml: one `plan_bound.py exit "$EPIC"` call
    per step, with the right `--stage` and no other flag, inside an
    `if … ; then exit 0 fi` whose body is the lone `exit 0`, directly ahead of
    the step's `hold.py apply`, which stays ahead of its Triage move — and
    each step's `if:` exactly as it was.
  * The behavior, run with plan.yml's own shell (the
    `tests/test_plan_critic_scenario.py` pattern): with `plan_bound.py`,
    `hold.py` and `linear_ops.py` replaced by recording stubs, exit 0 ends the
    step before the hold, and exit 3 — or a crash — parks exactly as today.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_bound_wiring.py -v
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
WF = os.path.join(ROOT, ".github", "workflows", "plan.yml")
sys.path.insert(0, SCRIPTS)

import hold  # noqa: E402

EPIC = "DRE-6455"
REPO = "dreadnought-foundry/bureau-pipeline"
RECEIPT = "plan_bound.py exit"
EXIT_PATH = ".bureau-pipeline/scripts/plan_bound.py"

# The six steps, the stage each one's bound is read on, and the `if:` each
# carried at `main` before this card — committed here so a change to any gate
# turns this red. Whether the step leaves the job red on its park path today
# is the last field: `Review — the review died` and the re-check end `exit 1`
# for the medic, and keep doing so on the park path.
SITES = (
    ("First critic — the bound parks in Triage", "pre",
     "steps.route.outputs.mode == 'plan' && (steps.pre1.outputs.bound == 'true' "
     "|| steps.pre2.outputs.bound == 'true')", 0),
    ("Review — the review died", "post",
     "steps.route.outputs.mode == 'review' && steps.posta.outcome == 'failure' "
     "&& steps.postverdict.outputs.verdict == 'NO_RESULT'", 1),
    ("Review — the second critic passed a plan the first critic held", "post",
     "steps.route.outputs.mode == 'review' && steps.post1.outputs.result == 'PASS' "
     "&& steps.post1.outputs.pre_passed != 'true'", 0),
    ("Review — the second critic produced no result", "post",
     "steps.route.outputs.mode == 'review' && steps.post1.outputs.result == 'NO_RESULT'", 0),
    ("Re-check the revised plan — review mode", "post",
     "steps.route.outputs.mode == 'review' && steps.post1.outputs.action == 'hold' "
     "&& steps.post1.outputs.bound != 'true'", 1),
    ("Review — the bound parks the plan in Triage", "post",
     "steps.route.outputs.mode == 'review' && steps.post1.outputs.bound == 'true'", 0),
)
SITE_NAMES = tuple(name for name, *_ in SITES)

CONTEXT_STEP = "Assemble planner context"
ONE_OFF_CONTEXT_STEP = "One-off revision — planner context"
# `One-off revision — planner context` at `main` before this card. The bound's
# section goes to the EPIC planner only; the one-off revision reads its prior
# findings through `one_off_prior_block` (DRE-6454).
ONE_OFF_CONTEXT_IF = "steps.oneoff.outputs.action == 'revise'"
ONE_OFF_CONTEXT_RUN = (
    'python3 .bureau-pipeline/scripts/assemble_context.py assemble planner \\\n'
    '  > .bureau-pipeline/agent-context.md\n'
    'python3 .bureau-pipeline/scripts/spoken_thread.py people "$CARD" >> '
    '.bureau-pipeline/agent-context.md\n'
    'echo "assembled $(wc -l < .bureau-pipeline/agent-context.md) lines of planner context"\n'
    '# A revision is read off these files and nothing else, so none may\n'
    '# be left over from anything that ran before it.\n'
    'rm -f "${{ runner.temp }}/one-off-revised-card.md" \\\n'
    '      "${{ runner.temp }}/one-off-revision-summary.md" \\\n'
    '      "${{ runner.temp }}/one-off-question.md" \\\n'
    '      "${{ runner.temp }}/one-off-question-choices.json"\n'
)
CONTEXT_FILE = ".bureau-pipeline/agent-context.md"
UNKNOWN = "BOUND STATUS: UNKNOWN"


def _steps() -> list[dict]:
    doc = yaml.safe_load(open(WF, encoding="utf-8").read())
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


def step(name: str) -> dict:
    found = [s for s in _steps() if s.get("name") == name]
    assert len(found) == 1, f"{len(found)} steps named {name!r} in plan.yml"
    return found[0]


def _logical(run: str) -> list[str]:
    """The run's lines with backslash continuations joined, each stripped —
    one entry per shell command line."""
    out: list[str] = []
    pending = ""
    for raw in run.splitlines():
        line = raw.strip()
        if line.endswith("\\"):
            pending += line[:-1].strip() + " "
            continue
        out.append((pending + line).strip())
        pending = ""
    if pending:
        out.append(pending.strip())
    return out


def _code(line: str) -> str:
    """A line with a trailing ` # comment` cut off."""
    return re.split(r"\s+#", line, maxsplit=1)[0].strip()


def exit_problems(run: str, stage: str) -> list[str]:
    """Everything wrong with one park step's bound-exit wiring."""
    lines = _logical(run)
    calls = [i for i, line in enumerate(lines) if RECEIPT in line]
    if len(calls) != 1:
        return [f"{len(calls)} `{RECEIPT}` calls, want exactly one"]
    i = calls[0]
    head = lines[i]
    if not (head.startswith("if ") and head.endswith("; then")):
        return [f"the call is not an `if … ; then`: {head!r}"]
    found: list[str] = []
    argv = shlex.split(head[len("if "):-len("; then")], posix=False)
    want = ["python3", EXIT_PATH, "exit", '"$EPIC"', "--stage", stage,
            "--repo", '"$GITHUB_REPOSITORY"']
    if argv != want:
        found.append(f"the call is {argv}, want {want}")
    body = lines[i + 1:i + 3]
    if [_code(line) for line in body] != ["exit 0", "fi"]:
        found.append(f"the `then` body is {body}, want the lone `exit 0` then `fi`")
    holds = [j for j, line in enumerate(lines)
             if line.startswith("python3 .bureau-pipeline/scripts/hold.py apply")]
    triage = [j for j, line in enumerate(lines)
              if line.startswith('python3 .bureau-pipeline/scripts/linear_ops.py '
                                 'state "$EPIC" "Triage"')]
    if len(holds) != 1 or len(triage) != 1:
        return found + [f"{len(holds)} hold.py apply and {len(triage)} Triage lines"]
    if holds[0] != i + 3:
        found.append("the `hold.py apply` line does not directly follow the "
                     "bound exit's `fi`")
    if not triage[0] > holds[0]:
        found.append("the Triage move is not after the hold")
    return found


def context_problems(run: str) -> list[str]:
    """Everything wrong with the planner-context append."""
    lines = _logical(run)
    found: list[str] = []
    appends = [i for i, line in enumerate(lines) if "plan_bound.py context" in line]
    if len(appends) != 1:
        return [f"{len(appends)} `plan_bound.py context` lines, want one"]
    i = appends[0]
    line = lines[i]
    want = (f'python3 {EXIT_PATH} context "$CARD" >> {CONTEXT_FILE} '
            f'|| echo "{UNKNOWN} ')
    if not line.startswith(want) or not line.endswith(f'" >> {CONTEXT_FILE}'):
        found.append(f"the append is {line!r}: want the `context \"$CARD\"` "
                     f"append with its `|| echo \"{UNKNOWN} …\"` fallback")
    people = [j for j, text in enumerate(lines) if "spoken_thread.py people" in text]
    closing = [j for j, text in enumerate(lines) if text.startswith('echo "assembled ')]
    if len(people) != 1 or not i > people[0]:
        found.append("the append is not after the `spoken_thread.py people` line")
    if len(closing) != 1 or not i < closing[0]:
        found.append("the append is not before the closing `echo \"assembled …\"`")
    return found


class TheWiringAsWritten(unittest.TestCase):
    def test_each_park_step_calls_the_exit_first(self):
        for name, stage, _gate, _rc in SITES:
            with self.subTest(step=name):
                self.assertEqual(exit_problems(step(name)["run"], stage), [])

    def test_each_park_steps_gate_is_unchanged(self):
        for name, _stage, gate, _rc in SITES:
            with self.subTest(step=name):
                self.assertEqual(step(name)["if"], gate)

    def test_the_exit_takes_no_note_file(self):
        for name, *_ in SITES:
            with self.subTest(step=name):
                call = [line for line in _logical(step(name)["run"]) if RECEIPT in line]
                self.assertTrue(call)
                self.assertNotIn("--note-file", call[0])

    def test_a_wiring_that_drifts_is_caught(self):
        """The reader above is not vacuous: each shape it refuses, it refuses."""
        name, stage = SITES[5][0], SITES[5][1]
        run = step(name)["run"]
        call = next(line for line in run.splitlines() if RECEIPT in line)
        mutants = {
            "no call": "\n".join(line for line in run.splitlines()
                                 if RECEIPT not in line and "--repo \"$GITHUB" not in line),
            "wrong stage": run.replace("--stage post", "--stage pre"),
            "a note file": run.replace(call, call + " --note-file x"),
            "a body that does more": run.replace("exit 0", "echo x\n  exit 0", 1),
            "after the hold": run.replace(
                "python3 .bureau-pipeline/scripts/hold.py apply",
                "python3 .bureau-pipeline/scripts/hold.py apply \"$EPIC\" --reason x\n"
                "if python3 .bureau-pipeline/scripts/plan_bound.py exit \"$EPIC\" "
                "--stage post --repo \"$GITHUB_REPOSITORY\"; then\n  exit 0\nfi\n"
                "python3 .bureau-pipeline/scripts/hold.py apply", 1),
        }
        for what, mutant in mutants.items():
            with self.subTest(mutant=what):
                self.assertNotEqual(exit_problems(mutant, stage), [], what)

    def test_the_epic_planner_context_appends_the_bound_section(self):
        self.assertEqual(context_problems(step(CONTEXT_STEP)["run"]), [])

    def test_no_other_step_appends_it(self):
        having = [s.get("name") for s in _steps()
                  if "plan_bound.py context" in (s.get("run") or "")]
        self.assertEqual(having, [CONTEXT_STEP])

    def test_the_one_off_context_is_unchanged(self):
        one_off = step(ONE_OFF_CONTEXT_STEP)
        self.assertEqual(one_off["if"], ONE_OFF_CONTEXT_IF)
        self.assertEqual(one_off["run"], ONE_OFF_CONTEXT_RUN)

    def test_an_append_removed_or_unguarded_is_caught(self):
        run = step(CONTEXT_STEP)["run"]
        kept = [line for line in run.splitlines()
                if "plan_bound.py context" not in line and UNKNOWN not in line]
        self.assertNotEqual(context_problems("\n".join(kept)), [])
        bare = re.sub(r"\s*\\\n\s*\|\| echo \"BOUND STATUS: UNKNOWN[^\n]*", "", run)
        self.assertNotEqual(bare, run, "the fallback was not found to strip")
        self.assertNotEqual(context_problems(bare), [])


class TheRegistryRows(unittest.TestCase):
    def test_each_row_names_the_exit_as_what_runs_first(self):
        doc = json.load(open(os.path.join(ROOT, "config", "holds.json"),
                             encoding="utf-8"))
        rows = {row["scope"]: row for row in doc["sites"]
                if row["file"] == ".github/workflows/plan.yml"
                and any(e["reason"] == "plan-critic-bound" for e in row["reasons"])}
        self.assertEqual(set(SITE_NAMES) & set(rows), set(SITE_NAMES))
        for name in SITE_NAMES:
            with self.subTest(step=name):
                (entry,) = [e for e in rows[name]["reasons"]
                            if e["reason"] == "plan-critic-bound"]
                tried = entry["tried_first"]
                self.assertEqual(tried["receipt"], RECEIPT)
                self.assertIn("Before the park, the bound exit (scripts/plan_bound.py)",
                              tried["step"])
                self.assertIn(tried["receipt"], step(name)["run"])

    def test_the_registry_check_passes(self):
        self.assertEqual(hold.problems(), [])


# --------------------------------------------------------------------------- #
# the behavior, run with plan.yml's own shell                                  #
# --------------------------------------------------------------------------- #

# Every stub logs one line per call to STUB_LOG, so a walk reads the order.
BOUND_STUB = '''#!/usr/bin/env python3
import os, sys
with open(os.environ["STUB_LOG"], "a") as f:
    f.write("bound " + " ".join(sys.argv[1:]) + "\\n")
if sys.argv[1] == "context" and os.environ.get("STUB_SECTION"):
    print(os.environ["STUB_SECTION"])
sys.exit(int(os.environ.get("STUB_BOUND_RC", "3")))
'''

HOLD_STUB = '''#!/usr/bin/env python3
import os, sys
with open(os.environ["STUB_LOG"], "a") as f:
    f.write("hold " + " ".join(sys.argv[1:]) + "\\n")
'''

LINEAR_STUB = '''#!/usr/bin/env python3
import os, sys
cmd, *args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as f:
    if cmd == "comment":
        f.write("comment " + args[1].replace("\\n", " | ") + "\\n")
    else:
        f.write(cmd + " " + " ".join(args) + "\\n")
if cmd == "dump-comments":
    print("[]")
'''

# The two constants the steps import, and the three verbs they call, of the
# real module — its own walks live in tests/test_plan_critic_scenario.py.
PLAN_CRITIC_STUB = '''#!/usr/bin/env python3
import os, sys
REAPPROVE_HOW = "move it back to Planning"
MAX_ROUNDS = 2


def _arg(name):
    return sys.argv[sys.argv.index(name) + 1]


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "died":
        open(_arg("--note-file"), "w").write("💀 the review died\\n")
        open(_arg("--record-file"), "w").write("plan-critic-death: subtype=error_max_turns\\n")
    elif cmd == "held-park":
        sys.stdin.read()
        print("🛑 Parked in Triage with needs-human — the first critic still held it.")
    elif cmd == "cycle-start":
        print("plan-cycle: start")
'''

REVIEW_RERUN_STUB = '''#!/usr/bin/env python3
import sys


def _arg(name):
    return sys.argv[sys.argv.index(name) + 1]


if sys.argv[1] == "after-death":
    open(_arg("--github-output"), "a").write("action=park\\n")
    open(_arg("--note-file"), "w").write("🛑 Parked in Triage — the review died twice.\\n")
'''

# The re-check's first gate refuses the revision, which is the park path.
VALIDATE_CARD_STUB = '''#!/usr/bin/env python3
import sys
sys.exit(1)
'''

CONTEXT_STUBS = {
    "assemble_context.py": 'print("# the assembled standards and brief")',
    "ledger_context.py": 'print("LEDGER STATUS: read")',
    "spoken_thread.py": 'print("SPOKEN STATUS: read")',
}

EXPRESSIONS = {
    "${{ github.event.client_payload.identifier }}": EPIC,
    "${{ github.run_id }}": "4242",
    "${{ github.run_attempt }}": "1",
    "${{ steps.posta.outputs.execution_file }}": "",
    "${{ steps.postturns.outputs.max_turns }}": "60",
}

SECTION = ("## Findings the critics left open on this card\n\n"
           "BOUND STATUS: rewrite granted at 2026-10-10T12:00:00Z — answer every "
           "finding below before anything else\n\n"
           "1. (revision) DRE-9001 names no file it edits")


class TheBehavior(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.scripts = os.path.join(self.tmp, ".bureau-pipeline", "scripts")
        os.makedirs(self.scripts)
        for name, body in (("plan_bound.py", BOUND_STUB), ("hold.py", HOLD_STUB),
                           ("linear_ops.py", LINEAR_STUB),
                           ("plan_critic.py", PLAN_CRITIC_STUB),
                           ("review_rerun.py", REVIEW_RERUN_STUB),
                           ("validate_card.py", VALIDATE_CARD_STUB)):
            self._stub(name, body)
        for name, body in CONTEXT_STUBS.items():
            self._stub(name, "#!/usr/bin/env python3\n" + body + "\n")
        self.log = os.path.join(self.tmp, "log.txt")
        self.gho = os.path.join(self.tmp, "step-output")
        for path in (self.log, self.gho):
            open(path, "w").close()

    def _stub(self, name, body):
        path = os.path.join(self.scripts, name)
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, 0o755)

    def _run(self, name: str, **env_extra) -> subprocess.CompletedProcess:
        script = step(name)["run"].replace("${{ runner.temp }}", self.tmp)
        for expression, value in EXPRESSIONS.items():
            script = script.replace(expression, value)
        leftover = re.findall(r"\$\{\{[^}]*\}\}", script)
        self.assertEqual(leftover, [], f"unmodelled expressions in {name!r}")
        env = dict(os.environ, STUB_LOG=self.log, GITHUB_OUTPUT=self.gho,
                   GITHUB_REPOSITORY=REPO, RUNNER_TEMP=self.tmp,
                   # The steps' own env, at the values that reach the park.
                   FINDING="DRE-9001 names no file it edits", NOTE="",
                   NO_RESULTS="2", REPLAN_OUTCOME="success", CARD=EPIC)
        env.update(env_extra)
        return subprocess.run(["bash", "-e", "-c", script], cwd=self.tmp,
                              capture_output=True, text=True, env=env)

    def _lines(self) -> list[str]:
        return open(self.log, encoding="utf-8").read().splitlines()

    def test_a_handled_bound_ends_the_step_before_the_hold(self):
        for name, stage, _gate, _rc in SITES:
            with self.subTest(step=name):
                open(self.log, "w").close()
                out = self._run(name, STUB_BOUND_RC="0")
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                lines = self._lines()
                self.assertIn(f"bound exit {EPIC} --stage {stage} --repo {REPO}", lines)
                self.assertFalse([line for line in lines if line.startswith("hold ")], lines)
                self.assertNotIn(f"state {EPIC} Triage", lines)
                self.assertFalse([line for line in lines if "🛑" in line], lines)

    def test_a_park_or_a_crash_parks_exactly_as_today(self):
        for rc in ("3", "1"):
            for name, stage, _gate, park_rc in SITES:
                with self.subTest(step=name, exit=rc):
                    open(self.log, "w").close()
                    out = self._run(name, STUB_BOUND_RC=rc)
                    self.assertEqual(out.returncode, park_rc, out.stdout + out.stderr)
                    lines = self._lines()
                    bound = lines.index(f"bound exit {EPIC} --stage {stage} --repo {REPO}")
                    held = lines.index(
                        f"hold apply {EPIC} --reason plan-critic-bound --by plan.yml")
                    triage = lines.index(f"state {EPIC} Triage")
                    self.assertLess(bound, held)
                    self.assertLess(held, triage)
                    notes = [j for j, line in enumerate(lines)
                             if line.startswith("comment 🛑")]
                    self.assertTrue(notes and notes[-1] > triage, lines)

    def _context(self) -> str:
        return open(os.path.join(self.tmp, CONTEXT_FILE), encoding="utf-8").read()

    def test_the_planner_reads_the_bound_section_last(self):
        out = self._run(CONTEXT_STEP, STUB_BOUND_RC="0", STUB_SECTION=SECTION)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn(f"bound context {EPIC}", self._lines())
        self.assertTrue(self._context().rstrip("\n").endswith(SECTION), self._context())
        self.assertIn("SPOKEN STATUS: read", self._context())

    def test_a_crash_in_the_section_leaves_a_readable_line_and_a_live_step(self):
        out = self._run(CONTEXT_STEP, STUB_BOUND_RC="1")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        text = self._context()
        unknown = [line for line in text.splitlines() if line.startswith(UNKNOWN)]
        self.assertEqual(len(unknown), 1, text)
        self.assertIn("SPOKEN STATUS: read", text)
        self.assertIn("LEDGER STATUS: read", text)


if __name__ == "__main__":
    unittest.main()
