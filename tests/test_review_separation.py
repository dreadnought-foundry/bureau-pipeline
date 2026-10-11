"""DRE-3880 — Sonnet 5 is the workhorse ladder's backup rung, and the
build/review separation is kept by SELECTION rather than by disjoint lists.

WHAT THE CEO DECIDED (signed console answer, 2026-09-16 07:28 PT)
----------------------------------------------------------------
`claude-sonnet-5` replaces `claude-sonnet-4-6` as the workhorse ladder's
backup rung. `claude-sonnet-4-6` is NOT retired: it keeps its existing place
as the judgement (planner) ladder's last resort — the "never block a plan on
availability" rung. Only its WORKHORSE entry is replaced.

That puts one model on two ladders at once: Sonnet 5 now tops the advisory
ladder AND backs the workhorse one. The separation the old schema rule bought
by keeping the lists disjoint is bought at selection time instead: when a
build ran on `claude-sonnet-5`, the critic and the verifier for that pull
request skip it and use `claude-opus-5`.

The overlap is not new in principle — the advisory ladder's own fallback is
already `claude-opus-5`, the workhorse PRIMARY, so reviewer and worker have
coincided whenever the critic falls back. This card makes the guarantee
explicit per-run instead of incidental.

DRE-6785 (the CEO's decision of 2026-10-10) stopped refusing a BARE overlap.
The separation no longer needs a written rule: the reviewer's walk loses the
model that built the pull request and the next model leads, and a declared
rule only says which model leads instead. A reviewer never falls back onto the
build model, even when every other model on its walk is unavailable. The
classes below that used to pin the bare-overlap refusal now pin that a config
without the rule validates AND still separates.

WHAT THESE TESTS HOLD
--------------------
  1. The committed ladders are selectable as written: every rung is in
     neither `retired` nor `excluded`, and every overlap they carry is
     declared. Which model sits where is the console's choice (DRE-6863).
  2. The schema permits one model on both the advisory and the workhorse
     ladder, with or without a declared selection rule (DRE-6785), and still
     refuses a declared rule that is malformed or stale.
  3. A Sonnet-5 build is never reviewed by Sonnet 5, at any availability, for
     either reviewer role, through the function AND through the CLI the
     workflows actually call.
  4. DRE-3892 (newer versions adopt themselves) cannot silently drop the
     rule: an adoption that moves the overlapping rung without moving the
     selection rule with it fails `sync_model_config.py --check` twice over —
     the new rung is a bare overlap, and the old rule is stale.

DRE-5116 (2026-09-28) put `claude-sonnet-5-5` above `claude-sonnet-5` on BOTH
ladders and kept Sonnet 5, so two models now sit on both. Each gets a rule, and
validation was widened to match: every advisory rung ABOVE the ladder's last
that also sits on a build ladder needs one (it used to be the advisory top
only, which refused Sonnet 5's rule as stale). `TwoSonnetsOnBothLaddersTest`
pins that, including the case it exists for — a Sonnet-5 build still never
reviewed by Sonnet 5 when Sonnet 5.5 is unavailable too.

WHAT IS READ FROM WHERE (DRE-6863)
----------------------------------
The console rewrites config/models.yaml on every save — which model leads a
group, which group a role is in — so a RULE here is proved on the frozen copy
(`model_fixture.fixture_tree` / `fixture_module` / `fixture_config`), and every
model id and role it names is read off that copy's ladders, `agents:` map and
`review_separation` block. Each name below says what its rung IS on the frozen
ladders, and `TheFixtureHoldsTheOverlapTest` pins that it still is. What must
hold for any saved state — the committed file passes `policy_errors`, its
overlaps are declared, its reviewer roles are advisory, its mirror carries the
rules it declares — keeps reading the committed file and derives what it
expects from it.

The 2026-08-09 guard is untouched by all of it, and the tests that pin it live
in tests/test_model_policy.py: a probe reporting a model up still decides only
how far DOWN a ladder we walk, and nothing here puts a model on a ladder.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "models.yaml"
PRICES = ROOT / "config" / "model-prices.yaml"
WORKFLOWS = ROOT / ".github" / "workflows"

sys.path.insert(0, str(ROOT / "scripts"))

sys.path.insert(0, str(ROOT / "tests"))

import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402

WORKHORSE = "workhorse"
ADVISORY = "advisory"
JUDGEMENT = "judgement"


def _committed(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text())


def _fixture() -> dict:
    return model_fixture.fixture_config()


def _rung_ids(rungs) -> list:
    return [r["model"] if isinstance(r, dict) else r for r in (rungs or [])]


def _ladder(cfg, kind) -> list:
    return _rung_ids(cfg["ladders"][cfg["kinds"][kind]["ladder"]])


# The rungs every rule below is about, read off the FROZEN ladders by their
# place — never typed. `TheFixtureHoldsTheOverlapTest` pins what each one is.
_WORK = _ladder(_fixture(), WORKHORSE)
_ADVICE = _ladder(_fixture(), ADVISORY)
_PLAN = _ladder(_fixture(), JUDGEMENT)
# The advisory top, which is also a build rung: the upper overlap (DRE-5116).
UPPER = _ADVICE[0]
# The rung below it on both ladders: the lower overlap, the build model a
# "Sonnet-5 build" ran on when this file was written (DRE-3880).
LOWER = _ADVICE[1]
# The build ladder's top, and the advisory ladder's last rung: the designed
# loud fall, and where the frozen rules send a reviewer instead.
BUILD_TOP = _WORK[0]
# A build rung on no other ladder — the "Opus build" that is no overlap.
BUILD_ONLY = next(m for m in _WORK if m not in _ADVICE and m not in _PLAN)
# The planner's model, and its cheaper last resort, both on no other ladder.
PLAN_TOP = _PLAN[0]
PLAN_LAST = _PLAN[-1]
# The successor that does not exist yet — the DRE-3892 adoption this config
# has to survive. Deliberately unpriced: an unpriced candidate is `ask` to the
# adoption rule, and here it is simply a rung nobody declared a price for.
SONNET6 = "claude-sonnet-6"

# The roles the separation binds, read off the frozen file rather than imported
# from the code, so a code change that quietly drops one is a failure here.
REVIEWER_ROLES = tuple(_fixture()["review_separation"]["roles"])
# The roles the separation does NOT bind, read off the frozen `agents:` map: an
# advisory role that reviews nothing, a build role, and a role that is not
# advisory at all.
_KINDS = _fixture()["agents"]
UNBOUND_ADVISORY = next(r for r, k in _KINDS.items()
                        if k == ADVISORY and r not in REVIEWER_ROLES)
BUILDER = next(r for r, k in _KINDS.items() if k == WORKHORSE)
NOT_ADVISORY = next(r for r, k in _KINDS.items() if k != ADVISORY)

# One fixture tree for the whole module, and `model_fallback` imported from it:
# its config, ladders and mirror are the frozen ones.
_TREE_DIR: tempfile.TemporaryDirectory | None = None
TREE: Path | None = None
fmf = None


def setUpModule():
    global _TREE_DIR, TREE, fmf
    _TREE_DIR = tempfile.TemporaryDirectory()
    TREE = model_fixture.fixture_tree(Path(_TREE_DIR.name) / "tree")
    fmf = model_fixture.fixture_module(TREE)


def tearDownModule():
    _TREE_DIR.cleanup()


def _copy_tree(tmp: Path) -> Path:
    """A fresh fixture tree, for a test that rewrites its config."""
    return model_fixture.fixture_tree(tmp / "tree")


def _write_config(tree: Path, cfg: dict) -> None:
    (tree / "config" / "models.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))


def _run_sync(tree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tree / "scripts" / "sync_model_config.py"), *args],
        capture_output=True,
        text=True,
    )


def _cli_select(tree: Path, agent: str, *extra: str, available=None):
    """The selector CLI with a STUBBED probe — what the workflows run."""
    env = dict(os.environ)
    env["BUREAU_FAKE_AVAILABLE"] = json.dumps(available or {})
    why = tree / "why.txt"
    proc = subprocess.run(
        [
            sys.executable,
            str(tree / "scripts" / "model_fallback.py"),
            "select",
            agent,
            *extra,
            "--explain-file",
            str(why),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"select {agent} failed: {proc.stdout}\n{proc.stderr}"
    return proc.stdout.strip(), why.read_text().strip()


def _separation(cfg: dict) -> dict:
    return cfg.get("review_separation") or {}


def _rules(cfg: dict) -> dict:
    return {r["built_on"]: r["reviewers_use"] for r in _separation(cfg).get("rules", [])}


class TheFixtureHoldsTheOverlapTest(unittest.TestCase):
    """What each name above relies on, true of the frozen ladders — so a rule
    test below that passes is about the case it says it is."""

    def test_both_overlaps_sit_on_both_ladders_above_the_advisory_last_rung(self):
        self.assertEqual(len(_ADVICE), 3)
        for model in (UPPER, LOWER):
            with self.subTest(model=model):
                self.assertIn(model, _WORK)
                self.assertNotEqual(model, _ADVICE[-1])

    def test_the_build_top_is_the_advisory_last_rung_and_the_planners_fallback(self):
        self.assertEqual(BUILD_TOP, _ADVICE[-1])
        self.assertEqual(BUILD_TOP, _PLAN[1])

    def test_each_frozen_rule_sends_its_reviewers_to_the_build_top(self):
        self.assertEqual(_rules(_fixture()), {UPPER: BUILD_TOP, LOWER: BUILD_TOP})

    def test_the_build_only_and_planner_rungs_sit_on_one_ladder(self):
        self.assertIn(BUILD_ONLY, _WORK)
        for model in (PLAN_TOP, PLAN_LAST):
            with self.subTest(model=model):
                self.assertNotIn(model, _WORK)
                self.assertNotIn(model, _ADVICE)
        self.assertNotIn(SONNET6, fmf.KNOWN_MODELS)

    def test_the_roles_are_where_the_scenarios_need_them(self):
        self.assertTrue(REVIEWER_ROLES)
        self.assertEqual({_KINDS[r] for r in REVIEWER_ROLES}, {ADVISORY})
        self.assertEqual(_KINDS[UNBOUND_ADVISORY], ADVISORY)
        self.assertEqual(_KINDS[BUILDER], WORKHORSE)
        self.assertNotEqual(_KINDS[NOT_ADVISORY], ADVISORY)


class LaddersAsDataTest(unittest.TestCase):
    """What holds for the committed ladders whatever the console last saved."""

    def test_every_committed_rung_is_selectable_where_it_is_named(self):
        # No rung of a ladder is retired or excluded, every rung is a model
        # the selector knows, and the loaded ladders put each one exactly
        # where the file names it.
        cfg = _committed()
        out = set(_rung_ids(cfg.get("retired"))) | set(_rung_ids(cfg.get("excluded")))
        named = {name: _rung_ids(rungs) for name, rungs in cfg["ladders"].items()}
        for name, rungs in named.items():
            for model in rungs:
                with self.subTest(ladder=name, model=model):
                    self.assertNotIn(model, out)
                    self.assertIn(model, mf.KNOWN_MODELS)
                    self.assertEqual(
                        sorted(n for n, m in mf.LADDERS.items() if model in m),
                        sorted(n for n, m in named.items() if model in m),
                    )

    def test_todays_config_passes_policy_validation(self):
        self.assertEqual(mf.policy_errors(_committed()), [])

    def test_every_overlap_is_real_and_declared(self):
        # Every advisory rung above the ladder's last that is also a build
        # rung carries a rule, and no rule reviews on the build model.
        cfg = _committed()
        advice = _ladder(cfg, ADVISORY)
        for model in advice[:-1]:
            if model in _ladder(cfg, WORKHORSE):
                with self.subTest(model=model):
                    self.assertIn(model, _rules(cfg))
                    self.assertNotEqual(_rules(cfg)[model], model)

    def test_the_committed_reviewer_roles_are_advisory_as_written(self):
        cfg = _committed()
        self.assertTrue(_separation(cfg)["roles"])
        for role in _separation(cfg)["roles"]:
            with self.subTest(role=role):
                self.assertEqual(cfg["agents"][role], ADVISORY)


class SchemaPermitsTheOverlapOnlyWithTheRuleTest(unittest.TestCase):
    """The schema permits the overlap with or without a rule since 2026-10-10
    (DRE-6785), and still refuses a declared rule that is malformed."""

    def assertRefused(self, cfg, needle):
        errors = fmf.policy_errors(cfg)
        self.assertTrue(errors, "this config must be refused")
        self.assertTrue(
            any(needle in e for e in errors),
            f"errors do not name {needle!r}: {errors}",
        )

    def test_a_bare_overlap_with_no_selection_rule_is_admitted_and_separated(self):
        # The same ladders, the declaration deleted: refused until 2026-10-10,
        # admitted since, because the reviewer still skips the build model.
        cfg = _fixture()
        cfg.pop("review_separation", None)
        self.assertEqual(fmf.policy_errors(cfg), [])
        for model in (LOWER, UPPER):
            with self.subTest(built_on=model):
                walk = fmf.reviewers_for(cfg, model)
                self.assertNotIn(model, walk)
                self.assertTrue(walk)

    def test_an_empty_rule_list_is_admitted_too(self):
        cfg = _fixture()
        cfg["review_separation"]["rules"] = []
        self.assertEqual(fmf.policy_errors(cfg), [])
        self.assertNotIn(LOWER, fmf.reviewers_for(cfg, LOWER))

    def test_a_rule_that_reviews_on_the_build_model_is_refused(self):
        # A "rule" that sends the reviewer back to the model the build ran on
        # is the overlap wearing a declaration — for either overlap.
        for model in (UPPER, LOWER):
            with self.subTest(built_on=model):
                cfg = _fixture()
                for rule in cfg["review_separation"]["rules"]:
                    if rule["built_on"] == model:
                        rule["reviewers_use"] = model
                self.assertRefused(cfg, model)

    def test_a_reviewers_model_off_the_advisory_ladder_is_refused(self):
        cfg = _fixture()
        cfg["review_separation"]["rules"][0]["reviewers_use"] = PLAN_LAST
        self.assertRefused(cfg, PLAN_LAST)

    def test_a_rule_naming_a_model_off_the_ladders_is_refused(self):
        # THE DRE-3892 CARRY-FORWARD, from the other side: a rule that no
        # longer describes a real overlap is stale, and a stale rule is how
        # the guarantee gets lost without anybody editing it.
        cfg = _fixture()
        cfg["review_separation"]["rules"].append(
            {"built_on": PLAN_LAST, "reviewers_use": BUILD_ONLY, "reason": "stale"}
        )
        self.assertRefused(cfg, PLAN_LAST)

    def test_the_rule_must_bind_both_the_critic_and_the_verifier(self):
        for dropped in REVIEWER_ROLES:
            with self.subTest(role=dropped):
                cfg = _fixture()
                cfg["review_separation"]["roles"] = [
                    r for r in cfg["review_separation"]["roles"] if r != dropped
                ]
                self.assertRefused(cfg, dropped)

    def test_a_reviewer_role_that_is_not_advisory_is_refused(self):
        cfg = _fixture()
        cfg["review_separation"]["roles"] = list(REVIEWER_ROLES) + [NOT_ADVISORY]
        self.assertRefused(cfg, NOT_ADVISORY)

    def test_a_rule_for_a_model_off_the_advisory_ladder_is_stale(self):
        # The planner's model on the build ladder is a person's choice since
        # 2026-10-10; a rule for it describes no overlap (it is not a reviewer
        # rung), so the RULE is refused as stale, and naming the model.
        cfg = _fixture()
        cfg["ladders"][cfg["kinds"][WORKHORSE]["ladder"]].append(
            {"model": PLAN_TOP, "reason": "the CEO's choice"}
        )
        cfg["review_separation"]["rules"].append(
            {"built_on": PLAN_TOP, "reviewers_use": BUILD_ONLY, "reason": "stale"}
        )
        self.assertRefused(cfg, PLAN_TOP)
        cfg["review_separation"]["rules"].pop()
        self.assertEqual(fmf.policy_errors(cfg), [])

    def test_ci_accepts_a_bare_overlap(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            cfg = _fixture()
            cfg.pop("review_separation", None)
            _write_config(tree, cfg)
            proc = _run_sync(tree, "--check")
            self.assertNotIn("rejected by model policy", proc.stdout + proc.stderr)

    def test_the_selector_separates_a_bare_overlap_with_no_rule(self):
        # The config loads (it validates), and the reviewer of a build on the
        # lower overlap still never runs on it: the separation is derived.
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            cfg = _fixture()
            cfg.pop("review_separation", None)
            _write_config(tree, cfg)
            model, note = _cli_select(
                tree, REVIEWER_ROLES[0], "--built-on", LOWER,
                available={BUILD_ONLY: True, LOWER: True},
            )
            self.assertNotEqual(model, LOWER)
            self.assertIn(f"this build ran on {LOWER}", note)


class BelowTheFallbackTest(unittest.TestCase):
    """What is left of rule 4b since 2026-10-10 (DRE-6785): every rung has a
    DECLARED PRICE. It used to refuse a dearer model below a non-build
    ladder's fallback as well; a person may now put any priced model there.
    """

    def test_the_planners_last_resort_is_allowed(self):
        self.assertEqual(fmf.policy_errors(_fixture()), [])

    def test_a_dearer_model_below_the_workhorse_fallback_is_a_persons_choice(self):
        cfg = _fixture()
        cfg["ladders"][cfg["kinds"][JUDGEMENT]["ladder"]] = [
            {"model": BUILD_ONLY, "reason": "the build model first"},
            {"model": PLAN_TOP, "reason": "…and the premium one below it"},
        ]
        self.assertEqual(fmf.policy_errors(cfg), [])

    def test_an_unpriced_rung_is_refused_in_plain_words(self):
        # A price is never guessed — fail closed, on any rung of any ladder.
        cfg = _fixture()
        cfg["ladders"][cfg["kinds"][JUDGEMENT]["ladder"]] = [
            {"model": PLAN_TOP, "reason": "the planner's model"},
            {"model": BUILD_ONLY, "reason": "the loud fallback"},
            {"model": SONNET6, "reason": "priced nowhere"},
        ]
        self.assertEqual(fmf.policy_errors(cfg), [
            f"ladders.{cfg['kinds'][JUDGEMENT]['ladder']}: {SONNET6} has no "
            "declared price, so it cannot be chosen."
        ])

    def test_every_ladder_rung_still_has_a_declared_price(self):
        declared = set(yaml.safe_load(PRICES.read_text())["prices"])
        for name, models in mf.LADDERS.items():
            for model in models:
                with self.subTest(ladder=name, model=model):
                    self.assertIn(model, declared)


class ASonnetFiveBuildIsNeverReviewedBySonnetFiveTest(unittest.TestCase):
    """The acceptance criterion, from every direction the fleet can reach it —
    on the frozen ladders, where the lower overlap is the "Sonnet 5"."""

    def setUp(self):
        fmf.clear_availability_cache()

    def tearDown(self):
        fmf.clear_availability_cache()

    def test_the_reviewers_skip_the_model_the_build_ran_on(self):
        instead = _rules(_fixture())[LOWER]
        for role in REVIEWER_ROLES:
            for avail in (
                {},                                   # everything up
                {LOWER: False},
                {instead: False},
                {instead: False, LOWER: False},
            ):
                with self.subTest(role=role, avail=avail):
                    fmf.clear_availability_cache()
                    got = fmf.select(
                        role,
                        probe=lambda m: avail.get(m, True),
                        built_on=LOWER,
                    )
                    self.assertNotEqual(
                        got, LOWER,
                        f"{role} reviewed a build on the model it ran on",
                    )
                    # The rule's model leads; with it down the reviewer falls
                    # to the advisory top (DRE-5116), a different model, never
                    # to the build model.
                    self.assertEqual(
                        got, instead if avail.get(instead, True) else UPPER
                    )

    def test_a_build_top_build_is_never_reviewed_on_it_when_every_overlap_is_down(self):
        # DRE-6785: the reviewer never falls back onto the build model. With
        # every other model on the walk down it is exhausted onto its last
        # model, loudly, and the note says the build model was skipped.
        down = {UPPER: False, LOWER: False}
        walk = [m for m in _ADVICE if m != BUILD_TOP]
        for role in REVIEWER_ROLES:
            with self.subTest(role=role):
                fmf.clear_availability_cache()
                decision = fmf.select_with_reasons(
                    role, probe=lambda m: down.get(m, True), built_on=BUILD_TOP)
                self.assertNotEqual(decision["model"], BUILD_TOP)
                self.assertEqual(decision["ladder"], walk)
                self.assertEqual(decision["separated"], [BUILD_TOP])
                self.assertTrue(decision["exhausted"])
                note = fmf.selection_note(decision)
                self.assertTrue(note.startswith("DEGRADED"), note)
                self.assertIn(
                    f"build/review separation: this build ran on {BUILD_TOP}", note)
                fmf.clear_availability_cache()
                self.assertNotEqual(
                    fmf.select(role, probe=lambda m: down.get(m, True),
                               built_on=BUILD_TOP), BUILD_TOP)

    def test_an_unknown_build_model_still_never_reviews_on_an_overlap_model(self):
        # Fail CLOSED. A card we could not read is not evidence the build ran
        # on something else, and "never" has to survive a Linear blip.
        for role in REVIEWER_ROLES:
            with self.subTest(role=role):
                fmf.clear_availability_cache()
                self.assertEqual(
                    fmf.select(role, probe=lambda m: True, built_on_unknown=True),
                    _rules(_fixture())[LOWER],
                )

    def test_a_build_on_a_rung_that_is_no_overlap_still_gets_the_advisory_top(self):
        # A build on a rung only the build ladder names is the ordinary case,
        # and NOT an overlap the rule declares — the reviewer keeps its own
        # ladder's top rung.
        fmf.clear_availability_cache()
        self.assertEqual(
            fmf.select(REVIEWER_ROLES[0], probe=lambda m: True, built_on=BUILD_ONLY),
            UPPER,
        )

    def test_no_build_model_at_all_leaves_selection_exactly_as_it_was(self):
        fmf.clear_availability_cache()
        self.assertEqual(fmf.select(REVIEWER_ROLES[0], probe=lambda m: True), UPPER)

    def test_the_separation_does_not_touch_a_role_it_does_not_bind(self):
        # An advisory role that reviews nothing (the medic, when this was
        # written): binding it would be a spend change nobody asked for. A
        # build role is not bound either.
        fmf.clear_availability_cache()
        self.assertEqual(
            fmf.select(UNBOUND_ADVISORY, probe=lambda m: True, built_on=UPPER), UPPER
        )
        fmf.clear_availability_cache()
        self.assertEqual(
            fmf.select(BUILDER, probe=lambda m: True, built_on=LOWER), BUILD_TOP
        )

    def test_the_separation_is_recorded_but_is_not_a_degradation(self):
        # It is the DESIGNED path, not a rung we lost: a ::warning:: on every
        # such build would teach the fleet to ignore the warning that means a
        # reviewer did not get its model.
        decision = fmf.select_with_reasons(
            REVIEWER_ROLES[0], probe=lambda m: True, built_on=LOWER
        )
        self.assertEqual(decision["model"], _rules(_fixture())[LOWER])
        self.assertEqual(decision["separated"], [LOWER])
        self.assertFalse(decision["degraded"], "separation is not a degradation")
        note = fmf.selection_note(decision)
        self.assertEqual(len(note.splitlines()), 1)
        self.assertFalse(note.startswith("DEGRADED"), note)
        self.assertIn(LOWER, note)
        self.assertIn("separation", note.lower())

    def test_an_unavailable_reviewer_model_is_still_loud(self):
        # The separation skip must not swallow the availability one riding
        # with it: a reviewer that lost a rung to a 404 still says DEGRADED.
        instead = _rules(_fixture())[LOWER]
        fmf.clear_availability_cache()
        decision = fmf.select_with_reasons(
            REVIEWER_ROLES[-1], probe=lambda m: m != instead, built_on=LOWER
        )
        self.assertTrue(decision["degraded"])
        self.assertTrue(fmf.selection_note(decision).startswith("DEGRADED"))

    def test_the_cli_the_workflows_call_holds_the_same_line(self):
        instead = _rules(_fixture())[LOWER]
        for role in REVIEWER_ROLES:
            with self.subTest(role=role):
                model, note = _cli_select(
                    TREE, role, "--built-on", LOWER,
                    available={instead: True, LOWER: True},
                )
                self.assertEqual(model, instead)
                self.assertIn(LOWER, note)
                model, _ = _cli_select(
                    TREE, role, "--built-on-unknown",
                    available={instead: True, LOWER: True},
                )
                self.assertEqual(model, instead)
        # stdout is still ONLY the model id — every workflow captures it.
        model, _ = _cli_select(
            TREE, REVIEWER_ROLES[0], "--built-on", BUILD_ONLY,
            available={BUILD_TOP: True, UPPER: True, LOWER: True},
        )
        self.assertEqual(model, UPPER)


class TheBuildModelIsReadFromTheCardTest(unittest.TestCase):
    """`🧠 model-attempt:` is where the build model is recorded, so that is
    where the reviewer reads it from."""

    def test_the_most_recent_attempt_marker_wins(self):
        bodies = [
            f"🧠 {fmf.attempt_marker(BUILD_ONLY)} — engineer agent starting.",
            f"🧠 {fmf.error_marker(BUILD_ONLY)}",
            f"🧠 {fmf.attempt_marker(LOWER)} — engineer agent starting.",
        ]
        self.assertEqual(fmf.last_attempt_model(bodies), LOWER)

    def test_an_unknown_id_is_not_read_as_a_model(self):
        self.assertIsNone(fmf.last_attempt_model(["model-attempt: not-a-model"]))
        self.assertIsNone(fmf.last_attempt_model([]))
        self.assertIsNone(fmf.last_attempt_model([None, ""]))

    def test_the_cli_reads_a_dumped_comment_thread(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "comments.json"
            path.write_text(
                json.dumps([f"🧠 {fmf.attempt_marker(LOWER)} — devops agent"])
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(TREE / "scripts" / "model_fallback.py"),
                    "build-model",
                    str(path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), LOWER)

    def test_an_unreadable_thread_prints_nothing_and_exits_clean(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "model_fallback.py"),
                "build-model",
                "/nope/not/a/file.json",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")


class ReviewWorkflowsEnforceItTest(unittest.TestCase):
    """The rule is only real where the reviewers actually run."""

    REVIEW_WORKFLOWS = {"qa-review.yml": "critic", "verify.yml": "verifier"}

    def test_each_review_workflow_passes_the_build_model_to_selection(self):
        for wf, role in self.REVIEW_WORKFLOWS.items():
            with self.subTest(workflow=wf):
                body = (WORKFLOWS / wf).read_text()
                self.assertIn(
                    "model_fallback.py build-model", body,
                    f"{wf}: nothing reads the model the build ran on",
                )
                self.assertIn(
                    "--built-on", body,
                    f"{wf}: the build model never reaches selection",
                )
                self.assertIn(
                    "--built-on-unknown", body,
                    f"{wf}: a card it could not read must fail CLOSED",
                )

    def test_the_card_snapshot_the_select_step_reads_can_reach_linear(self):
        # Since Stage 2 fix #14 the select step reads the build's heartbeat
        # out of the job's ONE card snapshot (scripts/card_snapshot.py); the
        # snapshot step is the one that must be able to read the card.
        for wf in self.REVIEW_WORKFLOWS:
            with self.subTest(workflow=wf):
                doc = yaml.safe_load((WORKFLOWS / wf).read_text())
                selects = [
                    s
                    for job in doc["jobs"].values()
                    for s in (job.get("steps") or [])
                    if s.get("id") == "model"
                ]
                self.assertTrue(selects, f"{wf}: no Select model step")
                for step in selects:
                    self.assertIn("card_snapshot.py thread", step["run"],
                                  f"{wf}: the select step does not read the snapshot")
                steps = [
                    s
                    for job in doc["jobs"].values()
                    for s in (job.get("steps") or [])
                    if s.get("id") == "cardsnap"
                ]
                self.assertTrue(steps, f"{wf}: no card snapshot step")
                for step in steps:
                    # The planner's bucket where the repo opted in, the fleet
                    # key otherwise (Stage 2 fix #12, review item 29) — a key
                    # either way.
                    self.assertEqual(
                        (step.get("env") or {}).get("LINEAR_API_KEY"),
                        "${{ vars.LINEAR_AGENT_BUCKET == 'planner' && "
                        "secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY }}",
                        f"{wf}: the card snapshot step cannot read the card",
                    )
                    self.assertEqual(
                        (step.get("env") or {}).get("LINEAR_API_KEY_FALLBACK"),
                        "${{ secrets.LINEAR_API_KEY }}",
                        f"{wf}: the card snapshot step has no fleet key to fall back to",
                    )

    def test_no_review_workflow_hardcodes_the_reviewers_model(self):
        # The substitute is DATA (config/models.yaml), never a literal in a
        # workflow: a pinned id is drift by construction.
        rungs = sorted({m for models in mf.LADDERS.values() for m in models})
        for wf in self.REVIEW_WORKFLOWS:
            body = (WORKFLOWS / wf).read_text()
            for model in rungs:
                with self.subTest(workflow=wf, model=model):
                    self.assertNotIn(f"--built-on {model}", body)


class TheDegradePathKeepsTheRuleTest(unittest.TestCase):
    """The generated mirror is what runs when the YAML is unreadable. A
    degrade that dropped the separation would review a build on the model it
    ran on and look exactly like a healthy run."""

    def test_the_mirror_carries_the_separation(self):
        mirror = mf._FALLBACK_MODEL_CONFIG["review_separation"]
        cfg = _committed()
        self.assertEqual(sorted(mirror["roles"]), sorted(_separation(cfg)["roles"]))
        self.assertEqual(mirror["rules"], _rules(cfg))

    def test_a_truncated_checkout_still_separates(self):
        instead = _rules(_fixture())[LOWER]
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            (tree / "config" / "models.yaml").write_text("{{ not yaml\n")
            model, _ = _cli_select(
                tree, REVIEWER_ROLES[0], "--built-on", LOWER,
                available={instead: True, LOWER: True},
            )
            self.assertEqual(model, instead)


class CarriedForwardToDre3892Test(unittest.TestCase):
    """A future same-family adoption cannot silently drop the rule.

    DRE-3892's adoption path edits `config/models.yaml` and regenerates the
    mirrors — which runs exactly the schema check below. Moving the
    overlapping rung without moving its selection rule fails twice: the new
    rung is a bare overlap, and the rule left behind names a model that is no
    longer on both ladders.
    """

    def _adopt(self, cfg, new_id):
        """Swap the lower overlap for a newer one on BOTH ladders, the way an
        adoption would — and touch nothing else."""
        for name, rungs in cfg["ladders"].items():
            for rung in rungs:
                if rung["model"] == LOWER:
                    rung["model"] = new_id
        return cfg

    def test_adopting_the_rung_without_the_rule_is_refused(self):
        # Still refused, on the stale rule left behind and the unpriced rung
        # (an unpriced rung is refused on every ladder since DRE-6785).
        cfg = self._adopt(_fixture(), SONNET6)
        errors = fmf.policy_errors(cfg)
        self.assertTrue(errors, "an adoption that drops the rule must be refused")
        self.assertTrue(
            any(LOWER in e for e in errors),
            f"the stale rule is not named: {errors}",
        )

    def test_adopting_the_rung_WITH_the_rule_is_accepted(self):
        # The adoption declares the candidate's price, so price it here too.
        cfg = self._adopt(_fixture(), SONNET6)
        for rule in cfg["review_separation"]["rules"]:
            if rule["built_on"] == LOWER:
                rule["built_on"] = SONNET6
                rule["reason"] = "carried forward"
        prices = {**fmf.declared_prices(), SONNET6: {"input": 2.0, "output": 10.0}}
        self.assertEqual(fmf.policy_errors(cfg, prices), [])

    def test_ci_goes_red_on_an_adoption_that_drops_the_rule(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            _write_config(tree, self._adopt(_fixture(), SONNET6))
            proc = _run_sync(tree, "--check")
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(SONNET6, proc.stdout + proc.stderr)

    def test_an_adoption_of_this_family_moves_the_rung_on_BOTH_ladders(self):
        # Why the rule has to travel with it: the lower overlap is one id on
        # two ladders, so a same-family successor replaces two rungs at once —
        # a build rung AND an advisory one. That is precisely the edit the
        # schema check above refuses unless the selection rule moves with it.
        model_adoption = model_fixture.fixture_module(TREE, "model_adoption")

        catalog = [
            {"id": BUILD_TOP, "created_at": "2026-09-22T00:00:00Z"},
            {"id": BUILD_ONLY, "created_at": "2026-05-01T00:00:00Z"},
            {"id": PLAN_TOP, "created_at": "2026-08-01T00:00:00Z"},
            {"id": PLAN_LAST, "created_at": "2026-02-17T00:00:00Z"},
            {"id": LOWER, "created_at": "2026-06-29T00:00:00Z"},
            {"id": SONNET6, "created_at": "2026-11-01T00:00:00Z"},
        ]
        prices = dict(model_adoption.load_prices())
        prices[SONNET6] = {"input": 2.0, "output": 10.0}
        records = model_adoption.classify_catalog(catalog, _fixture(), prices)
        adopted = [r for r in records if r["candidate"] == SONNET6]
        self.assertEqual(len(adopted), 1, records)
        cfg = _fixture()
        self.assertEqual(adopted[0]["rule"], model_adoption.RULE_ADOPT)
        self.assertEqual(
            sorted(
                r["ladder"] for r in adopted[0]["replaces"] if r["model"] == LOWER
            ),
            sorted(
                {cfg["kinds"][ADVISORY]["ladder"], cfg["kinds"][WORKHORSE]["ladder"]}
            ),
        )


class TwoSonnetsOnBothLaddersTest(unittest.TestCase):
    """DRE-5116: Sonnet 5.5 above Sonnet 5 on BOTH ladders, Sonnet 5 kept — on
    the frozen ladders, the upper and the lower overlap.

    Validation used to admit a rule only for the advisory ladder's TOP rung.
    With two models on both ladders that refused the lower one's rule as
    stale, and without that rule a build on the lower overlap — which happens
    exactly when the upper one was skipped — is one unavailable rung away from
    being reviewed on the model it ran on. So every advisory rung above the
    ladder's LAST that also sits on a build ladder needs a rule; the last rung
    is the designed loud fall onto the build model and needs none.
    """

    def setUp(self):
        fmf.clear_availability_cache()

    def tearDown(self):
        fmf.clear_availability_cache()

    def test_the_lower_overlap_rule_is_admitted_not_stale(self):
        errors = fmf.policy_errors(_fixture())
        self.assertEqual(errors, [])

    def test_dropping_the_lower_overlap_rule_is_admitted_and_still_separates(self):
        cfg = _fixture()
        cfg["review_separation"]["rules"] = [
            r for r in cfg["review_separation"]["rules"] if r["built_on"] != LOWER
        ]
        self.assertEqual(fmf.policy_errors(cfg), [])
        self.assertNotIn(LOWER, fmf.reviewers_for(cfg, LOWER))

    def test_dropping_the_top_rule_is_admitted_and_still_separates(self):
        cfg = _fixture()
        cfg["review_separation"]["rules"] = [
            r for r in cfg["review_separation"]["rules"] if r["built_on"] != UPPER
        ]
        self.assertEqual(fmf.policy_errors(cfg), [])
        self.assertNotIn(UPPER, fmf.reviewers_for(cfg, UPPER))

    def test_the_last_rung_needs_no_rule_but_may_carry_one(self):
        # The advisory ladder's last rung is also the build ladder's top: the
        # designed loud fall. No rule is required for it, and one is admitted
        # rather than refused as stale.
        cfg = _fixture()
        last = _ADVICE[-1]
        self.assertIn(last, _WORK)
        self.assertNotIn(last, _rules(cfg))
        self.assertEqual(fmf.policy_errors(cfg), [])
        cfg["review_separation"]["rules"].append(
            {"built_on": last, "reviewers_use": UPPER, "reason": "allowed"}
        )
        self.assertEqual(fmf.policy_errors(cfg), [])

    def test_a_rule_for_a_model_on_one_ladder_is_still_stale(self):
        # A build-only rung is no overlap, so a rule for it is stale.
        cfg = _fixture()
        cfg["review_separation"]["rules"].append(
            {"built_on": BUILD_ONLY, "reviewers_use": UPPER, "reason": "stale"}
        )
        errors = fmf.policy_errors(cfg)
        self.assertTrue(any(BUILD_ONLY in e and "STALE" in e for e in errors), errors)

    def test_a_lower_overlap_build_is_never_reviewed_on_it_at_any_availability(self):
        # THE case the widening exists for: the upper overlap unavailable too.
        instead = _rules(_fixture())[LOWER]
        for role in REVIEWER_ROLES:
            for avail in (
                {},
                {UPPER: False},
                {instead: False},
                {instead: False, UPPER: False},
            ):
                with self.subTest(role=role, avail=avail):
                    fmf.clear_availability_cache()
                    got = fmf.select(
                        role, probe=lambda m: avail.get(m, True), built_on=LOWER
                    )
                    self.assertNotEqual(got, LOWER)

    def test_an_upper_overlap_build_is_reviewed_on_its_rule_then_the_lower(self):
        instead = _rules(_fixture())[UPPER]
        for role in REVIEWER_ROLES:
            with self.subTest(role=role):
                fmf.clear_availability_cache()
                decision = fmf.select_with_reasons(
                    role, probe=lambda m: True, built_on=UPPER
                )
                self.assertEqual(decision["model"], instead)
                self.assertEqual(decision["ladder"], [instead, LOWER])
                self.assertEqual(decision["separated"], [UPPER])
                self.assertFalse(decision["degraded"])
                fmf.clear_availability_cache()
                self.assertEqual(
                    fmf.select(role, probe=lambda m: m != instead, built_on=UPPER),
                    LOWER,
                )

    def test_a_cardless_pr_still_lands_on_the_rules_model(self):
        # --built-on-unknown fails CLOSED: both overlaps come off, so the
        # reviewer lands where it landed before DRE-5116.
        for role in REVIEWER_ROLES:
            with self.subTest(role=role):
                fmf.clear_availability_cache()
                decision = fmf.select_with_reasons(
                    role, probe=lambda m: True, built_on_unknown=True
                )
                self.assertEqual(decision["model"], _rules(_fixture())[LOWER])
                self.assertEqual(sorted(decision["separated"]), sorted([UPPER, LOWER]))

    def test_the_mirror_carries_both_rules(self):
        # The generated mirror equals the file it was generated from.
        self.assertEqual(
            fmf._FALLBACK_MODEL_CONFIG["review_separation"]["rules"],
            _rules(_fixture()),
        )
        self.assertEqual(len(_rules(_fixture())), 2)


if __name__ == "__main__":
    unittest.main()
