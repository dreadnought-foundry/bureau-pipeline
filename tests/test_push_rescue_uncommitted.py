"""A build that ends with uncommitted work says so, and the work is saved (DRE-6959).

THE INCIDENT (2026-10-11, run 38164304144, card DRE-6950). The build agent
committed its failing test, made the change in its working tree, started the
web suite in the background and ended its turn — which in a headless run ends
the session. The push rescue delivered the one commit that existed and opened
a pull request holding a failing test and nothing else. `RESCUE_LOCAL_WORK`
read `true` and `RESCUE_PATCH` was empty: `rescue()` looks only at commits, so
the change itself was never saved anywhere and nobody was told it existed.

What is pinned below, against a REAL throwaway repository (`git init` in a
temporary directory) with only the push, the remote reads and `gh` faked:

  1. `--uncommitted <path>` saves the working tree against HEAD — modified,
     deleted and untracked files git does not ignore — as one `git diff
     --binary` built on a throwaway index, so `git apply` onto HEAD reproduces
     it and the agent's own index and working tree are untouched.
  2. It is a SEPARATE file from the `--patch` series `deliver_rescue.py`
     replays with `git am`, and it moves no existing output: uncommitted edits
     alone never set `local_work` and never write the patch.
  3. Where it is said, one row of the card's table per test: in the body of a
     pull request the rescue opens, as one comment on a pull request that
     already exists, and nowhere on GitHub when there is no pull request or
     the agent wrote a stop note.
  4. Without the flag, the rescue issues no new git command at all.
  5. agent-task.yml passes the flag, uploads the file and names it on the
     card; proof-task.yml and agent-fix.yml do not pass it.

Run: python3 -m pytest tests/test_push_rescue_uncommitted.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("GH_TOKEN", "x")

import push_rescue  # noqa: E402

WF_DIR = ROOT / ".github" / "workflows"
TASK = WF_DIR / "agent-task.yml"

CARD = "DRE-6950"
BRANCH = f"agent/{CARD}-overview-asks-the-server"
REPO = "dreadnought-foundry/agent-bureau"
CARD_URL = f"https://linear.app/dreadnoughtfoundry/issue/{CARD}/x"
PR_URL = f"https://github.com/{REPO}/pull/3673"
ARTIFACT = f"rescue-{CARD}.uncommitted.diff"
HEADING = "The build ended with uncommitted work."

MINT_1 = "ghs_the_rescues_own_mint"
MINT_2 = "ghs_the_rescues_own_re_mint"

# The git verbs that are new with the flag. Without it none of them may run.
CAPTURE_VERBS = {"status", "diff", "read-tree", "add"}


def git(repo: str, *args: str) -> str:
    done = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                          text=True, check=True)
    return done.stdout


def git_verb(argv: list[str]) -> str:
    """The subcommand of a git argv, past `-C <dir>`, `-c k=v` and `--flags`."""
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in ("-C", "-c"):
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        return arg
    return ""


class GitHub:
    """A `run` that answers git locally for real, and fakes only GitHub.

    `push`, `ls-remote` and `fetch` are the remote's; `gh` is GitHub's API.
    Every other git command runs against the throwaway repository, which is
    the point: the capture's diff, index and status are real git's."""

    def __init__(self, *, remote_has_branch=False, prs=(), list_fails=False,
                 create_fails=False, comment_fails=False):
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []
        self.remote_has_branch = remote_has_branch
        self.prs = list(prs)
        self.list_fails = list_fails
        self.create_fails = create_fails
        self.comment_fails = comment_fails
        self.local_head = ""

    def __call__(self, argv, *, cwd=None, env=None):
        self.calls.append(list(argv))
        self.envs.append(dict(env or {}))
        if argv[0] == "gh":
            if "list" in argv:
                if self.list_fails:
                    return 1, "", "gh: Bad credentials (HTTP 401)"
                return 0, json.dumps(self.prs), ""
            if "create" in argv:
                if self.create_fails:
                    return 1, "", "gh: Bad credentials (HTTP 401)"
                self.prs.append({"url": PR_URL})
                return 0, PR_URL + "\n", ""
            if "comment" in argv:
                if self.comment_fails:
                    return 1, "", "gh: Bad credentials (HTTP 401)"
                return 0, PR_URL + "#issuecomment-1\n", ""
            raise AssertionError(f"no answer for: {argv}")
        verb = git_verb(argv)
        if verb == "ls-remote":
            if not self.remote_has_branch:
                return 0, "", ""
            return 0, f"{self.local_head}\trefs/heads/{BRANCH}\n", ""
        if verb == "push":
            self.remote_has_branch = True
            return 0, "", ""
        if verb == "fetch":
            return 0, "", ""
        return push_rescue._subprocess_run(argv, cwd=cwd, env=env)

    def git_verbs(self) -> list[str]:
        return [git_verb(c) for c in self.calls if c[0] == "git"]

    def pushes(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "git" and git_verb(c) == "push"]

    def gh(self, verb: str = "") -> list[tuple[list[str], dict]]:
        return [(c, e) for c, e in zip(self.calls, self.envs)
                if c[0] == "gh" and (not verb or verb in c)]


class Workspace:
    """A product checkout as the build agent leaves it: one commit on its
    card branch that GitHub does not have, a modified tracked file, a new
    untracked file, and a file git is told to ignore through
    `.git/info/exclude` — the same mechanism that hides `.bureau-pipeline/`."""

    def __init__(self, tc: unittest.TestCase, *, commits_ahead=True,
                 branch=BRANCH, dirty=True):
        self.dir = tempfile.mkdtemp()
        tc.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.repo = os.path.join(self.dir, "repo")
        self.scratch = os.path.join(self.dir, "scratch")
        os.makedirs(self.repo)
        os.makedirs(self.scratch)
        r = self.repo
        git(r, "init", "-q", "-b", "main")
        git(r, "config", "user.email", "agent@example.com")
        git(r, "config", "user.name", "agent")
        git(r, "config", "commit.gpgsign", "false")
        self.write("app.py", "VALUE = 1\n")
        self.write("old.py", "GONE = True\n")
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "base")
        if branch:
            git(r, "checkout", "-q", "-b", branch)
        if commits_ahead:
            self.write("tests/test_app.py", "def test_value():\n    assert False\n")
            git(r, "add", "-A")
            git(r, "commit", "-q", "-m", "test: RED")
        with open(os.path.join(r, ".git", "info", "exclude"), "a",
                  encoding="utf-8") as fh:
            fh.write(".bureau-pipeline/\nsecret.log\n")
        self.write(".bureau-pipeline/agent-context.md", "context\n")
        self.write("secret.log", "not the agent's work\n")
        if dirty:
            self.write("app.py", "VALUE = 2\n")
            self.write("web/overview.ts", "export const asks = 'only the board';\n")
            os.remove(os.path.join(r, "old.py"))
        self.head = git(r, "rev-parse", "HEAD").strip()
        self.uncommitted = os.path.join(self.scratch, ARTIFACT)
        self.patch = os.path.join(self.scratch, f"rescue-{CARD}.patch")
        self.escalation = os.path.join(self.scratch, "agent-escalation.txt")
        self.blocker = os.path.join(self.scratch, "agent-blocker.txt")
        self.handback = os.path.join(self.scratch, "agent-handback.txt")

    def write(self, rel: str, text: str) -> None:
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def status(self) -> str:
        return git(self.repo, "status", "--porcelain", "--untracked-files=all")

    def index_bytes(self) -> bytes:
        return Path(self.repo, ".git", "index").read_bytes()

    @property
    def notes(self):
        return (self.escalation, self.blocker, self.handback)

    def rescue(self, github: GitHub, logged: list, *, flag=True):
        github.local_head = self.head
        return push_rescue.rescue(
            CARD, REPO, MINT_1, retry_token=MINT_2,
            token_source="the rescue's own mint",
            retry_token_source="the rescue's own re-mint",
            patch_path=self.patch, base="main", card_url=CARD_URL,
            card_title="the overview asks the server only for what the board shows",
            workdir=self.repo, run=github, log=logged.append,
            stop_notes=self.notes,
            uncommitted_path=self.uncommitted if flag else "",
        )


def lines(outcome) -> dict:
    return dict(line.split("=", 1) for line in push_rescue.output_lines(outcome))


# --------------------------------------------------------------------------- #
# 1. DRE-6950's case: commits not on GitHub, and the rescue opens the PR      #
# --------------------------------------------------------------------------- #
class TheIncidentReplayed(unittest.TestCase):

    def setUp(self):
        self.ws = Workspace(self)
        self.status_before = self.ws.status()
        self.index_before = self.ws.index_bytes()
        self.github = GitHub()
        self.logged: list[str] = []
        self.out = self.ws.rescue(self.github, self.logged)

    def test_the_output_names_the_saved_file(self):
        self.assertEqual(self.out.uncommitted, self.ws.uncommitted)
        self.assertEqual(lines(self.out)["uncommitted"], self.ws.uncommitted)
        self.assertTrue(os.path.isfile(self.ws.uncommitted))

    def test_the_first_line_names_the_commit_it_applies_onto(self):
        first = Path(self.ws.uncommitted).read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first, f"# applies onto {self.ws.head}")

    def test_git_apply_onto_a_clean_checkout_reproduces_both_edits_and_nothing_else(self):
        clone = os.path.join(self.ws.dir, "clone")
        subprocess.run(["git", "clone", "-q", self.ws.repo, clone], check=True)
        git(clone, "checkout", "-q", self.ws.head)
        git(clone, "apply", "--check", self.ws.uncommitted)
        git(clone, "apply", self.ws.uncommitted)
        self.assertEqual(Path(clone, "app.py").read_text(), "VALUE = 2\n")
        self.assertEqual(Path(clone, "web", "overview.ts").read_text(),
                         "export const asks = 'only the board';\n")
        self.assertFalse(Path(clone, "old.py").exists())
        self.assertFalse(Path(clone, "secret.log").exists())
        self.assertFalse(Path(clone, ".bureau-pipeline").exists())

    def test_the_agents_working_tree_and_index_are_left_exactly_as_they_were(self):
        self.assertEqual(self.ws.status(), self.status_before)
        self.assertEqual(self.ws.index_bytes(), self.index_before)

    def test_the_replayable_patch_is_not_written_and_local_work_is_unchanged(self):
        clean = Workspace(self, dirty=False)
        clean_out = clean.rescue(GitHub(), [])
        self.assertEqual(self.out.patch, "")
        self.assertEqual(lines(self.out)["patch"], "")
        self.assertFalse(os.path.exists(self.ws.patch))
        self.assertEqual(self.out.local_work, clean_out.local_work)
        for key in ("local_work", "pushed", "pr_opened", "rescued", "push_status",
                    "attempts", "patch", "error"):
            with self.subTest(output=key):
                self.assertEqual(lines(self.out)[key], lines(clean_out)[key])

    def test_the_pull_request_body_carries_the_paragraph_naming_the_artifact(self):
        created = self.github.gh("create")
        self.assertEqual(len(created), 1)
        argv = created[0][0]
        body = argv[argv.index("--body") + 1]
        self.assertIn(f"**{HEADING}**", body)
        self.assertIn(ARTIFACT, body)
        self.assertIn("not in this pull request", body)
        self.assertEqual(self.github.gh("comment"), [])

    def test_the_branch_was_still_delivered(self):
        self.assertTrue(self.out.pushed)
        self.assertTrue(self.out.pr_opened)


# --------------------------------------------------------------------------- #
# 2. the other rows of the table                                              #
# --------------------------------------------------------------------------- #
class APullRequestThatAlreadyExistsGetsOneComment(unittest.TestCase):

    def test_the_agents_own_pull_request_gets_one_comment_and_no_second_pr(self):
        ws = Workspace(self)
        github = GitHub(remote_has_branch=True, prs=[{"url": PR_URL}])
        out = ws.rescue(github, [])
        self.assertEqual(out.uncommitted, ws.uncommitted)
        self.assertEqual(github.gh("create"), [])
        comments = github.gh("comment")
        self.assertEqual(len(comments), 1)
        argv, env = comments[0]
        self.assertIn(PR_URL, argv)
        body = argv[argv.index("--body") + 1]
        self.assertIn(f"**{HEADING}**", body)
        self.assertIn(ARTIFACT, body)
        self.assertEqual(env.get("GH_TOKEN"), MINT_1)
        self.assertEqual(github.pushes(), [])
        self.assertFalse(out.local_work)
        self.assertEqual(out.pr_url, PR_URL)

    def test_the_comment_uses_the_token_whose_lookup_worked(self):
        ws = Workspace(self)
        github = GitHub(remote_has_branch=True, prs=[{"url": PR_URL}])

        def refuse_mint_1(argv, *, cwd=None, env=None):
            if argv[0] == "gh" and (env or {}).get("GH_TOKEN") == MINT_1:
                github.calls.append(list(argv))
                github.envs.append(dict(env or {}))
                return 1, "", "gh: Bad credentials (HTTP 401)"
            return github(argv, cwd=cwd, env=env)

        github.local_head = ws.head
        push_rescue.rescue(
            CARD, REPO, MINT_1, retry_token=MINT_2, patch_path=ws.patch,
            base="main", card_url=CARD_URL, workdir=ws.repo,
            run=refuse_mint_1, log=[].append, stop_notes=ws.notes,
            uncommitted_path=ws.uncommitted,
        )
        comments = github.gh("comment")
        self.assertEqual([e.get("GH_TOKEN") for _, e in comments], [MINT_2])

    def test_a_comment_github_refuses_is_logged_and_the_file_is_still_saved(self):
        ws = Workspace(self)
        github = GitHub(remote_has_branch=True, prs=[{"url": PR_URL}],
                        comment_fails=True)
        logged: list[str] = []
        out = ws.rescue(github, logged)
        self.assertEqual(out.uncommitted, ws.uncommitted)
        self.assertTrue(os.path.isfile(ws.uncommitted))
        self.assertTrue(any("comment" in line for line in logged), logged)
        self.assertEqual(out.pr_url, PR_URL)


class NoPullRequestNothingSaidOnGitHub(unittest.TestCase):

    def _assert_saved_and_silent(self, ws, github, out):
        self.assertEqual(out.uncommitted, ws.uncommitted)
        self.assertTrue(os.path.isfile(ws.uncommitted))
        self.assertEqual(github.pushes(), [])
        self.assertEqual(github.gh(), [])
        self.assertFalse(out.local_work)
        self.assertEqual(out.patch, "")

    def test_a_branch_with_no_commits_ahead_of_the_base(self):
        ws = Workspace(self, commits_ahead=False)
        github = GitHub()
        self._assert_saved_and_silent(ws, github, ws.rescue(github, []))

    def test_no_card_branch_at_all(self):
        ws = Workspace(self, commits_ahead=False, branch="")
        github = GitHub()
        self._assert_saved_and_silent(ws, github, ws.rescue(github, []))

    def test_each_stop_note_saves_the_file_and_pushes_and_opens_nothing(self):
        for which, text in (("escalation", "Which way should the overview read?\n"),
                            ("handback", "- piece one\n- piece two\n"),
                            ("blocker", "blocker-class: question\nFinding: x\n")):
            with self.subTest(note=which):
                ws = Workspace(self)
                Path(getattr(ws, which)).write_text(text, encoding="utf-8")
                github = GitHub()
                self._assert_saved_and_silent(ws, github, ws.rescue(github, []))


class ABranchWithoutPrNoteIsTheFirstTwoRows(unittest.TestCase):

    NOTE = (f"blocker-class: branch-without-pr\nThe work is committed on {BRANCH} "
            "and the pull request could not be opened.\n")

    def test_the_pull_request_the_rescue_opens_carries_the_paragraph(self):
        ws = Workspace(self)
        Path(ws.blocker).write_text(self.NOTE, encoding="utf-8")
        github = GitHub(remote_has_branch=True)
        out = ws.rescue(github, [])
        self.assertTrue(out.pr_opened)
        argv = github.gh("create")[0][0]
        self.assertIn(ARTIFACT, argv[argv.index("--body") + 1])
        self.assertEqual(github.gh("comment"), [])

    def test_an_existing_pull_request_gets_the_comment(self):
        ws = Workspace(self)
        Path(ws.blocker).write_text(self.NOTE, encoding="utf-8")
        github = GitHub(remote_has_branch=True, prs=[{"url": PR_URL}])
        ws.rescue(github, [])
        self.assertEqual(github.gh("create"), [])
        self.assertEqual(len(github.gh("comment")), 1)


class AnUnreadableLookupOrARefusedCreateSaysNothing(unittest.TestCase):

    def test_an_unreadable_lookup_saves_the_file_and_comments_nowhere(self):
        ws = Workspace(self)
        github = GitHub(remote_has_branch=True, list_fails=True)
        out = ws.rescue(github, [])
        self.assertEqual(out.uncommitted, ws.uncommitted)
        self.assertEqual(github.gh("create"), [])
        self.assertEqual(github.gh("comment"), [])

    def test_a_refused_create_saves_the_file(self):
        ws = Workspace(self)
        github = GitHub(remote_has_branch=True, create_fails=True)
        out = ws.rescue(github, [])
        self.assertEqual(out.uncommitted, ws.uncommitted)
        self.assertFalse(out.pr_opened)
        self.assertEqual(github.gh("comment"), [])


# --------------------------------------------------------------------------- #
# 3. a clean tree, no flag, and a capture that fails                          #
# --------------------------------------------------------------------------- #
class WhenThereIsNothingToSay(unittest.TestCase):

    def test_a_clean_tree_writes_no_file_and_an_empty_output(self):
        ws = Workspace(self, dirty=False)
        github = GitHub()
        out = ws.rescue(github, [])
        self.assertEqual(out.uncommitted, "")
        self.assertEqual(lines(out)["uncommitted"], "")
        self.assertFalse(os.path.exists(ws.uncommitted))
        argv = github.gh("create")[0][0]
        self.assertNotIn(HEADING, argv[argv.index("--body") + 1])

    def test_only_ignored_files_count_as_a_clean_tree(self):
        # The fixture's `.bureau-pipeline/` and `secret.log` are both on disk
        # and both excluded; with nothing else changed there is nothing to say.
        ws = Workspace(self, dirty=False)
        self.assertTrue(Path(ws.repo, "secret.log").exists())
        out = ws.rescue(GitHub(), [])
        self.assertEqual(out.uncommitted, "")

    def test_without_the_flag_no_capture_command_is_issued_at_all(self):
        ws = Workspace(self)
        github = GitHub()
        out = ws.rescue(github, [], flag=False)
        self.assertEqual(set(github.git_verbs()) & CAPTURE_VERBS, set())
        self.assertEqual(out.uncommitted, "")
        self.assertEqual(lines(out)["uncommitted"], "")
        self.assertFalse(os.path.exists(ws.uncommitted))
        argv = github.gh("create")[0][0]
        self.assertNotIn(HEADING, argv[argv.index("--body") + 1])

    def test_the_flag_is_what_issues_them(self):
        # Non-vacuous: the same run with the flag does read the tree.
        ws = Workspace(self)
        github = GitHub()
        ws.rescue(github, [])
        self.assertTrue(set(github.git_verbs()) & CAPTURE_VERBS)

    def test_a_capture_that_fails_is_logged_and_swallowed(self):
        ws = Workspace(self)
        github = GitHub()
        logged: list[str] = []
        github.local_head = ws.head
        out = push_rescue.rescue(
            CARD, REPO, MINT_1, retry_token=MINT_2, patch_path=ws.patch,
            base="main", card_url=CARD_URL, workdir=ws.repo, run=github,
            log=logged.append, stop_notes=ws.notes,
            uncommitted_path=os.path.join(ws.dir, "no-such-dir", ARTIFACT),
        )
        self.assertEqual(out.uncommitted, "")
        self.assertTrue(out.pushed)
        self.assertTrue(out.pr_opened)
        self.assertTrue(any("uncommitted" in line for line in logged), logged)

    def test_a_run_seam_that_raises_during_the_capture_is_swallowed(self):
        ws = Workspace(self)
        github = GitHub()

        def explode_on_status(argv, *, cwd=None, env=None):
            if argv[0] == "git" and git_verb(argv) == "status":
                raise OSError("git vanished")
            return github(argv, cwd=cwd, env=env)

        github.local_head = ws.head
        out = push_rescue.rescue(
            CARD, REPO, MINT_1, retry_token=MINT_2, patch_path=ws.patch,
            base="main", card_url=CARD_URL, workdir=ws.repo,
            run=explode_on_status, log=[].append, stop_notes=ws.notes,
            uncommitted_path=ws.uncommitted,
        )
        self.assertEqual(out.uncommitted, "")
        self.assertTrue(out.pushed)


class TheOutputLine(unittest.TestCase):

    def test_uncommitted_comes_after_every_existing_line(self):
        keys = [line.split("=", 1)[0] for line in
                push_rescue.output_lines(push_rescue.Outcome())]
        self.assertEqual(keys, [
            "branch", "local_work", "pushed", "pr_opened", "rescued", "pr_url",
            "push_status", "attempts", "patch", "error", "target_branch",
            "sidecar", "uncommitted",
        ])

    def test_the_cli_passes_the_flag_through(self):
        seen = {}

        def fake_rescue(*args, **kwargs):
            seen.update(kwargs)
            out = push_rescue.Outcome()
            out.uncommitted = kwargs.get("uncommitted_path", "")
            return out

        original = push_rescue.rescue
        push_rescue.rescue = fake_rescue
        try:
            from contextlib import redirect_stdout
            import io
            buf = io.StringIO()
            with redirect_stdout(buf):
                push_rescue.main(["rescue", "--card", CARD, "--repo", REPO,
                                  "--uncommitted", "/tmp/x.diff"])
        finally:
            push_rescue.rescue = original
        self.assertEqual(seen.get("uncommitted_path"), "/tmp/x.diff")
        self.assertIn("uncommitted=/tmp/x.diff", buf.getvalue().splitlines())

    def test_the_cli_without_the_flag_passes_none(self):
        seen = {}

        def fake_rescue(*args, **kwargs):
            seen.update(kwargs)
            return push_rescue.Outcome()

        original = push_rescue.rescue
        push_rescue.rescue = fake_rescue
        try:
            from contextlib import redirect_stdout
            import io
            with redirect_stdout(io.StringIO()):
                push_rescue.main(["rescue", "--card", CARD, "--repo", REPO])
        finally:
            push_rescue.rescue = original
        self.assertEqual(seen.get("uncommitted_path", ""), "")


# --------------------------------------------------------------------------- #
# 4. the workflow wiring                                                      #
# --------------------------------------------------------------------------- #
def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def steps(workflow: dict) -> list[dict]:
    out: list[dict] = []
    for job in (workflow.get("jobs") or {}).values():
        out.extend(job.get("steps") or [])
    return out


def named(all_steps: list[dict], name: str) -> dict:
    found = [s for s in all_steps if s.get("name") == name]
    assert len(found) == 1, f"expected exactly one {name!r} step"
    return found[0]


GATE_PARTS = (
    "always()",
    "steps.rescue.outputs.uncommitted != ''",
    "steps.gate.outputs.bounced != 'true'",
    "steps.dedupe.outputs.skip != 'true'",
)


class TheBuildWorkflowSavesAndNamesTheFile(unittest.TestCase):

    def setUp(self):
        self.steps = steps(load(TASK))

    def test_push_rescue_passes_the_flag(self):
        run = named(self.steps, "Push rescue")["run"]
        self.assertIn('--uncommitted "${RUNNER_TEMP:-/tmp}/rescue-$CARD.uncommitted.diff"', run)
        # The replayable series is still its own file.
        self.assertIn('--patch "${RUNNER_TEMP:-/tmp}/rescue-$CARD.patch"', run)

    def test_the_two_new_steps_follow_upload_rescued_work(self):
        names = [s.get("name") for s in self.steps]
        at = names.index("Upload rescued work")
        self.assertEqual(names[at + 1], "Upload uncommitted work")
        self.assertEqual(names[at + 2], "Name uncommitted work on the card")

    def test_both_are_gated_and_continue_on_error(self):
        for name in ("Upload uncommitted work", "Name uncommitted work on the card"):
            step = named(self.steps, name)
            with self.subTest(step=name):
                self.assertIs(step.get("continue-on-error"), True)
                for part in GATE_PARTS:
                    self.assertIn(part, step.get("if", ""))
                self.assertNotIn("${{", step.get("run") or "")

    def test_the_upload_names_the_artifact_and_keeps_it_30_days(self):
        step = named(self.steps, "Upload uncommitted work")
        self.assertTrue(step["uses"].startswith("actions/upload-artifact@"))
        self.assertEqual(step["with"]["name"],
                         "rescue-${{ github.event.client_payload.identifier }}.uncommitted.diff")
        self.assertEqual(step["with"]["path"], "${{ steps.rescue.outputs.uncommitted }}")
        self.assertEqual(int(step["with"]["retention-days"]), 30)

    def test_the_card_comment_says_what_was_saved_and_where(self):
        step = named(self.steps, "Name uncommitted work on the card")
        run = step["run"]
        self.assertIn("linear_ops.py comment", run)
        env = step.get("env") or {}
        self.assertEqual(env.get("CARD"), "${{ github.event.client_payload.identifier }}")
        self.assertIn("actions/runs/${{ github.run_id }}", env.get("RUN_URL", ""))
        rendered = run.replace("$RUN_URL", "https://github.com/o/r/actions/runs/1") \
                      .replace("$CARD", CARD)
        self.assertIn(
            "🧾 uncommitted-work: this build ended with changes in its working tree "
            "that are in no commit and on no branch — saved as artifact "
            f"{ARTIFACT} on run https://github.com/o/r/actions/runs/1 for 30 days.",
            rendered)

    def test_the_other_rescue_calls_do_not_pass_the_flag(self):
        for wf in ("proof-task.yml", "agent-fix.yml"):
            runs = [s.get("run") or "" for s in steps(load(WF_DIR / wf))
                    if "push_rescue.py" in (s.get("run") or "")]
            with self.subTest(workflow=wf):
                self.assertTrue(runs, f"{wf} has no push_rescue.py call")
                for run in runs:
                    self.assertNotIn("--uncommitted", run)


if __name__ == "__main__":
    unittest.main()
