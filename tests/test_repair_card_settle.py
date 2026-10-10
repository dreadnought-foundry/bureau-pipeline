"""A "Red main repaired" card closes itself when main is green again (DRE-4411).

The Red-Main Repair loop files its card straight into `In Progress`, before the
repair agent starts, and the card has exactly ONE way to close: a repair pull
request merging. When no pull request ever comes the card sits In Progress for
ever, the console reads "working", and a person cancels it by hand. Of the last
seventeen repair cards on the board, measured 2026-09-20 13:19 PT: six Done,
one Duplicate, **nine Canceled by hand**, none closed by the pipeline for any
reason other than a merge.

Three of them, cancelled that morning, are the cases replayed below:

  * DRE-4374 (portico) — the failed CI run PASSED on its second attempt, so
    there was nothing to repair and the agent opened nothing. 6 h "working".
  * DRE-4326 (portico) — CI really failed at that commit, but main moved on and
    has been green on every push since. 26 h.
  * DRE-4346 (bureau-pipeline) — the harness run at that commit now reads
    success; main is green. 11 h.

What these tests pin, one class per acceptance criterion:

  * `repair_card.py settle` exists as a second subcommand and is driven against
    a fake GitHub and a fake Linear;
  * a card whose named workflow's newest COMPLETED run on the default branch
    concluded success, with no open repair pull request, is moved to Canceled
    with ONE comment naming the proving run and saying nothing was delivered;
  * a card with an open repair pull request — on a head ref carrying its card
    id, or on the cardless `repair/<sha>` fallback — is left exactly as it is;
  * a card whose main is still red is left exactly as it is, and the sweep log
    says why (that is `red-main-repair.yml`'s own budget path, untouched here);
  * a GitHub or Linear read that fails leaves every card untouched and prints
    UNKNOWN with the card id — an unreadable answer is never "green";
  * the newest run being queued or in progress is not an answer: `settle` reads
    the newest COMPLETED run;
  * cards are discovered by the title anchor `card_title` itself is built from,
    imported rather than retyped;
  * at most one comment per card ever;
  * the sweep calls it once per pass, beside the existing sweep steps;
  * a red main whose repair branch is pushed, ahead of the default branch and
    under no pull request of any state gets ONE comment saying so (DRE-6524,
    the replay of DRE-6511 on 2026-10-09 below), and an unreadable branch read
    is UNKNOWN, never a yes.

Run: cd bureau-pipeline && python3 -m pytest tests/test_repair_card_settle.py -v
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import red_main_repair  # noqa: E402
import repair_card  # noqa: E402

SLUG = "bureau-pipeline"

# The three real cases, with their real shas and workflows.
FLAKE = {  # DRE-4374 — the failed run passed on its second attempt
    "card": "DRE-4374",
    "sha": "dfbda724e2ce" + "0" * 28,
    "workflow": "CI",
    "repo": "portico",
}
MOVED_ON = {  # DRE-4326 — main moved on and has been green on every push since
    "card": "DRE-4326",
    "sha": "c2cabbd6878b" + "0" * 28,
    "workflow": "CI",
    "repo": "portico",
}
REREAD = {  # DRE-4346 — the harness run at that commit now reads success
    "card": "DRE-4346",
    "sha": "92d6ad25352d" + "0" * 28,
    "workflow": "Integration harness",
    "repo": "bureau-pipeline",
}

RUN_URL = "https://github.com/dreadnought-foundry/portico/actions/runs/35000000001"


def run_record(sha: str, *, status="completed", conclusion="success",
               url=RUN_URL) -> dict:
    """One row of `gh run list --json status,conclusion,headSha,url`."""
    return {"status": status, "conclusion": conclusion, "headSha": sha,
            "url": url}


def repair_card_row(case: dict, *, state="In Progress", comments=()) -> dict:
    """A repair card as the sweep hands it to `settle` — title, body, lane,
    repo and the comments already on it. Built through the SHIPPED writers, so
    a change to either one moves this fixture with it."""
    return {
        "identifier": case["card"],
        "title": repair_card.card_title(case["workflow"], case["sha"]),
        "description": repair_card.card_body(
            workflow_name=case["workflow"], run_url=RUN_URL,
            head_sha=case["sha"], repo_slug=case["repo"], attempt=1,
        ),
        "state": state,
        "repo": case["repo"],
        "comments": list(comments),
    }


class FakeGitHubAndLinear:
    """Both seams `settle` reads, recorded. Every method mirrors the real one.

    `*_error` raises where the real seam would raise; `refs=None` / `runs=None`
    is the other unreadable shape the sweep's own helpers answer with, and both
    must reach the same UNKNOWN.
    """

    def __init__(self, cards=(), refs=(), runs=(), *, board_error=None,
                 refs_error=None, runs_error=None, cancel_error=None,
                 heads=None, pulls=..., compared=None, heads_error=None,
                 pulls_error=None, compare_error=None):
        self._cards = list(cards)
        self._refs = refs
        self._runs = runs
        self.board_error = board_error
        self.refs_error = refs_error
        self.runs_error = runs_error
        self.cancel_error = cancel_error
        self.canceled: list[str] = []
        self.comments: list[tuple] = []
        self.runs_asked: list[str] = []
        # DRE-6524's three reads off the repair branches. The defaults are a
        # remote with no repair branch on it, so a fixture that never names a
        # branch reads exactly as it did before they existed.
        self._heads = {} if heads is None else heads
        self._pulls = {} if pulls is ... else pulls
        self._compared = compared
        self.heads_error = heads_error
        self.pulls_error = pulls_error
        self.compare_error = compare_error
        self.branch_reads: list[tuple] = []

    def repair_cards(self):
        if self.board_error:
            raise self.board_error
        return self._cards

    def open_pull_head_refs(self):
        if self.refs_error:
            raise self.refs_error
        return self._refs

    def workflow_runs(self, workflow_name):
        self.runs_asked.append(workflow_name)
        if self.runs_error:
            raise self.runs_error
        return self._runs

    def cancel(self, identifier):
        if self.cancel_error:
            raise self.cancel_error
        self.canceled.append(identifier)

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))

    def branch_heads(self):
        self.branch_reads.append(("branch_heads",))
        if self.heads_error:
            raise self.heads_error
        return self._heads

    def pulls_for_head(self, branch):
        self.branch_reads.append(("pulls_for_head", branch))
        if self.pulls_error:
            raise self.pulls_error
        if self._pulls is None:
            return None
        return self._pulls.get(branch, [])

    def compare(self, base, head):
        self.branch_reads.append(("compare", base, head))
        if self.compare_error:
            raise self.compare_error
        return self._compared


def settle(ops, *, repo_slug=SLUG, **kw):
    """Drive one pass, capturing the sweep log the criteria talk about."""
    lines: list[str] = []
    report = repair_card.settle(
        repo_slug=repo_slug, ops=ops, log=lines.append, **kw
    )
    return report, "\n".join(lines)


class TitleAnchorTest(unittest.TestCase):
    """Cards are found by the anchor `card_title` itself is built from."""

    def test_the_title_is_built_from_the_anchor_settle_searches_by(self):
        # The criterion: a change to the title cannot silently stop the sweep
        # finding its own cards. Executed against the shipped writer, never
        # retyped here.
        title = repair_card.card_title("Integration harness", REREAD["sha"])
        self.assertTrue(
            title.startswith(repair_card.TITLE_ANCHOR),
            f"{title!r} does not start with {repair_card.TITLE_ANCHOR!r}",
        )
        self.assertTrue(repair_card.is_repair_card(title))

    def test_a_card_the_loop_did_not_file_is_not_one_of_ours(self):
        self.assertFalse(repair_card.is_repair_card(
            "bureau-pipeline: the sweep runs the promotion stall clock"))
        self.assertFalse(repair_card.is_repair_card(""))

    def test_the_workflow_name_reads_back_off_the_title(self):
        for name in ("CI", "Integration harness", "CI — matrix"):
            with self.subTest(name=name):
                title = repair_card.card_title(name, FLAKE["sha"])
                self.assertEqual(repair_card.card_workflow(title), name)

    def test_the_failing_commit_reads_back_off_the_body(self):
        body = repair_card.card_body(
            workflow_name="CI", run_url=RUN_URL, head_sha=MOVED_ON["sha"],
            repo_slug="portico", attempt=1,
        )
        self.assertEqual(repair_card.card_failing_sha(body), MOVED_ON["sha"])


class MainIsGreenAgainTest(unittest.TestCase):
    """The three real cancellations, replayed."""

    def _settle(self, case, runs):
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(case)], refs=[], runs=runs)
        report, log = settle(ops, repo_slug=case["repo"])
        return ops, report, log

    def test_a_second_attempt_pass_cancels_the_card(self):
        # DRE-4374: the failed CI run passed on its second attempt, so the
        # newest completed run AT THAT SHA now reads success.
        ops, report, log = self._settle(
            FLAKE, [run_record(FLAKE["sha"])])
        self.assertEqual(ops.canceled, [FLAKE["card"]])
        self.assertEqual(
            [d.decision for d in report.decisions], [repair_card.SETTLED_GREEN])
        self.assertIn(FLAKE["card"], log)

    def test_a_later_green_commit_cancels_the_card(self):
        # DRE-4326: CI really failed at that commit; main moved on and has been
        # green on every push since, so the newest completed run is at a LATER
        # sha and concluded success.
        later = "aaaaaaaaaaaa" + "0" * 28
        ops, report, log = self._settle(
            MOVED_ON,
            [run_record(later), run_record(MOVED_ON["sha"], conclusion="failure")],
        )
        self.assertEqual(ops.canceled, [MOVED_ON["card"]])

    def test_a_re_read_success_cancels_the_card(self):
        # DRE-4346: the harness run at that commit now reads success.
        ops, report, log = self._settle(
            REREAD, [run_record(REREAD["sha"])])
        self.assertEqual(ops.canceled, [REREAD["card"]])
        self.assertEqual(ops.runs_asked, ["Integration harness"])

    def test_exactly_one_comment_naming_the_proving_run(self):
        ops, _, _ = self._settle(FLAKE, [run_record(FLAKE["sha"])])
        self.assertEqual(len(ops.comments), 1)
        identifier, body = ops.comments[0]
        self.assertEqual(identifier, FLAKE["card"])
        self.assertIn(RUN_URL, body)

    def test_the_comment_says_nothing_was_delivered(self):
        ops, _, _ = self._settle(FLAKE, [run_record(FLAKE["sha"])])
        body = ops.comments[0][1]
        self.assertIn("nothing was delivered", body.lower())
        # Canceled, never Done — and the card says why in plain English.
        self.assertIn("canceled", body.lower())
        self.assertNotIn("Done", body)

    def test_canceled_never_done(self):
        ops, _, _ = self._settle(FLAKE, [run_record(FLAKE["sha"])])
        # The ONE state write, and it is the cancel.
        self.assertEqual(ops.canceled, [FLAKE["card"]])

    def test_another_repos_card_is_not_this_sweeps_to_settle(self):
        # Every repo runs its own sweep against its own GitHub; a portico card
        # must never be settled off bureau-pipeline's runs.
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(FLAKE)], refs=[],
            runs=[run_record(FLAKE["sha"])])
        settle(ops, repo_slug="bureau-pipeline")
        self.assertEqual(ops.canceled, [])
        self.assertEqual(ops.comments, [])


class AnOpenRepairPullRequestIsLeftAloneTest(unittest.TestCase):
    """Rule 1: the merge closes the card — settle must not race it."""

    def _settle(self, case, ref):
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(case)], refs=[ref],
            runs=[run_record(case["sha"])],  # green, and STILL left alone
        )
        report, log = settle(ops, repo_slug=case["repo"])
        return ops, report, log

    def test_a_head_ref_carrying_the_card_id_leaves_the_card_alone(self):
        ref = red_main_repair.repair_branch(
            FLAKE["sha"], 1, card=FLAKE["card"])
        ops, report, log = self._settle(FLAKE, ref)
        self.assertEqual(ops.canceled, [])
        self.assertEqual(ops.comments, [])
        self.assertEqual(
            [d.decision for d in report.decisions],
            [repair_card.SETTLED_PR_OPEN])

    def test_the_cardless_fallback_ref_leaves_the_card_alone(self):
        ref = red_main_repair.repair_branch(FLAKE["sha"], 1)
        self.assertEqual(ref, f"repair/{FLAKE['sha']}")
        ops, _, _ = self._settle(FLAKE, ref)
        self.assertEqual(ops.canceled, [])
        self.assertEqual(ops.comments, [])

    def test_a_second_attempts_ref_counts_too(self):
        ref = red_main_repair.repair_branch(
            FLAKE["sha"], 2, card=FLAKE["card"])
        ops, _, _ = self._settle(FLAKE, ref)
        self.assertEqual(ops.canceled, [])

    def test_a_repair_ref_for_another_commit_does_not_hold_this_card(self):
        other = red_main_repair.repair_branch("f" * 40, 1, card="DRE-9999")
        ops, report, _ = self._settle(FLAKE, other)
        self.assertEqual(ops.canceled, [FLAKE["card"]])


class MainIsStillRedTest(unittest.TestCase):
    """Rule 3: that is red-main-repair.yml's budget path, and it stays there."""

    def test_a_red_main_with_no_pull_request_is_left_exactly_as_it_is(self):
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(MOVED_ON)], refs=[],
            runs=[run_record(MOVED_ON["sha"], conclusion="failure")])
        report, log = settle(ops, repo_slug=MOVED_ON["repo"])
        self.assertEqual(ops.canceled, [])
        self.assertEqual(ops.comments, [])
        self.assertEqual(
            [d.decision for d in report.decisions],
            [repair_card.SETTLED_RED])

    def test_the_sweep_log_says_why(self):
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(MOVED_ON)], refs=[],
            runs=[run_record(MOVED_ON["sha"], conclusion="failure")])
        _, log = settle(ops, repo_slug=MOVED_ON["repo"])
        self.assertIn(MOVED_ON["card"], log)
        self.assertIn("still red", log.lower())

    def test_a_timed_out_run_is_not_a_success(self):
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(MOVED_ON)], refs=[],
            runs=[run_record(MOVED_ON["sha"], conclusion="timed_out")])
        settle(ops, repo_slug=MOVED_ON["repo"])
        self.assertEqual(ops.canceled, [])


class TheNewestCompletedRunIsTheAnswerTest(unittest.TestCase):
    """Rule 6: queued and in-progress are not answers."""

    def test_a_queued_run_over_a_red_one_does_not_cancel(self):
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(MOVED_ON)], refs=[],
            runs=[
                run_record("b" * 40, status="queued", conclusion=""),
                run_record(MOVED_ON["sha"], conclusion="failure"),
            ])
        report, _ = settle(ops, repo_slug=MOVED_ON["repo"])
        self.assertEqual(ops.canceled, [])
        self.assertEqual(
            [d.decision for d in report.decisions],
            [repair_card.SETTLED_RED])

    def test_an_in_progress_run_over_a_green_one_still_cancels(self):
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(MOVED_ON)], refs=[],
            runs=[
                run_record("b" * 40, status="in_progress", conclusion=""),
                run_record(MOVED_ON["sha"], conclusion="success"),
            ])
        settle(ops, repo_slug=MOVED_ON["repo"])
        self.assertEqual(ops.canceled, [MOVED_ON["card"]])

    def test_no_completed_run_at_all_is_unknown_never_green(self):
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(MOVED_ON)], refs=[],
            runs=[run_record("b" * 40, status="queued", conclusion="")])
        report, log = settle(ops, repo_slug=MOVED_ON["repo"])
        self.assertEqual(ops.canceled, [])
        self.assertEqual(
            [d.decision for d in report.decisions],
            [repair_card.SETTLED_UNKNOWN])
        self.assertIn("UNKNOWN", log)


class AnUnreadableAnswerIsNeverGreenTest(unittest.TestCase):
    """Rule 4: UNKNOWN — do nothing, and say so with the card id."""

    def _assert_untouched_and_unknown(self, ops, card):
        report, log = settle(ops, repo_slug=card["repo"])
        self.assertEqual(ops.canceled, [], "a failed read must never cancel")
        self.assertEqual(ops.comments, [])
        self.assertIn("UNKNOWN", log)
        self.assertIn(card["card"], log)
        self.assertEqual(
            [d.decision for d in report.decisions],
            [repair_card.SETTLED_UNKNOWN])

    def test_an_unreadable_run_listing_never_cancels(self):
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(FLAKE)], refs=[],
            runs_error=RuntimeError("HTTP 403: Resource not accessible"))
        self._assert_untouched_and_unknown(ops, FLAKE)

    def test_a_none_run_listing_never_cancels(self):
        # The sweep's own Actions helper answers None — never [] — when the
        # read fails, and None must not read as "no runs, so not red".
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(FLAKE)], refs=[], runs=None)
        self._assert_untouched_and_unknown(ops, FLAKE)

    def test_an_unreadable_pull_request_listing_never_cancels(self):
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(FLAKE)], refs=None,
            runs=[run_record(FLAKE["sha"])])
        self._assert_untouched_and_unknown(ops, FLAKE)

    def test_a_raising_pull_request_listing_never_cancels(self):
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(FLAKE)],
            refs_error=RuntimeError("HTTP 403: API rate limit exceeded"),
            runs=[run_record(FLAKE["sha"])])
        self._assert_untouched_and_unknown(ops, FLAKE)

    def test_an_unreadable_board_settles_nothing_and_says_so(self):
        ops = FakeGitHubAndLinear(
            board_error=RuntimeError("Linear says 429: rate limited"))
        report, log = settle(ops)
        self.assertEqual(ops.canceled, [])
        self.assertEqual(report.decisions, [])
        self.assertIn("UNKNOWN", log)

    def test_a_card_with_no_failing_commit_on_it_is_unknown(self):
        card = repair_card_row(FLAKE)
        card["description"] = "**Repo:** portico\n\nsomebody rewrote the body\n"
        ops = FakeGitHubAndLinear(
            cards=[card], refs=[], runs=[run_record(FLAKE["sha"])])
        self._assert_untouched_and_unknown(ops, FLAKE)

    def test_a_refused_cancel_is_reported_and_the_sweep_carries_on(self):
        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(FLAKE), repair_card_row(REREAD)],
            refs=[], runs=[run_record(FLAKE["sha"])],
            cancel_error=RuntimeError("Linear refused"))
        report, log = settle(ops, repo_slug="portico")
        self.assertTrue(report.failures)
        self.assertEqual(ops.comments, [], "no comment claiming a cancel that "
                                          "never happened")

    def test_a_fatal_exception_is_never_swallowed(self):
        class Fatal(RuntimeError):
            pass

        ops = FakeGitHubAndLinear(
            cards=[repair_card_row(FLAKE)], refs=[],
            runs=[run_record(FLAKE["sha"])], cancel_error=Fatal("rate limited"))
        with self.assertRaises(Fatal):
            settle(ops, repo_slug="portico", fatal=(Fatal,))


class OneCommentPerCardForEverTest(unittest.TestCase):
    """A second pass over an already-settled card does nothing."""

    def test_an_already_canceled_card_is_not_touched_again(self):
        card = repair_card_row(FLAKE, state="Canceled")
        ops = FakeGitHubAndLinear(
            cards=[card], refs=[], runs=[run_record(FLAKE["sha"])])
        settle(ops, repo_slug="portico")
        self.assertEqual(ops.canceled, [])
        self.assertEqual(ops.comments, [])

    def test_a_done_card_is_not_touched_again(self):
        card = repair_card_row(FLAKE, state="Done")
        ops = FakeGitHubAndLinear(
            cards=[card], refs=[], runs=[run_record(FLAKE["sha"])])
        settle(ops, repo_slug="portico")
        self.assertEqual(ops.canceled, [])
        self.assertEqual(ops.comments, [])

    def test_a_card_already_carrying_the_note_is_not_commented_twice(self):
        first = FakeGitHubAndLinear(
            cards=[repair_card_row(FLAKE)], refs=[],
            runs=[run_record(FLAKE["sha"])])
        settle(first, repo_slug="portico")
        note = first.comments[0][1]

        second = FakeGitHubAndLinear(
            cards=[repair_card_row(FLAKE, comments=[note])], refs=[],
            runs=[run_record(FLAKE["sha"])])
        settle(second, repo_slug="portico")
        self.assertEqual(second.comments, [])
        self.assertEqual(second.canceled, [])


class TheSubcommandTest(unittest.TestCase):
    """`repair_card.py` gains a SECOND subcommand, beside `open`."""

    def test_settle_is_a_subcommand(self):
        p = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "repair_card.py"),
             "settle", "--help"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_open_still_works(self):
        p = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "repair_card.py"),
             "open", "--help"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(p.returncode, 0, p.stderr)


class TheSweepCallsItOncePerPassTest(unittest.TestCase):
    """The one shared file: `scripts/reconcile.py`, one call beside the rest."""

    source = open(os.path.join(SCRIPTS, "reconcile.py")).read()

    def test_the_sweep_has_a_settle_step(self):
        self.assertIn("settle_repair_cards", self.source)

    def test_it_runs_beside_the_existing_backstops(self):
        tree = ast.parse(self.source)
        main = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "main")
        names = {
            e.id for n in ast.walk(main) if isinstance(n, ast.Tuple)
            for e in n.elts if isinstance(e, ast.Name)
        }
        self.assertIn("settle_repair_cards", names)

    def test_it_calls_the_one_settle_seam(self):
        tree = ast.parse(self.source)
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "settle_repair_cards")
        calls = {
            f"{n.func.value.id}.{n.func.attr}"
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
        }
        self.assertIn("repair_card.settle", calls)

    def test_discovery_imports_the_anchor_rather_than_retyping_it(self):
        # The criterion: a change to the title cannot silently stop the sweep
        # finding its own cards. The sweep asks repair_card, never a literal.
        self.assertIn("repair_card.is_repair_card", self.source)
        self.assertNotIn("Red main repaired", self.source)


class TheSweepEndToEndTest(unittest.TestCase):
    """The scenario the unit tests above cannot reach: the sweep's OWN board
    snapshot, its own `gh` reads and its own Linear writes, wired together.

    Unit-green is not live-working — this feature touches Linear, GitHub and
    the sweep, and every one of the seams below is a place the wiring can be
    right in isolation and wrong joined up.
    """

    def setUp(self):
        import reconcile

        self.reconcile = reconcile
        self.slug = reconcile.REPO_SLUG

    def _board(self, *, state="In Progress"):
        """One repair card in the shape `active_cards()` really answers in."""
        return [{
            "identifier": FLAKE["card"],
            "title": repair_card.card_title(FLAKE["workflow"], FLAKE["sha"]),
            "description": repair_card.card_body(
                workflow_name=FLAKE["workflow"], run_url=RUN_URL,
                head_sha=FLAKE["sha"], repo_slug=self.slug, attempt=1),
            "state": {"name": state},
            "labels": {"nodes": [{"name": f"repo:{self.slug}"}]},
            "comments": {"nodes": []},
        }, {
            "identifier": "DRE-9999",
            "title": "bureau-pipeline: some other card entirely",
            "description": f"**Repo:** {self.slug}\n",
            "state": {"name": state},
            "labels": {"nodes": [{"name": f"repo:{self.slug}"}]},
            "comments": {"nodes": []},
        }]

    def _run(self, *, runs_json, refs_json, board=None):
        """Drive `reconcile.settle_repair_cards()` with GitHub stubbed."""
        from unittest.mock import patch

        rc = self.reconcile
        calls = {"state": [], "comment": []}
        with patch.object(rc, "active_cards", lambda *a, **k: board or self._board()), \
             patch.object(rc, "default_branch", lambda: "main"), \
             patch.object(rc, "_actions_read", lambda args: (runs_json, None)), \
             patch.object(rc, "gh_read", lambda *a: refs_json), \
             patch.object(rc.linear_ops, "cmd_state",
                          lambda i, s, *f: calls["state"].append((i, s))), \
             patch.object(rc.linear_ops, "cmd_comment",
                          lambda i, b, *f: calls["comment"].append((i, b))):
            before_writes = list(rc._write_failures)
            rc._write_failures.clear()
            try:
                rc.settle_repair_cards()
                failures = list(rc._write_failures)
            finally:
                rc._write_failures[:] = before_writes
        return calls, failures

    def test_a_green_main_cancels_the_card_through_the_sweep(self):
        calls, failures = self._run(
            runs_json=f'[{{"status":"completed","conclusion":"success",'
                      f'"headSha":"{FLAKE["sha"]}","url":"{RUN_URL}"}}]',
            refs_json="[]",
        )
        self.assertEqual(calls["state"], [(FLAKE["card"], "Canceled")])
        self.assertEqual(len(calls["comment"]), 1)
        self.assertIn(RUN_URL, calls["comment"][0][1])
        self.assertEqual(failures, [])

    def test_an_open_repair_pull_request_stops_the_sweep_cancelling(self):
        ref = red_main_repair.repair_branch(
            FLAKE["sha"], 1, card=FLAKE["card"])
        calls, _ = self._run(
            runs_json=f'[{{"status":"completed","conclusion":"success",'
                      f'"headSha":"{FLAKE["sha"]}","url":"{RUN_URL}"}}]',
            refs_json=f'[{{"headRefName":"{ref}"}}]',
        )
        self.assertEqual(calls["state"], [])
        self.assertEqual(calls["comment"], [])

    def test_a_refused_pull_request_listing_never_cancels(self):
        from unittest.mock import patch

        rc = self.reconcile
        runs = (f'[{{"status":"completed","conclusion":"success",'
                f'"headSha":"{FLAKE["sha"]}","url":"{RUN_URL}"}}]')

        def refused(*a):
            raise rc.ReconcileRateLimited("HTTP 403: API rate limit exceeded")

        calls = {"state": [], "comment": []}
        with patch.object(rc, "active_cards", lambda *a, **k: self._board()), \
             patch.object(rc, "default_branch", lambda: "main"), \
             patch.object(rc, "_actions_read", lambda args: (runs, None)), \
             patch.object(rc, "gh_read", refused), \
             patch.object(rc.linear_ops, "cmd_state",
                          lambda i, s, *f: calls["state"].append((i, s))), \
             patch.object(rc.linear_ops, "cmd_comment",
                          lambda i, b, *f: calls["comment"].append((i, b))):
            before = list(rc._degraded)
            rc._degraded.clear()
            try:
                rc.settle_repair_cards()
                degraded = list(rc._degraded)
            finally:
                rc._degraded[:] = before
        self.assertEqual(calls["state"], [], "a failed read must never cancel")
        self.assertTrue(degraded, "the sweep must say what it could not read")

    def test_an_unreadable_run_listing_never_cancels(self):
        from unittest.mock import patch

        rc = self.reconcile
        calls = {"state": []}
        with patch.object(rc, "active_cards", lambda *a, **k: self._board()), \
             patch.object(rc, "default_branch", lambda: "main"), \
             patch.object(rc, "_actions_read",
                          lambda args: (None, "rc=1: HTTP 403")), \
             patch.object(rc, "gh_read", lambda *a: "[]"), \
             patch.object(rc.linear_ops, "cmd_state",
                          lambda i, s, *f: calls["state"].append((i, s))), \
             patch.object(rc.linear_ops, "cmd_comment", lambda i, b, *f: None):
            before = list(rc._degraded)
            rc._degraded.clear()
            try:
                rc.settle_repair_cards()
                degraded = list(rc._degraded)
            finally:
                rc._degraded[:] = before
        self.assertEqual(calls["state"], [])
        self.assertTrue(any("repair settle" in line for line in degraded))

    def test_a_refused_cancel_lands_on_the_sweeps_own_failure_rail(self):
        from unittest.mock import patch

        rc = self.reconcile
        runs = (f'[{{"status":"completed","conclusion":"success",'
                f'"headSha":"{FLAKE["sha"]}","url":"{RUN_URL}"}}]')

        def refuses(identifier, state, *flags):
            raise rc.linear_ops.LinearError("Linear says 500")

        with patch.object(rc, "active_cards", lambda *a, **k: self._board()), \
             patch.object(rc, "default_branch", lambda: "main"), \
             patch.object(rc, "_actions_read", lambda args: (runs, None)), \
             patch.object(rc, "gh_read", lambda *a: "[]"), \
             patch.object(rc.linear_ops, "cmd_state", refuses), \
             patch.object(rc.linear_ops, "cmd_comment", lambda i, b, *f: None):
            before = list(rc._write_failures)
            rc._write_failures.clear()
            try:
                rc.settle_repair_cards()
                failures = list(rc._write_failures)
            finally:
                rc._write_failures[:] = before
        self.assertTrue(failures)
        self.assertIn(FLAKE["card"], failures[0])

    def test_a_linear_quota_exhaustion_is_the_runs_own_exit_code(self):
        from unittest.mock import patch

        rc = self.reconcile
        runs = (f'[{{"status":"completed","conclusion":"success",'
                f'"headSha":"{FLAKE["sha"]}","url":"{RUN_URL}"}}]')

        def refuses(identifier, state, *flags):
            raise rc.linear_ops.LinearRateLimited("quota exhausted")

        with patch.object(rc, "active_cards", lambda *a, **k: self._board()), \
             patch.object(rc, "default_branch", lambda: "main"), \
             patch.object(rc, "_actions_read", lambda args: (runs, None)), \
             patch.object(rc, "gh_read", lambda *a: "[]"), \
             patch.object(rc.linear_ops, "cmd_state", refuses), \
             patch.object(rc.linear_ops, "cmd_comment", lambda i, b, *f: None):
            with self.assertRaises(rc.linear_ops.LinearRateLimited):
                rc.settle_repair_cards()

    def test_the_pull_request_listing_is_not_the_thirty_row_one(self):
        # A repair pull request that fell off the crashed-review region's
        # 30-row window would read as "no repair in flight" and this step
        # would cancel a card whose repair is about to merge.
        from unittest.mock import patch

        rc = self.reconcile
        seen: list = []
        with patch.object(rc, "active_cards", lambda *a, **k: self._board()), \
             patch.object(rc, "default_branch", lambda: "main"), \
             patch.object(rc, "_actions_read", lambda args: ("[]", None)), \
             patch.object(rc, "gh_read", lambda *a: (seen.append(a), "[]")[1]), \
             patch.object(rc.linear_ops, "cmd_state", lambda i, s, *f: None), \
             patch.object(rc.linear_ops, "cmd_comment", lambda i, b, *f: None):
            rc.settle_repair_cards()
        self.assertTrue(seen, "the step must read the open pull requests")
        self.assertIn("100", seen[0])


# DRE-6511, 2026-10-09: Pipeline Tests red at bcb36adf6037, and the sweep read
# the card twice (runs 38006250226 and 38008147573) and said "left alone" both
# times while `repair/DRE-6511-bcb36adf6037` sat on the remote one commit ahead
# of main under no pull request at all.
UNLANDED = {
    "card": "DRE-6511",
    "sha": "bcb36adf60370d0e8052c5a3f81021900f93165b",
    "workflow": "Pipeline Tests",
    "repo": "bureau-pipeline",
}
UNLANDED_BRANCH = "repair/DRE-6511-bcb36adf6037"
UNLANDED_HEAD = "64a5a2ac7" + "0" * 31
RED_RUN_URL = ("https://github.com/dreadnought-foundry/bureau-pipeline/"
               "actions/runs/38006250226")


def unlanded_ops(*, comments=(), heads=None, pulls=..., compared=...,
                 **kw) -> FakeGitHubAndLinear:
    """The 2026-10-09 reading: main red, no open pull request, the repair
    branch pushed and one commit ahead, and no pull request of any state."""
    return FakeGitHubAndLinear(
        cards=[repair_card_row(UNLANDED, comments=comments)], refs=[],
        runs=[run_record(UNLANDED["sha"], conclusion="failure",
                         url=RED_RUN_URL)],
        heads={UNLANDED_BRANCH: UNLANDED_HEAD} if heads is None else heads,
        pulls={UNLANDED_BRANCH: []} if pulls is ... else pulls,
        compared=({"status": "ahead", "ahead_by": 1}
                  if compared is ... else compared),
        **kw,
    )


class AnUnlandedRepairBranchIsSaidOnTheCardTest(unittest.TestCase):
    """Rule 3, DRE-6524: a finished fix sitting unlanded is said on the card."""

    def test_the_replay_of_2026_10_09_posts_one_comment(self):
        self.assertEqual(
            red_main_repair.repair_branch(UNLANDED["sha"], 1,
                                          card=UNLANDED["card"]),
            UNLANDED_BRANCH)
        ops = unlanded_ops()
        report, log = settle(ops)
        self.assertEqual(len(ops.comments), 1)
        identifier, body = ops.comments[0]
        self.assertEqual(identifier, UNLANDED["card"])
        self.assertTrue(body.startswith(repair_card.UNLANDED_MARKER), body)
        self.assertIn(UNLANDED_BRANCH, body)
        self.assertIn(UNLANDED_HEAD[:red_main_repair.SHA_CHARS], body)
        self.assertEqual(
            [d.decision for d in report.decisions],
            [repair_card.SETTLED_RED_UNLANDED])

    def test_the_card_state_does_not_change(self):
        ops = unlanded_ops()
        settle(ops)
        self.assertEqual(ops.canceled, [])

    def test_the_comment_says_what_happened_and_who_opens_it(self):
        ops = unlanded_ops()
        settle(ops)
        body = ops.comments[0][1].lower()
        self.assertIn("pushed", body)
        self.assertIn("no pull request", body)
        self.assertIn("repair loop", body)
        self.assertIn("person", body)

    def test_the_sweep_log_names_the_branch_and_the_head(self):
        ops = unlanded_ops()
        _, log = settle(ops)
        self.assertIn(UNLANDED["card"], log)
        self.assertIn(UNLANDED_BRANCH, log)
        self.assertIn(UNLANDED_HEAD[:red_main_repair.SHA_CHARS], log)

    def test_the_branch_is_compared_against_the_default_branch(self):
        ops = unlanded_ops()
        settle(ops)
        self.assertIn(("compare", "main", UNLANDED_BRANCH), ops.branch_reads)

    def test_the_contract_words_are_the_cards(self):
        self.assertEqual(repair_card.UNLANDED_MARKER,
                         "📌 Repair branch pushed, no pull request")
        self.assertEqual(repair_card.SETTLED_RED_UNLANDED,
                         "main-still-red-unlanded")


class TheUnlandedCommentIsSaidOncePerHeadTest(unittest.TestCase):
    """Idempotency is read off the card's own comments, as SETTLE_MARKER is."""

    def _first_note(self) -> str:
        first = unlanded_ops()
        settle(first)
        return first.comments[0][1]

    def test_a_second_pass_posts_nothing_and_says_already_said(self):
        second = unlanded_ops(comments=[self._first_note()])
        report, log = settle(second)
        self.assertEqual(second.comments, [])
        self.assertEqual(
            [d.decision for d in report.decisions], [repair_card.SETTLED_RED])
        self.assertIn("already said", report.decisions[0].why)
        self.assertIn("already said", log)

    def test_a_moved_head_gets_one_new_comment_naming_it(self):
        moved = "77c0ffee1234" + "0" * 28
        second = unlanded_ops(comments=[self._first_note()],
                              heads={UNLANDED_BRANCH: moved})
        report, _ = settle(second)
        self.assertEqual(len(second.comments), 1)
        body = second.comments[0][1]
        self.assertIn(moved[:red_main_repair.SHA_CHARS], body)
        self.assertNotIn(UNLANDED_HEAD[:red_main_repair.SHA_CHARS], body)
        self.assertEqual(
            [d.decision for d in report.decisions],
            [repair_card.SETTLED_RED_UNLANDED])


class AnUnreadableBranchIsNeverAYesTest(unittest.TestCase):
    """Rule 4 holds for the three new reads: UNKNOWN, card id, nothing posted."""

    def _assert_unknown(self, ops):
        report, log = settle(ops)
        self.assertEqual(ops.comments, [])
        self.assertEqual(ops.canceled, [])
        self.assertEqual(
            [d.decision for d in report.decisions],
            [repair_card.SETTLED_UNKNOWN])
        self.assertIn("UNKNOWN", log)
        self.assertIn(UNLANDED["card"], log)
        return report, log

    def test_branch_heads_answering_none(self):
        ops = unlanded_ops()
        ops._heads = None  # the fixture's own default is "no branch at all"
        self._assert_unknown(ops)

    def test_pulls_for_head_answering_none(self):
        self._assert_unknown(unlanded_ops(pulls=None))

    def test_compare_answering_none(self):
        self._assert_unknown(unlanded_ops(compared=None))

    def test_compare_without_an_integer_ahead_by(self):
        for payload in ({"status": "ahead"}, {"ahead_by": "1"},
                        {"ahead_by": None}, {"ahead_by": True}, ["ahead"]):
            with self.subTest(payload=payload):
                self._assert_unknown(unlanded_ops(compared=payload))

    def test_any_of_the_three_raising(self):
        boom = RuntimeError("HTTP 403: API rate limit exceeded")
        for kw in ({"heads_error": boom}, {"pulls_error": boom},
                   {"compare_error": boom}):
            with self.subTest(kw=list(kw)):
                report, _ = self._assert_unknown(unlanded_ops(**kw))
                self.assertEqual(report.failures, [])


class ABranchThatDoesNotQualifyIsTodaysAnswerTest(unittest.TestCase):
    """No qualifying branch: SETTLED_RED, today's `why`, nothing posted."""

    def _todays_why(self) -> str:
        ops = unlanded_ops(heads={})
        report, _ = settle(ops)
        return report.decisions[0].why

    def _assert_todays_answer(self, ops):
        report, _ = settle(ops)
        self.assertEqual(ops.comments, [])
        self.assertEqual(
            [d.decision for d in report.decisions], [repair_card.SETTLED_RED])
        self.assertEqual(report.decisions[0].why, self._todays_why())

    def test_todays_why_is_unchanged(self):
        self.assertTrue(self._todays_why().endswith(
            "main is still red, and the repair budget path in "
            "red-main-repair.yml owns what happens next"))

    def test_a_branch_with_a_closed_pull_request(self):
        self._assert_todays_answer(
            unlanded_ops(pulls={UNLANDED_BRANCH: ["CLOSED"]}))

    def test_a_branch_with_a_merged_pull_request(self):
        self._assert_todays_answer(
            unlanded_ops(pulls={UNLANDED_BRANCH: ["MERGED"]}))

    def test_a_branch_not_ahead_of_main(self):
        self._assert_todays_answer(
            unlanded_ops(compared={"status": "identical", "ahead_by": 0}))

    def test_a_repair_branch_for_another_card_is_not_read(self):
        other = red_main_repair.repair_branch("f" * 40, 1, card="DRE-9999")
        ops = unlanded_ops(heads={other: UNLANDED_HEAD},
                           pulls={other: []})
        self._assert_todays_answer(ops)
        self.assertFalse(
            [r for r in ops.branch_reads if r[0] != "branch_heads"])

    def test_the_cardless_fallback_branch_counts_too(self):
        fallback = red_main_repair.repair_branch(UNLANDED["sha"], 1)
        ops = unlanded_ops(heads={fallback: UNLANDED_HEAD},
                           pulls={fallback: []})
        report, _ = settle(ops)
        self.assertEqual(len(ops.comments), 1)
        self.assertIn(fallback, ops.comments[0][1])


class TheNewReadsAreLazyTest(unittest.TestCase):
    """Rule 1 and rule 2 pay for none of the three new reads."""

    def test_an_open_pull_request_reads_no_branch(self):
        ref = red_main_repair.repair_branch(
            UNLANDED["sha"], 1, card=UNLANDED["card"])
        ops = unlanded_ops()
        ops._refs = [ref]
        report, _ = settle(ops)
        self.assertEqual(
            [d.decision for d in report.decisions],
            [repair_card.SETTLED_PR_OPEN])
        self.assertEqual(ops.branch_reads, [])
        self.assertEqual(ops.comments, [])

    def test_a_green_main_reads_no_branch(self):
        ops = unlanded_ops()
        ops._runs = [run_record(UNLANDED["sha"])]
        report, _ = settle(ops)
        self.assertEqual(
            [d.decision for d in report.decisions], [repair_card.SETTLED_GREEN])
        self.assertEqual(ops.branch_reads, [])

    def test_no_repair_card_reads_no_branch(self):
        ops = FakeGitHubAndLinear(cards=[])
        settle(ops)
        self.assertEqual(ops.branch_reads, [])


def _settle_ops_calls() -> set:
    """Every `ops.<name>(…)` call `repair_card`'s settle path makes."""
    tree = ast.parse(open(os.path.join(SCRIPTS, "repair_card.py")).read())
    return {
        n.func.attr for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "ops"
    }


class TheSeamsMatchSettlesCallsTest(unittest.TestCase):
    """Both real seams carry every method `settle` calls."""

    def test_settle_calls_the_three_new_seams(self):
        self.assertLessEqual(
            {"branch_heads", "pulls_for_head", "compare"}, _settle_ops_calls())

    def test_the_sweep_seam_carries_every_method_settle_calls(self):
        import reconcile

        settle_only = _settle_ops_calls() - {
            # `open_card`'s Linear seam, not settle's.
            "find_open", "create_card", "stamp_card"}
        for name in sorted(settle_only):
            with self.subTest(name=name):
                self.assertTrue(
                    callable(getattr(reconcile._RepairSettleOps, name, None)),
                    f"reconcile._RepairSettleOps has no {name}")
                self.assertTrue(
                    callable(getattr(repair_card._SettleOps, name, None)),
                    f"repair_card._SettleOps has no {name}")


class TheSweepsBranchReadsTest(unittest.TestCase):
    """`reconcile._RepairSettleOps`: one read each, None when unreadable."""

    def setUp(self):
        import reconcile

        self.rc = reconcile

    def _ops_with(self, answer):
        """A fresh seam whose `gh_read` answers `answer` (or raises it)."""
        from unittest.mock import patch

        seen: list = []

        def reads(*args):
            seen.append(args)
            if isinstance(answer, Exception):
                raise answer
            return answer

        patcher = patch.object(self.rc, "gh_read", reads)
        patcher.start()
        self.addCleanup(patcher.stop)
        degraded = list(self.rc._degraded)
        self.addCleanup(lambda: self.rc._degraded.__setitem__(
            slice(None), degraded))
        return self.rc._RepairSettleOps(), seen

    def test_branch_heads_maps_ref_names_to_shas(self):
        ops, seen = self._ops_with(
            f'[{{"ref":"refs/heads/{UNLANDED_BRANCH}",'
            f'"object":{{"sha":"{UNLANDED_HEAD}","type":"commit"}}}}]')
        self.assertEqual(ops.branch_heads(), {UNLANDED_BRANCH: UNLANDED_HEAD})
        self.assertIn(
            f"repos/{self.rc.REPO}/git/matching-refs/heads/repair/", seen[0])

    def test_branch_heads_is_read_once_per_pass(self):
        ops, seen = self._ops_with("[]")
        self.assertEqual(ops.branch_heads(), {})
        ops.branch_heads()
        self.assertEqual(len(seen), 1)

    def test_pulls_for_head_reads_every_state(self):
        ops, seen = self._ops_with('[{"state":"CLOSED"}]')
        self.assertEqual(ops.pulls_for_head(UNLANDED_BRANCH), ["CLOSED"])
        self.assertIn("--head", seen[0])
        self.assertIn(UNLANDED_BRANCH, seen[0])
        self.assertIn("all", seen[0])

    def test_compare_returns_the_payload(self):
        ops, seen = self._ops_with('{"status":"ahead","ahead_by":1}')
        self.assertEqual(ops.compare("main", UNLANDED_BRANCH),
                         {"status": "ahead", "ahead_by": 1})
        self.assertTrue(any(
            f"compare/main...{UNLANDED_BRANCH}" in arg for arg in seen[0]))

    def test_a_failed_read_is_none_never_empty(self):
        boom = self.rc.ReconcileReadError("HTTP 502")
        for call in (lambda o: o.branch_heads(),
                     lambda o: o.pulls_for_head(UNLANDED_BRANCH),
                     lambda o: o.compare("main", UNLANDED_BRANCH)):
            ops, _ = self._ops_with(boom)
            self.assertIsNone(call(ops))

    def test_an_unparseable_read_is_none_never_empty(self):
        for raw in ("not json", '{"a": 1}', "null"):
            with self.subTest(raw=raw):
                ops, _ = self._ops_with(raw)
                self.assertIsNone(ops.branch_heads())
                ops, _ = self._ops_with(raw)
                self.assertIsNone(ops.pulls_for_head(UNLANDED_BRANCH))
        for raw in ("not json", "[]", "null"):
            with self.subTest(raw=raw):
                ops, _ = self._ops_with(raw)
                self.assertIsNone(ops.compare("main", UNLANDED_BRANCH))

    def test_a_failed_read_says_so(self):
        ops, _ = self._ops_with(self.rc.ReconcileReadError("HTTP 502"))
        self.rc._degraded.clear()
        ops.branch_heads()
        self.assertTrue(any("repair settle" in d for d in self.rc._degraded))


class TheCliSeamsBranchReadsTest(unittest.TestCase):
    """`repair_card._SettleOps` (by hand): None on a failed or bad read."""

    def _ops(self, rc=0, stdout=""):
        from unittest.mock import patch

        done = subprocess.CompletedProcess([], rc, stdout=stdout, stderr="no")
        patcher = patch.object(repair_card.subprocess, "run",
                               lambda *a, **k: done)
        patcher.start()
        self.addCleanup(patcher.stop)
        return repair_card._SettleOps("dreadnought-foundry/bureau-pipeline")

    def test_reads_answer(self):
        self.assertEqual(
            self._ops(stdout=f'[{{"ref":"refs/heads/{UNLANDED_BRANCH}",'
                             f'"object":{{"sha":"{UNLANDED_HEAD}"}}}}]'
                      ).branch_heads(),
            {UNLANDED_BRANCH: UNLANDED_HEAD})
        self.assertEqual(
            self._ops(stdout='[{"state":"OPEN"}]').pulls_for_head("x"),
            ["OPEN"])
        self.assertEqual(
            self._ops(stdout='{"status":"ahead","ahead_by":2}').compare(
                "main", "x"),
            {"status": "ahead", "ahead_by": 2})

    def test_a_failed_read_is_none(self):
        self.assertIsNone(self._ops(rc=1).branch_heads())
        self.assertIsNone(self._ops(rc=1).pulls_for_head("x"))
        self.assertIsNone(self._ops(rc=1).compare("main", "x"))

    def test_an_unparseable_read_is_none(self):
        self.assertIsNone(self._ops(stdout="nope").branch_heads())
        self.assertIsNone(self._ops(stdout="{}").pulls_for_head("x"))
        self.assertIsNone(self._ops(stdout="[]").compare("main", "x"))


class TheSweepSaysItEndToEndTest(unittest.TestCase):
    """The 2026-10-09 replay through the sweep's OWN seams and Linear writes."""

    def setUp(self):
        import reconcile

        self.reconcile = reconcile
        self.slug = reconcile.REPO_SLUG

    def _board(self, *, state="In Progress"):
        return [{
            "identifier": UNLANDED["card"],
            "title": repair_card.card_title(UNLANDED["workflow"],
                                            UNLANDED["sha"]),
            "description": repair_card.card_body(
                workflow_name=UNLANDED["workflow"], run_url=RED_RUN_URL,
                head_sha=UNLANDED["sha"], repo_slug=self.slug, attempt=1),
            "state": {"name": state},
            "labels": {"nodes": [{"name": f"repo:{self.slug}"}]},
            "comments": {"nodes": []},
        }]

    def test_the_sweep_posts_the_unlanded_comment(self):
        from unittest.mock import patch

        rc = self.reconcile
        runs = (f'[{{"status":"completed","conclusion":"failure",'
                f'"headSha":"{UNLANDED["sha"]}","url":"{RED_RUN_URL}"}}]')

        def reads(*args):
            joined = " ".join(args)
            if "matching-refs" in joined:
                return (f'[{{"ref":"refs/heads/{UNLANDED_BRANCH}",'
                        f'"object":{{"sha":"{UNLANDED_HEAD}"}}}}]')
            if "--head" in args:
                return "[]"
            if "/compare/" in joined:
                return '{"status":"ahead","ahead_by":1}'
            return "[]"  # the open pull request listing

        calls = {"state": [], "comment": []}
        with patch.object(rc, "active_cards", lambda *a, **k: self._board()), \
             patch.object(rc, "default_branch", lambda: "main"), \
             patch.object(rc, "_actions_read", lambda args: (runs, None)), \
             patch.object(rc, "gh_read", reads), \
             patch.object(rc.linear_ops, "cmd_state",
                          lambda i, s, *f: calls["state"].append((i, s))), \
             patch.object(rc.linear_ops, "cmd_comment",
                          lambda i, b, *f: calls["comment"].append((i, b))):
            rc.settle_repair_cards()
        self.assertEqual(calls["state"], [])
        self.assertEqual(len(calls["comment"]), 1)
        identifier, body = calls["comment"][0]
        self.assertEqual(identifier, UNLANDED["card"])
        self.assertTrue(body.startswith(repair_card.UNLANDED_MARKER))
        self.assertIn(UNLANDED_BRANCH, body)


if __name__ == "__main__":
    unittest.main()
