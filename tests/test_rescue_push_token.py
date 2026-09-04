"""The rescue push must not depend on the credential the failure invalidated
(DRE-3098).

THE INCIDENT (2026-09-04 10:03 PT, bureau-pipeline run 33896126776, card
DRE-3088, its third turn-cap death). The run was 26 minutes old — nowhere near
the hour DRE-3043's re-mint guards against — and the rescue push still got:

    push rescue: pushing agent/DRE-3088-second-hold-parks failed — fatal:
      unable to access 'https://github.com/dreadnought-foundry/bureau-pipeline.git/':
      The requested URL returned error: 400

A 400 on `git push` over HTTPS is a REJECTED CREDENTIAL, not an expiry. The
step had fallen back to `steps.worker.outputs.token` — the token the agent step
held, which the very failure being rescued from may have invalidated — and the
work ("3/5 implementation green", the tests and the edits) stayed on the runner
and was destroyed with it. The same happened on the two runs before it, so no
branch ever appeared for `agent/DRE-3088-*`.

The rescue push is the one push whose whole purpose is to run after something
has already gone wrong. Three things follow, and each is pinned below:

  1. ITS OWN CREDENTIAL — the token it pushes with is minted for the rescue,
     after the agent step, and the agent's token is never a fallback.
  2. ONE RETRY ON A FRESH MINT — a refused push prints the HTTP status and the
     token's source, re-points git at a second, independently minted
     credential, and pushes again. Once, never in a loop.
  3. THE WORK IS NEVER ONLY ON THE RUNNER — when both attempts are refused the
     branch's commits are written out as `rescue-<card>.patch` and uploaded as
     a run artifact, so the runner's disk is not the last copy.

These must FAIL before the mechanism exists and PASS after.
tests/test_credential_expiry.py owns DRE-3043's half of the same step.

Run: python3 -m pytest tests/test_rescue_push_token.py -v
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dead_run  # noqa: E402
import push_rescue  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "agent-task.yml"

CARD = "DRE-3098"
BRANCH = f"agent/{CARD}-rescue-push-own-token"
REPO = "dreadnought-foundry/bureau-pipeline"

# The three credentials the incident is about: the one the AGENT step held (and
# the rescue must never touch), and the two the rescue mints for itself.
AGENT_TOKEN = "ghs_the_token_the_agent_step_held"
MINT_1 = "ghs_minted_by_the_rescue_step"
MINT_2 = "ghs_minted_again_for_the_retry"
SOURCE_1 = "rescue-step mint"
SOURCE_2 = "rescue-step re-mint"

# What GitHub answered on run 33896126776, verbatim.
REFUSED_400 = (
    "fatal: unable to access "
    "'https://github.com/dreadnought-foundry/bureau-pipeline.git/': "
    "The requested URL returned error: 400"
)


# ── the fake world ───────────────────────────────────────────────────────────
class FakeGit:
    """A `run` stand-in: records every argv/env and answers from a script of
    push outcomes. Nothing here shells out.

    `push_results` is read one per push attempt — `(returncode, stderr)` — and
    the last entry repeats, so a one-element list is "always this".
    """

    def __init__(self, *, remote_sha="", ahead=3, push_results=((0, ""),),
                 patch="diff --git a/f.py b/f.py\n+value = 1\n", prs=()):
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []
        self.remote_sha = remote_sha
        self.ahead = ahead
        self.push_results = list(push_results)
        self.patch = patch
        self.prs = list(prs)
        self.pushes = 0

    def __call__(self, argv, *, cwd=None, env=None):
        self.calls.append(list(argv))
        self.envs.append(dict(env or {}))
        rest = [a for a in argv[1:] if a not in ("-C", cwd)]
        if argv[0] == "gh":
            import json
            if "list" in argv:
                return 0, json.dumps(self.prs), ""
            return 0, "https://github.com/x/y/pull/260\n", ""
        if "for-each-ref" in rest:
            return 0, f"{BRANCH}\n", ""
        if "rev-parse" in rest:
            return 0, "aaaa111\n", ""
        if "ls-remote" in rest:
            if not self.remote_sha:
                return 0, "", ""
            return 0, f"{self.remote_sha}\trefs/heads/{BRANCH}\n", ""
        if "rev-list" in rest:
            return 0, f"{self.ahead}\n", ""
        if "push" in rest:
            i = min(self.pushes, len(self.push_results) - 1)
            self.pushes += 1
            code, err = self.push_results[i]
            return code, "", err
        if "format-patch" in rest:
            return (0, self.patch, "") if self.patch else (128, "", "no patch")
        if "diff" in rest:
            return (0, self.patch, "") if self.patch else (128, "", "no diff")
        if "config" in rest or "fetch" in rest:
            return 0, "", ""
        raise AssertionError(f"FakeGit has no answer for: {' '.join(argv)}")

    def argv_containing(self, *needles) -> list[list[str]]:
        return [c for c in self.calls if all(n in c for n in needles)]

    def credential_writes(self) -> list[str]:
        """Every token the git credential was re-pointed at, in order."""
        out = []
        for call in self.calls:
            if "config" in call and push_rescue.GITHUB_EXTRAHEADER in call \
                    and "--unset-all" not in call:
                out.append(call[-1])
        return out


def _header(token: str) -> str:
    return "AUTHORIZATION: basic " + base64.b64encode(
        f"x-access-token:{token}".encode()).decode()


def _rescue(fake, logged=None, **kw):
    kw.setdefault("base", "main")
    kw.setdefault("card_url", f"https://linear.app/x/issue/{CARD}")
    kw.setdefault("card_title", "the rescue push after a dead agent")
    kw.setdefault("retry_token", MINT_2)
    kw.setdefault("token_source", SOURCE_1)
    kw.setdefault("retry_token_source", SOURCE_2)
    return push_rescue.rescue(
        CARD, REPO, MINT_1, run=fake,
        log=(logged.append if logged is not None else (lambda *_: None)), **kw
    )


# ── the workflow ─────────────────────────────────────────────────────────────
def _steps() -> list[dict]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["execute"]["steps"]


def _index(name: str) -> int:
    for i, step in enumerate(_steps()):
        if str(step.get("name") or "").startswith(name):
            return i
    raise AssertionError(f"agent-task.yml has no step named {name!r}")


def _step(name: str) -> dict:
    return _steps()[_index(name)]


def _by_id(step_id: str) -> dict:
    for step in _steps():
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"agent-task.yml has no step with id {step_id!r}")


def _index_of_id(step_id: str) -> int:
    for i, step in enumerate(_steps()):
        if step.get("id") == step_id:
            return i
    raise AssertionError(f"agent-task.yml has no step with id {step_id!r}")


def _token_step_ids(expression: str) -> list[str]:
    """The step ids an Actions expression takes a token from."""
    return re.findall(r"steps\.([A-Za-z0-9_-]+)\.outputs\.token", expression)


class TheRescueMintsItsOwnCredential(unittest.TestCase):
    """AC2: the rescue push's token is minted for the rescue, read out of the
    workflow file. An Actions `run:` step cannot mint an App token itself —
    `create-github-app-token` is an action — so "minted in the rescue step"
    is checked as: every credential the push step spends comes from a mint
    that runs AFTER the agent, and the agent's own token is not among them."""

    def test_the_agents_token_is_never_the_rescue_pushs_credential(self):
        # The regression, exactly: `PUSH_TOKEN: ${{ steps.pushtoken.outputs.token
        # || steps.worker.outputs.token }}` fell back to the credential the
        # agent step held, and run 33896126776 pushed with it and got a 400.
        env = _step("Push rescue")["env"]
        for key, value in env.items():
            self.assertNotIn(
                "steps.worker", str(value),
                f"Push rescue's {key} may not fall back to the agent's token",
            )

    def test_every_credential_it_spends_is_minted_after_the_agent(self):
        env = _step("Push rescue")["env"]
        ids = _token_step_ids(str(env.get("PUSH_TOKEN", "")))
        ids += _token_step_ids(str(env.get("PUSH_TOKEN_RETRY", "")))
        self.assertTrue(ids, env)
        for step_id in ids:
            step = _by_id(step_id)
            self.assertIn("actions/create-github-app-token", str(step.get("uses")))
            self.assertGreater(_index_of_id(step_id), _index("Implement card"))
            self.assertLess(_index_of_id(step_id), _index("Push rescue"))

    def test_the_retry_credential_is_a_second_independent_mint(self):
        env = _step("Push rescue")["env"]
        first = _token_step_ids(str(env.get("PUSH_TOKEN", "")))
        retry = _token_step_ids(str(env.get("PUSH_TOKEN_RETRY", "")))
        self.assertEqual(len(first), 1, env)
        self.assertEqual(len(retry), 1, env)
        self.assertNotEqual(first[0], retry[0], "the retry reuses one token")

    def test_both_mints_come_from_the_same_pool_app_and_cannot_fail_the_build(self):
        env = _step("Push rescue")["env"]
        ids = _token_step_ids(str(env.get("PUSH_TOKEN", ""))) + \
            _token_step_ids(str(env.get("PUSH_TOKEN_RETRY", "")))
        for step_id in ids:
            step = _by_id(step_id)
            self.assertIn("secrets.BUREAU_APP_ID", step["with"]["app-id"])
            self.assertIn("secrets.BUREAU_APP_PRIVATE_KEY",
                          step["with"]["private-key"])
            self.assertTrue(step.get("continue-on-error"), step_id)
            self.assertTrue(str(step.get("if", "")).startswith("always()"), step_id)

    def test_the_step_tells_the_script_where_each_credential_came_from(self):
        # "prints the token's source" is only possible if the step says which
        # mint each token is.
        env = _step("Push rescue")["env"]
        self.assertTrue(str(env.get("PUSH_TOKEN_SOURCE", "")).strip(), env)
        self.assertTrue(str(env.get("PUSH_TOKEN_RETRY_SOURCE", "")).strip(), env)


class TheRunnerIsNeverTheLastCopy(unittest.TestCase):
    """AC1, the workflow half: a refused rescue leaves an artifact behind."""

    def test_the_patch_is_uploaded_as_a_run_artifact(self):
        step = _step("Upload rescued work")
        self.assertIn("actions/upload-artifact", str(step["uses"]))
        self.assertGreater(_index("Upload rescued work"), _index("Push rescue"))
        name = str(step["with"]["name"])
        self.assertIn("rescue-", name)
        self.assertIn(".patch", name)
        self.assertIn("client_payload.identifier", name)
        self.assertIn("steps.rescue.outputs.patch", str(step["with"]["path"]))

    def test_it_uploads_only_when_the_rescue_wrote_one(self):
        condition = str(_step("Upload rescued work").get("if", ""))
        self.assertTrue(condition.startswith("always()"), condition)
        self.assertIn("steps.rescue.outputs.patch", condition)

    def test_the_receipt_is_told_the_status_and_the_artifact(self):
        # The Linear note a refused rescue produces must name what GitHub
        # said and where the work went — "work still on the runner" with no
        # HTTP status sent three runs' readers to the model and the card.
        run = _step("Report result to Linear")["run"]
        self.assertIn("--push-status", run)
        self.assertIn("--artifact", run)
        env = _step("Report result to Linear")["env"]
        joined = " ".join(str(v) for v in env.values())
        self.assertIn("steps.rescue.outputs.push_status", joined)
        self.assertIn("steps.rescue.outputs.patch", joined)


# ── the retry ────────────────────────────────────────────────────────────────
class OneRefusedPushRetriesOnAFreshMint(unittest.TestCase):

    def test_a_push_that_fails_once_then_succeeds_is_delivered(self):
        fake = FakeGit(push_results=[(128, REFUSED_400), (0, "")])
        out = _rescue(fake)
        self.assertTrue(out.pushed, fake.calls)
        self.assertEqual(fake.pushes, 2)
        self.assertEqual(out.attempts, 2)
        self.assertFalse(out.patch)

    def test_the_retry_pushes_with_the_second_mint_not_the_first(self):
        fake = FakeGit(push_results=[(128, REFUSED_400), (0, "")])
        _rescue(fake)
        self.assertEqual(
            fake.credential_writes(), [_header(MINT_1), _header(MINT_2)],
            fake.calls,
        )

    def test_the_re_point_happens_before_the_second_push(self):
        fake = FakeGit(push_results=[(128, REFUSED_400), (0, "")])
        _rescue(fake)
        pushes = [i for i, c in enumerate(fake.calls) if "push" in c]
        rewrite = [
            i for i, c in enumerate(fake.calls)
            if "config" in c and push_rescue.GITHUB_EXTRAHEADER in c
            and "--unset-all" not in c
        ][1]
        self.assertLess(pushes[0], rewrite)
        self.assertLess(rewrite, pushes[1])

    def test_gh_is_handed_the_credential_that_actually_worked(self):
        # The PR is opened after the push. Handing `gh` the token git just
        # got a 400 from re-runs the incident one call further on.
        fake = FakeGit(push_results=[(128, REFUSED_400), (0, "")])
        _rescue(fake)
        gh_envs = [e for c, e in zip(fake.calls, fake.envs) if c[0] == "gh"]
        self.assertTrue(gh_envs)
        for env in gh_envs:
            self.assertEqual(env.get("GH_TOKEN"), MINT_2)

    def test_it_retries_once_and_not_in_a_loop(self):
        fake = FakeGit(push_results=[(128, REFUSED_400)])
        out = _rescue(fake)
        self.assertEqual(fake.pushes, 2, "exactly one retry")
        self.assertFalse(out.pushed)
        self.assertEqual(out.attempts, 2)

    def test_a_first_push_that_works_never_re_mints(self):
        fake = FakeGit(push_results=[(0, "")])
        out = _rescue(fake)
        self.assertEqual(fake.pushes, 1)
        self.assertEqual(out.attempts, 1)
        self.assertEqual(fake.credential_writes(), [_header(MINT_1)])

    def test_no_retry_credential_means_one_attempt_not_a_crash(self):
        # Both mints are continue-on-error; the second can be empty.
        fake = FakeGit(push_results=[(128, REFUSED_400)])
        out = _rescue(fake, retry_token="")
        self.assertEqual(fake.pushes, 1)
        self.assertTrue(out.local_work)
        self.assertTrue(out.patch)

    def test_the_same_token_twice_is_not_a_retry(self):
        # A re-mint that quietly returned the same credential is not a fresh
        # one, and pushing with it again only doubles the failure.
        fake = FakeGit(push_results=[(128, REFUSED_400)])
        out = _rescue(fake, retry_token=MINT_1)
        self.assertEqual(fake.pushes, 1)
        self.assertEqual(out.attempts, 1)


class ARefusedRescueSaysWhatGithubSaid(unittest.TestCase):

    def test_the_http_status_is_read_off_githubs_own_words(self):
        self.assertEqual(push_rescue.http_status(REFUSED_400), "400")
        self.assertEqual(
            push_rescue.http_status("gh: Bad credentials (HTTP 401)"), "401")
        self.assertEqual(
            push_rescue.http_status("remote: error: 403 Forbidden"), "403")
        self.assertEqual(push_rescue.http_status(""), "")
        self.assertEqual(
            push_rescue.http_status("fatal: Authentication failed"), "")

    def test_a_version_number_is_not_an_http_status(self):
        self.assertEqual(push_rescue.http_status("fatal: git 2.43 exploded"), "")

    def test_the_status_and_the_source_are_printed_for_every_attempt(self):
        logged: list[str] = []
        fake = FakeGit(push_results=[(128, REFUSED_400)])
        _rescue(fake, logged=logged)
        text = "\n".join(logged)
        self.assertEqual(text.count("HTTP 400"), 2, text)
        self.assertIn(SOURCE_1, text)
        self.assertIn(SOURCE_2, text)

    def test_the_status_reaches_the_step_outputs(self):
        fake = FakeGit(push_results=[(128, REFUSED_400)])
        out = _rescue(fake)
        self.assertEqual(out.push_status, "400")
        lines = push_rescue.output_lines(out)
        self.assertIn("push_status=400", lines)
        self.assertIn("attempts=2", lines)
        for line in lines:
            self.assertRegex(line, r"^[a-z_]+=[^\n]*$")

    def test_a_delivered_push_reports_no_status_and_no_error(self):
        fake = FakeGit(push_results=[(128, REFUSED_400), (0, "")])
        out = _rescue(fake)
        self.assertEqual(out.push_status, "")
        self.assertEqual(out.error, "")

    def test_it_still_says_the_work_is_on_the_runner(self):
        logged: list[str] = []
        fake = FakeGit(push_results=[(128, REFUSED_400)])
        out = _rescue(fake, logged=logged)
        self.assertTrue(out.local_work)
        self.assertIn("still on the runner", "\n".join(logged))

    def test_no_token_ever_reaches_a_log_line(self):
        logged: list[str] = []
        fake = FakeGit(push_results=[(128, REFUSED_400)])
        _rescue(fake, logged=logged)
        self.assertTrue(logged)
        for line in logged:
            self.assertNotIn(MINT_1, line)
            self.assertNotIn(MINT_2, line)


class TheWorkIsWrittenOutBeforeTheRunnerDies(unittest.TestCase):

    def test_two_refusals_write_the_branchs_commits_to_a_patch(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, f"rescue-{CARD}.patch")
            fake = FakeGit(push_results=[(128, REFUSED_400)])
            out = _rescue(fake, patch_path=path)
            self.assertEqual(out.patch, path)
            self.assertIn("diff --git", Path(path).read_text(encoding="utf-8"))
            self.assertIn(f"patch={path}", push_rescue.output_lines(out))

    def test_the_patch_names_the_card_by_default(self):
        self.assertTrue(
            push_rescue.default_patch_path(CARD).endswith(f"rescue-{CARD}.patch"),
            push_rescue.default_patch_path(CARD),
        )

    def test_a_delivered_branch_writes_no_patch(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, f"rescue-{CARD}.patch")
            fake = FakeGit(push_results=[(0, "")])
            out = _rescue(fake, patch_path=path)
            self.assertEqual(out.patch, "")
            self.assertFalse(os.path.exists(path))

    def test_a_branch_already_on_github_writes_no_patch(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, f"rescue-{CARD}.patch")
            fake = FakeGit(remote_sha="aaaa111")
            out = _rescue(fake, patch_path=path)
            self.assertFalse(out.local_work)
            self.assertEqual(out.patch, "")

    def test_no_credential_at_all_still_preserves_the_work(self):
        # Both mints are continue-on-error. With neither, nothing can be
        # pushed — which is precisely when the runner must not be the last
        # copy.
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, f"rescue-{CARD}.patch")
            fake = FakeGit()
            out = push_rescue.rescue(
                CARD, REPO, "", retry_token="", patch_path=path,
                run=fake, log=lambda *_: None,
            )
            self.assertTrue(out.local_work)
            self.assertTrue(out.patch)
            self.assertEqual(fake.argv_containing("push"), [])
            self.assertEqual(fake.credential_writes(), [])

    def test_an_agent_that_chose_a_different_exit_writes_no_patch(self):
        with tempfile.TemporaryDirectory() as td:
            note = os.path.join(td, "agent-blocker.txt")
            Path(note).write_text("stopped on purpose\n", encoding="utf-8")
            path = os.path.join(td, f"rescue-{CARD}.patch")
            fake = FakeGit(push_results=[(128, REFUSED_400)])
            out = push_rescue.rescue(
                CARD, REPO, MINT_1, retry_token=MINT_2, patch_path=path,
                run=fake, log=lambda *_: None, stop_notes=(note,),
            )
            self.assertEqual(out.patch, "")
            self.assertEqual(fake.calls, [])

    def test_the_patch_is_a_real_git_patch_of_the_branchs_commits(self):
        """The fake proves the plumbing; this proves the argv is right."""
        git = subprocess.run(["git", "--version"], capture_output=True)
        self.assertEqual(git.returncode, 0)
        with tempfile.TemporaryDirectory() as td:
            def g(*args):
                subprocess.run(["git", "-C", td, *args], check=True,
                               capture_output=True)
            g("init", "-q", "-b", "main")
            g("config", "user.email", "bot@example.com")
            g("config", "user.name", "agent-bureau-bot")
            Path(td, "README.md").write_text("base\n", encoding="utf-8")
            g("add", "-A")
            g("commit", "-qm", "base")
            g("checkout", "-q", "-b", BRANCH)
            Path(td, "feature.py").write_text("value = 1\n", encoding="utf-8")
            g("add", "-A")
            g("commit", "-qm", f"feat({CARD}): the work that never left the runner")

            path = os.path.join(td, f"rescue-{CARD}.patch")
            written = push_rescue.write_patch(
                BRANCH, "main", path, run=push_rescue._subprocess_run, workdir=td
            )
            self.assertEqual(written, path)
            text = Path(path).read_text(encoding="utf-8")
            self.assertIn("feature.py", text)
            self.assertIn("value = 1", text)
            self.assertIn(f"feat({CARD})", text)


class TheCardSaysWhatHappenedAndWhereTheWorkIs(unittest.TestCase):
    """The receipt a refused rescue leaves on the card. "The work is still on
    the runner" told three runs' readers nothing they could act on: not what
    GitHub said, and not that the work still exists."""

    def test_the_receipt_names_the_http_status(self):
        d = dead_run.decide(0, credential_expiry=True, push_status="400")
        self.assertIn("400", d.comments[0])

    def test_the_receipt_names_the_artifact_the_work_is_in(self):
        d = dead_run.decide(0, credential_expiry=True, push_status="400",
                            artifact=f"rescue-{CARD}.patch")
        self.assertIn(f"rescue-{CARD}.patch", d.comments[0])

    def test_a_hold_at_the_cap_says_both_too(self):
        d = dead_run.decide(dead_run.REQUEUE_CAP, credential_expiry=True,
                            push_status="400", artifact=f"rescue-{CARD}.patch")
        self.assertEqual(d.action, "hold")
        self.assertIn("400", d.comments[0])
        self.assertIn(f"rescue-{CARD}.patch", d.comments[0])

    def test_it_still_says_credential_and_never_says_died(self):
        d = dead_run.decide(0, credential_expiry=True, push_status="400",
                            artifact=f"rescue-{CARD}.patch")
        self.assertIn("credential", d.comments[0].lower())
        self.assertNotIn("died", d.comments[0].lower())

    def test_an_unknown_status_claims_none(self):
        d = dead_run.decide(0, credential_expiry=True)
        self.assertNotIn("HTTP", d.comments[0])

    def test_the_cli_takes_both_flags(self):
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "dead_run.py"), "decide",
             "0", "--credential-expiry", "--push-status", "400",
             "--artifact", f"rescue-{CARD}.patch"],
            capture_output=True, text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines()[0], "requeue")
        self.assertIn("400", done.stdout)
        self.assertIn(f"rescue-{CARD}.patch", done.stdout)


if __name__ == "__main__":
    unittest.main()
