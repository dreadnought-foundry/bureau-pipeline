"""The Report step opens the repair pull request itself (DRE-6525).

On 2026-10-09 at 16:23 PT the Report step of Red-Main Repair run 38003951327
found no pull request and no escalation note for
`repair/DRE-6511-bcb36adf6037` and failed the run. The branch was on the
remote, one commit ahead of `main`, carrying the right fix; the operator opened
pull request #883 from it by hand 75 minutes later.

`scripts/repair_finish.py finish` finishes that job. What these tests pin, one
class per acceptance criterion:

  * the replay of 2026-10-09: exactly one `gh pr create` and exactly one card
    comment, with the title, body, outputs and exit code the contract names;
  * a pull request of ANY state on the branch is the answer — nothing is
    opened, nothing is posted (the idempotency the re-run relies on);
  * no branch, nothing ahead, and every unreadable read write nothing and
    exit 2, so the Report step falls through to its own error line;
  * the cardless `repair/<sha>` shape and a Linear failure;
  * the title rule and the output block;
  * the Report step's own script, run in bash with both helpers stubbed.

Run: cd bureau-pipeline && python3 -m pytest tests/test_repair_finish.py -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "red-main-repair.yml")
sys.path.insert(0, SCRIPTS)

import repair_finish  # noqa: E402
import step_shell  # noqa: E402

REPO = "dreadnought-foundry/bureau-pipeline"
BRANCH = "repair/DRE-6511-bcb36adf6037"
CARD = "DRE-6511"
CARD_URL = ("https://linear.app/dreadnoughtfoundry/issue/DRE-6511/"
            "bureau-pipeline-repair-agent-background-wait")
RUN_URL = ("https://github.com/dreadnought-foundry/bureau-pipeline/actions/"
           "runs/38001512530")
WF_NAME = "Tests"
FIX_SHA = "64a5a2ac7d1e0f3b9c2a8e4f6b1d0c9a7e5f3b21"
FIX_SUBJECT = "keep the repair agent's checks in the foreground"
PR_URL = "https://github.com/dreadnought-foundry/bureau-pipeline/pull/883"
REF = {"ref": f"refs/heads/{BRANCH}", "object": {"sha": FIX_SHA}}
COMPARE = {
    "ahead_by": 1,
    "commits": [{
        "sha": FIX_SHA,
        "commit": {"message": f"{FIX_SUBJECT}\n\nThe agent's own reason.\n"},
    }],
}


class FakeOps:
    """The GitHub and Linear seams, recorded. A read set to an Exception
    instance raises it; every write is kept so a test can count it."""

    def __init__(self, *, ref=REF, compare=COMPARE, pulls=(),
                 created_url=PR_URL, comment_error=None, create_error=None,
                 pulls_after_create_error=None):
        self.ref = ref
        self.compare_payload = compare
        self.pulls = list(pulls) if isinstance(pulls, (list, tuple)) else pulls
        self.created_url = created_url
        self.comment_error = comment_error
        self.create_error = create_error
        self.pulls_after_create_error = pulls_after_create_error
        self.created = []
        self.comments = []

    @staticmethod
    def _answer(value):
        if isinstance(value, Exception):
            raise value
        return value

    def branch_ref(self, repo, branch):
        return self._answer(self.ref)

    def compare(self, repo, base, head):
        return self._answer(self.compare_payload)

    def pulls_for_head(self, repo, branch):
        if self.create_error is not None and self.created:
            return self.pulls_after_create_error
        return self._answer(self.pulls)

    def create_pr(self, repo, *, base, head, title, body):
        self.created.append({"repo": repo, "base": base, "head": head,
                             "title": title, "body": body})
        if self.create_error is not None:
            raise self.create_error
        return self.created_url

    def cmd_comment(self, identifier, body):
        if self.comment_error is not None:
            raise self.comment_error
        self.comments.append((identifier, body))


def parse_outputs(text: str) -> dict:
    """Read a `$GITHUB_OUTPUT` block the way the runner does: `key=value`, or
    `key<<DELIM` … `DELIM`."""
    found: dict = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line:
            continue
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            key, delim = line.split("<<", 1)
            value = []
            while lines[i] != delim:
                value.append(lines[i])
                i += 1
            i += 1
            found[key] = "\n".join(value)
        else:
            key, value = line.split("=", 1)
            if key in found:
                raise AssertionError(f"output {key!r} written twice: {text!r}")
            found[key] = value
    return found


def run_cli(ops, *, branch=BRANCH, card_url=CARD_URL, extra=()):
    argv = ["finish", "--repo", REPO, "--branch", branch,
            "--default-branch", "main", "--failed-run-url", RUN_URL,
            "--workflow-name", WF_NAME]
    if card_url is not None:
        argv += ["--card-url", card_url]
    argv += list(extra)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = repair_finish.main(argv, ops=ops)
    return code, parse_outputs(out.getvalue()), err.getvalue()


class ReplayTest(unittest.TestCase):
    """2026-10-09: the agent pushed the fix and stopped."""

    def setUp(self):
        self.ops = FakeOps()
        self.code, self.out, self.err = run_cli(self.ops)

    def test_opens_exactly_one_pull_request_from_the_branch_into_main(self):
        self.assertEqual(len(self.ops.created), 1)
        created = self.ops.created[0]
        self.assertEqual(created["repo"], REPO)
        self.assertEqual(created["base"], "main")
        self.assertEqual(created["head"], BRANCH)
        self.assertTrue(created["title"].startswith("fix(red-main):"))
        self.assertTrue(created["title"].endswith(FIX_SUBJECT))
        self.assertEqual(created["title"], f"fix(red-main): {FIX_SUBJECT}")

    def test_the_body_names_the_run_the_card_the_commit_and_who_opened_it(self):
        body = self.ops.created[0]["body"]
        self.assertIn(f"Failed run: {RUN_URL}", body)
        self.assertIn(f"Card: {CARD_URL}", body)
        self.assertIn("Commits:", body)
        self.assertIn(f"{FIX_SHA[:12]} {FIX_SUBJECT}", body)
        self.assertIn(repair_finish.OPENED_LINE, body)
        self.assertTrue(repair_finish.OPENED_LINE.startswith(
            "Opened by the Red-Main Repair workflow:"))
        order = [body.index(f"Failed run: {RUN_URL}"),
                 body.index(f"Card: {CARD_URL}"),
                 body.index("Commits:"),
                 body.index(repair_finish.OPENED_LINE)]
        self.assertEqual(order, sorted(order))

    def test_the_body_carries_no_verdict_shaped_text(self):
        body = self.ops.created[0]["body"]
        for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(marker, body)
            self.assertNotIn(marker, self.ops.comments[0][1])

    def test_one_comment_on_the_card_carrying_the_pull_request(self):
        self.assertEqual(len(self.ops.comments), 1)
        card, body = self.ops.comments[0]
        self.assertEqual(card, CARD)
        self.assertTrue(body.startswith(repair_finish.FINISH_MARKER))
        self.assertIn(PR_URL, body)

    def test_outputs_and_exit(self):
        self.assertEqual(self.out, {"finish": "opened", "pr_url": PR_URL,
                                    "card": CARD})
        self.assertEqual(self.code, 0)

    def test_the_marker_is_the_contract(self):
        self.assertEqual(repair_finish.FINISH_MARKER,
                         "📬 Pull request opened by the repair workflow")


class AlreadyOpenTest(unittest.TestCase):
    """A pull request of any state on the branch is the answer."""

    def test_any_state_is_already_open(self):
        for state in ("OPEN", "CLOSED", "MERGED"):
            with self.subTest(state=state):
                found = "https://github.com/x/y/pull/12"
                ops = FakeOps(pulls=[{"number": 12, "url": found,
                                      "state": state}])
                code, out, _ = run_cli(ops)
                self.assertEqual(ops.created, [])
                self.assertEqual(ops.comments, [])
                self.assertEqual(out["finish"], "already-open")
                self.assertEqual(out["pr_url"], found)
                self.assertEqual(code, 0)

    def test_the_newest_pull_request_is_reported(self):
        ops = FakeOps(pulls=[
            {"number": 12, "url": "https://github.com/x/y/pull/12", "state": "CLOSED"},
            {"number": 40, "url": "https://github.com/x/y/pull/40", "state": "OPEN"},
        ])
        _, out, _ = run_cli(ops)
        self.assertEqual(out["pr_url"], "https://github.com/x/y/pull/40")

    def test_a_second_run_opens_nothing_and_posts_nothing(self):
        ops = FakeOps()
        run_cli(ops)
        ops.pulls = [{"number": 883, "url": PR_URL, "state": "OPEN"}]
        code, out, _ = run_cli(ops)
        self.assertEqual(len(ops.created), 1)
        self.assertEqual(len(ops.comments), 1)
        self.assertEqual(out["finish"], "already-open")
        self.assertEqual(code, 0)


class NothingToOpenTest(unittest.TestCase):
    """Every answer but the two good ones writes nothing and exits 2."""

    def assert_nothing(self, ops, finish):
        code, out, _ = run_cli(ops)
        self.assertEqual(ops.created, [])
        self.assertEqual(ops.comments, [])
        self.assertEqual(out["finish"], finish)
        self.assertEqual(out["pr_url"], "")
        self.assertEqual(code, 2)

    def test_no_branch(self):
        self.assert_nothing(FakeOps(ref=False, compare=None), "no-branch")

    def test_nothing_ahead(self):
        self.assert_nothing(FakeOps(compare={"ahead_by": 0, "commits": []}),
                            "nothing-ahead")

    def test_unreadable_reads(self):
        cases = {
            "ref None": {"ref": None},
            "ref raising": {"ref": RuntimeError("HTTP 502")},
            "compare None": {"compare": None},
            "compare raising": {"compare": RuntimeError("HTTP 502")},
            "pulls None": {"pulls": None},
            "pulls raising": {"pulls": RuntimeError("HTTP 502")},
            "ahead_by missing": {"compare": {"commits": COMPARE["commits"]}},
            "ahead_by a string": {"compare": {**COMPARE, "ahead_by": "1"}},
            "ahead_by a bool": {"compare": {**COMPARE, "ahead_by": True}},
            "ahead with no commits": {"compare": {"ahead_by": 1, "commits": []}},
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                self.assert_nothing(FakeOps(**kwargs), "unreadable")

    def test_decide_finish_is_pure(self):
        decide = repair_finish.decide_finish
        self.assertEqual(decide(REF, COMPARE, []), repair_finish.OPENED_NEEDED)
        self.assertEqual(
            decide(REF, COMPARE, [{"number": 1, "url": "u", "state": "MERGED"}]),
            repair_finish.ALREADY_OPEN)
        self.assertEqual(decide(False, None, []), repair_finish.NO_BRANCH)
        self.assertEqual(decide(REF, {"ahead_by": 0, "commits": []}, []),
                         repair_finish.NOTHING_AHEAD)
        self.assertEqual(decide(None, COMPARE, []), repair_finish.UNREADABLE)
        self.assertEqual(decide(REF, COMPARE, None), repair_finish.UNREADABLE)

    def test_a_create_that_fails_falls_through(self):
        ops = FakeOps(create_error=RuntimeError("gh pr create failed rc=1"),
                      pulls_after_create_error=[])
        code, out, err = run_cli(ops)
        self.assertEqual(ops.comments, [])
        self.assertEqual(out["finish"], "unreadable")
        self.assertEqual(code, 2)
        self.assertIn("gh pr create failed", err)

    def test_a_create_that_lost_a_race_reports_the_winner(self):
        ops = FakeOps(create_error=RuntimeError("a pull request already exists"),
                      pulls_after_create_error=[
                          {"number": 900, "url": PR_URL, "state": "OPEN"}])
        code, out, _ = run_cli(ops)
        self.assertEqual(ops.comments, [])
        self.assertEqual(out["finish"], "already-open")
        self.assertEqual(out["pr_url"], PR_URL)
        self.assertEqual(code, 0)


class CardlessAndLinearTest(unittest.TestCase):

    def test_cardless_branch_says_the_card_is_owed_and_posts_nothing(self):
        sha = "bcb36adf6037" + "0" * 28
        ops = FakeOps()
        code, out, _ = run_cli(ops, branch=f"repair/{sha}", card_url="")
        self.assertEqual(len(ops.created), 1)
        body = ops.created[0]["body"]
        self.assertIn("The repair card is owed for this repair: Linear could "
                      "not be reached when it started.", body)
        self.assertNotIn("Card:", body)
        self.assertEqual(ops.comments, [])
        self.assertEqual(out, {"finish": "opened", "pr_url": PR_URL, "card": ""})
        self.assertEqual(code, 0)

    def test_card_from_the_branch_when_no_url_is_given(self):
        ops = FakeOps()
        run_cli(ops, card_url=None)
        self.assertIn(f"Card: {CARD}", ops.created[0]["body"])

    def test_second_attempt_branch_names_its_card(self):
        ops = FakeOps()
        _, out, _ = run_cli(ops, branch=f"{BRANCH}-2", card_url=None)
        self.assertEqual(out["card"], CARD)
        self.assertEqual(ops.comments[0][0], CARD)

    def test_a_linear_failure_never_fails_the_open(self):
        ops = FakeOps(comment_error=RuntimeError("Linear 503"))
        code, out, err = run_cli(ops)
        self.assertEqual(len(ops.created), 1)
        self.assertEqual(out["finish"], "opened")
        self.assertEqual(out["pr_url"], PR_URL)
        self.assertEqual(code, 0)
        self.assertIn("Linear 503", err)


class TitleAndOutputTest(unittest.TestCase):

    def test_the_newest_commit_names_the_pull_request(self):
        compare = {"ahead_by": 2, "commits": [
            {"sha": "a" * 40, "commit": {"message": "test: the red one"}},
            {"sha": "b" * 40, "commit": {"message": "the fix itself\n\nwhy"}},
        ]}
        ops = FakeOps(compare=compare)
        run_cli(ops)
        created = ops.created[0]
        self.assertEqual(created["title"], "fix(red-main): the fix itself")
        self.assertIn(f"{'a' * 12} test: the red one", created["body"])
        self.assertIn(f"{'b' * 12} the fix itself", created["body"])

    def test_no_double_prefix(self):
        compare = {"ahead_by": 1, "commits": [
            {"sha": FIX_SHA,
             "commit": {"message": "fix(red-main): already prefixed"}}]}
        ops = FakeOps(compare=compare)
        run_cli(ops)
        self.assertEqual(ops.created[0]["title"],
                         "fix(red-main): already prefixed")

    def test_a_newline_cannot_break_the_output_block(self):
        compare = {"ahead_by": 1, "commits": [
            {"sha": FIX_SHA,
             "commit": {"message": "subject\nfinish=unreadable\ncard=DRE-1"}}]}
        ops = FakeOps(compare=compare,
                      created_url=f"{PR_URL}\nfinish=unreadable")
        code, out, _ = run_cli(ops)
        self.assertEqual(out["finish"], "opened")
        self.assertEqual(out["card"], CARD)
        self.assertEqual(out["pr_url"], f"{PR_URL}\nfinish=unreadable")
        self.assertEqual(code, 0)

    def test_outputs_go_through_the_one_writer(self):
        import github_output

        seen = []
        real = github_output.render

        def spy(pairs):
            pairs = list(pairs)
            seen.append(pairs)
            return real(pairs)

        github_output.render = spy
        try:
            run_cli(FakeOps())
        finally:
            github_output.render = real
        self.assertEqual([key for key, _ in seen[0]],
                         ["finish", "pr_url", "card"])


# --------------------------------------------------------------------------- #
# The Report step's own script, run in bash with both helpers stubbed          #
# --------------------------------------------------------------------------- #

_CARD_PR_STUB = """\
import os, sys
open(os.environ["CALLS"], "a").write("card_pr " + " ".join(sys.argv[1:]) + "\\n")
sys.stdout.write(os.environ.get("FAKE_CARD_PR", ""))
"""

_FINISH_STUB = """\
import os, sys
open(os.environ["CALLS"], "a").write("repair_finish " + " ".join(sys.argv[1:]) + "\\n")
code = int(os.environ["FAKE_FINISH_RC"])
finish = "opened" if code == 0 else "no-branch"
url = os.environ.get("FAKE_FINISH_URL", "") if code == 0 else ""
sys.stdout.write(f"finish={finish}\\npr_url={url}\\ncard=DRE-6511\\n")
sys.exit(code)
"""


def report_step() -> dict:
    with open(WORKFLOW) as fh:
        steps = yaml.safe_load(fh)["jobs"]["repair"]["steps"]
    return next(s for s in steps if s.get("name") == "Report")


class ReportStepShellTest(unittest.TestCase):
    """The step's `run:` body, executed as GitHub executes it (`bash -e`)."""

    def run_step(self, *, card_pr="", finish_rc=0):
        with tempfile.TemporaryDirectory() as tmp:
            scripts = os.path.join(tmp, ".bureau-pipeline", "scripts")
            os.makedirs(scripts)
            for name, text in (("card_pr.py", _CARD_PR_STUB),
                               ("repair_finish.py", _FINISH_STUB)):
                with open(os.path.join(scripts, name), "w") as fh:
                    fh.write(text)
            # The escalation note is read from an absolute path; point it into
            # the sandbox so a stray file on this machine cannot steer the test.
            body = step_shell.step_shell(report_step()).replace(
                "/tmp/repair-escalation.txt",
                os.path.join(tmp, "repair-escalation.txt"))
            calls = os.path.join(tmp, "calls.txt")
            open(calls, "w").close()
            env = {
                **os.environ,
                "CALLS": calls,
                "FAKE_CARD_PR": card_pr,
                "FAKE_FINISH_RC": str(finish_rc),
                "FAKE_FINISH_URL": PR_URL,
                "BRANCH": BRANCH,
                "RUN_URL": RUN_URL,
                "WF_NAME": WF_NAME,
                "CARD_URL": CARD_URL,
                "DEFAULT_BRANCH": "main",
                "GITHUB_REPOSITORY": REPO,
            }
            done = subprocess.run(["bash", "-e", "-c", body], cwd=tmp, env=env,
                                  capture_output=True, text=True, check=False)
            with open(calls) as fh:
                return done, fh.read()

    def test_helper_exit_0_ends_the_step_green_with_the_url(self):
        done, calls = self.run_step(finish_rc=0)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn(PR_URL, done.stdout)
        self.assertNotIn("::error::", done.stdout)
        self.assertIn("repair_finish finish", calls)
        self.assertIn(f"--branch {BRANCH}", calls)
        self.assertIn(f"--failed-run-url {RUN_URL}", calls)
        self.assertIn(f"--card-url {CARD_URL}", calls)
        self.assertIn(f"--repo {REPO}", calls)
        self.assertIn("--default-branch main", calls)
        self.assertIn(f"--workflow-name {WF_NAME}", calls)

    def test_helper_exit_2_keeps_todays_error_line(self):
        done, calls = self.run_step(finish_rc=2)
        self.assertEqual(done.returncode, 1)
        self.assertIn(
            "::error::repair agent produced neither a PR nor an escalation — "
            "failing for medic visibility", done.stdout)
        self.assertIn("repair_finish finish", calls)

    def test_a_pull_request_the_agent_opened_is_the_first_answer(self):
        done, calls = self.run_step(card_pr=f"OPEN\t{PR_URL}", finish_rc=2)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn(PR_URL, done.stdout)
        self.assertIn("card_pr find", calls)
        self.assertNotIn("repair_finish", calls)


class ActRegistryTest(unittest.TestCase):
    """The new card comment is declared, not an act."""

    def test_the_comment_is_declared_not_an_act(self):
        with open(os.path.join(ROOT, "config", "pipeline-acts.json")) as fh:
            registry = json.load(fh)
        rows = [row for row in registry["unconverted"]
                if row.get("file") == "scripts/repair_finish.py"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "not-an-act")
        self.assertIn("finish_note(", rows[0]["anchor"])

    def test_the_receipt_guard_is_green(self):
        done = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "check_act_receipts.py")],
            cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
