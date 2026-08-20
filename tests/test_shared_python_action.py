"""Shared python plumbing contract (DRE-2589 — the Python half of DRE-2550).

Origin: DRE-2550 moved the fleet's NODE plumbing into one composite action and
deliberately left Python out. agent-bureau's ci.yml still sets up Python and
installs test tooling in three jobs — `lint`, `backend`, `relay` — each with
its own `setup-python`, its own `pip install`, and its own copy of the pinned
tool versions. `pytest==9.1.0` appears in two of them. This repo had a smaller
copy of the same defect: tests.yml ran three separate `pip install` lines, two
of them `pyyaml` by bare name while requirements-dev.txt pins `PyYAML==6.0.3`.

This suite pins the contract of `.github/actions/setup-python-cached`. Like the
node suite it exists because the action's value lives in properties a YAML
parse cannot see:

  * the cache key is EXACT-match — no restore-keys. A near-miss virtualenv
    looks installed while holding another manifest's distributions, so the
    suite fails far from the cause.
  * the RESOLVED interpreter is IN the key. A venv is not relocatable across
    interpreters: `pyvenv.cfg` and `bin/python` bind to the exact
    hostedtoolcache install, and wheels are built per version. A key without
    it would restore a venv whose interpreter no longer exists.
  * `pip install` is SKIPPED on a hit. That is the whole saving —
    `setup-python`'s own `cache: pip` caches the DOWNLOADS (~/.cache/pip), so a
    hit there still pays the resolve, build and install on every run.
  * the install is a NAMED step. Today agent-bureau's pip install happens
    inside the test step, so its cost cannot be separated from the test's and
    `scripts/ci_minutes_report.py` has nothing to report. A named step is what
    makes the number exist.
  * every `run` step declares `shell:`. A composite step without it is a hard
    runtime error no YAML parse and no lint catches — it fails in the
    consumer's CI, in another repo, on someone else's card.
  * the action installs from a MANIFEST (`-r`), never from bare package names.
    That is what makes a Dependabot bump actually exercise the new pins
    (DRE-2039); an action that accepted `pytest==9.1.0` as an input would just
    move the duplicated pin from the workflow into the caller's `with:` block.

Live-extraction pattern, like tests/test_shared_node_action.py: these parse and
EXECUTE the actual action file, so a future diff that drops a condition or adds
a restore-keys turns Pipeline Tests red.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ACTIONS_DIR = ROOT / ".github" / "actions"
PYTHON_ACTION = ACTIONS_DIR / "setup-python-cached" / "action.yml"

# Two composite actions now live here (node + python). The node suite's floor
# stays at 1 on purpose — this is the floor that notices if the python one
# disappears and leaves every "no violations" assertion below vacuous.
KNOWN_ACTION_FLOOR = 2

_MANIFEST_BYTES = "pytest==9.1.1\n"


def _load(path):
    return yaml.safe_load(path.read_text())


def _steps(doc):
    return (doc.get("runs") or {}).get("steps") or []


def _step_by_id(doc, step_id):
    for step in _steps(doc):
        if step.get("id") == step_id:
            return step
    return None


def _step_named(doc, fragment):
    for step in _steps(doc):
        if fragment.lower() in str(step.get("name", "")).lower():
            return step
    return None


def _run_key_script(
    script,
    requirements="requirements-dev.txt",
    python_version="3.12",
    python_version_file="",
    venv_path=".venv",
    manifests=(("requirements-dev.txt", _MANIFEST_BYTES),),
):
    """Run the action's key step in a throwaway workspace.

    Returns (rc, combined) where `combined` is stdout + stderr + whatever the
    step appended to $GITHUB_OUTPUT, so one return value covers both asserting
    on an error message and asserting on the emitted key.
    """
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        for name, body in manifests:
            target = workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        out = Path(tmp) / "github_output"
        out.touch()
        runner_temp = Path(tmp) / "runner_temp"
        runner_temp.mkdir()
        canary = Path(tmp) / "PWNED"
        proc = subprocess.run(
            ["bash", "-c", script],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            env={
                **os.environ,
                # Always set on a real runner; the script anchors on it so a
                # caller's job-level working-directory default cannot shift it.
                "GITHUB_WORKSPACE": str(workspace),
                "REQUIREMENTS": requirements,
                "PYTHON_VERSION": python_version,
                "PYTHON_VERSION_FILE": python_version_file,
                "VENV_PATH": venv_path,
                "RUNNER_OS": "Linux",
                "RUNNER_ARCH": "X64",
                "RUNNER_TEMP": str(runner_temp),
                "GITHUB_OUTPUT": str(out),
                "CANARY": str(canary),
            },
        )
        return proc.returncode, proc.stdout + proc.stderr + out.read_text()


def _key_line(combined):
    for line in combined.splitlines():
        if line.startswith("key="):
            return line[len("key="):]
    raise AssertionError(f"no key= line emitted:\n{combined}")


def _digest_of(combined):
    """The trailing digest of the emitted `key=` line."""
    return _key_line(combined).rsplit("-", 1)[-1]


class SweepIsNotVacuous(unittest.TestCase):
    def test_both_shared_actions_are_present(self):
        found = sorted(ACTIONS_DIR.glob("*/action.yml"))
        self.assertGreaterEqual(
            len(found),
            KNOWN_ACTION_FLOOR,
            f"found {len(found)} composite actions, expected at least "
            f"{KNOWN_ACTION_FLOOR} (node + python) — the glob went vacuous or "
            f"an action vanished",
        )


class PythonActionShape(unittest.TestCase):
    """The action exists, is composite, and takes what the fleet calls it with."""

    def setUp(self):
        self.assertTrue(
            PYTHON_ACTION.is_file(),
            f"{PYTHON_ACTION} is missing — the fleet's Python CI has nothing to "
            f"call, so each job keeps its own setup-python + pip install pair "
            f"and its own copy of the pins (DRE-2589)",
        )
        self.doc = _load(PYTHON_ACTION)

    def test_is_a_composite_action(self):
        self.assertEqual((self.doc.get("runs") or {}).get("using"), "composite")

    def test_declares_the_inputs_callers_use(self):
        inputs = self.doc.get("inputs") or {}
        for name in (
            "requirements",
            "python-version",
            "python-version-file",
            "venv-path",
            "install",
        ):
            self.assertIn(
                name,
                inputs,
                f"input {name!r} is gone — a consumer calling it would fail with "
                f"an unhelpful 'unexpected input' warning and then install "
                f"nothing",
            )

    def test_install_defaults_to_installing(self):
        # A default of anything else turns every existing caller into a job
        # that sets up python and then runs a suite with no dependencies.
        self.assertEqual((self.doc["inputs"]["install"]).get("default"), "true")

    def test_takes_no_package_name_input(self):
        # The whole point: tooling comes from a pinned manifest. An input like
        # `packages:` would move the duplicated pin out of the workflow's `run`
        # and into its `with:` — same fact, same number of homes, and a
        # Dependabot bump would still exercise nothing (DRE-2039).
        inputs = self.doc.get("inputs") or {}
        for banned in ("packages", "pip-packages", "extra-packages", "tools"):
            self.assertNotIn(
                banned,
                inputs,
                f"input {banned!r} lets a caller pass bare package names, which "
                f"re-creates the per-job pin this action exists to remove",
            )

    def test_exposes_cache_hit_so_a_saving_can_be_reported(self):
        outputs = self.doc.get("outputs") or {}
        self.assertIn(
            "cache-hit",
            outputs,
            "without a cache-hit output a hit is indistinguishable from a very "
            "fast install when measuring the saving — the acceptance criterion "
            "needs the observation, not an inference",
        )
        self.assertIn(
            "steps.restore.outputs.cache-hit", str(outputs["cache-hit"].get("value"))
        )

    def test_every_run_step_declares_a_shell(self):
        for step in _steps(self.doc):
            if "run" in step:
                self.assertIn(
                    "shell",
                    step,
                    f"step {step.get('name')!r} runs a script with no shell: — "
                    f"that is a runtime failure in every repo that calls this",
                )

    def test_does_not_hardcode_a_python_version(self):
        setup = _step_named(self.doc, "set up python")
        self.assertIsNotNone(setup, "the action no longer sets up python at all")
        with_ = setup.get("with") or {}
        for field in ("python-version", "python-version-file"):
            self.assertIn(
                "inputs.",
                str(with_.get(field, "")),
                f"{field} is not threaded from inputs — the action would impose "
                f"one python version on every consumer repo",
            )

    def test_the_venv_is_put_on_path_for_later_steps(self):
        # Installing into a venv nothing can reach is worse than not caching:
        # the job would silently run against the runner image's python.
        step = _step_named(self.doc, "on path")
        self.assertIsNotNone(
            step,
            "no step exports the venv — the caller's `pytest` would resolve to "
            "the runner image's interpreter, which has none of the pinned tools",
        )
        self.assertIn("GITHUB_PATH", step.get("run", ""))


class CacheBreakRule(unittest.TestCase):
    """The properties that make a cached virtualenv trustworthy."""

    def setUp(self):
        self.assertTrue(PYTHON_ACTION.is_file(), f"{PYTHON_ACTION} is missing")
        self.doc = _load(PYTHON_ACTION)
        key_step = _step_by_id(self.doc, "key")
        self.assertIsNotNone(key_step, "the key-computing step (id: key) is gone")
        self.script = key_step["run"]

    def test_no_restore_keys_anywhere(self):
        # Exact match or a full install. restore-keys would hand a job a venv
        # built from a DIFFERENT manifest, which presents as a test failure with
        # no connection to the cache.
        for step in _steps(self.doc):
            self.assertNotIn(
                "restore-keys",
                step.get("with") or {},
                f"step {step.get('name')!r} added restore-keys — a partial cache "
                f"hit is worse than a miss for an installed environment",
            )

    def test_the_key_covers_the_manifests_and_the_interpreter(self):
        self.assertIn("REQUIREMENTS", self.script, "the key ignores the manifests")
        self.assertIn(
            "python_id",
            self.script,
            "the key ignores the interpreter — a venv restored under a different "
            "python has a bin/python symlink pointing at an install that is not "
            "there",
        )
        self.assertIn("digest=", self.script)
        self.assertIn("$digest", self.script.split("key=", 1)[1])

    def test_a_changed_manifest_changes_the_key(self):
        _, a = _run_key_script(self.script, manifests=(("requirements-dev.txt", "pytest==9.1.1\n"),))
        _, b = _run_key_script(self.script, manifests=(("requirements-dev.txt", "pytest==9.2.0\n"),))
        self.assertNotEqual(
            _digest_of(a),
            _digest_of(b),
            "the manifest's content does not reach the key at all — a Dependabot "
            "bump would restore the environment built from the OLD pins, so the "
            "PR would prove nothing about the new ones",
        )

    def test_a_different_interpreter_is_a_different_key(self):
        # Probed for real: the key step reads the RESOLVED interpreter, so this
        # asserts the two digests differ when that resolved value differs.
        _, here = _run_key_script(self.script)
        marker = "python_id"
        self.assertIn(marker, self.script)
        forced = self.script.replace(
            'python_id="$(', 'python_id="pretend-3.99.0" # $(', 1
        )
        self.assertNotEqual(forced, self.script, "could not force a second interpreter")
        _, other = _run_key_script(forced)
        self.assertNotEqual(
            _digest_of(here),
            _digest_of(other),
            "two interpreters produce the same key — one job would be handed a "
            "venv whose interpreter does not exist on this runner",
        )

    def test_the_key_script_refuses_to_run_with_no_python_version(self):
        # A caller that forgets both version inputs falls back to whatever
        # python the runner image happens to ship, which moves without a commit.
        rc, out = _run_key_script(self.script, python_version="", python_version_file="")
        self.assertEqual(rc, 1, f"expected a hard failure, got rc={rc}: {out}")
        self.assertIn("neither python-version nor python-version-file", out)

    def test_the_key_script_refuses_an_empty_requirements_list(self):
        # Installing nothing is not the same as `install: false` — it is a
        # caller who thinks tooling is being installed and gets none.
        rc, out = _run_key_script(self.script, requirements="")
        self.assertEqual(rc, 1, f"expected a hard failure, got rc={rc}: {out}")
        self.assertIn("no requirements file", out)

    def test_a_missing_manifest_fails_here_not_in_the_suite(self):
        rc, out = _run_key_script(self.script, requirements="requirements-nope.txt")
        self.assertEqual(rc, 1, f"expected a hard failure, got rc={rc}: {out}")
        self.assertIn("requirements-nope.txt", out)

    def test_caller_input_reaches_the_script_through_env_not_interpolation(self):
        # House rule from the pipeline's own workflows: a caller-supplied value
        # is never interpolated into a shell line.
        key_step = _step_by_id(self.doc, "key")
        env = key_step.get("env") or {}
        for name in ("REQUIREMENTS", "VENV_PATH"):
            self.assertIn(name, env, f"{name} does not reach the script through env")
        self.assertNotIn(
            "${{",
            key_step["run"],
            "the key script interpolates an expression directly into shell — a "
            "crafted input becomes shell input",
        )

    def test_a_cache_hit_skips_the_install(self):
        # This is the entire saving. If the install step loses its condition,
        # the cache costs wall-clock and saves none.
        install = _step_named(self.doc, "install dependencies")
        self.assertIsNotNone(install, "the install step is gone")
        self.assertIn(
            "steps.restore.outputs.cache-hit != 'true'",
            str(install.get("if", "")),
            "the install step is unconditional — a cache hit would still pay for "
            "pip install, which is the cost this action exists to remove",
        )

    def test_the_install_is_its_own_named_step(self):
        # Acceptance criterion: agent-bureau's pip install runs INSIDE the test
        # step today, so its cost cannot be separated from the test's and
        # scripts/ci_minutes_report.py has nothing to attribute. A named step is
        # what makes the number exist.
        install = _step_named(self.doc, "install dependencies")
        self.assertIn(
            "pip install",
            str(install.get("name", "")).lower() + install.get("run", ""),
            "the install step no longer names what it does",
        )
        self.assertNotIn(
            "pytest",
            install.get("run", ""),
            "the install step also runs the suite — that is the shape this card "
            "exists to undo: an install whose cost cannot be measured",
        )

    def test_the_install_reads_a_manifest_and_never_bare_names(self):
        install = _step_named(self.doc, "install dependencies")
        run = install.get("run", "")
        self.assertIn(
            "-r",
            run,
            "the install does not use `-r <manifest>` — installing bare names is "
            "what makes a Dependabot bump exercise nothing (DRE-2039)",
        )
        self.assertNotRegex(
            run,
            r"pip install[^\n]*[A-Za-z0-9_.-]+==",
            "a pinned package name is hardcoded in the action — the pin would "
            "then have a second home, which is the defect being fixed",
        )

    def test_the_cache_is_only_saved_after_an_actual_install(self):
        save = _step_named(self.doc, "save the virtualenv")
        self.assertIsNotNone(save, "nothing saves the cache — every run is a miss")
        self.assertIn(
            "steps.restore.outputs.cache-hit != 'true'",
            str(save.get("if", "")),
            "the save step would re-upload a cache it just restored, paying "
            "upload cost on every hit",
        )

    def test_restore_and_save_agree_on_the_key_and_the_path(self):
        # Two literals that must never drift: a save under a different key than
        # the restore looks for is a cache that is written and never read.
        restore = _step_named(self.doc, "restore the virtualenv")
        save = _step_named(self.doc, "save the virtualenv")
        self.assertEqual((restore["with"])["key"], (save["with"])["key"])
        self.assertEqual((restore["with"])["path"], (save["with"])["path"])


if __name__ == "__main__":
    unittest.main()
