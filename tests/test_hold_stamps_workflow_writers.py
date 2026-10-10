"""RED-first tests: the plan route, the fix loop and the re-review watcher stamp the reason on every hold they apply (DRE-6179).

THE GAP. Nine sites applied the bare `needs-human` label: six park steps in
plan.yml, the unfixable-check hold in agent-fix.yml, `park_for_human` in
report_fix_result.sh and the second firing in `rereview_watch._fire`. Each
already posts a receipt a person can read; none left the machine-readable
reason the sweep, the console report and the lift logic need (DRE-6173's
registry, `config/holds.json`, names the reason of each).

THE FIX UNDER TEST. Each label write becomes the registry's writer — `hold.py
apply` in shell, `hold.apply` in Python — at the same position, and nothing
else at the site moves:

  1. plan.yml: no `add-label … needs-human` line; six `hold.py apply …
     --reason plan-critic-bound --by plan.yml` lines, each followed in its
     step by the Triage move.
  2. The fix loop, EXECUTED with `hold.py` stubbed: the unfixable step stamps
     `--reason unfixable-check --at "$HEAD_SHA"`, `park_for_human` stamps
     `--reason fix-dispute --at "${HEAD_NOW:-$PRE_SHA}"` — the pull request's
     head when it was read, the run's starting head when it was not — each
     before its Triage move, and neither runs for a draft (DRE-5801).
  3. The stamps themselves, composed by the real `hold.py` against the real
     `config/holds.json` with only Linear patched: `lifts=new-head` at the two
     fix-loop sites, `lifts=manual` at the six plan.yml sites and the watcher.
  4. `rereview_watch._fire`: the second firing stamps before the lane write,
     the first applies no hold.
  5. The registry still matches every site exactly once, unchanged.

DRE-6456 adds a seventh plan.yml site: `One-off critic — park in Triage`,
the one bound park that wrote no stamp, calls the bound exit before it parks
and stamps `plan-critic-bound` between its note and its Triage move (6).

Run: python3 -m pytest tests/test_hold_stamps_workflow_writers.py -v
"""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
PLAN = ROOT / ".github" / "workflows" / "plan.yml"
AGENT_FIX = ROOT / ".github" / "workflows" / "agent-fix.yml"
REPORT = SCRIPTS / "report_fix_result.sh"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import hold  # noqa: E402
import plan_critic  # noqa: E402
import rereview_watch as rw  # noqa: E402
import unfixable_checks  # noqa: E402
from test_act_emission_scenario import (  # noqa: E402
    CARD,
    POST_SHA,
    PRE_SHA,
    _checkout,
    _open_handoff,
    _report_env,
)
from test_draft_pr_no_review_no_park import QA, VERDICT, _resolve  # noqa: E402
from test_hand_dispatch_no_work import rest  # noqa: E402
from test_unfixable_check_escalation import GH_STUB as GATE_GH_STUB  # noqa: E402
from test_unfixable_check_escalation import _check_run  # noqa: E402

BARE_LABEL = re.compile(r"\badd-label\s+\S+\s+needs-human\b")
HOLD_APPLY = re.compile(r"\bhold\.py\s+apply\b")
EPIC = "DRE-6179"
HEAD = "3e00473c" + "0" * 32

# One log for both stubs, so the order of the hold and the lane move is the
# order the step made them in.
STUB = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_CALLS"], "a") as fh:
    fh.write(json.dumps([{name!r}] + sys.argv[1:]) + "\\n")
if sys.argv[1:2] == ["count-comments"]:
    print("0")
"""


def _executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _stub_pair(scripts: Path) -> None:
    """`linear_ops.py` and `hold.py`, both recording into $STUB_CALLS."""
    _executable(scripts / "linear_ops.py", STUB.format(name="linear_ops.py"))
    _executable(scripts / "hold.py", STUB.format(name="hold.py"))


def _calls(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _holds(calls: list) -> list:
    return [c[1:] for c in calls if c[0] == "hold.py"]


def _triage_index(calls: list) -> int:
    return next(i for i, c in enumerate(calls)
                if c[0] == "linear_ops.py" and c[1] in ("advance", "state")
                and "Triage" in c)


def _code_lines(text: str) -> list:
    """(1-based number, line) for every line that is not a shell comment."""
    return [(n, line) for n, line in enumerate(text.splitlines(), start=1)
            if not line.lstrip().startswith("#")]


# --------------------------------------------------------------------------
# 1: plan.yml — seven stamped parks, no bare label
# --------------------------------------------------------------------------
class ThePlanRouteStampsEveryParkTest(unittest.TestCase):

    def setUp(self):
        self.lines = PLAN.read_text().splitlines()

    def _applies(self) -> list:
        return [(n, line) for n, line in _code_lines(PLAN.read_text())
                if HOLD_APPLY.search(line)]

    def test_no_bare_label_write_is_left(self):
        bare = [f"{n}: {line.strip()}" for n, line in _code_lines(PLAN.read_text())
                if BARE_LABEL.search(line)]
        self.assertEqual(bare, [])

    def test_seven_parks_stamp_plan_critic_bound(self):
        """Six epic parks stamp `$EPIC`; the one-off park stamps `$CARD`
        (DRE-6456)."""
        applies = self._applies()
        self.assertEqual(len(applies), 7, applies)
        cards = []
        for _, line in applies:
            match = re.search(r'hold\.py apply "\$(EPIC|CARD)" '
                              r"--reason plan-critic-bound --by plan\.yml", line)
            self.assertIsNotNone(match, line)
            cards.append(match.group(1))
        self.assertEqual(sorted(cards), ["CARD"] + ["EPIC"] * 6)

    def test_each_stamp_lands_before_its_steps_triage_move(self):
        """The label before the move: the relay dispatches a plan run the
        moment an `agent:planner` card enters Triage, and the plan-gate
        refuses it only when the label is already on."""
        for number, line in self._applies():
            var = re.search(r'hold\.py apply "(\$\w+)"', line).group(1)
            following = None
            for line in self.lines[number:]:
                if re.match(r"^\s*-\s+name:", line):
                    break
                if "linear_ops.py" in line and not line.lstrip().startswith("#"):
                    following = line
                    break
            with self.subTest(line=number):
                self.assertIsNotNone(following, "no linear_ops.py call follows in the step")
                self.assertIn(f'state "{var}" "Triage"', following)


# --------------------------------------------------------------------------
# 2a: the unfixable-check step, executed
# --------------------------------------------------------------------------
def _gate_step() -> dict:
    for step in yaml.safe_load(AGENT_FIX.read_text())["jobs"]["fix"]["steps"]:
        if step.get("id") == "unfixable":
            return step
    raise AssertionError("the unfixable step is gone from agent-fix.yml")


def _run_gate() -> list:
    """The gate's real run block on a red TDD-order check. Returns every
    linear_ops.py and hold.py call, in order."""
    run = _gate_step()["run"].replace("${{ github.repository }}", "acme/widget")
    assert "${{" not in run, "harness left an unsubstituted expression"
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        scripts = td / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True)
        for name in ("unfixable_checks.py", "gh_read_retry.py", "read_once.py"):
            (scripts / name).write_text((SCRIPTS / name).read_text())
        _stub_pair(scripts)
        _executable(td / "bin" / "gh", GATE_GH_STUB)
        (td / "checks.json").write_text(json.dumps([{"check_runs": [
            _check_run(unfixable_checks.TDD_CHECK_NAME, "failure")]}]))
        (td / "comments.json").write_text(json.dumps([[]]))
        out = td / "step-output"
        out.write_text("")
        script = td / "gate.sh"
        script.write_text("set -eo pipefail\n" + run)
        proc = subprocess.run(
            ["bash", str(script)], cwd=str(td), capture_output=True, text=True,
            env={**os.environ,
                 "PATH": f"{td / 'bin'}:{os.environ['PATH']}",
                 "GITHUB_OUTPUT": str(out), "RUNNER_TEMP": str(td),
                 "GH_LOG": str(td / "gh.jsonl"),
                 "GH_CHECKS": str(td / "checks.json"),
                 "GH_COMMENTS": str(td / "comments.json"),
                 "GH_POSTED": str(td / "posted.md"),
                 "GH_TOKEN": "test", "LINEAR_API_KEY": "test-key",
                 "HEAD_SHA": HEAD, "CARD": CARD, "PR": "176", "ATTEMPT": "1",
                 "STUB_CALLS": str(td / "calls.jsonl")},
            timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "escalate=true" in out.read_text(), "the gate did not escalate"
        return _calls(td / "calls.jsonl")


class TheUnfixableHoldIsStampedTest(unittest.TestCase):

    def test_the_step_carries_no_bare_label_write(self):
        bare = [line for _, line in _code_lines(_gate_step()["run"])
                if BARE_LABEL.search(line)]
        self.assertEqual(bare, [])

    def test_it_stamps_unfixable_check_at_the_full_head(self):
        holds = _holds(_run_gate())
        self.assertEqual(holds, [["apply", CARD, "--reason", "unfixable-check",
                                  "--at", HEAD, "--by", "agent-fix.yml"]])

    def test_the_stamp_lands_before_the_triage_move(self):
        calls = _run_gate()
        first_hold = next(i for i, c in enumerate(calls) if c[0] == "hold.py")
        self.assertLess(first_hold, _triage_index(calls))

    def test_its_own_receipt_is_untouched(self):
        calls = _run_gate()
        notes = [c for c in calls if c[:2] == ["linear_ops.py", "comment"]]
        self.assertEqual(len(notes), 1)
        self.assertIn(f"{unfixable_checks.HOLD_MARKER} @{HEAD[:8]}", notes[0][3])

    def test_a_draft_never_reaches_the_step(self):
        """DRE-5801: the step runs only on `go == 'true'` in fix mode, and the
        resolve step answers go=false for a draft's verdict."""
        guard = str(_gate_step().get("if") or "")
        self.assertIn("steps.pr.outputs.go == 'true'", guard)
        self.assertIn("steps.pr.outputs.mode == 'fix'", guard)
        outputs, _ = _resolve("CLEAN", True, [rest(QA, VERDICT)])
        self.assertEqual(outputs.get("go"), "false")


# --------------------------------------------------------------------------
# 2b: park_for_human, executed
# --------------------------------------------------------------------------
def _report_gh(td: str, head: str, is_draft: bool) -> str:
    """`gh` answering the Report step's reads with the head the caller
    chooses — empty for a read that failed."""
    binary = os.path.join(td, "bin")
    os.makedirs(binary, exist_ok=True)
    path = os.path.join(binary, "gh")
    with open(path, "w") as fh:
        fh.write(f"""#!/usr/bin/env python3
import sys
argv = sys.argv[1:]
if argv[:2] == ["pr", "view"]:
    fields = argv[argv.index("--json") + 1] if "--json" in argv else ""
    if fields == "isDraft":
        print({json.dumps("true" if is_draft else "false")})
    elif fields == "state":
        print("OPEN")
    elif fields == "headRefOid":
        if not {head!r}:
            sys.exit(1)
        print({head!r})
    sys.exit(0)
if argv[:1] == ["api"]:
    print("[]")
sys.exit(0)
""")
    os.chmod(path, 0o755)
    return binary


def _report_disputed_round(head: str, is_draft: bool = False) -> list:
    """The fix agent disputes the finding and pushes nothing. Returns every
    linear_ops.py and hold.py call the Report step made, in order."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(_checkout(td))
        _stub_pair(base / "scripts")
        binary = _report_gh(td, head, is_draft)
        _open_handoff(td, blocked=True)
        env = dict(os.environ, PATH=binary + os.pathsep + os.environ["PATH"],
                   CLASSIFICATION="", DISPATCH_TOKEN="test",
                   STUB_CALLS=os.path.join(td, "calls.jsonl"),
                   **_report_env(td, "fix"))
        proc = subprocess.run(["bash", str(REPORT)], cwd=td, env=env,
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        return _calls(Path(td) / "calls.jsonl")


class TheFixDisputeIsStampedTest(unittest.TestCase):

    def test_the_script_carries_no_bare_label_write(self):
        bare = [line for _, line in _code_lines(REPORT.read_text())
                if BARE_LABEL.search(line)]
        self.assertEqual(bare, [])

    def test_the_whole_fix_workflow_carries_no_bare_label_write(self):
        bare = [line for _, line in _code_lines(AGENT_FIX.read_text())
                if BARE_LABEL.search(line)]
        self.assertEqual(bare, [])

    def test_it_stamps_the_head_the_dispute_stands_on(self):
        holds = _holds(_report_disputed_round(POST_SHA))
        self.assertEqual(holds, [["apply", CARD, "--reason", "fix-dispute",
                                  "--at", POST_SHA, "--by", "agent-fix.yml"]])

    def test_an_unread_head_falls_back_to_the_runs_starting_head(self):
        holds = _holds(_report_disputed_round(""))
        self.assertEqual(holds, [["apply", CARD, "--reason", "fix-dispute",
                                  "--at", PRE_SHA, "--by", "agent-fix.yml"]])

    def test_the_stamp_lands_before_the_triage_move(self):
        calls = _report_disputed_round(POST_SHA)
        first_hold = next(i for i, c in enumerate(calls) if c[0] == "hold.py")
        self.assertLess(first_hold, _triage_index(calls))

    def test_a_draft_is_neither_stamped_nor_parked(self):
        calls = _report_disputed_round(POST_SHA, is_draft=True)
        self.assertEqual(_holds(calls), [])
        self.assertFalse([c for c in calls if "Triage" in c])


# --------------------------------------------------------------------------
# 3: the stamps the ten sites post, composed by the real hold.py
# --------------------------------------------------------------------------
# What each site's shell variables hold when it runs: the card, and a full sha
# wherever the site names a head.
_SHELL_VALUES = {
    "$EPIC": EPIC, "$CARD": EPIC, "$HEAD_SHA": HEAD,
    "${HEAD_NOW:-$PRE_SHA}": HEAD,
}


def _shell_applies() -> list:
    """`(where, argv)` for every `hold.py apply` line the nine shell sites
    carry, its variables replaced by what they hold at run time."""
    out = []
    sources = (PLAN, AGENT_FIX, REPORT)
    for path in sources:
        for number, line in _code_lines(path.read_text()):
            if not HOLD_APPLY.search(line):
                continue
            words = shlex.split(line.split("hold.py", 1)[1].split("||", 1)[0])
            out.append((f"{path.name}:{number}",
                        [_SHELL_VALUES.get(w, w) for w in words]))
    return out


def _stamps_from(run) -> list:
    """The comments `hold.apply` posts while `run()` runs, Linear patched."""
    fake = mock.MagicMock()
    with mock.patch.dict(sys.modules, {"linear_ops": fake}), \
            mock.patch.object(rw, "linear_ops", fake), \
            redirect_stdout(io.StringIO()):
        run()
    labels = [c.args for c in fake.add_label.call_args_list]
    assert all(args[1] == hold.HOLD_LABEL for args in labels), labels
    return [c.args[1] for c in fake.cmd_comment.call_args_list]


class TheStampsTheSitesPostTest(unittest.TestCase):
    """Against the real `config/holds.json`; `hold.stamp_line` is not
    patched, so a qualifier a reason refuses fails here, at the writer."""

    def setUp(self):
        hold._LOADED.clear()
        self.assertTrue(hold.load()["sites"])

    def _lifts(self, stamp: str) -> str:
        parsed = hold.read_stamp([stamp])
        self.assertIsNotNone(parsed, stamp)
        return parsed["lifts"]

    def test_nine_shell_sites_apply_through_hold_py(self):
        where = [w.split(":")[0] for w, _ in _shell_applies()]
        self.assertEqual(where.count("plan.yml"), 7)
        self.assertEqual(where.count("agent-fix.yml"), 1)
        self.assertEqual(where.count("report_fix_result.sh"), 1)

    def test_each_shell_site_posts_a_stamp_its_reason_accepts(self):
        want = {"plan.yml": "manual", "agent-fix.yml": "new-head",
                "report_fix_result.sh": "new-head"}
        for where, argv in _shell_applies():
            with self.subTest(site=where):
                rc = []
                stamps = _stamps_from(lambda a=argv: rc.append(hold.main(a)))
                self.assertEqual(rc, [0], f"hold.py {' '.join(argv)} refused")
                self.assertEqual(len(stamps), 1)
                self.assertEqual(self._lifts(stamps[0]), want[where.split(":")[0]])

    def test_the_watchers_second_firing_posts_a_manual_stamp(self):
        stamps = _stamps_from(lambda: rw._fire(EPIC, {"firing": 2}, None))
        self.assertEqual(stamps, [hold.stamp_line("epic-rereview-twice", "none",
                                                  "rereview_watch.py")])
        self.assertEqual(self._lifts(stamps[0]), "manual")


# --------------------------------------------------------------------------
# 4: rereview_watch._fire
# --------------------------------------------------------------------------
class TheWatcherStampsItsSecondFiringTest(unittest.TestCase):

    def _fire(self, found: dict) -> mock.MagicMock:
        parent = mock.MagicMock()
        with mock.patch.object(rw, "linear_ops", parent.linear), \
                mock.patch.object(rw, "hold", parent.hold, create=True), \
                mock.patch.object(rw, "dispatch_review", return_value=True):
            rw._fire(EPIC, found, "acme/widget")
        return parent

    def test_the_second_firing_stamps_before_the_lane_write(self):
        parent = self._fire({"firing": 2})
        self.assertEqual(parent.mock_calls, [
            mock.call.hold.apply(EPIC, "epic-rereview-twice", "none",
                                 "rereview_watch.py"),
            mock.call.linear.cmd_state(EPIC, plan_critic.BOUND_PARK_LANE),
        ])

    def test_the_first_firing_applies_no_hold(self):
        parent = self._fire({"firing": 1, "dispatch_reason": "review",
                             "trigger_state": "Planning"})
        self.assertEqual(parent.mock_calls, [])


# --------------------------------------------------------------------------
# 5: the registry still matches every site, unchanged
# --------------------------------------------------------------------------
class TheRegistryStillMatchesTest(unittest.TestCase):

    def test_hold_check_finds_no_problem(self):
        hold._LOADED.clear()
        self.assertEqual(hold.problems(hold.load(), root=str(ROOT)), [])

    def test_every_one_of_the_ten_sites_now_writes_through_the_registry(self):
        files = {".github/workflows/plan.yml", ".github/workflows/agent-fix.yml",
                 "scripts/rereview_watch.py"}
        sites = [s for s in hold.discover(str(ROOT)) if s.file in files]
        self.assertEqual(len(sites), 10, sites)
        for site in sites:
            with self.subTest(site=site.where):
                self.assertRegex(site.source, r"hold\.py\s+apply|hold\.apply\(")



# --------------------------------------------------------------------------
# 6: the one-off park — the bound exit first, then the stamped park (DRE-6456)
# --------------------------------------------------------------------------
ONE_OFF_PARK = "One-off critic — park in Triage"
# The step's `if:` at `main` d0f34c1c, before this card: the card adds two
# lines to the step's run and nothing to its gate.
ONE_OFF_PARK_IF = ("steps.oneoff.outputs.action == 'park' "
                   "|| steps.oorevised.outputs.outcome == 'unfinished'")
ONE_OFF_ANCHOR = "one-off-park.md"
BOUND_EXIT = ('if python3 .bureau-pipeline/scripts/plan_bound.py exit "$CARD" '
              '--stage one-off --repo "$GITHUB_REPOSITORY"; then')
PARK_COMMENT = 'python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD"'
PARK_HOLD = ('python3 .bureau-pipeline/scripts/hold.py apply "$CARD" '
             '--reason plan-critic-bound --by plan.yml')
PARK_TRIAGE = 'python3 .bureau-pipeline/scripts/linear_ops.py state "$CARD" "Triage"'


def _one_off_park() -> dict:
    doc = yaml.safe_load(PLAN.read_text())
    found = [s for job in doc["jobs"].values() for s in job.get("steps") or []
             if s.get("name") == ONE_OFF_PARK]
    assert len(found) == 1, f"{len(found)} steps named {ONE_OFF_PARK!r}"
    return found[0]


def _logical(run: str) -> list:
    """The run's command lines, continuations joined, comments dropped."""
    out, pending = [], ""
    for raw in run.splitlines():
        line = raw.strip()
        if line.endswith("\\"):
            pending += line[:-1].strip() + " "
            continue
        line = re.split(r"\s+#", (pending + line).strip(), maxsplit=1)[0].strip()
        pending = ""
        if line and not line.startswith("#"):
            out.append(line)
    return out


def one_off_park_problems(run: str) -> list:
    """Everything wrong with the order the one-off park step runs in."""
    lines = _logical(run)
    exits = [i for i, line in enumerate(lines) if "plan_bound.py exit" in line]
    if len(exits) != 1:
        return [f"{len(exits)} `plan_bound.py exit` calls, want exactly one"]
    i = exits[0]
    found = []
    if lines[i] != BOUND_EXIT:
        found.append(f"the exit is {lines[i]!r}, want {BOUND_EXIT!r}")
    if "--note-file" in lines[i]:
        found.append("the exit takes no note file")
    if lines[i + 1:i + 3] != ["exit 0", "fi"]:
        found.append(f"the `then` body is {lines[i + 1:i + 3]}, want `exit 0` then `fi`")
    order = []
    for want in (PARK_COMMENT, PARK_HOLD, PARK_TRIAGE):
        hits = [j for j, line in enumerate(lines) if line.startswith(want)]
        if len(hits) != 1:
            return found + [f"{len(hits)} lines open with {want!r}, want one"]
        order.append(hits[0])
    if not i < order[0] < order[1] < order[2]:
        found.append(f"the order is exit {i}, note {order[0]}, hold {order[1]}, "
                     f"Triage {order[2]} — want exit, note, hold, Triage")
    return found


class TheOneOffParkCallsTheExitAndStampsTest(unittest.TestCase):

    def test_the_step_runs_the_exit_the_note_the_hold_and_the_move_in_order(self):
        self.assertEqual(one_off_park_problems(_one_off_park()["run"]), [])

    def test_the_steps_gate_is_unchanged(self):
        self.assertEqual(_one_off_park()["if"], ONE_OFF_PARK_IF)

    def test_a_step_that_drifts_is_caught(self):
        """The reader is not vacuous: each shape it refuses, it refuses."""
        run = _one_off_park()["run"]
        no_exit = "\n".join(line for line in run.splitlines()
                             if "plan_bound.py exit" not in line
                             and '--repo "$GITHUB_REPOSITORY"' not in line)
        mutants = {
            "no exit": no_exit,
            "an epic stage": run.replace("--stage one-off", "--stage post"),
            "a note file": run.replace('--repo "$GITHUB_REPOSITORY"',
                                       '--repo "$GITHUB_REPOSITORY" --note-file "$PARK_NOTE"'),
            "no hold": "\n".join(line for line in run.splitlines()
                                  if "hold.py apply" not in line),
            "the hold after the move": run.replace(
                PARK_HOLD, "true").replace(PARK_TRIAGE, PARK_TRIAGE + "\n" + PARK_HOLD),
        }
        for what, mutant in mutants.items():
            with self.subTest(mutant=what):
                self.assertNotEqual(mutant, run, what)
                self.assertNotEqual(one_off_park_problems(mutant), [], what)


BOUND_STUB = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_CALLS"], "a") as fh:
    fh.write(json.dumps(["plan_bound.py"] + sys.argv[1:]) + "\\n")
sys.exit(int(os.environ["STUB_BOUND_RC"]))
"""


def _run_one_off_park(rc: int) -> list:
    """The step's real run block, `plan_bound.py`, `hold.py` and
    `linear_ops.py` stubbed and recording, the decision step's note on disk.
    Returns every call, in order."""
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        run = (_one_off_park()["run"]
               .replace("${{ github.event.client_payload.identifier }}", EPIC)
               .replace("${{ runner.temp }}", str(td)))
        assert "${{" not in run, "harness left an unsubstituted expression"
        scripts = td / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True)
        _stub_pair(scripts)
        _executable(scripts / "plan_bound.py", BOUND_STUB)
        (td / ONE_OFF_ANCHOR).write_text("🛑 Sent back twice: the card names no file.\n")
        proc = subprocess.run(
            ["bash", "-e", "-c", run], cwd=str(td), capture_output=True, text=True,
            env={**os.environ, "STUB_CALLS": str(td / "calls.jsonl"),
                 "STUB_BOUND_RC": str(rc), "GITHUB_REPOSITORY": "acme/widget",
                 "LINEAR_API_KEY": "test-key"},
            timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return _calls(td / "calls.jsonl")


class TheOneOffParkRunsTest(unittest.TestCase):

    EXIT = ["plan_bound.py", "exit", EPIC, "--stage", "one-off", "--repo", "acme/widget"]

    def test_a_handled_bound_writes_no_hold_and_no_lane(self):
        calls = _run_one_off_park(0)
        self.assertEqual(calls, [self.EXIT])

    def test_a_park_or_a_crash_posts_the_note_applies_the_hold_then_moves(self):
        for rc in (3, 1):
            with self.subTest(exit=rc):
                calls = _run_one_off_park(rc)
                self.assertEqual([c[:3] for c in calls], [
                    self.EXIT[:3],
                    ["linear_ops.py", "comment", EPIC],
                    ["hold.py", "apply", EPIC],
                    ["linear_ops.py", "state", EPIC],
                ])
                self.assertEqual(calls[0], self.EXIT)
                self.assertIn("names no file", calls[1][3])
                self.assertEqual(calls[2], ["hold.py", "apply", EPIC, "--reason",
                                            "plan-critic-bound", "--by", "plan.yml"])
                self.assertEqual(calls[3], ["linear_ops.py", "state", EPIC, "Triage"])

    def test_its_stamp_is_the_contracts(self):
        argv = ["apply", EPIC, "--reason", "plan-critic-bound", "--by", "plan.yml"]
        hold._LOADED.clear()
        rc = []
        stamps = _stamps_from(lambda: rc.append(hold.main(argv)))
        self.assertEqual(rc, [0])
        self.assertEqual(stamps, ["🔒 hold: reason=plan-critic-bound at=none "
                                  "lifts=manual by=plan.yml"])


class TheOneOffParkHasItsRowTest(unittest.TestCase):

    def setUp(self):
        hold._LOADED.clear()
        self.doc = hold.load()
        self.rows = [r for r in self.doc["sites"]
                     if r["file"] == ".github/workflows/plan.yml"
                     and r["scope"] == ONE_OFF_PARK]

    def test_the_row_names_the_site(self):
        self.assertEqual(len(self.rows), 1, self.rows)
        row = self.rows[0]
        self.assertEqual(row["anchor"], ONE_OFF_ANCHOR)
        self.assertEqual([(e["reason"], e["lifts"]) for e in row["reasons"]],
                         [("plan-critic-bound", "manual")])
        self.assertEqual(row["reasons"][0]["tried_first"]["receipt"], "plan_bound.py exit")
        self.assertEqual(row["readers"], list(hold.READERS))

    def test_discovery_finds_the_site_once_and_the_row_matches_it(self):
        sites = [s for s in hold.discover(str(ROOT)) if s.scope == ONE_OFF_PARK]
        self.assertEqual(len(sites), 1, sites)
        self.assertEqual(sites[0].file, ".github/workflows/plan.yml")
        self.assertEqual(len(self.rows), 1)
        self.assertTrue(hold.row_matches(self.rows[0], sites[0]))

    def test_the_check_passes_with_seven_plan_yml_sites(self):
        sites = [s for s in hold.discover(str(ROOT))
                 if s.file == ".github/workflows/plan.yml"]
        self.assertEqual(len(sites), 7, sites)
        self.assertEqual(hold.problems(self.doc, root=str(ROOT)), [])

    def test_the_page_lists_the_writer(self):
        text = (ROOT / "docs" / "holds.md").read_text(encoding="utf-8")
        rows = [line for line in text.splitlines() if line.startswith(
            f"| `.github/workflows/plan.yml` · {ONE_OFF_PARK} | `{ONE_OFF_ANCHOR}` "
            "| `plan-critic-bound` | `manual` |")]
        self.assertEqual(len(rows), 1, rows)

    def test_the_triage_entrance_says_the_one_off_parks_held(self):
        contract = json.loads((ROOT / "config" / "lane-contract.json").read_text())
        (triage,) = [lane for lane in contract["lanes"] if lane["name"] == "Triage"]
        text = triage["clauses"]["entrance"]["text"]
        self.assertIn("a one-off card the pre-approval critic sent back `MAX_ROUNDS` "
                      "times, or whose revision the planner did not finish, parked "
                      "with `needs-human`", text)


if __name__ == "__main__":
    unittest.main()
