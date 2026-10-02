"""A requeued run resumes the dead run's branch (DRE-4368).

When a run died at the turn ceiling its branch — often carrying finished work
— was ignored, and the requeued run started from a clean checkout of the
default branch. `scripts/resume_branch.py decide` now looks for the dead run's
`agent/<CARD>-*` branch before the agent starts and answers `resume=true` only
when that branch has commits of its own AND merges cleanly onto the default
branch; every other case starts clean and says which.

A pre-existing branch also changes what a pushed branch PROVES. Four steps in
agent-task.yml read "this card has a branch" as "this run's agent ran": the two
rate-limit retry decisions, the result gate and the Report step. With the dead
run's branch already on GitHub a run that died before its agent would look
alive, so `resume_branch.py proof` counts a branch only when its tip moved past
the tip it had before this run (`steps.resume.outputs.sha`).

Every git case below is a real repository built in the test — a bare origin
and a clone, exactly the shape actions/checkout leaves on a runner — never a
canned listing.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "resume_branch.py"
TDD_SCRIPT = ROOT / "scripts" / "check_tdd_commits.py"
WORKFLOW = ROOT / ".github" / "workflows" / "agent-task.yml"
sys.path.insert(0, str(ROOT / "scripts"))

import step_shell  # noqa: E402

CARD = "DRE-7"

# One identity, no user or system config: a developer's global git config
# (signing, hooks, a different default branch) must not reach these repos.
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _env(extra=None) -> dict:
    env = dict(os.environ)
    env.update(GIT_ENV)
    env.update(extra or {})
    return env


def git(cwd, *args, when=None) -> str:
    """Run git in `cwd`; `when` pins both dates so "newest" is deterministic."""
    extra = {}
    if when is not None:
        stamp = f"{when} +0000"
        extra = {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=_env(extra), check=True,
        capture_output=True, text=True,
    ).stdout


class Scratch:
    """A bare origin plus a working clone of it — the runner's checkout."""

    def __init__(self, td: str):
        self.td = td
        self.origin = os.path.join(td, "origin.git")
        self.seed = os.path.join(td, "seed")
        self.work = os.path.join(td, "work")
        git(td, "init", "--bare", "-b", "main", self.origin)
        git(td, "clone", self.origin, self.seed)
        self.write(self.seed, "README.md", "hello\n")
        self.write(self.seed, "scripts/app.py", "VALUE = 1\n")
        self.commit(self.seed, "initial", when=1_700_000_000)
        git(self.seed, "push", "origin", "HEAD:main")

    @staticmethod
    def write(repo, path, text):
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    @staticmethod
    def commit(repo, subject, when=1_700_000_100):
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", subject, when=when)

    def branch(self, name, files, subject="work", when=1_700_000_100, base="origin/main"):
        """Push a branch off `base` carrying one commit that writes `files`."""
        git(self.seed, "fetch", "-q", "origin")
        git(self.seed, "checkout", "-q", "-B", name, base)
        for path, text in files.items():
            self.write(self.seed, path, text)
        self.commit(self.seed, subject, when=when)
        git(self.seed, "push", "-q", "-f", "origin", f"{name}:refs/heads/{name}")
        git(self.seed, "checkout", "-q", "--detach", "origin/main")

    def empty_branch(self, name):
        git(self.seed, "fetch", "-q", "origin")
        git(self.seed, "push", "-q", "origin", f"origin/main:refs/heads/{name}")

    def advance_main(self, files, subject="main moves", when=1_700_000_200):
        git(self.seed, "fetch", "-q", "origin")
        git(self.seed, "checkout", "-q", "-B", "main", "origin/main")
        for path, text in files.items():
            self.write(self.seed, path, text)
        self.commit(self.seed, subject, when=when)
        git(self.seed, "push", "-q", "origin", "main:main")
        git(self.seed, "checkout", "-q", "--detach", "origin/main")

    def clone(self):
        """The runner's checkout, taken AFTER the branches exist."""
        git(self.td, "clone", "-q", self.origin, self.work)
        return self.work

    def tip(self, ref, repo=None):
        return git(repo or self.work, "rev-parse", ref).strip()


def run_script(cwd, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=cwd, env=_env(),
        capture_output=True, text=True, check=False,
    )


def read_outputs(path) -> dict:
    """Parse a `$GITHUB_OUTPUT` file, heredoc form included."""
    out, lines, i = {}, Path(path).read_text().splitlines(), 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            key, delim = line.split("<<", 1)
            i += 1
            body = []
            while lines[i] != delim:
                body.append(lines[i])
                i += 1
            out[key] = "\n".join(body)
        elif "=" in line:
            key, value = line.split("=", 1)
            out[key] = value
        i += 1
    return out


def decide(cwd, card=CARD, default="main"):
    """Run `decide` the way the workflow step does; return (proc, outputs, note)."""
    gh_out = os.path.join(os.path.dirname(cwd), "gh-output.txt")
    note = os.path.join(os.path.dirname(cwd), "resume-note.md")
    snapshot = os.path.join(os.path.dirname(cwd), "resume-branches.json")
    for path in (gh_out, note, snapshot):
        if os.path.exists(path):
            os.remove(path)
    proc = run_script(
        cwd, "decide", "--card", card, "--default", default,
        "--github-output", gh_out, "--note", note, "--snapshot", snapshot,
    )
    outputs = read_outputs(gh_out) if os.path.exists(gh_out) else {}
    text = Path(note).read_text() if os.path.exists(note) else ""
    return proc, outputs, text


# --------------------------------------------------------------------------- #
# decide                                                                       #
# --------------------------------------------------------------------------- #


class DecideResumesTest(unittest.TestCase):
    def test_a_branch_with_commits_that_merges_cleanly_is_resumed(self):
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            s.branch(f"agent/{CARD}-resume-me",
                     {"tests/test_app.py": "def test_x(): pass\n"},
                     subject="test: RED for the card")
            work = s.clone()
            proc, out, _ = decide(work)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(out["resume"], "true")
            self.assertEqual(out["branch"], f"agent/{CARD}-resume-me")
            self.assertEqual(out["sha"], s.tip(f"origin/agent/{CARD}-resume-me"))

    def test_the_outputs_are_exactly_the_contract(self):
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            s.branch(f"agent/{CARD}-resume-me", {"tests/test_app.py": "x = 1\n"})
            _, out, _ = decide(s.clone())
            self.assertEqual(set(out), {"resume", "branch", "sha", "reason"})

    def test_the_note_says_what_is_already_there(self):
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            s.branch(f"agent/{CARD}-resume-me",
                     {"tests/test_app.py": "def test_x(): pass\n"},
                     subject="test: RED for the card")
            _, _, note = decide(s.clone())
            self.assertIn(f"agent/{CARD}-resume-me", note)
            self.assertIn("test: RED for the card", note)   # git log --oneline
            self.assertIn("tests/test_app.py", note)        # git diff --stat
            self.assertIn("not behind", note)

    def test_a_branch_behind_the_default_that_still_merges_is_resumed(self):
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            s.branch(f"agent/{CARD}-resume-me", {"tests/test_app.py": "x = 1\n"})
            s.advance_main({"docs/other.md": "unrelated\n"})
            _, out, note = decide(s.clone())
            self.assertEqual(out["resume"], "true")
            self.assertIn("behind main by 1 commit", note)

    def test_it_never_fails_the_step(self):
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            proc, _, _ = decide(s.clone())
            self.assertEqual(proc.returncode, 0, proc.stderr)


class DecideStartsCleanTest(unittest.TestCase):
    def test_no_card_branch(self):
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            proc, out, note = decide(s.clone())
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(out["resume"], "false")
            self.assertEqual(out["reason"], "no card branch")
            self.assertEqual(out["branch"], "")
            self.assertEqual(note, "")

    def test_another_cards_branch_is_not_this_cards(self):
        # DRE-7 is a string prefix of DRE-77 (the DRE-2025 bug, one layer up).
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            s.branch("agent/DRE-77-someone-else", {"tests/test_app.py": "x = 1\n"})
            _, out, _ = decide(s.clone())
            self.assertEqual(out["resume"], "false")
            self.assertEqual(out["reason"], "no card branch")

    def test_a_branch_with_no_commits_beyond_the_default(self):
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            s.empty_branch(f"agent/{CARD}-nothing-yet")
            _, out, _ = decide(s.clone())
            self.assertEqual(out["resume"], "false")
            self.assertEqual(out["reason"], "branch has no commits beyond main")

    def test_a_branch_that_conflicts_names_the_files(self):
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            s.branch(f"agent/{CARD}-conflicts", {"scripts/app.py": "VALUE = 2\n"})
            s.advance_main({"scripts/app.py": "VALUE = 3\n"})
            _, out, note = decide(s.clone())
            self.assertEqual(out["resume"], "false")
            self.assertEqual(out["reason"],
                             "branch conflicts with main on scripts/app.py")
            self.assertEqual(note, "")

    def test_unreadable_git_starts_clean(self):
        with tempfile.TemporaryDirectory() as td:
            not_a_repo = os.path.join(td, "work")
            os.makedirs(not_a_repo)
            proc, out, _ = decide(not_a_repo)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(out["resume"], "false")
            self.assertEqual(out["reason"], "git unreadable — starting clean")

    def test_an_unknown_default_branch_is_unreadable_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            s.branch(f"agent/{CARD}-resume-me", {"tests/test_app.py": "x = 1\n"})
            proc, out, _ = decide(s.clone(), default="no-such-branch")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(out["resume"], "false")
            self.assertEqual(out["reason"], "git unreadable — starting clean")


class DecideSeveralBranchesTest(unittest.TestCase):
    def test_the_newest_commit_wins_and_the_others_are_named(self):
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            # Alphabetical order and age disagree on purpose: `head -1` of a
            # sorted listing would pick the OLD one.
            s.branch(f"agent/{CARD}-a-older", {"tests/test_a.py": "a = 1\n"},
                     when=1_700_000_100)
            s.branch(f"agent/{CARD}-b-newer", {"tests/test_b.py": "b = 1\n"},
                     when=1_700_000_900)
            _, out, _ = decide(s.clone())
            self.assertEqual(out["resume"], "true")
            self.assertEqual(out["branch"], f"agent/{CARD}-b-newer")
            self.assertIn("newest of 2", out["reason"])
            self.assertIn(f"agent/{CARD}-a-older", out["reason"])

    def test_the_snapshot_records_every_card_branch_tip(self):
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            s.branch(f"agent/{CARD}-a-older", {"tests/test_a.py": "a = 1\n"})
            s.branch(f"agent/{CARD}-b-newer", {"tests/test_b.py": "b = 1\n"},
                     when=1_700_000_900)
            work = s.clone()
            decide(work)
            snap = json.loads(Path(td, "resume-branches.json").read_text())
            self.assertEqual(snap, {
                f"agent/{CARD}-a-older": s.tip(f"origin/agent/{CARD}-a-older"),
                f"agent/{CARD}-b-newer": s.tip(f"origin/agent/{CARD}-b-newer"),
            })


# --------------------------------------------------------------------------- #
# proof — a pre-existing branch is proof of THIS run only once it moved        #
# --------------------------------------------------------------------------- #


def proof(cwd, branch="", sha="", snapshot="", fallback=""):
    args = ["proof", "--card", CARD, "--branch", branch, "--sha", sha]
    if snapshot:
        args += ["--snapshot", snapshot]
    if fallback:
        args += ["--fallback", fallback]
    proc = run_script(cwd, *args)
    return proc


class ProofTest(unittest.TestCase):
    def _resumable(self, td):
        s = Scratch(td)
        name = f"agent/{CARD}-resume-me"
        s.branch(name, {"tests/test_app.py": "x = 1\n"})
        work = s.clone()
        _, out, _ = decide(work)
        return s, work, name, out

    def test_an_unmoved_pre_existing_branch_is_not_proof(self):
        with tempfile.TemporaryDirectory() as td:
            _, work, _, out = self._resumable(td)
            proc = proof(work, out["branch"], out["sha"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), "")

    def test_a_branch_this_run_pushed_to_is_proof(self):
        with tempfile.TemporaryDirectory() as td:
            s, work, name, out = self._resumable(td)
            git(work, "checkout", "-q", name)
            s.write(work, "scripts/app.py", "VALUE = 9\n")
            s.commit(work, "feat: the implementation")
            git(work, "push", "-q", "origin", name)
            proc = proof(work, out["branch"], out["sha"])
            self.assertEqual(proc.stdout.strip(), name)

    def test_a_local_commit_on_the_resumed_branch_is_proof(self):
        # The agent worked on the branch; whether its push reached GitHub is
        # the rescue's question, not this one.
        with tempfile.TemporaryDirectory() as td:
            s, work, name, out = self._resumable(td)
            git(work, "checkout", "-q", name)
            s.write(work, "scripts/app.py", "VALUE = 9\n")
            s.commit(work, "feat: the implementation")
            proc = proof(work, out["branch"], out["sha"])
            self.assertEqual(proc.stdout.strip(), name)

    def test_checking_the_branch_out_is_not_proof(self):
        with tempfile.TemporaryDirectory() as td:
            _, work, name, out = self._resumable(td)
            git(work, "checkout", "-q", name)
            proc = proof(work, out["branch"], out["sha"])
            self.assertEqual(proc.stdout.strip(), "")

    def test_a_new_branch_beside_an_unmoved_one_is_proof(self):
        # The conflicting-branch case: the run started clean and created its
        # own branch, and the dead run's branch still sits beside it.
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            s.branch(f"agent/{CARD}-a-dead", {"scripts/app.py": "VALUE = 2\n"})
            s.advance_main({"scripts/app.py": "VALUE = 3\n"})
            work = s.clone()
            _, out, _ = decide(work)
            self.assertEqual(out["resume"], "false")
            git(work, "checkout", "-q", "-b", f"agent/{CARD}-b-fresh", "origin/main")
            s.write(work, "tests/test_new.py", "y = 1\n")
            s.commit(work, "test: RED")
            git(work, "push", "-q", "origin", f"agent/{CARD}-b-fresh")
            snap = os.path.join(td, "resume-branches.json")
            proc = proof(work, out["branch"], out["sha"], snapshot=snap)
            self.assertEqual(proc.stdout.strip(), f"agent/{CARD}-b-fresh")

    def test_every_pre_existing_branch_in_the_snapshot_is_discounted(self):
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            s.branch(f"agent/{CARD}-a-older", {"tests/test_a.py": "a = 1\n"})
            s.branch(f"agent/{CARD}-b-newer", {"tests/test_b.py": "b = 1\n"},
                     when=1_700_000_900)
            work = s.clone()
            _, out, _ = decide(work)
            snap = os.path.join(td, "resume-branches.json")
            proc = proof(work, out["branch"], out["sha"], snapshot=snap)
            self.assertEqual(proc.stdout.strip(), "")

    def test_with_no_resume_decision_any_card_branch_is_proof(self):
        # The resume step skipped or never ran: exactly the read before.
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            s.branch(f"agent/{CARD}-pushed", {"tests/test_a.py": "a = 1\n"})
            proc = proof(s.clone())
            self.assertEqual(proc.stdout.strip(), f"agent/{CARD}-pushed")

    def test_unreadable_git_answers_the_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            not_a_repo = os.path.join(td, "work")
            os.makedirs(not_a_repo)
            proc = proof(not_a_repo, fallback=f"agent/{CARD}-legacy")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), f"agent/{CARD}-legacy")


# --------------------------------------------------------------------------- #
# A resumed history still passes the TDD commit check                          #
# --------------------------------------------------------------------------- #


class ResumedHistoryPassesTddCheckTest(unittest.TestCase):
    def _check(self, work):
        return subprocess.run(
            [sys.executable, str(TDD_SCRIPT), "origin/main", "HEAD"],
            cwd=work, env=_env(), capture_output=True, text=True, check=False,
        )

    def test_the_dead_runs_red_commit_still_comes_first(self):
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            name = f"agent/{CARD}-resume-me"
            # The dead run: RED, then part of the implementation.
            s.branch(name, {"tests/test_app.py": "def test_x(): assert False\n"},
                     subject="test: RED", when=1_700_000_100)
            git(s.seed, "checkout", "-q", "-B", name, f"origin/{name}")
            s.write(s.seed, "scripts/app.py", "VALUE = 2\n")
            s.commit(s.seed, "feat: half of it", when=1_700_000_150)
            git(s.seed, "push", "-q", "origin", name)
            git(s.seed, "checkout", "-q", "--detach", "origin/main")
            # main moves on while the card waits for its resume run.
            s.advance_main({"scripts/other.py": "OTHER = 1\n"})
            work = s.clone()
            _, out, _ = decide(work)
            self.assertEqual(out["resume"], "true")
            # The resume run: check out, merge the default in, finish.
            git(work, "checkout", "-q", name)
            git(work, "merge", "-q", "--no-edit", "origin/main", when=1_700_000_300)
            s.write(work, "scripts/app.py", "VALUE = 3\n")
            s.commit(work, "feat: the rest", when=1_700_000_400)
            proc = self._check(work)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_the_check_still_catches_code_first_on_the_same_shape(self):
        # The control: the same merged shape with the order reversed is red,
        # so the case above is green because of the order and nothing else.
        with tempfile.TemporaryDirectory() as td:
            s = Scratch(td)
            name = f"agent/{CARD}-code-first"
            s.branch(name, {"scripts/app.py": "VALUE = 2\n"},
                     subject="feat: code first")
            s.advance_main({"scripts/other.py": "OTHER = 1\n"})
            work = s.clone()
            git(work, "checkout", "-q", name)
            git(work, "merge", "-q", "--no-edit", "origin/main", when=1_700_000_300)
            s.write(work, "tests/test_app.py", "x = 1\n")
            s.commit(work, "test: late", when=1_700_000_400)
            proc = self._check(work)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)


# --------------------------------------------------------------------------- #
# agent-task.yml wiring                                                        #
# --------------------------------------------------------------------------- #


def _steps() -> list:
    return yaml.safe_load(step_shell.workflow_source(WORKFLOW))["jobs"]["execute"]["steps"]


def _step(step_id: str) -> dict:
    for step in _steps():
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"agent-task.yml has no step with id {step_id!r}")


def _index(step_id: str) -> int:
    for i, step in enumerate(_steps()):
        if step.get("id") == step_id:
            return i
    raise AssertionError(f"agent-task.yml has no step with id {step_id!r}")


PROMPT_IDS = ("claude", "claude_retry1", "claude_retry2")
# The four branch-as-proof reads the card names: both retry decisions, the
# result gate and the Report step.
PROOF_STEPS = ("retry1", "retry2", "Gate on agent result", "Report result to Linear")


def _proof_step(key: str) -> dict:
    for step in _steps():
        if step.get("id") == key or step.get("name") == key:
            return step
    raise AssertionError(f"agent-task.yml has no step {key!r}")


PROOF_RE = re.compile(
    r"^\s*(BRANCH=\$\(python3 \.bureau-pipeline/scripts/resume_branch\.py proof .*)$",
    re.M,
)


class ResumeStepWiringTest(unittest.TestCase):
    def test_the_step_runs_after_the_checkouts_and_before_select_model(self):
        steps = _steps()
        resume = _index("resume")
        checkouts = [i for i, s in enumerate(steps)
                     if str(s.get("uses", "")).startswith("actions/checkout@")]
        self.assertTrue(checkouts)
        self.assertGreater(resume, max(checkouts))
        self.assertLess(resume, _index("model"))

    def test_the_step_can_never_fail_a_build(self):
        self.assertIs(_step("resume").get("continue-on-error"), True)

    def test_the_step_calls_decide_with_the_contract_paths(self):
        step = _step("resume")
        text = step_shell.step_shell(step) + json.dumps(step.get("env", {}))
        self.assertIn("resume_branch.py decide", step_shell.step_shell(step))
        self.assertIn("--github-output", step_shell.step_shell(step))
        self.assertIn("$RUNNER_TEMP/resume-note.md", text)

    def test_every_prompt_copy_carries_the_resume_text(self):
        for step_id in PROMPT_IDS:
            with self.subTest(step=step_id):
                prompt = _step(step_id)["with"]["prompt"]
                self.assertIn("steps.resume.outputs.resume == 'true'", prompt)
                self.assertIn("steps.resume.outputs.branch", prompt)
                self.assertIn("steps.resume.outputs.reason", prompt)
                self.assertIn("$RUNNER_TEMP/resume-note.md", prompt)
                self.assertIn("Do not create a new branch.", prompt)

    def test_no_prompt_copy_unconditionally_creates_a_branch(self):
        unconditional = re.compile(
            r"^\s*1\. Create branch agent/.*off the default branch\.\s*$", re.M
        )
        for step_id in PROMPT_IDS:
            with self.subTest(step=step_id):
                prompt = _step(step_id)["with"]["prompt"]
                self.assertIsNone(unconditional.search(prompt))

    def test_the_heartbeat_names_the_resume(self):
        step = _step("inprogress")
        clause = step["env"]["RESUME_CLAUSE"]
        self.assertIn("format('resume={0}', steps.resume.outputs.branch)", clause)
        self.assertIn("format('resume=none ({0})'", clause)
        heartbeat = [line for line in step_shell.step_shell(step).splitlines()
                     if "model-attempt:" in line]
        self.assertEqual(len(heartbeat), 1)
        self.assertIn("$RESUME_CLAUSE", heartbeat[0])


class ProofWiringTest(unittest.TestCase):
    """The live statements, executed against a scratch repository."""

    def test_every_branch_as_proof_read_goes_through_proof(self):
        for key in PROOF_STEPS:
            with self.subTest(step=key):
                step = _proof_step(key)
                self.assertEqual(len(PROOF_RE.findall(step_shell.step_shell(step))), 1)
                env = step["env"]
                self.assertEqual(env["RESUME_BRANCH"],
                                 "${{ steps.resume.outputs.branch }}")
                self.assertEqual(env["RESUME_SHA"],
                                 "${{ steps.resume.outputs.sha }}")

    def test_every_site_uses_the_same_statement(self):
        stmts = {PROOF_RE.findall(step_shell.step_shell(_proof_step(k)))[0] for k in PROOF_STEPS}
        self.assertEqual(len(stmts), 1, stmts)

    def _run_sites(self, work, env):
        """Run the legacy lookup + the proof filter exactly as each step does."""
        results = []
        for key in PROOF_STEPS:
            run = step_shell.step_shell(_proof_step(key))
            lines = run.splitlines()
            start = next(i for i, l in enumerate(lines)
                         if l.strip().startswith("BRANCH=$(git branch -r"))
            end = next(i for i, l in enumerate(lines)
                       if PROOF_RE.match(l))
            script = "\n".join(l for l in lines[start:end + 1]
                               if not l.strip().startswith("#"))
            script += '\nprintf "%s" "$BRANCH"\n'
            proc = subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", script], cwd=work,
                env=_env(env), capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            results.append(proc.stdout.strip())
        return results

    def _layout(self, td):
        s = Scratch(td)
        name = f"agent/{CARD}-resume-me"
        s.branch(name, {"tests/test_app.py": "x = 1\n"})
        work = s.clone()
        os.symlink(ROOT, os.path.join(work, ".bureau-pipeline"))
        _, out, _ = decide(work)
        env = {
            "CARD": CARD,
            "RESUME_BRANCH": out["branch"],
            "RESUME_SHA": out["sha"],
            "RESUME_SNAPSHOT": os.path.join(td, "resume-branches.json"),
        }
        return s, work, name, env

    def test_a_run_that_died_before_its_agent_shows_no_branch(self):
        with tempfile.TemporaryDirectory() as td:
            _, work, _, env = self._layout(td)
            self.assertEqual(self._run_sites(work, env), [""] * len(PROOF_STEPS))

    def test_a_run_that_pushed_to_the_resumed_branch_shows_it(self):
        with tempfile.TemporaryDirectory() as td:
            s, work, name, env = self._layout(td)
            git(work, "checkout", "-q", name)
            s.write(work, "scripts/app.py", "VALUE = 9\n")
            s.commit(work, "feat: the implementation")
            git(work, "push", "-q", "origin", name)
            self.assertEqual(self._run_sites(work, env), [name] * len(PROOF_STEPS))


if __name__ == "__main__":
    unittest.main()
