"""RED-first tests for DRE-3091 — a new act cannot merge until the console
knows it.

TWICE IN TWELVE HOURS a bureau-pipeline card added an act to
`config/pipeline-acts.json`, merged green, and broke every open PR in
agent-bureau: DRE-3042's `conflict-sweep-crashed` (2026-09-03 22:26 PT,
DRE-3081) and DRE-3084's `refuted-finding` (2026-09-04 09:13 PT, DRE-3090).
The consumer's guard — `test_every_act_the_pipeline_declares_is_known_to_the
_console`, shipped with DRE-2825 — is RIGHT, and it lives only in the
consumer. So it fires in the wrong repo, after the fact, and holds unrelated
PRs hostage until someone files the one-line console card.

`standards/engineering.md` states the rule this breaks: a shared contract is
either one module both sides import, or **every consumer is updated in the
same change**. Across two repositories neither holds, so the producer grows
the guard the consumer already has.

WHAT THESE TESTS PIN, one section per acceptance criterion:

  1. A fixture registry with an act absent from a fixture `receipts.py` fails
     the guard, NAMING the act and the one-line console fix. The same
     registry against a console that carries the act passes — the answer
     changes with the tree, which is the only thing separating a guard from a
     `return True`.
  2. Unread is a SKIP, never a pass. No token, no network, a console file
     with no `ACTS` in it, an `ACTS` that yields no strings — every one of
     them reports a visible reason and is not `ok`. "The console knows
     nothing about any act" is a conclusion this guard is never allowed to
     reach by accident.
  3. The CI job that runs it asserts it did not skip, and the assertion is
     itself exercised here against a skipped, an empty and a clean report.
  4. The critic's context for a bureau-pipeline PR that touches the registry
     carries the guard's result, so a reviewer sees "this act is unknown to
     the console" before approving.
  5. The sequencing rule is written down beside the registry: console first,
     pipeline second.

Run: cd bureau-pipeline && python3 -m pytest tests/test_pipeline_acts_consumers.py -v
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import check_act_consumers as guard  # noqa: E402
import review_card_context as rcc  # noqa: E402

REGISTRY = os.path.join(ROOT, "config", "pipeline-acts.json")
GUARD_SCRIPT = os.path.join(SCRIPTS, "check_act_consumers.py")
RCC_SCRIPT = os.path.join(SCRIPTS, "review_card_context.py")
TESTS_YML = os.path.join(ROOT, ".github", "workflows", "tests.yml")
QA_REVIEW = os.path.join(ROOT, ".github", "workflows", "qa-review.yml")
DOC = os.path.join(ROOT, "docs", "pipeline-acts.md")
CONFIG_README = os.path.join(ROOT, "config", "README.md")

#: The one file a PR must touch for any of this to be its business.
REGISTRY_PATH = "config/pipeline-acts.json"


def _live() -> dict:
    with open(REGISTRY, encoding="utf-8") as fh:
        return json.load(fh)


def _doc(rows) -> dict:
    """The live registry with its act list replaced by `rows`.

    The `console` block is kept exactly as shipped: these tests are about the
    guard's answer, and swapping the consumer spec for an invented one would
    only prove the invention parses.
    """
    doc = copy.deepcopy(_live())
    doc["acts"] = [
        {"name": name, "tag": tag, "kind": kind} for name, tag, kind in rows
    ]
    return doc


#: A console that carries one act. Written as the console writes it — a
#: mapping of tag to kind — but the guard reads STRINGS out of the assignment
#: rather than a shape, because the console's literal shape is the console's
#: to choose and a producer-side guard that pins it would break on a
#: refactor that changed nothing about what the console knows.
CONSOLE_KNOWS_ONE = textwrap.dedent(
    '''
    """The console's receipt vocabulary."""

    ACTS = {
        "known-thing": "refusal",
    }

    OTHER = {"decoy-thing": "hold"}
    '''
)

CONSOLE_KNOWS_BOTH = textwrap.dedent(
    '''
    ACTS = {
        "known-thing": "refusal",
        "new-thing": "recovery",
    }
    '''
)

ONE_KNOWN = ("known-act", "known-thing", "refusal")
ONE_UNKNOWN = ("new-act", "new-thing", "recovery")


# ── 1. an act the console does not carry fails the guard ────────────────────
class TestAnActTheConsoleDoesNotCarryFailsTheGuard(unittest.TestCase):
    def test_the_unknown_act_is_found_and_named(self):
        report = guard.check(doc=_doc([ONE_KNOWN, ONE_UNKNOWN]), source=CONSOLE_KNOWS_ONE)
        self.assertFalse(report.skipped, report.text())
        self.assertFalse(report.ok, report.text())
        self.assertEqual(("new-act",), report.unknown)

    def test_the_message_names_the_act_its_tag_and_the_one_line_console_fix(self):
        report = guard.check(doc=_doc([ONE_KNOWN, ONE_UNKNOWN]), source=CONSOLE_KNOWS_ONE)
        text = report.text()
        spec = guard.console_spec(_live())
        for expected in ("new-act", "new-thing", "recovery", spec["path"], spec["symbol"]):
            self.assertIn(expected, text)

    def test_the_same_registry_passes_once_the_console_carries_the_act(self):
        """The answer changes with the tree. A guard whose verdict does not
        move when the console learns the act is a `return True` with a
        docstring."""
        doc = _doc([ONE_KNOWN, ONE_UNKNOWN])
        self.assertFalse(guard.check(doc=doc, source=CONSOLE_KNOWS_ONE).ok)
        after = guard.check(doc=doc, source=CONSOLE_KNOWS_BOTH)
        self.assertTrue(after.ok, after.text())
        self.assertEqual((), after.unknown)

    def test_only_the_ACTS_assignment_counts(self):
        """A tag that appears elsewhere in the console file is not the console
        knowing the act — `OTHER` in the fixture carries `decoy-thing`."""
        report = guard.check(
            doc=_doc([("decoy-act", "decoy-thing", "hold")]), source=CONSOLE_KNOWS_ONE
        )
        self.assertEqual(("decoy-act",), report.unknown)

    def test_an_act_the_console_keys_on_its_name_is_known_too(self):
        """The console owns its own key. Tag or name — either string standing
        inside `ACTS` means the console carries the act, and an act it has
        never heard of carries neither."""
        source = 'ACTS = {"new-act": "recovery"}\n'
        self.assertTrue(guard.check(doc=_doc([ONE_UNKNOWN]), source=source).ok)

    def test_the_registry_declares_where_the_consumer_lives(self):
        spec = guard.console_spec(_live())
        self.assertEqual("dreadnought-foundry/agent-bureau", spec["repo"])
        self.assertEqual("console/backend/receipts.py", spec["path"])
        self.assertEqual("ACTS", spec["symbol"])
        self.assertTrue(spec.get("fix"), "the one-line console fix is data, not a string in code")


# ── 2. unread is a skip with a visible reason, never a pass ─────────────────
class TestUnreadIsASkipNeverAPass(unittest.TestCase):
    def _assert_skipped(self, report, *, because):
        self.assertTrue(report.skipped, report.text())
        self.assertFalse(report.ok, report.text())
        self.assertTrue(report.reason.strip(), "a skip with no reason is a silent pass")
        self.assertIn(because, report.reason)

    def test_no_source_at_all_is_a_skip(self):
        self._assert_skipped(
            guard.check(doc=_doc([ONE_UNKNOWN]), source=None, reason="no console token"),
            because="no console token",
        )

    def test_a_console_file_without_the_symbol_is_a_skip(self):
        self._assert_skipped(
            guard.check(doc=_doc([ONE_UNKNOWN]), source="KINDS = ['refusal']\n"),
            because="ACTS",
        )

    def test_an_ACTS_that_yields_no_strings_is_a_skip_not_an_empty_vocabulary(self):
        """`ACTS = {row["tag"]: row for row in _ROWS}` is a legal console. It
        yields nothing readable, and reading it as "the console carries no
        act" would fail every act at once for a reason that is not true."""
        self._assert_skipped(
            guard.check(doc=_doc([ONE_UNKNOWN]), source="ACTS = _build(_ROWS)\n"),
            because="ACTS",
        )

    def test_an_unparseable_console_is_a_skip(self):
        self._assert_skipped(
            guard.check(doc=_doc([ONE_UNKNOWN]), source="ACTS = {\n"), because="parse"
        )

    def test_the_skip_reason_is_printed_by_the_cli(self):
        out = self._run(["check"], env={})
        self.assertEqual(guard.EXIT_SKIPPED, out.returncode, out.stdout + out.stderr)
        self.assertIn("SKIPPED", out.stdout)
        self.assertIn("BUREAU_CONSOLE_TOKEN", out.stdout)

    def test_the_cli_answers_0_and_1_off_a_real_console_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = os.path.join(tmp, "acts.json")
            console = os.path.join(tmp, "receipts.py")
            with open(console, "w", encoding="utf-8") as fh:
                fh.write(CONSOLE_KNOWS_ONE)
            self._write(registry, _doc([ONE_KNOWN, ONE_UNKNOWN]))
            bad = self._run(
                ["check", "--registry", registry],
                env={"BUREAU_CONSOLE_ACTS_FILE": console},
            )
            self.assertEqual(1, bad.returncode, bad.stdout + bad.stderr)
            self.assertIn("new-act", bad.stdout)

            self._write(registry, _doc([ONE_KNOWN]))
            good = self._run(
                ["check", "--registry", registry],
                env={"BUREAU_CONSOLE_ACTS_FILE": console},
            )
            self.assertEqual(0, good.returncode, good.stdout + good.stderr)

    @staticmethod
    def _write(path, doc):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    @staticmethod
    def _run(args, env):
        environ = {
            k: v
            for k, v in os.environ.items()
            if k not in ("BUREAU_CONSOLE_TOKEN", "BUREAU_CONSOLE_ACTS_FILE")
        }
        environ.update(env)
        return subprocess.run(
            [sys.executable, GUARD_SCRIPT, *args],
            capture_output=True,
            text=True,
            env=environ,
        )


# ── 3. the CI job asserts the guard did not skip ────────────────────────────
def _junit(tests, skipped, body="") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<testsuites><testsuite name="pytest" errors="0" failures="0" '
        f'skipped="{skipped}" tests="{tests}">{body}</testsuite></testsuites>\n'
    )


class TestTheCIJobFailsOnASkip(unittest.TestCase):
    def _assert_ran(self, xml):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.xml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(xml)
            return subprocess.run(
                [sys.executable, GUARD_SCRIPT, "assert-ran", path],
                capture_output=True,
                text=True,
            )

    def test_a_skipped_report_fails(self):
        out = self._assert_ran(_junit(1, 1, '<testcase name="t"><skipped message="no token"/></testcase>'))
        self.assertNotEqual(0, out.returncode)
        self.assertIn("skip", (out.stdout + out.stderr).lower())

    def test_a_report_with_no_tests_fails(self):
        out = self._assert_ran(_junit(0, 0))
        self.assertNotEqual(0, out.returncode)

    def test_a_missing_report_fails(self):
        out = subprocess.run(
            [sys.executable, GUARD_SCRIPT, "assert-ran", "/nonexistent/report.xml"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, out.returncode)

    def test_a_clean_report_passes(self):
        out = self._assert_ran(_junit(2, 0, '<testcase name="t"/>'))
        self.assertEqual(0, out.returncode, out.stdout + out.stderr)

    def test_tests_yml_runs_the_guard_and_then_asserts_it_ran(self):
        with open(TESTS_YML, encoding="utf-8") as fh:
            wf = yaml.safe_load(fh)
        jobs = wf["jobs"]
        job = next(
            (j for j in jobs.values() if "consumer" in (j.get("name") or "").lower()),
            None,
        )
        self.assertIsNotNone(job, "tests.yml declares no act-consumer job")
        steps = job["steps"]
        runs = "\n".join(s.get("run", "") for s in steps)
        self.assertIn("tests/test_pipeline_acts_consumers.py", runs)
        self.assertIn("--junit-xml", runs)
        self.assertIn("assert-ran", runs)
        # Premortem Q2 (DRE-2047/2067): a dependabot-triggered pull_request
        # gets the EMPTY Dependabot secrets store, so the mint below yields
        # nothing and a job that must not skip would be red on every bump.
        self.assertIn("dependabot[bot]", job.get("if", ""))

    def test_the_job_mints_a_token_scoped_to_the_console_repository(self):
        with open(TESTS_YML, encoding="utf-8") as fh:
            wf = yaml.safe_load(fh)
        job = next(
            j for j in wf["jobs"].values() if "consumer" in (j.get("name") or "").lower()
        )
        mint = next(
            (s for s in job["steps"] if "create-github-app-token" in (s.get("uses") or "")),
            None,
        )
        self.assertIsNotNone(mint, "the job never mints a token for the console repo")
        self.assertEqual("agent-bureau", mint["with"]["repositories"])
        self.assertEqual("dreadnought-foundry", mint["with"]["owner"])
        env = "\n".join(
            "\n".join(f"{k}={v}" for k, v in (s.get("env") or {}).items())
            for s in job["steps"]
        )
        self.assertIn("BUREAU_CONSOLE_TOKEN", env)


# ── 4. the critic sees the guard's result on the PR ─────────────────────────
class TestTheCriticSeesTheGuardResult(unittest.TestCase):
    def test_the_block_is_absent_when_there_is_nothing_to_say(self):
        self.assertNotIn(
            "act registry", rcc.build_context("DRE-1", "agent/DRE-1-x", "").lower()
        )

    def test_the_block_reaches_every_pr_shape(self):
        note = "ACT REGISTRY CONSUMER CHECK: new-act is unknown to the console."
        for branch in ("agent/DRE-1-x", "dependabot/npm/x", "repair/x", "whatever"):
            for card in ("DRE-1", ""):
                built = rcc.build_context(card, branch, "body", acts_consumer=note)
                self.assertIn(note, built, f"{branch} / card={card!r}")

    def test_the_cli_takes_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = os.path.join(tmp, "body.txt")
            acts = os.path.join(tmp, "acts.txt")
            with open(body, "w", encoding="utf-8") as fh:
                fh.write("a body")
            with open(acts, "w", encoding="utf-8") as fh:
                fh.write("ACT REGISTRY CONSUMER CHECK: new-act is unknown to the console.")
            out = subprocess.run(
                [
                    sys.executable, RCC_SCRIPT,
                    "--card", "DRE-1",
                    "--branch", "agent/DRE-1-x",
                    "--pr-body-file", body,
                    "--acts-consumer-file", acts,
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(0, out.returncode, out.stderr)
            self.assertIn("unknown to the console", out.stdout)

    def test_context_says_nothing_for_a_pr_that_does_not_touch_the_registry(self):
        self.assertEqual("", guard.context(["scripts/reconcile.py"], doc=_doc([ONE_UNKNOWN])))

    def test_context_reports_the_gap_for_a_pr_that_does(self):
        text = guard.context(
            [REGISTRY_PATH], doc=_doc([ONE_UNKNOWN]), source=CONSOLE_KNOWS_ONE
        )
        self.assertIn("new-act", text)
        self.assertIn(REGISTRY_PATH, text)

    def test_context_reports_a_clean_check_too(self):
        """A receipt that only ever appears when something is wrong teaches a
        reviewer that its absence means the check ran and passed. It does
        not — most of the time it means the check never ran."""
        text = guard.context(
            [REGISTRY_PATH], doc=_doc([ONE_KNOWN]), source=CONSOLE_KNOWS_ONE
        )
        self.assertTrue(text.strip())
        self.assertNotIn("known-act", text)

    def test_context_reports_a_skip_as_a_skip(self):
        text = guard.context(
            [REGISTRY_PATH], doc=_doc([ONE_UNKNOWN]), source=None, reason="no console token"
        )
        self.assertIn("no console token", text)

    def test_qa_review_builds_the_file_and_passes_it_to_the_builder(self):
        with open(QA_REVIEW, encoding="utf-8") as fh:
            wf = yaml.safe_load(fh)
        steps = wf["jobs"]["review"]["steps"]
        runs = "\n".join(s.get("run", "") for s in steps)
        self.assertIn("check_act_consumers.py context", runs)
        self.assertIn("--acts-consumer-file", runs)
        mint = next(
            (
                s for s in steps
                if "create-github-app-token" in (s.get("uses") or "")
                and (s.get("with") or {}).get("repositories") == "agent-bureau"
            ),
            None,
        )
        self.assertIsNotNone(mint, "qa-review never mints a console-scoped token")
        # Best effort BY DESIGN: this is another repository and another
        # installation, and a mint that cannot happen must cost the receipt,
        # never the whole review.
        self.assertTrue(mint.get("continue-on-error"))


# ── 5. the sequencing rule is written down beside the registry ─────────────
class TestTheSequencingRuleIsWrittenDown(unittest.TestCase):
    def test_the_doc_exists_and_states_console_first(self):
        self.assertTrue(os.path.exists(DOC), "docs/pipeline-acts.md is missing")
        with open(DOC, encoding="utf-8") as fh:
            text = fh.read()
        low = text.lower()
        self.assertIn("console-first", low)
        for expected in ("console/backend/receipts.py", "ACTS", REGISTRY_PATH,
                         "check_act_consumers.py", "DRE-3081", "DRE-3090"):
            self.assertIn(expected, text)

    def test_the_config_readme_points_at_it(self):
        with open(CONFIG_README, encoding="utf-8") as fh:
            self.assertIn("docs/pipeline-acts.md", fh.read())


# ── the live guard itself ──────────────────────────────────────────────────
class TestTheLiveConsoleKnowsEveryDeclaredAct(unittest.TestCase):
    """The producer-side twin of the console's own
    `test_every_act_the_pipeline_declares_is_known_to_the_console`.

    It is the ONLY test here that reaches the network, and offline it SKIPS
    with the reason printed — never passes silently. The `act registry
    consumers` job in tests.yml is what turns that skip into a red build.
    """

    def test_every_act_the_pipeline_declares_is_known_to_the_console(self):
        report = guard.check()
        if report.skipped:
            self.skipTest(report.text())
        self.assertTrue(report.ok, report.text())


if __name__ == "__main__":
    unittest.main()
