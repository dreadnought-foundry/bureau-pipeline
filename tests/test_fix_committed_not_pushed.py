"""RED-first tests: a fix run that committed reads what it left behind before any handoff file (DRE-6351).

THE BUG. `report_fix_result.sh` routed a fix round on the agent's handoff
files first. Two runs finished their fix, committed it on the runner, and had
the push refused:

  * the DRE-4883 run ended on a blocker written AFTER its push answered 401,
    so the Report posted `🛑 Fix attempt N blocked` and parked the card;
  * the DRE-3898 run left no file, so the Report posted `🛑 Fix attempt N
    pushed no new commit` and parked it too.

Both were false — a commit existed — and both were final, because the
reconcile sweep reads a park as "the loop is over".

FIX UNDER TEST. Before any handoff file is read as an exit, the Report reads
the runner's own `HEAD` beside the pull request's live head:

  1. Delivered — the branch holds this run's commit: `fix-attempt-landed`, with
     one line naming the `Push rescue` step when it made the push.
  2. Committed, not pushed — the commit is on the runner and GitHub does not
     have it: the artifact goes to `deliver-rescue`, one tagged marker goes on
     the pull request, and nobody is parked. A second time on the same head
     is the cap: a `fix-attempt-disputed` hold and a park.
  3. Overtaken — the branch moved by another hand: one quiet line.

With no commit on the runner the script routes exactly as before.

The harness follows tests/test_fix_exit_classified.py's, with a real git
repository in the runner directory so `git rev-parse HEAD` has an answer.

Run: python3 -m pytest tests/test_fix_committed_not_pushed.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
SCRIPT = os.path.join(SCRIPTS, "report_fix_result.sh")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fix_budget  # noqa: E402
import fix_dead_run  # noqa: E402
import fix_handoff  # noqa: E402
import pipeline_act  # noqa: E402
from test_act_emission_scenario import CARD, PR, REPO, _checkout, _report_env  # noqa: E402

TAG = "fix-run-committed-not-pushed"
WORKER = "agent-bureau-bot[bot]"
OTHER = "e" * 40
ARTIFACT = f"rescue-{CARD}.patch"
BRANCH = f"agent/{CARD}-the-fix"
RUN_ID = "1234"
RUN_URL = f"https://github.com/{REPO}/actions/runs/{RUN_ID}"
STATUS = "401"
ERROR = "remote: Invalid username or token. fatal: Authentication failed (HTTP 401)"
BLOCKER = ("My push answered 401, so the pipeline's post-run step will deliver "
           "the commit.\nA second line that is not the note.")
NOTE = BLOCKER.splitlines()[0]
CAP_OPENING = "🛑 Fix attempt 2 finished its fix and GitHub refused the push again"


def rest(login: str, body: str) -> dict:
    return {"user": {"login": login,
                     "type": "Bot" if login.endswith("[bot]") else "User"},
            "body": body, "created_at": "2026-10-08T10:00:00Z"}


def marker(head: str, login: str = WORKER) -> dict:
    """A marker comment the way the Report posts it, for the thread."""
    return rest(login, fix_budget.committed_not_pushed_body(
        1, head, "f" * 40, status=STATUS, error=ERROR, artifact=ARTIFACT,
        run_url=RUN_URL, delivery=fix_budget.DELIVERY_DISPATCHED, pr_open=True))


# --------------------------------------------------------------------------
# the harness
# --------------------------------------------------------------------------
GH_STUB = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
log = os.environ["GH_LOG"]
def record(kind, **kw):
    open(log, "a").write(json.dumps(dict(kind=kind, argv=argv, **kw)) + "\n")
if argv[:2] == ["pr", "view"]:
    fields = argv[argv.index("--json") + 1] if "--json" in argv else ""
    if fields == "isDraft":
        print("false")
    elif fields == "state":
        print(os.environ.get("GH_STATE", "OPEN"))
    else:
        print(os.environ["GH_HEAD"])
    sys.exit(0)
if argv[:2] == ["pr", "comment"]:
    body = None
    if "--body-file" in argv:
        body = open(argv[argv.index("--body-file") + 1], encoding="utf-8").read()
    elif "--body" in argv:
        body = argv[argv.index("--body") + 1]
    record("comment", body=body)
    sys.exit(0)
if argv[:2] == ["workflow", "run"]:
    record("dispatch", token=os.environ.get("GH_TOKEN", ""))
    sys.exit(0)
if argv[:1] == ["api"]:
    if any("/check-runs" in a for a in argv):
        print("[]")
        sys.exit(0)
    print(open(os.environ["GH_THREAD"]).read())
    sys.exit(0)
sys.exit(0)
'''

#: linear_ops.py as both a CLI (the shell's card comments) and a module
#: (deliver_rescue.handoff's `cmd_comment`), logging into one file in order.
LINEAR_STUB = '''#!/usr/bin/env python3
import json, sys
LOG = {log!r}
def _log(row):
    open(LOG, "a").write(json.dumps(row) + "\\n")
def cmd_comment(card, body):
    _log(["cmd_comment", card, body])
if __name__ == "__main__":
    _log(sys.argv[1:])
'''

RECEIPT_PATHS = ("/tmp/act-fix-blocked.md", "/tmp/act-fix-pushed.md",
                 "/tmp/act-fix-refuted.md", "/tmp/act-fix-refuted-capped.md",
                 "/tmp/fix-nothing-to-fix.md")


def _git(td: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=td, capture_output=True, text=True, check=True,
        env=dict(os.environ, GIT_AUTHOR_DATE="2026-10-08T10:00:00Z",
                 GIT_COMMITTER_DATE="2026-10-08T10:00:00Z")).stdout.strip()


class Result:
    def __init__(self, proc, writes, calls, trailer, pre, local):
        self.proc, self.writes, self.calls = proc, writes, calls
        self.trailer, self.pre, self.local = trailer, pre, local

    @property
    def comments(self):
        return [w["body"] for w in self.writes if w["kind"] == "comment"]

    @property
    def dispatches(self):
        return [w for w in self.writes if w["kind"] == "dispatch"]

    @property
    def card_notes(self):
        return [c for c in self.calls if c[:1] == ["comment"]]

    @property
    def handoffs(self):
        return [c for c in self.calls if c[:1] == ["cmd_comment"]]

    @property
    def parks(self):
        return [c for c in self.calls
                if (c[:1] == ["add-label"] and "needs-human" in c)
                or c[:2] == ["hold.py", "apply"]
                or (c[:1] in (["advance"], ["state"]) and "Triage" in c)]


def report(*, commit=True, head=None, thread=(), blocker=None, pushed="false",
           patch=True, state="OPEN", mode="fix") -> Result:
    """Run the real report_fix_result.sh from a runner directory that is a git
    repository. `PRE_SHA` is its first commit; `commit` adds one more on top,
    the fix the run made. `head` is the pull request's live head: PRE_SHA
    when None, the runner's HEAD when "local", otherwise the sha given.
    `thread` is the pull request's comments, or a function of PRE_SHA
    returning them."""
    for path in RECEIPT_PATHS:
        if os.path.exists(path):
            os.remove(path)
    with tempfile.TemporaryDirectory() as td:
        _git(td, "init", "-q")
        _git(td, "commit", "-q", "--allow-empty", "-m", "the head the run started from")
        pre = _git(td, "rev-parse", "HEAD")
        if commit:
            _git(td, "commit", "-q", "--allow-empty", "-m", "the fix")
        local = _git(td, "rev-parse", "HEAD")
        live = pre if head is None else local if head == "local" else head

        base = _checkout(td)
        linear_log = os.path.join(td, "linear.jsonl")
        with open(os.path.join(base, "scripts", "linear_ops.py"), "w") as fh:
            fh.write(LINEAR_STUB.format(log=linear_log))
        with open(os.path.join(base, "scripts", "hold.py"), "w") as fh:
            fh.write("#!/usr/bin/env python3\nimport json, sys\n"
                     f"open({linear_log!r}, 'a').write(json.dumps(['hold.py'] + sys.argv[1:]) + '\\n')\n")
        with open(os.path.join(base, "critic-verdict.md"), "w", encoding="utf-8") as fh:
            fh.write(f"🔎 QA Critic — VERDICT: REQUEST_CHANGES cause:defect @{pre}\n")
        binary = os.path.join(td, "bin")
        os.makedirs(binary)
        with open(os.path.join(binary, "gh"), "w") as fh:
            fh.write(GH_STUB)
        os.chmod(os.path.join(binary, "gh"), 0o755)
        thread_file = os.path.join(td, "thread.json")
        with open(thread_file, "w", encoding="utf-8") as fh:
            json.dump([list(thread(pre) if callable(thread) else thread)], fh)
        paths = fix_handoff.open_handoff(td, REPO, PR, pre,
                                         legacy_dir=os.path.join(td, "legacy"))
        if blocker is not None:
            with open(paths["blocker"], "w", encoding="utf-8") as fh:
                fh.write(blocker + "\n")
        gh_log = os.path.join(td, "gh.jsonl")
        env = dict(os.environ, PATH=binary + os.pathsep + os.environ["PATH"],
                   CLASSIFICATION="", DISPATCH_TOKEN="the-workflow-token",
                   GH_LOG=gh_log, GH_HEAD=live, GH_THREAD=thread_file,
                   GH_STATE=state, **_report_env(td, mode))
        env.update(
            PRE_SHA=pre, RUN_ID=RUN_ID, RUN_URL=RUN_URL,
            RESCUE_LOCAL_WORK="true" if commit else "false",
            RESCUE_PUSHED=pushed,
            RESCUE_PATCH=os.path.join(td, ARTIFACT) if patch else "",
            RESCUE_ARTIFACT=ARTIFACT,
            RESCUE_PUSH_STATUS=STATUS if pushed != "true" else "",
            RESCUE_ERROR=ERROR if pushed != "true" else "",
            RESCUE_TARGET_BRANCH=BRANCH,
        )
        proc = subprocess.run(["bash", SCRIPT], cwd=td, env=env,
                              capture_output=True, text=True, timeout=120)

        def lines(path):
            if not os.path.exists(path):
                return []
            with open(path, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh.read().splitlines()]

        trailer = fix_handoff.attribution(td, REPO, PR, pre, CARD)
        return Result(proc, lines(gh_log), lines(linear_log), trailer, pre, local)


def first_line(body: str) -> str:
    return body.splitlines()[0] if body else ""


def markers_in(result: Result) -> list:
    return [b for b in result.comments if TAG in first_line(b)]


# --------------------------------------------------------------------------
# 1: the contract constants
# --------------------------------------------------------------------------
class TheContractTest(unittest.TestCase):

    def test_the_three_constants(self):
        self.assertEqual(TAG, fix_dead_run.COMMITTED_NOT_PUSHED_TAG)
        self.assertEqual(30, fix_dead_run.COMMITTED_NOT_PUSHED_WAIT_MINUTES)
        self.assertEqual(1, fix_dead_run.COMMITTED_NOT_PUSHED_RESTARTS)

    def test_the_tag_is_not_a_retry_marker(self):
        """retry_dead_fix_runs would re-dispatch a fresh agent on the next sweep
        while the delivery is still replaying the patch."""
        self.assertNotIn(TAG, fix_dead_run.RETRY_MARKERS)
        for retry in fix_dead_run.RETRY_MARKERS:
            self.assertNotIn(retry, TAG)
            self.assertNotIn(TAG, retry)

    def test_the_marker_counts_only_the_worker_and_only_this_head(self):
        pre = "a" * 40
        thread = [marker(pre), marker(OTHER), marker(pre, login="mallory")]
        self.assertEqual(1, fix_dead_run.committed_not_pushed_markers(thread, pre))
        self.assertEqual(1, fix_dead_run.committed_not_pushed_markers(thread, OTHER))
        self.assertEqual(0, fix_dead_run.committed_not_pushed_markers(thread, "c" * 40))

    def test_the_marker_is_read_off_the_first_line_only(self):
        pre = "a" * 40
        quoted = rest(WORKER, f"Some other comment\n\n> {TAG}: head still at {pre[:8]}")
        self.assertEqual(0, fix_dead_run.committed_not_pushed_markers([quoted], pre))

    def test_the_marker_spends_no_fix_attempt(self):
        """fix_budget.decide counts attempts off `🔧 Fix attempt` markers."""
        thread = [marker("a" * 40)]
        marker_text, _ = fix_budget.BUDGETS["fix"]
        self.assertEqual(0, fix_budget.count_markers(thread, WORKER, marker_text))

    def test_the_marker_is_not_a_push_and_not_a_blocker(self):
        body = marker("a" * 40)["body"]
        self.assertNotIn(fix_dead_run.PUSH_MARKER, body)
        self.assertFalse(body.startswith("🛑"))
        self.assertTrue(body.startswith(TAG))


# --------------------------------------------------------------------------
# 2: the wording
# --------------------------------------------------------------------------
class TheWordingTest(unittest.TestCase):

    def body(self, **kw):
        args = dict(status=STATUS, error=ERROR, artifact=ARTIFACT, run_url=RUN_URL,
                    delivery=fix_budget.DELIVERY_DISPATCHED, pr_open=True)
        args.update(kw)
        return fix_budget.committed_not_pushed_body(2, "a" * 40, "c" * 40, **args)

    def test_the_first_line_carries_the_tag_and_the_head(self):
        line = first_line(self.body())
        self.assertTrue(line.startswith(TAG), line)
        self.assertIn("head still at aaaaaaaa", line)

    def test_it_names_what_happened(self):
        body = self.body()
        for fact in ("2", "cccccccc", STATUS, ERROR, ARTIFACT, RUN_URL):
            self.assertIn(fact, body)

    def test_the_mechanisms_are_the_four_that_exist(self):
        body = self.body()
        for named in ("`Push rescue`", ARTIFACT, "`deliver-rescue`", "reconcile sweep"):
            self.assertIn(named, body)
        for claimed in ("re-mint", "delivers a committed branch", "medic",
                        "token", "Report", "hook", "push_rescue", "agent-fix"):
            self.assertNotIn(claimed, body)

    def test_the_sweep_sentence_needs_an_open_pull_request(self):
        self.assertIn("30 minutes", self.body(pr_open=True))
        closed = self.body(pr_open=False)
        self.assertNotIn("reconcile sweep", closed)
        self.assertNotIn("30 minutes", closed)

    def test_the_delivery_is_said_as_it_went(self):
        self.assertIn("was dispatched", self.body())
        failed = self.body(delivery=fix_budget.DELIVERY_FAILED,
                           delivery_reason="HTTP 403: Resource not accessible")
        self.assertIn("could not be dispatched", failed)
        self.assertIn("HTTP 403: Resource not accessible", failed)
        skipped = self.body(delivery=fix_budget.DELIVERY_SKIPPED, artifact="")
        self.assertIn("nothing to hand over", skipped)
        self.assertNotIn("was dispatched", skipped)

    def test_the_agents_note_is_a_note_never_an_escalation(self):
        body = self.body(note=NOTE)
        self.assertIn(f"> {NOTE}", body)
        self.assertNotIn("blocked", body)
        self.assertNotIn("Escalating", body)

    def test_a_credential_in_githubs_words_is_redacted(self):
        token = "ghs_" + "A" * 36
        body = self.body(error=f"fatal: auth failed for {token}")
        self.assertNotIn(token, body)

    def test_the_cli_writes_the_function_body(self):
        with tempfile.TemporaryDirectory() as td:
            def put(name, text):
                path = os.path.join(td, name)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                return path
            out = os.path.join(td, "out.md")
            rc = fix_budget.main([
                "committed-not-pushed", "--attempt", "2", "--head", "a" * 40,
                "--commit", "c" * 40, "--status", STATUS,
                "--error-file", put("error", ERROR), "--artifact", ARTIFACT,
                "--run-url", RUN_URL, "--delivery", "dispatched",
                "--delivery-reason-file", put("reason", ""),
                "--note-file", put("note", BLOCKER), "--pr-state", "OPEN",
                "--out", out])
            self.assertEqual(0, rc)
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(self.body(note=NOTE), fh.read())

    def test_the_hold_opens_with_the_contract_line(self):
        hold = fix_budget.committed_not_pushed_hold(
            2, "a" * 40, "c" * 40, status=STATUS, error=ERROR,
            artifact=ARTIFACT, run_url=RUN_URL)
        self.assertTrue(hold.startswith(CAP_OPENING), hold)
        for fact in (STATUS, ERROR, ARTIFACT, RUN_URL):
            self.assertIn(fact, hold)
        self.assertNotIn(TAG, first_line(hold))


# --------------------------------------------------------------------------
# 3: the Report, executed
# --------------------------------------------------------------------------
class CommittedNotPushedTest(unittest.TestCase):

    def assert_restarted(self, r: Result):
        self.assertEqual(0, r.proc.returncode, r.proc.stderr + r.proc.stdout)
        self.assertEqual(1, len(r.comments), r.comments)
        line = first_line(r.comments[0])
        self.assertIn(TAG, line)
        self.assertIn(f"head still at {r.pre[:8]}", line)
        self.assertIn(r.local[:8], r.comments[0])
        self.assertTrue(r.comments[0].endswith(r.trailer + "\n"))
        self.assertEqual(1, len(r.dispatches), r.dispatches)
        self.assertEqual(["workflow", "run", "self-deliver-rescue.yml"],
                         r.dispatches[0]["argv"][:3])
        # The App token carries no Actions permission (DRE-1254).
        self.assertEqual("the-workflow-token", r.dispatches[0]["token"])
        self.assertEqual([], r.parks)
        # The card-side marker is deliver_rescue's own `rescue-push-failed`.
        self.assertEqual(1, len(r.handoffs), r.calls)
        self.assertIn(f"rescue-push-failed: status {STATUS}", r.handoffs[0][2])
        self.assertIn(f"for branch {BRANCH}", r.handoffs[0][2])
        self.assertEqual(1, len(r.card_notes), r.calls)
        note = r.card_notes[0][2]
        for word in ("finished", "preserved", "delivering"):
            self.assertIn(word, note)

    def test_no_file_posts_the_marker_and_parks_nobody(self):
        """The DRE-3898 shape: no blocker, an unmoved head, a commit on the runner."""
        r = report()
        self.assert_restarted(r)
        self.assertNotIn("pushed no new commit", r.comments[0])
        self.assertIn("was dispatched", r.comments[0])

    def test_a_blocker_is_quoted_as_the_agents_note(self):
        """The DRE-4883 shape: a blocker written after the push answered 401."""
        r = report(blocker=BLOCKER)
        self.assert_restarted(r)
        self.assertIn(f"> {NOTE}", r.comments[0])
        self.assertNotIn("A second line that is not the note.", r.comments[0])
        self.assertNotIn("🛑 Fix attempt 2 blocked", "".join(r.comments))

    def test_no_patch_dispatches_nothing_and_says_so(self):
        r = report(patch=False)
        self.assertEqual(0, r.proc.returncode, r.proc.stderr + r.proc.stdout)
        self.assertEqual([], r.dispatches)
        self.assertEqual([], r.handoffs)
        self.assertEqual(1, len(markers_in(r)), r.comments)
        self.assertIn("nothing to hand over", r.comments[0])
        self.assertEqual([], r.parks)

    def test_a_closed_pull_request_gets_no_sweep_sentence(self):
        r = report(state="CLOSED")
        self.assertEqual(1, len(markers_in(r)), r.comments)
        self.assertNotIn("reconcile sweep", r.comments[0])


class TheCapTest(unittest.TestCase):

    def test_a_second_refusal_on_the_same_head_holds(self):
        r = report(thread=lambda pre: [marker(pre)])
        self.assertEqual(0, r.proc.returncode, r.proc.stderr + r.proc.stdout)
        self.assertEqual([], markers_in(r))
        self.assertEqual([], r.dispatches)
        self.assertEqual([], r.handoffs)
        self.assertEqual(1, len(r.comments), r.comments)
        hold = r.comments[0]
        self.assertTrue(hold.startswith(CAP_OPENING), hold)
        self.assertIn(pipeline_act.trailer("fix-attempt-disputed"), hold)
        for fact in (STATUS, ERROR, ARTIFACT, RUN_URL, "Operator decision"):
            self.assertIn(fact, hold)
        self.assertEqual(1, len(r.card_notes), r.calls)
        self.assertIn("refused both pushes", r.card_notes[0][2])
        self.assertIn(ARTIFACT, r.card_notes[0][2])
        self.assertTrue([c for c in r.calls if c[:2] == ["hold.py", "apply"]], r.calls)
        self.assertIn(["advance", CARD, "Triage", "In Review,In Progress,Todo"], r.calls)

    def test_a_marker_for_another_head_does_not_count(self):
        r = report(thread=[marker(OTHER)])
        self.assertEqual(1, len(markers_in(r)), r.comments)
        self.assertEqual(1, len(r.dispatches))
        self.assertEqual([], r.parks)

    def test_a_planted_marker_does_not_count(self):
        """Worker-bot authored only (DRE-1995)."""
        r = report(thread=lambda pre: [marker(pre, login="mallory")])
        self.assertEqual(1, len(markers_in(r)), r.comments)
        self.assertEqual([], r.parks)


class NoCommitIsUnchangedTest(unittest.TestCase):
    """HEAD at PRE_SHA in a real repository routes exactly as before."""

    def test_a_blocker_still_blocks(self):
        r = report(commit=False, blocker=BLOCKER)
        self.assertEqual(0, r.proc.returncode, r.proc.stderr + r.proc.stdout)
        self.assertEqual(1, len(r.comments), r.comments)
        self.assertTrue(r.comments[0].startswith("🛑 Fix attempt 2 blocked: "))
        self.assertEqual([], r.dispatches)
        self.assertTrue(r.parks)

    def test_no_file_still_escalates_no_push(self):
        r = report(commit=False)
        self.assertEqual(0, r.proc.returncode, r.proc.stderr + r.proc.stdout)
        self.assertEqual(1, len(r.comments), r.comments)
        self.assertTrue(r.comments[0].startswith("🛑 Fix attempt 2 pushed no new commit"))
        self.assertEqual([], markers_in(r))
        self.assertEqual([], r.dispatches)
        self.assertTrue(r.parks)


class DeliveredTest(unittest.TestCase):

    def test_a_rescued_push_lands_and_quotes_the_note(self):
        r = report(head="local", pushed="true", blocker=BLOCKER)
        self.assertEqual(0, r.proc.returncode, r.proc.stderr + r.proc.stdout)
        self.assertEqual(1, len(r.comments), r.comments)
        body = r.comments[0]
        self.assertTrue(body.startswith(
            "🔧 Fix attempt 2 pushed — CI and critic review re-running."), body)
        self.assertIn(pipeline_act.trailer("fix-attempt-landed"), body)
        rescue = [line for line in body.splitlines() if "`Push rescue`" in line]
        self.assertEqual(1, len(rescue), body)
        self.assertIn(NOTE, rescue[0])
        self.assertNotIn("blocked", body)
        self.assertEqual([], r.parks)
        self.assertEqual([], r.dispatches)

    def test_the_agents_own_push_lands_as_today(self):
        r = report(head="local")
        self.assertEqual(0, r.proc.returncode, r.proc.stderr + r.proc.stdout)
        self.assertEqual(
            ["🔧 Fix attempt 2 pushed — CI and critic review re-running."
             f"\n\n{pipeline_act.trailer('fix-attempt-landed')}\n\n{r.trailer}\n"],
            r.comments)
        self.assertEqual([], r.parks)


class OvertakenTest(unittest.TestCase):

    def test_another_hands_push_gets_one_quiet_line(self):
        r = report(head=OTHER, blocker=BLOCKER)
        self.assertEqual(0, r.proc.returncode, r.proc.stderr + r.proc.stdout)
        self.assertEqual(1, len(r.comments), r.comments)
        line = r.comments[0]
        self.assertIn(r.local[:8], line)
        self.assertIn(OTHER[:8], line)
        self.assertIn(ARTIFACT, line)
        self.assertNotIn(fix_dead_run.PUSH_MARKER, line)
        self.assertFalse(line.startswith("🛑"))
        self.assertEqual([], r.dispatches)
        self.assertEqual([], r.handoffs)
        self.assertEqual([], r.parks)
        self.assertEqual([], r.card_notes)


if __name__ == "__main__":
    unittest.main()
