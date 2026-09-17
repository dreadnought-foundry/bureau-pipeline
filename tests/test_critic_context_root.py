"""DRE-4158: the critic assembles its context from where the checkout now lives.

PR #425 (DRE-3226) merged to `main` at 09:52 PT on 2026-09-17. It moves the
pipeline checkout out of the pull request's working tree to
`$RUNNER_TEMP/bureau-pipeline` and exports that as `$PIPELINE_DIR`. The
"Assemble critic context" step was updated to RUN `assemble_context.py` from
there, but it passed no `--root`, and the script's default root is the
workspace-relative `.bureau-pipeline` — which after the move holds only the
stub `.github/actions`. From that merge on, every critic run on a
bureau-pipeline pull request (the one repo reviewed by `qa-review.yml@main`)
died in that step (run 35253052110, attempt 2, 11:54 PT):

    FileNotFoundError: [Errno 2] No such file or directory:
        '.bureau-pipeline/standards/comms.md'

and the pull request got "QA Critic could not run (infra error)".

Why nothing caught it, and what that means for the tests here. DRE-3226's
guard looks for the literal `.bureau-pipeline/scripts/` in a `run:` block. The
stale path in this defect appears NOWHERE in the workflow — it is a default
inside the script — so no reading of the YAML's text could see it. Two tests
follow from that:

  * a BEHAVIOURAL one: lay a temp dir out the way the runner looks after the
    move, and execute the assemble step's real `run:` block, taken from the
    workflow, with `PIPELINE_DIR` set. On the `main` this card was filed
    against it fails with the production error above;
  * a SHAPE guard that checks for the ABSENCE of `--root "$PIPELINE_DIR"` on
    every `assemble_context.py` invocation in a job that performs the move,
    and that no step after the move names the in-tree path at all, outside
    two exceptions pinned by name below.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"

#: Where `actions/checkout` plants the pipeline, and what is left of it in
#: the working tree after the move.
IN_TREE = ".bureau-pipeline"

#: The ONLY things a step after the move may address under the in-tree path.
#: Each is there on purpose; anything else is pipeline content that now lives
#: at $PIPELINE_DIR and reading it in-tree is this card's defect.
ALLOWED_IN_TREE = {
    # The stub the move step copies back. A `uses: ./…` local action is
    # resolved under $GITHUB_WORKSPACE and `uses:` admits no expression, so
    # the two composite actions this job runs (setup-node-cached,
    # install-claude-code) cannot be addressed any other way. NODE_ACTION
    # probes the same stub for the file before `uses:` is reached.
    "the stub actions directory": IN_TREE + "/.github/actions",
    # Not pipeline content at all: the workflow WRITES this file, and the
    # agent prompt names it — one path across every workflow
    # (tests/test_assemble_context_wiring.py). The durable copy is kept at
    # $PIPELINE_DIR/agent-context.md and restored from there before a retry.
    "the agent context the workflow itself writes": IN_TREE + "/agent-context.md",
}

_ROOT_AT_PIPELINE_DIR = re.compile(r"""--root[ =]+["']?\$\{?PIPELINE_DIR\}?["']?(?=\s|$)""")
_IN_TREE_REF = re.compile(r"(?<![\w$])" + re.escape(IN_TREE) + r"(?![\w-])(/[^\s\"'`)]*)?")


def _jobs() -> dict:
    return yaml.safe_load(QA_REVIEW.read_text())["jobs"]


def _executable(run: str) -> str:
    """A `run:` block as the shell sees it: no comments, continuations joined."""
    lines = [ln for ln in run.splitlines() if not ln.lstrip().startswith("#")]
    return re.sub(r"\\\n\s*", " ", "\n".join(lines))


def _move_index(steps: list[dict]) -> int | None:
    """Index of the step that moves the checkout and exports PIPELINE_DIR."""
    for i, step in enumerate(steps):
        run = step.get("run") or ""
        if "PIPELINE_DIR=" in run and "GITHUB_ENV" in run:
            return i
    return None


def _jobs_that_move() -> dict[str, tuple[int, list[dict]]]:
    found = {}
    for name, job in _jobs().items():
        steps = job.get("steps") or []
        at = _move_index(steps)
        if at is not None:
            found[name] = (at, steps)
    return found


def _label(i: int, step: dict) -> str:
    return f"step {i} ({step.get('name') or step.get('id') or step.get('uses')})"


def assemble_calls_without_the_moved_root(run: str) -> list[str]:
    """Every `assemble_context.py assemble|paths` command in a run block that
    does not pass `--root "$PIPELINE_DIR"`."""
    bad = []
    for command in _executable(run).splitlines():
        if not re.search(r"assemble_context\.py\s+(assemble|paths)\b", command):
            continue
        if not _ROOT_AT_PIPELINE_DIR.search(command):
            bad.append(command.strip())
    return bad


def stale_in_tree_reads(step: dict) -> list[str]:
    """Every mention of the in-tree path in a step — its `run:`, `env:`,
    `with:` and `uses:` alike — that is not one of the named exceptions."""
    fields = dict(step)
    lines = _executable(fields.pop("run", None) or "").splitlines()
    lines += yaml.safe_dump(fields, width=10**6).splitlines()
    bad = []
    for line in lines:
        for match in _IN_TREE_REF.finditer(line):
            ref = match.group(0).rstrip("/.,;:")
            if any(ref == ok or ref.startswith(ok + "/")
                   for ok in ALLOWED_IN_TREE.values()):
                continue
            if ref == IN_TREE and re.match(
                    r"\s*mkdir -p\s+[\"']?(\$GITHUB_WORKSPACE/)?"
                    + re.escape(IN_TREE) + r"[\"']?\s*$", line):
                # Making sure the directory the context is written into
                # exists reads nothing from it.
                continue
            bad.append(f"{ref!r} in: {line.strip()[:160]}")
    return bad


def _lay_out_the_runner_after_the_move(td: Path) -> tuple[Path, Path]:
    """(workspace, pipeline_dir) exactly as the move step leaves them: the
    pipeline's content at $RUNNER_TEMP/bureau-pipeline, and in the working
    tree nothing but the stub `.github/actions`."""
    workspace = td / "work" / "bureau-pipeline" / "bureau-pipeline"
    pipeline_dir = td / "work" / "_temp" / "bureau-pipeline"
    pipeline_dir.mkdir(parents=True)
    for content in ("scripts", "standards", "briefs"):
        (pipeline_dir / content).symlink_to(ROOT / content)
    stub = workspace / IN_TREE / ".github" / "actions" / "install-claude-code"
    stub.mkdir(parents=True)
    (stub / "action.yml").write_text("name: install\n")
    return workspace, pipeline_dir


class AssembleStepRunsAfterTheMoveTest(unittest.TestCase):
    """The step's own command line, executed where the runner executes it."""

    def _run_ctx_step(self, td: Path):
        workspace, pipeline_dir = _lay_out_the_runner_after_the_move(td)
        steps = _jobs()["review"]["steps"]
        ctx = next(s for s in steps if s.get("id") == "ctx")
        self.assertNotIn("${{", ctx["run"],
                         "an unresolved GitHub expression would reach the shell")
        script = td / "ctx.sh"
        # `bash -e` is what Actions gives a `run:` step with no `shell:`.
        script.write_text("set -e\n" + ctx["run"])
        env = dict(os.environ)
        env.update({
            "PIPELINE_DIR": str(pipeline_dir),
            "GITHUB_WORKSPACE": str(workspace),
            "RUNNER_TEMP": str(pipeline_dir.parent),
        })
        proc = subprocess.run(["bash", str(script)], cwd=workspace, env=env,
                              capture_output=True, text=True)
        return proc, workspace, pipeline_dir

    def test_it_produces_the_critic_context(self):
        with tempfile.TemporaryDirectory() as raw:
            proc, workspace, pipeline_dir = self._run_ctx_step(Path(raw))
            self.assertEqual(
                proc.returncode, 0,
                "the assemble step died the way it does in production:\n"
                + proc.stderr,
            )
            in_tree = workspace / IN_TREE / "agent-context.md"
            self.assertTrue(in_tree.exists(),
                            "the agent is told to read this file and it is not there")
            text = in_tree.read_text()
            self.assertIn("===== BEGIN standards/comms.md =====", text)
            self.assertIn("===== BEGIN briefs/critic.md =====", text)
            # DRE-3226's rule rides the brief; an empty context would lose it.
            self.assertIn("git stash -u", text)
            durable = pipeline_dir / "agent-context.md"
            self.assertTrue(durable.exists(),
                            "the retry restores from this copy and it was never kept")
            self.assertEqual(durable.read_text(), text)

    def test_it_reads_nothing_from_the_working_tree(self):
        # The working tree on a bureau-pipeline PR IS a pipeline checkout at
        # the PR's head. The critic's rules must come from the pinned channel
        # at $PIPELINE_DIR, never from the code under review — so a decoy
        # planted at the old default root must not reach the context.
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            decoy = (td / "work" / "bureau-pipeline" / "bureau-pipeline"
                     / IN_TREE / "standards")
            decoy.mkdir(parents=True)
            (decoy / "comms.md").write_text("DECOY FROM THE WORKING TREE\n")
            proc, workspace, _ = self._run_ctx_step(td)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            text = (workspace / IN_TREE / "agent-context.md").read_text()
            self.assertNotIn("DECOY FROM THE WORKING TREE", text)


class NothingAfterTheMoveReadsTheOldPathTest(unittest.TestCase):
    """The workflow-shape guard, with its exceptions named."""

    def test_the_move_is_where_this_guard_thinks_it_is(self):
        # If the move is ever renamed out of this guard's sight it would pass
        # by checking nothing.
        moving = _jobs_that_move()
        self.assertEqual(
            sorted(moving), ["review"],
            "qa-review.yml's jobs that move the checkout changed — every one "
            "of them is covered below, but read this test before trusting it",
        )
        at, steps = moving["review"]
        self.assertGreater(len(steps) - at - 1, 20,
                           "the guard is looking at almost no steps")

    def test_every_assemble_call_after_the_move_reads_from_pipeline_dir(self):
        offenders, seen = [], 0
        for job, (at, steps) in _jobs_that_move().items():
            for i, step in enumerate(steps[at + 1:], start=at + 1):
                run = step.get("run") or ""
                if "assemble_context.py" not in _executable(run):
                    continue
                seen += 1
                offenders += [f"{job} {_label(i, step)}: {cmd}"
                              for cmd in assemble_calls_without_the_moved_root(run)]
        self.assertGreater(seen, 0, "no assemble step found — the guard is blind")
        self.assertEqual(
            offenders, [],
            'assemble_context.py defaults --root to the in-tree path, which '
            'the move emptied; pass --root "$PIPELINE_DIR":\n'
            + "\n".join(offenders),
        )

    def test_removing_the_root_is_caught(self):
        # The card's criterion, without editing the workflow: the step as
        # written today with its --root taken away must be flagged.
        ctx = next(s for s in _jobs()["review"]["steps"] if s.get("id") == "ctx")
        stripped = _ROOT_AT_PIPELINE_DIR.sub("", _executable(ctx["run"]))
        self.assertTrue(assemble_calls_without_the_moved_root(stripped),
                        "the guard cannot see a missing --root")
        for wrong in ('--root .bureau-pipeline', '--root "$GITHUB_WORKSPACE"'):
            cmd = f'python3 "$PIPELINE_DIR"/scripts/assemble_context.py assemble critic {wrong}'
            self.assertTrue(assemble_calls_without_the_moved_root(cmd), wrong)

    def test_no_step_after_the_move_names_the_in_tree_path(self):
        offenders = []
        for job, (at, steps) in _jobs_that_move().items():
            for i, step in enumerate(steps[at + 1:], start=at + 1):
                offenders += [f"{job} {_label(i, step)}: {hit}"
                              for hit in stale_in_tree_reads(step)]
        allowed = "\n".join(f"  - {why}: {path}"
                            for why, path in ALLOWED_IN_TREE.items())
        self.assertEqual(
            offenders, [],
            "a step after the move reads pipeline content from the working "
            "tree, where it no longer is. Allowed, by name:\n" + allowed
            + "\nFound:\n" + "\n".join(offenders),
        )

    def test_the_guard_flags_pipeline_content_and_passes_the_exceptions(self):
        for stale in (
            {"run": "cat .bureau-pipeline/standards/comms.md"},
            {"run": "python3 .bureau-pipeline/scripts/linear_ops.py x"},
            {"env": {"CFG": ".bureau-pipeline/config/models.yaml"}},
            {"run": 'cat "$GITHUB_WORKSPACE/.bureau-pipeline/briefs/critic.md"'},
            {"run": "ls .bureau-pipeline"},
        ):
            self.assertTrue(stale_in_tree_reads(stale), stale)
        for fine in (
            {"uses": "./.bureau-pipeline/.github/actions/install-claude-code"},
            {"env": {"NODE_ACTION":
                     ".bureau-pipeline/.github/actions/setup-node-cached/action.yml"}},
            {"run": "wc -l < .bureau-pipeline/agent-context.md"},
            {"run": 'mkdir -p "$GITHUB_WORKSPACE/.bureau-pipeline"\n'
                    'cp -f "$PIPELINE_DIR/agent-context.md" '
                    '"$GITHUB_WORKSPACE/.bureau-pipeline/agent-context.md"'},
            {"run": "# python3 .bureau-pipeline/scripts/old.py — history\ntrue"},
            {"run": 'python3 "$PIPELINE_DIR"/scripts/linear_ops.py x'},
        ):
            self.assertEqual(stale_in_tree_reads(fine), [], fine)


if __name__ == "__main__":
    unittest.main()
