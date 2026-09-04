"""Workhorse vs advisory vs judgement: the critic gets the advisory model, the
planner gets the strongest one, and a new model can only auto-promote to
advisory (DRE-2317, DRE-3015).

The inversion this closes
-------------------------
The QA critic gates EVERY unattended merge — it is the correctness backstop for
a pipeline where no human reads a diff — and it ran on the CHEAPEST model we
run, while the build agents walked a ladder whose top rung was the most
expensive one available. The cheapest model was judging the most expensive
one's work. A build failure is loud; a shallow review is silent, which makes
this the more dangerous half.

The rule: **availability is not permission.** On 2026-08-09 Anthropic enabled
`claude-fable-5`, the probe stopped returning 404, and a best-first ladder
promoted the whole fleet onto it with no human deciding anything. PR #134 took
Fable off the build path as DATA. This card makes it STRUCTURAL:

  * every role is classified `workhorse` (high-volume build work) or `advisory`
    (bounded consults at decision points — the critic and verifier), and a role
    may only be assigned one of those two KINDS, never an arbitrary ladder;
  * the advisory ladder's strongest model is unreachable from any workhorse
    ladder — pinned here at every availability, which is the incident condition;
  * a model the system discovers may at most join the ADVISORY ladder, and
    `on_new_model: workhorse` is REJECTED by schema validation — that value is
    precisely the incident;
  * every run records the model it used AND why anything above it was skipped,
    so a weakened advisory model can never be silent.

The third kind (DRE-3015)
-------------------------
`judgement` holds the planner alone. It runs ONCE PER EPIC, at a decision
point, and every downstream build run is shaped by its output — an
advisory-shaped cost profile that wore a workhorse label. It walks its own
ladder, `claude-fable-5-1` → `claude-opus-5` → `claude-sonnet-4-6`, and falling
to Opus is the LOUD (DEGRADED-prefixed) fallback the advisory ladder already
does.

What DID NOT change is the 08-09 guard, and it is pinned here from BOTH sides:
the engineer's ladder cannot reach Fable at any availability, the planner's
can, and a newly-discovered model may never land on either the build or the
planning ladder by itself — `discovery.on_new_model: judgement` is refused
exactly as `workhorse` is. A human editing config/models.yaml is the only way
up.

These tests are the pin. Every one of them fails if its behaviour is removed.
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
AGENTS = ROOT / "agents.yaml"
MODEL_FALLBACK = ROOT / "scripts" / "model_fallback.py"
MODEL_CATALOG = ROOT / "scripts" / "model_catalog.py"
WORKFLOWS = ROOT / ".github" / "workflows"

sys.path.insert(0, str(ROOT / "scripts"))

import model_catalog as mc  # noqa: E402
import model_fallback as mf  # noqa: E402

OPUS = "claude-opus-5"
SONNET = "claude-sonnet-4-6"
SONNET5 = "claude-sonnet-5"
FABLE = "claude-fable-5"
# The CURRENT Fable id. `claude-fable-5` above is its predecessor, excluded from
# every ladder on cost policy (2026-08-12) and kept readable for attribution;
# `claude-fable-5-1` is a different model, and DRE-3015 puts it on the
# judgement ladder — and nowhere else.
FABLE51 = "claude-fable-5-1"

WORKHORSE = "workhorse"
ADVISORY = "advisory"
JUDGEMENT = "judgement"

# The files the generator + selector need to run against a throwaway tree, so
# schema validation is exercised without ever mutating the working copy.
_TREE_FILES = (
    "scripts/model_fallback.py",
    "scripts/sync_model_config.py",
    "config/models.yaml",
    "agents.yaml",
)

# Every workflow that selects a model, and the agent name it selects for.
SELECTORS = {
    "agent-task.yml": '"$ROLE"',
    "plan.yml": "planner",
    "qa-review.yml": "critic",
    "verify.yml": "verifier",
    "medic.yml": "medic",
    "agent-fix.yml": "fixer",
    "red-main-repair.yml": "repairer",
}


# Every file that states the role-kind rule in prose a human acts on — config,
# the selector and its generator, the roster, and the workflows that select.
# The staleness sweeps below read all of them, so a new kind cannot leave a
# sentence behind in one of them.
KIND_DOCUMENTS = (
    CONFIG,
    ROOT / "config" / "README.md",
    MODEL_FALLBACK,
    MODEL_CATALOG,
    ROOT / "scripts" / "sync_model_config.py",
    AGENTS,
    WORKFLOWS / "plan.yml",
    WORKFLOWS / "agent-task.yml",
    WORKFLOWS / "model-drift.yml",
)


def _canonical(path: Path = CONFIG) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _rung_ids(rungs) -> list:
    out = []
    for rung in rungs or []:
        out.append(rung["model"] if isinstance(rung, dict) else rung)
    return out


def _copy_tree(tmp: Path) -> Path:
    for rel in _TREE_FILES:
        dest = tmp / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dest)
    return tmp


def _write_config(tree: Path, cfg: dict) -> None:
    (tree / "config" / "models.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))


def _run_sync(tree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tree / "scripts" / "sync_model_config.py"), *args],
        capture_output=True,
        text=True,
    )


def _cli_select(tree: Path, agent: str, available=None, explain=False):
    """Run the selector CLI in `tree` with a STUBBED availability probe. Returns
    (model, explanation) — the explanation is what the workflow records."""
    env = dict(os.environ)
    env["BUREAU_FAKE_AVAILABLE"] = json.dumps(
        available if available is not None else {OPUS: True, SONNET: True, FABLE: True}
    )
    argv = [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select", agent]
    why_path = tree / "why.txt"
    if explain:
        argv += ["--explain-file", str(why_path)]
    proc = subprocess.run(argv, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"select {agent} failed: {proc.stdout}\n{proc.stderr}"
    note = why_path.read_text().strip() if explain else ""
    return proc.stdout.strip(), note


def _advisory_ladder(cfg=None) -> list:
    cfg = cfg or _canonical()
    return _rung_ids(cfg["ladders"][cfg["kinds"][ADVISORY]["ladder"]])


def _workhorse_ladder(cfg=None) -> list:
    cfg = cfg or _canonical()
    return _rung_ids(cfg["ladders"][cfg["kinds"][WORKHORSE]["ladder"]])


def _judgement_ladder(cfg=None) -> list:
    cfg = cfg or _canonical()
    return _rung_ids(cfg["ladders"][cfg["kinds"][JUDGEMENT]["ladder"]])


class RoleKindsTest(unittest.TestCase):
    """Three role kinds, and every role is classified into one of them."""

    def test_config_declares_exactly_the_three_kinds(self):
        cfg = _canonical()
        self.assertEqual(
            sorted(cfg["kinds"]), [ADVISORY, JUDGEMENT, WORKHORSE],
            "config/models.yaml declares exactly three role kinds",
        )
        ladders = {kind: spec["ladder"] for kind, spec in cfg["kinds"].items()}
        for kind, ladder in ladders.items():
            self.assertIn(ladder, cfg["ladders"], f"kind {kind}: unknown ladder")
        self.assertEqual(
            len(set(ladders.values())), len(ladders),
            "each kind gets its OWN ladder — sharing one is how build work "
            "ends up on a model nobody chose for it",
        )

    def test_every_agent_role_is_classified(self):
        cfg = _canonical()
        registry = {a["name"] for a in yaml.safe_load(AGENTS.read_text())["agents"]}
        self.assertEqual(
            sorted(cfg["agents"]), sorted(registry),
            "every agents.yaml role needs a kind in config/models.yaml",
        )
        for role, kind in cfg["agents"].items():
            self.assertIn(kind, cfg["kinds"], f"{role}: {kind!r} is not a role kind")

    def test_the_critic_and_verifier_are_advisory(self):
        cfg = _canonical()
        for role in ("critic", "verifier"):
            self.assertEqual(cfg["agents"][role], ADVISORY, f"{role} must be advisory")

    def test_the_build_roles_are_workhorse(self):
        # The planner left this list on 2026-09-03 (DRE-3015) and is the ONLY
        # role that may: it runs once per epic, not hundreds of turns per card.
        # Every role that writes code stays here.
        cfg = _canonical()
        for role in ("engineer", "frontend", "devops", "database-architect",
                     "fixer", "repairer"):
            self.assertEqual(cfg["agents"][role], WORKHORSE, f"{role} builds")

    def test_the_critic_runs_the_advisory_model(self):
        # The critic resolves to the advisory ladder's top rung from config —
        # no hardcoded string anywhere.
        #
        # RENAMED 2026-08-12 (was ..._runs_the_strongest_model). The advisory
        # ladder moved Fable -> Sonnet 5 on measured cost: 5.5x per review with
        # the rejection-rate difference inside noise. The advisory model is no
        # longer the strongest thing we can run, so a test asserting it is
        # would pin a premise we deliberately dropped.
        advisory = _advisory_ladder()
        self.assertEqual(advisory[0], SONNET5)
        mf.clear_availability_cache()
        self.assertEqual(mf.select("critic", probe=lambda m: True), SONNET5)

    def test_the_excluded_model_is_on_no_ladder_at_all(self):
        # What survives the rename above. Fable came OFF every ladder rather
        # than moving down one, so the 2026-08-09 guarantee is now "excluded
        # means unreachable" — enforced by policy rule 7 rather than implied by
        # Fable happening to be the advisory model.
        self.assertIn(FABLE, mf.CONFIG["excluded"])
        for name, models in mf.CONFIG["ladders"].items():
            with self.subTest(ladder=name):
                self.assertNotIn(FABLE, models, "an excluded model reached a ladder")

    def test_an_unknown_role_gets_the_workhorse_kind(self):
        # select() must never block a build on a role it does not recognize —
        # and an unrecognized role must land on the CHEAP side of the fence.
        cfg = _canonical()
        self.assertEqual(cfg["kinds"][WORKHORSE]["ladder"], cfg["default_ladder"])
        mf.clear_availability_cache()
        self.assertEqual(mf.kind_for("no-such-role"), WORKHORSE)
        self.assertEqual(mf.ladder_for("no-such-role"), _workhorse_ladder())

    def test_advisory_falls_back_onto_the_workhorse_model(self):
        # AC: exhaustion of the advisory model drops to the WORKHORSE model —
        # never to something weaker still, and never silently (see the note
        # tests below). That fallback target is data, so it is pinned as data.
        self.assertEqual(_advisory_ladder()[-1], _workhorse_ladder()[0])


class IncidentConditionTest(unittest.TestCase):
    """The pinned incident: every model available, and the build path STILL
    cannot reach the advisory model."""

    def setUp(self):
        mf.clear_availability_cache()

    def tearDown(self):
        mf.clear_availability_cache()

    def test_no_build_role_reaches_the_advisory_model_at_full_availability(self):
        advisory_top = _advisory_ladder()[0]
        everything_available = lambda model: True  # noqa: E731 — the probe 404s nothing
        build_roles = [r for r, k in mf.AGENT_KINDS.items() if k == WORKHORSE]
        self.assertTrue(build_roles, "there are build roles to check")
        for role in build_roles:
            with self.subTest(role=role):
                mf.clear_availability_cache()
                self.assertNotIn(
                    advisory_top, mf.ladder_for(role),
                    f"{role}'s ladder contains the advisory model",
                )
                self.assertNotEqual(
                    mf.select(role, probe=everything_available), advisory_top,
                    f"{role} selected the advisory model — this is the "
                    "2026-08-09 incident",
                )
        # An unrecognized role walks the default ladder — same guarantee.
        self.assertNotEqual(
            mf.select("no-such-role", probe=everything_available), advisory_top
        )

    def test_the_cli_path_holds_the_same_line(self):
        # The workflows call the CLI, not the function. Probe says EVERYTHING
        # is available; the build path still resolves to the workhorse model,
        # and the two non-build kinds each reach their own top rung — the
        # planner's being the one this file spends most of its words guarding.
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            all_up = {OPUS: True, SONNET: True, SONNET5: True,
                      FABLE: True, FABLE51: True}
            self.assertEqual(_cli_select(tree, "engineer", all_up)[0], OPUS)
            self.assertEqual(_cli_select(tree, "planner", all_up)[0], FABLE51)
            self.assertEqual(_cli_select(tree, "critic", all_up)[0], SONNET5)

    def test_availability_still_only_walks_down(self):
        # The other half of the rule: a probe may decide how far DOWN a ladder
        # we walk, never how far up.
        self.assertEqual(
            mf.select("engineer", probe=lambda m: m != OPUS), SONNET
        )


class JudgementKindTest(unittest.TestCase):
    """The third kind (DRE-3015): the planner runs on the strongest model, and
    the 2026-08-09 guard is pinned from BOTH sides — the engineer's ladder still
    cannot reach Fable at any availability, the planner's can."""

    def setUp(self):
        mf.clear_availability_cache()

    def tearDown(self):
        mf.clear_availability_cache()

    def test_the_judgement_kind_holds_the_planner_alone(self):
        cfg = _canonical()
        holders = sorted(r for r, k in cfg["agents"].items() if k == JUDGEMENT)
        self.assertEqual(
            holders, ["planner"],
            "judgement is for judgement-heavy, LOW-VOLUME roles; a role that "
            "runs per card belongs on the workhorse ladder",
        )

    def test_the_judgement_ladder_is_fable_first_then_the_build_models(self):
        # Data, so it is pinned as data: the strongest model, then the two
        # workhorse rungs as the deliberate (loud) degrade path.
        self.assertEqual(_judgement_ladder(), [FABLE51, OPUS, SONNET])
        self.assertEqual(_judgement_ladder()[1], _workhorse_ladder()[0])

    def test_the_planner_runs_on_fable_when_it_is_available(self):
        self.assertEqual(mf.kind_for("planner"), JUDGEMENT)
        self.assertEqual(mf.select("planner", probe=lambda m: True), FABLE51)

    def test_the_planner_reaches_fable_at_every_availability_that_allows_it(self):
        # The other side of the 08-09 pin. Whatever the rest of the ladder is
        # doing, an available Fable is the planner's model — that is the whole
        # decision, and a config edit that quietly stopped honouring it would
        # look exactly like a healthy run.
        for avail in (
            {FABLE51: True, OPUS: True, SONNET: True},
            {FABLE51: True, OPUS: False, SONNET: True},
            {FABLE51: True, OPUS: False, SONNET: False},
        ):
            with self.subTest(avail=avail):
                mf.clear_availability_cache()
                self.assertEqual(
                    mf.select("planner", probe=lambda m: avail.get(m, True)),
                    FABLE51,
                )

    def test_falling_to_opus_is_the_loud_fallback(self):
        # Exactly as the advisory ladder does it: DEGRADED-prefixed, naming
        # what was skipped and why, on one line the workflow turns into a
        # ::warning::. A planner that quietly ran on Opus would be
        # indistinguishable from one that got what it was promised.
        decision = mf.select_with_reasons("planner", probe=lambda m: m != FABLE51)
        self.assertEqual(decision["model"], OPUS)
        self.assertEqual(decision["kind"], JUDGEMENT)
        self.assertEqual([s["model"] for s in decision["skipped"]], [FABLE51])
        note = mf.selection_note(decision)
        self.assertEqual(len(note.splitlines()), 1)
        self.assertTrue(note.startswith("DEGRADED"), f"the fallback is silent: {note}")
        self.assertIn(FABLE51, note)
        self.assertIn(JUDGEMENT, note)

    def test_no_workhorse_role_reaches_fable_at_any_availability(self):
        # THE GUARD FROM 2026-08-09, unchanged. The probe reporting Fable up is
        # precisely the incident condition, and it still buys nothing on the
        # build path: every workhorse role's ladder does not contain it, so no
        # combination of availabilities can select it.
        build_roles = sorted(r for r, k in mf.AGENT_KINDS.items() if k == WORKHORSE)
        self.assertIn("engineer", build_roles)
        self.assertNotIn("planner", build_roles)
        workhorse = _workhorse_ladder()
        for role in build_roles:
            for avail in (
                {FABLE51: True, OPUS: True, SONNET: True},
                {FABLE51: True, OPUS: False, SONNET: True},
                {FABLE51: True, OPUS: True, SONNET: False},
                {FABLE51: True, OPUS: False, SONNET: False},
            ):
                with self.subTest(role=role, avail=avail):
                    mf.clear_availability_cache()
                    self.assertNotIn(FABLE51, mf.ladder_for(role))
                    got = mf.select(role, probe=lambda m: avail.get(m, True))
                    self.assertNotEqual(
                        got, FABLE51,
                        f"{role} reached Fable — this is the 2026-08-09 incident",
                    )
                    self.assertIn(got, workhorse)
        # An unrecognized role walks the default (workhorse) ladder: same line.
        mf.clear_availability_cache()
        self.assertNotEqual(mf.select("no-such-role", probe=lambda m: True), FABLE51)

    def test_the_cli_holds_that_line_too(self):
        # The workflows call the CLI, not select(). Everything up, including
        # both Fable ids.
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            all_up = {OPUS: True, SONNET: True, SONNET5: True,
                      FABLE: True, FABLE51: True}
            for role in ("engineer", "frontend", "devops", "fixer", "repairer"):
                with self.subTest(role=role):
                    self.assertEqual(_cli_select(tree, role, all_up)[0], OPUS)
            self.assertEqual(_cli_select(tree, "planner", all_up)[0], FABLE51)

    def test_fable_on_a_build_ladder_is_still_rejected(self):
        # The judgement ladder is permission for the PLANNER, not for the model.
        cfg = _canonical()
        cfg["ladders"][cfg["kinds"][WORKHORSE]["ladder"]].insert(
            0, {"model": FABLE51, "reason": "someone's well-meaning edit"}
        )
        errors = mf.policy_errors(cfg)
        self.assertTrue(errors, "Fable must not reach a build ladder")
        self.assertTrue(any(FABLE51 in e for e in errors), f"unhelpful: {errors}")

    def test_a_judgement_only_model_may_not_hide_below_the_fallback(self):
        # Rule 4 reads the TOP rung of a non-build ladder, so a premium model
        # parked BELOW the workhorse rungs would be invisible to it. Once a
        # ladder descends onto the build path it stays there.
        cfg = _canonical()
        cfg["ladders"][cfg["kinds"][JUDGEMENT]["ladder"]] = [
            {"model": OPUS, "reason": "the build model first"},
            {"model": FABLE51, "reason": "…and the premium one hidden below it"},
        ]
        self.assertTrue(
            mf.policy_errors(cfg),
            "a premium model below the workhorse fallback must be rejected",
        )

    def test_the_selector_degrades_rather_than_honouring_fable_on_the_build_path(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            cfg = _canonical()
            cfg["ladders"][cfg["kinds"][WORKHORSE]["ladder"]].insert(
                0, {"model": FABLE51, "reason": "the incident, as a config edit"}
            )
            _write_config(tree, cfg)
            model, _ = _cli_select(
                tree, "engineer", {OPUS: True, SONNET: True, FABLE51: True}
            )
            self.assertEqual(
                model, OPUS, "a policy-violating config must not reach the build path"
            )

    def test_discovery_may_not_target_the_judgement_ladder(self):
        # A newly-discovered model may never land on a BUILD **or a PLANNING**
        # ladder by itself. `judgement` is refused exactly as `workhorse` is —
        # the planner's ladder is now the strongest one we run, which makes it
        # the most attractive place for an unattended promotion to land.
        cfg = _canonical()
        cfg["discovery"]["on_new_model"] = JUDGEMENT
        errors = mf.policy_errors(cfg)
        self.assertTrue(errors, "on_new_model: judgement must be rejected")
        self.assertTrue(
            any("on_new_model" in e for e in errors), f"unhelpful errors: {errors}"
        )
        self.assertNotIn(JUDGEMENT, mf.DISCOVERY_TARGETS)

    def test_ci_goes_red_on_a_discovery_targeting_judgement(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            cfg = _canonical()
            cfg["discovery"]["on_new_model"] = JUDGEMENT
            _write_config(tree, cfg)
            proc = _run_sync(tree, "--check")
            self.assertNotEqual(proc.returncode, 0, "CI must fail on this config")
            self.assertIn("on_new_model", proc.stdout + proc.stderr)

    def test_the_registry_shows_the_planners_kind_and_model(self):
        # The roster is the surface a human reads to ask "what is the planner
        # running on, and why is it allowed to be the expensive one?"
        planner = {a["name"]: a for a in yaml.safe_load(AGENTS.read_text())["agents"]}[
            "planner"
        ]
        self.assertEqual(planner["kind"], JUDGEMENT)
        self.assertEqual(planner["model"], FABLE51)

    def test_fable_5_1_is_a_known_model_and_its_predecessor_still_is_too(self):
        # Attribution: markers in flight carry either id.
        self.assertIn(FABLE51, mf.KNOWN_MODELS)
        self.assertIn(FABLE, mf.KNOWN_MODELS)
        self.assertEqual(mf.last_error_model([mf.error_marker(FABLE51)]), FABLE51)
        self.assertEqual(mf.last_error_model([mf.error_marker(FABLE)]), FABLE)
        # And the predecessor is still on no ladder at all.
        for name, models in mf.CONFIG["ladders"].items():
            with self.subTest(ladder=name):
                self.assertNotIn(FABLE, models)


class SchemaRejectsAutoPromotionTest(unittest.TestCase):
    """`on_new_model: workhorse` — the incident as a config value — is rejected
    by schema validation, and so is every other way to put the strongest model
    on the build path."""

    def test_discovery_policy_is_advisory(self):
        cfg = _canonical()
        self.assertEqual(cfg["discovery"]["on_new_model"], ADVISORY)
        self.assertIs(cfg["discovery"]["alert"], True)

    def test_on_new_model_workhorse_is_rejected(self):
        cfg = _canonical()
        cfg["discovery"]["on_new_model"] = WORKHORSE
        errors = mf.policy_errors(cfg)
        self.assertTrue(errors, "on_new_model: workhorse must be rejected")
        self.assertTrue(
            any("on_new_model" in e for e in errors), f"unhelpful errors: {errors}"
        )

    def test_the_allowed_discovery_policies_are_accepted(self):
        for value in (ADVISORY, "none"):
            cfg = _canonical()
            cfg["discovery"]["on_new_model"] = value
            with self.subTest(policy=value):
                self.assertEqual(mf.policy_errors(cfg), [])

    def test_todays_config_passes_validation(self):
        self.assertEqual(mf.policy_errors(_canonical()), [])

    def test_the_advisory_model_on_a_build_ladder_is_rejected(self):
        cfg = _canonical()
        cfg["ladders"][cfg["kinds"][WORKHORSE]["ladder"]].insert(
            0, {"model": FABLE, "reason": "someone's well-meaning edit"}
        )
        errors = mf.policy_errors(cfg)
        self.assertTrue(errors, "the strongest model must not reach a build ladder")
        self.assertTrue(any(FABLE in e for e in errors), f"unhelpful errors: {errors}")

    def test_demoting_the_critic_to_a_build_kind_is_rejected(self):
        cfg = _canonical()
        cfg["agents"]["critic"] = WORKHORSE
        errors = mf.policy_errors(cfg)
        self.assertTrue(errors, "the critic must stay advisory")
        self.assertTrue(any("critic" in e for e in errors), f"unhelpful: {errors}")

    def test_a_role_cannot_be_pointed_at_a_raw_ladder(self):
        # Roles are assigned KINDS, not ladders: that is what stops a future
        # edit from inventing a third ladder and quietly putting builds on it.
        cfg = _canonical()
        cfg["agents"]["engineer"] = cfg["kinds"][ADVISORY]["ladder"] + "-copy"
        cfg["ladders"][cfg["agents"]["engineer"]] = [{"model": FABLE, "reason": "x"}]
        self.assertTrue(mf.policy_errors(cfg))

    def test_ci_goes_red_on_a_rejected_config(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            cfg = _canonical()
            cfg["discovery"]["on_new_model"] = WORKHORSE
            _write_config(tree, cfg)
            proc = _run_sync(tree, "--check")
            self.assertNotEqual(proc.returncode, 0, "CI must fail on this config")
            self.assertIn("on_new_model", proc.stdout + proc.stderr)

    def test_the_selector_degrades_rather_than_honouring_a_rejected_config(self):
        # A config that violates policy is NOT loaded: the fleet keeps running
        # the last-known-good ladders (and says so) instead of adopting the
        # thing the policy exists to prevent.
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            cfg = _canonical()
            cfg["ladders"][cfg["kinds"][WORKHORSE]["ladder"]].insert(
                0, {"model": FABLE, "reason": "the incident, as a config edit"}
            )
            _write_config(tree, cfg)
            model, _ = _cli_select(tree, "engineer", {OPUS: True, SONNET: True, FABLE: True})
            self.assertEqual(
                model, OPUS, "a policy-violating config must not reach the build path"
            )


class SelectionIsRecordedTest(unittest.TestCase):
    """Every run records the model it used AND why anything above it was
    skipped. A silently weakened advisory model is the failure this prevents."""

    def setUp(self):
        mf.clear_availability_cache()

    def tearDown(self):
        mf.clear_availability_cache()

    def test_the_decision_records_every_skipped_rung_and_its_reason(self):
        # The unavailable rung is the advisory TOP, whatever it currently is —
        # SONNET5 since 2026-08-12, FABLE before that.
        decision = mf.select_with_reasons("critic", probe=lambda m: m != SONNET5)
        self.assertEqual(decision["model"], OPUS)
        self.assertEqual(decision["kind"], ADVISORY)
        self.assertEqual([s["model"] for s in decision["skipped"]], [SONNET5])
        self.assertTrue(decision["skipped"][0]["reason"])
        self.assertTrue(decision["degraded"])

    def test_an_inconclusive_probe_is_recorded_differently_from_a_404(self):
        def probe(model):
            if model == SONNET5:
                raise RuntimeError("probe blew up")
            return True

        decision = mf.select_with_reasons("critic", probe=probe)
        self.assertEqual(decision["model"], OPUS)
        self.assertIn("inconclusive", decision["skipped"][0]["reason"].lower())

    def test_the_top_rung_records_that_nothing_was_skipped(self):
        decision = mf.select_with_reasons("engineer", probe=lambda m: True)
        self.assertEqual(decision["model"], OPUS)
        self.assertEqual(decision["skipped"], [])
        self.assertFalse(decision["degraded"])
        self.assertIn("nothing", mf.selection_note(decision).lower())

    def test_the_note_is_one_line_and_names_what_was_skipped_and_why(self):
        decision = mf.select_with_reasons("critic", probe=lambda m: m != SONNET5)
        note = mf.selection_note(decision)
        self.assertEqual(len(note.splitlines()), 1, "the note is a single line")
        self.assertIn(OPUS, note)
        self.assertIn(SONNET5, note)
        self.assertIn("skip", note.lower())

    def test_a_weakened_advisory_model_is_marked_degraded_loudly(self):
        # The alert half of the AC: an advisory role that did not get its
        # INTENDED model announces it. NOTE the meaning inverted on 2026-08-12
        # — falling from Sonnet 5 to Opus is falling UP in cost ($2/$10 ->
        # $5/$25), so this now reads "unexpected spend", not "quietly cheap".
        # Either way the run is worth looking at, which is why it stays loud.
        decision = mf.select_with_reasons("critic", probe=lambda m: m != SONNET5)
        note = mf.selection_note(decision)
        self.assertTrue(
            note.startswith("DEGRADED"),
            f"a weakened advisory model must be loud, got: {note}",
        )

    def test_the_cli_writes_the_explanation_for_the_workflow(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            model, note = _cli_select(
                tree, "critic",
                {OPUS: True, SONNET: True, SONNET5: False}, explain=True
            )
            # stdout stays exactly the model id — the workflows capture it.
            self.assertEqual(model, OPUS)
            self.assertTrue(note.startswith("DEGRADED"), note)
            self.assertIn(SONNET5, note)

    def test_every_selecting_workflow_records_the_explanation(self):
        for wf, agent in SELECTORS.items():
            with self.subTest(workflow=wf):
                body = (WORKFLOWS / wf).read_text()
                self.assertIn(
                    "--explain-file", body,
                    f"{wf}: selection does not record why anything was skipped",
                )
                self.assertIn("why=$WHY", body, f"{wf}: the note is not an output")
                self.assertIn(
                    "::warning::", body, f"{wf}: a degraded selection is silent"
                )

    def test_the_card_heartbeat_carries_the_explanation(self):
        # agent-task.yml:303 already records the selection; the "why skipped"
        # half rides the same heartbeat so the board shows both.
        for wf in ("agent-task.yml", "plan.yml"):
            with self.subTest(workflow=wf):
                body = (WORKFLOWS / wf).read_text()
                heartbeat = [
                    line for line in body.splitlines() if "model-attempt:" in line
                ]
                self.assertTrue(heartbeat, f"{wf}: no model-attempt heartbeat")
                self.assertTrue(
                    any("steps.model.outputs.why" in line for line in heartbeat),
                    f"{wf}: the heartbeat records the model but not why",
                )


class DiscoveryJoinsAdvisoryOnlyTest(unittest.TestCase):
    """A model id the system sees that is absent from config raises an alert
    and, at most, joins the advisory ladder — never the build path."""

    def test_a_model_absent_from_config_is_discovered(self):
        catalog = [
            {"id": OPUS, "display_name": "Opus 5", "created_at": "2026-08-01T00:00:00Z"},
            {"id": "claude-nova-9", "display_name": "Nova 9",
             "created_at": "2026-09-01T00:00:00Z"},
        ]
        found = [e["id"] for e in mc.new_models(catalog)]
        self.assertEqual(found, ["claude-nova-9"])

    def test_known_ids_and_dated_snapshots_are_not_discoveries(self):
        catalog = [
            {"id": SONNET, "display_name": None, "created_at": None},
            {"id": FABLE, "display_name": None, "created_at": None},
            {"id": "claude-opus-4-8", "display_name": None, "created_at": None},
            {"id": f"{OPUS}-20260801", "display_name": None, "created_at": None},
        ]
        self.assertEqual(mc.new_models(catalog), [])

    def test_a_model_we_have_already_seen_is_not_rediscovered(self):
        # "New" means new to US: an id already recorded in the committed
        # snapshot is not a discovery, however it got there. Without this the
        # weekly watch would re-alert on every model Anthropic offers that we
        # simply do not run — noise that trains the alert into wallpaper.
        catalog = [
            {"id": "claude-nova-9", "display_name": None, "created_at": None},
            {"id": "claude-relic-2", "display_name": None, "created_at": None},
        ]
        known = set(mf.KNOWN_MODELS) | {"claude-relic-2"}
        self.assertEqual(
            [e["id"] for e in mc.new_models(catalog, known_ids=known)],
            ["claude-nova-9"],
        )

    def test_the_title_is_deterministic_so_the_card_dedupes(self):
        # linear_ops.py find-open matches an EXACT title. Two runs seeing the
        # same set of models in a different order must produce the SAME title,
        # or the weekly watch mints a duplicate card every Monday.
        a = [
            {"id": "claude-nova-9", "display_name": None, "created_at": None},
            {"id": "claude-aria-2", "display_name": None, "created_at": None},
        ]
        self.assertEqual(mc.new_model_title(a), mc.new_model_title(list(reversed(a))))

    def test_a_long_finding_still_makes_a_readable_title(self):
        entries = [
            {"id": f"claude-model-{i}", "display_name": None, "created_at": None}
            for i in range(12)
        ]
        title = mc.new_model_title(entries)
        self.assertLess(len(title), 160, f"unreadable card title: {title}")
        # The body stays complete even when the title elides.
        body = mc.new_model_body(entries)
        for entry in entries:
            self.assertIn(entry["id"], body)

    def test_the_workflow_compares_against_the_previous_snapshot(self):
        # The snapshot refresh runs FIRST, so comparing the new catalog against
        # the file it just wrote would find nothing, ever. The baseline is the
        # snapshot as it stood BEFORE the refresh.
        drift = (WORKFLOWS / "model-drift.yml").read_text()
        self.assertIn("--baseline", drift, "check-new has no previous-state baseline")

    def test_the_baseline_suppresses_a_model_already_recorded(self):
        payload = {"data": [{"id": "claude-nova-9", "display_name": "Nova 9",
                             "created_at": "2026-09-01T00:00:00Z"}]}
        with tempfile.TemporaryDirectory() as td:
            baseline = Path(td) / "previous.json"
            baseline.write_text(json.dumps(
                {"source": "x", "models": [{"id": "claude-nova-9",
                                            "display_name": "Nova 9",
                                            "created_at": None,
                                            "in_catalog": True}]}
            ))
            env = dict(os.environ, BUREAU_FAKE_CATALOG=json.dumps(payload))

            def run(*args):
                return subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "model_catalog.py"),
                     "check-new", *args],
                    capture_output=True, text=True, env=env,
                )

            # Control: with no baseline this model IS a discovery…
            self.assertEqual(run().returncode, 3)
            # …and with the baseline that already records it, it is not.
            proc = run("--baseline", str(baseline))
            self.assertEqual(
                proc.returncode, 0,
                f"an already-recorded model must not re-alert: {proc.stdout}",
            )

    def test_the_discovery_target_is_the_advisory_ladder(self):
        self.assertEqual(mc.discovery_policy()["on_new_model"], ADVISORY)

    def test_the_alert_proposes_advisory_and_forbids_the_build_path(self):
        entries = [
            {"id": "claude-nova-9", "display_name": "Nova 9",
             "created_at": "2026-09-01T00:00:00Z"}
        ]
        title = mc.new_model_title(entries)
        body = mc.new_model_body(entries)
        self.assertIn("claude-nova-9", title)
        self.assertIn("claude-nova-9", body)
        self.assertIn(ADVISORY, body.lower())
        # It must say, in words, that the build path is off limits.
        self.assertRegex(
            body.lower(), r"never.{0,80}(workhorse|build path|build ladder)"
        )
        # And it must not claim anything was adopted.
        self.assertIn("nothing has been changed automatically", body.lower())

    def test_discovery_never_writes_the_config(self):
        # Structural: the discovery path is data + an alert. Nothing in it can
        # edit the one file that decides spend.
        source = MODEL_CATALOG.read_text()
        self.assertNotRegex(
            source, r"open\([^)]*models\.yaml",
            "the catalog must never open the one file that decides spend",
        )
        self.assertNotRegex(
            source, r"write_text\(|\.write\([^)]*ladder",
            "the catalog writes data and cards, never a ladder",
        )

    def test_the_cli_reports_a_new_model_and_the_workflow_alerts(self):
        payload = {
            "data": [
                {"id": "claude-nova-9", "display_name": "Nova 9",
                 "created_at": "2026-09-01T00:00:00Z"},
            ]
        }
        with tempfile.TemporaryDirectory() as td:
            title = Path(td) / "t.txt"
            body = Path(td) / "b.md"
            env = dict(os.environ, BUREAU_FAKE_CATALOG=json.dumps(payload))
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "model_catalog.py"),
                 "check-new", "--title-file", str(title), "--body-file", str(body)],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(proc.returncode, 3, f"{proc.stdout}\n{proc.stderr}")
            self.assertIn("claude-nova-9", title.read_text())
            self.assertIn(ADVISORY, body.read_text().lower())

        drift = (WORKFLOWS / "model-drift.yml").read_text()
        self.assertIn("check-new", drift, "the discovery alert is not wired up")
        self.assertIn("find-open", drift, "the alert must be idempotent")


class DocumentedPolicyTest(unittest.TestCase):
    """The rule is written down where the next well-meaning edit will read it."""

    def test_the_config_names_every_kind_and_the_rule(self):
        text = CONFIG.read_text().lower()
        for phrase in ("workhorse", "advisory", "judgement",
                       "availability is not permission"):
            self.assertIn(phrase, text, f"config/models.yaml must state: {phrase}")

    def test_the_readme_documents_the_three_kinds(self):
        readme = (ROOT / "config" / "README.md").read_text().lower()
        self.assertIn("advisory", readme)
        self.assertIn("workhorse", readme)
        self.assertIn("judgement", readme)
        self.assertIn("on_new_model", readme)

    def test_no_document_still_calls_it_a_two_kind_rule(self):
        # A change that contradicts a document updates that document in the
        # SAME PR. "Two role kinds" was true until 2026-09-03 and is now the
        # kind of sentence a reader trusts and acts on.
        stale = []
        for path in KIND_DOCUMENTS:
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"(two|2)\s+(role\s+)?kinds?\b", line, re.I):
                    stale.append(f"{path.name}:{n}: {line.strip()}")
        self.assertEqual(stale, [], "a document still says there are two kinds")

    def test_no_document_closes_the_list_of_kinds_on_a_short_one(self):
        # The other half of the same drift, and the half that slipped through
        # the sweep above: a sentence that ENUMERATES the kinds and then shuts
        # the list ("… and nothing else") without saying "two". The rule-1
        # error string in model_fallback.py read "every role is workhorse or
        # advisory, and nothing else" for the whole of the PR that ADDED the
        # third kind. A numeral is optional; naming every kind is not.
        #
        # Two judgement calls keep this from crying wolf, because a guard that
        # flags correct prose gets deleted rather than obeyed:
        #   * naming TWO kinds is what makes a line an enumeration of the set
        #     rather than a sentence about one of them — "the JUDGEMENT
        #     ladder: the planner, and nothing else" is about who walks a
        #     ladder, and is right;
        #   * prose WRAPS, so the surrounding lines count as the same
        #     sentence. model-drift.yml names `judgement` two lines below its
        #     "and nothing else", which leaves no reader misled.
        stale = []
        for path in KIND_DOCUMENTS:
            lines = path.read_text().splitlines()
            for n, line in enumerate(lines, 1):
                if not re.search(r"\bnothing else\b", line, re.I):
                    continue
                named = [k for k in mf.ROLE_KINDS if k in line.lower()]
                if len(named) < 2:
                    continue
                window = " ".join(lines[max(0, n - 3):n + 2]).lower()
                missing = [k for k in mf.ROLE_KINDS if k not in window]
                if missing:
                    stale.append(f"{path.name}:{n}: omits {missing}: {line.strip()}")
        self.assertEqual(
            stale, [], "a closed list of the role kinds is missing one of them"
        )

    def test_the_rejected_kinds_error_names_every_kind(self):
        # The string a human reads when `config/models.yaml` is rejected for a
        # typo'd kind name. Assert the PROSE half (after the em-dash), not the
        # whole message: the first half interpolates `ROLE_KINDS` and so names
        # every kind however stale the sentence that follows it is. The
        # sentence is the part a reader believes — "every role is workhorse or
        # advisory" tells them `judgement` was removed, not that they
        # misspelled it.
        cfg = _canonical()
        cfg["kinds"]["judgment"] = cfg["kinds"].pop(JUDGEMENT)  # the typo itself
        errors = mf.policy_errors(cfg)
        self.assertTrue(errors, "a misspelled kind must be rejected")
        rule = next((e for e in errors if e.startswith("kinds: must declare")), None)
        self.assertIsNotNone(rule, f"no rule-1 rejection: {errors}")
        _, _, prose = rule.partition("—")
        self.assertTrue(prose.strip(), f"the rejection carries no explanation: {rule}")
        for kind in mf.ROLE_KINDS:
            with self.subTest(kind=kind):
                self.assertIn(
                    kind, prose, f"the explanation never names {kind!r}: {prose!r}"
                )


if __name__ == "__main__":
    unittest.main()
