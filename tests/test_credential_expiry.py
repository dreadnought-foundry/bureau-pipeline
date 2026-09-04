"""A run longer than an hour must still deliver its work (DRE-3043).

THE INCIDENT (2026-09-03, bureau-pipeline run 33822932627, card DRE-3029).
The run started at 17:43 PT. `agent-task.yml` mints a GitHub App installation
token at the top of the job and GitHub kills it at 60 minutes, so at 18:43 PT
every credential on the runner was dead — the `gh` token the agent pushes with
AND the `http.https://github.com/.extraheader` that `actions/checkout`
configured, which are two spellings of the same one-hour token. At 18:53 PT the
agent finished, green, 5,001 tests, and could not push a byte of it. The requeue
rebuilt the whole card: another ~27 minutes and ~$21 for work that already
existed on a disk nobody could read.

Every card that runs past an hour has that ceiling, and it is silent until the
push. Four things close it, and each one is pinned below:

  1. RE-MINT BEFORE THE PUSH — a step after the agent mints a fresh token from
     the same App and delivers what the agent could not (`push_rescue.py`).
  2. GIT GETS THE FRESH TOKEN, NOT ONLY `gh` — the checkout's extraheader is
     re-pointed, and the stale value is UNSET first: `http.extraheader` is
     multi-valued and git sends every value it finds, so a fresh header
     appended beside the dead one sends two Authorization headers.
  3. A LOUD FLOOR — at 50 minutes with nothing pushed the card says so
     (`credential_clock.py`), and the brief tells the agent to push a WIP
     branch, so an expiry costs a rebase rather than a rebuild.
  4. THE MEDIC CLASSIFIES IT — `check_agent_result.py` names
     `credential_expiry` when the work is on the runner and the push was
     refused, so the requeue note says *credential*, not *died*.

These must FAIL before the mechanism exists and PASS after.
tests/test_credential_expiry_scenario.py drives the workflow step itself
against a real git remote that refuses the stale token.

Run: python3 -m pytest tests/test_credential_expiry.py -v
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_agent_result  # noqa: E402
import credential_clock  # noqa: E402
import dead_run  # noqa: E402
import push_rescue  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "agent-task.yml"
ENGINEER_BRIEF = ROOT / "briefs" / "engineer.md"

CARD = "DRE-3043"
BRANCH = f"agent/{CARD}-push-before-token-expiry"
REPO = "dreadnought-foundry/bureau-pipeline"
STALE = "ghs_start_of_job"
FRESH = "ghs_minted_for_the_push"


def _steps() -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["execute"]["steps"]


def _step_index(name: str) -> int:
    """The step whose name IS `name`, or begins with it — the parenthetical
    half of a step name is a label, not the identity."""
    for i, step in enumerate(_steps()):
        if str(step.get("name") or "").startswith(name):
            return i
    raise AssertionError(f"agent-task.yml has no step named {name!r}")


def _step(name: str) -> dict:
    return _steps()[_step_index(name)]


class FakeGit:
    """A `run` stand-in for push_rescue: records every argv and answers the
    reads from a scripted world. Nothing here shells out."""

    def __init__(
        self,
        *,
        branches=(BRANCH,),
        local_sha="aaaa111",
        remote_sha="",
        ahead=3,
        push_ok=True,
        prs=(),
        create_url="https://github.com/x/y/pull/244",
        gh_ok=True,
    ):
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []
        self.branches = list(branches)
        self.local_sha = local_sha
        self.remote_sha = remote_sha
        self.ahead = ahead
        self.push_ok = push_ok
        self.prs = list(prs)
        self.create_url = create_url
        self.gh_ok = gh_ok

    def __call__(self, argv, *, cwd=None, env=None):
        self.calls.append(list(argv))
        self.envs.append(dict(env or {}))
        rest = [a for a in argv[1:] if a not in ("-C", cwd)]
        joined = " ".join(argv)
        if argv[0] == "gh":
            if not self.gh_ok:
                return 1, "", "gh: Bad credentials (HTTP 401)"
            if "list" in argv:
                return 0, json.dumps(self.prs), ""
            if "create" in argv:
                return 0, self.create_url + "\n", ""
            return 0, "", ""
        if "for-each-ref" in rest:
            # The glob the caller asked for, honoured — a fake that answers
            # every pattern with every branch cannot show that another card's
            # branch is left alone.
            prefix = rest[-1].removeprefix("refs/heads/").removesuffix("*")
            matched = [b for b in self.branches if b.startswith(prefix)]
            return 0, "".join(f"{b}\n" for b in matched), ""
        if "rev-parse" in rest:
            return (0, self.local_sha + "\n", "") if self.local_sha else (128, "", "no")
        if "ls-remote" in rest:
            if not self.remote_sha:
                return 0, "", ""
            return 0, f"{self.remote_sha}\trefs/heads/{BRANCH}\n", ""
        if "rev-list" in rest:
            return 0, f"{self.ahead}\n", ""
        if "push" in rest:
            if not self.push_ok:
                return 128, "", (
                    "fatal: Authentication failed for "
                    "'https://github.com/dreadnought-foundry/bureau-pipeline/'"
                )
            return 0, "", ""
        if "config" in rest:
            return 0, "", ""
        if "fetch" in rest:
            return 0, "", ""
        raise AssertionError(f"FakeGit has no answer for: {joined}")

    def argv_containing(self, *needles) -> list[list[str]]:
        return [c for c in self.calls if all(n in c for n in needles)]


def _rescue(fake, **kw):
    kw.setdefault("base", "main")
    kw.setdefault("card_url", "https://linear.app/x/issue/DRE-3043")
    kw.setdefault("card_title", "a build run longer than an hour cannot push")
    return push_rescue.rescue(CARD, REPO, FRESH, run=fake, log=lambda *_: None, **kw)


# ── 2. git gets the fresh token, not only gh ─────────────────────────────────
class GitGetsTheFreshToken(unittest.TestCase):
    """`gh` reads GH_TOKEN from the environment; git reads the header
    `actions/checkout` wrote into .git/config. Re-pointing only the first
    leaves the push using the corpse."""

    def test_the_checkout_header_is_repointed_at_the_fresh_token(self):
        fake = FakeGit()
        _rescue(fake)
        sets = [
            c for c in fake.calls
            if "config" in c and push_rescue.GITHUB_EXTRAHEADER in c
            and "--unset-all" not in c
        ]
        self.assertEqual(len(sets), 1, fake.calls)
        expected = base64.b64encode(f"x-access-token:{FRESH}".encode()).decode()
        self.assertIn(f"AUTHORIZATION: basic {expected}", sets[0])

    def test_the_stale_header_is_unset_before_the_fresh_one_is_written(self):
        # http.extraheader is MULTI-valued: git sends every value it finds, so
        # a fresh header appended beside the dead one is two Authorization
        # headers and GitHub refuses the pair. Order is the whole fix.
        fake = FakeGit()
        _rescue(fake)
        unset = next(
            i for i, c in enumerate(fake.calls)
            if "--unset-all" in c and push_rescue.GITHUB_EXTRAHEADER in c
        )
        write = next(
            i for i, c in enumerate(fake.calls)
            if "config" in c and push_rescue.GITHUB_EXTRAHEADER in c
            and "--unset-all" not in c
        )
        self.assertLess(unset, write, fake.calls)

    def test_the_credential_is_repointed_before_any_credentialed_read(self):
        # `git ls-remote` is itself an authenticated call. Asking the remote
        # what it has BEFORE re-pointing answers with the dead token.
        fake = FakeGit()
        _rescue(fake)
        write = next(
            i for i, c in enumerate(fake.calls)
            if "config" in c and push_rescue.GITHUB_EXTRAHEADER in c
            and "--unset-all" not in c
        )
        first_remote = next(
            i for i, c in enumerate(fake.calls)
            if "ls-remote" in c or "push" in c
        )
        self.assertLess(write, first_remote, fake.calls)

    def test_gh_is_handed_the_fresh_token_too(self):
        fake = FakeGit()
        _rescue(fake)
        gh_envs = [
            env for call, env in zip(fake.calls, fake.envs) if call[0] == "gh"
        ]
        self.assertTrue(gh_envs)
        for env in gh_envs:
            self.assertEqual(env.get("GH_TOKEN"), FRESH)


# ── 1. re-mint before the push: what the step actually delivers ──────────────
class TheRescueDelivers(unittest.TestCase):

    def test_an_unpushed_branch_is_pushed_and_gets_its_pr(self):
        fake = FakeGit(remote_sha="")
        out = _rescue(fake)
        self.assertTrue(out.pushed, fake.calls)
        self.assertTrue(out.pr_opened)
        self.assertTrue(out.rescued)
        self.assertTrue(fake.argv_containing("push"))
        self.assertTrue(fake.argv_containing("create"))

    def test_a_branch_already_on_github_with_no_pr_still_gets_one(self):
        # The other half of the incident: the push landed and `gh pr create`
        # was the call that died.
        fake = FakeGit(remote_sha="aaaa111")
        out = _rescue(fake)
        self.assertFalse(out.pushed)
        self.assertTrue(out.pr_opened)
        self.assertEqual(fake.argv_containing("push"), [])

    def test_the_pushed_ref_is_fetched_so_the_report_step_can_see_it(self):
        # Both later steps resolve the card's branch with `git branch -r`.
        # A push that leaves no remote-tracking ref reads as "no branch" — and
        # a bare `fetch origin <branch>` obeys the narrow `remote.origin.fetch`
        # actions/checkout configures, so the refspec is written out in full.
        fake = FakeGit(remote_sha="")
        _rescue(fake)
        fetches = fake.argv_containing("fetch")
        self.assertTrue(fetches, fake.calls)
        self.assertIn(
            f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}", fetches[0]
        )

    def test_the_pr_body_carries_the_card_url(self):
        fake = FakeGit(remote_sha="")
        _rescue(fake)
        create = fake.argv_containing("create")[0]
        body = create[create.index("--body") + 1]
        self.assertIn("https://linear.app/x/issue/DRE-3043", body)

    def test_the_pr_title_names_the_card(self):
        fake = FakeGit(remote_sha="")
        _rescue(fake)
        create = fake.argv_containing("create")[0]
        title = create[create.index("--title") + 1]
        self.assertTrue(title.startswith(f"feat({CARD}):"), title)


class TheRescueIsANoOpOnTheHappyPath(unittest.TestCase):
    """The overwhelming majority of runs push their own work. This step must
    change nothing for them — a second PR would be worse than the bug."""

    def test_a_branch_that_is_pushed_and_has_a_pr_is_left_alone(self):
        fake = FakeGit(
            remote_sha="aaaa111",
            prs=[{"url": "https://github.com/x/y/pull/240", "number": 240}],
        )
        out = _rescue(fake)
        self.assertFalse(out.local_work)
        self.assertFalse(out.pushed)
        self.assertFalse(out.pr_opened)
        self.assertFalse(out.rescued)
        self.assertEqual(fake.argv_containing("create"), [])

    def test_no_card_branch_at_all_is_a_no_op(self):
        fake = FakeGit(branches=[])
        out = _rescue(fake)
        self.assertEqual(out.branch, "")
        self.assertFalse(out.local_work)
        self.assertEqual(fake.argv_containing("push"), [])
        self.assertEqual(fake.argv_containing("create"), [])

    def test_a_branch_with_no_commits_of_its_own_is_not_pushed(self):
        # An agent that made the branch and died before committing has no work
        # to deliver; pushing it would open an empty PR for the critic.
        fake = FakeGit(remote_sha="", ahead=0)
        out = _rescue(fake)
        self.assertFalse(out.local_work)
        self.assertEqual(fake.argv_containing("push"), [])
        self.assertEqual(fake.argv_containing("create"), [])

    def test_an_agent_that_chose_a_different_exit_is_not_overruled(self):
        # An escalation asks the CEO a question and a blocker parks the card;
        # both are decisions the prompt asks the agent to make for itself, and
        # a rescue that opened a PR anyway would overrule the one thing this
        # step has no business deciding.
        import tempfile

        for name in ("agent-escalation.txt", "agent-blocker.txt",
                     "agent-handback.txt"):
            with tempfile.TemporaryDirectory() as td:
                note = os.path.join(td, name)
                with open(note, "w", encoding="utf-8") as fh:
                    fh.write("the agent stopped on purpose\n")
                fake = FakeGit(remote_sha="")
                out = push_rescue.rescue(
                    CARD, REPO, FRESH, run=fake, log=lambda *_: None,
                    stop_notes=(note,),
                )
                self.assertFalse(out.rescued, name)
                self.assertFalse(out.local_work, name)
                self.assertEqual(fake.calls, [], name)

    def test_the_stop_notes_are_the_prompts_own_three_paths(self):
        self.assertEqual(
            set(push_rescue.STOP_NOTES),
            {"/tmp/agent-escalation.txt", "/tmp/agent-blocker.txt",
             "/tmp/agent-handback.txt"},
        )

    def test_an_empty_stop_note_does_not_stop_the_rescue(self):
        # The workflow's own branches test these files with `-s`: a zero-byte
        # leftover is not a decision.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            note = os.path.join(td, "agent-blocker.txt")
            open(note, "w", encoding="utf-8").close()
            fake = FakeGit(remote_sha="")
            out = push_rescue.rescue(
                CARD, REPO, FRESH, run=fake, log=lambda *_: None,
                stop_notes=(note,),
            )
        self.assertTrue(out.rescued)

    def test_another_cards_branch_is_never_touched(self):
        fake = FakeGit(branches=["agent/DRE-9999-something-else"])
        out = _rescue(fake)
        self.assertEqual(out.branch, "")
        pattern = [c for c in fake.calls if "for-each-ref" in c][0]
        self.assertTrue(any(f"agent/{CARD}-" in a for a in pattern), pattern)


class TheRescueReportsHonestly(unittest.TestCase):

    def test_local_work_is_reported_even_when_the_push_is_refused(self):
        # If the re-mint itself failed, the Report step still has to be able to
        # say "the work is on the runner" rather than "the agent died".
        fake = FakeGit(remote_sha="", push_ok=False)
        out = _rescue(fake)
        self.assertTrue(out.local_work)
        self.assertFalse(out.pushed)
        self.assertFalse(out.rescued)
        self.assertTrue(out.error)

    def test_a_refused_push_does_not_then_open_a_pr(self):
        fake = FakeGit(remote_sha="", push_ok=False)
        out = _rescue(fake)
        self.assertFalse(out.pr_opened)

    def test_the_outputs_are_actions_key_value_lines(self):
        fake = FakeGit(remote_sha="")
        out = _rescue(fake)
        lines = push_rescue.output_lines(out)
        self.assertIn("local_work=true", lines)
        self.assertIn("pushed=true", lines)
        self.assertIn("pr_opened=true", lines)
        self.assertIn("rescued=true", lines)
        for line in lines:
            self.assertRegex(line, r"^[a-z_]+=[^\n]*$")

    def test_the_token_never_reaches_a_log_line(self):
        logged: list[str] = []
        fake = FakeGit(remote_sha="")
        push_rescue.rescue(
            CARD, REPO, FRESH, base="main", card_url="u", card_title="t",
            run=fake, log=logged.append,
        )
        self.assertTrue(logged)
        for line in logged:
            self.assertNotIn(FRESH, line)

    def test_a_card_reference_with_shell_metacharacters_is_refused(self):
        # The identifier arrives from the relay's client_payload. It is an
        # argv element here, never a shell word — but it also becomes a git
        # ref GLOB, so it is validated rather than trusted.
        fake = FakeGit()
        for hostile in ("DRE-1; rm -rf /", "../../etc", "DRE 1", "*"):
            with self.assertRaises(ValueError):
                push_rescue.rescue(
                    hostile, REPO, FRESH, run=fake, log=lambda *_: None,
                )


# ── 3. a loud floor, not a silent ceiling ────────────────────────────────────
class TheFiftyMinuteFloor(unittest.TestCase):

    def test_the_warning_sits_inside_the_hour_with_room_to_push(self):
        self.assertLess(credential_clock.WARN_AT_SECONDS, 60 * 60)
        self.assertGreaterEqual(credential_clock.WARN_AT_SECONDS, 45 * 60)
        self.assertEqual(
            credential_clock.WARN_AT_SECONDS,
            credential_clock.TOKEN_LIFETIME_SECONDS
            - credential_clock.WARN_MARGIN_SECONDS,
        )

    def test_it_waits_until_fifty_minutes_after_the_mint(self):
        minted = 1_000_000.0
        self.assertEqual(
            credential_clock.seconds_until_warning(minted, minted + 60),
            credential_clock.WARN_AT_SECONDS - 60,
        )
        self.assertEqual(
            credential_clock.seconds_until_warning(minted, minted + 99_999), 0
        )

    def _watch(self, *, pushed, post=None, slept=None):
        slept = [] if slept is None else slept
        posted = [] if post is None else post
        credential_clock.watch(
            CARD,
            minted_at=1_000_000.0,
            run_url="https://github.com/x/y/actions/runs/1",
            branch_pushed=lambda: pushed,
            post=posted.append,
            sleep=slept.append,
            now=lambda: 1_000_000.0,
        )
        return posted, slept

    def test_it_warns_when_nothing_has_reached_github(self):
        posted, slept = self._watch(pushed=False)
        self.assertEqual(len(posted), 1, posted)
        self.assertEqual(slept, [float(credential_clock.WARN_AT_SECONDS)])

    def test_the_warning_asks_for_the_push_and_names_the_margin(self):
        posted, _ = self._watch(pushed=False)
        body = posted[0]
        self.assertIn("credential expires in 10 minutes", body)
        self.assertIn("push what is green", body)

    def test_it_stays_silent_when_the_branch_is_already_pushed(self):
        posted, _ = self._watch(pushed=True)
        self.assertEqual(posted, [])

    def test_a_failed_post_never_takes_the_run_down_with_it(self):
        def explode(_body):
            raise RuntimeError("Linear said no")

        credential_clock.watch(
            CARD,
            minted_at=1_000_000.0,
            branch_pushed=lambda: False,
            post=explode,
            sleep=lambda _s: None,
            now=lambda: 1_000_000.0,
        )

    def test_an_unreadable_remote_is_treated_as_not_pushed(self):
        # "I could not tell" must warn rather than go quiet: a missed warning
        # costs the whole run, a spurious one costs a line on the card.
        def refuse(argv, *, cwd=None, env=None):
            return 128, "", "fatal: could not read Username"

        self.assertFalse(credential_clock.branch_is_pushed(CARD, run=refuse))

    def test_a_pushed_branch_is_seen(self):
        def answer(argv, *, cwd=None, env=None):
            return 0, f"aaa111\trefs/heads/{BRANCH}\n", ""

        self.assertTrue(credential_clock.branch_is_pushed(CARD, run=answer))


class TheBriefTellsTheAgentToPushEarly(unittest.TestCase):

    def test_the_engineer_brief_carries_the_push_before_expiry_rule(self):
        text = ENGINEER_BRIEF.read_text(encoding="utf-8")
        self.assertIn("60 minutes", text)
        lowered = text.lower()
        self.assertIn("wip", lowered)
        self.assertIn("credential", lowered)
        self.assertIn("rebase, not a rebuild", lowered)


# ── 4. the medic classifies it ───────────────────────────────────────────────
class TheClassification(unittest.TestCase):

    FINISHED = {
        "type": "result", "subtype": "success", "is_error": False,
        "num_turns": 88, "total_cost_usd": 20.4, "duration_ms": 4_200_000,
        "result": "The work is finished and green, and this run could not open "
                  "the pull request: its GitHub credential expired.",
    }
    TURN_CAP = {
        "subtype": "error_max_turns", "is_error": True, "num_turns": 150,
        "total_cost_usd": 17.4, "duration_ms": 3_000_000,
        "result": "Reached maximum number of turns (150)",
    }
    PUSH_401 = {
        "subtype": "success", "is_error": True, "num_turns": 90,
        "total_cost_usd": 19.0, "duration_ms": 4_000_000,
        "result": "fatal: Authentication failed for "
                  "'https://github.com/dreadnought-foundry/bureau-pipeline/'",
    }

    def test_work_on_the_runner_with_no_pr_is_a_credential_expiry(self):
        self.assertEqual(
            check_agent_result.classify_death(self.FINISHED, work_on_runner=True),
            check_agent_result.DEATH_CREDENTIAL_EXPIRY,
        )

    def test_a_push_refusal_in_the_record_classifies_on_its_own(self):
        self.assertEqual(
            check_agent_result.classify_death(self.PUSH_401),
            check_agent_result.DEATH_CREDENTIAL_EXPIRY,
        )

    def test_a_turn_cap_death_stays_turn_exhaustion_even_with_local_work(self):
        # An agent that commits its RED tests and then runs out of steps has
        # work on the runner too. The budget ceiling is the honest story.
        self.assertEqual(
            check_agent_result.classify_death(self.TURN_CAP, work_on_runner=True),
            check_agent_result.DEATH_TURN_EXHAUSTION,
        )

    def test_nothing_changes_for_a_run_with_no_local_work(self):
        self.assertEqual(
            check_agent_result.classify_death(self.FINISHED),
            check_agent_result.DEATH_NONE,
        )
        self.assertEqual(
            check_agent_result.classify_death(
                {"is_error": True, "num_turns": 30, "total_cost_usd": 2.0,
                 "duration_ms": 900_000}
            ),
            check_agent_result.DEATH_API,
        )

    def test_an_outage_signature_is_never_read_as_a_credential_expiry(self):
        outage = {"is_error": True, "num_turns": 1, "total_cost_usd": 0,
                  "duration_ms": 400}
        self.assertEqual(
            check_agent_result.classify_death(outage, work_on_runner=True),
            check_agent_result.DEATH_API,
        )

    def _classify_cli(self, execution, *flags):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "exec.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(execution, fh)
            done = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "check_agent_result.py"),
                 "classify", path, *flags],
                capture_output=True, text=True,
            )
        return done.stdout.strip()

    def test_the_cli_takes_the_flag_the_workflow_passes(self):
        self.assertEqual(
            self._classify_cli(self.FINISHED, "--work-on-runner", "true"),
            check_agent_result.DEATH_CREDENTIAL_EXPIRY,
        )
        self.assertEqual(
            self._classify_cli(self.FINISHED, "--work-on-runner", "false"),
            check_agent_result.DEATH_NONE,
        )
        self.assertEqual(self._classify_cli(self.FINISHED), "none")


class TheRequeueNoteSaysCredential(unittest.TestCase):

    def test_the_requeue_note_says_credential_and_not_died(self):
        d = dead_run.decide(0, credential_expiry=True, run_url="https://r")
        self.assertEqual(d.action, "requeue")
        body = d.comments[0]
        self.assertIn("credential", body.lower())
        self.assertNotIn("died", body.lower())

    def test_it_spends_the_shared_dead_run_budget(self):
        d = dead_run.decide(0, credential_expiry=True)
        self.assertIn(dead_run.DEAD_TAG, d.comments[0])
        self.assertNotIn(dead_run.TURN_TAG, d.comments[0])

    def test_it_records_no_model_as_having_failed(self):
        d = dead_run.decide(0, credential_expiry=True, is_error=True,
                            error_model="claude-opus-5")
        self.assertNotIn(dead_run.ERROR_MARKER_PREFIX, d.comments[0])

    def test_the_hold_at_the_cap_also_names_the_credential(self):
        d = dead_run.decide(dead_run.REQUEUE_CAP, credential_expiry=True)
        self.assertEqual(d.action, "hold")
        self.assertIn("credential", d.comments[0].lower())
        self.assertNotIn("died", d.comments[0].lower())

    def test_turn_exhaustion_still_wins(self):
        d = dead_run.decide(0, credential_expiry=True, turn_exhaustion=True)
        self.assertIn(dead_run.TURN_TAG, d.comments[0])

    def test_a_pre_agent_fault_still_wins(self):
        d = dead_run.decide(0, credential_expiry=True, pre_agent=True)
        self.assertEqual(d.action, "infra")

    def test_the_cli_flag_reaches_the_decision(self):
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "dead_run.py"), "decide",
             "0", "--credential-expiry", "--run-url", "https://r"],
            capture_output=True, text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines()[0], "requeue")
        self.assertIn("credential", done.stdout.lower())


# ── the wiring: the workflow actually calls all of it ────────────────────────
class TheWorkflowWiring(unittest.TestCase):

    def test_a_fresh_token_is_minted_from_the_same_app_after_the_agent(self):
        step = _step("Mint fresh push token")
        self.assertIn("actions/create-github-app-token", step["uses"])
        self.assertGreater(
            _step_index("Mint fresh push token"), _step_index("Implement card")
        )
        self.assertIn("secrets.BUREAU_APP_ID", step["with"]["app-id"])
        self.assertIn("secrets.BUREAU_APP_PRIVATE_KEY", step["with"]["private-key"])

    def test_the_fresh_mint_can_never_fail_the_build(self):
        step = _step("Mint fresh push token")
        self.assertTrue(step.get("continue-on-error"))
        self.assertTrue(str(step.get("if", "")).startswith("always()"))

    def test_the_rescue_runs_before_the_gate_and_the_report_read_the_branch(self):
        i = _step_index("Push rescue")
        self.assertGreater(i, _step_index("Implement card"))
        self.assertLess(i, _step_index("Gate on agent result"))
        self.assertLess(i, _step_index("Report result to Linear"))

    def test_the_rescue_calls_the_script_with_the_fresh_token(self):
        step = _step("Push rescue")
        self.assertIn("push_rescue.py", step["run"])
        self.assertIn("steps.pushtoken.outputs.token", step["env"]["PUSH_TOKEN"])
        # …and falls back to the job-start token if the re-mint itself failed,
        # so the step is never worse than doing nothing.
        self.assertIn("steps.worker.outputs.token", step["env"]["PUSH_TOKEN"])
        self.assertTrue(str(step.get("if", "")).startswith("always()"))

    def test_the_rescue_never_runs_for_a_bounced_or_duplicate_dispatch(self):
        condition = str(_step("Push rescue").get("if", ""))
        self.assertIn("steps.gate.outputs.bounced != 'true'", condition)
        self.assertIn("steps.dedupe.outputs.skip != 'true'", condition)

    def test_the_floor_is_armed_before_the_agent_and_can_reach_linear(self):
        i = _step_index("Credential floor")
        self.assertLess(i, _step_index("Implement card"))
        step = _step("Credential floor")
        self.assertIn("credential_clock.py", step["run"])
        self.assertEqual(
            step["env"]["LINEAR_API_KEY"], "${{ secrets.LINEAR_API_KEY }}"
        )
        self.assertIn("steps.minted.outputs.at", step["env"]["MINTED_AT"])

    def test_the_floor_measures_from_the_mint_not_from_the_agent(self):
        # setup.sh, the pool probes and the card gate all sit between the mint
        # and the agent; a clock started at the agent step would warn late.
        self.assertLess(
            _step_index("Record credential mint time"),
            _step_index("Credential floor"),
        )
        self.assertLess(
            _step_index("Record credential mint time"), _step_index("Implement card")
        )

    def test_the_floor_does_not_block_the_agent_step(self):
        run = _step("Credential floor")["run"]
        self.assertIn("&", run)
        self.assertIn("nohup", run)

    def test_the_report_step_hands_the_classifier_what_it_observed(self):
        run = _step("Report result to Linear")["run"]
        self.assertIn("--work-on-runner", run)
        self.assertIn("steps.rescue.outputs.local_work", run)
        self.assertIn("--credential-expiry", run)
        self.assertIn(check_agent_result.DEATH_CREDENTIAL_EXPIRY, run)


if __name__ == "__main__":
    unittest.main()
