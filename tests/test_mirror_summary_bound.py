"""RED-first tests for the bounded mirror summary (DRE-6683).

Promote Channel run 38076381376 moved `stable` to 0a1a515, recorded the
release, and then went red on its last reporting step: `Say what happened`
could not start, `Argument list too long`. Its `MIRROR` environment value was
the mirror check's one-line summary, and that line named every one of the
1,238 agent-bureau tests already red on `stable` — 207,749 bytes, where Linux
refuses any single argument or environment string over 131,072. No line of
the step ran, so its log held no error, and attempt 2 found the channel moved
and went green over the top of it.

So:

  * `promote_channel.mirror_summary()` is bounded. It states both counts
    exactly, names a handful of examples, says how many more, and names where
    the full lists are: `mirror_result.json`, kept as the run's artifact.
  * No step in `promote-channel.yml` takes an unbounded output through `env:`.
  * A reporting step that cannot run does not turn a promotion red; the
    workflow says so, and a step after it says the report was not written.
"""

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import promote_channel  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote-channel.yml"

SHA = "0a1a51564753ca27f0abbd370df3648fd8dc98fc"
STABLE = "9c5b7d2" + "0" * 33
AB_SHA = "c96e058" + "0" * 33
OPERATOR = "sidmohan"
RED_COUNT = 1238

#: The card's bound: under 2,000 bytes, whatever the result holds.
BOUND = 2000


def _entries(n, *, prefix="already"):
    """`n` entries shaped like run 38076381376's: real-length node ids, most
    with no mirrored file read."""
    return [
        {"test": (f"console/backend/tests/test_{prefix}_red_module_{i:04d}.py"
                  f"::TestTheMirrorMatchesBureauPipeline"
                  f"::test_the_vocabulary_mirror_matches_number_{i:04d}"),
         "mirrors": (["config/routing-verdicts.json"] if i % 7 == 0 else [])}
        for i in range(n)
    ]


def _result(status, *, failing=0, already_red=0, why=""):
    return {
        "status": status, "why": why, "candidate": SHA, "stable": STABLE,
        "agent_bureau_sha": AB_SHA, "tests": 1500, "seconds": 600,
        "failing": _entries(failing, prefix="failing"),
        "already_red": _entries(already_red),
    }


def _size(text):
    return len(text.encode("utf-8"))


class MirrorSummaryBoundTest(unittest.TestCase):
    def test_the_unbounded_line_was_over_the_kernels_limit(self):
        """The fixture reproduces the failure: every name joined is more than
        Linux takes in one environment string, so a test under the bound is
        not passing by having a small input."""
        everything = "; ".join(e["test"] for e in _entries(RED_COUNT))
        self.assertGreater(_size(everything), 131072)

    def test_1238_already_red_is_bounded_and_both_counts_are_exact(self):
        line = promote_channel.mirror_summary(
            _result("passed", already_red=RED_COUNT))
        self.assertLess(_size(line), BOUND)
        self.assertIn("0 failing", line)
        self.assertIn(f"{RED_COUNT} already red on stable", line)

    def test_1238_failing_is_bounded_and_both_counts_are_exact(self):
        line = promote_channel.mirror_summary(
            _result("failed", failing=RED_COUNT))
        self.assertLess(_size(line), BOUND)
        self.assertIn(f"{RED_COUNT} failing", line)
        self.assertIn("0 already red on stable", line)
        self.assertTrue(line.startswith(promote_channel.OUTCOME_MIRROR_FAILED))

    def test_both_lists_at_once_stay_under_the_bound(self):
        line = promote_channel.mirror_summary(
            _result("failed", failing=RED_COUNT, already_red=RED_COUNT))
        self.assertLess(_size(line), BOUND)
        self.assertIn(f"{RED_COUNT} failing", line)
        self.assertIn(f"{RED_COUNT} already red on stable", line)

    def test_it_names_a_handful_of_examples_and_how_many_more(self):
        red = _entries(RED_COUNT)
        line = promote_channel.mirror_summary(
            _result("passed", already_red=RED_COUNT))
        shown = promote_channel.MIRROR_EXAMPLES
        self.assertGreater(shown, 0)
        for entry in red[:shown]:
            self.assertIn(entry["test"], line)
        self.assertNotIn(red[shown]["test"], line)
        self.assertIn(f"and {RED_COUNT - shown} more", line)

    def test_a_short_list_is_named_in_full_with_no_more(self):
        line = promote_channel.mirror_summary(_result("failed", failing=1))
        self.assertIn(_entries(1, prefix="failing")[0]["test"], line)
        self.assertIn("config/routing-verdicts.json", line)
        self.assertNotIn("more", line)

    def test_it_names_where_the_full_lists_are(self):
        line = promote_channel.mirror_summary(
            _result("passed", already_red=RED_COUNT))
        self.assertIn(promote_channel.MIRROR_RESULT_FILE, line)
        self.assertIn(promote_channel.MIRROR_ARTIFACT, line)
        self.assertEqual(promote_channel.MIRROR_RESULT_FILE, "mirror_result.json")

    def test_a_clean_pass_names_no_list(self):
        line = promote_channel.mirror_summary(_result("passed"))
        self.assertIn("1500 agent-bureau tests", line)
        self.assertIn(AB_SHA[:7], line)
        self.assertNotIn(promote_channel.MIRROR_RESULT_FILE, line)

    def test_one_enormous_test_name_cannot_break_the_bound(self):
        result = _result("passed", already_red=1)
        result["already_red"][0]["test"] = "tests/test_x.py::" + "y" * 300_000
        result["already_red"][0]["mirrors"] = ["z" * 300_000]
        line = promote_channel.mirror_summary(result)
        self.assertLess(_size(line), BOUND)
        self.assertIn("1 already red on stable", line)

    def test_a_long_blocked_reason_cannot_break_the_bound(self):
        line = promote_channel.mirror_summary(
            _result("blocked", why="pytest could not run: " + "x" * 300_000))
        self.assertLess(_size(line), BOUND)
        self.assertTrue(line.startswith(promote_channel.OUTCOME_MIRROR_BLOCKED))

    def test_the_forced_warning_carries_the_bounded_line(self):
        base = promote_channel.evaluate(
            {"statuses": [{"context": promote_channel.STATUS_CONTEXT,
                           "state": "failure"}]},
            SHA, ancestry="ahead", manual=True, force=True, trunk="behind",
            actor=OPERATOR, reason="who=sid testing")
        result = _result("failed", failing=RED_COUNT, already_red=RED_COUNT)
        d = promote_channel.with_mirror(base, result, sha=SHA, force=True,
                                        actor=OPERATOR, reason="who=sid testing")
        self.assertEqual(d.outcome, promote_channel.OUTCOME_BY_HAND_FORCED)
        self.assertIn(promote_channel.mirror_summary(result), d.reason)
        self.assertLess(_size(d.reason), 2 * BOUND)


def _decide(result, extra=()):
    """The `Decide` step's own path: `promote_channel.main` over a result
    file, reading back what it wrote to `$GITHUB_OUTPUT`."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        statuses = tmp / "s.json"
        statuses.write_text(json.dumps({"statuses": [
            {"context": promote_channel.STATUS_CONTEXT, "state": "success"}]}))
        path = tmp / "mirror_result.json"
        path.write_text(json.dumps(result))
        out = tmp / "gh_output"
        out.write_text("")
        old = os.environ.get("GITHUB_OUTPUT")
        os.environ["GITHUB_OUTPUT"] = str(out)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = promote_channel.main(
                    ["--sha", SHA, "--statuses-file", str(statuses),
                     "--ancestry", "ahead", "--mirror-result", str(path),
                     *extra])
        finally:
            if old is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = old
        assert rc == 0
        return dict(line.split("=", 1)
                    for line in out.read_text().splitlines())


class DecideOutputBoundTest(unittest.TestCase):
    def test_the_mirror_output_line_is_under_the_bound(self):
        for name, result in (
            ("already red", _result("passed", already_red=RED_COUNT)),
            ("failing", _result("failed", failing=RED_COUNT)),
            ("both", _result("failed", failing=RED_COUNT, already_red=RED_COUNT)),
        ):
            with self.subTest(result=name):
                outputs = _decide(result)
                self.assertLess(_size(f"mirror={outputs['mirror']}"), BOUND)

    def test_the_reason_output_that_quotes_it_is_bounded_too(self):
        for name, result in (
            ("already red", _result("passed", already_red=RED_COUNT)),
            ("failing", _result("failed", failing=RED_COUNT, already_red=RED_COUNT)),
        ):
            with self.subTest(result=name):
                outputs = _decide(result)
                self.assertLess(_size(f"reason={outputs['reason']}"), 2 * BOUND)

    def test_a_promotion_over_1238_already_red_still_promotes(self):
        outputs = _decide(_result("passed", already_red=RED_COUNT))
        self.assertEqual(outputs["promote"], "true")
        self.assertEqual(outputs["outcome"], promote_channel.OUTCOME_PROMOTING)
        self.assertIn(f"{RED_COUNT} already red on stable", outputs["mirror"])


# --------------------------------------------------------------------------- #
# The workflow                                                                 #
# --------------------------------------------------------------------------- #

_OUTPUT_REF = re.compile(r"steps\.([\w-]+)\.outputs\.([\w-]+)")

#: Every output of the steps that read or write the mirror result, and why it
#: may reach an `env:`. COVERED outputs quote the mirror summary and are safe
#: only because of the bound proved above; SCALAR ones are a word, a number
#: or a card id. A new output of these steps is in neither set, and fails the
#: test below until someone says which it is.
MIRROR_STEPS = ("mirror", "decide", "mirror_card", "discover", "keep_mirror")
COVERED = {("decide", "mirror"), ("decide", "reason")}
SCALAR = {("decide", "promote"), ("decide", "outcome"),
          ("mirror_card", "card"), ("discover", "count"),
          ("discover", "python_version")}


class WorkflowEnvTest(unittest.TestCase):
    def setUp(self):
        self.steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["promote"]["steps"]

    def _step(self, name):
        for step in self.steps:
            if step.get("name") == name:
                return step
        self.fail(f"no step named {name!r}")

    def test_no_env_takes_an_unbounded_mirror_output(self):
        seen = set()
        for step in self.steps:
            for key, value in (step.get("env") or {}).items():
                for step_id, output in _OUTPUT_REF.findall(str(value)):
                    if step_id not in MIRROR_STEPS:
                        continue
                    seen.add((step_id, output))
                    self.assertIn(
                        (step_id, output), COVERED | SCALAR,
                        f"{step.get('name')!r} takes steps.{step_id}.outputs."
                        f"{output} as {key}: an output of a mirror step that "
                        f"no bound covers — read mirror_result.json instead")
        # The guard is about something: the summary does reach an env.
        self.assertIn(("decide", "mirror"), seen)

    def test_the_check_step_writes_no_output_at_all(self):
        """Its per-test lists go to the result file and the log, never to
        `$GITHUB_OUTPUT`, where a step would pick them up."""
        import mirror_check

        source = (ROOT / "scripts" / "mirror_check.py").read_text()
        body = source.split("def _cmd_run", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("_output(", body)
        self.assertNotIn("GITHUB_OUTPUT", body)
        self.assertTrue(callable(mirror_check.main))

    def test_the_full_result_is_kept_where_the_summary_says(self):
        run = next(i for i, s in enumerate(self.steps)
                   if "mirror_check.py run" in str(s.get("run", "")))
        keep = [(i, s) for i, s in enumerate(self.steps)
                if "upload-artifact" in str(s.get("uses", ""))
                and promote_channel.MIRROR_RESULT_FILE in str(s.get("with", {}))]
        self.assertEqual(len(keep), 1, "one step uploads mirror_result.json")
        i, step = keep[0]
        self.assertGreater(i, run)
        self.assertEqual(step["with"]["name"], promote_channel.MIRROR_ARTIFACT)
        self.assertEqual(step["with"]["path"], promote_channel.MIRROR_RESULT_FILE)
        self.assertEqual(step["with"].get("if-no-files-found"), "ignore")
        self.assertTrue(step.get("continue-on-error"))
        self.assertIn("steps.proof.outputs.promote == 'true'", str(step.get("if")))
        self.assertRegex(step["uses"], r"@[0-9a-f]{40}\b", "pinned by sha")

    def test_a_report_that_cannot_run_does_not_fail_a_promotion(self):
        """The decision is stated, not a side effect: `Say what happened` is
        reporting, so its failure does not turn the run red — and the step
        after it says the report was not written, from scalars alone."""
        names = [s.get("name") for s in self.steps]
        say = self._step("Say what happened")
        self.assertTrue(say.get("continue-on-error"))
        say_id = say.get("id")
        self.assertTrue(say_id, "the report step needs an id to be checked")
        after = [s for s in self.steps[names.index("Say what happened") + 1:]
                 if f"steps.{say_id}.outcome == 'failure'" in str(s.get("if", ""))]
        self.assertEqual(len(after), 1)
        self.assertIn("::warning title=Run summary not written::", after[0]["run"])
        for value in (after[0].get("env") or {}).values():
            for ref in _OUTPUT_REF.findall(str(value)):
                self.assertNotIn(ref, COVERED, "the fallback must not need "
                                 "the text the report could not take")
        # The blocked-check failure is still the job's last word (DRE-6622).
        self.assertIn("mirror-check-blocked", str(self.steps[-1].get("if", "")))

    def test_the_receipt_promoted_block_names_where_the_full_list_is(self):
        run = self._step("Say what happened")["run"]
        self.assertIn(promote_channel.MIRROR_ARTIFACT, run)


class SayWhatHappenedScenarioTest(unittest.TestCase):
    """Run 38076381376 replayed: the `Decide` outputs over 1,238 already-red
    tests, fed to the real `Say what happened` script through its real env."""

    def test_a_promotion_over_1238_already_red_says_it_moved_the_channel(self):
        outputs = _decide(_result("passed", already_red=RED_COUNT))
        steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["promote"]["steps"]
        say = next(s for s in steps if s.get("name") == "Say what happened")
        self.assertNotIn("${{", say["run"], "an expression this test does not expand")
        wanted = {
            "OUTCOME": "steps.decide.outputs.outcome",
            "REASON": "steps.decide.outputs.reason",
            "PROMOTED": "steps.decide.outputs.promote",
            "MIRROR": "steps.decide.outputs.mirror",
            "MOVER": "github.actor",
            "CANDIDATE": "steps.resolve.outputs.candidate",
        }
        for key, expr in wanted.items():
            self.assertIn(expr, say["env"][key], key)
        env = {key: "" for key in say["env"]}
        env.update({
            "OUTCOME": outputs["outcome"],
            "REASON": outputs["reason"],
            "PROMOTED": outputs["promote"],
            "MIRROR": outputs["mirror"],
            "MOVER": "agent-bureau-qa-bot[bot]",
            "CANDIDATE": SHA,
            "CHANNEL_HEAD": STABLE,
            "EVENT": "workflow_run",
            "BRANCH": "main",
            "HARNESS_RUN": "https://github.com/x/y/actions/runs/38076381376",
        })
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            summary.write_text("")
            env["GITHUB_STEP_SUMMARY"] = str(summary)
            env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
            proc = subprocess.run(["bash", "-e", "-c", say["run"]], env=env,
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            text = summary.read_text()
        self.assertIn(f"moved stable to {SHA[:7]}", text)
        self.assertIn("## Promoted", text)
        self.assertIn(SHA, text)
        self.assertIn("agent-bureau-qa-bot[bot]", text)
        self.assertIn(f"- mirror check: {outputs['mirror']}", text)
        self.assertIn(f"{RED_COUNT} already red on stable", text)
        self.assertIn(promote_channel.MIRROR_ARTIFACT, text)


class DocsTest(unittest.TestCase):
    def test_the_docs_say_the_summary_is_bounded_and_where_the_list_is(self):
        flat = " ".join((ROOT / "docs" / "self-hosting.md").read_text().split())
        self.assertIn("DRE-6683", flat)
        self.assertIn(promote_channel.MIRROR_ARTIFACT, flat)


if __name__ == "__main__":
    unittest.main()
