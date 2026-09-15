"""One shared Claude Code install step, asserted, at every install site (DRE-3414).

When the Claude Code install leaves no binary, nothing notices at install
time. The agent step dies minutes later reporting `native binary not found` —
one turn, $0 — and that fingerprint is indistinguishable from a stale
credential or a model outage to everyone downstream. DRE-3416 was exactly
this: claude-code-action v1.0.218's Claude Code 2.1.265 installer exited
clean and left no launcher, and every Claude-running job in the fleet died
for 72 minutes reading like an auth failure.

The remedy is one composite action, `.github/actions/install-claude-code`,
that installs Claude Code and then PROVES the binary runs (`claude
--version`) before any model step starts. An installer's exit code is not the
thing we need; a launcher that answers is.

Two halves, and both are enforced here:

  * **the action's behaviour**, executed rather than grepped — the script out
    of action.yml is run against a stub installer, so "retries once" means the
    installer really ran twice and "fails loudly" means the process really
    exited non-zero saying the word install. A YAML parse can see none of it.
  * **the sweep** — every `anthropics/claude-code-action` step in
    `.github/workflows/` is preceded, in its own job, by that shared step and
    takes its binary from it. A raw install left anywhere fails this file,
    which is the only thing that keeps the seventeenth site from being the one
    nobody wired.

The installer URL, the install directory and the retry delay are read from
the environment with live defaults, so these tests drive the real script
against a stub instead of the network. The assert is the behaviour under
test; the download is not.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_qa_review_no_verdict_message import (  # noqa: E402
    AUTH_DEATH,
    gate_outputs,
    run_post,
)

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION = ROOT / ".github" / "actions" / "install-claude-code" / "action.yml"

# The vendor step every one of these workflows runs its model through.
VENDOR_ACTION = "anthropics/claude-code-action"

# The three strings that wire a site to the shared step. They are asserted
# rather than described because each is what actually carries the binary:
# the path resolves against the CALLER's workspace (these are reusable
# workflows, so the pipeline's own checkout is at .bureau-pipeline/ — the
# same form qa-review.yml already uses for setup-node-cached), the id is what
# the executable expression and the critic's wording both reference, and the
# expression is what stops the vendor action installing a second copy it
# never asserted.
SHARED_STEP_USES = "./.bureau-pipeline/.github/actions/install-claude-code"
SHARED_STEP_ID = "install_claude"
EXECUTABLE_EXPR = "${{ steps.install_claude.outputs.executable }}"


def _action() -> dict:
    return yaml.safe_load(ACTION.read_text())


def _install_script() -> str:
    """The one `run:` block out of the composite action."""
    steps = _action()["runs"]["steps"]
    runs = [s["run"] for s in steps if "run" in s]
    assert len(runs) == 1, (
        f"the shared install action should be one shell step; found {len(runs)}"
    )
    return runs[0]


def _stub_installer(path: Path, launcher_on_attempts: set[int], *,
                    launcher_exit: int = 0, installer_exit: int = 0) -> None:
    """Write a stub `install.sh` that records every call.

    It appends a line to `$STUB_LOG` per invocation and writes a `claude`
    launcher into `$CLAUDE_BIN_DIR` only on the attempts named — which is how
    a test says "the first install left no launcher, the second one did".
    """
    wanted = " ".join(str(n) for n in sorted(launcher_on_attempts))
    path.write_text(
        "#!/usr/bin/env bash\n"
        'echo "install $1" >> "$STUB_LOG"\n'
        'ATTEMPT=$(wc -l < "$STUB_LOG" | tr -d " ")\n'
        f'for n in {wanted or "none"}; do\n'
        '  if [ "$n" = "$ATTEMPT" ]; then\n'
        '    mkdir -p "$CLAUDE_BIN_DIR"\n'
        '    printf \'#!/usr/bin/env bash\\nexit %s\\n\' '
        f'"{launcher_exit}" > "$CLAUDE_BIN_DIR/claude"\n'
        '    chmod +x "$CLAUDE_BIN_DIR/claude"\n'
        "  fi\n"
        "done\n"
        f"exit {installer_exit}\n"
    )
    path.chmod(0o755)


def run_install(launcher_on_attempts: set[int], env_overrides=None,
                keep=None, **stub_kwargs):
    """Execute the real action script against the stub. Returns (proc, facts).

    `env_overrides` reaches the script's own environment — the seam DRE-3991's
    watchdog is driven through, the same way `CLAUDE_INSTALLER_URL` drives the
    download. `keep` is a directory to build in instead of a temporary one,
    for a test that needs the artifacts to outlive the call.
    """
    with contextlib.ExitStack() as stack:
        raw = keep or stack.enter_context(tempfile.TemporaryDirectory())
        td = Path(raw)
        installer = td / "install.sh"
        _stub_installer(installer, launcher_on_attempts, **stub_kwargs)
        bin_dir = td / "bin"
        log = td / "calls.log"
        log.touch()
        out = td / "github_output"
        out.touch()
        path_file = td / "github_path"
        path_file.touch()

        script = td / "install-step.sh"
        script.write_text(_install_script())

        env = dict(os.environ)
        env.update({
            "HOME": str(td / "home"),
            "RUNNER_TEMP": str(td / "runner-temp"),
            "GITHUB_OUTPUT": str(out),
            "GITHUB_PATH": str(path_file),
            "CLAUDE_CODE_VERSION": "stable",
            # curl reads a file:// URL exactly as it reads an https one, so
            # the download path in the script is the real one under test.
            "CLAUDE_INSTALLER_URL": installer.as_uri(),
            "CLAUDE_BIN_DIR": str(bin_dir),
            "CLAUDE_INSTALL_RETRY_DELAY": "0",
            "STUB_LOG": str(log),
        })
        env.update(env_overrides or {})
        (td / "runner-temp").mkdir(exist_ok=True)
        proc = subprocess.run(["bash", str(script)], cwd=td, env=env,
                              capture_output=True, text=True)
        return proc, {
            "installs": len(log.read_text().splitlines()),
            "output": out.read_text(),
            "path": path_file.read_text(),
            "bin": str(bin_dir / "claude"),
            "versions": " ".join(log.read_text().split()),
        }


def published(output: str, key: str) -> str:
    """The value the install step wrote to `$GITHUB_OUTPUT` under `key`."""
    for line in output.splitlines():
        name, _, value = line.partition("=")
        if name == key:
            return value
    return ""


class TheActionExistsAndIsWellFormedTest(unittest.TestCase):
    def test_it_is_a_composite_action(self):
        self.assertTrue(ACTION.exists(), f"{ACTION} is missing")
        self.assertEqual(_action()["runs"]["using"], "composite")

    def test_every_run_step_declares_a_shell(self):
        # A composite `run:` without `shell:` is a hard runtime error no YAML
        # parse and no lint catches — it fails in the consumer's CI, in
        # another repo, on someone else's card.
        for step in _action()["runs"]["steps"]:
            if "run" in step:
                self.assertIn("shell", step,
                              f"composite step {step.get('name')!r} has no shell:")

    def test_it_publishes_the_executable_it_asserted(self):
        outputs = _action().get("outputs") or {}
        self.assertIn("executable", outputs,
                      "the action must publish the binary it proved, or the "
                      "vendor action installs a second copy nobody asserted")
        # Since DRE-3991 `executable` is the proved binary WRAPPED in the
        # stream watchdog, so the bare binary needs a name of its own — the
        # assert's answer is still a fact a caller may want.
        self.assertIn("proved-executable", outputs)

    def test_the_assert_is_claude_version(self):
        self.assertIn("--version", _install_script(),
                      "the card's assert is `claude --version`")


class TheBinaryIsProvedNotAssumedTest(unittest.TestCase):
    """An installer that exits 0 has proved nothing — DRE-3416's did."""

    def test_a_clean_install_publishes_the_executable(self):
        proc, facts = run_install({1})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(facts["installs"], 1,
                         "a working first install must not be re-run")
        self.assertEqual(facts["bin"], published(facts["output"],
                                                 "proved-executable"))
        self.assertIn(str(Path(facts["bin"]).parent), facts["path"],
                      "the asserted binary's directory belongs on PATH")

    def test_with_no_watchdog_on_the_runner_the_bare_binary_is_published(self):
        # The DRE-3991 fallback: no watchdog script reachable (this harness
        # points at no checkout), so the step publishes the proved binary and
        # SAYS SO. A safety net that could stop the run it guards would be a
        # worse failure than the one it prevents.
        proc, facts = run_install({1})
        self.assertEqual(facts["bin"], published(facts["output"], "executable"))
        self.assertIn("::warning::stream watchdog not installed", proc.stdout)

    def test_the_declared_version_reaches_the_installer(self):
        _, facts = run_install({1})
        self.assertIn("stable", facts["versions"],
                      "the version input must reach the installer")

    def test_an_installer_that_exits_clean_with_no_launcher_is_a_failure(self):
        # The DRE-3416 shape exactly: install_code 0, no launcher.
        proc, facts = run_install(set())
        self.assertNotEqual(proc.returncode, 0,
                            "a clean exit with no binary was reported as success")

    def test_a_launcher_that_does_not_run_is_a_failure(self):
        # Present and executable, but `claude --version` exits non-zero — a
        # libc mismatch or a half-written launcher. Existence is not the test.
        proc, _ = run_install({1, 2}, launcher_exit=1)
        self.assertNotEqual(proc.returncode, 0,
                            "the binary's existence was accepted in place of "
                            "it running")


class ItRetriesExactlyOnceTest(unittest.TestCase):
    def test_a_second_install_that_works_rescues_the_step(self):
        proc, facts = run_install({2})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(facts["installs"], 2)
        self.assertIn("executable=", facts["output"])

    def test_it_does_not_retry_forever(self):
        # "Retry the install once" is two attempts — a loop that keeps going
        # burns the runner on a vendor outage instead of failing at once.
        proc, facts = run_install(set())
        self.assertEqual(facts["installs"], 2,
                         f"expected exactly two install attempts, got "
                         f"{facts['installs']}")
        self.assertNotEqual(proc.returncode, 0)

    def test_an_installer_that_itself_fails_is_still_retried(self):
        proc, facts = run_install(set(), installer_exit=1)
        self.assertEqual(facts["installs"], 2)
        self.assertNotEqual(proc.returncode, 0)


class ItFailsAsAnInstallFailureTest(unittest.TestCase):
    """The whole point: the message names the cause the run actually had."""

    def _message(self) -> str:
        proc, _ = run_install(set())
        self.assertNotEqual(proc.returncode, 0)
        return (proc.stdout + proc.stderr).lower()

    def test_it_names_an_install_failure(self):
        self.assertIn("install", self._message())

    def test_it_says_out_loud_this_is_not_auth_and_not_the_model(self):
        # An operator who reads "native binary not found" rotates a healthy
        # credential; DRE-2924 is the same lesson from the critic's side.
        message = self._message()
        self.assertIn("not an authentication", message)
        self.assertIn("not a model", message)

    def test_it_is_a_github_error_annotation(self):
        self.assertIn("::error::", self._message(),
                      "the failure must surface on the run, not only in the log")


def _jobs_with_vendor_steps():
    """Every (workflow, job, steps) in .github/workflows that runs the model."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for job_name, job in (doc.get("jobs") or {}).items():
            steps = job.get("steps") or []
            if any(VENDOR_ACTION in str(s.get("uses", "")) for s in steps):
                found.append((path.name, job_name, steps))
    return found


class EverySiteUsesTheSharedStepTest(unittest.TestCase):
    """Fails on any raw install left in .github/workflows/ (the card's gate)."""

    def test_the_sweep_finds_the_sites_it_is_meant_to_guard(self):
        # A sweep that matches nothing passes silently and guards nothing.
        sites = sum(
            1
            for _, _, steps in _jobs_with_vendor_steps()
            for s in steps
            if VENDOR_ACTION in str(s.get("uses", ""))
        )
        self.assertGreaterEqual(sites, 16,
                                f"only {sites} model steps found — the sweep "
                                f"is looking in the wrong place")

    def test_every_job_installs_through_the_shared_step_first(self):
        raw = []
        for wf, job, steps in _jobs_with_vendor_steps():
            shared = [
                i for i, s in enumerate(steps)
                if str(s.get("uses", "")).strip() == SHARED_STEP_USES
            ]
            if not shared:
                raw.append(f"{wf}::{job} runs Claude Code without the shared "
                           f"install-and-assert step ({SHARED_STEP_USES})")
                continue
            if steps[shared[0]].get("id") != SHARED_STEP_ID:
                raw.append(f"{wf}::{job}'s install step must carry "
                           f"id: {SHARED_STEP_ID} — the executable expression "
                           f"and the critic's wording both reference it")
            first_model = min(
                i for i, s in enumerate(steps)
                if VENDOR_ACTION in str(s.get("uses", ""))
            )
            if shared[0] > first_model:
                raw.append(f"{wf}::{job} installs Claude Code after it first "
                           f"runs it")
        self.assertEqual(raw, [], "\n".join(raw))

    def test_every_model_step_takes_the_binary_that_was_proved(self):
        raw = []
        for wf, job, steps in _jobs_with_vendor_steps():
            for step in steps:
                if VENDOR_ACTION not in str(step.get("uses", "")):
                    continue
                got = (step.get("with") or {}).get(
                    "path_to_claude_code_executable")
                if (got or "").strip() != EXECUTABLE_EXPR:
                    raw.append(
                        f"{wf}::{job}::{step.get('name')} still installs its "
                        f"own Claude Code — the assert above it proves "
                        f"nothing about the binary this step runs"
                    )
        self.assertEqual(raw, [], "\n".join(raw))

    def test_a_model_step_that_survives_a_failed_install_is_guarded(self):
        # Once a step fails, GitHub skips the rest of the job — except the
        # steps that ask for `always()`. Those would run a review against an
        # executable the install step never produced.
        raw, seen = [], 0
        for wf, job, steps in _jobs_with_vendor_steps():
            for step in steps:
                if VENDOR_ACTION not in str(step.get("uses", "")):
                    continue
                condition = str(step.get("if") or "")
                if "always()" not in condition:
                    continue
                seen += 1
                if f"steps.{SHARED_STEP_ID}.outcome == 'success'" not in condition:
                    raw.append(
                        f"{wf}::{job}::{step.get('name')} runs on always() "
                        f"and would start after a failed install"
                    )
        self.assertEqual(raw, [], "\n".join(raw))
        self.assertGreaterEqual(seen, 2,
                                "the always() retries this guard exists for "
                                "are no longer being found")


class TheCriticNamesTheInstallFailureTest(unittest.TestCase):
    """qa-review's "crashed on both attempts" notice, when nothing ran.

    A failed install skips both critic attempts, so both gates report an
    attempt that left no execution record and the neutral comment falls into
    the branch that says "startup/auth failure". That sentence sends the
    operator to rotate a credential the reviewer never reached — the DRE-2924
    mistake, with a new cause. When the shared step is what failed, the notice
    says so.
    """

    def _comment(self, install_failed: str) -> str:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            gate = gate_outputs(td, AUTH_DEATH)
            proc, body = run_post(td, gate,
                                  extra_env={"INSTALL_FAILED": install_failed})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return body

    def test_it_names_an_install_failure(self):
        body = self._comment("true").lower()
        self.assertIn("install", body)
        self.assertNotIn("startup/auth failure", body)

    def test_it_absolves_the_credential_and_the_model(self):
        body = self._comment("true").lower()
        self.assertIn("not an authentication", body)
        self.assertIn("not a model", body)

    def test_it_stays_a_neutral_status_the_merge_gate_can_read(self):
        # Same two rules every branch of this notice lives under: merge-gate
        # only treats a comment as the latest verdict if it says QA Critic,
        # and a notice carrying VERDICT: APPROVE would BE a merge credential.
        body = self._comment("true")
        self.assertIn("QA Critic", body)
        self.assertNotIn("VERDICT: APPROVE", body)

    def test_a_real_crash_keeps_the_wording_it_already_had(self):
        # The install-failure branch must not swallow the case it was added
        # beside: nothing installed-related happened here.
        body = self._comment("")
        self.assertIn("startup/auth failure", body)


if __name__ == "__main__":
    unittest.main()
