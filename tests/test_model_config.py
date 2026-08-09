"""config/models.yaml is the ONE model config, and the CI path reads it
(DRE-2316).

The problem this pins shut
--------------------------
The model each agent ran on was declared twice, in two languages, and the half
that actually ran ignored the half a human would edit:

  * ``agents.yaml`` declared ``model:`` and a 5x copy-pasted ``ladder:`` block
    for ten agents. Only the CONSOLE read it.
  * The CI path read the Python constant ``model_fallback.LADDER``. Nothing on
    that path performed a YAML load of ``agents.yaml`` — the three workflow
    references to it were comments.
  * The critic, verifier and medic skipped selection entirely: ``--model
    claude-sonnet-4-6`` was pinned in five places across three workflow files.

A config surface with one consumer is decoration. So: ``config/models.yaml`` is
canonical, every mirror of it is GENERATED between explicit markers by
``scripts/sync_model_config.py``, a drift ``--check`` fails red, and every agent
— build and review alike — resolves its model through the same ladder walk.

The house pattern is ``config/repo-map.json`` (DRE-1624/1626): canonical file,
generated region, regeneration script, drift gate. These tests are the
model-config sibling of ``tests/test_sync_fallback_map.py`` /
``tests/test_repo_map_snapshot.py``.

The hard constraint the shape obeys: product-repo workflows have NO AWS
credentials and NO private-repo token, so the config must be a FILE in the
public ``.bureau-pipeline`` checkout the workflow already does — never a runtime
lookup.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "models.yaml"
MODEL_FALLBACK = ROOT / "scripts" / "model_fallback.py"
SYNC_SCRIPT = ROOT / "scripts" / "sync_model_config.py"
AGENTS = ROOT / "agents.yaml"
WORKFLOWS = sorted(glob.glob(str(ROOT / ".github" / "workflows" / "*.yml")))

sys.path.insert(0, str(ROOT / "scripts"))

import model_fallback as mf  # noqa: E402

OPUS = "claude-opus-5"
SONNET = "claude-sonnet-4-6"
FABLE = "claude-fable-5"
RETIRED_OPUS = "claude-opus-4-8"

# The marker phrases the generator splices on — same shape as the repo-map
# mirror's "BEGIN generated repo mirror" / "END generated repo mirror".
PY_BEGIN = "BEGIN generated model config"
PY_END = "END generated model config"
YAML_BEGIN = "BEGIN generated model"
YAML_END = "END generated model"

# The one command that repairs a stale mirror. A --check failure must name it
# verbatim so the fix is copy-pasteable out of the CI log.
REPAIR_COMMAND = "python3 scripts/sync_model_config.py"

# The minimal tree the generator + selector need, so writer mode is exercised
# without ever mutating the real working copy.
_TREE_FILES = (
    "scripts/model_fallback.py",
    "scripts/sync_model_config.py",
    "config/models.yaml",
    "agents.yaml",
)


def _copy_tree(tmp: Path) -> Path:
    for rel in _TREE_FILES:
        dest = tmp / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dest)
    return tmp


def _run_sync(tree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tree / "scripts" / "sync_model_config.py"), *args],
        capture_output=True,
        text=True,
    )


def _cli_select(tree: Path, agent: str, available=None) -> str:
    """Run the selector CLI in `tree` with a STUBBED availability probe (the
    BUREAU_FAKE_AVAILABLE hook) — the workflows call this entry point, and no
    test may touch the network."""
    env = dict(os.environ)
    env["BUREAU_FAKE_AVAILABLE"] = json.dumps(
        available if available is not None else {OPUS: True, SONNET: True, FABLE: True}
    )
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select", agent],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"select {agent} failed: {proc.stdout}\n{proc.stderr}"
    return proc.stdout.strip()


def _agents_registry(path: Path = AGENTS) -> dict:
    with open(path) as f:
        return {a["name"]: a for a in yaml.safe_load(f)["agents"]}


def _canonical(path: Path = CONFIG) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _ladder_for_role(cfg: dict, role: str) -> list:
    """The ordered ladder a role walks: role → KIND → ladder. Roles are
    assigned a KIND (workhorse/advisory), never a raw ladder name (DRE-2317)."""
    return _ladder_models(cfg["ladders"][cfg["kinds"][cfg["agents"][role]]["ladder"]])


def _ladder_models(entries) -> list:
    """A canonical ladder is a list of {model:, reason:} rungs — the readable
    shape agents.yaml used to carry. Return just the ordered model ids."""
    out = []
    for rung in entries:
        assert isinstance(rung, dict) and "model" in rung, f"bad rung {rung!r}"
        out.append(rung["model"])
    return out


class CanonicalConfigTest(unittest.TestCase):
    """config/models.yaml exists, is readable, and covers the whole fleet."""

    def test_config_file_exists(self):
        self.assertTrue(
            CONFIG.is_file(),
            "config/models.yaml is the ONE file a human edits to change models",
        )

    def test_config_declares_named_ladders_and_a_default(self):
        cfg = _canonical()
        self.assertIn("ladders", cfg)
        self.assertIn("default_ladder", cfg)
        self.assertIn(cfg["default_ladder"], cfg["ladders"])
        for name, rungs in cfg["ladders"].items():
            self.assertTrue(rungs, f"ladder {name} is empty")
            models = _ladder_models(rungs)
            self.assertEqual(
                len(set(models)), len(models), f"ladder {name} repeats a model"
            )

    def test_every_registry_agent_is_assigned_a_kind(self):
        # The registry and the config cover the same fleet: an agent added to
        # one and not the other is exactly the drift this card removes. Since
        # DRE-2317 the assignment is a KIND (workhorse/advisory), which is what
        # keeps a role from being pointed at an arbitrary ladder.
        cfg = _canonical()
        self.assertEqual(
            sorted(cfg["agents"]),
            sorted(_agents_registry()),
            "config/models.yaml `agents:` must name every agents.yaml agent",
        )
        for name, kind in cfg["agents"].items():
            self.assertIn(kind, cfg["kinds"], f"{name}: unknown kind {kind!r}")

    def test_workhorse_ladder_is_opus_first_and_fable_free(self):
        # The 2026-08-09 policy survives the move: builds start at Opus and
        # Fable is not reachable from a build ladder at any availability.
        cfg = _canonical()
        workhorse = _ladder_models(cfg["ladders"][cfg["default_ladder"]])
        self.assertEqual(workhorse[0], OPUS)
        self.assertNotIn(FABLE, workhorse)

    def test_review_agents_share_one_kind(self):
        # critic/verifier/medic were the three that bypassed selection with a
        # hardcoded string. They read and judge rather than build — one kind.
        cfg = _canonical()
        tiers = {cfg["agents"][name] for name in ("critic", "verifier", "medic")}
        self.assertEqual(tiers, {"advisory"}, f"review agents split across {tiers}")

    def test_retired_and_excluded_ids_are_readable_not_selectable(self):
        # `model-attempt:`/`model-error:` markers on in-flight cards must keep
        # attributing after a model leaves the ladder (the RETIRED_MODELS
        # contract) — so the ids stay in the config, off every ladder.
        cfg = _canonical()
        readable = set(_ladder_models(cfg.get("retired") or [])) | set(
            _ladder_models(cfg.get("excluded") or [])
        )
        self.assertIn(RETIRED_OPUS, readable)
        # FABLE is no longer read-only: DRE-2317 put it on the ADVISORY ladder,
        # off every build ladder. Its attribution comes from being selectable
        # there, and tests/test_model_policy.py pins that it stays off the hot
        # path at every availability.
        self.assertIn(FABLE, mf.KNOWN_MODELS)
        for name, rungs in cfg["ladders"].items():
            for model in _ladder_models(rungs):
                self.assertNotIn(
                    model, readable, f"{model} is both selectable and retired"
                )


class SelectResolvesFromConfigTest(unittest.TestCase):
    """model_fallback resolves its ladder FROM the config — not from a Python
    constant a human maintains separately."""

    def setUp(self):
        mf.clear_availability_cache()

    def tearDown(self):
        mf.clear_availability_cache()

    def test_ladders_come_from_the_canonical_file(self):
        cfg = _canonical()
        for name, rungs in cfg["ladders"].items():
            self.assertEqual(
                mf.LADDERS[name],
                _ladder_models(rungs),
                f"ladder {name} drifted from config/models.yaml",
            )

    def test_module_ladder_is_the_configs_default_ladder(self):
        cfg = _canonical()
        self.assertEqual(
            mf.LADDER, _ladder_models(cfg["ladders"][cfg["default_ladder"]])
        )

    def test_ladder_for_resolves_each_agents_assignment(self):
        cfg = _canonical()
        for name in cfg["agents"]:
            self.assertEqual(
                mf.ladder_for(name),
                _ladder_for_role(cfg, name),
                f"{name}: ladder_for() != its config assignment",
            )

    def test_unknown_agent_falls_back_to_the_default_ladder(self):
        # select() sits on the dispatch path of every card: an unrecognized
        # role must degrade to the workhorse ladder, never raise.
        self.assertEqual(mf.ladder_for("no-such-agent"), mf.LADDER)
        self.assertEqual(
            mf.select("no-such-agent", probe=lambda m: True), mf.LADDER[0]
        )

    def test_review_agents_select_their_own_tier_not_the_workhorse(self):
        # The regression this card exists to prevent: the critic must not
        # silently ride the build ladder (or vice versa).
        cfg = _canonical()
        reviewer = _ladder_for_role(cfg, "critic")
        for name in ("critic", "verifier", "medic"):
            with self.subTest(agent=name):
                mf.clear_availability_cache()
                self.assertEqual(
                    mf.select(name, probe=lambda m: True), reviewer[0]
                )

    def test_known_models_are_derived_from_the_config(self):
        cfg = _canonical()
        expected = set()
        for rungs in cfg["ladders"].values():
            expected |= set(_ladder_models(rungs))
        expected |= set(_ladder_models(cfg.get("retired") or []))
        expected |= set(_ladder_models(cfg.get("excluded") or []))
        self.assertEqual(mf.KNOWN_MODELS, expected)
        # And attribution still works for an id that left the ladder.
        self.assertEqual(
            mf.last_error_model([mf.error_marker(RETIRED_OPUS)]), RETIRED_OPUS
        )
        self.assertEqual(mf.last_error_model([mf.error_marker(FABLE)]), FABLE)


class GeneratedMirrorTest(unittest.TestCase):
    """Every copy of the canonical config sits between explicit markers, is
    produced by the script, and CI fails red when it drifts."""

    def test_python_mirror_sits_between_comment_markers(self):
        source = MODEL_FALLBACK.read_text()
        lines = source.splitlines()
        begin = [i for i, ln in enumerate(lines) if PY_BEGIN in ln]
        end = [i for i, ln in enumerate(lines) if PY_END in ln]
        self.assertEqual(len(begin), 1, f"expected one '{PY_BEGIN}' marker line")
        self.assertEqual(len(end), 1, f"expected one '{PY_END}' marker line")
        literal = [
            i
            for i, ln in enumerate(lines)
            if ln.startswith("_FALLBACK_MODEL_CONFIG = {")
        ]
        self.assertEqual(len(literal), 1, "expected one _FALLBACK_MODEL_CONFIG")
        self.assertLess(begin[0], literal[0])
        self.assertLess(literal[0], end[0])
        for ln in lines:
            if PY_BEGIN in ln or PY_END in ln:
                self.assertTrue(
                    ln.lstrip().startswith("#"),
                    f"marker must be a comment: {ln!r}",
                )

    def test_registry_model_lines_are_generated_regions(self):
        # agents.yaml keeps `model:` (the console roster's contract) — but as a
        # GENERATED line, one marked region per agent, never a hand edit.
        lines = AGENTS.read_text().splitlines()
        begins = [i for i, ln in enumerate(lines) if YAML_BEGIN in ln]
        ends = [i for i, ln in enumerate(lines) if YAML_END in ln]
        agents = _agents_registry()
        self.assertEqual(
            len(begins), len(agents), "one generated model region per agent"
        )
        self.assertEqual(len(ends), len(begins))
        for ln in lines:
            if YAML_BEGIN in ln or YAML_END in ln:
                self.assertTrue(
                    ln.lstrip().startswith("#"),
                    f"marker must be a YAML comment: {ln!r}",
                )

    def test_registry_model_matches_the_top_of_its_configured_ladder(self):
        cfg = _canonical()
        for name, agent in _agents_registry().items():
            with self.subTest(agent=name):
                top = _ladder_for_role(cfg, name)[0]
                self.assertEqual(
                    agent["model"], top,
                    f"{name}: agents.yaml model: {agent['model']} != {top}",
                )

    def test_the_duplicated_ladder_block_is_gone_from_the_registry(self):
        # It was copy-pasted 5x. The ladder now lives in ONE file; the registry
        # carries no ladder key at all.
        raw = AGENTS.read_text()
        self.assertNotRegex(
            raw, r"(?m)^\s*ladder:",
            "agents.yaml must no longer declare ladders — config/models.yaml does",
        )
        for name, agent in _agents_registry().items():
            self.assertNotIn("ladder", agent, f"{name}: stray ladder key")

    def test_python_mirror_equals_the_canonical_config(self):
        # The mirror is the degrade path (used only if the YAML is unreadable);
        # if it can disagree with canonical, the degrade path lies.
        cfg = _canonical()
        mirror = mf._FALLBACK_MODEL_CONFIG
        self.assertEqual(mirror["default_ladder"], cfg["default_ladder"])
        self.assertEqual(
            mirror["ladders"],
            {n: _ladder_models(r) for n, r in cfg["ladders"].items()},
        )
        self.assertEqual(mirror["agents"], dict(cfg["agents"]))
        # The kinds and the discovery policy are mirrored too — the degrade
        # path must carry the POLICY, not just the ladders (DRE-2317).
        self.assertEqual(
            mirror["kinds"], {k: v["ladder"] for k, v in cfg["kinds"].items()}
        )
        self.assertEqual(
            mirror["discovery"]["on_new_model"], cfg["discovery"]["on_new_model"]
        )

    def test_regeneration_of_todays_config_is_a_noop(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            before = {
                rel: (tree / rel).read_bytes()
                for rel in ("scripts/model_fallback.py", "agents.yaml")
            }
            proc = _run_sync(tree)
            self.assertEqual(
                proc.returncode, 0, f"sync failed: {proc.stdout}\n{proc.stderr}"
            )
            for rel, data in before.items():
                self.assertEqual(
                    data, (tree / rel).read_bytes(),
                    f"regenerating an in-sync {rel} must be a no-op diff",
                )

    def test_check_mode_passes_on_todays_tree(self):
        # The CI invocation: the working tree must be in sync.
        proc = _run_sync(ROOT, "--check")
        self.assertEqual(
            proc.returncode, 0,
            f"--check failed on an in-sync tree: {proc.stdout}\n{proc.stderr}",
        )

    def test_check_fails_on_a_config_only_edit_and_names_the_repair(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            _swap_workhorse(tree)
            proc = _run_sync(tree, "--check")
            self.assertNotEqual(
                proc.returncode, 0, "--check must go red when a mirror is stale"
            )
            self.assertIn(REPAIR_COMMAND, proc.stdout + proc.stderr)

    def test_writer_repairs_a_stale_mirror(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            _swap_workhorse(tree)
            write = _run_sync(tree)
            self.assertEqual(
                write.returncode, 0, f"writer failed: {write.stdout}\n{write.stderr}"
            )
            check = _run_sync(tree, "--check")
            self.assertEqual(
                check.returncode, 0,
                f"--check still red after repair: {check.stdout}\n{check.stderr}",
            )


def _swap_workhorse(tree: Path) -> None:
    """Reverse the default ladder in a throwaway tree's config — the smallest
    edit a human would make to change the fleet's model."""
    path = tree / "config" / "models.yaml"
    cfg = yaml.safe_load(path.read_text())
    default = cfg["default_ladder"]
    cfg["ladders"][default] = list(reversed(cfg["ladders"][default]))
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))


class ConfigDrivesTheFleetTest(unittest.TestCase):
    """Editing ONLY config/models.yaml changes what the fleet runs on."""

    def test_config_edit_alone_changes_what_the_ci_path_selects(self):
        # No regeneration: the CI path READS the config, so the edit is live on
        # the next dispatch. (The generated mirror is only the degrade path.)
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            self.assertEqual(_cli_select(tree, "engineer"), OPUS)
            _swap_workhorse(tree)
            self.assertEqual(
                _cli_select(tree, "engineer"), SONNET,
                "editing config/models.yaml did not change the selected model",
            )

    def test_config_edit_plus_generator_updates_every_mirror(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            _swap_workhorse(tree)
            self.assertEqual(_run_sync(tree).returncode, 0)
            # The registry the console reads:
            self.assertEqual(
                _agents_registry(tree / "agents.yaml")["engineer"]["model"], SONNET
            )
            # The Python degrade path:
            self.assertIn(
                f'"{SONNET}", "{OPUS}"',
                (tree / "scripts" / "model_fallback.py").read_text(),
            )
            # And the CI path agrees.
            self.assertEqual(_cli_select(tree, "engineer"), SONNET)

    def test_review_tier_is_editable_the_same_way(self):
        # Not just the build ladder: the critic/verifier/medic tier moves off
        # one file edit too — the whole point of folding them into selection.
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            path = tree / "config" / "models.yaml"
            cfg = yaml.safe_load(path.read_text())
            advisory = cfg["kinds"]["advisory"]["ladder"]
            # A human moving the advisory tier in a reviewed PR is the
            # SANCTIONED path — what policy validation blocks is the reverse
            # direction (the strongest model landing on a build ladder).
            cfg["ladders"][advisory] = [{"model": SONNET, "reason": "one-file edit"}]
            path.write_text(yaml.safe_dump(cfg, sort_keys=False))
            self.assertEqual(_cli_select(tree, "critic"), SONNET)
            # …and the build fleet is untouched by that edit.
            self.assertEqual(_cli_select(tree, "engineer"), OPUS)

    def test_selector_degrades_to_the_mirror_when_the_config_is_unreadable(self):
        # The config ships in the public .bureau-pipeline checkout, so it is
        # normally there — but a truncated checkout must not strand a dispatch.
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            (tree / "config" / "models.yaml").write_text("{{ not yaml\n")
            self.assertEqual(_cli_select(tree, "engineer"), OPUS)
            self.assertEqual(_cli_select(tree, "critic"), FABLE)


class WorkflowModelPinTest(unittest.TestCase):
    """The registry alone cannot see workflow drift — so read the workflows.

    Five hardcoded `--model claude-sonnet-4-6` strings (qa-review x2, verify x2,
    medic) and one `--model claude-opus-5` (agent-fix) bypassed selection
    entirely. A hardcoded id in a workflow is drift by construction: it cannot
    be changed by editing the config.
    """

    # Agent name -> the workflow that must resolve its model from the config.
    SELECTORS = {
        "critic": "qa-review.yml",
        "verifier": "verify.yml",
        "medic": "medic.yml",
        "fixer": "agent-fix.yml",
        "planner": "plan.yml",
        "repairer": "red-main-repair.yml",
    }

    def test_no_workflow_hardcodes_a_model_id(self):
        offenders = {}
        for path in WORKFLOWS:
            hits = re.findall(r"--model\s+(claude-[a-z0-9.-]+)", Path(path).read_text())
            if hits:
                offenders[os.path.basename(path)] = sorted(set(hits))
        self.assertEqual(
            offenders, {},
            "workflows must take their model from config/models.yaml via "
            f"model_fallback.py select — hardcoded pins found: {offenders}",
        )

    def test_every_agent_workflow_selects_through_model_fallback(self):
        for agent, wf in self.SELECTORS.items():
            with self.subTest(agent=agent):
                body = (ROOT / ".github" / "workflows" / wf).read_text()
                self.assertRegex(
                    body,
                    r"model_fallback\.py\s+select\s+(?:\\\s*)?" + re.escape(agent),
                    f"{wf} must select the {agent} model from the config",
                )

    def test_shared_agent_task_selects_by_role(self):
        # engineer/frontend/devops share agent-task.yml and are routed by the
        # label-derived role, so it selects with "$ROLE" rather than a name.
        body = (ROOT / ".github" / "workflows" / "agent-task.yml").read_text()
        self.assertIn('model_fallback.py select "$ROLE"', body)

    def test_agent_steps_consume_the_selected_model(self):
        # A Select-model step that nothing reads is theatre: the claude_args
        # block must interpolate the step output.
        for wf in ("qa-review.yml", "verify.yml", "medic.yml"):
            with self.subTest(workflow=wf):
                body = (ROOT / ".github" / "workflows" / wf).read_text()
                self.assertIn("--model ${{ steps.model.outputs.model }}", body)

    def test_selected_model_is_used_by_every_claude_invocation(self):
        # qa-review and verify each run TWO agent attempts (attempt 1 + retry);
        # both must ride the selected model, not just the first.
        for wf, expected in (("qa-review.yml", 2), ("verify.yml", 2)):
            with self.subTest(workflow=wf):
                body = (ROOT / ".github" / "workflows" / wf).read_text()
                self.assertEqual(
                    body.count("--model ${{ steps.model.outputs.model }}"), expected,
                    f"{wf}: both agent attempts must use the selected model",
                )


class ConfigIsBundledNotLookedUpTest(unittest.TestCase):
    """The hard constraint: product-repo workflows have no AWS credentials and
    no private-repo token, so the config is a file in the public checkout."""

    def test_selection_never_reaches_for_aws_or_a_private_repo(self):
        source = MODEL_FALLBACK.read_text() + SYNC_SCRIPT.read_text()
        for forbidden in ("boto3", "get_parameter", "gh api"):
            self.assertNotIn(
                forbidden, source,
                f"model config must not be a runtime lookup ({forbidden})",
            )

    def test_config_is_read_from_the_bundled_checkout(self):
        # The same shape validate_card.py uses for config/repo-map.json.
        self.assertIn('"config"', MODEL_FALLBACK.read_text())
        self.assertIn("models.yaml", MODEL_FALLBACK.read_text())

    def test_config_readme_documents_the_model_config(self):
        readme = (ROOT / "config" / "README.md").read_text()
        self.assertIn("models.yaml", readme)


if __name__ == "__main__":
    unittest.main()
