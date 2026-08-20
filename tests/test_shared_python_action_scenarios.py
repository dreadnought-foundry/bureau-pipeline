"""Integration scenarios and adversarial inputs for setup-python-cached (DRE-2589).

tests/test_shared_python_action.py checks the action's parts. This file checks
what the parts DO together, because every failure mode that matters here is a
composed one:

  * a cache HIT that still runs `pip install` — the action costs wall-clock and
    saves nothing, and looks fine in every per-step assertion
  * a cache HIT that re-uploads the cache it just restored
  * a MISS that installs and never saves — every run is a miss, forever
  * a HIT that restores the venv and never puts it on PATH, so the job runs
    against the runner image's python and every pinned tool is missing
  * `install: false` that still demands a manifest

None of those is visible in one step. They live in the `if:` conditions, which
is exactly the code no YAML parse and no linter evaluates. So this file
EVALUATES them, for every scenario, and asserts the decision table.

The expression evaluator is the one built for the node action — imported, not
copied. A second copy of it would be the very defect this card is about.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# The node scenarios module is a sibling test file, not a package module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_shared_node_action_scenarios import (  # noqa: E402
    ExpressionError,
    interpolate,
    step_runs,
)

PYTHON_ACTION = ROOT / ".github" / "actions" / "setup-python-cached" / "action.yml"
README = ROOT / "README.md"


def _contexts(install="true", cache_hit="", venv_path=".venv",
              requirements="requirements-dev.txt"):
    return {
        "inputs": {
            "install": install,
            "requirements": requirements,
            "venv-path": venv_path,
            "python-version": "3.12",
            "python-version-file": "",
        },
        "steps": {
            "restore": {"outputs": {"cache-hit": cache_hit}},
            "key": {
                "outputs": {
                    "key": "python-venv-v1-Linux-X64-venv-abc",
                    "manifests": "/tmp/rt/setup-python-cached-manifests.txt",
                }
            },
        },
        "github": {"workspace": "/home/runner/work/repo/repo"},
    }


class Scenario(unittest.TestCase):
    """Walk the action's real steps under real contexts."""

    def setUp(self):
        self.assertTrue(PYTHON_ACTION.is_file(), f"{PYTHON_ACTION} is missing")
        self.steps = yaml.safe_load(PYTHON_ACTION.read_text())["runs"]["steps"]

    def _ran(self, contexts):
        return {
            str(s.get("name", s.get("uses", "?"))): s
            for s in self.steps
            if step_runs(s, contexts)
        }

    def _named(self, ran, fragment):
        for name in ran:
            if fragment.lower() in name.lower():
                return ran[name]
        return None

    # -- scenario 1: cold cache, the first run on a branch -------------------

    def test_cold_cache_installs_and_saves(self):
        ran = self._ran(_contexts(cache_hit=""))
        for fragment in (
            "compute the virtualenv cache key",
            "restore the virtualenv",
            "install dependencies",
            "save the virtualenv",
            "on path",
        ):
            self.assertIsNotNone(
                self._named(ran, fragment),
                f"{fragment!r} did not run on a MISS — either the suite runs with "
                f"no dependencies, or nothing is ever saved and every future run "
                f"is a miss too",
            )

    # -- scenario 2: warm cache — the point of the whole action ---------------

    def test_warm_cache_skips_the_install(self):
        ran = self._ran(_contexts(cache_hit="true"))
        self.assertIsNone(
            self._named(ran, "install dependencies"),
            "a cache HIT still ran pip install — this is the entire saving, and "
            "its absence is invisible in every per-step assertion",
        )

    def test_warm_cache_does_not_re_upload_the_cache(self):
        ran = self._ran(_contexts(cache_hit="true"))
        self.assertIsNone(
            self._named(ran, "save the virtualenv"),
            "a HIT re-saved the cache it just restored — paying upload cost on "
            "every run for a byte-identical entry",
        )

    def test_warm_cache_still_exports_the_venv(self):
        # The restored venv is useless if nothing puts it on PATH: the job would
        # run against the runner image's python, with none of the pinned tools,
        # and the failure would look like a missing dependency in the code.
        ran = self._ran(_contexts(cache_hit="true"))
        self.assertIsNotNone(
            self._named(ran, "on path"),
            "a HIT restored the venv and never exported it — every pinned tool "
            "would be missing from PATH",
        )

    def test_warm_cache_still_sets_up_python(self):
        # python itself is not cached by us. If this step were skipped on a hit,
        # the venv's bin/python symlink would point at an interpreter the runner
        # has not installed.
        ran = self._ran(_contexts(cache_hit="true"))
        self.assertIsNotNone(self._named(ran, "set up python"))

    # -- scenario 3: python only, no install ---------------------------------

    def test_install_false_touches_no_cache_and_needs_no_manifest(self):
        ran = self._ran(_contexts(install="false"))
        for fragment in (
            "compute the virtualenv cache key",
            "restore the virtualenv",
            "install dependencies",
            "save the virtualenv",
            "on path",
        ):
            self.assertIsNone(
                self._named(ran, fragment),
                f"{fragment!r} ran with install: false — the key step REQUIRES a "
                f"manifest, so a python-only job in a repo without one would fail",
            )
        self.assertIsNotNone(self._named(ran, "set up python"))

    # -- the paths the steps actually address --------------------------------

    def test_cache_path_is_the_callers_venv(self):
        ctx = _contexts(venv_path="console/backend/.venv")
        for fragment in ("restore the virtualenv", "save the virtualenv"):
            step = self._named(self._ran(ctx), fragment)
            self.assertEqual(
                interpolate(step["with"]["path"], ctx), "console/backend/.venv"
            )

    def test_the_scripts_anchor_at_the_workspace(self):
        # A caller's job-level defaults.run.working-directory must not be able to
        # turn `.venv` into `console/backend/.venv`. agent-bureau's infra-ci.yml
        # sets exactly such a default.
        for step_id in ("key", "install"):
            step = [s for s in self.steps if s.get("id") == step_id][0]
            self.assertIn(
                'cd "$GITHUB_WORKSPACE"',
                step["run"],
                f"the {step_id} step does not anchor at the workspace",
            )

    def test_the_manifest_list_is_parsed_once_and_handed_on(self):
        # The install step must consume the list the key step already parsed. A
        # second parse of `inputs.requirements` would be two copies of one rule
        # — the exact defect class this card is about — and they could disagree
        # about which files the key covered versus which were installed.
        install = [s for s in self.steps if s.get("id") == "install"][0]
        env = install.get("env") or {}
        self.assertTrue(
            any("steps.key.outputs" in str(v) for v in env.values()),
            "the install step does not read anything the key step produced — it "
            "is parsing the requirements list a second time",
        )
        self.assertNotIn(
            "REQUIREMENTS",
            env,
            "the install step re-parses inputs.requirements — the key step "
            "already did, and two parses can disagree about what was cached",
        )

    def test_every_expression_in_the_action_is_one_the_evaluator_understands(self):
        # A guard against this file going quietly vacuous: if a future diff adds
        # an `if:` the evaluator cannot parse, the scenarios above would still
        # pass while no longer describing the action.
        ctx = _contexts()
        for step in self.steps:
            if "if" not in step:
                continue
            try:
                step_runs(step, ctx)
            except ExpressionError as exc:
                self.fail(f"step {step.get('name')!r} has an unparsed if: {exc}")


# --- adversarial inputs, executed for real ----------------------------------


def _run_key(script, requirements="requirements-dev.txt", python_version="3.12",
             python_version_file="", venv_path=".venv",
             manifests=(("requirements-dev.txt", "pytest==9.1.1\n"),)):
    """Execute the key step in a throwaway workspace.

    Returns (rc, combined, pwned, workspace_snapshot) — `pwned` is True when a
    caller-supplied string reached the shell.
    """
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        for name, body in manifests:
            target = workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        out = Path(tmp) / "out"
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
        return (
            proc.returncode,
            proc.stdout + proc.stderr + out.read_text(),
            canary.exists(),
        )


def _key(combined):
    for line in combined.splitlines():
        if line.startswith("key="):
            return line[len("key="):]
    raise AssertionError(f"no key= line emitted:\n{combined}")


class AdversarialInputs(unittest.TestCase):
    """The action takes caller-supplied strings and builds a shell command
    around them. Every repo in the fleet is a caller, so the inputs are treated
    as hostile."""

    def setUp(self):
        self.assertTrue(PYTHON_ACTION.is_file(), f"{PYTHON_ACTION} is missing")
        doc = yaml.safe_load(PYTHON_ACTION.read_text())
        self.script = [s for s in doc["runs"]["steps"] if s.get("id") == "key"][0]["run"]

    def test_command_substitution_in_the_venv_path_does_not_execute(self):
        _, _, pwned = _run_key(self.script, venv_path='$(touch "$CANARY")')
        self.assertFalse(pwned, "command substitution in venv-path EXECUTED")

    def test_command_substitution_in_the_requirements_does_not_execute(self):
        rc, out, pwned = _run_key(self.script, requirements='$(touch "$CANARY")')
        self.assertFalse(pwned, "command substitution in requirements EXECUTED")
        self.assertEqual(rc, 1, out)

    def test_semicolon_injection_does_not_execute(self):
        _, _, pwned = _run_key(self.script, venv_path='; touch "$CANARY"; echo')
        self.assertFalse(pwned, "a semicolon in venv-path reached the shell")

    def test_an_absolute_venv_path_is_refused(self):
        # The cache path is passed to actions/cache verbatim. An absolute path
        # would put a cached, restorable directory outside the workspace — and
        # `rm -rf` of it on a miss would be outside the workspace too.
        rc, out, _ = _run_key(self.script, venv_path="/tmp/evil")
        self.assertEqual(rc, 1, f"an absolute venv-path was accepted: {out}")
        self.assertIn("venv-path", out)

    def test_a_venv_path_escaping_the_workspace_is_refused(self):
        rc, out, _ = _run_key(self.script, venv_path="../../evil")
        self.assertEqual(rc, 1, f"a venv-path escaping the workspace was accepted: {out}")

    def test_an_empty_venv_path_is_refused(self):
        # An empty path means the cache path is the workspace ROOT: the action
        # would cache the entire checkout and `rm -rf` it on a miss.
        rc, out, _ = _run_key(self.script, venv_path="")
        self.assertEqual(rc, 1, f"an empty venv-path was accepted: {out}")

    def test_a_manifest_list_with_blank_lines_and_indentation_is_tolerated(self):
        # Callers pass a YAML block scalar, which arrives indented and with a
        # trailing newline. Treating "  requirements-dev.txt" as a missing file
        # would fail every caller that used the natural spelling.
        rc, out, _ = _run_key(
            self.script,
            requirements="\n  requirements-dev.txt\n\n",
        )
        self.assertEqual(rc, 0, f"an indented manifest list was rejected:\n{out}")

    def test_several_manifests_all_reach_the_key(self):
        pair = (("a.txt", "pytest==9.1.1\n"), ("b.txt", "PyYAML==6.0.3\n"))
        _, both, _ = _run_key(self.script, requirements="a.txt\nb.txt", manifests=pair)
        _, first_only, _ = _run_key(self.script, requirements="a.txt", manifests=pair)
        self.assertNotEqual(
            _key(both),
            _key(first_only),
            "a second manifest does not change the key — bumping a pin in it "
            "would restore the environment built before the bump",
        )

    def test_a_directory_with_spaces_still_works(self):
        rc, out, _ = _run_key(self.script, venv_path="my project/.venv")
        self.assertEqual(rc, 0, f"unquoted path handling broke on spaces:\n{out}")
        self.assertIn("key=", out)

    def test_the_key_is_deterministic_across_runs(self):
        first = _run_key(self.script)[1]
        second = _run_key(self.script)[1]
        self.assertEqual(
            _key(first),
            _key(second),
            "the key varies per run, so it would never hit — the action would "
            "look like it worked while saving nothing",
        )

    def test_two_venvs_with_identical_manifests_get_distinct_keys(self):
        a = _run_key(self.script, venv_path="backend/.venv")[1]
        b = _run_key(self.script, venv_path="relay/.venv")[1]
        self.assertNotEqual(_key(a), _key(b))

    def test_the_key_fits_githubs_limit(self):
        rc, out, _ = _run_key(self.script, venv_path="a/" * 60 + ".venv")
        self.assertEqual(rc, 0, out)
        key = _key(out)
        self.assertLess(len(key), 512, f"key is {len(key)} chars: {key!r}")

    def test_the_key_holds_no_characters_github_disallows(self):
        # Commas separate restore-keys, so a comma in a key is a silent split.
        rc, out, _ = _run_key(self.script, venv_path="my venv/x")
        key = _key(out)
        for bad in (",", " ", "\t"):
            self.assertNotIn(bad, key, f"{bad!r} in cache key {key!r}")


class KeyAndInstallAgree(unittest.TestCase):
    """The two scripts are one mechanism split across two steps. Run them in
    sequence, for real, and assert the handoff works — this is the seam a unit
    assertion on either half cannot see."""

    def setUp(self):
        self.assertTrue(PYTHON_ACTION.is_file(), f"{PYTHON_ACTION} is missing")
        doc = yaml.safe_load(PYTHON_ACTION.read_text())
        steps = doc["runs"]["steps"]
        self.key_script = [s for s in steps if s.get("id") == "key"][0]["run"]
        self.install_script = [s for s in steps if s.get("id") == "install"][0]["run"]
        self.install_env = [s for s in steps if s.get("id") == "install"][0].get("env") or {}

    def test_the_install_creates_the_venv_at_the_path_that_gets_cached(self):
        # Deliberately comment-only manifests: `pip install -r` on them is a
        # no-op, so this exercises the wiring — venv creation, the -r argument
        # list handed over from the key step — without a network round trip.
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            (workspace / "a.txt").write_text("# nothing to install\n")
            (workspace / "b.txt").write_text("# also nothing\n")
            runner_temp = Path(tmp) / "runner_temp"
            runner_temp.mkdir()
            out = Path(tmp) / "out"
            out.touch()
            env = {
                **os.environ,
                "GITHUB_WORKSPACE": str(workspace),
                "REQUIREMENTS": "a.txt\nb.txt",
                "PYTHON_VERSION": "3.12",
                "PYTHON_VERSION_FILE": "",
                "VENV_PATH": "build/.venv",
                "RUNNER_OS": "Linux",
                "RUNNER_ARCH": "X64",
                "RUNNER_TEMP": str(runner_temp),
                "GITHUB_OUTPUT": str(out),
            }
            key = subprocess.run(
                ["bash", "-c", self.key_script], cwd=str(workspace), env=env,
                capture_output=True, text=True,
            )
            self.assertEqual(key.returncode, 0, key.stdout + key.stderr)

            # Whatever the key step emitted as `manifests=` is what the install
            # step's env is wired to receive.
            emitted = dict(
                line.split("=", 1) for line in out.read_text().splitlines() if "=" in line
            )
            self.assertIn(
                "manifests",
                emitted,
                "the key step emits no manifest list, so the install step has "
                "nothing to install from",
            )
            wired = [k for k, v in self.install_env.items()
                     if "steps.key.outputs.manifests" in str(v)]
            self.assertTrue(
                wired,
                f"no install-step env var reads steps.key.outputs.manifests "
                f"(env: {sorted(self.install_env)})",
            )

            install = subprocess.run(
                ["bash", "-c", self.install_script],
                cwd=str(workspace),
                env={**env, wired[0]: emitted["manifests"]},
                capture_output=True,
                text=True,
            )
            self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
            self.assertTrue(
                (workspace / "build/.venv/bin/python").exists(),
                "the install step did not create a venv at the path the cache "
                "steps save and restore — the cache would be empty forever",
            )


class DocumentedExampleMatchesTheAction(unittest.TestCase):
    """The README block is what an adopting repo copies. If it drifts from the
    action's real inputs, the error surfaces in someone else's repo, on someone
    else's card."""

    def setUp(self):
        self.text = README.read_text()
        self.assertIn(
            "setup-python-cached",
            self.text,
            "the README no longer documents the shared python action — an "
            "adopting repo has nothing to copy",
        )

    def test_every_input_in_the_readme_example_is_declared(self):
        import re

        declared = set(yaml.safe_load(PYTHON_ACTION.read_text())["inputs"])
        # Ref-agnostic on purpose: this test must not be the thing that pins
        # `@main` as the one correct spelling.
        block = re.split(r"setup-python-cached@\S+", self.text, maxsplit=1)[1]
        block = block.split("```", 1)[0]
        used = set(re.findall(r"^\s{4}([a-z][a-z0-9-]*):", block, re.M))
        self.assertTrue(used, "the README example passes no inputs at all")
        unknown = used - declared
        self.assertFalse(
            unknown,
            f"the README example passes {sorted(unknown)}, which the action does "
            f"not declare — a repo copying it would silently install nothing",
        )
        self.assertIn(
            "requirements", used, "the example omits the manifest, which is the point"
        )

    def test_the_docs_do_not_offer_main_to_the_whole_fleet(self):
        """standards/engineering.md: only agent-bureau and bureau-pipeline ride
        `@main`. A composite action is consumed by the caller's CI on its very
        next run, so an unpinned fleet-facing snippet means a bad merge here
        reprograms that repo's CI with no canary soak."""
        import re

        refs = set(re.findall(r"setup-python-cached@(\S+)", self.text))
        self.assertTrue(refs, "the README documents no ref at all")
        if "main" in refs:
            self.assertRegex(
                self.text,
                r"(?i)canary",
                "the README shows setup-python-cached@main without ever naming "
                "the canary restriction — a product repo would copy it",
            )
            self.assertTrue(
                refs - {"main"},
                "the README offers ONLY @main — every product repo copying it "
                "would ride live main for its CI plumbing",
            )


if __name__ == "__main__":
    unittest.main()
