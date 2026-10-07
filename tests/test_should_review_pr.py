"""RED-first tests for the QA-critic review gate (DRE-1888).

Origin: the adversarial critic (qa-review.yml) only ran on agent-dispatched
branches (`agent/DRE-N-*`). Operator-routed cards — the ones the pipeline's
repo-scoped agent tokens can't author, so the operator opens the PR by hand on
a `fix/DRE-N-...` / `feat/DRE-N-...` branch — were SKIPPED. Operator work thus
merged with no real critic verdict, dodging the gate every normal card PR
passes.

The fix opts operator-routed CARD PRs in: a PR whose head branch carries a
linked Linear card (`DRE-<n>`, the same signal linear-sync uses to close the
loop) gets the critic, whatever the branch prefix. A truly chrome-only PR
(no linked card) stays skippable so the gate never blocks non-card work.

These tests must FAIL before should_review_pr.py exists / before qa-review.yml
broadens its guard, and PASS after.
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import should_review_pr  # noqa: E402


class ShouldReviewTest(unittest.TestCase):
    # --- operator-routed card PRs: NOW reviewed (the DRE-1888 fix) -------

    def test_operator_fix_branch_with_card_is_reviewed(self):
        # The exact shape that was skipping before: an operator-authored
        # bureau-pipeline fix on a fix/DRE-N branch.
        self.assertTrue(
            should_review_pr.should_review("fix/DRE-1885-dont-park-building-card")
        )

    def test_operator_feat_branch_with_card_is_reviewed(self):
        self.assertTrue(
            should_review_pr.should_review("feat/DRE-1888-critic-on-operator-prs")
        )

    def test_docs_branch_with_card_is_reviewed(self):
        # A card is a card regardless of the conventional-commit prefix.
        self.assertTrue(should_review_pr.should_review("docs/DRE-1900-adr"))

    def test_bare_card_branch_is_reviewed(self):
        self.assertTrue(should_review_pr.should_review("DRE-1773"))

    # --- lowercase card refs: reviewed + normalized (DRE-2003) ----------
    # The workflow-level contains() guard is case-INsensitive but this
    # authoritative gate was not: a lowercase `ops/dre-N-...` branch started
    # the review job yet returned review=false — a silent review bypass
    # (bit us live 2026-07-09; three security PRs re-pushed as uppercase
    # twins).

    def test_lowercase_ops_branch_with_card_is_reviewed(self):
        self.assertTrue(should_review_pr.should_review("ops/dre-123-x"))

    def test_uppercase_agent_branch_still_reviewed(self):
        self.assertTrue(should_review_pr.should_review("agent/DRE-9-x"))

    def test_card_in_branch_normalizes_lowercase_to_uppercase(self):
        # Linear identifiers are uppercase; `dre-123` must resolve to card
        # DRE-123, so the extractor normalizes before anything uses it.
        self.assertEqual(
            should_review_pr.card_in_branch("ops/dre-123-x"), "DRE-123"
        )

    def test_card_in_branch_normalizes_mixed_case(self):
        self.assertEqual(
            should_review_pr.card_in_branch("fix/Dre-42-y"), "DRE-42"
        )

    # --- normal pipeline PRs: STILL reviewed (no regression) ------------

    def test_agent_branch_is_reviewed(self):
        self.assertTrue(
            should_review_pr.should_review("agent/DRE-1759-engine-drilldown")
        )

    def test_agent_branch_even_without_card_is_reviewed(self):
        # Legacy convention is honored on the prefix alone.
        self.assertTrue(should_review_pr.should_review("agent/some-task"))

    # --- dependabot PRs: reviewed (DRE-2039) -----------------------------
    # The merge gate auto-merges a grouped minor/patch dependabot PR ONLY on
    # a SHA-bound critic APPROVE — so the critic must actually review
    # dependabot/** or the gate waits forever on a verdict that can't exist.

    def test_dependabot_grouped_branch_is_reviewed(self):
        self.assertTrue(
            should_review_pr.should_review("dependabot/pip/pip-minor-patch-1a2b3c4")
        )

    def test_dependabot_actions_branch_is_reviewed(self):
        self.assertTrue(
            should_review_pr.should_review(
                "dependabot/github_actions/actions-minor-patch-9f8e7d6"
            )
        )

    # --- DRE-2250: every PR is reviewed; no branch is "chrome-only" ------
    #
    # These four previously asserted False. That was the DRE-1888 policy:
    # infer triviality from the branch name and skip. The inference was wrong
    # often enough to matter — on 2026-07-29 all five open PRs across the
    # fleet were substantive work sitting on hand-named branches, reviewed by
    # nobody, with nothing going red. The assertions are inverted rather than
    # deleted so the behaviour change stays visible in the diff.

    def test_chore_branch_without_card_is_reviewed(self):
        self.assertTrue(should_review_pr.should_review("chore/bump-deps"))

    def test_docs_branch_without_card_is_reviewed(self):
        self.assertTrue(should_review_pr.should_review("docs/readme-typo"))

    def test_hand_named_branches_are_reviewed(self):
        """The exact shapes that were silently skipping (DRE-2250)."""
        for branch in (
            "fix/opus5-console-write-path",
            "model/fold-ladder-kwarg-upstream",
            "db/work",
        ):
            self.assertTrue(should_review_pr.should_review(branch), branch)

    def test_unknown_branch_fails_open_toward_review(self):
        """When the head ref is missing or unreadable, review. The two failure
        directions are not symmetric: a needless review costs tokens, a missed
        one ships unreviewed code."""
        self.assertTrue(should_review_pr.should_review(""))
        self.assertTrue(should_review_pr.should_review(None))

    # --- card extraction matches the pipeline's DRE-N convention --------

    def test_card_in_branch_picks_first_reference(self):
        self.assertEqual(
            should_review_pr.card_in_branch("fix/DRE-1885-then-DRE-1999"),
            "DRE-1885",
        )

    def test_card_in_branch_none_when_absent(self):
        self.assertIsNone(should_review_pr.card_in_branch("chore/bump-deps"))


class CliTest(unittest.TestCase):
    """CLI: exit 0 == review (run the critic); exit 1 == skip. Stdout carries
    a `review=true|false` line the workflow captures as a step output."""

    def _run(self, branch):
        return subprocess.run(
            [sys.executable,
             os.path.join(os.path.dirname(__file__), "..", "scripts",
                          "should_review_pr.py"),
             branch],
            capture_output=True, text=True,
        )

    def test_cli_exit_0_and_review_true_on_operator_card_branch(self):
        p = self._run("fix/DRE-1885-dont-park")
        self.assertEqual(p.returncode, 0)
        self.assertIn("review=true", p.stdout)

    def test_cli_exit_0_on_agent_branch(self):
        p = self._run("agent/DRE-1-x")
        self.assertEqual(p.returncode, 0)
        self.assertIn("review=true", p.stdout)

    def test_cli_exit_0_and_review_true_on_dependabot_branch(self):
        p = self._run("dependabot/pip/pip-minor-patch-1a2b3c4")
        self.assertEqual(p.returncode, 0)
        self.assertIn("review=true", p.stdout)

    def test_cli_exit_0_on_a_branch_that_used_to_be_called_chrome(self):
        # DRE-2250: was exit 1 / review=false. The CLI's skip path is kept
        # (exit 1, review=false) so the workflow contract is unchanged and a
        # future explicit opt-out has somewhere to land — nothing reaches it
        # today.
        p = self._run("chore/bump-deps")
        self.assertEqual(p.returncode, 0)
        self.assertIn("review=true", p.stdout)

    def test_cli_exit_0_and_review_true_on_lowercase_card_branch(self):
        # DRE-2003: the lowercase shape that silently bypassed the critic.
        p = self._run("ops/dre-1987-merge-gate-verdict-authorship")
        self.assertEqual(p.returncode, 0)
        self.assertIn("review=true", p.stdout)
        # The card it reports must be the real (uppercase) Linear identifier.
        self.assertIn("DRE-1987", p.stdout)



# --- DRE-6192: a person's description edit re-reviews a sent-back head ------
#
# agent-bureau #3323 (2026-10-07): the critic sent a proof record back for a
# missing `What's new:` line, a person fixed the description by hand, and
# nothing reviewed it again — nobody pushed, and no stub listed `edited`. The
# pull request sat 57 minutes until someone re-ran the review by hand. An
# `edited` event now reviews when, and only when, the body changed, the
# newest qa-bot verdict is REQUEST_CHANGES bound to the current head, and the
# editor is not a Bot. Every other `edited` event is a skip.

import json  # noqa: E402
import re  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "should_review_pr.py"
WORKFLOWS = ROOT / ".github" / "workflows"

QA_LOGIN = "agent-bureau-qa-bot[bot]"
HEAD = "d81d7170" * 5  # the head the verdict was earned on, and still is
OLDER = "a1b2c3d4" * 5  # a commit before it
CID = "c" * 64


def _critic(verdict, sha, cid=None, login=QA_LOGIN):
    line = f"🔎 QA Critic — VERDICT: {verdict} @{sha}"
    if cid:
        line += f" content:{cid}"
    return {"user": {"login": login, "type": "Bot"},
            "body": line + "\n\nOne blocking finding: the What's new line."}


SENT_BACK = _critic("REQUEST_CHANGES cause:unmet-criteria", HEAD)


def _compare():
    """A compare record whose content id the carry reads (verdict_content)."""
    return {"status": "ahead", "merge_base_commit": {"sha": "f" * 40},
            "files": [{"filename": "a.py", "sha": "1" * 40,
                       "status": "modified"}]}


class EditedEventTest(unittest.TestCase):
    """The CLI on an `edited` event, through the same flags qa-review.yml's
    Decide review step passes."""

    def _run(self, comments, *, action="edited", body_changed="true",
             sender_is_bot="false", head=HEAD, compare=None, commits=None,
             extra=()):
        with tempfile.TemporaryDirectory() as td:
            cf = Path(td) / "comments.json"
            cf.write_text(json.dumps([comments]))  # --slurp: one page
            args = [sys.executable, str(SCRIPT), "agent/DRE-5836-proof-record",
                    "--comments-file", str(cf), "--qa-login", QA_LOGIN,
                    "--is-draft", "false"]
            if compare is not None:
                p = Path(td) / "compare.json"
                p.write_text(json.dumps(compare))
                args += ["--compare-file", str(p)]
            if commits is not None:
                p = Path(td) / "commits.json"
                p.write_text(json.dumps(commits))
                args += ["--pr-commits-file", str(p)]
            if action is not None:
                args += ["--event-action", action,
                         "--body-changed", body_changed,
                         "--sender-is-bot", sender_is_bot,
                         "--head-sha", head]
            args += list(extra)
            return subprocess.run(args, capture_output=True, text=True,
                                  timeout=60)

    def _review(self, proc):
        lines = proc.stdout.splitlines()
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        if "review=true" in lines:
            self.assertEqual(proc.returncode, 0, proc.stdout)
            return True
        self.assertIn("review=false", lines, proc.stdout + proc.stderr)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        return False

    # -- the one case that reviews ------------------------------------------

    def test_a_persons_body_fix_on_a_sent_back_head_is_reviewed(self):
        """The #3323 shape: REQUEST_CHANGES at d81d7170, head still
        d81d7170, a User edits the body."""
        self.assertTrue(self._review(self._run([SENT_BACK])))

    # -- every other edit is a skip ------------------------------------------

    def test_a_title_only_edit_is_not_reviewed(self):
        self.assertFalse(self._review(
            self._run([SENT_BACK], body_changed="false")))

    def test_a_verdict_on_an_older_sha_is_not_reviewed(self):
        """The head moved since the verdict, so its push already started
        the review that matters."""
        older = _critic("REQUEST_CHANGES cause:unmet-criteria", OLDER)
        self.assertFalse(self._review(self._run([older])))

    def test_no_verdict_is_not_reviewed(self):
        self.assertFalse(self._review(self._run([])))

    def test_a_bot_editor_is_not_reviewed(self):
        """The pipeline's own body writes — the receipt at open, the fix
        agent's edit before its empty commit — never re-review."""
        self.assertFalse(self._review(
            self._run([SENT_BACK], sender_is_bot="true")))

    def test_an_unreadable_editor_is_not_reviewed(self):
        """Only GitHub's literal `false` admits the editor: this path adds a
        review that did not exist before, so a guess skips."""
        for raw in ("", "null"):
            self.assertFalse(self._review(
                self._run([SENT_BACK], sender_is_bot=raw)), raw)

    def test_an_unknown_head_is_not_reviewed(self):
        self.assertFalse(self._review(self._run([SENT_BACK], head="")))

    def test_a_later_approve_at_the_head_wins(self):
        """The NEWEST verdict decides — a REQUEST_CHANGES already answered
        by an APPROVE on the same head starts nothing."""
        approve = _critic("APPROVE", HEAD)
        self.assertFalse(self._review(self._run([SENT_BACK, approve])))

    # -- the verdict is read through merge_gate's readers --------------------

    def test_a_forged_verdict_from_another_login_starts_no_review(self):
        forged = _critic("REQUEST_CHANGES cause:defect", HEAD,
                         login="someone-else")
        self.assertFalse(self._review(self._run([forged])))

    def test_a_quoted_verdict_starts_no_review(self):
        """merge_gate's anchor: a qa-bot comment that only QUOTES a verdict
        is not one."""
        quoted = {"user": {"login": QA_LOGIN, "type": "Bot"},
                  "body": f"> 🔎 QA Critic — VERDICT: REQUEST_CHANGES @{HEAD}"}
        self.assertFalse(self._review(self._run([quoted])))

    def test_the_decision_reads_the_gates_parsers(self):
        import inspect
        src = inspect.getsource(should_review_pr.edit_rereviews)
        for reader in ("merge_gate.latest_verdict_comment",
                       "merge_gate.verdict_token", "merge_gate.verdict_sha"):
            self.assertIn(reader, src)
        self.assertNotIn("re.compile", src)

    # -- a standing APPROVE at the head takes today's path -------------------

    def test_a_standing_approve_whose_content_carries_still_prints_the_carry(self):
        # The carry needs a content id on the verdict equal to the head's.
        from verdict_content import content_id
        cid = content_id(_compare())
        self.assertIsNotNone(cid)
        approve = _critic("APPROVE", HEAD, cid=cid)
        proc = self._run([approve], compare=_compare(),
                         commits=[{"sha": HEAD}])
        self.assertFalse(self._review(proc))
        self.assertIn(f"carried_sha={HEAD}", proc.stdout.splitlines())
        self.assertIn(f"content_id={cid}", proc.stdout.splitlines())

    def test_a_standing_approve_with_no_carry_is_not_reviewed(self):
        proc = self._run([_critic("APPROVE", HEAD)])
        self.assertFalse(self._review(proc))
        self.assertFalse([ln for ln in proc.stdout.splitlines()
                          if ln.startswith("carried_sha=")])

    # -- every other event decides exactly as before -------------------------

    def test_other_actions_decide_as_they_did(self):
        """The new flags change nothing for opened / reopened / synchronize /
        ready_for_review: a REQUEST_CHANGES at the head is reviewed by a push
        event as it always was, and the flags are ignored."""
        baseline = self._run([SENT_BACK], action=None)
        for action in ("opened", "reopened", "synchronize",
                       "ready_for_review"):
            for bot in ("true", "false"):
                proc = self._run([SENT_BACK], action=action,
                                 body_changed="false", sender_is_bot=bot)
                self.assertEqual(
                    (proc.returncode, proc.stdout),
                    (baseline.returncode, baseline.stdout), action)

    def test_a_dispatched_review_still_reviews(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--requested", "--is-draft",
             "false", "--event-action", ""],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("review=true", proc.stdout.splitlines())

    def test_a_draft_edit_is_still_a_draft_skip(self):
        proc = self._run([SENT_BACK], extra=("--is-draft", "true"))
        self.assertFalse(self._review(proc))
        self.assertIn("draft=true", proc.stdout.splitlines())


# --- the workflows ----------------------------------------------------------


def _on(doc):
    # PyYAML reads the bare `on:` key as the boolean True.
    return doc.get("on", doc.get(True))


_EXPR = re.compile(r"\$\{\{(.*?)\}\}")
_IDENT = re.compile(r"'[^']*'|\b[a-z_]+(?:\.[a-z_]+)+\b")


def _render(template, ctx):
    """Render a `${{ }}` template the way Actions does for the operators a
    concurrency group uses: `||` and `&&` return an operand, `==` compares,
    a missing property is null. Dotted names are looked up in `ctx`."""
    def evaluate(expr):
        py = _IDENT.sub(
            lambda m: m.group(0) if m.group(0).startswith("'")
            else f"ctx.get({m.group(0)!r})", expr)
        py = py.replace("||", " or ").replace("&&", " and ")
        value = eval(py, {"ctx": ctx})  # noqa: S307 — a test's own workflow file
        return "" if value is None else str(value)
    return _EXPR.sub(lambda m: evaluate(m.group(1)), template)


class SelfStubOptsInTest(unittest.TestCase):
    """bureau-pipeline's own stub lists `edited`, and an edited run never
    shares a concurrency group with a push's review."""

    def setUp(self):
        self.doc = yaml.safe_load((WORKFLOWS / "pr-review.yml").read_text())

    def test_edited_is_a_trigger(self):
        types = _on(self.doc)["pull_request"]["types"]
        for event in ("opened", "reopened", "synchronize",
                      "ready_for_review", "edited"):
            self.assertIn(event, types)

    def _group(self, **ctx):
        return _render(self.doc["concurrency"]["group"], ctx)

    def test_edited_runs_have_a_group_of_their_own(self):
        edited = self._group(**{"github.event.pull_request.number": 42,
                                "github.event.action": "edited"})
        others = [self._group(**{"github.event.pull_request.number": 42,
                                 "github.event.action": action})
                  for action in ("opened", "reopened", "synchronize",
                                 "ready_for_review")]
        others.append(self._group(**{"inputs.pr_number": "42"}))
        # Every non-edited run keeps today's group, so a push still cancels
        # a stale review of the same pull request.
        self.assertEqual(set(others), {"qa-review-42"})
        self.assertNotIn(edited, others)
        # Edited runs collapse only each other, per pull request.
        self.assertEqual(edited, self._group(**{
            "github.event.pull_request.number": 42,
            "github.event.action": "edited"}))
        self.assertNotEqual(edited, self._group(**{
            "github.event.pull_request.number": 43,
            "github.event.action": "edited"}))
        self.assertTrue(self.doc["concurrency"]["cancel-in-progress"])


def _review_job():
    return yaml.safe_load((WORKFLOWS / "qa-review.yml").read_text())[
        "jobs"]["review"]


def _decide_step():
    return next(s for s in _review_job()["steps"] if s.get("id") == "decide")


class DecideStepWiringTest(unittest.TestCase):
    """qa-review.yml hands the three facts to the script as env, never
    interpolated into the script body."""

    def test_the_facts_come_from_the_event_as_env(self):
        env = _decide_step()["env"]
        joined = " ".join(str(v) for v in env.values())
        self.assertIn("github.event.action", joined)
        self.assertIn("github.event.changes.body", joined)
        self.assertIn("github.event.sender.type", joined)
        self.assertIn("github.event.pull_request.head.sha", joined)

    def test_the_script_body_interpolates_none_of_them(self):
        run = _decide_step()["run"]
        for name in ("github.event.action", "github.event.changes",
                     "github.event.sender", "github.event.pull_request"):
            self.assertNotIn(name, run)
        for flag in ("--event-action", "--body-changed", "--sender-is-bot",
                     "--head-sha"):
            self.assertIn(flag, run)

    def test_the_job_skips_a_title_only_edit_before_any_mint(self):
        cond = " ".join(_review_job()["if"].split())
        self.assertIn("github.event.action != 'edited'", cond)
        self.assertIn("github.event.changes.body", cond)


DECIDE_GH_STUB = r"""#!/bin/sh
case "$*" in
  "pr view"*) cat "$GH_PR" ;;
  *"/compare/"*) printf '{"files": []}\n' ;;
  *"/comments"*) cat "$GH_COMMENTS" ;;
  *"/commits?"*) printf '[]\n' ;;
  *) printf '{}\n' ;;
esac
"""


def _run_decide(comments, *, action, body_changed, sender_is_bot):
    """Execute the Decide review step's shell against a stubbed `gh`."""
    step = _decide_step()
    run = step["run"].replace("${{ github.repository }}",
                              "dreadnought-foundry/agent-bureau")
    assert "${{" not in run, "an expression this test does not expand"
    record = {"headRefName": "agent/DRE-5836-proof-record",
              "headRefOid": HEAD, "baseRefName": "main", "changedFiles": 1,
              "additions": 1, "deletions": 0, "isDraft": False}
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        gh = td / "bin" / "gh"
        gh.write_text(DECIDE_GH_STUB)
        gh.chmod(0o755)
        (td / "pr.json").write_text(json.dumps(record))
        (td / "comments.json").write_text(json.dumps([comments]))
        out = td / "github-output"
        out.write_text("")
        env = {
            "PATH": f"{td / 'bin'}:{os.environ['PATH']}", "HOME": raw,
            "GH_PR": str(td / "pr.json"),
            "GH_COMMENTS": str(td / "comments.json"),
            "RUNNER_TEMP": raw, "GITHUB_OUTPUT": str(out),
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "dreadnought-foundry/agent-bureau",
            "PIPELINE_DIR": str(ROOT), "GH_TOKEN": "t", "PR": "3323",
            "EVENT": "pull_request", "HEAD_REF": "agent/DRE-5836-proof-record",
            "QA_LOGIN": QA_LOGIN, "PR_DRAFT": "false",
            "PR_HEAD_SHA": HEAD, "PR_ACTION": action,
            "BODY_CHANGED": body_changed, "SENDER_IS_BOT": sender_is_bot,
            "PR_RECORD_FIELDS": _review_job()["env"]["PR_RECORD_FIELDS"],
        }
        for name in ("PR_HEAD_SHA", "PR_ACTION", "BODY_CHANGED",
                     "SENDER_IS_BOT"):
            assert name in step["env"], f"Decide review sets no {name}"
        proc = subprocess.run(["bash", "-e", "-c", run], cwd=td, env=env,
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        return dict(line.partition("=")[::2]
                    for line in out.read_text().splitlines() if "=" in line)


class DecideStepExecutedTest(unittest.TestCase):
    """The step, run end to end: the #3323 edit reviews, a title edit and a
    bot's edit do not."""

    def test_a_persons_body_fix_is_reviewed(self):
        out = _run_decide([SENT_BACK], action="edited", body_changed="true",
                          sender_is_bot="false")
        self.assertEqual(out.get("review"), "true")

    def test_a_title_edit_is_not_reviewed(self):
        out = _run_decide([SENT_BACK], action="edited", body_changed="false",
                          sender_is_bot="false")
        self.assertEqual(out.get("review"), "false")

    def test_a_bots_body_edit_is_not_reviewed(self):
        out = _run_decide([SENT_BACK], action="edited", body_changed="true",
                          sender_is_bot="true")
        self.assertEqual(out.get("review"), "false")

    def test_a_push_still_reviews_a_sent_back_head(self):
        out = _run_decide([SENT_BACK], action="synchronize",
                          body_changed="false", sender_is_bot="true")
        self.assertEqual(out.get("review"), "true")


class TheStandardSaysSoTest(unittest.TestCase):
    """standards/whats-new.md: a person's description edit re-runs the
    review where the stub opts in; the fix agent still pushes its empty
    commit."""

    def setUp(self):
        text = (ROOT / "standards" / "whats-new.md").read_text()
        section = text.split("## Answering a sent-back or held pull request",
                             1)[1].split("\n## ", 1)[0]
        self.text = " ".join(section.split())

    def test_a_persons_edit_re_runs_the_review_where_the_stub_opts_in(self):
        self.assertIn("a person's edit to the description", self.text)
        self.assertIn("`edited`", self.text)
        self.assertNotIn("a body edit alone re-runs nothing", self.text)

    def test_the_empty_commit_is_still_the_answer(self):
        self.assertIn("git commit --allow-empty", self.text)
        self.assertIn("DRE-5632", self.text)


if __name__ == "__main__":
    unittest.main()
