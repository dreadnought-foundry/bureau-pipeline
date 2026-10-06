"""proof-task.yml — the proof run's own reusable workflow (DRE-5924).

The proof runner (DRE-5921) is NOT a role on `agent-task.yml`, on purpose:
`scripts/proof_and_demo.py` derives the build roles from the roster entries
running on that workflow, and a role there is a role the fleet picks up for a
PROOF card (DRE-3039). So the proof run gets its own workflow, and these tests
LIVE-EXTRACT it (no fixtures, no copies) and pin what makes it a proof run
rather than a build run:

  * it assembles context and selects a model for role `proof`, and pins no
    model id;
  * it holds a GitHub identity that is read-only BY ITS PERMISSIONS — the
    four `permission-*: read` inputs and nothing else — and an AWS session
    only when the caller provides a role, both minted before the agent runs,
    and both written to the step summary in the line the record copies;
  * it writes three lanes and no other (DRE-5925): the park into
    `Green Light` on a press only the CEO can make, out of `Hand-work` or
    `In Review`, after the `🔬 proof-waiting` hold and the question; the
    return to `Hand-work` out of `Green Light` on his signed answer, after the
    `🔬 proof-observed` discharge record and before the agent; and `In Review`
    out of `Hand-work` when the record's pull request is open. There is no
    `Card → In Progress` step, no `report_agent_result.sh` (whose every move
    reads the card out of `In Progress,Todo` and whose dead-run exits requeue,
    park or replan), no `dead_run.py`, and no `state` write;
  * its result step has exactly four exits, the pull request read before the
    escalation — and that, and the return step, are EXECUTED here, against
    stub scripts, rather than grepped, because the order is the contract.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PROOF_TASK = WORKFLOWS / "proof-task.yml"
AGENT_TASK = WORKFLOWS / "agent-task.yml"
RELEASE_TRAIN = WORKFLOWS / "release-train.yml"

ACTION = "anthropics/claude-code-action"
APP_TOKEN = "actions/create-github-app-token"
AWS_CREDS = "aws-actions/configure-aws-credentials"
#: The pin this repo already carries for the App token mint (v3.2.0).
APP_TOKEN_PIN = "bcd2ba49218906704ab6c1aa796996da409d3eb1"

READ_PERMISSIONS = {
    "permission-contents": "read",
    "permission-actions": "read",
    "permission-pull-requests": "read",
    "permission-metadata": "read",
}

GITHUB_SUMMARY = (
    "proof identity: github read — app {slug}, permissions contents:read "
    "actions:read pull-requests:read metadata:read"
)
AWS_NONE = "proof identity: aws — none, PROOF_ROLE_ARN not provided"
AWS_ASSUMED = "proof identity: aws — role assumed as proof-"

RESULT_STEP = "Report proof result to Linear"
RETURN_STEP = "Return from Green Light on the CEO's answer"
NO_RECORD = "🤖 proof run ended with no record"
PARK_RECEIPT = "🙋 The proof run met a press only the CEO can make, and asks:"
RETURN_REASON = "re-run after the CEO's answer"


def _doc() -> dict:
    return yaml.safe_load(PROOF_TASK.read_text())


def _on(doc: dict) -> dict:
    # YAML 1.1 parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _job() -> dict:
    jobs = _doc().get("jobs") or {}
    assert len(jobs) == 1, f"proof-task.yml has one job, found {list(jobs)}"
    return next(iter(jobs.values()))


def _steps() -> list[dict]:
    return _job().get("steps") or []


def _index(name: str) -> int:
    for i, step in enumerate(_steps()):
        if step.get("name") == name:
            return i
    raise AssertionError(f"proof-task.yml has no step named {name!r}")


def _step(name: str) -> dict:
    return _steps()[_index(name)]


def _agent_steps() -> list[tuple[int, dict]]:
    return [
        (i, s) for i, s in enumerate(_steps())
        if str(s.get("uses") or "").split("@")[0] == ACTION
    ]


def _runs() -> str:
    return "\n".join(str(s.get("run") or "") for s in _steps())


class ProofTaskContractTest(unittest.TestCase):
    def test_the_file_exists_and_is_a_reusable_named_for_the_watch_lists(self):
        self.assertTrue(PROOF_TASK.is_file(), "proof-task.yml does not exist")
        doc = _doc()
        self.assertEqual("Proof Task (reusable)", doc.get("name"))
        self.assertEqual({"workflow_call"}, set(_on(doc)),
                         "the reusable is workflow_call only")

    def test_inputs(self):
        inputs = _on(_doc())["workflow_call"].get("inputs") or {}
        self.assertEqual({"pipeline_ref", "linear_identity", "aws_region"}, set(inputs))
        self.assertIs(inputs["pipeline_ref"].get("required"), True)
        self.assertEqual("fleet", inputs["linear_identity"].get("default"))
        self.assertEqual("us-west-2", inputs["aws_region"].get("default"))

    def test_secrets_are_agent_tasks_plus_an_optional_proof_role(self):
        mine = _on(_doc())["workflow_call"].get("secrets") or {}
        theirs = _on(yaml.safe_load(AGENT_TASK.read_text()))["workflow_call"]["secrets"]
        self.assertEqual(set(theirs) | {"PROOF_ROLE_ARN"}, set(mine))
        for name, spec in theirs.items():
            self.assertEqual(spec, mine[name], f"{name} is declared differently")
        self.assertIs(mine["PROOF_ROLE_ARN"].get("required"), False)

    def test_timeout_is_agent_tasks_and_the_runner_is_the_long_chain(self):
        # The timeout is agent-task.yml's; the runner is NOT. Its build lane
        # (DRE-4846) is for the jobs that run a product's suite, and a proof
        # run runs none — so this job reads the long chain every other reusable
        # job reads (tests/test_runs_on_switchable.py).
        theirs = next(iter(yaml.safe_load(AGENT_TASK.read_text())["jobs"].values()))
        self.assertEqual(theirs["timeout-minutes"], _job()["timeout-minutes"])
        self.assertNotIn("BUREAU_CI_RUNS_ON", _job()["runs-on"])
        self.assertEqual(
            "${{ fromJSON(vars.BUREAU_RUNS_ON || '[\"ubuntu-latest\"]') }}",
            _job()["runs-on"])

    def test_both_repositories_are_checked_out(self):
        checkouts = [s for s in _steps()
                     if str(s.get("uses") or "").startswith("actions/checkout@")]
        repos = {(s.get("with") or {}).get("repository") for s in checkouts}
        self.assertEqual({None, "dreadnought-foundry/bureau-pipeline"}, repos)
        pipeline = [s for s in checkouts
                    if (s.get("with") or {}).get("repository")][0]["with"]
        self.assertEqual(".bureau-pipeline", pipeline["path"])
        self.assertIn("inputs.pipeline_ref", pipeline["ref"])


class ProofRoleTest(unittest.TestCase):
    def test_context_is_assembled_for_role_proof(self):
        runs = _runs()
        self.assertRegex(runs, r"assemble_context\.py assemble proof\b")
        self.assertIn("spoken_thread.py people", runs)

    def test_the_model_is_selected_for_role_proof_and_none_is_pinned(self):
        src = PROOF_TASK.read_text()
        self.assertRegex(_runs(), r"model_fallback\.py select proof\b")
        self.assertNotRegex(src, r"--model\s+claude-")
        for _i, step in _agent_steps():
            self.assertIn("--model ${{ steps.model.outputs.model }}",
                          step["with"]["claude_args"])

    def test_the_agent_step_and_its_rate_limit_retries(self):
        agents = _agent_steps()
        self.assertEqual(3, len(agents), "an attempt and two rate-limit retries")
        first = agents[0][1]
        for _i, retry in agents[1:]:
            self.assertEqual(first["with"], retry["with"],
                             "a retry re-runs exactly the attempt GitHub refused")
            self.assertEqual(first.get("env"), retry.get("env"))
        self.assertIn("claude_rate_limit_retry.py decide", _runs())
        self.assertIn("claude_rate_limit_retry.py resolve", _runs())

    def test_the_card_text_is_sanitized_and_fenced(self):
        self.assertIn("sanitize_untrusted.py", _runs())
        prompt = _agent_steps()[0][1]["with"]["prompt"]
        self.assertIn("===== BEGIN UNTRUSTED CARD TEXT =====", prompt)
        self.assertIn("===== END UNTRUSTED CARD TEXT =====", prompt)
        self.assertIn("${{ steps.card.outputs.description }}", prompt)
        self.assertNotIn("client_payload.description", prompt)
        self.assertNotIn("client_payload.title", prompt)

    def test_the_prompt_says_what_the_proof_brief_says(self):
        prompt = _agent_steps()[0][1]["with"]["prompt"]
        for phrase in (
            "briefs/proof.md",
            "GH_TOKEN=$GH_READ_TOKEN gh api",
            "**Files:**",
            "agent/${{ github.event.client_payload.identifier }}-proof-record",
            "/tmp/agent-escalation.txt",
        ):
            self.assertIn(phrase, prompt)
        # Not a build prompt: no TDD process, no split hand-back.
        self.assertNotIn("agent-handback.txt", PROOF_TASK.read_text())
        self.assertNotIn("TDD", prompt)

    def test_no_turn_rung_and_no_role_from_labels(self):
        src = PROOF_TASK.read_text()
        self.assertNotIn("turn_budget.py", src)
        self.assertNotIn("steps.gate.outputs.role", src)
        self.assertNotIn("validate_card.py", src)
        for _i, step in _agent_steps():
            self.assertRegex(step["with"]["claude_args"], r"--max-turns 400\b")

    def test_the_model_attempt_receipt_carries_its_run_tail(self):
        """The sibling run-state reader parses `Run: …/actions/runs/<id>`."""
        runs = _runs()
        self.assertIn("🧠 model-attempt: ${{ steps.model.outputs.model }} — proof agent starting", runs)
        line = next(l for l in runs.splitlines() if "🧠 model-attempt:" in l)
        self.assertTrue(
            line.rstrip().rstrip('"').endswith(
                "Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"),
            line)

    def test_the_kept_arms(self):
        names = {s.get("name") for s in _steps()}
        for kept in ("Push rescue", "Upload rescued work",
                     "Keep the run's working log",
                     "Keep the run's death-cause receipt",
                     "Install Claude Code (asserted)",
                     "Fail if the agent step failed"):
            self.assertIn(kept, names)


class ReadOnlyIdentityTest(unittest.TestCase):
    MINT = "Mint the read-only proof identity"

    def test_the_mint_is_read_only_by_its_permissions(self):
        step = _step(self.MINT)
        self.assertEqual(f"{APP_TOKEN}@{APP_TOKEN_PIN}", step.get("uses"))
        with_ = step.get("with") or {}
        perms = {k: v for k, v in with_.items() if k.startswith("permission-")}
        self.assertEqual(READ_PERMISSIONS, perms)
        self.assertNotIn("write", " ".join(str(v) for v in with_.values()))

    def test_the_mint_draws_from_the_read_pool(self):
        step = _step(self.MINT)
        pool_id = re.search(r"steps\.(\w+)\.outputs\.n", str(step["with"]["app-id"])).group(1)
        pool = next(s for s in _steps() if s.get("id") == pool_id)
        self.assertIn("dispatch_pool.py select", pool["run"])
        self.assertEqual("1", str(pool["env"].get("BUREAU_POOL_READ_ONLY")))
        self.assertLess(_steps().index(pool), _index(self.MINT))

    def test_the_agent_holds_it_as_gh_read_token_and_it_comes_first(self):
        mint_id = _step(self.MINT)["id"]
        for i, step in _agent_steps():
            self.assertEqual(f"${{{{ steps.{mint_id}.outputs.token }}}}",
                             (step.get("env") or {}).get("GH_READ_TOKEN"))
            self.assertLess(_index(self.MINT), i)

    def test_the_summary_line_names_the_app_and_the_permissions(self):
        runs = _runs()
        self.assertIn(GITHUB_SUMMARY.format(slug="$APP_SLUG"), runs)
        self.assertIn("$GITHUB_STEP_SUMMARY", runs)

    def test_a_refused_mint_costs_the_rows_never_the_run(self):
        """GitHub refuses the whole mint when the installation lacks one of
        the four permissions. The run goes on, the summary says why the
        GitHub rows are `Not observed.`, and the prompt forbids falling back
        to the worker token."""
        self.assertIs(_step(self.MINT).get("continue-on-error"), True)
        self.assertIn("proof identity: github read — none, the read-only mint was refused", _runs())
        self.assertIn("never observe with GH_TOKEN instead", _agent_steps()[0][1]["with"]["prompt"])


class CallerAwsIdentityTest(unittest.TestCase):
    ASSUME = "Assume the caller's proof identity"

    def _release_train_pin(self) -> str:
        m = re.search(rf"{AWS_CREDS}@([0-9a-f]{{40}})", RELEASE_TRAIN.read_text())
        return m.group(1)

    def test_the_action_is_at_release_trains_pin(self):
        step = _step(self.ASSUME)
        self.assertEqual(f"{AWS_CREDS}@{self._release_train_pin()}", step["uses"])
        self.assertEqual("e1253824e5c10ff9df46874f81ed3ec929e19cfd",
                         self._release_train_pin())

    def test_its_inputs(self):
        with_ = _step(self.ASSUME)["with"]
        self.assertEqual("${{ secrets.PROOF_ROLE_ARN }}", with_["role-to-assume"])
        self.assertEqual("proof-${{ github.event.client_payload.identifier }}",
                         with_["role-session-name"])
        self.assertEqual("${{ inputs.aws_region }}", with_["aws-region"])

    def test_it_runs_only_when_the_role_is_provided(self):
        """The secrets context cannot be read in an `if:`, and a job-level
        mirror would put the role in the agent step's env chain. So one step
        holds the secret alone and answers whether it is non-empty."""
        step = _step(self.ASSUME)
        m = re.fullmatch(r"\s*steps\.(\w+)\.outputs\.provided == 'true'\s*",
                         str(step.get("if")))
        self.assertIsNotNone(m, f"the assume step's gate is {step.get('if')!r}")
        probe = next(s for s in _steps() if s.get("id") == m.group(1))
        self.assertEqual("${{ secrets.PROOF_ROLE_ARN }}", probe["env"]["PROOF_ROLE_ARN"])
        self.assertIn('-n "$PROOF_ROLE_ARN"', probe["run"])
        self.assertLess(_steps().index(probe), _index(self.ASSUME))

    def test_it_comes_before_the_agent(self):
        self.assertLess(_index(self.ASSUME), _agent_steps()[0][0])

    def test_the_role_never_reaches_the_job_env(self):
        self.assertNotIn("PROOF_ROLE_ARN", json.dumps(_job().get("env") or {}))
        self.assertNotIn("PROOF_ROLE_ARN", json.dumps(_doc().get("env") or {}))

    def test_the_summary_lines(self):
        runs = _runs()
        self.assertIn(AWS_NONE, runs)
        self.assertIn(AWS_ASSUMED + "$CARD", runs)


#: `linear_ops.py advance <card> "<to>" "<from>"`, as a run step writes it.
_ADVANCE = re.compile(
    r'linear_ops\.py\s+advance\s+"\$CARD"\s+"([^"]+)"\s+"([^"]+)"')


class WritesOnlyItsThreeLanesTest(unittest.TestCase):
    """The card waits in `Hand-work` and runs there; the run itself writes
    only the park, the return and the move to review (DRE-5925)."""

    def test_no_in_progress_step(self):
        names = {s.get("name") for s in _steps()}
        self.assertNotIn("Card → In Progress", names)

    def test_never_the_build_report_or_the_dead_run_budget(self):
        src = PROOF_TASK.read_text()
        self.assertNotIn("report_agent_result.sh", src)
        self.assertNotIn("dead_run.py", src)

    def test_never_a_state_write(self):
        # `state-of` is a read.
        self.assertNotRegex(_runs(), r"linear_ops\.py\s+state(?![\w-])")

    def test_the_only_lane_writes_are_the_three_declared(self):
        runs = _runs()
        writes = re.findall(r"linear_ops\.py\s+advance\b[^\n]*", runs)
        self.assertEqual(3, len(writes), writes)
        self.assertEqual(
            {("Green Light", "Hand-work,In Review"),
             ("Hand-work", "Green Light"),
             ("In Review", "Hand-work")},
            set(_ADVANCE.findall(runs)))
        for lane in ("Todo", "In Progress", "Backlog", "Planning", "Triage"):
            self.assertNotRegex(runs, rf"linear_ops\.py[^\n]*['\"]{lane}['\"]",
                                f"a run step names {lane!r} to linear_ops")

    def test_each_write_sits_in_its_own_step(self):
        def advances(name):
            return set(_ADVANCE.findall(str(_step(name).get("run") or "")))
        self.assertEqual({("Hand-work", "Green Light")}, advances(RETURN_STEP))
        self.assertEqual({("Green Light", "Hand-work,In Review"),
                          ("In Review", "Hand-work")}, advances(RESULT_STEP))

    def test_never_the_dispatch_receipts_tag(self):
        self.assertNotIn("🔬 proof-run", PROOF_TASK.read_text())


# --- the result step, executed -------------------------------------------

_CARD_PR_STUB = """\
import os, sys
open(os.environ["CALLS"], "a").write("card_pr " + " ".join(sys.argv[1:]) + "\\n")
answer = os.environ.get("FAKE_PR", "")
if answer == "unreadable":
    sys.exit(3)
if answer:
    print(answer)
"""

_LINEAR_OPS_STUB = """\
import json, os, sys
open(os.environ["CALLS"], "a").write(json.dumps(["linear_ops"] + sys.argv[1:]) + "\\n")
if sys.argv[1] == "state-of":
    if os.environ.get("FAKE_LANE") == "unreadable":
        sys.exit("HTTP 400")
    print(os.environ.get("FAKE_LANE", ""))
if sys.argv[1] == "proof-waiting" and os.environ.get("FAKE_REFUSE_HOLD"):
    sys.exit("proof: refused")
"""

_SPOKEN_THREAD_STUB = """\
import json, os, sys
open(os.environ["CALLS"], "a").write(json.dumps(["spoken_thread"] + sys.argv[1:]) + "\\n")
answer = os.environ.get("FAKE_ANSWER", "")
if not answer:
    sys.exit(1)
print(answer)
"""

RUN_URL = "https://github.example/acme/widgets/actions/runs/42"


DESCRIPTION = """\
Observe the release.

## Acceptance criteria

- [ ] The release page names v3, read as the proof-reader identity
- [ ] needs the CEO's press: Approve the release in the console
- [ ] The CEO closes this card after reading the record
"""


class _StepHarness(unittest.TestCase):
    """One step's script, executed as written against stub scripts."""

    STEP = ""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        scripts = self.tmp / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "card_pr.py").write_text(_CARD_PR_STUB)
        (scripts / "linear_ops.py").write_text(_LINEAR_OPS_STUB)
        (scripts / "spoken_thread.py").write_text(_SPOKEN_THREAD_STUB)
        # The escalation is read through the real gate, not a stub.
        for real in ("check_agent_result.py", "execution_result.py"):
            shutil.copy(ROOT / "scripts" / real, scripts / real)
        self.calls = self.tmp / "calls.log"
        self.escalation = self.tmp / "agent-escalation.txt"
        self.summary = self.tmp / "summary.md"

    #: Harness-only names, never the step's own env.
    _HARNESS = ("PATH", "CALLS", "FAKE_PR", "FAKE_LANE", "FAKE_ANSWER",
                "FAKE_REFUSE_HOLD", "RUNNER_TEMP", "GITHUB_STEP_SUMMARY")

    def _exec(self, env: dict) -> tuple:
        step = _step(self.STEP)
        script = step["run"]
        self.assertNotIn("${{", script, "the step reads env, never interpolates")
        env = {"PATH": os.environ["PATH"], "CALLS": str(self.calls),
               "RUNNER_TEMP": str(self.tmp),
               "GITHUB_STEP_SUMMARY": str(self.summary), **env}
        for name in env:
            if name not in self._HARNESS:
                self.assertIn(name, step["env"], f"{self.STEP!r} does not declare {name}")
        done = subprocess.run(["bash", "-e", "-c", script], cwd=self.tmp, env=env,
                              capture_output=True, text=True, timeout=60)
        lines = self.calls.read_text().splitlines() if self.calls.exists() else []
        return done, [json.loads(l) for l in lines if l.startswith("[")], lines


class ResultStepTest(_StepHarness):
    """The four exits, the first that holds taking it — except that a record
    pull request AND an escalation both act, the pull request first (a run
    that meets a CEO-only press opens its record before it asks)."""

    STEP = RESULT_STEP

    def _run(self, *, pr: str = "", escalation: str | None = None,
             description: str = DESCRIPTION, refuse_hold: bool = False) -> list:
        if escalation is not None:
            self.escalation.write_text(escalation)
        env = {
            "FAKE_PR": pr,
            "CARD": "DRE-77",
            "CARD_DESCRIPTION": description,
            "RECORD_BRANCH": "agent/DRE-77-proof-record",
            "ESCALATION_FILE": str(self.escalation),
            "BUREAU_SERVER_URL": "https://github.example",
            "BUREAU_REPOSITORY": "acme/widgets",
            "BUREAU_RUN_ID": "42",
            "CLAUDE_EXECUTION_FILE": "",
        }
        if refuse_hold:
            env["FAKE_REFUSE_HOLD"] = "1"
        done, posted, lines = self._exec(env)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        card_pr = [l for l in lines if l.startswith("card_pr ")]
        self.assertEqual(["card_pr find DRE-77 --branch agent/DRE-77-proof-record"], card_pr)
        for call in posted:
            self.assertEqual(["linear_ops", "DRE-77"], [call[0], call[2]], call)
            self.assertIn(call[1], ("comment", "proof-waiting", "advance"), call)
            if call[1] == "comment":
                self.assertNotIn("🔬 proof-run", call[3])
                self.assertTrue(call[3].rstrip().endswith(f"Run: {RUN_URL}"), call[3])
        return posted

    @staticmethod
    def _comments(posted: list) -> list:
        return [c[3] for c in posted if c[1] == "comment"]

    @staticmethod
    def _moves(posted: list) -> list:
        return [tuple(c[3:]) for c in posted if c[1] == "advance"]

    def test_the_env_names_the_real_paths(self):
        env = _step(RESULT_STEP)["env"]
        self.assertEqual("/tmp/agent-escalation.txt", env["ESCALATION_FILE"])
        self.assertEqual("agent/${{ github.event.client_payload.identifier }}-proof-record",
                         env["RECORD_BRANCH"])
        self.assertEqual("${{ github.event.client_payload.description }}",
                         env["CARD_DESCRIPTION"])

    def test_exit_1_an_open_record_pull_request_carries_the_card_to_review(self):
        posted = self._run(pr="OPEN\thttps://github.example/acme/widgets/pull/9")
        self.assertEqual(
            [f"🤖 PR opened: https://github.example/acme/widgets/pull/9 — CI + critic review running. Run: {RUN_URL}"],
            self._comments(posted))
        # After a return the amended record is pushed to a pull request that
        # was already open, and neither qa-review.yml nor the sweep moves it.
        self.assertEqual([("In Review", "Hand-work")], self._moves(posted))
        self.assertEqual("comment", posted[0][1])

    def test_exit_2_the_record_already_merged(self):
        posted = self._run(pr="MERGED\thttps://github.example/acme/widgets/pull/9")
        self.assertEqual(1, len(posted))
        self.assertTrue(posted[0][3].startswith("🤖 PR already merged: https://github.example/acme/widgets/pull/9"),
                        posted[0])

    def test_exit_3_an_escalation_holds_then_asks_then_parks(self):
        posted = self._run(escalation="Approve the release in the console and say what "
                                      "you saw, or drop the criterion?\n")
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted])
        hold, ask, park = posted
        self.assertEqual(["needs the CEO's press: Approve the release in the console",
                          "the CEO's press: Approve the release in the console"], hold[3:])
        self.assertTrue(ask[3].startswith(PARK_RECEIPT), ask[3])
        self.assertIn("Approve the release in the console and say what you saw", ask[3])
        self.assertNotIn(NO_RECORD, ask[3])
        self.assertEqual(("Green Light", "Hand-work,In Review"), tuple(park[3:]))

    def test_a_pull_request_and_an_escalation_both_act_the_pull_request_first(self):
        posted = self._run(pr="OPEN\thttps://github.example/acme/widgets/pull/9",
                           escalation="Approve the release in the console?\n")
        self.assertEqual(["comment", "advance", "proof-waiting", "comment", "advance"],
                         [c[1] for c in posted])
        self.assertTrue(posted[0][3].startswith("🤖 PR opened:"))
        self.assertTrue(posted[3][3].startswith(PARK_RECEIPT))
        self.assertIn("Approve the release in the console?", posted[3][3])
        # Review first, then the park out of either lane.
        self.assertEqual([("In Review", "Hand-work"), ("Green Light", "Hand-work,In Review")],
                         self._moves(posted))

    def test_the_press_is_read_off_the_criterion_the_escalation_names(self):
        description = (
            "- [ ] The page names v3\n"
            "- [ ] The release is published — needs the CEO’s press: Publish v3 in the console.\n"
            "* [ ] needs the CEO's press: Rotate the console key\n")
        posted = self._run(escalation="Rotate the console key, or drop it?\n",
                           description=description)
        [hold] = [c for c in posted if c[1] == "proof-waiting"]
        self.assertEqual(["needs the CEO's press: Rotate the console key",
                          "the CEO's press: Rotate the console key"], hold[3:])
        self.calls.unlink()
        posted = self._run(escalation="Publish v3, or drop the row?\n",
                           description=description)
        [hold] = [c for c in posted if c[1] == "proof-waiting"]
        self.assertEqual(["The release is published",
                          "the CEO's press: Publish v3 in the console"], hold[3:])

    def test_a_card_that_names_no_press_holds_on_the_escalation_s_words(self):
        posted = self._run(escalation="Press publish in the console, or drop the row?\nmore\n",
                           description="- [ ] The page names v3\n")
        [hold] = [c for c in posted if c[1] == "proof-waiting"]
        self.assertEqual("the CEO's press: Press publish in the console, or drop the row?",
                         hold[4])
        self.assertTrue(hold[3].strip())
        self.assertEqual([("Green Light", "Hand-work,In Review")], self._moves(posted))

    def test_a_refused_hold_still_parks_the_question_and_says_so(self):
        posted = self._run(escalation="Approve the release in the console?\n",
                           refuse_hold=True)
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted])
        self.assertIn("hold could not be posted", self.summary.read_text())

    def test_exit_4_nothing_came_out(self):
        posted = self._run()
        self.assertEqual(
            [f"{NO_RECORD} — the dispatcher reads this run off the card and decides whether to dispatch again. Run: {RUN_URL}"],
            self._comments(posted))
        self.assertEqual([], self._moves(posted))

    def test_an_empty_escalation_file_is_not_an_escalation(self):
        posted = self._run(escalation="")
        self.assertEqual(1, len(posted))
        self.assertTrue(posted[0][3].startswith(NO_RECORD))

    def test_an_unreadable_pull_request_is_never_reported_as_opened(self):
        posted = self._run(pr="unreadable")
        self.assertEqual(1, len(posted))
        self.assertTrue(posted[0][3].startswith(NO_RECORD), posted[0])
        self.assertIn("could not be read", posted[0][3])


class ReturnStepTest(_StepHarness):
    """The return from Green Light (DRE-5925): only on the dispatcher's return
    reason AND a card in Green Light, the discharge record before the move,
    and only into `Hand-work` — all before the agent step."""

    STEP = RETURN_STEP
    REASON = f"{RETURN_REASON} at 2026-10-06 09:12 PT"
    ANSWER = "2026-10-06 09:12 PT\tPressed it; the release shows v3."

    def _run(self, *, reason: str = REASON, lane: str = "Green Light",
             answer: str = ANSWER) -> tuple:
        return self._exec({"CARD": "DRE-77", "REASON": reason,
                           "FAKE_LANE": lane, "FAKE_ANSWER": answer})

    def test_it_runs_before_every_agent_attempt_and_after_the_reason_is_sanitized(self):
        here = _index(RETURN_STEP)
        self.assertLess(here, min(i for i, _ in _agent_steps()))
        self.assertGreater(here, _index("Sanitize untrusted card text"))
        step = _step(RETURN_STEP)
        self.assertEqual("${{ steps.card.outputs.reason }}", step["env"]["REASON"])
        self.assertNotIn("continue-on-error", step)

    def test_the_return_posts_the_discharge_then_moves_to_hand_work(self):
        done, posted, _ = self._run()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual([
            ["linear_ops", "state-of", "DRE-77"],
            ["spoken_thread", "answer", "DRE-77"],
            ["linear_ops", "proof-observed", "DRE-77",
             "the CEO answered via the console at 2026-10-06 09:12 PT: "
             "Pressed it; the release shows v3."],
            ["linear_ops", "advance", "DRE-77", "Hand-work", "Green Light"],
        ], posted)

    def test_any_other_reason_does_nothing_and_says_so(self):
        for reason in ("", "first run: the release carrying the epic is live",
                       "second dispatch after a dead run",
                       f"not a {RETURN_REASON}"):
            done, posted, _ = self._run(reason=reason)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual([], posted, reason)
            self.assertIn("nothing to return", done.stdout)
            self.calls.unlink(missing_ok=True)

    def test_any_other_lane_does_nothing_and_says_so(self):
        for lane in ("Hand-work", "In Review", "Triage", ""):
            done, posted, _ = self._run(lane=lane)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual([["linear_ops", "state-of", "DRE-77"]], posted, lane)
            self.assertIn("nothing to return", done.stdout)
            self.calls.unlink(missing_ok=True)

    def test_an_unreadable_lane_or_answer_fails_before_any_write(self):
        for kwargs in ({"lane": "unreadable"}, {"answer": ""}):
            done, posted, _ = self._run(**kwargs)
            self.assertNotEqual(0, done.returncode, kwargs)
            self.assertEqual([], [c for c in posted if c[1] in ("proof-observed", "advance")],
                             kwargs)
            self.calls.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
