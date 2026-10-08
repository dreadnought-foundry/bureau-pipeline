"""A fix run whose start token died still delivers its commit (DRE-6350).

`agent-fix.yml` mints its tokens at the top of the job and GitHub kills them at
sixty minutes. Two runs committed a fix and lost it at the push:
agent-bureau run 36169231289 (DRE-4883, 2026-09-25) committed `a2ab30b`, lost
it 171 seconds after the token died and ended on its own blocker file saying
the work was committed and not pushed; bureau-pipeline run 36640595665
(DRE-3898, 2026-09-29) committed `2a08e0a` and lost it the same way.

`agent-task.yml` has carried the remedy since DRE-3043, and this card gives the
fix job the same four steps: `Mint fresh push token`, `Mint rescue retry
token`, `Push rescue` and `Upload rescued work`. What is pinned here drives
the REAL `Push rescue` step — its `run:` block AND its `env:` wiring, read out
of `agent-fix.yml` — against:

  * a REAL bare remote holding the pull request's branch at the head the run
    started from, and a checkout one commit ahead of it;
  * the checkout's `http.extraheader` carrying the expired job-start token;
  * a `git` on PATH that refuses every network operation unless that header is
    exactly the fresh mint, and a `gh` that answers only to the fresh mint and
    reports the pull request `OPEN`.

The fixture is `tests/test_credential_expiry_scenario.py`'s, as DRE-6348 copied
it for a fix run (`tests/test_push_rescue_fix_branch.py`): same shims, plus the
pull request's branch already on the remote and a `gh pr view` that answers a
state. It is reused, not copied a third time.

The negative control is the load-bearing half: the same fixture with the
job-start token in `PUSH_TOKEN` must fail to push and say so.

Run: python3 -m pytest tests/test_fix_credential_expiry_scenario.py -v
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import push_rescue  # noqa: E402

import test_push_rescue_fix_branch as fix_branch  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "agent-fix.yml"

CARD = fix_branch.CARD
BRANCH = fix_branch.BRANCH
REPO = fix_branch.REPO
PR = fix_branch.PR
STALE_TOKEN = fix_branch.STALE_TOKEN
FRESH_TOKEN = fix_branch.FRESH_TOKEN

# The contract shared with DRE-6348 and DRE-6351, spelled once.
MINT_STEPS = {"pushtoken": "Mint fresh push token",
              "pushtoken2": "Mint rescue retry token"}
RESCUE_STEP = "Push rescue"
RESCUE_ID = "rescue"
UPLOAD_STEP = "Upload rescued work"
REPORT_STEP = "Report"
REPORT_RESCUE_ENV = ("RESCUE_LOCAL_WORK", "RESCUE_PUSHED", "RESCUE_PATCH",
                     "RESCUE_ARTIFACT", "RESCUE_PUSH_STATUS", "RESCUE_ERROR",
                     "RESCUE_TARGET_BRANCH", "RUN_ID")
GATE = ("always() && steps.pr.outputs.go == 'true' && "
        "steps.unfixable.outputs.escalate != 'true'")


def steps() -> list[dict]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["fix"]["steps"]


def named(name: str) -> tuple[int, dict]:
    for i, step in enumerate(steps()):
        if step.get("name") == name:
            return i, step
    raise AssertionError(f"agent-fix.yml has no {name!r} step")


def conjuncts(condition: str) -> set[str]:
    return {t.strip() for t in str(condition or "").split("&&") if t.strip()}


def resolve(text: str, values: dict) -> str:
    """Apply the `${{ }}` substitutions Actions would make. An expression the
    fixture has no value for is a hole in the harness, not a pass."""

    def repl(m):
        key = m.group(1).strip()
        if key not in values:
            raise AssertionError(f"fixture has no value for ${{{{ {key} }}}}")
        return values[key]

    out = re.sub(r"\$\{\{([^}]*)\}\}", repl, str(text))
    assert "${{" not in out
    return out


class FixRun:
    """A fix run's runner at the moment the `Fix` step has ended."""

    def __init__(self, td: str, *, push_token: str, retry_token: str = ""):
        self.fx = fix_branch.Fixture(td)
        self.push_token = push_token
        self.retry_token = retry_token
        self.output = self.fx.root / "github_output"
        self.output.write_text("", encoding="utf-8")
        # The pipeline checkout the step calls into, where the workflow puts it.
        shutil.copytree(ROOT / "scripts",
                        self.fx.work / ".bureau-pipeline" / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        with open(self.fx.work / ".git" / "info" / "exclude", "a",
                  encoding="utf-8") as fh:
            fh.write(".bureau-pipeline/\n")

    def agent_committed_a_fix(self) -> str:
        return self.fx.agent_committed_a_fix()

    def agent_wrote_a_blocker(self) -> Path:
        """The DRE-4883 ending: the run's own keyed blocker file, opened by
        the real `fix_handoff.py open` the workflow's `Open this run's fix
        handoff` step runs, with words in it."""
        (self.fx.root / "legacy").mkdir(exist_ok=True)
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "fix_handoff.py"), "open",
             "--base", str(self.fx.root), "--repo", REPO, "--pr", PR,
             "--sha", self.fx.start_sha,
             "--legacy-dir", str(self.fx.root / "legacy")],
            capture_output=True, text=True, check=True,
        )
        paths = dict(line.split("=", 1) for line in done.stdout.splitlines()
                     if "=" in line)
        blocker = Path(paths["blocker"])
        blocker.write_text(
            "The fix is committed on the branch and the push answered "
            "`Bad credentials`; the work is committed and not pushed.\n",
            encoding="utf-8")
        return blocker

    def values(self) -> dict:
        return {
            "steps.pushtoken.outputs.token": self.push_token,
            "steps.pushtoken2.outputs.token": self.retry_token,
            "steps.pr.outputs.card": CARD,
            "steps.pr.outputs.head_sha": self.fx.start_sha,
            "steps.pr.outputs.branch": BRANCH,
            "steps.pr.outputs.number": PR,
            "github.repository": REPO,
        }

    def run_the_rescue_step(self):
        _, step = named(RESCUE_STEP)
        values = self.values()
        script = self.fx.root / "rescue.sh"
        script.write_text("set -eo pipefail\n" + resolve(step["run"], values),
                          encoding="utf-8")
        env = self.fx.env()
        env.update({key: resolve(value, values)
                    for key, value in (step.get("env") or {}).items()})
        env["GITHUB_OUTPUT"] = str(self.output)
        return subprocess.run(["bash", str(script)], cwd=str(self.fx.work),
                              env=env, capture_output=True, text=True)

    def outputs(self) -> dict:
        out = {}
        for line in self.output.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                out[key] = value
        return out


def _park_build_stop_notes(test: unittest.TestCase) -> None:
    """The build agent's three exits are never this step's to read, but a
    leftover from the live run this suite executes inside must not decide a
    result either way. Moved aside and put back, never deleted."""
    for path in push_rescue.STOP_NOTES:
        if os.path.exists(path):
            parked = path + ".parked-by-test"
            os.replace(path, parked)
            test.addCleanup(os.replace, parked, path)


class _Scenario(unittest.TestCase):
    TOKEN = FRESH_TOKEN
    RETRY = ""
    COMMIT = True
    BLOCKER = False

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        _park_build_stop_notes(self)
        self.run_ = FixRun(td.name, push_token=self.TOKEN,
                           retry_token=self.RETRY)
        self.fx = self.run_.fx
        self.sha = (self.run_.agent_committed_a_fix() if self.COMMIT
                    else self.fx.start_sha)
        self.blocker = self.run_.agent_wrote_a_blocker() if self.BLOCKER else None
        self.proc = self.run_.run_the_rescue_step()
        self.out = self.run_.outputs()

    def log(self) -> str:
        return self.proc.stdout + self.proc.stderr


class TheFixReachesTheRemoteWithTheFreshToken(_Scenario):
    """AC 1: the remote holds the pull request's branch at the head the run
    started from; the checkout is one commit ahead and still carries the dead
    job-start header."""

    def test_the_step_succeeds(self):
        self.assertEqual(self.proc.returncode, 0, self.log())

    def test_the_fixture_started_behind(self):
        # Guards the fixture: the remote held the OLDER sha, so a green below
        # is a push and not a remote that already had the commit.
        self.assertNotEqual(self.fx.start_sha, self.sha)

    def test_the_new_commit_reaches_the_remote(self):
        self.assertEqual(self.fx.remote_sha(), self.sha, self.log())

    def test_no_pull_request_is_created(self):
        self.assertEqual(self.fx.pr_creates(), [])

    def test_the_outputs_reach_github_output(self):
        self.assertEqual(self.out.get("local_work"), "true", self.out)
        self.assertEqual(self.out.get("pushed"), "true", self.out)
        self.assertEqual(self.out.get("target_branch"), BRANCH, self.out)
        self.assertEqual(self.out.get("patch"), "", self.out)

    def test_the_pull_request_state_was_read_with_the_fresh_token(self):
        views = [c for c in self.fx.gh_calls() if c["argv"][:2] == ["pr", "view"]]
        self.assertTrue(views, self.fx.gh_calls())
        self.assertEqual({c["token"] for c in views}, {FRESH_TOKEN})
        self.assertEqual(views[0]["argv"][2], PR)

    def test_no_token_is_printed(self):
        self.assertNotIn(FRESH_TOKEN, self.log())
        self.assertNotIn(STALE_TOKEN, self.log())


class TheNegativeControl(_Scenario):
    """The same fixture with the job-start token in PUSH_TOKEN, as the job
    held it before this card. If this pushed, the fixture would be proving
    nothing."""

    TOKEN = STALE_TOKEN

    def test_the_step_does_not_fail_the_job(self):
        self.assertEqual(self.proc.returncode, 0, self.log())

    def test_the_expired_token_cannot_push(self):
        self.assertEqual(self.fx.remote_sha(), self.fx.start_sha)

    def test_it_says_so(self):
        self.assertEqual(self.out.get("pushed"), "false", self.out)
        self.assertEqual(self.out.get("local_work"), "true", self.out)
        self.assertIn("unreadable", self.out.get("error", ""), self.out)
        self.assertIn("not pushing", self.log())

    def test_no_pull_request_is_created(self):
        self.assertEqual(self.fx.pr_creates(), [])


class ABlockerBesideACommitStillDelivers(_Scenario):
    """AC 2, the exact DRE-4883 case: one commit beyond the starting head AND
    a non-empty blocker file in the run's own handoff. The commit is work and
    is delivered; the blocker is the Report's to read (DRE-6351)."""

    BLOCKER = True

    def test_the_blocker_is_really_there(self):
        self.assertTrue(self.blocker.is_file() and self.blocker.stat().st_size)

    def test_the_commit_is_pushed(self):
        self.assertEqual(self.fx.remote_sha(), self.sha, self.log())

    def test_the_outputs(self):
        self.assertEqual(self.out.get("local_work"), "true", self.out)
        self.assertEqual(self.out.get("pushed"), "true", self.out)


class ABlockerWithNoCommitPushesNothing(_Scenario):
    """AC 2's other half: a non-empty blocker and NO commit beyond the
    starting head is an escalation, and there is nothing to deliver."""

    BLOCKER = True
    COMMIT = False

    def test_nothing_is_pushed(self):
        self.assertEqual(self.fx.pushes(), [])
        self.assertEqual(self.fx.remote_sha(), self.fx.start_sha)

    def test_the_outputs(self):
        self.assertEqual(self.out.get("local_work"), "false", self.out)
        self.assertEqual(self.out.get("pushed"), "false", self.out)
        self.assertEqual(self.out.get("patch"), "", self.out)


class BothMintsRefusedLeaveThePatchAndTheSidecar(_Scenario):
    """AC 3: both credentials GitHub refuses. The step writes the patch and
    the sidecar, which is what `Upload rescued work` is gated on."""

    TOKEN = STALE_TOKEN
    RETRY = fix_branch.OTHER_DEAD_TOKEN

    def test_nothing_is_pushed(self):
        self.assertEqual(self.fx.remote_sha(), self.fx.start_sha)

    def test_the_patch_is_written(self):
        patch = self.out.get("patch", "")
        self.assertTrue(patch.endswith(f"rescue-{CARD}.patch"), self.out)
        self.assertIn("value = 2", Path(patch).read_text(encoding="utf-8"))

    def test_the_sidecar_is_written_beside_it(self):
        sidecar = self.out.get("sidecar", "")
        self.assertTrue(sidecar.endswith(f"rescue-{CARD}.target.json"), self.out)
        self.assertTrue(Path(sidecar).is_file())
        self.assertEqual(Path(sidecar).parent,
                         Path(self.out.get("patch", "")).parent)


class TheStepsAreWiredToTheContract(unittest.TestCase):
    """The step names, ids, gates and wiring DRE-6348 and DRE-6351 read."""

    def test_the_four_steps_sit_after_the_working_log_and_before_the_report_mint(self):
        log, _ = named("Keep the run's working log")
        mint, _ = named("Mint fresh report token")
        order = [named(n)[0] for n in (*MINT_STEPS.values(), RESCUE_STEP,
                                       UPLOAD_STEP)]
        self.assertEqual(order, sorted(order))
        self.assertGreater(order[0], log)
        self.assertLess(order[-1], mint)

    def test_the_mints_and_the_rescue_carry_the_reports_gate(self):
        for step_id, name in MINT_STEPS.items():
            _, step = named(name)
            self.assertEqual(step.get("id"), step_id)
            self.assertEqual(conjuncts(step.get("if")), conjuncts(GATE), name)
        _, rescue = named(RESCUE_STEP)
        self.assertEqual(rescue.get("id"), RESCUE_ID)
        self.assertEqual(conjuncts(rescue.get("if")), conjuncts(GATE))

    def test_the_rescue_names_the_run_s_branch_pull_request_and_start_head(self):
        _, rescue = named(RESCUE_STEP)
        env, run = rescue.get("env") or {}, rescue["run"]
        self.assertEqual(env.get("PRE_SHA"), "${{ steps.pr.outputs.head_sha }}")
        self.assertEqual(env.get("BRANCH"), "${{ steps.pr.outputs.branch }}")
        self.assertEqual(env.get("PR"), "${{ steps.pr.outputs.number }}")
        self.assertEqual(env.get("PUSH_TOKEN"), "${{ steps.pushtoken.outputs.token }}")
        self.assertEqual(env.get("PUSH_TOKEN_RETRY"),
                         "${{ steps.pushtoken2.outputs.token }}")
        flat = " ".join(run.split())
        for flag in ('--base "$PRE_SHA"', '--branch "$BRANCH"',
                     '--existing-pr "$PR"',
                     '--patch "$RUNNER_TEMP/rescue-$CARD.patch"'):
            self.assertIn(flag, flat)
        self.assertIn('>> "$GITHUB_OUTPUT"', run)
        self.assertNotIn("${{", run)

    def test_the_rescue_reads_no_stop_note_and_no_handoff(self):
        # A committed fix is delivered whatever the agent wrote beside it.
        _, rescue = named(RESCUE_STEP)
        text = yaml.safe_dump(rescue, width=10**6)
        for note in push_rescue.STOP_NOTES:
            self.assertNotIn(note, text)
        for word in ("--stop", "stop-note", "blocker", "handoff"):
            self.assertNotIn(word, rescue["run"])
        self.assertNotIn("steps.handoff", text)

    def test_the_upload_takes_the_patch_and_the_sidecar(self):
        _, upload = named(UPLOAD_STEP)
        self.assertEqual(conjuncts(upload.get("if")),
                         conjuncts("always() && steps.rescue.outputs.patch != ''"))
        self.assertTrue(upload.get("continue-on-error"))
        self.assertTrue(str(upload.get("uses")).startswith("actions/upload-artifact@"))
        with_ = upload.get("with") or {}
        self.assertEqual(with_.get("name"), "rescue-${{ steps.pr.outputs.card }}.patch")
        paths = [p.strip() for p in str(with_.get("path")).splitlines() if p.strip()]
        self.assertEqual(sorted(paths), sorted([
            "${{ steps.rescue.outputs.patch }}",
            "${{ steps.rescue.outputs.sidecar }}",
        ]))
        self.assertEqual(with_.get("retention-days"), 30)

    def test_the_upload_uses_the_files_pinned_upload_action(self):
        _, upload = named(UPLOAD_STEP)
        pins = {s.get("uses") for s in steps()
                if str(s.get("uses") or "").startswith("actions/upload-artifact@")
                and s.get("name") != UPLOAD_STEP}
        self.assertEqual(pins, {upload.get("uses")})

    def test_the_report_env_carries_the_rescue_keys(self):
        _, report = named(REPORT_STEP)
        env = report.get("env") or {}
        for key in REPORT_RESCUE_ENV:
            self.assertIn(key, env)
        self.assertEqual(env["RESCUE_ARTIFACT"], "rescue-${{ steps.pr.outputs.card }}.patch")
        self.assertEqual(env["RUN_ID"], "${{ github.run_id }}")
        for key, output in (("RESCUE_LOCAL_WORK", "local_work"),
                            ("RESCUE_PUSHED", "pushed"),
                            ("RESCUE_PATCH", "patch"),
                            ("RESCUE_PUSH_STATUS", "push_status"),
                            ("RESCUE_ERROR", "error"),
                            ("RESCUE_TARGET_BRANCH", "target_branch")):
            self.assertEqual(env[key], "${{ steps.rescue.outputs.%s }}" % output, key)


# ── the Fix prompt's two sentences (AC 6) ────────────────────────────────────

def fix_prompt() -> str:
    _, step = named("Fix")
    return step["with"]["prompt"]


def prompt_step(n: str) -> str:
    """Step `n`'s text in the Fix prompt, hard wraps collapsed, up to the next
    numbered step."""
    raw = fix_prompt()
    start = re.search(rf"(?m)^\s*{re.escape(n)}\. ", raw)
    assert start, f"step {n} not found in the Fix prompt"
    nxt = re.compile(r"(?m)^\s*\d+[a-z]?\. ").search(raw, start.end())
    return " ".join(raw[start.start():nxt.start() if nxt else None].split())


ARTIFACT_RE = r"rescue-(?:<CARD>|\$\{\{ steps\.pr\.outputs\.card \}\})\.patch"


def refused_push_sentence() -> str:
    body = prompt_step("5")
    found = [s for s in re.split(r"(?<=[.])\s+(?=[A-Z])", body)
             if re.search(r"(?i)\bgit push\b[^.]*\brefused\b", s)]
    assert len(found) == 1, f"step 5 carries {len(found)} refused-push sentences"
    return found[0]


class TheFixPromptSaysWhatHappensToARefusedPush(unittest.TestCase):

    def setUp(self):
        self.sentence = refused_push_sentence()

    def test_it_names_the_refusals_it_means(self):
        for word in ("Bad credentials", "GH013", "permission", "non-fast-forward"):
            self.assertIn(word, self.sentence)

    def test_it_names_the_push_rescue_step_and_the_artifact(self):
        self.assertIn("`Push rescue`", self.sentence)
        self.assertRegex(self.sentence, ARTIFACT_RE)

    def test_it_names_nothing_else_as_a_delivery_mechanism(self):
        # What happens to the artifact afterwards is DRE-6351's and is said on
        # the pull request by the Report, never promised in the prompt.
        for other in ("deliver-rescue", "deliver_rescue", "follow-up",
                      "rescue-push-failed", "dispatch", "another run",
                      "next run"):
            self.assertNotIn(other, self.sentence.lower())

    def test_it_tells_the_agent_what_not_to_do(self):
        s = self.sentence.lower()
        self.assertRegex(s, r"do not retry with another token")
        self.assertRegex(s, r"do not undo the commit")
        self.assertRegex(s, r"do not write the blocker file")
        self.assertRegex(s, r"leave the commit on the branch and end")


class TheFixPromptSaysTheBlockerIsForUncommittedWork(unittest.TestCase):

    def setUp(self):
        self.body = prompt_step("6")

    def test_the_blocker_file_is_for_work_the_agent_did_not_commit(self):
        self.assertRegex(self.body, r"(?i)\bblocker file is for work you did not commit\b")
        self.assertRegex(self.body, r"(?i)\bcommit nothing when you write it\b")

    def test_a_refused_push_is_step_5_s_case(self):
        self.assertRegex(self.body, r"(?i)\ba push GitHub refused is step 5's case\b")

    def test_a_commit_beyond_the_start_is_delivered_whatever_the_file_says(self):
        self.assertRegex(
            self.body,
            r"(?i)\bcommit you made beyond the head you started from is "
            r"delivered after this run whatever you write here\b")


if __name__ == "__main__":
    unittest.main()
