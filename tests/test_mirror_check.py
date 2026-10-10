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
    `<agent-bureau>/.bureau-pipeline` AND in `BUREAU_PIPELINE_DIR`;
  * the re-read — each failure run again against the current `stable`, so a
    test red on both is agent-bureau's own red and refuses nothing;
  * the mirror map — the bureau-pipeline file a failing test mirrors, read off
    the test's own string literals, never guessed;
  * the card — one per breakage, found again by its exact title.

The stand-in trees under tests/fixtures/mirror-check/ are modeled on the
2026-10-09 change, because the real agent-bureau as it stood that day is not
readable from a pull request.
"""

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402
import mirror_check  # noqa: E402
import validate_card  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "mirror-check"

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
                 stable="bureau-pipeline-old"):
        self._tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.agent_bureau = base / "agent-bureau"
        shutil.copytree(FIXTURES / "agent-bureau", self.agent_bureau)
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
            "assert 'operator-step' not in open(P).read()\n"
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
        verdicts = (trees.candidate / VERDICTS).read_text()
        self.assertIn("operator-step", verdicts)
        self.assertNotIn("operator-step", (trees.stable / VERDICTS).read_text())

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


class SelfHostingFixtureTest(unittest.TestCase):
    """The stand-in is bureau-pipeline's own test tree too: collected by this
    repo's suite, every mirror stand-in must SKIP rather than fail, because no
    bureau-pipeline checkout sits beside it here."""

    def test_the_stand_in_tests_do_not_read_this_repo(self):
        for rel in (ROUTING_TEST, RELAY_TEST):
            text = (FIXTURES / "agent-bureau" / rel).read_text()
            self.assertTrue(re.search(r"skipif", text), rel)


if __name__ == "__main__":
    unittest.main()
