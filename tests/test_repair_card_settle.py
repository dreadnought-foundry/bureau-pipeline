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
  * the sweep calls it once per pass, beside the existing sweep steps.

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
                 refs_error=None, runs_error=None, cancel_error=None):
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

    def comment(self, identifier, body):
        self.comments.append((identifier, body))


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


if __name__ == "__main__":
    unittest.main()
