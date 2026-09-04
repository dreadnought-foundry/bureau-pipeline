"""linear-sync's conflict sweep is a CALL SITE, and a missing variable on it
is a missing argument (DRE-3042).

Observed 2026-09-03 19:19 PT on bureau-pipeline. Four sibling PRs merged in
48 minutes; the fifth, #244, went DIRTY on the files they all touched — the
exact case the conflict sweep exists for. The sweep step ran, and died:
`Linear Sync` run 33829082382, job `call / conflict-sweep`, exit code 1. No
fix agent was dispatched, and a DIRTY pull request emits no workflow events
at all, so nothing else said anything either. #244 sat with no critic, no fix
loop and nothing watching it until a person dispatched `self-agent-fix.yml`
by hand twelve minutes later.

The cause was one absent variable. `reconcile.py` binds `REPO_SLUG` at import
and `fix_workflow()` reads it; **unset it does not raise, it defaults to
`atlas`** — so on this repo the sweep computed `agent-fix.yml`, which is a
`workflow_call` reusable with no `workflow_dispatch` trigger, `gh workflow
run` exited non-zero and the sweep raised. Every other step that runs
`reconcile.py` sets `REPO_SLUG`; this one was written without it.

Three layers, matching the three things the card asks for:

1. **The lint** — every workflow step that invokes `reconcile.py` provides
   every variable in `reconcile.REQUIRED_ENV`. Derived from the workflow
   files and the helper's own declaration, never a list restated here, so a
   step added next month is checked the moment it exists. This is what makes
   a missing variable fail CI rather than a Thursday evening.
2. **The dispatch** — a fixture PR marked DIRTY is dispatched to the fix
   workflow by the SHIPPED shell, run verbatim against the real
   `unstick_conflicts`. The workflow name it dispatches is the assertion:
   `self-agent-fix.yml` only if `REPO_SLUG` actually reached the helper.
3. **The failure mode** — when the sweep cannot run, the step says so ON the
   conflicted pull requests, with the recovery a person would need, and
   still fails the run. A dispatcher that fails quietly into "no dispatch"
   is the whole incident.

Run: python3 -m pytest tests/test_linear_sync_workflow.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"
LINEAR_SYNC = WORKFLOWS / "linear-sync.yml"

sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "test")

import pipeline_act  # noqa: E402
import reconcile  # noqa: E402

#: The step under repair, by its own name in the workflow.
CONFLICT_STEP = "Dispatch fix agents for newly conflicted PRs"

#: The act the crash path posts. Declared in config/pipeline-acts.json.
CRASH_ACT = "conflict-sweep-unavailable"

#: The fenced region of that step, executed verbatim by the scenario tests.
MARKER_OPEN = "# >>> DRE-3042 conflict sweep"
MARKER_CLOSE = "# <<< DRE-3042 conflict sweep"

#: The incident, as a fixture: one conflicted agent PR among healthy ones.
CONFLICTED_PR = 244
OPEN_PRS = [
    {"number": 240, "headRefName": "agent/DRE-3016-planner-score",
     "mergeStateStatus": "CLEAN"},
    {"number": CONFLICTED_PR, "headRefName": "agent/DRE-3029-oneoff-classifier",
     "mergeStateStatus": "DIRTY"},
    # A DIRTY branch no card owns: the fix loop does not act on it and neither
    # does the notice — `unstick_conflicts` asks `card_branch` first.
    {"number": 245, "headRefName": "dependabot/pip/urllib3-2.5.0",
     "mergeStateStatus": "DIRTY"},
]

RUN_URL = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/33829082382"


# --------------------------------------------------------------------------- #
# the lint: a workflow step is a call site                                     #
# --------------------------------------------------------------------------- #

#: `VAR=` opening its own line (`export VAR=` too) — how the sweep's other
#: steps derive a variable in shell rather than declaring it in `env:`.
_ASSIGNMENT = re.compile(r"(?m)^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=")

#: `VAR=value python3 …` — the per-command prefix form (linear-sync's merge
#: step and plan.yml both pass REPO/REPO_SLUG that way).
_PREFIXED = re.compile(r"([A-Z][A-Z0-9_]*)=")


def _invocations(run: str) -> list:
    """Every logical line of `run` that EXECUTES reconcile.py.

    Continuations folded, comments dropped: three workflows discuss the helper
    in prose, and a lint that reads a sentence about a call site as a call site
    reports findings nobody can fix.
    """
    folded = run.replace("\\\n", " ")
    return [
        line.strip()
        for line in folded.splitlines()
        if "reconcile.py" in line and not line.strip().startswith("#")
    ]


def call_sites(doc: dict, workflow: str) -> list:
    """`(workflow, step, provided vars, invocations)` per reconcile.py step."""
    out = []
    top = set(doc.get("env") or {})
    for job in (doc.get("jobs") or {}).values():
        job_env = set((job or {}).get("env") or {})
        for step in (job or {}).get("steps") or []:
            run = step.get("run")
            if not isinstance(run, str):
                continue
            lines = _invocations(run)
            if not lines:
                continue
            provided = top | job_env | set(step.get("env") or {})
            provided |= set(_ASSIGNMENT.findall(run))
            for line in lines:
                provided |= set(_PREFIXED.findall(line))
            out.append((workflow, step.get("name") or "<unnamed>", provided, lines))
    return out


def shipped_call_sites() -> list:
    out = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        out.extend(call_sites(yaml.safe_load(path.read_text()), path.name))
    return out


def missing_env(sites: list) -> dict:
    """`{"workflow / step": [missing vars]}` — the lint's whole finding set."""
    found = {}
    for workflow, step, provided, _ in sites:
        absent = [v for v in reconcile.REQUIRED_ENV if v not in provided]
        if absent:
            found[f"{workflow} / {step}"] = absent
    return found


class ReconcileCallSitesDeclareTheirArguments(unittest.TestCase):
    """The lint. A missing variable fails this build, not a live sweep."""

    def test_the_helper_declares_what_a_caller_must_pass(self):
        """The required set is the HELPER's, so the lint cannot drift from it.

        REPO_SLUG is the one that matters and the one that hides: REPO raises
        on absence, REPO_SLUG defaults, and a wrong default is silent.
        """
        self.assertIn("REPO_SLUG", reconcile.REQUIRED_ENV)
        self.assertIn("REPO", reconcile.REQUIRED_ENV)

    def test_every_shipped_call_site_provides_every_required_variable(self):
        self.assertEqual({}, missing_env(shipped_call_sites()))

    def test_the_conflict_sweep_is_one_of_the_call_sites(self):
        """A lint that finds no call sites passes vacuously."""
        sites = shipped_call_sites()
        self.assertIn(CONFLICT_STEP, [step for _, step, _, _ in sites])
        self.assertGreaterEqual(len(sites), 4, [s[:2] for s in sites])

    def test_a_call_site_that_drops_a_variable_is_a_finding(self):
        """The incident, replayed against the lint: strip REPO_SLUG back out of
        the shipped step and the build must go red naming it."""
        doc = yaml.safe_load(LINEAR_SYNC.read_text())
        stripped = 0
        for job in doc["jobs"].values():
            for step in job.get("steps") or []:
                if step.get("name") != CONFLICT_STEP:
                    continue
                (step.get("env") or {}).pop("REPO_SLUG", None)
                step["run"] = "\n".join(
                    line for line in step["run"].splitlines()
                    if not re.match(r"\s*(export\s+)?REPO_SLUG\b", line)
                )
                stripped += 1
        self.assertEqual(1, stripped, "the conflict-sweep step was not found")
        found = missing_env(call_sites(doc, "linear-sync.yml"))
        self.assertEqual(
            {f"linear-sync.yml / {CONFLICT_STEP}": ["REPO_SLUG"]}, found
        )


class TheDefaultSlugIsWhatMadeItSilent(unittest.TestCase):
    """Why the absent variable crashed instead of being caught: the helper
    defaults it, and on THIS repo the default names a workflow that cannot be
    dispatched at all."""

    def _fix_workflow(self, **env) -> str:
        proc = subprocess.run(  # nosec B603 — fixed argv, test-local script
            [sys.executable, "-c",
             "import reconcile; print(reconcile.fix_workflow())"],
            capture_output=True, text=True,
            env={
                "PATH": os.environ["PATH"],
                "PYTHONPATH": str(SCRIPTS),
                "REPO": "dreadnought-foundry/bureau-pipeline",
                "LINEAR_API_KEY": "test-key",
                **env,
            },
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        return proc.stdout.strip()

    def test_without_repo_slug_the_helper_names_the_wrong_workflow(self):
        self.assertEqual("agent-fix.yml", self._fix_workflow())

    def test_with_repo_slug_the_helper_names_this_repo_s_stub(self):
        self.assertEqual(
            "self-agent-fix.yml", self._fix_workflow(REPO_SLUG="bureau-pipeline")
        )

    def test_the_workflow_the_default_names_cannot_be_dispatched(self):
        """`gh workflow run agent-fix.yml` on this repo is not a near miss, it
        is an error: the file is a reusable with no dispatch trigger. That is
        the non-zero exit the step died on."""
        triggers = yaml.safe_load((WORKFLOWS / "agent-fix.yml").read_text())[True]
        self.assertIn("workflow_call", triggers)
        self.assertNotIn("workflow_dispatch", triggers)
        self.assertIn(
            "workflow_dispatch",
            yaml.safe_load((WORKFLOWS / "self-agent-fix.yml").read_text())[True],
        )


# --------------------------------------------------------------------------- #
# the scenario harness: the shipped shell, run                                 #
# --------------------------------------------------------------------------- #
#: A reconcile.py that drives the REAL sweep off a fixture PR listing and
#: records what it dispatches — so the workflow name below is computed by the
#: shipped helper from the environment the shipped shell built.
DRIVER = textwrap.dedent(f"""\
    import json, os, sys
    sys.path.insert(0, {str(SCRIPTS)!r})
    import reconcile

    def fake_gh(*args):
        if args[:2] == ("pr", "list"):
            return open(os.environ["PR_FIXTURE"], encoding="utf-8").read()
        raise AssertionError("unexpected gh read: %r" % (args,))

    def record(*args):
        with open(os.environ["DISPATCH_LOG"], "a", encoding="utf-8") as fh:
            fh.write(json.dumps({{
                "args": list(args),
                "repo_slug_in_env": os.environ.get("REPO_SLUG"),
                "repo_slug_seen_by_helper": reconcile.REPO_SLUG,
                "fix_workflow": reconcile.fix_workflow(),
            }}) + "\\n")

    reconcile.gh = fake_gh
    reconcile.gh_dispatch = record
    reconcile.fix_lane_state = lambda: reconcile.FixLane({{}}, 0)
    reconcile.card_parked_for_human = lambda card: False
    reconcile.run(sys.argv[1:])
    """)

#: A reconcile.py that cannot run at all — the failure the step must announce.
BROKEN = "import sys\nsys.stderr.write('boom\\n')\nsys.exit(1)\n"

#: A `gh` that answers the two reads the crash path makes and records posts.
GH_STUB = """#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
if argv[:2] == ["pr", "list"]:
    sys.stdout.write(open(os.environ["PR_FIXTURE"], encoding="utf-8").read())
    sys.exit(0)
if argv[:2] == ["pr", "view"]:
    existing = json.load(open(os.environ["PR_COMMENTS"], encoding="utf-8"))
    print(json.dumps({"comments": [
        {"body": b} for b in existing.get(argv[2], [])]}))
    sys.exit(0)
if argv[:2] == ["pr", "comment"]:
    body = None
    if "--body-file" in argv:
        body = open(argv[argv.index("--body-file") + 1], encoding="utf-8").read()
    with open(os.environ["COMMENT_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"pr": argv[2], "body": body}) + "\\n")
    sys.exit(0)
sys.stderr.write("unexpected gh call: %r\\n" % (argv,))
sys.exit(9)
"""


def conflict_step() -> dict:
    for job in yaml.safe_load(LINEAR_SYNC.read_text())["jobs"].values():
        for step in job.get("steps") or []:
            if step.get("name") == CONFLICT_STEP:
                return step
    raise AssertionError(f"{CONFLICT_STEP!r} is gone from linear-sync.yml")


def fenced_block() -> str:
    """The shipped shell between the DRE-3042 markers, verbatim."""
    lines = conflict_step()["run"].splitlines()
    start = next(i for i, ln in enumerate(lines) if MARKER_OPEN in ln)
    end = next(i for i, ln in enumerate(lines) if MARKER_CLOSE in ln)
    assert start < end, "the conflict-sweep markers are out of order"
    return "\n".join(lines[start + 1:end])


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


class SweepScenario:
    """One execution of the shipped shell against a chosen reconcile.py."""

    def __init__(self, td: str, reconcile_source: str, *, comments=None):
        self.dir = Path(td)
        base = self.dir / ".bureau-pipeline"
        shutil.copytree(SCRIPTS, base / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "config", base / "config")
        (base / "scripts" / "reconcile.py").write_text(reconcile_source)

        self.bin = self.dir / "bin"
        self.bin.mkdir()
        # No real waiting, and a python3 that is THIS interpreter.
        _executable(self.bin / "sleep", "#!/bin/sh\nexit 0\n")
        _executable(self.bin / "python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        _executable(self.bin / "gh", GH_STUB)

        self.prs = self.dir / "prs.json"
        self.prs.write_text(json.dumps(OPEN_PRS))
        self.comments = self.dir / "comments.json"
        self.comments.write_text(json.dumps(comments or {}))
        self.comment_log = self.dir / "posted.jsonl"
        self.dispatch_log = self.dir / "dispatched.jsonl"

    def run(self, repository="dreadnought-foundry/bureau-pipeline"):
        script = self.dir / "sweep.sh"
        script.write_text(fenced_block())
        # `bash -e`, the runner's own flags for a `run:` block. No REPO_SLUG in
        # the environment on purpose: the step derives it or it is not there.
        self.proc = subprocess.run(  # nosec B603 — fixed argv, test-local script
            ["bash", "-e", str(script)],
            cwd=self.dir, capture_output=True, text=True,
            env={
                "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                "HOME": str(self.dir),
                "RUNNER_TEMP": str(self.dir),
                "GITHUB_REPOSITORY": repository,
                "REPO": repository,
                "RUN_URL": RUN_URL,
                "GH_TOKEN": "test",
                "GH_DISPATCH_TOKEN": "test",
                "LINEAR_API_KEY": "test-key",
                "PR_FIXTURE": str(self.prs),
                "PR_COMMENTS": str(self.comments),
                "COMMENT_LOG": str(self.comment_log),
                "DISPATCH_LOG": str(self.dispatch_log),
            },
        )
        return self.proc

    def _read(self, path: Path) -> list:
        if not path.exists():
            return []
        return [json.loads(ln) for ln in path.read_text().splitlines() if ln]

    @property
    def dispatched(self) -> list:
        return self._read(self.dispatch_log)

    @property
    def posted(self) -> list:
        return self._read(self.comment_log)


class AConflictedPrIsDispatched(unittest.TestCase):
    """The acceptance criterion, end to end: fixture PR → shipped shell →
    real `unstick_conflicts` → the fix workflow this repo actually has."""

    def test_the_conflicted_pr_is_dispatched_with_repo_slug_present(self):
        with tempfile.TemporaryDirectory() as td:
            scenario = SweepScenario(td, DRIVER)
            proc = scenario.run()
            dispatched = scenario.dispatched
        self.assertEqual(0, proc.returncode, proc.stderr)
        # Two, because the step sweeps twice — GitHub recomputes mergeability
        # lazily and the second pass is what catches a slow recompute. Each
        # pass is its own process, so the stubbed (always idle) fix lane lets
        # both through; in a live run the second sees the first in flight.
        self.assertEqual(2, len(dispatched), proc.stdout)
        for record in dispatched:
            self.assertEqual("bureau-pipeline", record["repo_slug_in_env"])
            self.assertEqual("bureau-pipeline", record["repo_slug_seen_by_helper"])
            self.assertEqual("self-agent-fix.yml", record["fix_workflow"])
            self.assertIn("self-agent-fix.yml", record["args"])
            self.assertIn(f"pr_number={CONFLICTED_PR}", record["args"])

    def test_a_product_repo_still_dispatches_its_own_fix_stub(self):
        """The derivation is the repo's, not a literal: the same shell on a
        product repo names `agent-fix.yml`, which is the stub THERE."""
        with tempfile.TemporaryDirectory() as td:
            scenario = SweepScenario(td, DRIVER)
            proc = scenario.run(repository="dreadnought-foundry/portico")
            dispatched = scenario.dispatched
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(dispatched)
        self.assertEqual("portico", dispatched[0]["repo_slug_in_env"])
        self.assertEqual("agent-fix.yml", dispatched[0]["fix_workflow"])

    def test_nothing_is_announced_when_the_sweep_works(self):
        """The crash path is a crash path. A healthy sweep posts nothing."""
        with tempfile.TemporaryDirectory() as td:
            scenario = SweepScenario(td, DRIVER)
            scenario.run()
            self.assertEqual([], scenario.posted)


class AFailedSweepIsNeverSilent(unittest.TestCase):
    """A step that dispatches fix agents must not fail quietly into "no
    dispatch" — a DIRTY pull request emits no workflow events, so the notice
    is the only thing that can reach it."""

    def test_the_conflicted_pr_gets_the_hand_recovery_note(self):
        with tempfile.TemporaryDirectory() as td:
            scenario = SweepScenario(td, BROKEN)
            proc = scenario.run()
            posted = scenario.posted
        self.assertNotEqual(0, proc.returncode, "a failed sweep must fail the run")
        self.assertEqual(1, len(posted), posted)
        self.assertEqual(str(CONFLICTED_PR), posted[0]["pr"])
        body = posted[0]["body"]
        self.assertTrue(
            body.startswith(f"🚨 {pipeline_act.tag(CRASH_ACT)}:"),
            body,
        )
        self.assertIn(RUN_URL, body)
        self.assertIn(pipeline_act.trailer(CRASH_ACT), body)

    def test_a_branch_no_card_owns_is_not_noticed(self):
        """`unstick_conflicts` only ever acts on `agent/` branches, so the
        notice standing in for it must not speak on any other."""
        with tempfile.TemporaryDirectory() as td:
            scenario = SweepScenario(td, BROKEN)
            scenario.run()
            self.assertEqual(
                [str(CONFLICTED_PR)], [p["pr"] for p in scenario.posted]
            )

    def test_the_notice_is_posted_once_per_pull_request(self):
        """The tag is the idempotency key, as it is for every other receipt
        this pipeline posts — two merges in a row must not stack notices."""
        existing = {
            str(CONFLICTED_PR): [
                f"🚨 {pipeline_act.tag(CRASH_ACT)}: already said, an hour ago"
            ]
        }
        with tempfile.TemporaryDirectory() as td:
            scenario = SweepScenario(td, BROKEN, comments=existing)
            proc = scenario.run()
            self.assertEqual([], scenario.posted)
        self.assertNotEqual(0, proc.returncode)


class TheCrashNoticeIsADeclaredAct(unittest.TestCase):
    """Every receipt this pipeline posts composes through the one writer
    (DRE-2826), and this one is no exception."""

    def test_the_act_is_declared(self):
        self.assertIn(CRASH_ACT, pipeline_act.acts())
        self.assertEqual("hold", pipeline_act.kind(CRASH_ACT))

    def test_the_step_composes_through_the_writer(self):
        self.assertIn(
            f"pipeline_act.py receipt {CRASH_ACT}", conflict_step()["run"]
        )

    def test_the_registry_is_self_consistent(self):
        self.assertEqual([], pipeline_act.problems())


if __name__ == "__main__":
    unittest.main()
