"""Shared node plumbing contract (DRE-2550 — Wave 1, Step 1).

Origin: six repos each carried their own `setup-node` + `npm ci` pair, and
agent-bureau carried three of them in one ci.yml. The caching strategy, and
the rule deciding when a cache is STALE, therefore existed in six copies that
could drift independently. Wave 0 hit that same fan-out three times (MAX_WIP,
ci.yml, the turn ceiling) and each "one change" turned out to be six.

This suite pins the contract of `.github/actions/setup-node-cached`, the one
place that plumbing now lives. It exists because the action's VALUE is entirely
in properties a YAML parse cannot see:

  * the cache key is EXACT-match — no restore-keys. A near-miss node_modules
    looks installed while holding another lockfile's packages, so the suite
    fails far from the cause and the run reads as a code defect.
  * the node identity is IN the key. The same lockfile under a different node
    major produces different artefacts (node-gyp output; the platform build
    esbuild/rollup/sharp pick at install time), so a key without it hands one
    job the binaries another job compiled.
  * `npm ci` is SKIPPED on a hit. That is the whole saving — `setup-node`'s
    own `cache: npm` caches the downloads, not node_modules, so a hit there
    still pays the unpack (measured: 89s per web shard on a warm npm cache).
  * every `run` step declares `shell:`. A composite step without it is a hard
    runtime error that no YAML parse and no lint catches — it fails in the
    consumer's CI, in another repo, on someone else's card.

Also enforced here: NO composite action in this repo may check out
bureau-pipeline. scripts/check_pipeline_ref.py enforces the DRE-2026 threading
rule, but it only sweeps .github/workflows — a composite action is outside it,
and a `uses:` reference cannot carry an expression, so an internal checkout
here could not be pinned by any mechanism the estate has. That is a NEW hole
opened by adding composite actions at all, so it is closed in the same PR.

Live-extraction pattern, like tests/test_pipeline_ref_threading.py: these
parse the ACTUAL action files, so a future diff that drops a condition or adds
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
NODE_ACTION = ACTIONS_DIR / "setup-node-cached" / "action.yml"
PIPELINE_REPO = "dreadnought-foundry/bureau-pipeline"

# The key script is EXECUTED by the tests below, not just read. This repo has
# no node project of its own, so the probe builds a throwaway workspace: a
# lockfile whose exact bytes do not matter (only that they are stable across
# calls) and, when asked, a .nvmrc beside it.
NVMRC = ".nvmrc"
_LOCKFILE_BYTES = '{"lockfileVersion": 3, "packages": {}}\n'


def _run_key_script(script, working_directory, node_version="", node_version_file=""):
    """Run the action's key step in a temp workspace. Returns (rc, combined).

    `combined` is stdout plus whatever the step appended to $GITHUB_OUTPUT, so
    a caller can assert on the error message or on the emitted key with one
    return value.
    """
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "workspace"
        project = workspace / working_directory
        project.mkdir(parents=True, exist_ok=True)
        (project / "package-lock.json").write_text(_LOCKFILE_BYTES)
        if node_version_file:
            (project / node_version_file).write_text("24\n")
            node_version_file = str(Path(working_directory) / node_version_file)
        out = Path(tmp) / "github_output"
        out.touch()
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
                "WORKING_DIRECTORY": working_directory,
                "NODE_VERSION": node_version,
                "NODE_VERSION_FILE": node_version_file,
                "RUNNER_OS": "Linux",
                "RUNNER_ARCH": "X64",
                "GITHUB_OUTPUT": str(out),
            },
        )
        return proc.returncode, proc.stdout + proc.stderr + out.read_text()


def _digest_of(combined):
    """The trailing digest of the emitted `key=` line."""
    for line in combined.splitlines():
        if line.startswith("key="):
            return line.rsplit("-", 1)[-1]
    raise AssertionError(f"no key= line emitted:\n{combined}")

# Floor against a vacuous sweep: if a path typo makes the directory glob find
# nothing, every "no violations" assertion below passes for the wrong reason.
# This is the Wave 0 lesson — a guard whose output nobody reads.
KNOWN_ACTION_FLOOR = 1


def _load(path):
    return yaml.safe_load(path.read_text())


def _action_files():
    return sorted(ACTIONS_DIR.glob("*/action.yml"))


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


class SweepIsNotVacuous(unittest.TestCase):
    def test_actions_directory_exists(self):
        self.assertTrue(
            ACTIONS_DIR.is_dir(),
            f"{ACTIONS_DIR} is missing — the shared plumbing has no home, and "
            f"every 'no violations' assertion in this file would pass vacuously",
        )

    def test_at_least_the_known_actions_are_found(self):
        found = _action_files()
        self.assertGreaterEqual(
            len(found),
            KNOWN_ACTION_FLOOR,
            f"found {len(found)} composite actions, expected at least "
            f"{KNOWN_ACTION_FLOOR} — the glob went vacuous or actions vanished",
        )


class NodeActionShape(unittest.TestCase):
    """The action exists, is composite, and takes what the fleet calls it with."""

    def setUp(self):
        self.assertTrue(
            NODE_ACTION.is_file(),
            f"{NODE_ACTION} is missing — the fleet's CI has nothing to call, so "
            f"each repo keeps its own copy of the plumbing (DRE-2550)",
        )
        self.doc = _load(NODE_ACTION)

    def test_is_a_composite_action(self):
        self.assertEqual((self.doc.get("runs") or {}).get("using"), "composite")

    def test_declares_the_inputs_callers_use(self):
        inputs = self.doc.get("inputs") or {}
        for name in ("working-directory", "node-version", "node-version-file", "install"):
            self.assertIn(
                name,
                inputs,
                f"input {name!r} is gone — a consumer repo calling it would fail "
                f"with an unhelpful 'unexpected input' warning and then install "
                f"nothing",
            )
        self.assertTrue(
            inputs["working-directory"].get("required"),
            "working-directory must be required — defaulting it to the repo root "
            "would silently cache the wrong project's node_modules",
        )

    def test_install_defaults_to_installing(self):
        # A default of anything else turns every existing caller into a job
        # that sets up node and then runs a suite with no dependencies.
        self.assertEqual((self.doc["inputs"]["install"]).get("default"), "true")

    def test_exposes_cache_hit_so_a_saving_can_be_reported(self):
        outputs = self.doc.get("outputs") or {}
        self.assertIn(
            "cache-hit",
            outputs,
            "without a cache-hit output, a hit is indistinguishable from a very "
            "fast install when measuring the saving — the wave's exit test needs "
            "the observation, not an inference",
        )
        self.assertIn("steps.restore.outputs.cache-hit", str(outputs["cache-hit"].get("value")))

    def test_every_run_step_declares_a_shell(self):
        # A composite `run` step without `shell:` is a hard runtime error. It
        # cannot be caught by a YAML parse, so it surfaces in a CONSUMER repo's
        # CI, on an unrelated card.
        for step in _steps(self.doc):
            if "run" in step:
                self.assertIn(
                    "shell",
                    step,
                    f"step {step.get('name')!r} runs a script with no shell: — "
                    f"that is a runtime failure in every repo that calls this",
                )

    def test_does_not_hardcode_a_node_version(self):
        # The action's job is to take the version from the caller. A literal
        # here would override .nvmrc for the whole fleet from one file.
        setup = _step_named(self.doc, "set up node")
        self.assertIsNotNone(setup, "the action no longer sets up node at all")
        with_ = setup.get("with") or {}
        for field in ("node-version", "node-version-file"):
            self.assertIn(
                "inputs.",
                str(with_.get(field, "")),
                f"{field} is not threaded from inputs — the action would impose "
                f"one node version on every consumer repo",
            )


class CacheBreakRule(unittest.TestCase):
    """The properties that make a cached node_modules trustworthy."""

    def setUp(self):
        self.assertTrue(NODE_ACTION.is_file(), f"{NODE_ACTION} is missing")
        self.doc = _load(NODE_ACTION)
        self.text = NODE_ACTION.read_text()

    def test_no_restore_keys_anywhere(self):
        # Exact match or a full install. restore-keys would hand a job a
        # node_modules built from a DIFFERENT lockfile, which presents as a
        # test failure with no connection to the cache.
        for step in _steps(self.doc):
            self.assertNotIn(
                "restore-keys",
                step.get("with") or {},
                f"step {step.get('name')!r} added restore-keys — a partial cache "
                f"hit is worse than a miss for node_modules",
            )

    def test_key_covers_both_the_lockfile_and_the_node_identity(self):
        key_step = _step_by_id(self.doc, "key")
        self.assertIsNotNone(key_step, "the key-computing step (id: key) is gone")
        script = key_step["run"]
        self.assertIn("package-lock.json", script, "the key ignores the lockfile")
        self.assertIn(
            "NODE_VERSION_FILE",
            script,
            "the key ignores .nvmrc — bumping the pinned node major would reuse "
            "the previous major's compiled artefacts",
        )
        self.assertIn("NODE_VERSION", script, "the key ignores the literal version")
        # Both must reach the digest that becomes the key, not merely be read.
        self.assertIn("digest=", script)
        self.assertIn("$digest", script.split("key=", 1)[1])

    def test_the_key_script_refuses_to_run_with_no_node_version(self):
        # Probed, not assumed (see the module docstring's origin note): a caller
        # that forgets both version inputs must fail HERE, loudly. Falling
        # through would build a key with an empty node identity, so two
        # different majors would share one cache entry.
        script = _step_by_id(self.doc, "key")["run"]
        rc, out = _run_key_script(script, working_directory="website")
        self.assertEqual(
            rc, 1, f"expected a hard failure with no node version, got rc={rc}: {out}"
        )
        self.assertIn("neither node-version nor node-version-file", out)

    def test_the_key_ignores_where_the_version_was_read_from(self):
        # A project moving a literal into a .nvmrc changes how it pins node,
        # not what gets installed. Charging it a cache miss would be a defect.
        script = _step_by_id(self.doc, "key")["run"]
        _, from_literal = _run_key_script(script, "website", node_version="24")
        _, from_file = _run_key_script(script, "website", node_version_file=NVMRC)
        self.assertEqual(_digest_of(from_literal), _digest_of(from_file))

    def test_a_different_node_major_is_a_different_key(self):
        script = _step_by_id(self.doc, "key")["run"]
        _, on_24 = _run_key_script(script, "website", node_version="24")
        _, on_20 = _run_key_script(script, "website", node_version="20")
        self.assertNotEqual(
            _digest_of(on_24),
            _digest_of(on_20),
            "the same lockfile keys identically across node majors — one job "
            "would be handed the binaries another major compiled",
        )

    def test_caller_input_reaches_the_script_through_env_not_interpolation(self):
        # House rule from the pipeline's own workflows: a caller-supplied value
        # is never interpolated into a shell line.
        key_step = _step_by_id(self.doc, "key")
        self.assertIn("WORKING_DIRECTORY", key_step.get("env") or {})
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
            "npm ci, which is the cost this action exists to remove",
        )

    def test_install_uses_npm_ci_not_npm_install(self):
        # npm ci installs the lockfile exactly and fails when package.json and
        # the lockfile disagree. That failure is what makes the key honest.
        install = _step_named(self.doc, "install dependencies")
        self.assertIn("npm ci", install.get("run", ""))
        self.assertNotIn("npm install", install.get("run", ""))

    def test_the_cache_is_only_saved_after_an_actual_install(self):
        save = _step_named(self.doc, "save node_modules")
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
        restore = _step_named(self.doc, "restore node_modules")
        save = _step_named(self.doc, "save node_modules")
        self.assertEqual((restore["with"])["key"], (save["with"])["key"])
        self.assertEqual((restore["with"])["path"], (save["with"])["path"])


class NoCompositeActionEscapesThePin(unittest.TestCase):
    """DRE-2026's hole, closed where composite actions open it.

    check_pipeline_ref.py requires every internal bureau-pipeline checkout to
    thread `pipeline_ref`, so a stub pinned to a tag does not silently run
    scripts from main. It sweeps .github/workflows ONLY. A composite action is
    outside that sweep, and `uses:` cannot carry an expression — so a checkout
    of this repo from inside an action could not be pinned at all, by anything.
    """

    def test_no_action_checks_out_the_pipeline_repo(self):
        found = _action_files()
        self.assertGreaterEqual(len(found), KNOWN_ACTION_FLOOR, "sweep went vacuous")
        for path in found:
            doc = _load(path)
            for step in _steps(doc):
                if not str(step.get("uses", "")).startswith("actions/checkout"):
                    continue
                repo = (step.get("with") or {}).get("repository")
                self.assertNotEqual(
                    repo,
                    PIPELINE_REPO,
                    f"{path.name} checks out {PIPELINE_REPO} — a composite action "
                    f"cannot thread pipeline_ref (no expression is allowed in "
                    f"`uses:`), so this checkout escapes the DRE-2026 pin "
                    f"entirely and a tagged release would run main's scripts",
                )


if __name__ == "__main__":
    unittest.main()
