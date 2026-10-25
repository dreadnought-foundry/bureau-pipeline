"""DRE-7303 — the critic and the verifier keep off EVERY model the card
records as building the change, and a change whose first author is not on
record stays unknown.

Until this card `build-model` printed the newest `🧠 model-attempt:` line on
the thread. Once the fix agent posts its own line (the switch epic, DRE-7318)
that newest line names the fixer and hides the original builder: an
Opus-built change fixed on Sonnet 5.5 would be reviewed by Opus. And a
hand-built card, which has no build line today, would look known the moment a
fixer line appeared on it.

WHAT THESE TESTS HOLD
---------------------
  1. A build line is `🧠 model-attempt: <id> — <role> agent starting` with a
     role other than `planner` and an `attributable` id; an authoring line is
     a build line whose role is not `fixer`. `build-model` prints every
     distinct model on a build line, oldest first, joined with commas — and
     nothing when the thread holds no authoring line, so the workflows pass
     `--built-on-unknown`.
  2. `select --built-on <a>,<b>` bars every listed model; a declared rule
     still leads for each barred model it names.
  3. A walk with every rung barred chooses NOTHING: `select` exits non-zero,
     prints nothing on stdout and names the builders in one `::error::` line.
  4. For every pair of models on the fixture's build walks recorded on one
     card, neither reviewer is ever chosen on either of them.
  5. One builder selects exactly as today, with today's note clause, and
     `last_attempt_model` / `_build_model_from_thread` still read the newest
     line for their other readers.

Every model id is read off the frozen home document by its place, as
`tests/test_review_separation.py` does.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import model_fixture  # noqa: E402
from model_fixture import fixture_home, home_env, ladder  # noqa: E402

HOME = fixture_home()
_WORK = ladder(HOME, "workhorse")
_ADVICE = ladder(HOME, "advisory")
_PLAN = ladder(HOME, "judgement")

# The reviewers' walk: the upper overlap, the lower one, the build top last.
UPPER, LOWER, BUILD_TOP = _ADVICE
# A build model no reviewer walk names (the "Opus 5" of the measured cards).
BUILD_ONLY = next(m for m in _WORK if m not in _ADVICE and m not in _PLAN)
PLAN_TOP = _PLAN[0]

REVIEWER_ROLES = tuple(model_fixture.fixture_config()["review_separation"]["roles"])
# The rules the frozen file declares, `{built_on: reviewers_use}`.
RULES = {r["built_on"]: r["reviewers_use"]
         for r in model_fixture.fixture_config()["review_separation"]["rules"]}

_TREE_DIR: tempfile.TemporaryDirectory | None = None
TREE: Path | None = None
fmf = None


def setUpModule():
    global _TREE_DIR, TREE, fmf
    _TREE_DIR = tempfile.TemporaryDirectory()
    TREE = model_fixture.fixture_tree(Path(_TREE_DIR.name) / "tree")
    fmf = model_fixture.fixture_module(TREE, doc=HOME)


def tearDownModule():
    _TREE_DIR.cleanup()


def line(model: str, role: str) -> str:
    """The build line `agent-task.yml` posts, in its full shape."""
    return (f"🧠 model-attempt: {model} — {role} agent starting (turns=400, "
            "fresh). model-policy: … Run: https://github.com/x/y/actions/runs/1")


def planner_line(model: str) -> str:
    return f"🧠 model-attempt: {model} — planner agent starting. model-policy: …"


def classifier_receipt(asked: str, answered: str) -> str:
    return (f"🧠 model-attempt: {asked} (asked) / {answered} (answered) · "
            "cost $0.0123 — planning classifier read the card.")


def build_model_cli(bodies: list) -> subprocess.CompletedProcess:
    """`model_fallback.py build-model` over a dumped thread of `bodies`."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "thread.json"
        path.write_text(json.dumps(bodies))
        return subprocess.run(
            [sys.executable, str(TREE / "scripts" / "model_fallback.py"),
             "build-model", str(path)],
            capture_output=True, text=True, env=home_env(TREE, HOME), timeout=120)


def select_cli(role: str, *extra: str, available=None) -> subprocess.CompletedProcess:
    """The selector CLI the review workflows call, with a stubbed probe."""
    env = home_env(TREE, HOME, BUREAU_FAKE_AVAILABLE=available or {})
    return subprocess.run(
        [sys.executable, str(TREE / "scripts" / "model_fallback.py"), "select",
         role, *extra, "--explain-file", str(TREE / "why.txt")],
        capture_output=True, text=True, env=env, timeout=120)


def review_cli(role: str, bodies: list, available=None) -> subprocess.CompletedProcess:
    """A reviewer's Select model step end to end: `build-model` over the
    thread, then `--built-on` what it printed or `--built-on-unknown`."""
    built_on = build_model_cli(bodies).stdout.strip()
    flags = ["--built-on", built_on] if built_on else ["--built-on-unknown"]
    return select_cli(role, *flags, available=available)


class ABuildLineIsTheContractShapeTest(unittest.TestCase):
    """What `build_models` counts, and what it leaves out."""

    def test_an_engineer_then_a_fixer_reads_both_oldest_first(self):
        bodies = ["⏳ 1/5 plan", line(BUILD_TOP, "engineer"), line(UPPER, "fixer")]
        self.assertEqual(fmf.build_models(bodies), [BUILD_TOP, UPPER])
        proc = build_model_cli(bodies)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), f"{BUILD_TOP},{UPPER}")

    def test_only_fixer_lines_read_as_unknown(self):
        bodies = [line(UPPER, "fixer"), line(LOWER, "fixer")]
        self.assertEqual(fmf.build_models(bodies), [])
        proc = build_model_cli(bodies)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "\n")

    def test_only_the_planners_and_the_classifiers_lines_read_as_unknown(self):
        bodies = [classifier_receipt(PLAN_TOP, PLAN_TOP), planner_line(PLAN_TOP)]
        self.assertEqual(fmf.build_models(bodies), [])
        self.assertEqual(build_model_cli(bodies).stdout.strip(), "")

    def test_the_planners_line_is_no_build_line_beside_an_authoring_one(self):
        bodies = [classifier_receipt(UPPER, UPPER), planner_line(PLAN_TOP),
                  line(BUILD_TOP, "engineer")]
        self.assertEqual(fmf.build_models(bodies), [BUILD_TOP])

    def test_every_role_but_planner_and_fixer_authors(self):
        for role in ("engineer", "devops", "frontend", "repairer", "hand-built"):
            with self.subTest(role=role):
                self.assertEqual(
                    fmf.build_models([line(UPPER, role), line(LOWER, "fixer")]),
                    [UPPER, LOWER])

    def test_a_fixer_line_before_the_authoring_line_keeps_thread_order(self):
        bodies = [line(UPPER, "fixer"), line(BUILD_TOP, "hand-built")]
        self.assertEqual(fmf.build_models(bodies), [UPPER, BUILD_TOP])

    def test_a_model_on_several_lines_is_named_once(self):
        bodies = [line(BUILD_TOP, "engineer"), line(UPPER, "fixer"),
                  line(BUILD_TOP, "engineer"), line(UPPER, "fixer")]
        self.assertEqual(fmf.build_models(bodies), [BUILD_TOP, UPPER])
        self.assertEqual(build_model_cli(bodies).stdout.strip(), f"{BUILD_TOP},{UPPER}")

    def test_a_line_that_is_not_the_shape_is_not_counted(self):
        for body in (
            f"🧠 model-attempt: {BUILD_TOP} — devops agent",          # no "starting"
            f"🧠 model-attempt: {BUILD_TOP}[1m] — engineer agent starting",
            "🧠 model-attempt: Opus 5.5 — engineer agent starting",
            "🧠 model-attempt: not-a-model — engineer agent starting",
            f"model-attempt: {BUILD_TOP}",                           # bare heartbeat
            f"🧠 model-error: {BUILD_TOP}",
        ):
            with self.subTest(body=body):
                self.assertEqual(fmf.build_models([body]), [])
                self.assertEqual(build_model_cli([body]).stdout.strip(), "")

    def test_a_dumped_thread_with_authors_reads_the_same(self):
        records = [{"body": line(BUILD_TOP, "engineer"), "author": "bot"},
                   {"body": line(UPPER, "fixer"), "author": "bot"}]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.json"
            path.write_text(json.dumps(records))
            self.assertEqual(fmf._build_models_from_thread(str(path)), [BUILD_TOP, UPPER])
            self.assertEqual(fmf._build_models_from_thread(str(Path(td) / "nope")), [])

    def test_the_newest_line_readers_are_unchanged(self):
        bodies = [line(BUILD_TOP, "engineer"), line(UPPER, "fixer")]
        self.assertEqual(fmf.last_attempt_model(bodies), UPPER)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.json"
            path.write_text(json.dumps(bodies))
            self.assertEqual(fmf._build_model_from_thread(str(path)), UPPER)


class EveryBuilderIsBarredTest(unittest.TestCase):
    """`--built-on <a>,<b>` keeps the reviewer off both."""

    def setUp(self):
        fmf.clear_availability_cache()

    def test_an_opus_build_fixed_on_sonnet_is_reviewed_on_neither(self):
        bodies = [line(BUILD_TOP, "engineer"), line(UPPER, "fixer")]
        for role in REVIEWER_ROLES:
            with self.subTest(role=role):
                proc = review_cli(role, bodies)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout.strip(), LOWER)
                note = (TREE / "why.txt").read_text()
                self.assertIn(
                    f"build/review separation: this change was built on {BUILD_TOP} "
                    f"and {UPPER}, so the reviewer uses neither", note)

    def test_the_library_takes_a_list_or_the_cli_string(self):
        for built_on in ([BUILD_TOP, UPPER], f"{BUILD_TOP},{UPPER}"):
            for role in REVIEWER_ROLES:
                with self.subTest(built_on=built_on, role=role):
                    fmf.clear_availability_cache()
                    got = fmf.select_with_reasons(
                        role, probe=lambda m: True, built_on=built_on)
                    self.assertEqual(got["model"], LOWER)
                    self.assertEqual(got["ladder"], [LOWER])
                    self.assertEqual(got["separated"], [UPPER, BUILD_TOP])
                    self.assertFalse(got["degraded"])

    def test_a_declared_rule_still_leads_for_each_barred_model_it_names(self):
        # The second builder sits on no reviewer walk, so the rule for the
        # first one leads exactly as it does when that one builds alone.
        for barred in (UPPER, LOWER):
            with self.subTest(barred=barred):
                fmf.clear_availability_cache()
                alone = fmf.select_with_reasons(
                    REVIEWER_ROLES[0], probe=lambda m: True, built_on=barred)
                fmf.clear_availability_cache()
                both = fmf.select_with_reasons(
                    REVIEWER_ROLES[0], probe=lambda m: True,
                    built_on=[BUILD_ONLY, barred])
                self.assertEqual(both["model"], RULES[barred])
                self.assertEqual(both["ladder"], alone["ladder"])

    def test_every_pair_of_build_models_on_one_card_is_never_chosen(self):
        builds = sorted(fmf._build_models(fmf.KINDS, fmf.effective_ladders(fmf.CONFIG, None)))
        self.assertGreaterEqual(len(builds), 4, builds)
        for first, second in itertools.permutations(builds, 2):
            bodies = [line(first, "engineer"), line(second, "fixer")]
            built_on = fmf.build_models(bodies)
            self.assertEqual(built_on, [first, second])
            for role in REVIEWER_ROLES:
                walk = fmf.ladder_for(role)
                for n in range(len(walk) + 1):
                    for down in itertools.combinations(walk, n):
                        with self.subTest(pair=(first, second), role=role, down=down):
                            fmf.clear_availability_cache()
                            got = fmf.select(role, probe=lambda m, d=down: m not in d,
                                             built_on=built_on)
                            self.assertNotIn(got, (first, second))

    def test_one_builder_selects_exactly_as_today_with_todays_clause(self):
        for role in REVIEWER_ROLES:
            for built in (UPPER, LOWER, BUILD_TOP):
                with self.subTest(role=role, built=built):
                    fmf.clear_availability_cache()
                    as_str = fmf.select_with_reasons(
                        role, probe=lambda m: True, built_on=built)
                    fmf.clear_availability_cache()
                    as_list = fmf.select_with_reasons(
                        role, probe=lambda m: True, built_on=[built])
                    self.assertEqual(as_str["model"], as_list["model"])
                    self.assertNotEqual(as_str["model"], built)
                    note = fmf.selection_note(as_list)
                    self.assertIn(
                        f"build/review separation: this build ran on {built}, so "
                        "the reviewer does not", note)
        proc = review_cli(REVIEWER_ROLES[0], [line(UPPER, "engineer"),
                                              line(UPPER, "fixer")])
        self.assertEqual(proc.stdout.strip(), RULES[UPPER])
        self.assertIn(f"this build ran on {UPPER}, so the reviewer does not",
                      (TREE / "why.txt").read_text())


class AFullyBarredWalkChoosesNoReviewerTest(unittest.TestCase):
    """Every rung barred: no unseparated fallback, the step fails, a person
    decides."""

    def setUp(self):
        fmf.clear_availability_cache()

    def test_the_cli_exits_non_zero_with_one_error_line_and_no_model(self):
        for role in REVIEWER_ROLES:
            with self.subTest(role=role):
                proc = select_cli(role, "--built-on", f"{UPPER},{LOWER},{BUILD_TOP}")
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout, "")
                self.assertIn(
                    f"::error::build/review separation: this change was built on "
                    f"{UPPER}, {LOWER} and {BUILD_TOP}, which is every model on "
                    f"the {role}'s walk, so no reviewer is chosen\n", proc.stderr)
                errors = [ln for ln in proc.stderr.splitlines() if "::error::" in ln]
                self.assertEqual(len(errors), 1, proc.stderr)

    def test_the_library_never_returns_a_barred_model(self):
        for role in REVIEWER_ROLES:
            for down in ((), (UPPER,), tuple(_ADVICE)):
                with self.subTest(role=role, down=down):
                    fmf.clear_availability_cache()
                    with self.assertRaises(fmf.NoReviewerLeft):
                        fmf.select(role, probe=lambda m, d=down: m not in d,
                                   built_on=[BUILD_TOP, UPPER, LOWER])

    def test_one_builder_on_a_one_rung_walk_chooses_nothing_too(self):
        # The defensive branch used to hand back the whole walk, build model
        # and all.
        with self.assertRaises(fmf.NoReviewerLeft):
            fmf.select(REVIEWER_ROLES[0], probe=lambda m: True,
                       ladder=[UPPER], built_on=UPPER)

    def test_an_unknown_builder_still_keeps_the_walks_last_rung(self):
        proc = select_cli(REVIEWER_ROLES[0], "--built-on-unknown")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), RULES[LOWER])


class TheWorkflowCommentsSayTheRuleTest(unittest.TestCase):
    """The comment above each reviewer's Select model step says every
    builder is barred, and that a fully barred walk fails the step."""

    def test_each_comment_names_every_builder_and_the_failed_step(self):
        for wf in ("qa-review.yml", "verify.yml"):
            with self.subTest(workflow=wf):
                lines = (WORKFLOWS / wf).read_text().splitlines()
                at = next(i for i, ln in enumerate(lines)
                          if ln == "      - name: Select model"
                          and lines[i + 1] == "        id: model")
                above = list(itertools.takewhile(
                    lambda ln: ln.lstrip().startswith("#"), reversed(lines[:at])))
                self.assertTrue(above, f"{wf}: no comment above Select model")
                flat = " ".join(ln.strip().lstrip("#").strip() for ln in reversed(above))
                self.assertNotIn("the model this card's last attempt ran on", flat)
                self.assertIn("every model", flat)
                self.assertIn("fixer", flat)
                self.assertIn("fails the step", flat)


if __name__ == "__main__":
    unittest.main()
