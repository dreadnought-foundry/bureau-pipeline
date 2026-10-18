"""proof-task.yml — the proof run's own reusable workflow (DRE-5924).

The proof runner (DRE-5921) is NOT a role on `agent-task.yml`, on purpose:
`scripts/proof_and_demo.py` derives the build roles from the roster entries
running on that workflow, and a role there is a role the fleet picks up for a
PROOF card (DRE-3039). So the proof run gets its own workflow, and these tests
LIVE-EXTRACT it (no fixtures, no copies) and pin what makes it a proof run
rather than a build run:

  * it assembles context and selects a model for role `proof`, and pins no
    model id;
  * it holds a GitHub identity that is read-only BY ITS PERMISSIONS — only
    `permission-*: read` inputs, scoped to the caller's organization, minted
    with `variables` and again without it when the installation refuses that
    — and an AWS session only when the caller provides a role, both minted
    before the agent runs, and both written to the step summary in the line
    the record copies, the GitHub line derived from the mint that succeeded;
  * it states the agent's Linear request cap, `PROOF_LINEAR_REQUESTS`, beside
    the reason for its value;
  * it writes three lanes and no other (DRE-5925): the park into
    `Green Light` on a press only the CEO can make, out of `Hand-work` or
    `In Review`, after the `🔬 proof-waiting` hold and the question; the
    return to `Hand-work` out of `Green Light` on his signed answer, after the
    `🔬 proof-observed` discharge record and before the agent; and `In Review`
    out of `Hand-work` when the record's pull request is open. There is no
    `Card → In Progress` step, no `report_agent_result.sh` (whose every move
    reads the card out of `In Progress,Todo` and whose dead-run exits requeue,
    park or replan), no `dead_run.py`, and no `state` write;
  * its result step's exits — an open record, a merged one, an escalation,
    and nothing at all — read the pull request before the escalation, and
    that, the return step and the identity record line are EXECUTED here,
    against stub scripts, rather than grepped, because the order is the
    contract.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import step_shell  # noqa: E402 — a moved step's shell, where it now lives (DRE-3488)
import proof_cause_park  # noqa: E402 — the cause question the step tells a cause file by

WORKFLOWS = ROOT / ".github" / "workflows"
PROOF_TASK = WORKFLOWS / "proof-task.yml"
AGENT_TASK = WORKFLOWS / "agent-task.yml"
RELEASE_TRAIN = WORKFLOWS / "release-train.yml"

ACTION = "anthropics/claude-code-action"
APP_TOKEN = "actions/create-github-app-token"
AWS_CREDS = "aws-actions/configure-aws-credentials"
#: The pin this repo already carries for the App token mint (v3.2.0).
APP_TOKEN_PIN = "bcd2ba49218906704ab6c1aa796996da409d3eb1"

#: The version comment the pin above carries on every mint.
APP_TOKEN_VERSION = "v3.2.0"

AWS_NONE = "proof identity: aws — none, PROOF_ROLE_ARN not provided"
AWS_ASSUMED = "proof identity: aws — role assumed as proof-"
AWS_REFUSED = ("proof identity: aws — none, PROOF_ROLE_ARN provided but the "
               "role could not be assumed")

#: The caller's repository variable that sets the AWS session's length
#: (DRE-7020). Unset, the input is empty and the pinned action uses its own
#: 3,600 seconds; the agent steps read the same variable with that fallback.
SESSION_VAR = "${{ vars.PROOF_ROLE_DURATION_SECONDS }}"
SESSION_ENV = "PROOF_SESSION_SECONDS"
SESSION_VALUE = "${{ vars.PROOF_ROLE_DURATION_SECONDS || '3600' }}"

RESULT_STEP = "Report proof result to Linear"
RETURN_STEP = "Return from Green Light on the CEO's answer"
NO_RECORD = "🤖 proof run ended with no record"
PARK_RECEIPT = "🙋 The proof run met a press only the CEO can make, and asks:"
DECISION_RECEIPT = "🙋 The proof run asks a decision only the CEO can make:"
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
    return "\n".join(step_shell.step_shell(s) for s in _steps() if s.get("run"))


def _source() -> str:
    """The file with each delegation line expanded to its script, so a check
    on the file's text still reads the shell `step_shell.py move` took out."""
    return step_shell.workflow_source(PROOF_TASK)


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
        src = _source()
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
        self.assertNotIn("agent-handback.txt", _source())
        self.assertNotIn("TDD", prompt)

    def test_no_turn_rung_and_no_role_from_labels(self):
        src = _source()
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


def _step_source(name: str) -> str:
    """The step's own lines in the file, so a `uses:` line's comment is read."""
    src = PROOF_TASK.read_text()
    start = src.index(f"      - name: {name}\n")
    end = src.find("\n      - name: ", start + 1)
    return src[start:end if end != -1 else len(src)]


def _permissions(step: dict) -> dict:
    return {k: v for k, v in (step.get("with") or {}).items()
            if k.startswith("permission-")}


def _permission_text(step: dict) -> str:
    """The mint's own `permission-*` inputs as the record line spells them:
    `permission-pull-requests: read` → `pull-requests:read`."""
    return " ".join(f"{k[len('permission-'):]}:{v}"
                    for k, v in _permissions(step).items())


class ReadOnlyIdentityTest(unittest.TestCase):
    MINT = "Mint the read-only proof identity"
    NARROW = "Mint the read-only proof identity without variables"
    HAND = "Hand the read-only proof identity to the agent"
    RECORD = "Record the read-only proof identity"
    # Derived from `config/proof-reach.json` (DRE-6605), never restated here:
    # the narrow mint asks for `permissions`, the wide one for those and
    # `optional_permissions` too.
    _REACH = json.loads((ROOT / "config" / "proof-reach.json").read_text())["github"]
    FOUR = {f"permission-{k}" for k in _REACH["permissions"]}
    FIVE = FOUR | {f"permission-{k}" for k in _REACH["optional_permissions"]}

    def _mints(self) -> list[dict]:
        return [_step(self.MINT), _step(self.NARROW)]

    def test_the_mint_is_read_only_by_its_permissions(self):
        for name in (self.MINT, self.NARROW):
            step = _step(name)
            self.assertEqual(f"{APP_TOKEN}@{APP_TOKEN_PIN}", step.get("uses"), name)
            self.assertIn(f"uses: {APP_TOKEN}@{APP_TOKEN_PIN} # {APP_TOKEN_VERSION}",
                          _step_source(name), name)
            with_ = step.get("with") or {}
            # Every repository the App's installation reaches in the caller's
            # organization: owner set, repositories empty.
            self.assertEqual("${{ github.repository_owner }}", with_.get("owner"), name)
            self.assertNotIn("repositories", with_, name)
            perms = _permissions(step)
            self.assertTrue(perms, name)
            self.assertEqual({"read"}, set(perms.values()), name)
        self.assertEqual("reader", _step(self.MINT).get("id"))
        self.assertEqual("reader_narrow", _step(self.NARROW).get("id"))
        self.assertEqual(self.FIVE, set(_permissions(_step(self.MINT))))
        self.assertEqual(self.FOUR, set(_permissions(_step(self.NARROW))))
        self.assertEqual("steps.reader.outcome != 'success'",
                         str(_step(self.NARROW).get("if")).strip())
        self.assertEqual(_index(self.MINT) + 1, _index(self.NARROW))

    def test_neither_mint_asks_for_a_write(self):
        for step in self._mints():
            with_ = step.get("with") or {}
            for key, value in _permissions(step).items():
                self.assertNotEqual("write", str(value).strip(), (step["name"], key))
                self.assertEqual("read", str(value).strip(), (step["name"], key))
            self.assertNotIn("write", " ".join(str(v) for v in with_.values()),
                             step["name"])

    def test_the_mint_draws_from_the_read_pool(self):
        for step in self._mints():
            pool_ids = {re.search(r"steps\.(\w+)\.outputs\.n", str(step["with"][k])).group(1)
                        for k in ("app-id", "private-key")}
            self.assertEqual({"readpool"}, pool_ids, step["name"])
            pool = next(s for s in _steps() if s.get("id") == "readpool")
            self.assertIn("dispatch_pool.py select", pool["run"])
            self.assertEqual("1", str(pool["env"].get("BUREAU_POOL_READ_ONLY")))
            self.assertLess(_steps().index(pool), _steps().index(step))
        # Both mints ask the same App pair, so the fallback is the same App.
        self.assertEqual(_step(self.MINT)["with"]["app-id"],
                         _step(self.NARROW)["with"]["app-id"])
        self.assertEqual(_step(self.MINT)["with"]["private-key"],
                         _step(self.NARROW)["with"]["private-key"])

    def test_one_step_hands_the_token_on_in_the_form_the_roster_reads(self):
        step = _step(self.HAND)
        self.assertEqual("readtoken", step.get("id"))
        self.assertGreater(_index(self.HAND), _index(self.MINT))
        self.assertGreater(_index(self.HAND), _index(self.NARROW))
        self.assertEqual(
            {"TOKEN": "${{ steps.reader.outputs.token || steps.reader_narrow.outputs.token }}"},
            step.get("env"))
        script = str(step.get("run") or "")
        self.assertNotIn("${{", script)
        self.assertIn("token=", script)
        self.assertIn("$GITHUB_OUTPUT", script)
        # Executed: the token reaches the output and nothing is printed.
        for token in ("ghs_example", ""):
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "output"
                done = subprocess.run(
                    ["bash", "-e", "-c", script],
                    env={"PATH": os.environ["PATH"], "TOKEN": token,
                         "GITHUB_OUTPUT": str(out)},
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(0, done.returncode, done.stderr)
                self.assertEqual("", done.stdout + done.stderr)
                self.assertEqual(f"token={token}\n", out.read_text())

    def test_the_agent_holds_it_as_gh_read_token_and_it_comes_first(self):
        for i, step in _agent_steps():
            self.assertEqual("${{ steps.readtoken.outputs.token }}",
                             (step.get("env") or {}).get("GH_READ_TOKEN"))
            self.assertLess(_index(self.HAND), i)

    def _record(self, wide: str, narrow: str) -> str:
        step = _step(self.RECORD)
        script = step["run"]
        self.assertNotIn("${{", script, "the step reads env, never interpolates")
        env = {"WIDE": wide, "NARROW": narrow, "APP_SLUG": "agent-bureau-bot-3",
               "OWNER": "acme"}
        self.assertEqual(set(env), set(step.get("env") or {}))
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            done = subprocess.run(
                ["bash", "-e", "-c", script],
                env={"PATH": os.environ["PATH"], "GITHUB_STEP_SUMMARY": str(summary),
                     **env},
                capture_output=True, text=True, timeout=30)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual(done.stdout, summary.read_text())
            return done.stdout.rstrip("\n")

    def test_the_summary_line_names_the_app_and_the_permissions(self):
        step = _step(self.RECORD)
        env = step.get("env") or {}
        self.assertEqual("${{ steps.reader.outcome }}", env.get("WIDE"))
        self.assertEqual("${{ steps.reader_narrow.outcome }}", env.get("NARROW"))
        self.assertEqual(
            "${{ steps.reader.outputs.app-slug || steps.reader_narrow.outputs.app-slug }}",
            env.get("APP_SLUG"))
        # The owner the line names is the owner the mints were scoped to.
        for mint in self._mints():
            self.assertEqual(mint["with"]["owner"], env.get("OWNER"))
        self.assertGreater(_index(self.RECORD), _index(self.HAND))

        # Derived from each mint's own inputs, never restated here.
        wide = _permission_text(_step(self.MINT))
        narrow = _permission_text(_step(self.NARROW))
        self.assertIn("variables:read", wide)
        self.assertNotIn("variables:read", narrow)
        self.assertIn(wide, step["run"])
        self.assertIn(narrow, step["run"])
        head = "proof identity: github read — app agent-bureau-bot-3, owner acme, permissions "
        self.assertEqual(head + wide, self._record("success", "skipped"))
        self.assertEqual(head + narrow + " (variables:read not granted to this installation)",
                         self._record("failure", "success"))
        self.assertEqual("proof identity: github read — none, the read-only mint was refused",
                         self._record("failure", "failure"))

    def test_a_refused_mint_costs_the_rows_never_the_run(self):
        """GitHub refuses the whole mint when the installation lacks one of
        the permissions asked for. The narrow mint runs; when it is refused
        too, the run goes on, the summary says why the GitHub rows are
        `Not observed.`, and the prompt forbids falling back to the worker
        token."""
        for name in (self.MINT, self.NARROW):
            self.assertIs(_step(name).get("continue-on-error"), True, name)
        self.assertIn("proof identity: github read — none, the read-only mint was refused", _runs())
        prompt = _agent_steps()[0][1]["with"]["prompt"]
        self.assertIn("never observe with GH_TOKEN instead", prompt)
        self.assertIn("If GH_READ_TOKEN is empty, the", prompt)

    def test_no_other_copy_lists_the_permissions(self):
        roster = (ROOT / "agents.yaml").read_text()
        entry = roster[roster.index("  - name: proof\n"):]
        entry = entry[:entry.index("\n  - name: ", 1)]
        self.assertNotIn("pull requests, metadata", entry)
        self.assertIn("Record the read-only proof identity", entry)
        brief = " ".join((ROOT / "briefs" / "proof.md").read_text().split())
        self.assertNotIn("contents, actions, pull requests, metadata", brief)


class LinearRequestCapTest(unittest.TestCase):
    """The agent may read cards in Linear, never write them, within a stated
    number of requests per run (briefs/proof.md, Identities)."""

    def test_every_agent_step_states_the_cap_with_its_reason(self):
        src = PROOF_TASK.read_text().splitlines()
        lines = [i for i, l in enumerate(src)
                 if l.strip().startswith("PROOF_LINEAR_REQUESTS:")]
        self.assertEqual(3, len(lines), "one on each agent step")
        for i in lines:
            self.assertEqual('PROOF_LINEAR_REQUESTS: "40"', src[i].strip())
            comment = " ".join(l.strip() for l in src[i - 6:i] if l.strip().startswith("#"))
            self.assertIn("2,500", comment)
        for _i, step in _agent_steps():
            self.assertEqual("40", (step.get("env") or {}).get("PROOF_LINEAR_REQUESTS"))

    def test_the_prompt_allows_reading_cards_up_to_the_cap(self):
        prompt = _agent_steps()[0][1]["with"]["prompt"]
        flat = " ".join(prompt.split())
        bullet = flat[flat.index("- LINEAR_API_KEY"):]
        bullet = bullet[:bullet.index(" - ", 2)]
        self.assertNotIn("and nothing else", bullet)
        for phrase in ("heartbeats, receipts and escalation",
                       "reading cards (never writing them)",
                       "PROOF_LINEAR_REQUESTS requests in all",
                       "briefs/proof.md"):
            self.assertIn(phrase, bullet)


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

    def test_the_session_length_is_the_callers_variable_and_nothing_else_moved(self):
        """DRE-7020: the step gains `role-duration-seconds` read from the
        caller's variable with no fallback — unset, the input is empty and the
        pinned action's own 3,600 applies, today's behavior exactly — and
        every other field of the step is what it was."""
        step = _step(self.ASSUME)
        self.assertEqual(
            f"{AWS_CREDS}@e1253824e5c10ff9df46874f81ed3ec929e19cfd", step["uses"])
        self.assertEqual("steps.proof_role.outputs.provided == 'true'", step["if"])
        self.assertIs(True, step["continue-on-error"])
        self.assertEqual("aws", step["id"])
        self.assertEqual({
            "role-to-assume": "${{ secrets.PROOF_ROLE_ARN }}",
            "role-session-name": "proof-${{ github.event.client_payload.identifier }}",
            "aws-region": "${{ inputs.aws_region }}",
            "role-duration-seconds": SESSION_VAR,
        }, step["with"])
        self.assertEqual(
            {"name", "id", "if", "continue-on-error", "uses", "with"}, set(step))

    def test_the_session_length_input_says_what_unset_and_too_long_cost(self):
        src = PROOF_TASK.read_text().splitlines()
        at = [i for i, l in enumerate(src)
              if l.strip() == f"role-duration-seconds: {SESSION_VAR}"]
        self.assertEqual(1, len(at), "one role-duration-seconds input, as written")
        start = max(i for i, l in enumerate(src[:at[0]])
                    if l.strip() == f"- name: {self.ASSUME}")
        comment = " ".join(l.strip().lstrip("#").strip()
                           for l in src[start:at[0]] if l.strip().startswith("#"))
        for phrase in ("3,600", "unset", "maximum", "AWS rows"):
            self.assertIn(phrase, comment)

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

    def test_the_record_step_writes_its_three_lines_character_for_character(self):
        """DRE-7020 changes the session's length, never the line the record
        copies: the step writes exactly these three, and no fourth."""
        run = _step("Record the caller's proof identity")["run"]
        lines = re.findall(r'^\s*LINE="([^"\n]*)"\s*$', run, re.M)
        self.assertEqual([AWS_NONE, AWS_ASSUMED + "$CARD", AWS_REFUSED], lines)


class ProofSessionLengthTest(unittest.TestCase):
    """The run learns its AWS session's length from its env (DRE-7020): a
    number, not a credential — the read token cannot read repository
    variables on every installation, and a run cannot read its own step
    summary."""

    def test_every_agent_step_carries_the_length_with_the_one_hour_fallback(self):
        agents = _agent_steps()
        self.assertEqual(3, len(agents))
        for _i, step in agents:
            self.assertEqual(SESSION_VALUE, (step.get("env") or {}).get(SESSION_ENV),
                             f"{step['name']} does not carry {SESSION_ENV}")

    def test_no_other_step_carries_it(self):
        agent_ids = {i for i, _s in _agent_steps()}
        for i, step in enumerate(_steps()):
            if i in agent_ids:
                continue
            self.assertNotIn(SESSION_ENV, step.get("env") or {}, step.get("name"))
        self.assertNotIn(SESSION_ENV, _job().get("env") or {})
        self.assertNotIn(SESSION_ENV, _doc().get("env") or {})
        self.assertEqual(3, PROOF_TASK.read_text().count(SESSION_ENV),
                         "the name appears once per agent step and nowhere else")


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
        src = _source()
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
            return set(_ADVANCE.findall(step_shell.step_shell(_step(name))))
        self.assertEqual({("Hand-work", "Green Light")}, advances(RETURN_STEP))
        self.assertEqual({("Green Light", "Hand-work,In Review"),
                          ("In Review", "Hand-work")}, advances(RESULT_STEP))

    def test_never_the_dispatch_receipts_tag(self):
        self.assertNotIn("🔬 proof-run", _source())


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
# linear_ops.proof_text_refusal's console hold markers, refused the same way.
if sys.argv[1] == "proof-observed" and any(
        m in sys.argv[3].lower() for m in ("budget exhausted", "holding for a human")):
    sys.exit("proof: refused")
if sys.argv[1] == "advance" and os.environ.get("FAKE_NOT_ADVANCING"):
    print(f"{sys.argv[2]} is in 'Done', not in {sys.argv[4]!r} — not advancing")
"""

_SPOKEN_THREAD_STUB = """\
import json, os, sys
open(os.environ["CALLS"], "a").write(json.dumps(["spoken_thread"] + sys.argv[1:]) + "\\n")
answer = os.environ.get("FAKE_ANSWER", "")
if answer == "unreadable":
    sys.exit(3)
if not answer:
    sys.exit(1)
print(answer)
"""

#: The one writer of a cause park (DRE-6837), stubbed: it records its argv and
#: prints its result line last, as the real CLI does, and exits as
#: `FAKE_PARK_EXIT` says. Its `QUESTION` is the real module's — the step imports
#: it to tell a cause file from a decision, and an import records nothing.
_PROOF_CAUSE_PARK_STUB = f"""\
import json, os, sys
QUESTION = {proof_cause_park.QUESTION!r}
if __name__ == "__main__":
    open(os.environ["CALLS"], "a").write(json.dumps(["proof_cause_park"] + sys.argv[1:]) + "\\n")
    code = int(os.environ.get("FAKE_PARK_EXIT") or 0)
    if code == 0:
        print(f"proof_cause_park: {{sys.argv[2]}} — held, asked")
        print(f"left {{sys.argv[2]}} in In Review for the operator")
    else:
        print("proof_cause_park: refused", file=sys.stderr)
    sys.exit(code)
"""

RUN_URL = "https://github.example/acme/widgets/actions/runs/42"


DESCRIPTION = """\
Observe the release.

## Acceptance criteria

- [ ] The release page names v3, read as the proof-reader identity
- [ ] needs the CEO's press: Approve the release in the console
- [ ] The CEO closes this card after reading the record
"""


#: A card description that marks no press.
NO_PRESS = "- [ ] The page names v3\n- [ ] The report goes out weekly\n"

#: An escalation of cause rows and nothing else: the three lines, its Question
#: the module's own, and one cause line.
CAUSE_FILE = (
    "🔎 Finding: 1 row of the proof record cannot be observed as worded (cause: out-of-reach).\n"
    f"❓ Question: {proof_cause_park.QUESTION}\n"
    "💡 Recommendation: Reword it on a new card — the read token is not granted variables.\n"
    "cause: out-of-reach — “The repo variable PROOF_POOL_SIZE reads 5” — 403 on gh "
    "variable get\n")


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
                "FAKE_REFUSE_HOLD", "FAKE_NOT_ADVANCING", "FAKE_PARK_EXIT",
                "RUNNER_TEMP", "GITHUB_STEP_SUMMARY")

    def _install_cause_park(self) -> None:
        """The stub writer, and the real three lines it is read beside."""
        scripts = self.tmp / ".bureau-pipeline" / "scripts"
        (scripts / "proof_cause_park.py").write_text(_PROOF_CAUSE_PARK_STUB)
        shutil.copy(ROOT / "scripts" / "console_escalation.py", scripts)

    def _exec(self, env: dict) -> tuple:
        step = _step(self.STEP)
        script = step_shell.step_shell(step)
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
             description: str = DESCRIPTION, refuse_hold: bool = False,
             not_advancing: bool = False, park_exit: int | None = None) -> list:
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
        if not_advancing:
            env["FAKE_NOT_ADVANCING"] = "1"
        if park_exit is not None:
            env["FAKE_PARK_EXIT"] = str(park_exit)
        done, posted, lines = self._exec(env)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.stdout = done.stdout
        # The writer's calls apart: what the step posted is linear_ops'.
        self.parks = [c for c in posted if c[0] == "proof_cause_park"]
        posted = [c for c in posted if c[0] != "proof_cause_park"]
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
        # The sanitized description, the one the rest of the workflow reads.
        self.assertEqual("${{ steps.card.outputs.description }}",
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

    def test_a_park_the_card_refused_says_so_in_the_summary(self):
        posted = self._run(escalation="Approve the release in the console?\n",
                           not_advancing=True)
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted])
        self.assertIn("did not move DRE-77 to Green Light", self.summary.read_text())

    def test_a_park_that_moved_says_nothing_of_a_refusal(self):
        self._run(escalation="Approve the release in the console?\n")
        self.assertNotIn("did not move", self.summary.read_text())

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

    # --- the escalation sorted by what it names (DRE-6840) -----------------

    def _park_argv(self) -> list:
        return ["proof_cause_park", "park", "DRE-77", "--escalation-file",
                str(self.escalation), "--run-url", RUN_URL]

    def _summary(self) -> str:
        return self.summary.read_text() if self.summary.exists() else ""

    def test_cause_rows_with_an_open_record_go_to_the_one_writer(self):
        self._install_cause_park()
        posted = self._run(pr="OPEN\thttps://github.example/acme/widgets/pull/9",
                           escalation=CAUSE_FILE, description=NO_PRESS)
        self.assertEqual([self._park_argv()], self.parks)
        # The pull request's report and its In Review move, and nothing else:
        # no hold, no question and no Green Light move of the step's own.
        self.assertEqual(["comment", "advance"], [c[1] for c in posted])
        self.assertEqual([("In Review", "Hand-work")], self._moves(posted))
        self.assertFalse(any(c.startswith("🙋") for c in self._comments(posted)))
        # The writer's own result line closes the run's log, never a lane assumed.
        self.assertIn("proof result: record pull request, then left DRE-77 in "
                      "In Review for the operator\n", self.stdout)
        self.assertNotIn("only the CEO can make", self._summary())
        self.assertNotIn("only the CEO can make", self.stdout)

    def test_a_file_with_no_press_and_no_cause_line_is_a_decision(self):
        posted = self._run(escalation="Keep the weekly report on Saturdays, or move it "
                                      "to Monday?\n", description=NO_PRESS)
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted])
        hold, ask, park = posted
        self.assertEqual("the CEO's press: Keep the weekly report on Saturdays, or move "
                         "it to Monday?", hold[4])
        self.assertTrue(ask[3].startswith(DECISION_RECEIPT), ask[3])
        self.assertNotIn(PARK_RECEIPT, ask[3])
        self.assertEqual(("Green Light", "Hand-work,In Review"), tuple(park[3:]))
        self.assertIn("proof park: decision", self._summary())
        self.assertNotIn("proof park: press", self._summary())

    def test_a_writer_that_finds_no_cause_row_sends_the_file_to_the_decision(self):
        self._install_cause_park()
        posted = self._run(pr="OPEN\thttps://github.example/acme/widgets/pull/9",
                           escalation=CAUSE_FILE, description=NO_PRESS, park_exit=2)
        self.assertEqual([self._park_argv()], self.parks)
        self.assertEqual(["comment", "advance", "proof-waiting", "comment", "advance"],
                         [c[1] for c in posted])
        self.assertTrue(posted[3][3].startswith(DECISION_RECEIPT), posted[3][3])
        self.assertEqual([("In Review", "Hand-work"), ("Green Light", "Hand-work,In Review")],
                         self._moves(posted))

    def test_a_writer_that_refuses_leaves_a_warning_and_moves_nothing(self):
        self._install_cause_park()
        posted = self._run(pr="OPEN\thttps://github.example/acme/widgets/pull/9",
                           escalation=CAUSE_FILE, description=NO_PRESS, park_exit=1)
        self.assertEqual([self._park_argv()], self.parks)
        # Never the press or decision fallback: that would put the operator's
        # question in the CEO's queue.
        self.assertEqual(["comment", "advance"], [c[1] for c in posted])
        self.assertEqual([("In Review", "Hand-work")], self._moves(posted))
        self.assertIn("::warning::", self.stdout)
        self.assertIn("proof park: the cause park could not be completed for DRE-77 — the "
                      "dispatcher's next pass parks the record off its rows", self._summary())
        self.assertNotIn("only the CEO can make", self._summary())


class ReturnStepTest(_StepHarness):
    """The return from Green Light (DRE-5925): a card in Green Light with an
    answer of his posted after the park returns, whatever the dispatch reason
    — so a return that died between its two writes is finished by the next
    dispatch. The discharge record before the move, and only into
    `Hand-work` — all before the agent step."""

    STEP = RETURN_STEP
    REASON = f"{RETURN_REASON} at 2026-10-06 09:12 PT"
    ANSWER = "2026-10-06 09:12 PT\tPressed it; the release shows v3."
    DISCHARGE = ["linear_ops", "proof-observed", "DRE-77",
                 "the CEO answered via the console at 2026-10-06 09:12 PT: "
                 "Pressed it; the release shows v3."]
    RETURN = ["linear_ops", "advance", "DRE-77", "Hand-work", "Green Light"]

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
            self.DISCHARGE,
            self.RETURN,
        ], posted)

    def test_an_answered_card_still_in_green_light_returns_on_any_reason(self):
        # The return that died after its discharge record: the card is still
        # in Green Light, his answer still follows the park, and the next
        # dispatch carries some other reason. It finishes the return.
        for reason in ("second dispatch after a dead run",
                       "first run: the release carrying the epic is live", ""):
            done, posted, _ = self._run(reason=reason)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            self.assertEqual([self.DISCHARGE, self.RETURN],
                             [c for c in posted if c[1] in ("proof-observed", "advance")],
                             reason)
            self.calls.unlink(missing_ok=True)

    def test_an_unanswered_park_on_any_other_reason_does_nothing_and_says_so(self):
        for reason in ("", "second dispatch after a dead run",
                       f"not a {RETURN_REASON}"):
            done, posted, _ = self._run(reason=reason, answer="")
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual([["linear_ops", "state-of", "DRE-77"],
                              ["spoken_thread", "answer", "DRE-77"]], posted, reason)
            self.assertIn("nothing to return", done.stdout)
            self.calls.unlink(missing_ok=True)

    def test_any_other_lane_does_nothing_and_says_so(self):
        for lane in ("Hand-work", "In Review", "Triage", ""):
            for reason in (self.REASON, "second dispatch after a dead run"):
                done, posted, _ = self._run(lane=lane, reason=reason)
                self.assertEqual(0, done.returncode, done.stderr)
                self.assertEqual([["linear_ops", "state-of", "DRE-77"]], posted, lane)
                self.assertIn("nothing to return", done.stdout)
                self.calls.unlink(missing_ok=True)

    def test_an_unreadable_lane_or_answer_fails_before_any_write(self):
        for kwargs in ({"lane": "unreadable"}, {"answer": ""},
                       {"answer": "unreadable"},
                       {"answer": "unreadable", "reason": "second dispatch after a dead run"}):
            done, posted, _ = self._run(**kwargs)
            self.assertNotEqual(0, done.returncode, kwargs)
            self.assertEqual([], [c for c in posted if c[1] in ("proof-observed", "advance")],
                             kwargs)
            self.calls.unlink(missing_ok=True)

    def test_words_the_record_refuses_still_discharge_and_return(self):
        # `proof-observed` refuses the console's fix-budget hold markers; his
        # words carrying one must not leave the card parked.
        done, posted, _ = self._run(
            answer="2026-10-06 09:12 PT\tStop holding for a human and ship it.")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        writes = [c for c in posted if c[1] in ("proof-observed", "advance")]
        self.assertEqual(["proof-observed", "proof-observed", "advance"],
                         [c[1] for c in writes])
        self.assertEqual("the CEO answered via the console at 2026-10-06 09:12 PT "
                         "— his words are in that answer on this card", writes[1][3])
        self.assertEqual(self.RETURN, writes[2])


# --- the park's three lines (DRE-6174) ------------------------------------

#: The command the park runs on the agent's escalation file (DRE-3908's CLI),
#: and the `cat` it falls back to when the renderer cannot run.
COMPLETE = ('python3 .bureau-pipeline/scripts/console_escalation.py complete '
            '"$ESCALATION_FILE" --question "Make the press and say what you '
            'saw, or drop the criterion?" --who "the proof run"')
FALLBACK = 'cat "$ESCALATION_FILE"'
NONE_GIVEN = "none given — the proof run stated no recommendation"
WF_REL = ".github/workflows/proof-task.yml"
INTRODUCED_BY = "console_escalation.py complete"

#: An escalation in the proof brief's shape: one plain sentence naming the
#: press and the one decision asked, its recommendation in prose.
PROSE = ("The release row needs the CEO's press: approve the release in the "
         "console and say what you saw, or drop the criterion — I would make "
         "the press, since every other row is met.\n")


def _console_escalation():
    import importlib
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        return importlib.import_module("console_escalation")
    finally:
        sys.path.remove(str(ROOT / "scripts"))


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                             text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _before_and_after() -> tuple[str, str] | None:
    """proof-task.yml before and after this card: the commit that first
    brought `console_escalation.py complete` into it against its parent, or —
    before that commit exists — the working tree against its merge base with
    `main`."""
    log = _git("log", "--format=%H", "--reverse", "-S", INTRODUCED_BY, "--", WF_REL)
    first = (log or "").split()
    if first:
        before = _git("show", f"{first[0]}^:{WF_REL}")
        after = _git("show", f"{first[0]}:{WF_REL}")
        if before is not None and after is not None:
            return before, after
    base = (_git("merge-base", "HEAD", "origin/main") or "").strip()
    if base:
        before = _git("show", f"{base}:{WF_REL}")
        if before is not None:
            return before, PROOF_TASK.read_text(encoding="utf-8")
    return None


def _step_texts(src: str) -> list[tuple[str, str]]:
    """Every step of the file as `(name, its own lines)`, in order."""
    marker = "\n      - name: "
    chunks = src.split(marker)[1:]
    return [(c.split("\n", 1)[0], c) for c in chunks]


class ParkCompletesThreeLinesTest(unittest.TestCase):
    """The park's question carries the three Green Light lines (DRE-6174):
    the agent's file run through `console_escalation.py complete`, which
    leaves a declared file alone and finishes a prose one with `none given`."""

    def _run_lines(self) -> list[str]:
        return step_shell.step_shell(_step(RESULT_STEP)).split("\n")

    def _line(self, needle: str, start: int = 0) -> int:
        lines = self._run_lines()
        for i in range(start, len(lines)):
            if needle in lines[i]:
                return i
        self.fail(f"no line after {start} carries {needle!r}")

    def test_the_question_is_completed_inside_the_escalation_comment(self):
        lines = self._run_lines()
        preamble = self._line(f'echo "{PARK_RECEIPT}"')
        complete = self._line(INTRODUCED_BY)
        self.assertEqual(f"{COMPLETE} || {FALLBACK}", lines[complete].strip())
        self.assertIn('--who "the proof run"', lines[complete])
        # Inside the block whose output is the comment posted below.
        opened = max(i for i in range(preamble) if lines[i].strip() == "{")
        closed = self._line('} > "${RUNNER_TEMP:-/tmp}/proof-escalation-comment.md"',
                            complete)
        self.assertLess(opened, preamble)
        self.assertLess(preamble, complete)
        self.assertLess(complete, closed)
        comment = self._line('linear_ops.py comment "$CARD"', complete)
        park = self._line('advance "$CARD" "Green Light"', complete)
        self.assertLess(closed, comment)
        self.assertLess(comment, park)
        self.assertIn("proof-escalation-comment.md", lines[comment + 1])

    def test_cat_is_only_the_fallback(self):
        lines = [l for l in self._run_lines() if FALLBACK in l]
        self.assertEqual(1, len(lines), lines)
        self.assertTrue(lines[0].strip().endswith(f"|| {FALLBACK}"), lines[0])
        self.assertTrue(lines[0].strip().startswith(COMPLETE), lines[0])

    def _complete(self, text: str) -> str:
        """The `complete` half of the step's own line, run as written."""
        [line] = [l for l in self._run_lines() if INTRODUCED_BY in l]
        command = line.strip().split(f" || {FALLBACK}")[0]
        self.assertEqual(COMPLETE, command)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        scripts = tmp / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy(ROOT / "scripts" / "console_escalation.py", scripts)
        staged = tmp / "agent-escalation.txt"
        staged.write_text(text, encoding="utf-8")
        done = subprocess.run(["bash", "-e", "-c", command], cwd=tmp,
                              env={"PATH": os.environ["PATH"],
                                   "ESCALATION_FILE": str(staged)},
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        return done.stdout

    def test_a_prose_escalation_ends_with_the_three_lines_none_given(self):
        ce = _console_escalation()
        out = self._complete(PROSE)
        self.assertTrue(out.startswith(PROSE.rstrip("\n")), out)
        last = out.rstrip("\n").split("\n")[-3:]
        for line, prefix in zip(last, (ce.FINDING_PREFIX, ce.QUESTION_PREFIX,
                                       ce.RECOMMENDATION_PREFIX)):
            self.assertTrue(line.startswith(prefix), last)
        self.assertEqual([], ce.problems("\n".join(last)))
        self.assertEqual([], ce.problems(out))
        self.assertEqual(f"{ce.RECOMMENDATION_PREFIX} {NONE_GIVEN}", last[2])
        self.assertEqual(f"{ce.QUESTION_PREFIX} Make the press and say what you "
                         "saw, or drop the criterion?", last[1])

    def test_a_declared_escalation_comes_back_byte_identical(self):
        ce = _console_escalation()
        declared = PROSE + "\n" + ce.render(ce.Escalation(
            finding="The release row needs the CEO's press",
            question="Approve the release in the console, or drop the criterion?",
            recommendation="Approve it",
            why="every other row is met"))
        self.assertEqual([], ce.problems(declared))
        # The file's own bytes, and the newline `print` ends them with.
        self.assertEqual(declared + "\n", self._complete(declared))

    def test_the_diff_touches_no_other_step(self):
        pair = _before_and_after()
        if pair is None:
            # The unit job checks out full history and sets this, so there the
            # missing history is a failure, never a green skip.
            if os.environ.get("BUREAU_REQUIRE_GIT_HISTORY"):
                self.fail(f"no git history for {WF_REL}, and this job requires it")
            self.skipTest(f"no git history for {WF_REL} (a shallow checkout)")
        before, after = pair
        was, now = _step_texts(before), _step_texts(after)
        self.assertEqual([n for n, _ in was], [n for n, _ in now])
        agents = 0
        for (name, old), (_, new) in zip(was, now):
            if name != RESULT_STEP:
                self.assertEqual(old, new, f"the step {name!r} changed")
                agents += f"uses: {ACTION}@" in new
        self.assertEqual(3, agents, "the three agent attempts are compared")
        self.assertEqual(before.split("\n")[0], after.split("\n")[0])
        self.assertEqual(before.split("\n      - name: ")[0],
                         after.split("\n      - name: ")[0])
        # Within the step, the one line and nothing else.
        old_lines, new_lines = before.split("\n"), after.split("\n")
        changed = [(o, n) for o, n in zip(old_lines, new_lines) if o != n]
        self.assertEqual(len(old_lines), len(new_lines))
        self.assertEqual([(f"              {FALLBACK}",
                           f"              {COMPLETE} || {FALLBACK}")], changed)


class ParkStepThreeLinesTest(_StepHarness):
    """The result step executed with the real renderer beside it, and with
    one that cannot run: the park is never lost to the renderer."""

    STEP = RESULT_STEP

    def _park(self, escalation: str) -> tuple[str, list]:
        self.escalation.write_text(escalation, encoding="utf-8")
        done, posted, _ = self._exec({
            "FAKE_PR": "", "CARD": "DRE-77", "CARD_DESCRIPTION": DESCRIPTION,
            "RECORD_BRANCH": "agent/DRE-77-proof-record",
            "ESCALATION_FILE": str(self.escalation),
            "BUREAU_SERVER_URL": "https://github.example",
            "BUREAU_REPOSITORY": "acme/widgets", "BUREAU_RUN_ID": "42",
            "CLAUDE_EXECUTION_FILE": ""})
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted])
        self.assertEqual(["Green Light", "Hand-work,In Review"], posted[2][3:])
        return posted[1][3], posted

    def test_a_prose_escalation_parks_with_none_given(self):
        shutil.copy(ROOT / "scripts" / "console_escalation.py",
                    self.tmp / ".bureau-pipeline" / "scripts")
        ce = _console_escalation()
        ask, _ = self._park(PROSE)
        self.assertTrue(ask.startswith(PARK_RECEIPT), ask)
        self.assertIn(PROSE.rstrip("\n"), ask)
        self.assertTrue(ask.endswith(f"Run: {RUN_URL}"), ask)
        got = ce.parse(ask)
        self.assertIsNotNone(got, ask)
        self.assertIsNone(got.recommendation)
        self.assertIn(f"{ce.RECOMMENDATION_PREFIX} {NONE_GIVEN}\n", ask)
        self.assertEqual([], ce.problems(ask))

    def test_a_renderer_that_cannot_run_still_parks_the_file_verbatim(self):
        (self.tmp / ".bureau-pipeline" / "scripts" / "console_escalation.py").write_text(
            "import sys\nsys.exit('console_escalation: broken')\n")
        ask, _ = self._park(PROSE)
        self.assertEqual(f"{PARK_RECEIPT}\n\n{PROSE}\nRun: {RUN_URL}", ask)
        self.assertNotIn(_console_escalation().RECOMMENDATION_PREFIX, ask)


IDENTITY_STEP = "Set the git identity every proof run commits as"
#: The identity claude-code-action writes into the checkout's own config —
#: its `bot_name` and `bot_id` defaults — and the first run's commits carry.
BOT_NAME = "claude[bot]"
BOT_EMAIL = "41898282+claude[bot]@users.noreply.github.com"


class GitIdentityTest(unittest.TestCase):
    """DRE-6516: a re-run's merge-of-main commit was authored `x <x@x>`
    (agent-bureau#3458, `2db90c35a`) where the first re-run's was
    `claude[bot]`. The action sets that identity in the checkout's local
    config and logs and carries on when it cannot, so any git the agent runs
    outside that config commits as whoever it improvises. The workflow sets
    the same identity for the whole job, before every attempt. EXECUTED: the
    step's own script runs, then a merge of main in a repository that is not
    the checkout."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.tmp / "home"),
                    "GIT_CONFIG_NOSYSTEM": "1"}
        (self.tmp / "home").mkdir()

    def _git(self, cwd: Path, *args: str) -> str:
        done = subprocess.run(["git", *args], cwd=cwd, env=self.env,
                              capture_output=True, text=True)
        self.assertEqual(0, done.returncode, done.stderr)
        return done.stdout.strip()

    def test_the_step_comes_before_every_agent_attempt(self):
        at = _index(IDENTITY_STEP)
        for i, step in _agent_steps():
            self.assertLess(at, i, step.get("name"))
        self.assertNotIn("if", _step(IDENTITY_STEP),
                         "every run, the first and every re-run, sets it")

    def test_a_merge_of_main_is_authored_as_the_first_run_commits(self):
        work = self.tmp / "work"
        work.mkdir()
        done = subprocess.run(["bash", "-e", "-c", _step(IDENTITY_STEP)["run"]],
                              cwd=work, env=self.env, capture_output=True, text=True)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

        repo = self.tmp / "clone"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        (repo / "a").write_text("a\n")
        self._git(repo, "add", "a")
        self._git(repo, "commit", "-q", "-m", "base")
        self._git(repo, "checkout", "-q", "-b", "agent/DRE-1-proof-record")
        (repo / "record.md").write_text("record\n")
        self._git(repo, "add", "record.md")
        self._git(repo, "commit", "-q", "-m", "record")
        self._git(repo, "checkout", "-q", "main")
        (repo / "b").write_text("b\n")
        self._git(repo, "add", "b")
        self._git(repo, "commit", "-q", "-m", "main moved")
        self._git(repo, "checkout", "-q", "agent/DRE-1-proof-record")
        self._git(repo, "merge", "-q", "--no-edit", "main")

        self.assertEqual("1", self._git(repo, "rev-list", "--count", "--merges", "HEAD"))
        self.assertEqual(f"{BOT_NAME} <{BOT_EMAIL}>",
                         self._git(repo, "log", "-1", "--format=%an <%ae>"))
        self.assertEqual(f"{BOT_NAME} <{BOT_EMAIL}>",
                         self._git(repo, "log", "-1", "--format=%cn <%ce>"))


IDENTITY_RECORD_STEP = "Record the caller's proof identity"
INSTALL_STEP = "Install Claude Code (asserted)"
#: The four steps before the agent, in this order (DRE-6027).
LOCAL_BEFORE = ("local_decl", "local_node", "local_node_setup", "local")
DECLARED = "steps.local_decl.outputs.declared == 'true'"
NODE_DIR = "steps.local_decl.outputs.node_dir != ''"
#: The agent's seven `PROOF_*` names, read off the `local` step's outputs.
LOCAL_ENV = {
    "PROOF_LOCAL_STATUS": "${{ steps.local.outputs.status || 'none' }}",
    "PROOF_LOCAL_NOTE": "${{ steps.local.outputs.note }}",
    "PROOF_LOCAL_URL": "${{ steps.local.outputs.url }}",
    "PROOF_LOCAL_COMMIT": "${{ steps.local.outputs.commit }}",
    "PROOF_LOCAL_TAG": "${{ steps.local.outputs.tag }}",
    "PROOF_PYTHON": "${{ steps.local.outputs.python || '' }}",
    "PROOF_LOGIN_FILE": "${{ runner.temp }}/proof-login.json",
}


def _by_id(step_id: str) -> tuple[int, dict]:
    for i, step in enumerate(_steps()):
        if step.get("id") == step_id:
            return i, step
    raise AssertionError(f"proof-task.yml has no step with id {step_id!r}")


class LocalRunStepsTest(unittest.TestCase):
    """DRE-6027: the workflow stands the declared local run up before the
    agent and tears it down after. The logic is the sibling modules'
    (proof_local.py, proof_browser.py, proof_session.py); these are the five
    steps that call them, the agent's env and the prompt's bullet."""

    def test_four_steps_between_the_aws_record_and_the_install_in_order(self):
        after = _index(IDENTITY_RECORD_STEP)
        before = _index(INSTALL_STEP)
        at = [_by_id(step_id)[0] for step_id in LOCAL_BEFORE]
        self.assertEqual(sorted(at), at, "local_decl, local_node, local_node_setup, local")
        self.assertLess(after, at[0], "the local run holds the AWS session")
        self.assertLess(at[-1], before, "the agent finds the local run ready")
        self.assertEqual(list(range(after + 1, after + 5)), at, "nothing between them")

    def test_the_declaration_is_read_on_every_run(self):
        _i, step = _by_id("local_decl")
        self.assertEqual("Read the local-run declaration", step["name"])
        self.assertNotIn("if", step, "a repo with no file writes declared=false")
        self.assertIn('python3 .bureau-pipeline/scripts/proof_local.py read '
                      '--github-output "$GITHUB_OUTPUT"', step["run"])
        # An invalid declaration exits 1; it costs the Local screen: rows,
        # never the run, and `prepare` names it (below).
        self.assertIs(step.get("continue-on-error"), True)

    def test_the_node_steps_are_qa_reviews_copied_and_gated_on_a_node_dir(self):
        _i, resolve = _by_id("local_node")
        self.assertEqual("Resolve the consumer's node for the local run", resolve["name"])
        _i, setup = _by_id("local_node_setup")
        self.assertEqual("Set up the consumer's node for the local run", setup["name"])
        for step in (resolve, setup):
            self.assertIn(DECLARED, step["if"])
            self.assertIn(NODE_DIR, step["if"])
            self.assertIs(step.get("continue-on-error"), True)
        self.assertIn("python3 .bureau-pipeline/scripts/consumer_node.py", resolve["run"])
        self.assertIn('"$NODE_DIR"', resolve["run"])
        self.assertEqual("${{ steps.local_decl.outputs.node_dir }}",
                         resolve["env"]["NODE_DIR"])
        self.assertEqual("./.bureau-pipeline/.github/actions/setup-node-cached",
                         setup["uses"])
        self.assertIn("steps.local_node.outputs.setup == 'true'", setup["if"])
        self.assertEqual("${{ steps.local_decl.outputs.node_dir }}",
                         setup["with"]["working-directory"])

    def test_prepare_stands_the_run_up_and_never_fails_the_job(self):
        _i, step = _by_id("local")
        self.assertEqual("Stand up a local run of the released commit", step["name"])
        self.assertIn(DECLARED, step["if"])
        self.assertIn("steps.local_decl.outcome == 'failure'", step["if"],
                      "an invalid declaration reaches prepare, which names it")
        self.assertIs(step.get("continue-on-error"), True)
        self.assertIn('python3 .bureau-pipeline/scripts/proof_browser.py prepare '
                      '--github-output "$GITHUB_OUTPUT" --summary "$GITHUB_STEP_SUMMARY"',
                      step["run"])

    def test_stop_runs_after_the_result_step_always_with_no_env(self):
        at, step = _by_id("local_stop")
        self.assertEqual("Stop the local run", step["name"])
        self.assertLess(_index(RESULT_STEP), at)
        self.assertLess(at, _index("Fail if the agent step failed"))
        self.assertEqual("always()", step["if"])
        self.assertNotIn("env", step, "PROOF_LOGIN_FILE is the module's own default")
        self.assertEqual("python3 .bureau-pipeline/scripts/proof_browser.py stop",
                         step["run"].strip())

    def test_every_expression_is_in_env_never_in_run(self):
        for step_id in (*LOCAL_BEFORE, "local_stop"):
            _i, step = _by_id(step_id)
            self.assertNotIn("${{", str(step.get("run") or ""), step_id)

    def test_no_setup_python_and_the_steps_run_the_runners_python3(self):
        for step in _steps():
            uses = str(step.get("uses") or "")
            self.assertNotIn("actions/setup-python", uses, step.get("name"))
            self.assertNotIn("setup-python-cached", uses, step.get("name"))
        for step_id in (*LOCAL_BEFORE, "local_stop"):
            _i, step = _by_id(step_id)
            run = str(step.get("run") or "")
            if not run:
                continue
            self.assertRegex(run, r"(^|\s)python3 \.bureau-pipeline/scripts/", step_id)
            # No interpreter of its own: never the venv, never a path.
            self.assertNotRegex(run, r"PROOF_PYTHON|/bin/python|proof-venv", step_id)

    def test_the_agent_env_carries_the_seven_names_on_every_attempt(self):
        for _i, step in _agent_steps():
            env = step.get("env") or {}
            for name, value in LOCAL_ENV.items():
                self.assertEqual(value, env.get(name), f"{step['name']}: {name}")

    def test_the_prompt_says_how_a_local_screen_row_is_observed(self):
        prompt = " ".join(_agent_steps()[0][1]["with"]["prompt"].split())
        for phrase in (
            "PROOF_LOCAL_STATUS",
            "`Not observed. no local run declared`",
            "`Not observed. the local run did not start: $PROOF_LOCAL_NOTE`",
            '`"$PROOF_PYTHON" <script>`',
            "A `Local screen:` row is never inferred from a `Live request:` row.",
            "proof_session.py login",
            "screenshot --signed-in",
            "never open, print, copy or quote the login file at $PROOF_LOGIN_FILE, "
            "the browser state file or anything under $RUNNER_TEMP/proof-venv",
            "the password is never in the record, the pull request or a comment",
            "row for row as `<method> <origin and path> <status>`",
            "the sidecar itself is never committed",
        ):
            self.assertIn(phrase, prompt)
        # `login` once, then the signed-in screenshot — never the other order.
        self.assertLess(prompt.index("proof_session.py login"),
                        prompt.index("screenshot --signed-in"))


class LocalRunStepExecutedTest(unittest.TestCase):
    """The read and stop steps' own scripts, run as written against the real
    modules: a repo with no declaration writes `declared=false`, and `stop`
    leaves no login file behind."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / ".bureau-pipeline").symlink_to(ROOT)
        self.output = self.tmp / "output"
        self.output.write_text("")
        self.env = {"PATH": os.environ["PATH"], "RUNNER_TEMP": str(self.tmp),
                    "GITHUB_OUTPUT": str(self.output),
                    "GITHUB_STEP_SUMMARY": str(self.tmp / "summary.md")}

    def _run(self, step_id: str) -> subprocess.CompletedProcess:
        _i, step = _by_id(step_id)
        return subprocess.run(["bash", "-e", "-c", step["run"]], cwd=self.tmp,
                              env=self.env, capture_output=True, text=True, timeout=60)

    def test_a_repo_with_no_declaration_writes_declared_false(self):
        done = self._run("local_decl")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("declared=false", self.output.read_text().splitlines())

    def test_stop_deletes_a_login_file_left_at_the_agents_path(self):
        # The agent's PROOF_LOGIN_FILE is `${{ runner.temp }}/proof-login.json`;
        # stop carries no env, so it must find the file at its own default.
        path = LOCAL_ENV["PROOF_LOGIN_FILE"].replace("${{ runner.temp }}", str(self.tmp))
        Path(path).write_text('{"password": "x"}')
        done = self._run("local_stop")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertFalse(Path(path).exists(), "the login file outlived the run")


if __name__ == "__main__":
    unittest.main()
