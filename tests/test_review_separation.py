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

WHAT THESE TESTS PIN
--------------------
  1. The ladders, as data: workhorse `claude-opus-5` → `claude-sonnet-5`;
     `claude-sonnet-4-6` still the judgement ladder's last rung, still in
     config/models.yaml, in neither `retired` nor `excluded`.
  2. The schema rule that forbade one model on both the advisory and the
     workhorse ladder now permits it ONLY together with a declared selection
     rule. A BARE overlap — the same edit with no rule — is still refused, and
     the judgement ladder gets no such exemption at all.
  3. A Sonnet-5 build is never reviewed by Sonnet 5, at any availability, for
     either reviewer role, through the function AND through the CLI the
     workflows actually call.
  4. DRE-3892 (newer versions adopt themselves) cannot silently drop the
     rule: an adoption that moves the overlapping rung without moving the
     selection rule with it fails `sync_model_config.py --check` twice over —
     the new rung is a bare overlap, and the old rule is stale.

The 2026-08-09 guard is untouched by all of it, and the tests that pin it live
in tests/test_model_policy.py: a probe reporting a model up still decides only
how far DOWN a ladder we walk, and nothing here puts a model on a ladder.
"""

from __future__ import annotations

import json
import os
import shutil
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

import model_fallback as mf  # noqa: E402

OPUS = "claude-opus-5"
SONNET46 = "claude-sonnet-4-6"
SONNET5 = "claude-sonnet-5"
FABLE51 = "claude-fable-5-1"
# The successor that does not exist yet — the DRE-3892 adoption this config
# has to survive. Deliberately unpriced: an unpriced candidate is `ask` to the
# adoption rule, and here it is simply a rung nobody declared a price for.
SONNET6 = "claude-sonnet-6"

WORKHORSE = "workhorse"
ADVISORY = "advisory"
JUDGEMENT = "judgement"

# The two roles the separation binds. Spelled out rather than imported so a
# code change that quietly drops one is a failure here.
REVIEWER_ROLES = ("critic", "verifier")

_TREE_FILES = (
    "scripts/model_fallback.py",
    "scripts/sync_model_config.py",
    "config/models.yaml",
    "config/model-prices.yaml",
    "agents.yaml",
)


def _canonical(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text())


def _rung_ids(rungs) -> list:
    return [r["model"] if isinstance(r, dict) else r for r in (rungs or [])]


def _ladder(cfg, kind) -> list:
    return _rung_ids(cfg["ladders"][cfg["kinds"][kind]["ladder"]])


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


class LaddersAsDataTest(unittest.TestCase):
    """The two ladder facts the CEO's answer states, pinned as data."""

    def test_the_workhorse_ladder_is_opus_then_sonnet_5(self):
        self.assertEqual(_ladder(_canonical(), WORKHORSE), [OPUS, SONNET5])

    def test_sonnet_4_6_keeps_the_judgement_ladders_last_rung(self):
        # "Do NOT retire it" — it keeps its existing place as the planner's
        # last resort, the rung that exists so a plan never blocks on
        # availability. An earlier answer on this card (2026-09-14) said to
        # retire it; the 2026-09-16 answer supersedes that.
        judgement = _ladder(_canonical(), JUDGEMENT)
        self.assertEqual(judgement, [FABLE51, OPUS, SONNET46])
        self.assertEqual(judgement[-1], SONNET46)

    def test_sonnet_4_6_is_neither_retired_nor_excluded(self):
        cfg = _canonical()
        self.assertNotIn(SONNET46, _rung_ids(cfg.get("retired")))
        self.assertNotIn(SONNET46, _rung_ids(cfg.get("excluded")))
        self.assertIn(SONNET46, mf.KNOWN_MODELS)
        # Still selectable, on exactly the one ladder it is named on.
        on = [n for n, m in mf.LADDERS.items() if SONNET46 in m]
        self.assertEqual(on, [_canonical()["kinds"][JUDGEMENT]["ladder"]])

    def test_todays_config_passes_policy_validation(self):
        self.assertEqual(mf.policy_errors(_canonical()), [])

    def test_the_overlap_is_real_and_declared(self):
        cfg = _canonical()
        self.assertIn(SONNET5, _ladder(cfg, WORKHORSE))
        self.assertEqual(_ladder(cfg, ADVISORY)[0], SONNET5)
        self.assertEqual(_rules(cfg).get(SONNET5), OPUS)


class SchemaPermitsTheOverlapOnlyWithTheRuleTest(unittest.TestCase):
    """The schema rule that forbade the overlap now permits it WITH the
    selection rule — and still refuses a bare one."""

    def assertRefused(self, cfg, needle):
        errors = mf.policy_errors(cfg)
        self.assertTrue(errors, "this config must be refused")
        self.assertTrue(
            any(needle in e for e in errors),
            f"errors do not name {needle!r}: {errors}",
        )

    def test_a_bare_overlap_with_no_selection_rule_is_refused(self):
        # The same ladders, the declaration deleted. This is the edit the old
        # rule refused outright and it must still be refused.
        cfg = _canonical()
        cfg.pop("review_separation", None)
        self.assertRefused(cfg, SONNET5)

    def test_an_empty_rule_list_is_still_a_bare_overlap(self):
        cfg = _canonical()
        cfg["review_separation"]["rules"] = []
        self.assertRefused(cfg, SONNET5)

    def test_a_rule_that_reviews_on_the_build_model_is_refused(self):
        # A "rule" that sends the reviewer back to the model the build ran on
        # is the overlap wearing a declaration.
        cfg = _canonical()
        cfg["review_separation"]["rules"][0]["reviewers_use"] = SONNET5
        self.assertRefused(cfg, SONNET5)

    def test_a_reviewers_model_off_the_advisory_ladder_is_refused(self):
        cfg = _canonical()
        cfg["review_separation"]["rules"][0]["reviewers_use"] = SONNET46
        self.assertRefused(cfg, SONNET46)

    def test_a_rule_naming_a_model_off_the_ladders_is_refused(self):
        # THE DRE-3892 CARRY-FORWARD, from the other side: a rule that no
        # longer describes a real overlap is stale, and a stale rule is how
        # the guarantee gets lost without anybody editing it.
        cfg = _canonical()
        cfg["review_separation"]["rules"].append(
            {"built_on": SONNET46, "reviewers_use": OPUS, "reason": "stale"}
        )
        self.assertRefused(cfg, SONNET46)

    def test_the_rule_must_bind_both_the_critic_and_the_verifier(self):
        for dropped in REVIEWER_ROLES:
            with self.subTest(role=dropped):
                cfg = _canonical()
                cfg["review_separation"]["roles"] = [
                    r for r in cfg["review_separation"]["roles"] if r != dropped
                ]
                self.assertRefused(cfg, dropped)

    def test_a_reviewer_role_that_is_not_advisory_is_refused(self):
        cfg = _canonical()
        cfg["review_separation"]["roles"] = list(REVIEWER_ROLES) + ["engineer"]
        self.assertRefused(cfg, "engineer")

    def test_the_judgement_ladders_top_rung_gets_no_such_exemption(self):
        # The separation is about REVIEWERS. Declaring one buys nothing for
        # the planner's model: Fable on a build ladder is still the 2026-08-09
        # incident, whatever `review_separation` says.
        cfg = _canonical()
        cfg["ladders"][cfg["kinds"][WORKHORSE]["ladder"]].append(
            {"model": FABLE51, "reason": "someone's well-meaning edit"}
        )
        cfg["review_separation"]["rules"].append(
            {"built_on": FABLE51, "reviewers_use": OPUS, "reason": "not a licence"}
        )
        self.assertRefused(cfg, FABLE51)

    def test_ci_goes_red_on_a_bare_overlap(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            cfg = _canonical()
            cfg.pop("review_separation", None)
            _write_config(tree, cfg)
            proc = _run_sync(tree, "--check")
            self.assertNotEqual(proc.returncode, 0, "CI must fail on a bare overlap")
            self.assertIn(SONNET5, proc.stdout + proc.stderr)

    def test_the_selector_refuses_a_bare_overlap_and_degrades(self):
        # A config that violates policy is never loaded: the fleet keeps the
        # last-known-good ladders rather than review a Sonnet-5 build on
        # Sonnet 5 because somebody deleted the rule.
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            cfg = _canonical()
            cfg.pop("review_separation", None)
            _write_config(tree, cfg)
            model, _ = _cli_select(
                tree, "critic", "--built-on", SONNET5,
                available={OPUS: True, SONNET5: True},
            )
            self.assertNotEqual(model, SONNET5)


class BelowTheFallbackTest(unittest.TestCase):
    """Rule 4's second half, restated on the DECLARED PRICE it always meant.

    The rule reads the TOP rung of a non-build ladder, so a premium model
    parked BELOW the workhorse rungs would be invisible to it. It used to be
    spelled "every rung below the fallback must be a workhorse model", which
    was a proxy for "no DEARER model hides down there" — a proxy that stopped
    working the moment `claude-sonnet-4-6` left the build ladder and stayed
    the planner's last resort.
    """

    def test_the_planners_last_resort_is_allowed_because_it_is_cheaper(self):
        self.assertEqual(mf.policy_errors(_canonical()), [])

    def test_a_dearer_model_may_not_hide_below_the_workhorse_fallback(self):
        cfg = _canonical()
        cfg["ladders"][cfg["kinds"][JUDGEMENT]["ladder"]] = [
            {"model": OPUS, "reason": "the build model first"},
            {"model": FABLE51, "reason": "…and the premium one hidden below it"},
        ]
        errors = mf.policy_errors(cfg)
        self.assertTrue(errors, "a dearer model below the fallback must be refused")
        self.assertTrue(any(FABLE51 in e for e in errors), f"unhelpful: {errors}")

    def test_an_unpriced_model_below_the_fallback_is_refused(self):
        # A price is never guessed, so an unpriced rung cannot be shown to be
        # no dearer than the fallback — fail closed.
        cfg = _canonical()
        cfg["ladders"][cfg["kinds"][JUDGEMENT]["ladder"]] = [
            {"model": FABLE51, "reason": "the planner's model"},
            {"model": OPUS, "reason": "the loud fallback"},
            {"model": SONNET6, "reason": "priced nowhere"},
        ]
        self.assertTrue(mf.policy_errors(cfg), "an unpriced rung must be refused")

    def test_every_ladder_rung_still_has_a_declared_price(self):
        declared = set(yaml.safe_load(PRICES.read_text())["prices"])
        for name, models in mf.LADDERS.items():
            for model in models:
                with self.subTest(ladder=name, model=model):
                    self.assertIn(model, declared)


class ASonnetFiveBuildIsNeverReviewedBySonnetFiveTest(unittest.TestCase):
    """The acceptance criterion, from every direction the fleet can reach it."""

    def setUp(self):
        mf.clear_availability_cache()

    def tearDown(self):
        mf.clear_availability_cache()

    def test_the_reviewers_skip_the_model_the_build_ran_on(self):
        for role in REVIEWER_ROLES:
            for avail in (
                {},                                   # everything up
                {SONNET5: False},
                {OPUS: False},
                {OPUS: False, SONNET5: False},
            ):
                with self.subTest(role=role, avail=avail):
                    mf.clear_availability_cache()
                    got = mf.select(
                        role,
                        probe=lambda m: avail.get(m, True),
                        built_on=SONNET5,
                    )
                    self.assertNotEqual(
                        got, SONNET5,
                        f"{role} reviewed a Sonnet-5 build on Sonnet 5",
                    )
                    self.assertEqual(got, OPUS)

    def test_an_unknown_build_model_still_never_reviews_on_an_overlap_model(self):
        # Fail CLOSED. A card we could not read is not evidence the build ran
        # on something else, and "never" has to survive a Linear blip.
        for role in REVIEWER_ROLES:
            with self.subTest(role=role):
                mf.clear_availability_cache()
                self.assertEqual(
                    mf.select(role, probe=lambda m: True, built_on_unknown=True),
                    OPUS,
                )

    def test_a_build_on_the_workhorse_primary_still_gets_the_advisory_top(self):
        # Opus builds are the ordinary case, and they are NOT an overlap the
        # rule declares — the critic keeps its own ladder's top rung.
        mf.clear_availability_cache()
        self.assertEqual(
            mf.select("critic", probe=lambda m: True, built_on=OPUS), SONNET5
        )

    def test_no_build_model_at_all_leaves_selection_exactly_as_it_was(self):
        mf.clear_availability_cache()
        self.assertEqual(mf.select("critic", probe=lambda m: True), SONNET5)

    def test_the_separation_does_not_touch_a_role_it_does_not_bind(self):
        # The medic is advisory too, and it reviews nothing: binding it would
        # be a spend change nobody asked for.
        mf.clear_availability_cache()
        self.assertEqual(
            mf.select("medic", probe=lambda m: True, built_on=SONNET5), SONNET5
        )
        mf.clear_availability_cache()
        self.assertEqual(
            mf.select("engineer", probe=lambda m: True, built_on=SONNET5), OPUS
        )

    def test_the_separation_is_recorded_but_is_not_a_degradation(self):
        # It is the DESIGNED path, not a rung we lost: a ::warning:: on every
        # Sonnet-5 build would teach the fleet to ignore the warning that
        # means a reviewer did not get its model.
        decision = mf.select_with_reasons(
            "critic", probe=lambda m: True, built_on=SONNET5
        )
        self.assertEqual(decision["model"], OPUS)
        self.assertEqual(decision["separated"], [SONNET5])
        self.assertFalse(decision["degraded"], "separation is not a degradation")
        note = mf.selection_note(decision)
        self.assertEqual(len(note.splitlines()), 1)
        self.assertFalse(note.startswith("DEGRADED"), note)
        self.assertIn(SONNET5, note)
        self.assertIn("separation", note.lower())

    def test_an_unavailable_reviewer_model_is_still_loud(self):
        # The separation skip must not swallow the availability one riding
        # with it: a reviewer that lost a rung to a 404 still says DEGRADED.
        mf.clear_availability_cache()
        decision = mf.select_with_reasons(
            "verifier", probe=lambda m: m != OPUS, built_on=SONNET5
        )
        self.assertTrue(decision["degraded"])
        self.assertTrue(mf.selection_note(decision).startswith("DEGRADED"))

    def test_the_cli_the_workflows_call_holds_the_same_line(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            for role in REVIEWER_ROLES:
                with self.subTest(role=role):
                    model, note = _cli_select(
                        tree, role, "--built-on", SONNET5,
                        available={OPUS: True, SONNET5: True},
                    )
                    self.assertEqual(model, OPUS)
                    self.assertIn(SONNET5, note)
                    model, _ = _cli_select(
                        tree, role, "--built-on-unknown",
                        available={OPUS: True, SONNET5: True},
                    )
                    self.assertEqual(model, OPUS)
            # stdout is still ONLY the model id — every workflow captures it.
            model, _ = _cli_select(
                tree, "critic", "--built-on", OPUS,
                available={OPUS: True, SONNET5: True},
            )
            self.assertEqual(model, SONNET5)


class TheBuildModelIsReadFromTheCardTest(unittest.TestCase):
    """`🧠 model-attempt:` is where the build model is recorded, so that is
    where the reviewer reads it from."""

    def test_the_most_recent_attempt_marker_wins(self):
        bodies = [
            f"🧠 {mf.attempt_marker(OPUS)} — engineer agent starting.",
            f"🧠 {mf.error_marker(OPUS)}",
            f"🧠 {mf.attempt_marker(SONNET5)} — engineer agent starting.",
        ]
        self.assertEqual(mf.last_attempt_model(bodies), SONNET5)

    def test_an_unknown_id_is_not_read_as_a_model(self):
        self.assertIsNone(mf.last_attempt_model(["model-attempt: not-a-model"]))
        self.assertIsNone(mf.last_attempt_model([]))
        self.assertIsNone(mf.last_attempt_model([None, ""]))

    def test_the_cli_reads_a_dumped_comment_thread(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            path = tree / "comments.json"
            path.write_text(
                json.dumps([f"🧠 {mf.attempt_marker(SONNET5)} — devops agent"])
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(tree / "scripts" / "model_fallback.py"),
                    "build-model",
                    str(path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), SONNET5)

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

    def test_the_select_step_can_reach_linear_to_read_the_card(self):
        for wf in self.REVIEW_WORKFLOWS:
            with self.subTest(workflow=wf):
                doc = yaml.safe_load((WORKFLOWS / wf).read_text())
                steps = [
                    s
                    for job in doc["jobs"].values()
                    for s in (job.get("steps") or [])
                    if s.get("id") == "model"
                ]
                self.assertTrue(steps, f"{wf}: no Select model step")
                for step in steps:
                    self.assertEqual(
                        (step.get("env") or {}).get("LINEAR_API_KEY"),
                        "${{ secrets.LINEAR_API_KEY }}",
                        f"{wf}: the select step cannot read the card",
                    )

    def test_no_review_workflow_hardcodes_the_reviewers_model(self):
        # The substitute is DATA (config/models.yaml), never a literal in a
        # workflow: a pinned id is drift by construction.
        for wf in self.REVIEW_WORKFLOWS:
            body = (WORKFLOWS / wf).read_text()
            with self.subTest(workflow=wf):
                self.assertNotIn(f"--built-on {OPUS}", body)
                self.assertNotIn(f"--built-on {SONNET5}", body)


class TheDegradePathKeepsTheRuleTest(unittest.TestCase):
    """The generated mirror is what runs when the YAML is unreadable. A
    degrade that dropped the separation would review a Sonnet-5 build on
    Sonnet 5 and look exactly like a healthy run."""

    def test_the_mirror_carries_the_separation(self):
        mirror = mf._FALLBACK_MODEL_CONFIG["review_separation"]
        cfg = _canonical()
        self.assertEqual(sorted(mirror["roles"]), sorted(_separation(cfg)["roles"]))
        self.assertEqual(mirror["rules"], _rules(cfg))

    def test_a_truncated_checkout_still_separates(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            (tree / "config" / "models.yaml").write_text("{{ not yaml\n")
            model, _ = _cli_select(
                tree, "critic", "--built-on", SONNET5,
                available={OPUS: True, SONNET5: True},
            )
            self.assertEqual(model, OPUS)


class CarriedForwardToDre3892Test(unittest.TestCase):
    """A future same-family adoption cannot silently drop the rule.

    DRE-3892's adoption path edits `config/models.yaml` and regenerates the
    mirrors — which runs exactly the schema check below. Moving the
    overlapping rung without moving its selection rule fails twice: the new
    rung is a bare overlap, and the rule left behind names a model that is no
    longer on both ladders.
    """

    def _adopt(self, cfg, new_id):
        """Swap the sonnet rung for a newer one on BOTH ladders, the way an
        adoption would — and touch nothing else."""
        for name, rungs in cfg["ladders"].items():
            for rung in rungs:
                if rung["model"] == SONNET5:
                    rung["model"] = new_id
        return cfg

    def test_adopting_the_rung_without_the_rule_is_refused(self):
        cfg = self._adopt(_canonical(), SONNET6)
        errors = mf.policy_errors(cfg)
        self.assertTrue(errors, "an adoption that drops the rule must be refused")
        self.assertTrue(
            any(SONNET6 in e for e in errors),
            f"the new rung is not named as a bare overlap: {errors}",
        )
        self.assertTrue(
            any(SONNET5 in e for e in errors),
            f"the stale rule is not named: {errors}",
        )

    def test_adopting_the_rung_WITH_the_rule_is_accepted(self):
        cfg = self._adopt(_canonical(), SONNET6)
        cfg["review_separation"]["rules"] = [
            {"built_on": SONNET6, "reviewers_use": OPUS, "reason": "carried forward"}
        ]
        self.assertEqual(mf.policy_errors(cfg), [])

    def test_ci_goes_red_on_an_adoption_that_drops_the_rule(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            _write_config(tree, self._adopt(_canonical(), SONNET6))
            proc = _run_sync(tree, "--check")
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(SONNET6, proc.stdout + proc.stderr)

    def test_an_adoption_of_this_family_moves_the_rung_on_BOTH_ladders(self):
        # Why the rule has to travel with it: `claude-sonnet-5` is one id on
        # two ladders, so a same-family successor replaces two rungs at once —
        # the workhorse backup AND the advisory top. That is precisely the
        # edit the schema check above refuses unless the selection rule moves
        # with it.
        import model_adoption

        catalog = [
            {"id": OPUS, "created_at": "2026-05-01T00:00:00Z"},
            {"id": FABLE51, "created_at": "2026-08-01T00:00:00Z"},
            {"id": SONNET46, "created_at": "2026-02-17T00:00:00Z"},
            {"id": SONNET5, "created_at": "2026-06-29T00:00:00Z"},
            {"id": SONNET6, "created_at": "2026-11-01T00:00:00Z"},
        ]
        prices = dict(model_adoption.load_prices())
        prices[SONNET6] = {"input": 2.0, "output": 10.0}
        records = model_adoption.classify_catalog(catalog, _canonical(), prices)
        adopted = [r for r in records if r["candidate"] == SONNET6]
        self.assertEqual(len(adopted), 1, records)
        cfg = _canonical()
        self.assertEqual(adopted[0]["rule"], model_adoption.RULE_ADOPT)
        self.assertEqual(
            sorted(
                r["ladder"] for r in adopted[0]["replaces"] if r["model"] == SONNET5
            ),
            sorted(
                {cfg["kinds"][ADVISORY]["ladder"], cfg["kinds"][WORKHORSE]["ladder"]}
            ),
        )


if __name__ == "__main__":
    unittest.main()
