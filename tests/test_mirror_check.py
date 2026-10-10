"""RED-first tests for the agent-bureau mirror check (DRE-6496).

On 2026-10-09 one bureau-pipeline change — the `operator-step` vocabulary
(DRE-6227/DRE-6228) — reached `stable` around 03:00 PT and broke agent-bureau's
CI three separate times that day: the read door's e2e, the relay's lane guard
and the open-holds report. agent-bureau mirrors several of this repo's
vocabularies, and each mirror's drift test reads bureau-pipeline at `stable`.
So a change here was detected only AFTER it was promoted, by every
agent-bureau PR at once, and fixed by hand each time.

`scripts/mirror_check.py` runs those drift tests against the CANDIDATE before
`stable` moves:

  * discovery — every `test_*.py` / `*_test.py` in the agent-bureau checkout
    whose text names `BUREAU_PIPELINE_DIR` or `.bureau-pipeline`; never a list;
  * the run — pytest from agent-bureau's root with the candidate at
    `<agent-bureau>/.bureau-pipeline` AND in `BUREAU_PIPELINE_DIR`, one
    process per suite, a suite being the first component of a test's path
    (DRE-6670);
  * the re-read — every suite with a failure run again, whole, against the
    current `stable`, so a test red on both is agent-bureau's own red, or the
    run's own doing, and refuses nothing;
  * the mirror map — the bureau-pipeline file a failing test mirrors, read off
    the test's own string literals, never guessed;
  * the card — one per breakage, found again by its exact title.

The stand-in trees under tests/fixtures/mirror-check/ are modeled on the
2026-10-09 change, because the real agent-bureau as it stood that day is not
readable from a pull request. Since DRE-6622 the stand-in agent-bureau has the
real shape around those tests too: a coverage floor in its pytest settings and
requirements that declare no pytest, so every check below runs through both.
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402
import mirror_check  # noqa: E402
import validate_card  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "mirror-check"

#: The stand-in agent-bureau's pytest settings, with its coverage floor
#: (DRE-6622). Kept beside the tree rather than in it — see the file — and
#: written into every throwaway copy as `pyproject.toml`.
PYTEST_SETTINGS = FIXTURES / "agent-bureau.pyproject.toml"

ROUTING_TEST = "console/backend/tests/test_routing_verdicts_mirror.py"
RELAY_TEST = "cloud/relay/test_lane_guard_mirror.py"
UNRELATED_TEST = "console/backend/tests/test_unrelated.py"
VERDICTS = "config/routing-verdicts.json"

CANDIDATE = "c" * 40
STABLE = "5" * 40
AGENT_BUREAU = "a" * 40
RUN_URL = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/1"


class _Trees:
    """A throwaway copy of the stand-in: agent-bureau with a bureau-pipeline
    checkout inside it, and the current `stable` beside it."""

    def __init__(self, test, *, candidate="bureau-pipeline-new",
                 stable="bureau-pipeline-old", overlay=None):
        self._tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.agent_bureau = base / "agent-bureau"
        shutil.copytree(FIXTURES / "agent-bureau", self.agent_bureau)
        if overlay:
            shutil.copytree(overlay, self.agent_bureau, dirs_exist_ok=True)
        shutil.copyfile(PYTEST_SETTINGS, self.agent_bureau / "pyproject.toml")
        self.candidate = self.agent_bureau / mirror_check.PIPELINE_DIRNAME
        shutil.copytree(FIXTURES / candidate, self.candidate)
        self.stable = base / "bureau-pipeline-stable"
        if stable:
            shutil.copytree(FIXTURES / stable, self.stable)

    def write(self, rel, text):
        path = self.agent_bureau / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def check(self, **kw):
        kw.setdefault("candidate", CANDIDATE)
        kw.setdefault("stable_sha", STABLE)
        kw.setdefault("agent_bureau_sha", AGENT_BUREAU)
        kw.setdefault("python", sys.executable)
        return mirror_check.check(self.agent_bureau, self.stable, **kw)


# --------------------------------------------------------------------------- #
# 1. Which tests — discovered, never typed                                    #
# --------------------------------------------------------------------------- #


class DiscoveryTest(unittest.TestCase):
    def test_it_finds_the_tests_that_read_the_pipeline_and_nothing_else(self):
        trees = _Trees(self)
        self.assertEqual(mirror_check.discover(trees.agent_bureau),
                         sorted([ROUTING_TEST, RELAY_TEST]))

    def test_a_test_that_names_neither_marker_is_not_a_mirror_test(self):
        trees = _Trees(self)
        self.assertNotIn(UNRELATED_TEST, mirror_check.discover(trees.agent_bureau))

    def test_both_markers_and_both_file_patterns_are_read(self):
        trees = _Trees(self)
        trees.write("cloud/other/relay_mirror_test.py",
                    "PATH = '.bureau-pipeline/config/holds.json'\n")
        trees.write("cloud/other/test_env_only.py",
                    "import os\nos.environ['BUREAU_PIPELINE_DIR']\n")
        trees.write("cloud/other/helpers.py",
                    "import os\nos.environ['BUREAU_PIPELINE_DIR']\n")
        found = mirror_check.discover(trees.agent_bureau)
        self.assertIn("cloud/other/relay_mirror_test.py", found)
        self.assertIn("cloud/other/test_env_only.py", found)
        self.assertNotIn("cloud/other/helpers.py", found)

    def test_the_pipeline_checkout_inside_agent_bureau_is_never_searched(self):
        """The candidate sits at `<agent-bureau>/.bureau-pipeline`, and
        bureau-pipeline's own suite names `.bureau-pipeline` everywhere — its
        tests are not agent-bureau's mirrors."""
        trees = _Trees(self)
        (trees.candidate / "tests").mkdir()
        (trees.candidate / "tests" / "test_self.py").write_text(
            "BUREAU_PIPELINE_DIR = '.bureau-pipeline'\n")
        for found in mirror_check.discover(trees.agent_bureau):
            self.assertFalse(found.startswith(mirror_check.PIPELINE_DIRNAME), found)

    def test_requirements_come_from_the_root_and_the_test_directories(self):
        trees = _Trees(self)
        for rel in ("requirements.txt", "requirements-dev.txt",
                    "console/backend/requirements.txt",
                    "cloud/relay/requirements.txt",
                    "infra/requirements.txt"):
            trees.write(rel, "pytest\n")
        found = mirror_check.requirement_files(
            trees.agent_bureau, mirror_check.discover(trees.agent_bureau))
        self.assertEqual(found, [
            "cloud/relay/requirements.txt",
            "console/backend/requirements.txt",
            "requirements-dev.txt",
            "requirements.txt",
        ])


# --------------------------------------------------------------------------- #
# 2. Which file each test mirrors — read from the test itself                 #
# --------------------------------------------------------------------------- #


class MirrorMappingTest(unittest.TestCase):
    def setUp(self):
        self.trees = _Trees(self)

    def test_a_path_built_from_literals_names_the_file_it_mirrors(self):
        """`PIPELINE / "config" / "routing-verdicts.json"` is the console's
        shape — no single literal names the file, the chain does."""
        self.assertEqual(
            mirror_check.mirrored_files(self.trees.agent_bureau / ROUTING_TEST,
                                        self.trees.candidate),
            [VERDICTS])

    def test_a_literal_path_names_the_file_it_mirrors(self):
        self.assertEqual(
            mirror_check.mirrored_files(self.trees.agent_bureau / RELAY_TEST,
                                        self.trees.candidate),
            [VERDICTS])

    def test_a_dotted_checkout_prefix_is_read_through(self):
        path = self.trees.write(
            "cloud/x/test_prefixed.py",
            "P = '.bureau-pipeline/config/routing-verdicts.json'\n")
        self.assertEqual(
            mirror_check.mirrored_files(path, self.trees.candidate), [VERDICTS])

    def test_a_test_naming_no_candidate_file_reads_nothing_rather_than_a_guess(self):
        path = self.trees.write(
            "cloud/x/test_vague.py",
            "import os\nD = os.environ['BUREAU_PIPELINE_DIR']\n"
            "N = 'config/holds.json'\nM = 'config'\n")
        self.assertEqual(mirror_check.mirrored_files(path, self.trees.candidate), [])

    def test_a_literal_cannot_reach_outside_the_candidate(self):
        outside = self.trees.agent_bureau.parent / "secret.json"
        outside.write_text("{}")
        path = self.trees.write("cloud/x/test_escape.py",
                                "P = '../secret.json'\nQ = '/etc/hostname'\n")
        self.assertEqual(mirror_check.mirrored_files(path, self.trees.candidate), [])


# --------------------------------------------------------------------------- #
# 3. The check, end to end on the stand-in                                    #
# --------------------------------------------------------------------------- #


class CheckTest(unittest.TestCase):
    def test_the_operator_step_change_fails_the_mirror_tests(self):
        """The 2026-10-09 shape: the candidate carries the new vocabulary,
        `stable` the old, and agent-bureau's mirrors still hold the old."""
        trees = _Trees(self)
        result = trees.check()
        self.assertEqual(result["status"], mirror_check.MIRROR_FAILED)
        failing = {f["test"].split("::")[0]: f["mirrors"] for f in result["failing"]}
        self.assertEqual(failing, {ROUTING_TEST: [VERDICTS], RELAY_TEST: [VERDICTS]})
        self.assertEqual(result["already_red"], [])
        self.assertEqual(result["tests"], 2)
        self.assertEqual(result["agent_bureau_sha"], AGENT_BUREAU)
        self.assertEqual(result["candidate"], CANDIDATE)
        self.assertEqual(result["stable"], STABLE)

    def test_a_candidate_that_breaks_nothing_passes(self):
        trees = _Trees(self, candidate="bureau-pipeline-old")
        result = trees.check()
        self.assertEqual(result["status"], mirror_check.MIRROR_PASSED)
        self.assertEqual(result["failing"], [])
        self.assertEqual(result["tests"], 2)

    def test_the_candidate_is_what_both_lookups_read(self):
        """`.bureau-pipeline` AND `BUREAU_PIPELINE_DIR`: a test that reads
        only one of them still reads the candidate."""
        trees = _Trees(self)
        trees.write("cloud/x/test_both.py", (
            "import os\n"
            "from pathlib import Path\n"
            "ROOT = Path(__file__).resolve().parents[2]\n"
            "def test_both_name_the_same_checkout():\n"
            "    env = Path(os.environ['BUREAU_PIPELINE_DIR']).resolve()\n"
            "    assert env == (ROOT / '.bureau-pipeline').resolve()\n"
            "    assert (env / 'config' / 'routing-verdicts.json').is_file()\n"
        ))
        result = trees.check()
        tests = {f["test"].split("::")[0] for f in result["failing"]}
        self.assertNotIn("cloud/x/test_both.py", tests)
        self.assertEqual(result["tests"], 3)

    def test_a_test_red_on_stable_too_is_named_and_refuses_nothing(self):
        """Candidate and `stable` both carry the new vocabulary: the mirror
        tests fail on both, so they are agent-bureau's own red."""
        trees = _Trees(self, stable="bureau-pipeline-new")
        result = trees.check()
        self.assertEqual(result["status"], mirror_check.MIRROR_PASSED)
        self.assertEqual(result["failing"], [])
        red = {f["test"].split("::")[0] for f in result["already_red"]}
        self.assertEqual(red, {ROUTING_TEST, RELAY_TEST})

    def test_only_the_candidates_own_failures_refuse(self):
        trees = _Trees(self)
        trees.write("cloud/x/test_always_red.py", (
            "BUREAU_PIPELINE_DIR = 'read but broken for its own reasons'\n"
            "def test_red_everywhere():\n"
            "    assert False\n"
        ))
        result = trees.check()
        self.assertEqual(result["status"], mirror_check.MIRROR_FAILED)
        failing = {f["test"].split("::")[0] for f in result["failing"]}
        red = {f["test"].split("::")[0] for f in result["already_red"]}
        self.assertEqual(failing, {ROUTING_TEST, RELAY_TEST})
        self.assertEqual(red, {"cloud/x/test_always_red.py"})

    def test_a_collection_error_is_a_failure_of_its_file(self):
        trees = _Trees(self, candidate="bureau-pipeline-old")
        trees.write("cloud/x/test_import_breaks.py", (
            "import json, os\n"
            "P = os.path.join(os.environ['BUREAU_PIPELINE_DIR'], 'config/routing-verdicts.json')\n"
            "assert all('operator-step' not in v['marks'] for v in json.load(open(P))['verdicts'])\n"
            "def test_never_reached():\n"
            "    pass\n"
        ))
        result = mirror_check.check(
            trees.agent_bureau, trees.stable, candidate=CANDIDATE,
            stable_sha=STABLE, agent_bureau_sha=AGENT_BUREAU,
            python=sys.executable)
        self.assertEqual(result["status"], mirror_check.MIRROR_PASSED)
        # Same tree on the candidate side but the new vocabulary breaks it.
        trees2 = _Trees(self)
        trees2.write("cloud/x/test_import_breaks.py",
                     (trees.agent_bureau / "cloud/x/test_import_breaks.py").read_text())
        result = trees2.check()
        failing = {f["test"].split("::")[0] for f in result["failing"]}
        self.assertIn("cloud/x/test_import_breaks.py", failing)

    def test_the_checkouts_are_put_back_after_the_re_read(self):
        trees = _Trees(self)
        trees.check()
        def operator_marks(tree):
            verdicts = json.loads((tree / VERDICTS).read_text())["verdicts"]
            return [v["marks"] for v in verdicts if v["name"] == "OPERATOR"][0]

        self.assertIn("operator-step", operator_marks(trees.candidate))
        self.assertIn("hand-built", operator_marks(trees.stable))

    # --- a check that could not run is never a failing candidate ---------- #

    def _assert_blocked(self, result, *words):
        self.assertEqual(result["status"], mirror_check.MIRROR_BLOCKED)
        self.assertEqual(result["failing"], [])
        for word in words:
            self.assertIn(word, result["why"])

    def test_a_setup_step_that_failed_blocks_the_check(self):
        trees = _Trees(self)
        for name in ("token", "agent-bureau checkout", "install"):
            with self.subTest(name=name):
                self._assert_blocked(
                    trees.check(setup={name: "failure"}), name, "failure")

    def test_a_setup_step_that_succeeded_does_not(self):
        trees = _Trees(self)
        result = trees.check(setup={"token": "success", "install": "success"})
        self.assertEqual(result["status"], mirror_check.MIRROR_FAILED)

    def test_a_missing_agent_bureau_checkout_blocks_the_check(self):
        trees = _Trees(self)
        shutil.rmtree(trees.agent_bureau)
        self._assert_blocked(trees.check(), "agent-bureau")

    def test_a_missing_candidate_checkout_blocks_the_check(self):
        trees = _Trees(self)
        shutil.rmtree(trees.candidate)
        self._assert_blocked(trees.check(), "candidate")

    def test_no_mirror_tests_at_all_blocks_rather_than_passes(self):
        trees = _Trees(self)
        for rel in (ROUTING_TEST, RELAY_TEST):
            (trees.agent_bureau / rel).unlink()
        self._assert_blocked(trees.check(), "no test")

    def test_pytest_that_cannot_start_blocks_the_check(self):
        trees = _Trees(self)
        self._assert_blocked(trees.check(python="/nonexistent/python3"), "pytest")

    def test_a_run_past_its_time_limit_blocks_the_check(self):
        trees = _Trees(self, candidate="bureau-pipeline-old")
        trees.write("cloud/x/test_slow.py", (
            "import time\n"
            "BUREAU_PIPELINE_DIR = 'slow'\n"
            "def test_slow():\n"
            "    time.sleep(30)\n"
        ))
        self._assert_blocked(trees.check(budget_seconds=2), "time limit")

    def test_failures_with_no_stable_to_re_read_block_the_check(self):
        trees = _Trees(self, stable=None)
        self._assert_blocked(trees.check(), "stable")


# --------------------------------------------------------------------------- #
# 4. The run's own receipt                                                    #
# --------------------------------------------------------------------------- #


class SummaryLineTest(unittest.TestCase):
    def test_the_duration_line_has_the_harness_shape(self):
        line = mirror_check.summary_line(
            {"seconds": 125, "tests": 103, "agent_bureau_sha": AGENT_BUREAU})
        self.assertEqual(line, f"⏱ mirror check: 2m (103 tests, agent-bureau {AGENT_BUREAU[:7]})")

    def test_the_cli_writes_the_result_and_the_summary_line(self):
        trees = _Trees(self)
        out = trees.agent_bureau.parent / "mirror_result.json"
        summary = trees.agent_bureau.parent / "summary.md"
        os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = mirror_check.main([
                    "run", "--agent-bureau", str(trees.agent_bureau),
                    "--stable", str(trees.stable), "--candidate", CANDIDATE,
                    "--stable-sha", STABLE, "--agent-bureau-sha", AGENT_BUREAU,
                    "--out", str(out), "--setup", "token=success",
                ])
        finally:
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
        self.assertEqual(rc, 0)
        result = json.loads(out.read_text())
        self.assertEqual(result["status"], mirror_check.MIRROR_FAILED)
        self.assertRegex(summary.read_text(),
                         r"⏱ mirror check: \d+m \(2 tests, agent-bureau aaaaaaa\)")

    def test_discover_names_the_requirements_for_the_install_step(self):
        trees = _Trees(self)
        trees.write("requirements.txt", "pytest\n")
        trees.write("console/backend/requirements.txt", "pytest\n")
        out = trees.agent_bureau.parent / "gh_output"
        out.write_text("")
        os.environ["GITHUB_OUTPUT"] = str(out)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = mirror_check.main([
                    "discover", "--agent-bureau", str(trees.agent_bureau),
                    "--prefix", "agent-bureau"])
        finally:
            os.environ.pop("GITHUB_OUTPUT", None)
        self.assertEqual(rc, 0)
        written = out.read_text()
        self.assertIn("count=2", written)
        self.assertIn("agent-bureau/requirements.txt", written)
        self.assertIn("agent-bureau/console/backend/requirements.txt", written)


# --------------------------------------------------------------------------- #
# 5. The card the refusal files                                               #
# --------------------------------------------------------------------------- #


def _failed(*failing, already_red=()):
    return {
        "status": mirror_check.MIRROR_FAILED, "why": "",
        "candidate": CANDIDATE, "stable": STABLE,
        "agent_bureau_sha": AGENT_BUREAU, "tests": 103, "seconds": 60,
        "failing": list(failing), "already_red": list(already_red),
    }


ROUTING_FAIL = {"test": f"{ROUTING_TEST}::test_the_mirror_matches_bureau_pipeline",
                "mirrors": [VERDICTS]}
HOLDS_FAIL = {"test": "console/backend/tests/test_open_holds_report.py::test_registry",
              "mirrors": ["config/holds.json"]}
VAGUE_FAIL = {"test": "cloud/relay/test_vague.py::test_x", "mirrors": []}


class _FakeOps:
    def __init__(self, *, open_card=None, comments=(), fail=None):
        self.open_card = open_card
        self.comments = list(comments)
        self.fail = fail or set()
        self.created = []
        self.posted = []

    def find_open(self, title):
        if "find" in self.fail:
            raise RuntimeError("Linear 503")
        self.looked_up = title
        return self.open_card

    def comment_bodies(self, identifier):
        if "read" in self.fail:
            raise RuntimeError("Linear 503")
        return self.comments

    def oneoff(self, title, body, labels):
        if "create" in self.fail:
            raise RuntimeError("Linear 503")
        self.created.append((title, body, list(labels)))
        return {"identifier": "DRE-9001", "url": "https://linear.app/x/DRE-9001"}

    def cmd_comment(self, identifier, body):
        if "comment" in self.fail:
            raise RuntimeError("Linear 503")
        self.posted.append((identifier, body))


class CardTest(unittest.TestCase):
    def test_the_title_names_the_sorted_mirrored_files_and_no_sha(self):
        title = mirror_check.card_title(_failed(ROUTING_FAIL, HOLDS_FAIL))
        self.assertEqual(
            title, "agent-bureau: mirrors of config/holds.json, "
                   "config/routing-verdicts.json must follow bureau-pipeline")
        self.assertNotIn(CANDIDATE[:7], title)

    def test_the_title_is_the_same_for_the_same_breakage_at_another_candidate(self):
        one = _failed(ROUTING_FAIL)
        two = dict(_failed(ROUTING_FAIL), candidate="d" * 40, agent_bureau_sha="e" * 40)
        self.assertEqual(mirror_check.card_title(one), mirror_check.card_title(two))

    def test_the_title_falls_back_to_the_test_names_when_no_file_was_read(self):
        title = mirror_check.card_title(_failed(VAGUE_FAIL))
        self.assertEqual(title, "agent-bureau: mirrors of cloud/relay/test_vague.py "
                                "must follow bureau-pipeline")

    def test_the_labels_send_it_to_the_fleet(self):
        labels = mirror_check.card_labels()
        self.assertEqual(sorted(labels), sorted(
            ["repo:agent-bureau", "agent:engineer", "initiative:bureau", "Bug"]))
        self.assertNotIn("automation", labels)
        self.assertNotIn("hand-built", labels)

    def test_the_body_carries_everything_the_fix_needs(self):
        result = _failed(ROUTING_FAIL, VAGUE_FAIL)
        body = mirror_check.card_body(result, RUN_URL)
        for wanted in (ROUTING_FAIL["test"], VERDICTS, VAGUE_FAIL["test"],
                       mirror_check.NOT_READ, CANDIDATE, AGENT_BUREAU, STABLE,
                       RUN_URL, "force"):
            self.assertIn(wanted, body)

    def test_the_body_is_a_card_the_create_seam_accepts(self):
        result = _failed(ROUTING_FAIL)
        body = mirror_check.card_body(result, RUN_URL)
        title = mirror_check.card_title(result)
        labels = mirror_check.card_labels()
        self.assertIsNone(linear_ops.body_problem(body))
        self.assertEqual(validate_card.missing(body, labels), [])
        self.assertIsNone(validate_card.repo_title_mismatch(title, labels))
        self.assertEqual(linear_ops.parse_blocked_by(body), [])

    def test_the_body_carries_no_verdict_marker(self):
        body = mirror_check.card_body(_failed(ROUTING_FAIL), RUN_URL)
        for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(marker, body)

    def test_no_open_card_files_one(self):
        ops = _FakeOps()
        got = mirror_check.file_card(_failed(ROUTING_FAIL), RUN_URL, ops=ops)
        self.assertEqual(got["card"], "DRE-9001")
        self.assertFalse(got["owed"])
        self.assertEqual(len(ops.created), 1)
        title, _body, labels = ops.created[0]
        self.assertEqual(title, mirror_check.card_title(_failed(ROUTING_FAIL)))
        self.assertEqual(ops.looked_up, title)
        self.assertIn("repo:agent-bureau", labels)
        self.assertEqual(ops.posted, [])

    def test_an_open_card_for_the_same_files_gets_a_comment_not_a_twin(self):
        ops = _FakeOps(open_card="DRE-8000", comments=["filed earlier"])
        result = dict(_failed(ROUTING_FAIL), candidate="d" * 40)
        got = mirror_check.file_card(result, RUN_URL, ops=ops)
        self.assertEqual(got["card"], "DRE-8000")
        self.assertEqual(ops.created, [])
        self.assertEqual(len(ops.posted), 1)
        identifier, body = ops.posted[0]
        self.assertEqual(identifier, "DRE-8000")
        self.assertIn("d" * 40, body)
        self.assertIn(RUN_URL, body)

    def test_a_comment_already_naming_the_candidate_is_not_posted_again(self):
        first = _FakeOps(open_card="DRE-8000")
        mirror_check.file_card(_failed(ROUTING_FAIL), RUN_URL, ops=first)
        said = first.posted[0][1]
        again = _FakeOps(open_card="DRE-8000", comments=[said])
        got = mirror_check.file_card(_failed(ROUTING_FAIL), RUN_URL + "-rerun", ops=again)
        self.assertEqual(again.posted, [])
        self.assertEqual(got["card"], "DRE-8000")
        self.assertFalse(got["owed"])

    def test_linear_failing_leaves_the_card_owed(self):
        for fail in ({"find"}, {"create"}):
            with self.subTest(fail=fail):
                with contextlib.redirect_stderr(io.StringIO()):
                    got = mirror_check.file_card(_failed(ROUTING_FAIL), RUN_URL,
                                                 ops=_FakeOps(fail=fail))
                self.assertTrue(got["owed"])
                self.assertEqual(got["card"], "")

    def test_the_card_cli_warns_once_and_says_card_owed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "mirror_result.json"
            result.write_text(json.dumps(_failed(ROUTING_FAIL)))
            out = Path(tmp) / "gh_output"
            out.write_text("")
            os.environ["GITHUB_OUTPUT"] = str(out)
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), \
                        contextlib.redirect_stderr(io.StringIO()):
                    rc = mirror_check.main(
                        ["card", "--result", str(result), "--run-url", RUN_URL],
                        ops=_FakeOps(fail={"create"}))
            finally:
                os.environ.pop("GITHUB_OUTPUT", None)
            self.assertEqual(rc, 0)
            self.assertIn("card=card owed", out.read_text())
            warnings = [l for l in buf.getvalue().splitlines()
                        if l.startswith("::warning")]
            self.assertEqual(len(warnings), 1)

    def test_the_card_cli_does_nothing_for_a_result_that_did_not_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "mirror_result.json"
            result.write_text(json.dumps(dict(_failed(), status="passed")))
            ops = _FakeOps()
            with contextlib.redirect_stdout(io.StringIO()):
                rc = mirror_check.main(
                    ["card", "--result", str(result), "--run-url", RUN_URL], ops=ops)
            self.assertEqual(rc, 0)
            self.assertEqual(ops.created, [])


# --------------------------------------------------------------------------- #
# 6. The check can actually run (DRE-6622)                                    #
# --------------------------------------------------------------------------- #


def _gh_output(name, written):
    """One `name<<EOF … EOF` or `name=value` block of a GITHUB_OUTPUT file."""
    lines = written.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{name}<<"):
            end = line.partition("<<")[2]
            return lines[i + 1:lines.index(end, i + 1)]
        if line.startswith(f"{name}="):
            return [line.partition("=")[2]]
    return []


class CheckCanRunTest(unittest.TestCase):
    """From 02:26 PT on 2026-10-10 every Promote Channel run held `stable` as
    `mirror-check-blocked` and finished green. Two causes, one behind the
    other: the check's environment had no pytest, because agent-bureau's
    requirements declare none, and with pytest a run of the mirror tests alone
    fell under agent-bureau's coverage floor, so pytest exited 1 with no
    failing test. The stand-in agent-bureau carries both: a requirements file
    with no pytest and a `pyproject.toml` with the floor."""

    def test_the_stand_in_carries_the_floor_and_no_pytest(self):
        trees = _Trees(self)
        settings = (trees.agent_bureau / "pyproject.toml").read_text()
        self.assertIn("--cov=console/backend", settings)
        self.assertIn("--cov-fail-under=90", settings)
        requirements = (FIXTURES / "agent-bureau" / "console/backend/requirements.txt")
        declared = [l for l in requirements.read_text().lower().splitlines()
                    if l.strip() and not l.lstrip().startswith("#")]
        self.assertTrue(declared)
        self.assertFalse([l for l in declared if "pytest" in l], declared)

    # --- cause 1: the check's test tools ---------------------------------- #

    def test_discover_adds_this_repos_pinned_test_tools_after_agent_bureaus(self):
        trees = _Trees(self)
        out = trees.agent_bureau.parent / "gh_output"
        out.write_text("")
        os.environ["GITHUB_OUTPUT"] = str(out)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = mirror_check.main([
                    "discover", "--agent-bureau", str(trees.agent_bureau),
                    "--prefix", "agent-bureau"])
        finally:
            os.environ.pop("GITHUB_OUTPUT", None)
        self.assertEqual(rc, 0)
        self.assertEqual(_gh_output("requirements", out.read_text()), [
            "agent-bureau/console/backend/requirements.txt",
            "requirements-dev.txt",
        ])
        pins = (ROOT / "requirements-dev.txt").read_text().splitlines()
        for tool in ("pytest", "pytest-cov", "pytest-asyncio"):
            with self.subTest(tool=tool):
                self.assertTrue(
                    [p for p in pins if re.fullmatch(rf"{tool}==\d+(\.\d+)*", p)],
                    f"requirements-dev.txt holds no exact pin for {tool}")

    def test_an_interpreter_with_no_pytest_blocks_and_says_so(self):
        trees = _Trees(self)
        venv = trees.agent_bureau.parent / "no-pytest-venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                       check=True, capture_output=True)
        python = venv / "bin" / "python3"
        result = trees.check(python=str(python))
        self.assertEqual(result["status"], mirror_check.MIRROR_BLOCKED)
        self.assertEqual(result["failing"], [])
        self.assertTrue(result["why"].startswith(
            "pytest does not start in the check's environment"), result["why"])
        self.assertIn("No module named pytest", result["why"])

    # --- cause 2: agent-bureau's coverage floor --------------------------- #

    def test_every_mirror_test_passing_is_a_pass_under_the_floor(self):
        trees = _Trees(self, candidate="bureau-pipeline-old")
        result = trees.check()
        self.assertEqual(result["why"], "")
        self.assertEqual(result["status"], mirror_check.MIRROR_PASSED)
        self.assertEqual(result["failing"], [])
        self.assertEqual(result["tests"], 2)

    def test_a_candidate_breaking_a_mirror_still_refuses_under_the_floor(self):
        """Both runs carry `--no-cov`: the candidate run, and the re-read
        against `stable`, where every test passes and the floor alone would
        otherwise answer `blocked`."""
        trees = _Trees(self)
        result = trees.check()
        self.assertEqual(result["why"], "")
        self.assertEqual(result["status"], mirror_check.MIRROR_FAILED)
        failing = {f["test"].split("::")[0]: f["mirrors"] for f in result["failing"]}
        self.assertEqual(failing, {ROUTING_TEST: [VERDICTS], RELAY_TEST: [VERDICTS]})
        self.assertEqual(result["already_red"], [])


# --------------------------------------------------------------------------- #
# 7. Two runs that are alike, and a title Linear accepts (DRE-6657)           #
# --------------------------------------------------------------------------- #

#: The 23 files the failing tests mirrored on Promote Channel run 38072065598
#: (candidate e47e565, `stable` at 9c5b7d2). Named in full they made a
#: 716-character title, and Linear's `issueCreate` takes 255 at most.
RUN_38072065598_MIRRORS = [
    ".github/actions/tdd-commit-check/action.yml",
    ".github/workflows/agent-fix.yml",
    ".github/workflows/qa-review.yml",
    ".github/workflows/reconcile.yml",
    ".github/workflows/release-train.yml",
    ".github/workflows/self-groomer.yml",
    ".gitignore",
    "config/linear-identities.json",
    "config/models.yaml",
    "config/pipeline-acts.json",
    "scripts/bureau_read.py",
    "scripts/check_tdd_commits.py",
    "scripts/console_receipt.py",
    "scripts/epic_cap.py",
    "scripts/groomer.py",
    "scripts/model_fallback.py",
    "scripts/plan_critic.py",
    "scripts/planning_escalation.py",
    "scripts/promote_channel.py",
    "scripts/release_train.py",
    "scripts/reviewer_environment.py",
    "scripts/split_ledger.py",
    "tests/fixtures/act-receipt-bodies.json",
]

#: Passes, and on import leaves a plain module in `sys.modules` where a package
#: of that name sits on the path — the shape of run 38072065598's 1238
#: `'jwt' is not a package` errors.
_LEAVES_A_STUB = (
    "import sys, types\n"
    "BUREAU_PIPELINE_DIR = 'read, and leaves a stub behind'\n"
    "sys.modules['mirrorstub'] = types.ModuleType('mirrorstub')\n"
    "def test_passes():\n"
    "    pass\n"
)

#: Passes alone, and errors only after the file above ran in the same process.
_READS_THE_STUB = (
    "BUREAU_PIPELINE_DIR = 'read'\n"
    "def test_imports_a_submodule():\n"
    "    import mirrorstub.algorithms\n"
)


class LikeForLikeReReadTest(unittest.TestCase):
    """Run 38072065598 ran 38 files on the candidate and re-read only the 22
    that held a failure on `stable`. A failure the run itself caused — one
    file's leftovers breaking another's — showed only on the candidate side,
    and was laid at a candidate that could not have caused it."""

    def _polluted(self):
        trees = _Trees(self, candidate="bureau-pipeline-old")
        trees.write("cloud/x/mirrorstub/__init__.py", "")
        trees.write("cloud/x/mirrorstub/algorithms.py", "")
        trees.write("cloud/x/test_a_leaves_a_stub.py", _LEAVES_A_STUB)
        trees.write("cloud/x/test_b_reads_the_stub.py", _READS_THE_STUB)
        return trees

    def test_the_second_file_passes_on_its_own(self):
        """The stand-in is honest: the second file is red only beside the
        first, never because of anything either checkout holds."""
        trees = self._polluted()
        (trees.agent_bureau / "cloud/x/test_a_leaves_a_stub.py").unlink()
        result = trees.check()
        self.assertEqual(result["status"], mirror_check.MIRROR_PASSED)
        self.assertEqual(result["already_red"], [])

    def test_a_failure_the_run_itself_causes_is_already_red_on_stable(self):
        trees = self._polluted()
        result = trees.check()
        self.assertEqual(result["why"], "")
        self.assertEqual(result["failing"], [])
        red = {f["test"] for f in result["already_red"]}
        self.assertEqual(
            red, {"cloud/x/test_b_reads_the_stub.py::test_imports_a_submodule"})
        self.assertEqual(result["status"], mirror_check.MIRROR_PASSED)

    def test_the_stable_run_gets_the_whole_suite(self):
        """DRE-6670: the re-read is per suite — every file of a suite that
        held a failure, in the same order, and nothing of a suite that held
        none."""
        trees = _Trees(self)
        trees.write("cloud/x/test_more.py", "BUREAU_PIPELINE_DIR = 'read'\n")
        cloud = [RELAY_TEST, "cloud/x/test_more.py"]
        calls = []

        def fake_run_pytest(root, targets, *, python, timeout):
            calls.append(list(targets))
            red = RELAY_TEST in targets
            return {"results": {f"{RELAY_TEST}::test_mirror": "failed"} if red else {},
                    "totals": _totals(failed=1) if red else _totals(passed=1)}

        with mock.patch.object(mirror_check, "run_pytest", fake_run_pytest):
            trees.check()
        self.assertEqual(calls, [cloud, [ROUTING_TEST], cloud])


class TitleCapTest(unittest.TestCase):
    def _result(self, names):
        return _failed(*({"test": f"t/test_{i}.py::test_x", "mirrors": [name]}
                         for i, name in enumerate(names)))

    def test_the_cap_is_inside_linears_limit(self):
        self.assertLessEqual(mirror_check.TITLE_MAX, 200)

    def test_run_38072065598s_23_files_make_a_title_inside_the_cap(self):
        title = mirror_check.card_title(self._result(RUN_38072065598_MIRRORS))
        self.assertLessEqual(len(title), mirror_check.TITLE_MAX)
        self.assertTrue(title.startswith("agent-bureau: "), title)
        match = re.search(r" and (\d+) more$", title)
        self.assertIsNotNone(match, title)
        named = len(RUN_38072065598_MIRRORS) - int(match.group(1))
        self.assertGreater(named, 0, title)
        for name in sorted(RUN_38072065598_MIRRORS)[:named]:
            self.assertIn(name, title)

    def test_the_capped_title_is_the_same_whatever_order_the_tests_failed_in(self):
        one = mirror_check.card_title(self._result(RUN_38072065598_MIRRORS))
        two = mirror_check.card_title(self._result(reversed(RUN_38072065598_MIRRORS)))
        self.assertEqual(one, two)

    def test_a_list_that_fits_is_written_as_before(self):
        names = RUN_38072065598_MIRRORS[:3]
        title = mirror_check.card_title(self._result(names))
        self.assertEqual(title, "agent-bureau: mirrors of "
                                + ", ".join(sorted(names))
                                + " must follow bureau-pipeline")

    def test_the_capped_title_is_a_card_the_create_seam_accepts(self):
        title = mirror_check.card_title(self._result(RUN_38072065598_MIRRORS))
        self.assertIsNone(validate_card.repo_title_mismatch(
            title, mirror_check.card_labels()))


class SelfHostingFixtureTest(unittest.TestCase):
    """The stand-in is bureau-pipeline's own test tree too: collected by this
    repo's suite, every mirror stand-in must SKIP rather than fail, because no
    bureau-pipeline checkout sits beside it here."""

    def test_the_stand_in_tests_do_not_read_this_repo(self):
        for rel in (ROUTING_TEST, RELAY_TEST):
            text = (FIXTURES / "agent-bureau" / rel).read_text()
            self.assertTrue(re.search(r"skipif", text), rel)

    def test_no_pytest_settings_sit_where_this_repos_pytest_would_read_them(self):
        """pytest's rootdir search walks up from every file it is handed. CI
        hands each part its test files by name, so a settings file inside the
        stand-in became this repo's own config for any part naming one of the
        stand-in's tests — and its coverage floor failed that part (DRE-6622,
        the first CI run of this change)."""
        names = ("pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")
        found = [p.relative_to(FIXTURES).as_posix() for p in FIXTURES.rglob("*")
                 if p.name in names]
        self.assertEqual(found, [])


# --------------------------------------------------------------------------- #
# 8. One suite at a time, judged like for like (DRE-6670)                     #
# --------------------------------------------------------------------------- #

#: Laid over the stand-in where a test needs the relay-stubs-the-console shape:
#: a relay file that replaces `standin_tokens` at import, and a console file
#: whose fixture needs the real package. Run 38072065598's 1238 `'jwt' is not a
#: package` errors, at the size of a fixture.
CROSS_TALK = FIXTURES / "agent-bureau-cross-talk"
STUB_TEST = "cloud/relay/test_token_stub_mirror.py"
READER_TEST = "console/backend/tests/test_token_reader_mirror.py"
READER_NODE = f"{READER_TEST}::test_the_console_signs_with_the_real_module"

#: The line the card and the run summary carry per suite — the contract.
SUITE_LINE = re.compile(
    r"^(?:- )?suite (?P<name>\S+): candidate \d+ passed, \d+ failed, \d+ errors, "
    r"\d+ skipped · (?:stable \d+ passed, \d+ failed, \d+ errors, \d+ skipped"
    r"|stable not re-read: nothing failed on the candidate)$")


def _totals(passed=0, failed=0, errors=0, skipped=0):
    return {"passed": passed, "failed": failed, "errors": errors,
            "skipped": skipped}


def _suite(result, name):
    found = [s for s in result.get("suites") or [] if s["name"] == name]
    if not found:
        raise AssertionError(f"no suite {name!r} in {result.get('suites')!r}")
    return found[0]


class SuiteTest(unittest.TestCase):
    """A suite is the first component of a test's root-relative path, read off
    the discovered files and nothing else, and each runs in a pytest process
    of its own."""

    def test_a_suites_name_is_the_first_component_of_its_path(self):
        for path, name in (("cloud/relay/test_lambda_function.py", "cloud"),
                           ("console/backend/tests/test_console_actor.py", "console"),
                           ("test_at_the_root.py", ".")):
            with self.subTest(path=path):
                self.assertEqual(mirror_check.suite_of(path), name)

    def test_the_suites_come_from_the_discovered_files_alone(self):
        files = ["test_root.py", "console/backend/tests/test_c.py",
                 "cloud/relay/test_b.py", "cloud/relay/test_a.py"]
        got = mirror_check.suites(files)
        self.assertEqual(list(got), [".", "cloud", "console"])
        self.assertEqual(got, {
            ".": ["test_root.py"],
            "cloud": ["cloud/relay/test_a.py", "cloud/relay/test_b.py"],
            "console": ["console/backend/tests/test_c.py"],
        })

    def test_the_check_runs_one_pytest_process_per_suite(self):
        """agent-bureau's own workflow layout is never read: a workflow that
        runs everything in one job changes nothing here."""
        trees = _Trees(self, candidate="bureau-pipeline-old")
        trees.write("test_root_mirror.py", (
            "BUREAU_PIPELINE_DIR = 'read'\n"
            "def test_at_the_root():\n"
            "    pass\n"))
        trees.write(".github/workflows/ci.yml", (
            "jobs:\n  everything:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: pytest cloud console test_root_mirror.py\n"))
        calls = []
        real = mirror_check.run_pytest

        def recording(root, targets, *, python, timeout):
            calls.append(list(targets))
            return real(root, targets, python=python, timeout=timeout)

        with mock.patch.object(mirror_check, "run_pytest", recording):
            result = trees.check()
        self.assertEqual(calls, [["test_root_mirror.py"], [RELAY_TEST], [ROUTING_TEST]])
        self.assertEqual(result["status"], mirror_check.MIRROR_PASSED)
        self.assertEqual([s["name"] for s in result["suites"]], [".", "cloud", "console"])
        self.assertEqual(_suite(result, "cloud")["files"], [RELAY_TEST])
        self.assertEqual(_suite(result, "console")["files"], [ROUTING_TEST])


class CrossTalkTest(unittest.TestCase):
    """Two suites, one of which replaces at import a module the other needs —
    the relay and the console on run 38072065598."""

    def test_the_stand_in_really_crosses_in_one_process(self):
        """Honesty first: in one process the console test errors at setup,
        and that is an error, not a failure."""
        trees = _Trees(self, candidate="bureau-pipeline-old", overlay=CROSS_TALK)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            run = mirror_check.run_pytest(trees.agent_bureau, [STUB_TEST, READER_TEST],
                                          python=sys.executable, timeout=120)
        self.assertEqual(run["results"][READER_NODE], "error")
        self.assertEqual(run["totals"], _totals(passed=1, errors=1))

    def test_a_stub_one_suite_leaves_never_reaches_another(self):
        """The candidate changes nothing these tests mirror, so the check
        passes, and the console test passes outright — it is not even
        `already red on stable`."""
        trees = _Trees(self, candidate="bureau-pipeline-old", overlay=CROSS_TALK)
        result = trees.check()
        self.assertEqual(result["why"], "")
        self.assertEqual(result["status"], mirror_check.MIRROR_PASSED)
        self.assertEqual(result["failing"], [])
        self.assertEqual(result["already_red"], [])
        console = _suite(result, "console")
        self.assertEqual(console["status"], mirror_check.MIRROR_PASSED)
        self.assertEqual(console["files"], [READER_TEST, ROUTING_TEST])
        self.assertEqual(console["totals"]["candidate"], _totals(passed=2))
        cloud = _suite(result, "cloud")
        self.assertEqual(cloud["files"], [RELAY_TEST, STUB_TEST])
        self.assertEqual(cloud["totals"]["candidate"], _totals(passed=2))


class LikeForLikeVerdictTest(unittest.TestCase):
    def test_a_fail_on_the_candidate_and_a_pass_on_stable_refuses(self):
        trees = _Trees(self, overlay=CROSS_TALK)
        result = trees.check()
        self.assertEqual(result["status"], mirror_check.MIRROR_FAILED)
        failing = {f["test"].split("::")[0]: f["mirrors"] for f in result["failing"]}
        self.assertEqual(failing, {ROUTING_TEST: [VERDICTS], RELAY_TEST: [VERDICTS]})
        for name in ("cloud", "console"):
            with self.subTest(suite=name):
                suite = _suite(result, name)
                self.assertEqual(suite["status"], mirror_check.MIRROR_FAILED)
                self.assertTrue(suite["re_read"])
                self.assertEqual(suite["totals"]["candidate"], _totals(passed=1, failed=1))
                self.assertEqual(suite["totals"]["stable"], _totals(passed=2))

    def test_a_failure_on_both_identical_runs_is_already_red_and_refuses_nothing(self):
        trees = _Trees(self, stable="bureau-pipeline-new", overlay=CROSS_TALK)
        result = trees.check()
        self.assertEqual(result["status"], mirror_check.MIRROR_PASSED)
        self.assertEqual(result["failing"], [])
        red = {f["test"].split("::")[0] for f in result["already_red"]}
        self.assertEqual(red, {ROUTING_TEST, RELAY_TEST})
        for name in ("cloud", "console"):
            with self.subTest(suite=name):
                suite = _suite(result, name)
                self.assertEqual(suite["status"], mirror_check.MIRROR_PASSED)
                self.assertEqual(suite["totals"]["candidate"], suite["totals"]["stable"])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mirror_check.main([
                "run", "--agent-bureau", str(trees.agent_bureau),
                "--stable", str(trees.stable), "--candidate", CANDIDATE,
                "--out", str(trees.agent_bureau.parent / "r.json")]), 0)
        self.assertIn(f"mirror check: already red on stable: {ROUTING_TEST}",
                      out.getvalue())

    def test_a_suite_with_no_failure_is_not_re_read_and_says_so(self):
        trees = _Trees(self, overlay=CROSS_TALK)
        (trees.agent_bureau / RELAY_TEST).unlink()
        calls = []
        real = mirror_check.run_pytest

        def recording(root, targets, *, python, timeout):
            calls.append(list(targets))
            return real(root, targets, python=python, timeout=timeout)

        with mock.patch.object(mirror_check, "run_pytest", recording):
            result = trees.check()
        self.assertEqual(calls, [[STUB_TEST], [READER_TEST, ROUTING_TEST],
                                 [READER_TEST, ROUTING_TEST]])
        cloud = _suite(result, "cloud")
        self.assertFalse(cloud["re_read"])
        self.assertIsNone(cloud["totals"]["stable"])
        self.assertEqual(
            mirror_check.suite_line(cloud),
            "suite cloud: candidate 1 passed, 0 failed, 0 errors, 0 skipped "
            "· stable not re-read: nothing failed on the candidate")


class TotalsTest(unittest.TestCase):
    def test_each_suite_carries_both_runs_four_counts_and_errors_apart(self):
        trees = _Trees(self)
        trees.write("console/backend/tests/test_setup_errors_mirror.py", (
            "import pytest\n"
            "BUREAU_PIPELINE_DIR = 'read'\n"
            "@pytest.fixture\n"
            "def broken():\n"
            "    raise RuntimeError('setup breaks')\n"
            "def test_passes():\n"
            "    pass\n"
            "def test_errors_at_setup(broken):\n"
            "    pass\n"
            "@pytest.mark.skip(reason='not here')\n"
            "def test_skipped():\n"
            "    pass\n"))
        out = trees.agent_bureau.parent / "mirror_result.json"
        with contextlib.redirect_stdout(io.StringIO()):
            mirror_check.main([
                "run", "--agent-bureau", str(trees.agent_bureau),
                "--stable", str(trees.stable), "--candidate", CANDIDATE,
                "--stable-sha", STABLE, "--agent-bureau-sha", AGENT_BUREAU,
                "--out", str(out)])
        result = json.loads(out.read_text())
        console = _suite(result, "console")
        self.assertEqual(console["totals"], {
            "candidate": _totals(passed=1, failed=1, errors=1, skipped=1),
            "stable": _totals(passed=2, failed=0, errors=1, skipped=1),
        })
        cloud = _suite(result, "cloud")
        self.assertEqual(cloud["totals"], {
            "candidate": _totals(failed=1), "stable": _totals(passed=1)})
        # The setup error is red on both identical runs: already red, never
        # the candidate's.
        red = {f["test"] for f in result["already_red"]}
        self.assertEqual(red, {"console/backend/tests/test_setup_errors_mirror.py"
                               "::test_errors_at_setup"})


class BlockedSuiteTest(unittest.TestCase):
    """A suite its environment cannot run is `blocked`, never `failed`; the
    check is blocked; the other suites still run and are still reported."""

    def _run_cli(self, trees, *extra):
        out = trees.agent_bureau.parent / "mirror_result.json"
        summary = trees.agent_bureau.parent / "summary.md"
        os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
        try:
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                mirror_check.main([
                    "run", "--agent-bureau", str(trees.agent_bureau),
                    "--stable", str(trees.stable), "--candidate", CANDIDATE,
                    "--stable-sha", STABLE, "--agent-bureau-sha", AGENT_BUREAU,
                    "--out", str(out), *extra])
        finally:
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
        return json.loads(out.read_text()), summary.read_text(), stdout.getvalue()

    def _assert_infra_blocked_first(self, result, summary, stdout, *words):
        self.assertEqual(result["status"], mirror_check.MIRROR_BLOCKED)
        self.assertEqual(result["failing"], [])
        self.assertTrue(result["why"].startswith("suite infra: "), result["why"])
        infra = result["suites"][0]
        self.assertEqual(infra["name"], "infra")
        self.assertEqual(infra["status"], mirror_check.MIRROR_BLOCKED)
        for word in words:
            self.assertIn(word, infra["why"])
        self.assertEqual([s["name"] for s in result["suites"][1:]], ["cloud", "console"])
        for name in ("cloud", "console"):
            self.assertIsNotNone(_suite(result, name)["totals"]["candidate"], name)
        for text in (summary, stdout):
            lines = [l for l in text.splitlines() if "suite " in l]
            self.assertTrue(lines and "suite infra: blocked — " in lines[0], text)
            named = {SUITE_LINE.match(l.removeprefix("mirror check: ")).group("name")
                     for l in lines[1:]
                     if SUITE_LINE.match(l.removeprefix("mirror check: "))}
            self.assertEqual(named, {"cloud", "console"}, text)

    def test_a_suite_pytest_cannot_run_is_blocked_and_the_others_still_run(self):
        trees = _Trees(self)
        trees.write("infra/conftest.py",
                    "raise RuntimeError('the infra environment is missing')\n")
        trees.write("infra/test_infra_mirror.py", (
            "BUREAU_PIPELINE_DIR = 'read'\n"
            "def test_infra():\n"
            "    pass\n"))
        result, summary, stdout = self._run_cli(trees)
        self._assert_infra_blocked_first(result, summary, stdout, "exit")
        console = _suite(result, "console")
        self.assertEqual(console["status"], mirror_check.MIRROR_FAILED)
        self.assertEqual(console["totals"]["stable"], _totals(passed=1))

    def test_a_suite_that_writes_no_report_is_blocked(self):
        trees = _Trees(self)
        trees.write("infra/test_infra_mirror.py", (
            "import os\n"
            "BUREAU_PIPELINE_DIR = 'read'\n"
            "def test_dies():\n"
            "    os._exit(1)\n"))
        result, summary, stdout = self._run_cli(trees)
        self._assert_infra_blocked_first(result, summary, stdout, "no report")

    def test_a_suite_past_the_budget_is_blocked(self):
        trees = _Trees(self, candidate="bureau-pipeline-old")
        trees.write("infra/test_infra_mirror.py", (
            "import time\n"
            "BUREAU_PIPELINE_DIR = 'read'\n"
            "def test_slow():\n"
            "    time.sleep(60)\n"))
        result, summary, stdout = self._run_cli(trees, "--budget-seconds", "12")
        self._assert_infra_blocked_first(result, summary, stdout, "time limit")
        self.assertEqual(_suite(result, "cloud")["status"], mirror_check.MIRROR_PASSED)


class SideBySideTest(unittest.TestCase):
    CONSOLE = {"name": "console", "files": [ROUTING_TEST], "status": "passed",
               "re_read": True,
               "totals": {"candidate": _totals(1212, 0, 1238, 4),
                          "stable": _totals(1212, 0, 1238, 4)}}
    CLOUD = {"name": "cloud", "files": [RELAY_TEST], "status": "passed",
             "re_read": False,
             "totals": {"candidate": _totals(3), "stable": None}}

    def test_the_line_has_exactly_the_contract_shape(self):
        self.assertEqual(
            mirror_check.suite_line(self.CONSOLE),
            "suite console: candidate 1212 passed, 0 failed, 1238 errors, 4 skipped "
            "· stable 1212 passed, 0 failed, 1238 errors, 4 skipped")
        self.assertEqual(
            mirror_check.suite_line(self.CLOUD),
            "suite cloud: candidate 3 passed, 0 failed, 0 errors, 0 skipped "
            "· stable not re-read: nothing failed on the candidate")

    def test_the_refusal_card_carries_one_line_per_suite_under_the_failing_tests(self):
        result = dict(_failed(ROUTING_FAIL), suites=[self.CLOUD, self.CONSOLE])
        body = mirror_check.card_body(result, RUN_URL)
        lines = body.splitlines()
        row = next(i for i, l in enumerate(lines) if ROUTING_FAIL["test"] in l)
        for suite in (self.CLOUD, self.CONSOLE):
            line = mirror_check.suite_line(suite)
            at = [i for i, l in enumerate(lines) if l.endswith(line)]
            self.assertEqual(len(at), 1, line)
            self.assertGreater(at[0], row)
        self.assertIsNone(linear_ops.body_problem(body))
        self.assertEqual(validate_card.missing(body, mirror_check.card_labels()), [])

    def test_the_run_summary_carries_one_line_per_suite_under_the_duration(self):
        trees = _Trees(self, overlay=CROSS_TALK)
        (trees.agent_bureau / RELAY_TEST).unlink()
        out = trees.agent_bureau.parent / "mirror_result.json"
        summary = trees.agent_bureau.parent / "summary.md"
        os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
        try:
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                mirror_check.main([
                    "run", "--agent-bureau", str(trees.agent_bureau),
                    "--stable", str(trees.stable), "--candidate", CANDIDATE,
                    "--out", str(out)])
        finally:
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
        result = json.loads(out.read_text())
        wanted = [mirror_check.suite_line(_suite(result, n)) for n in ("cloud", "console")]
        self.assertIn("stable not re-read: nothing failed on the candidate", wanted[0])
        self.assertTrue(wanted[1].endswith("stable 2 passed, 0 failed, 0 errors, 0 skipped"),
                        wanted[1])
        written = [l for l in summary.read_text().splitlines() if l.strip()]
        self.assertTrue(written[0].startswith("⏱ mirror check: "), written)
        self.assertEqual([l.removeprefix("- ") for l in written[1:3]], wanted)
        printed = stdout.getvalue().splitlines()
        self.assertTrue(printed[0].startswith("mirror check: failed — "), printed)
        self.assertEqual([l.removeprefix("mirror check: ") for l in printed[1:3]], wanted)


class CrossTalkFixtureHereTest(unittest.TestCase):
    """Collected by this repo's own suite, the cross-talk stand-ins find no
    bureau-pipeline checkout: they stub nothing and skip."""

    def test_they_skip_and_stub_nothing_here(self):
        env = {k: v for k, v in os.environ.items() if k != mirror_check.PIPELINE_ENV}
        probe = (
            "import sys, pytest\n"
            f"code = pytest.main(['-q', '-p', 'no:cacheprovider', '-rs', "
            f"{str(CROSS_TALK / STUB_TEST)!r}, {str(CROSS_TALK / READER_TEST)!r}])\n"
            "print('STUBBED' if 'standin_tokens' in sys.modules else 'CLEAN')\n"
            "sys.exit(int(code))\n")
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run([sys.executable, "-c", probe], cwd=tmp, env=env,
                                  capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("2 skipped", proc.stdout)
        self.assertIn("CLEAN", proc.stdout)
        for rel in (STUB_TEST, READER_TEST):
            self.assertTrue(re.search(r"skipif", (CROSS_TALK / rel).read_text()), rel)


if __name__ == "__main__":
    unittest.main()
