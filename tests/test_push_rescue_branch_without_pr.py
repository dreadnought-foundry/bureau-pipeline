"""A `branch-without-pr` note no longer switches the push rescue off (DRE-6719).

THE INCIDENT (2026-10-10, five build runs). Each finished its card, pushed the
branch, and then could not open the pull request: the run's GitHub credential
had expired, and `gh pr create` answered HTTP 401 Bad credentials. Since
DRE-6443 the brief tells the agent to say exactly that in a blocker note
stamped `blocker-class: branch-without-pr`, and the rescue step read ANY note
as "the agent chose a different exit". So the one step built for this case,
holding two freshly minted tokens, printed

    push rescue: the agent chose a different exit (/tmp/agent-blocker.txt) — nothing to deliver

and stood aside. Every card went to Backlog and waited 4 to 12 minutes for the
sweep's `branch-without-pr` action to open the pull request — and would have
waited for ever with the sweep paused.

What is pinned below:

  1. A note stamped `branch-without-pr` is the rescue's job: it pushes the
     branch if GitHub does not have it and opens the pull request if the card
     has none, with its own token.
  2. Every other note — another class, a stamp-less note, an escalation or a
     hand-back beside it — leaves the rescue a no-op with today's line.
  3. Both rescue tokens refused leaves the note where it is, and the Report
     step posts the blocker exactly as before; the sweep stays the backstop.
  4. A rescue that opened the pull request is reported as an ordinary build
     result — `🤖 PR opened:` and In Review — with no `Agent blocked` comment
     and no move to Backlog, even though the Report step's own PR lookup holds
     the agent's expired token.
  5. Run 38041502892 replayed end to end: real git, a real remote holding the
     pushed branch, the note quoting the 401, and a pull request at the end.

Run: python3 -m pytest tests/test_push_rescue_branch_without_pr.py -v
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("GH_TOKEN", "x")

import push_rescue  # noqa: E402

import test_credential_expiry_scenario as expiry  # noqa: E402
import test_report_agent_result as report_file  # noqa: E402

CARD = "DRE-6583"
BRANCH = f"agent/{CARD}-one-approved-pr"
REPO = "dreadnought-foundry/bureau-pipeline"
CARD_URL = f"https://linear.app/dreadnoughtfoundry/issue/{CARD}/x"
# Run 38041502892's branch head, as its log printed it.
HEAD = "2de2112d" + "0" * 32
PR_URL = f"https://github.com/{REPO}/pull/935"

# The token the AGENT held (dead at the hour) and the rescue's own two mints.
AGENT_TOKEN = "ghs_the_agents_token_dead_at_sixty_minutes"
MINT_1 = "ghs_the_rescues_own_mint"
MINT_2 = "ghs_the_rescues_own_re_mint"

# The note on DRE-6583, from run 38041502892 (02:28 to 03:31 PT).
NOTE_401 = (
    "blocker-class: branch-without-pr\n"
    f"The work is committed and pushed on {BRANCH}. The pull request could "
    "not be opened because the run's GitHub credential expired before "
    "`gh pr create`, which returned HTTP 401 Bad credentials.\n"
)

STAND_ASIDE = "the agent chose a different exit"

REFUSED_401 = "gh: Bad credentials (HTTP 401)"
PUSH_REFUSED_401 = (
    "remote: Invalid username or password.\n"
    f"fatal: unable to access 'https://github.com/{REPO}.git/': "
    "The requested URL returned error: 401"
)


def _token_of(header: str) -> str:
    """The token inside an `AUTHORIZATION: basic …` header value."""
    encoded = header.rsplit(" ", 1)[-1]
    return base64.b64decode(encoded).decode().split(":", 1)[1]


class GitHub:
    """A `run` stand-in that asks the credential question the way GitHub
    does: a refused token is refused by `git ls-remote`, `git push` and `gh`
    alike. Git's token is whichever the rescue last wrote into the
    extraheader; gh's is the GH_TOKEN it was handed."""

    def __init__(self, *, remote_sha="", refused=(), prs=(), create_refused=()):
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []
        self.remote_sha = remote_sha
        self.refused = set(refused)
        self.create_refused = set(create_refused)
        self.prs = list(prs)
        self.git_token = AGENT_TOKEN
        self.created: list[dict] = []

    def __call__(self, argv, *, cwd=None, env=None):
        self.calls.append(list(argv))
        self.envs.append(dict(env or {}))
        if argv[0] == "gh":
            token = (env or {}).get("GH_TOKEN", "")
            if token in self.refused:
                return 1, "", REFUSED_401
            if "list" in argv:
                return 0, json.dumps(self.prs), ""
            if "create" in argv:
                if token in self.create_refused:
                    return 1, "", REFUSED_401
                self.created.append({"argv": list(argv), "token": token})
                self.prs.append({"url": PR_URL})
                return 0, PR_URL + "\n", ""
            raise AssertionError(f"no answer for: {argv}")
        rest = [a for a in argv[1:] if a not in ("-C", cwd, ".")]
        if "config" in rest:
            if (push_rescue.GITHUB_EXTRAHEADER in rest
                    and "--unset-all" not in rest and "--get-all" not in rest):
                self.git_token = _token_of(rest[-1])
            return (1, "", "") if "--get-regexp" in rest else (0, "", "")
        if "for-each-ref" in rest:
            return 0, f"{BRANCH}\n", ""
        if "rev-parse" in rest:
            return 0, f"{HEAD}\n", ""
        if "rev-list" in rest:
            return 0, "4\n", ""
        if "ls-remote" in rest:
            if self.git_token in self.refused:
                return 128, "", PUSH_REFUSED_401
            if not self.remote_sha:
                return 0, "", ""
            return 0, f"{self.remote_sha}\trefs/heads/{BRANCH}\n", ""
        if "push" in rest:
            if self.git_token in self.refused:
                return 128, "", PUSH_REFUSED_401
            self.remote_sha = HEAD
            return 0, "", ""
        if "fetch" in rest:
            return 0, "", ""
        if "format-patch" in rest or "diff" in rest:
            return 0, "diff --git a/f.py b/f.py\n+value = 1\n", ""
        raise AssertionError(f"no answer for: {argv}")

    def pushes(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "git" and "push" in c]

    def gh(self, verb: str = "") -> list[tuple[list[str], dict]]:
        return [(c, e) for c, e in zip(self.calls, self.envs)
                if c[0] == "gh" and (not verb or verb in c)]


class Notes:
    """The agent's three exit files, in a scratch directory of their own —
    the real `/tmp` paths belong to whatever run this suite executes in."""

    def __init__(self, tc: unittest.TestCase):
        self.dir = tempfile.mkdtemp()
        tc.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.escalation = os.path.join(self.dir, "agent-escalation.txt")
        self.blocker = os.path.join(self.dir, "agent-blocker.txt")
        self.handback = os.path.join(self.dir, "agent-handback.txt")
        self.patch = os.path.join(self.dir, f"rescue-{CARD}.patch")

    @property
    def paths(self):
        return (self.escalation, self.blocker, self.handback)

    def write(self, path: str, text: str) -> None:
        Path(path).write_text(text, encoding="utf-8")


def _rescue(tc, github, notes, logged):
    return push_rescue.rescue(
        CARD, REPO, MINT_1, retry_token=MINT_2,
        token_source="the rescue's own mint",
        retry_token_source="the rescue's own re-mint",
        patch_path=notes.patch, base="main", card_url=CARD_URL,
        card_title="one approved pull request the fix agent cannot fix",
        run=github, log=logged.append, stop_notes=notes.paths,
    )


# --------------------------------------------------------------------------- #
# 1. the note is the rescue's job                                              #
# --------------------------------------------------------------------------- #
class ABranchWithoutPrNoteIsDelivered(unittest.TestCase):

    def setUp(self):
        self.notes = Notes(self)
        self.notes.write(self.notes.blocker, NOTE_401)
        self.logged: list[str] = []

    def test_a_pushed_branch_with_no_pr_gets_its_pr_from_the_rescues_token(self):
        github = GitHub(remote_sha=HEAD)
        out = _rescue(self, github, self.notes, self.logged)
        self.assertEqual(github.pushes(), [], "the branch was already on GitHub")
        self.assertEqual(len(github.created), 1, github.calls)
        self.assertEqual(github.created[0]["token"], MINT_1)
        self.assertIn(BRANCH, github.created[0]["argv"])
        self.assertTrue(out.pr_opened)
        self.assertTrue(out.rescued)
        self.assertEqual(out.pr_url, PR_URL)
        self.assertIn("pr_opened=true", push_rescue.output_lines(out))
        self.assertIn(f"pr_url={PR_URL}", push_rescue.output_lines(out))
        text = "\n".join(self.logged)
        self.assertNotIn(STAND_ASIDE, text)
        self.assertIn("branch-without-pr", text)
        self.assertIn(f"opened {PR_URL}", text)

    def test_a_branch_github_does_not_have_is_pushed_then_gets_its_pr(self):
        github = GitHub(remote_sha="")
        out = _rescue(self, github, self.notes, self.logged)
        self.assertEqual(len(github.pushes()), 1, github.calls)
        self.assertTrue(out.pushed)
        self.assertEqual(len(github.created), 1, github.calls)
        push_at = github.calls.index(github.pushes()[0])
        create_at = github.calls.index(github.gh("create")[0][0])
        self.assertLess(push_at, create_at)
        self.assertTrue(out.pr_opened)
        self.assertEqual(out.patch, "")

    def test_a_branch_that_already_has_a_pr_gets_no_second_one(self):
        github = GitHub(remote_sha=HEAD, prs=[{"url": PR_URL}])
        out = _rescue(self, github, self.notes, self.logged)
        self.assertEqual(github.created, [])
        self.assertFalse(out.pr_opened)

    def test_the_pr_body_says_the_rescue_opened_it_on_the_agents_note(self):
        github = GitHub(remote_sha=HEAD)
        _rescue(self, github, self.notes, self.logged)
        argv = github.created[0]["argv"]
        body = argv[argv.index("--body") + 1]
        self.assertIn(CARD_URL, body)
        self.assertIn("push-rescue step, not by the agent", body)
        self.assertIn("branch-without-pr", body)

    def test_the_note_is_left_where_the_agent_wrote_it(self):
        """The rescue reports through its outputs; the note is the agent's,
        and the Report step decides what it means."""
        _rescue(self, GitHub(remote_sha=HEAD), self.notes, self.logged)
        self.assertEqual(Path(self.notes.blocker).read_text(encoding="utf-8"),
                         NOTE_401)


# --------------------------------------------------------------------------- #
# 2. every other note keeps today's stand-aside                                #
# --------------------------------------------------------------------------- #
class EveryOtherNoteStillStandsAside(unittest.TestCase):

    def _assert_no_op(self, files: dict):
        notes = Notes(self)
        for attr, text in files.items():
            notes.write(getattr(notes, attr), text)
        logged: list[str] = []
        github = GitHub(remote_sha=HEAD)
        out = _rescue(self, github, notes, logged)
        self.assertEqual(github.calls, [], "the rescue touched GitHub")
        self.assertFalse(out.rescued)
        self.assertEqual(out.patch, "")
        self.assertIn(STAND_ASIDE, "\n".join(logged))

    def test_every_other_class_is_a_no_op(self):
        for cls in ("nothing-to-change", "wrong-repo", "question"):
            with self.subTest(cls=cls):
                self._assert_no_op(
                    {"blocker": f"blocker-class: {cls}\nrepo: agent-bureau\nwhy\n"})

    def test_a_note_with_no_class_line_is_a_no_op(self):
        self._assert_no_op({"blocker": "the API this card names does not exist\n"})

    def test_a_note_whose_wording_alone_names_the_class_is_a_no_op(self):
        """A stamp-less note of this class may be a branch held on purpose
        (DRE-6056, docs/blocker-classes.md): only the stamp says the pull
        request is simply missing."""
        self._assert_no_op({"blocker": (
            f"The work is pushed on the branch {BRANCH} and I opened no pull "
            "request — waiting on parity numbers.\n")})

    def test_the_stamp_counts_only_on_the_first_line(self):
        self._assert_no_op(
            {"blocker": "held on purpose\nblocker-class: branch-without-pr\n"})

    def test_an_escalation_beside_the_note_is_a_no_op(self):
        self._assert_no_op({"blocker": NOTE_401,
                            "escalation": "Should A or B ship first?\n"})

    def test_a_hand_back_beside_the_note_is_a_no_op(self):
        self._assert_no_op({"blocker": NOTE_401,
                            "handback": "piece one\npiece two\n"})

    def test_an_escalation_or_a_hand_back_alone_is_a_no_op(self):
        self._assert_no_op({"escalation": "Should A or B ship first?\n"})
        self._assert_no_op({"handback": "piece one\npiece two\n"})


# --------------------------------------------------------------------------- #
# 3. both tokens refused: the blocker stands, exactly as today                 #
# --------------------------------------------------------------------------- #
class BothTokensRefusedLeavesTheBlocker(unittest.TestCase):

    def setUp(self):
        self.notes = Notes(self)
        self.notes.write(self.notes.blocker, NOTE_401)
        self.logged: list[str] = []

    def test_a_refused_push_on_both_tokens_opens_nothing(self):
        github = GitHub(remote_sha=HEAD, refused={MINT_1, MINT_2})
        out = _rescue(self, github, self.notes, self.logged)
        self.assertEqual(len(github.pushes()), 2, "one push per token")
        self.assertEqual(github.created, [])
        self.assertFalse(out.pr_opened)
        self.assertFalse(out.rescued)
        self.assertEqual(Path(self.notes.blocker).read_text(encoding="utf-8"),
                         NOTE_401)

    def test_a_refused_pr_create_is_retried_on_the_second_token(self):
        github = GitHub(remote_sha=HEAD, create_refused={MINT_1})
        out = _rescue(self, github, self.notes, self.logged)
        tokens = [e.get("GH_TOKEN") for _, e in github.gh("create")]
        self.assertEqual(tokens, [MINT_1, MINT_2])
        self.assertTrue(out.pr_opened)
        self.assertEqual(github.created[0]["token"], MINT_2)

    def test_a_pr_create_refused_on_both_tokens_opens_nothing(self):
        github = GitHub(remote_sha=HEAD, create_refused={MINT_1, MINT_2})
        out = _rescue(self, github, self.notes, self.logged)
        self.assertEqual(len(github.gh("create")), 2)
        self.assertFalse(out.pr_opened)
        self.assertIn("401", out.error)
        self.assertEqual(Path(self.notes.blocker).read_text(encoding="utf-8"),
                         NOTE_401)

    def test_the_report_step_posts_the_blocker_as_today(self):
        """The refused rescue's REAL outputs, handed to the REAL Report
        script: the `branch-without-pr` marker and the park, nothing else."""
        github = GitHub(remote_sha=HEAD, refused={MINT_1, MINT_2})
        out = _rescue(self, github, self.notes, self.logged)
        posted, moved = _report(self, out, NOTE_401, card_pr=CARD_PR_UNREADABLE)
        self.assertEqual(1, len(posted), posted)
        self.assertTrue(posted[0].startswith(
            "🛑 Agent blocked: class=branch-without-pr · "), posted[0])
        self.assertEqual([[report_file.CARD, "Queued", "--park"]], moved)


# --------------------------------------------------------------------------- #
# 4. the Report step: a delivered note is an ordinary build result             #
# --------------------------------------------------------------------------- #

#: `card_pr.py find` holding the agent's expired token: exit 3, UNREADABLE.
CARD_PR_UNREADABLE = '''#!/usr/bin/env python3
import sys
print("gh: Bad credentials (HTTP 401)", file=sys.stderr)
sys.exit(3)
'''


def _outputs(out) -> dict:
    return dict(line.split("=", 1) for line in push_rescue.output_lines(out))


def _report(tc, out, note: str, *, card_pr: str):
    """Run the real Report script with the rescue's outputs wired the way
    agent-task.yml wires them, and the agent's note in a notes directory of
    this test's own (DRE-6961)."""
    notes = report_file.notes_with(tc, {report_file.BLOCKER: note})
    values = _outputs(out)
    proc, journal = report_file.run_pr_opened(
        tc, ["bash", report_file.SCRIPT], card_pr=card_pr,
        env_extra={
            "AGENT_NOTES_DIR": notes,
            "RESCUE_LOCAL_WORK": values["local_work"],
            "RESCUE_PUSHED": values["pushed"],
            "RESCUE_PUSH_STATUS": values["push_status"],
            "RESCUE_ERROR": values["error"],
            "RESCUE_PATCH": values["patch"],
            "RESCUE_PR_OPENED": values["pr_opened"],
            "RESCUE_PR_URL": values["pr_url"],
        },
    )
    tc.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
    posted = [e["args"][1] for e in journal if e["op"] == "comment"]
    moved = [e["args"] for e in journal if e["op"] in ("advance", "state")]
    return posted, moved


class ADeliveredNoteIsAnOrdinaryBuildResult(unittest.TestCase):

    def _delivered(self):
        notes = Notes(self)
        notes.write(notes.blocker, NOTE_401)
        out = _rescue(self, GitHub(remote_sha=HEAD), notes, [])
        self.assertTrue(out.pr_opened)
        return out

    def _assert_ordinary(self, posted, moved):
        run = (f"https://github.com/{report_file.REPO}/actions/runs/"
               f"{report_file.RUN_ID}")
        self.assertEqual(
            [f"🤖 PR opened: {PR_URL} — CI + critic review running. Run: {run}"],
            posted)
        self.assertEqual([[report_file.CARD, "In Review", "In Progress"]],
                         moved)
        for comment in posted:
            self.assertNotIn("Agent blocked", comment)
        self.assertNotIn("Queued", [m[1] for m in moved])

    def test_the_lookups_expired_token_does_not_hide_the_rescues_pr(self):
        posted, moved = _report(self, self._delivered(), NOTE_401,
                                card_pr=CARD_PR_UNREADABLE)
        self._assert_ordinary(posted, moved)

    def test_a_lookup_that_answers_no_pr_does_not_hide_it_either(self):
        posted, moved = _report(self, self._delivered(), NOTE_401,
                                card_pr=report_file.CARD_PR_NO_PR_STUB)
        self._assert_ordinary(posted, moved)

    def test_a_rescue_that_opened_nothing_changes_nothing(self):
        """`pr_opened=false` with a url — a pull request the rescue FOUND —
        is not this run's delivery; the note is posted as today."""
        out = push_rescue.Outcome()
        out.pr_url = PR_URL
        posted, moved = _report(self, out, NOTE_401,
                                card_pr=report_file.CARD_PR_NO_PR_STUB)
        self.assertTrue(posted[0].startswith(
            "🛑 Agent blocked: class=branch-without-pr · "), posted)
        self.assertEqual([[report_file.CARD, "Queued", "--park"]], moved)

    def test_a_url_that_is_not_a_github_pull_request_is_not_reported(self):
        out = push_rescue.Outcome()
        out.pr_opened = True
        out.pr_url = "https://example.com/pull/1 && echo"
        posted, moved = _report(self, out, NOTE_401,
                                card_pr=report_file.CARD_PR_NO_PR_STUB)
        self.assertTrue(posted[0].startswith("🛑 Agent blocked"), posted)
        self.assertNotIn("example.com", " ".join(posted))


# --------------------------------------------------------------------------- #
# 5. the build identity, and no token printed                                  #
# --------------------------------------------------------------------------- #
class TheBuildIdentityOpensItAndNoTokenIsPrinted(unittest.TestCase):

    def setUp(self):
        notes = Notes(self)
        notes.write(notes.blocker, NOTE_401)
        self.logged: list[str] = []
        self.github = GitHub(remote_sha="")
        self.out = _rescue(self, self.github, notes, self.logged)

    def test_every_gh_call_spends_a_rescue_mint_never_the_agents_token(self):
        """The rescue's mints are the build pool's own App
        (tests/test_rescue_push_token.py pins the workflow side), so a pull
        request opened with them is authored by the build identity."""
        for _, env in self.github.gh():
            self.assertIn(env.get("GH_TOKEN"), (MINT_1, MINT_2))
            self.assertEqual(env.get("GH_TOKEN"), env.get("GITHUB_TOKEN"))
        self.assertEqual(self.github.created[0]["token"], MINT_1)

    def test_no_token_reaches_the_log_or_the_outputs(self):
        printed = "\n".join(self.logged + push_rescue.output_lines(self.out))
        for token in (MINT_1, MINT_2, AGENT_TOKEN):
            self.assertNotIn(token, printed)
            self.assertNotIn(base64.b64encode(
                f"x-access-token:{token}".encode()).decode(), printed)

    def test_no_token_is_ever_an_argv_element(self):
        for call in self.github.calls:
            joined = " ".join(call)
            for token in (MINT_1, MINT_2):
                self.assertNotIn(token, joined)


# --------------------------------------------------------------------------- #
# 6. run 38041502892, replayed end to end                                      #
# --------------------------------------------------------------------------- #
class Run38041502892Replayed(unittest.TestCase):
    """The REAL `Push rescue` step against a real repository whose real
    remote already holds the agent's branch, the agent's note at its real
    path, and a `gh` that refuses the agent's expired token — then the REAL
    Report script with what that step wrote to `$GITHUB_OUTPUT`."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.fx = expiry.Fixture(td.name, token=expiry.FRESH_TOKEN)
        # blocker_class reads the vocabulary from the pipeline checkout, as
        # the runner's `.bureau-pipeline/config` provides it.
        shutil.copytree(ROOT / "config",
                        self.fx.work / ".bureau-pipeline" / "config")
        self.fx.park_the_stop_notes(self.addCleanup)
        self.head = self.fx.agent_finished_its_work()
        # The agent pushed before its token died; only `gh pr create` failed.
        self.fx._git("push", "-q", str(self.fx.bare),
                     f"{expiry.BRANCH}:refs/heads/{expiry.BRANCH}")
        self.assertEqual(self.fx.remote_sha(), self.head)
        Path(push_rescue.STOP_NOTES[1]).write_text(
            NOTE_401.replace(BRANCH, expiry.BRANCH), encoding="utf-8")
        self.addCleanup(lambda: os.path.exists(push_rescue.STOP_NOTES[1])
                        and os.remove(push_rescue.STOP_NOTES[1]))
        self.proc = self.fx.run_the_rescue_step()

    def test_the_pull_request_is_opened_in_the_run(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        creates = [c for c in self.fx.gh_calls() if c[:2] == ["pr", "create"]]
        self.assertEqual(len(creates), 1, self.proc.stderr)
        self.assertIn(expiry.BRANCH, creates[0])
        out = self.fx.outputs()
        self.assertEqual(out.get("pr_opened"), "true", self.proc.stderr)
        self.assertEqual(out.get("pushed"), "false", "nothing needed pushing")
        self.assertNotIn(STAND_ASIDE, self.proc.stderr)

    def test_the_remote_branch_is_untouched(self):
        self.assertEqual(self.fx.remote_sha(), self.head)

    def test_no_token_is_printed(self):
        printed = self.proc.stdout + self.proc.stderr
        self.assertNotIn(expiry.FRESH_TOKEN, printed)
        self.assertNotIn(expiry.STALE_TOKEN, printed)

    def test_the_card_gets_the_pull_request_and_no_blocker(self):
        out = self.fx.outputs()
        notes = report_file.notes_with(self, {report_file.BLOCKER: NOTE_401})
        proc, journal = report_file.run_pr_opened(
            self, ["bash", report_file.SCRIPT], card_pr=CARD_PR_UNREADABLE,
            env_extra={
                "AGENT_NOTES_DIR": notes,
                "RESCUE_LOCAL_WORK": out["local_work"],
                "RESCUE_PUSHED": out["pushed"],
                "RESCUE_PUSH_STATUS": out["push_status"],
                "RESCUE_ERROR": out["error"],
                "RESCUE_PATCH": out["patch"],
                "RESCUE_PR_OPENED": out["pr_opened"],
                "RESCUE_PR_URL": out["pr_url"],
            },
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        posted = [e["args"][1] for e in journal if e["op"] == "comment"]
        self.assertEqual(1, len(posted), posted)
        self.assertTrue(posted[0].startswith(f"🤖 PR opened: {out['pr_url']} "),
                        posted)
        moved = [e["args"] for e in journal if e["op"] in ("advance", "state")]
        self.assertEqual([[report_file.CARD, "In Review", "In Progress"]],
                         moved)


if __name__ == "__main__":
    unittest.main()
