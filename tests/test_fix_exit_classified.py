"""RED-first tests: a fix run's exit is classified before any escalation is written (DRE-6018).

THE BUG (twice on 2026-10-06 PT). A fix run that had nothing left to fix still
posted "🙋 The fix agent disagrees with the reviewer's blocking finding … This
needs your call", added `needs-human` and moved the card to Triage.

  * DRE-5654 (portico #931): the critic approved at 10:46 and 10:52. A fix run
    dispatched at 10:51 for an older round wrote "Nothing to fix … No human
    decision is needed: let the review of head 75ef866d finish."
  * DRE-5969 (agent-bureau #3274): the critic approved at 15:13 and the
    Verifier passed at 15:14 — newer than the round the fix run was answering.

Both times the operator moved the card back by hand, and on #3274 the medic
then refused to retry the failed CI because of the label.

FIX UNDER TEST: `scripts/fix_exit.py` reads the pull request thread as it
stands when the run ends, and `report_fix_result.sh` asks it before routing a
fix round that pushed nothing.

  1. A critic APPROVE at the current head, newer than the verdict the run
     fetched, is not a disagreement: one quiet line, no `needs-human`, no lane.
  2. The fixer's own "Nothing to fix" with no open blocking finding at the
     current head is the same.
  3. A real disagreement — an open REQUEST_CHANGES at the current head, with
     the fixer disputing it — still escalates exactly as today.

Run: python3 -m pytest tests/test_fix_exit_classified.py -v
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

import fix_context  # noqa: E402
import fix_convergence  # noqa: E402
import fix_exit  # noqa: E402
import pipeline_act  # noqa: E402
from test_act_emission_scenario import (  # noqa: E402
    CARD,
    PR,
    PRE_SHA,
    REPO,
    _checkout,
    _report_env,
    answers,
)
import fix_handoff  # noqa: E402

QA = "agent-bureau-qa-bot[bot]"
WORKER = "agent-bureau-bot[bot]"
OLDER = "c" * 40
NEWER = "d" * 40

DISPUTE = "the reviewer's finding is wrong and I will not force it"
NOTHING = ("Nothing to fix — the finding this run was sent for is on an older "
           "commit. No human decision is needed: let the review of head "
           f"{PRE_SHA[:8]} finish.")


def critic(token: str, sha: str, extra: str = "") -> str:
    cause = " cause:defect" if token == "REQUEST_CHANGES" else ""
    return f"🔎 QA Critic — VERDICT: {token}{cause} @{sha}{extra}\n\nfindings…"


def verifier(token: str, sha: str) -> str:
    return f"🧪 QA Verifier — VERDICT: {token} @{sha}\n\nscenario…"


def rest(login: str, body: str) -> dict:
    return {"user": {"login": login,
                     "type": "Bot" if login.endswith("[bot]") else "User"},
            "body": body, "created_at": "2026-10-06T22:13:00Z"}


# --------------------------------------------------------------------------
# 1: the classifier, pure
# --------------------------------------------------------------------------
class TheClassifierTest(unittest.TestCase):

    def kind(self, thread, head=PRE_SHA, fetched="", text=""):
        return fix_exit.classify(thread, head, fetched, text).kind

    def test_a_newer_approve_at_the_head_is_quiet(self):
        """DRE-5969: dispatched for the REQUEST_CHANGES, the APPROVE came after."""
        rc = critic("REQUEST_CHANGES", PRE_SHA)
        thread = [rest(QA, rc), rest(QA, critic("APPROVE", PRE_SHA)),
                  rest(QA, verifier("PASS", PRE_SHA))]
        self.assertEqual(fix_exit.APPROVED,
                         self.kind(thread, fetched=rc, text=DISPUTE))

    def test_the_approve_the_run_was_sent_with_is_not_newer(self):
        """Approved-but-red: the sweep dispatches a fix on an APPROVE, and a
        blocker there is the same escalation it always was."""
        approve = critic("APPROVE", PRE_SHA)
        self.assertEqual(fix_exit.ESCALATE,
                         self.kind([rest(QA, approve)], fetched=approve, text=DISPUTE))

    def test_an_approve_on_an_older_commit_is_not_the_heads(self):
        thread = [rest(QA, critic("REQUEST_CHANGES", OLDER)),
                  rest(QA, critic("APPROVE", OLDER))]
        self.assertEqual(fix_exit.ESCALATE, self.kind(thread, text=DISPUTE))

    def test_nothing_to_fix_with_no_open_finding_at_the_head_is_quiet(self):
        """DRE-5654: the fixer says so, and the thread agrees."""
        thread = [rest(QA, critic("REQUEST_CHANGES", OLDER)),
                  rest(QA, critic("APPROVE", OLDER))]
        self.assertEqual(fix_exit.NOTHING,
                         self.kind(thread, fetched=critic("APPROVE", OLDER), text=NOTHING))

    def test_nothing_to_fix_against_an_open_finding_at_the_head_escalates(self):
        """The fixer's word alone cannot close a finding the critic holds open."""
        rc = critic("REQUEST_CHANGES", PRE_SHA)
        self.assertEqual(fix_exit.ESCALATE,
                         self.kind([rest(QA, rc)], fetched=rc, text=NOTHING))

    def test_a_verifier_fail_at_the_head_is_an_open_finding(self):
        rc = critic("REQUEST_CHANGES", OLDER)
        thread = [rest(QA, rc), rest(QA, critic("APPROVE", PRE_SHA)),
                  rest(QA, verifier("FAIL", PRE_SHA))]
        self.assertEqual(fix_exit.ESCALATE, self.kind(thread, fetched=rc, text=DISPUTE))
        self.assertEqual(fix_exit.ESCALATE, self.kind(thread, fetched=rc, text=NOTHING))

    def test_a_real_disagreement_escalates(self):
        rc = critic("REQUEST_CHANGES", PRE_SHA)
        self.assertEqual(fix_exit.ESCALATE, self.kind([rest(QA, rc)], fetched=rc, text=DISPUTE))

    def test_an_unreadable_thread_escalates(self):
        """Nothing is silenced on a read that failed."""
        self.assertEqual(fix_exit.ESCALATE, self.kind(None, text=NOTHING))

    def test_an_unknown_head_escalates(self):
        thread = [rest(QA, critic("APPROVE", PRE_SHA))]
        self.assertEqual(fix_exit.ESCALATE, self.kind(thread, head="", text=NOTHING))

    def test_only_the_critics_identity_counts(self):
        """A planted APPROVE (DRE-1995) silences nothing."""
        rc = critic("REQUEST_CHANGES", PRE_SHA)
        thread = [rest(QA, rc), rest("someone", critic("APPROVE", PRE_SHA)),
                  rest(WORKER, critic("APPROVE", PRE_SHA))]
        self.assertEqual(fix_exit.ESCALATE, self.kind(thread, fetched=rc, text=DISPUTE))

    def test_a_quoted_verdict_is_not_one(self):
        rc = critic("REQUEST_CHANGES", PRE_SHA)
        thread = [rest(QA, rc), rest(QA, "> " + critic("APPROVE", PRE_SHA))]
        self.assertEqual(fix_exit.ESCALATE, self.kind(thread, fetched=rc, text=DISPUTE))

    def test_a_neutral_critic_status_does_not_hide_the_verdict_before_it(self):
        rc = critic("REQUEST_CHANGES", PRE_SHA)
        thread = [rest(QA, rc), rest(QA, critic("APPROVE", PRE_SHA)),
                  rest(QA, "🔎 QA Critic — review could not run (rate limit)")]
        self.assertEqual(fix_exit.APPROVED, self.kind(thread, fetched=rc, text=DISPUTE))

    def test_nothing_to_fix_is_read_from_the_opening_words_only(self):
        thread = [rest(QA, critic("APPROVE", OLDER))]
        for text in ("Nothing to fix.", "**Nothing to fix** — approved",
                     "nothing to fix: the review is running",
                     "Nothing for the fixer to fix — the only red check is a time limit"):
            self.assertEqual(fix_exit.NOTHING, self.kind(thread, text=text), text)
        for text in ("", DISPUTE, "I disagree; there is nothing to fix here",
                     "Nothing to fixate on, but the finding is wrong"):
            self.assertEqual(fix_exit.ESCALATE, self.kind(thread, text=text), text)

    def test_the_quiet_line_is_one_line_and_never_a_blocker(self):
        for kind in (fix_exit.APPROVED, fix_exit.NOTHING):
            line = fix_exit.quiet_line(kind, "2", PRE_SHA)
            self.assertEqual(1, len(line.splitlines()), line)
            self.assertFalse(line.startswith(fix_context.BLOCKER_PREFIX), line)
            self.assertNotIn("Operator decision", line)

    def test_the_quiet_line_counts_toward_the_convergence_halt(self):
        """Two quiet stand-downs on one commit halt the loop, so an
        approved-but-red sweep cannot dispatch quiet runs forever."""
        line = fix_exit.quiet_line(fix_exit.NOTHING, "2", PRE_SHA)
        self.assertTrue(fix_convergence.is_no_progress(
            rest(WORKER, line), PRE_SHA[:8], WORKER))


class TheCliTest(unittest.TestCase):

    def run_cli(self, thread_text, text, fetched=""):
        with tempfile.TemporaryDirectory() as td:
            paths = {}
            for name, content in (("thread", thread_text), ("text", text),
                                  ("verdict", fetched)):
                paths[name] = os.path.join(td, name)
                with open(paths[name], "w", encoding="utf-8") as fh:
                    fh.write(content)
            out = os.path.join(td, "quiet.md")
            proc = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS, "fix_exit.py"), "classify",
                 "--comments-json", paths["thread"], "--head", PRE_SHA,
                 "--verdict-file", paths["verdict"], "--text-file", paths["text"],
                 "--attempt", "3", "--out", out],
                capture_output=True, text=True, timeout=60)
            quiet = open(out, encoding="utf-8").read() if os.path.exists(out) else None
            return proc, quiet

    def test_a_quiet_exit_prints_its_kind_and_writes_the_line(self):
        rc = critic("REQUEST_CHANGES", PRE_SHA)
        pages = json.dumps([[rest(QA, rc), rest(QA, critic("APPROVE", PRE_SHA))]])
        proc, quiet = self.run_cli(pages, DISPUTE, fetched=rc)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(fix_exit.APPROVED, proc.stdout.splitlines()[0])
        self.assertEqual(fix_exit.quiet_line(fix_exit.APPROVED, "3", PRE_SHA), quiet)

    def test_an_escalation_writes_nothing(self):
        rc = critic("REQUEST_CHANGES", PRE_SHA)
        proc, quiet = self.run_cli(json.dumps([[rest(QA, rc)]]), DISPUTE, fetched=rc)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(fix_exit.ESCALATE, proc.stdout.splitlines()[0])
        self.assertIsNone(quiet)

    def test_an_unparseable_thread_escalates(self):
        for raw in ("", "not json", '{"message": "Bad credentials"}'):
            proc, quiet = self.run_cli(raw, NOTHING)
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual(fix_exit.ESCALATE, proc.stdout.splitlines()[0], raw)
            self.assertIsNone(quiet)


# --------------------------------------------------------------------------
# 2: the Report step, executed
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
        print("OPEN")
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
    record("dispatch")
    sys.exit(0)
if argv[:1] == ["api"]:
    if os.environ.get("GH_CHECKS") and any("/check-runs" in a for a in argv):
        print(open(os.environ["GH_CHECKS"]).read())
        sys.exit(0)
    if os.environ.get("GH_THREAD_FAILS") == "1":
        sys.stderr.write("HTTP 502\n")
        sys.exit(1)
    print(open(os.environ["GH_THREAD"]).read())
    sys.exit(0)
sys.exit(0)
'''

RECEIPT_PATHS = ("/tmp/act-fix-blocked.md", "/tmp/act-fix-pushed.md",
                 "/tmp/act-fix-refuted.md", "/tmp/act-fix-refuted-capped.md",
                 "/tmp/fix-nothing-to-fix.md")


def report(thread, *, blocker=None, refutation=None, fetched="", head=PRE_SHA,
           mode="fix", thread_fails=False, checks=None):
    """Run the real report_fix_result.sh. Returns (proc, gh writes, linear calls, td-answers).
    `checks`, when given, is the head's check-runs payload `gh api` answers."""
    for path in RECEIPT_PATHS:
        if os.path.exists(path):
            os.remove(path)
    with tempfile.TemporaryDirectory() as td:
        base = _checkout(td)
        linear_log = os.path.join(td, "linear.jsonl")
        with open(os.path.join(base, "scripts", "linear_ops.py"), "w") as fh:
            fh.write("#!/usr/bin/env python3\nimport json, sys\n"
                     f"open({linear_log!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n")
        with open(os.path.join(base, "critic-verdict.md"), "w", encoding="utf-8") as fh:
            fh.write(fetched)
        binary = os.path.join(td, "bin")
        os.makedirs(binary)
        with open(os.path.join(binary, "gh"), "w") as fh:
            fh.write(GH_STUB)
        os.chmod(os.path.join(binary, "gh"), 0o755)
        thread_file = os.path.join(td, "thread.json")
        with open(thread_file, "w", encoding="utf-8") as fh:
            json.dump([thread], fh)
        checks_file = ""
        if checks is not None:
            checks_file = os.path.join(td, "checks.json")
            with open(checks_file, "w", encoding="utf-8") as fh:
                json.dump(checks, fh)
        paths = fix_handoff.open_handoff(td, REPO, PR, PRE_SHA,
                                         legacy_dir=os.path.join(td, "legacy"))
        for kind, text in (("blocker", blocker), ("refutation", refutation)):
            if text is not None:
                with open(paths[kind], "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
        gh_log = os.path.join(td, "gh.jsonl")
        env = dict(os.environ, PATH=binary + os.pathsep + os.environ["PATH"],
                   CLASSIFICATION="", DISPATCH_TOKEN="test",
                   GH_LOG=gh_log, GH_HEAD=head, GH_THREAD=thread_file,
                   GH_THREAD_FAILS="1" if thread_fails else "0",
                   GH_CHECKS=checks_file,
                   **_report_env(td, mode))
        proc = subprocess.run(["bash", SCRIPT], cwd=td, env=env,
                              capture_output=True, text=True, timeout=120)

        def lines(path):
            if not os.path.exists(path):
                return []
            with open(path, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh.read().splitlines()]

        return proc, lines(gh_log), lines(linear_log), answers(td)


def parks(calls):
    return [c for c in calls
            if (c[:1] == ["add-label"] and "needs-human" in c)
            or (c[:1] in (["advance"], ["state"]) and "Triage" in c)]


def card_comments(calls):
    return [c for c in calls if c[:1] == ["comment"]]


def pr_comments(writes):
    return [w["body"] for w in writes if w["kind"] == "comment"]


class AQuietExit:
    """What every quiet exit owes: one line on the pull request, and nothing else."""

    def assert_quiet(self, result, kind):
        proc, writes, calls, trailer = result
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        self.assertEqual([], parks(calls), "no needs-human and no lane move")
        self.assertEqual([], card_comments(calls), "nothing on the card")
        self.assertEqual([c for c in calls if c[:1] in (["advance"], ["state"])], [])
        self.assertEqual([], [w for w in writes if w["kind"] == "dispatch"])
        bodies = pr_comments(writes)
        self.assertEqual(1, len(bodies), bodies)
        self.assertEqual(fix_exit.quiet_line(kind, "2", PRE_SHA) + "\n\n" + trailer + "\n",
                         bodies[0])
        self.assertNotIn("disagrees", bodies[0])


class DRE5969ShapeTest(AQuietExit, unittest.TestCase):
    """An APPROVE newer than the round the fix run was dispatched for."""

    RC = critic("REQUEST_CHANGES", PRE_SHA)
    THREAD = [rest(QA, RC), rest(WORKER, "⏳ fix run started"),
              rest(QA, critic("APPROVE", PRE_SHA)), rest(QA, verifier("PASS", PRE_SHA))]

    def test_a_disputing_blocker_crossing_the_approve_stays_quiet(self):
        self.assert_quiet(report(self.THREAD, blocker=DISPUTE, fetched=self.RC),
                          fix_exit.APPROVED)

    def test_a_refutation_crossing_the_approve_starts_no_re_review(self):
        self.assert_quiet(report(self.THREAD, refutation=DISPUTE, fetched=self.RC),
                          fix_exit.APPROVED)

    def test_a_run_that_pushed_nothing_and_wrote_nothing_stays_quiet(self):
        self.assert_quiet(report(self.THREAD, fetched=self.RC), fix_exit.APPROVED)


class DRE5654ShapeTest(AQuietExit, unittest.TestCase):
    """The fix run's own result says nothing to fix."""

    APPROVE_OLDER = critic("APPROVE", OLDER)
    THREAD = [rest(QA, critic("REQUEST_CHANGES", OLDER)), rest(QA, APPROVE_OLDER)]

    def test_nothing_to_fix_stays_quiet(self):
        self.assert_quiet(report(self.THREAD, blocker=NOTHING, fetched=self.APPROVE_OLDER),
                          fix_exit.NOTHING)


class ARealDisagreementStillEscalatesTest(unittest.TestCase):
    """An open REQUEST_CHANGES at the current head, with the fixer disputing it."""

    RC = critic("REQUEST_CHANGES", PRE_SHA)

    def test_it_escalates_exactly_as_today(self):
        proc, writes, calls, trailer = report([rest(QA, self.RC)], blocker=DISPUTE,
                                              fetched=self.RC)
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        body = (f"🛑 Fix attempt 2 blocked: {DISPUTE}\n\n"
                + subprocess.run([sys.executable, os.path.join(SCRIPTS, "fix_context.py"),
                                  "--answer-format"],
                                 capture_output=True, text=True).stdout.rstrip("\n"))
        # The cause rides after the act's trailer, as the attribution line
        # does, so the body the registry froze is untouched (DRE-5745).
        self.assertEqual(
            [body + f"\n\n{pipeline_act.trailer('fix-attempt-disputed')}"
             f"\n\n{fix_exit.CAUSE_LABEL} {fix_exit.DISPUTE_CAUSE}\n\n{trailer}\n"],
            pr_comments(writes))
        said = card_comments(calls)
        self.assertEqual(1, len(said), said)
        self.assertTrue(said[0][2].startswith(
            "🙋 The fix agent disagrees with the reviewer's blocking finding"))
        self.assertIn(["add-label", CARD, "needs-human"], calls)
        self.assertIn(["advance", CARD, "Triage", "In Review,In Progress,Todo"], calls)

    def test_nothing_to_fix_against_the_open_finding_still_escalates(self):
        proc, writes, calls, _ = report([rest(QA, self.RC)], blocker=NOTHING,
                                        fetched=self.RC)
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        self.assertIn(["add-label", CARD, "needs-human"], calls)
        self.assertTrue(pr_comments(writes)[0].startswith("🛑 Fix attempt 2 blocked: "))

    def test_an_unreadable_thread_escalates(self):
        approve = critic("APPROVE", PRE_SHA)
        proc, writes, calls, _ = report([rest(QA, self.RC), rest(QA, approve)],
                                        blocker=NOTHING, fetched=self.RC,
                                        thread_fails=True)
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        self.assertIn(["add-label", CARD, "needs-human"], calls)

    def test_approved_but_red_still_escalates(self):
        """The run was sent WITH the APPROVE: it is not newer than the round."""
        approve = critic("APPROVE", PRE_SHA)
        proc, writes, calls, _ = report([rest(QA, approve)], blocker=DISPUTE,
                                        fetched=approve)
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        self.assertIn(["add-label", CARD, "needs-human"], calls)

    def test_a_conflict_round_is_not_classified(self):
        """An APPROVE says nothing about a merge conflict still standing."""
        rc = critic("REQUEST_CHANGES", PRE_SHA)
        thread = [rest(QA, rc), rest(QA, critic("APPROVE", PRE_SHA))]
        proc, writes, calls, _ = report(thread, fetched=rc, mode="conflict")
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        self.assertNotIn(fix_exit.QUIET_PREFIX, "".join(pr_comments(writes)))

    def test_a_pushed_round_is_untouched(self):
        rc = critic("REQUEST_CHANGES", PRE_SHA)
        thread = [rest(QA, rc), rest(QA, critic("APPROVE", PRE_SHA))]
        proc, writes, calls, trailer = report(thread, fetched=rc, head=NEWER)
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        self.assertEqual(
            ["🔧 Fix attempt 2 pushed — CI and critic review re-running."
             f"\n\n{pipeline_act.trailer('fix-attempt-landed')}\n\n{trailer}\n"],
            pr_comments(writes))


if __name__ == "__main__":
    unittest.main()
