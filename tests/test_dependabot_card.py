"""A dependabot pull request gets a Linear card of its own (DRE-3665).

The CEO, 2026-09-12, on the console's Pull Requests tab looking at atlas #154
(`chore(deps): bump the python-minor-patch group with 4 updates`, CARD "—",
"Stuck — needs you"): "Right now, our pen bots are not adding cards to things.
They should."

The pipeline reads a pull request's card out of its HEAD REF, and dependabot
cannot name its branch after a card. So the join is a machine-written first
line in the pull request body (`scripts/dependabot_card.py`), written by the
reconcile sweep when it files the card, and honoured only on a `dependabot/*`
head authored by dependabot[bot]. What these tests pin, one class per reader
and one per acceptance criterion:

  * THE READER — the marker is read off the first lines of a dependabot pull
    request's body and nowhere else: a `DRE-<n>` inside dependabot's quoted
    release notes is not a card, a human branch's body is still prose
    (DRE-2027), and a human's branch merely NAMED dependabot/… is nobody's.
  * THE CARD — `<slug>: <pull request title>`, `repo:<slug>` + the automation
    marker + a role, In Review, and it passes the live card gate.
  * LINEAR-SYNC — the fenced arm in the Card → Done step, executed verbatim:
    a stamped dependabot merge resolves its card; an agent branch keeps its
    own; a human pull request resolves nothing.
  * THE FILER — `reconcile.card_dependabot_prs`, fake `gh` + fake Linear in
    the shapes the dependabot suites already use: one card per pull request,
    idempotent on a second sweep, a wiped marker re-stamped from Linear by
    URL rather than re-filed, merge → Done, close-unmerged → Canceled,
    re-open → the same card, a pull request that already carries a card left
    alone, paced per sweep, off-map repos quiet, failures recorded.
  * THE SWEEP — an automation card is out of the WIP base and out of the
    nudge loop whose no-PR branch would requeue it into Todo.
  * THE RECORD — the cancel comment is declared in the act registry and the
    lane contract permits the sweep to write Canceled.

Run: cd bureau-pipeline && python3 -m pytest tests/test_dependabot_card.py -v
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
LINEAR_SYNC = ROOT / ".github" / "workflows" / "linear-sync.yml"
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "EveryBite/atlas")
os.environ.setdefault("REPO_SLUG", "atlas")
os.environ.setdefault("GH_TOKEN", "x")

import check_act_receipts  # noqa: E402
import lane_contract  # noqa: E402
import merge_gate  # noqa: E402
import reconcile  # noqa: E402
import validate_card  # noqa: E402

try:
    import dependabot_card  # noqa: E402
except ImportError:  # RED on the unfixed tree: the module does not exist yet
    dependabot_card = None

REPO = "EveryBite/atlas"
SLUG = "atlas"
CARD = "DRE-9001"
PR_URL = "https://github.com/EveryBite/atlas/pull/154"
PR_TITLE = "chore(deps): bump the python-minor-patch group with 4 updates"
BRANCH = "dependabot/uv/python-minor-patch-6372e7bff4"
# The real atlas #154 body shape (read live 2026-09-12): the "Bumps"/"Updates"
# lines, then dependabot's quoted release notes — which here mention a card
# id, because a vendor's changelog may say anything.
PR_BODY = "\n".join([
    "Bumps the python-minor-patch group with 4 updates: [ruff](https://github.com/astral-sh/ruff), "
    "[alembic](https://github.com/sqlalchemy/alembic), [pypdf](https://github.com/py-pdf/pypdf) "
    "and [mcp](https://github.com/modelcontextprotocol/python-sdk).",
    "",
    "Updates `ruff` from 0.16.5 to 0.16.6",
    "<details>",
    "<summary>Release notes</summary>",
    "<p><em>Sourced from ruff's releases.</em></p>",
    "<li>Fix the DRE-42 regression in the linter (#28219)</li>",
    "**Card:** DRE-77 is a line inside a quoted changelog, not a join",
    "</details>",
    "Updates `alembic` from 1.19.1 to 1.19.2",
    "Updates `pypdf` from 6.16.2 to 6.18.0",
    "Updates `mcp` from 1.29.1 to 1.30.0",
    "",
    "Dependabot will resolve any conflicts with this PR as long as you don't alter it yourself.",
])
FENCE_OPEN = "# >>> DRE-3665 dependabot join"
FENCE_CLOSE = "# <<< DRE-3665 dependabot join"


def _needs_module(test: unittest.TestCase) -> None:
    if dependabot_card is None:
        test.fail("scripts/dependabot_card.py does not exist — the join has no reader")


def _pr(number=154, *, branch=BRANCH, author="app/dependabot", state="OPEN",
        body=PR_BODY, title=PR_TITLE, url=None):
    return {
        "number": number,
        "url": url or f"https://github.com/{REPO}/pull/{number}",
        "title": title,
        "body": body,
        "headRefName": branch,
        "author": {"login": author, "is_bot": author.startswith("app/")},
        "state": state,
    }


def _stamped(pr: dict, identifier: str = CARD) -> dict:
    return {**pr, "body": dependabot_card.stamped_body(identifier, pr["body"])}


def _card(identifier: str, *, labels=(), state="In Review", title="atlas: a card"):
    return {
        "identifier": identifier,
        "title": title,
        "description": f"**Repo:** {SLUG}\n",
        "updatedAt": "2026-01-01T00:00:00.000Z",
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in (f"repo:{SLUG}", *labels)]},
        "comments": {"nodes": []},
        "children": {"nodes": []},
    }


# --------------------------------------------------------------------------- #
# the reader                                                                    #
# --------------------------------------------------------------------------- #

class JoinReaderTest(unittest.TestCase):
    """`card_from_pr(head_ref, author, body)` — the ONE parser every reader
    goes through."""

    def setUp(self):
        _needs_module(self)

    def test_reads_the_marker_on_a_dependabot_pull_request(self):
        body = dependabot_card.stamped_body(CARD, PR_BODY)
        self.assertEqual(dependabot_card.card_from_pr(BRANCH, "dependabot[bot]", body), CARD)

    def test_a_release_note_mentioning_a_card_is_not_a_card(self):
        # atlas #154's body quotes third-party release notes; DRE-42 and the
        # marker-shaped line inside them are a vendor's words, not a join.
        self.assertIsNone(dependabot_card.card_from_pr(BRANCH, "app/dependabot", PR_BODY))

    def test_a_marker_below_the_window_is_not_read(self):
        buried = "\n".join(["line one", "line two", "line three", dependabot_card.marker_line(CARD)])
        self.assertIsNone(dependabot_card.card_from_pr(BRANCH, "app/dependabot", buried))

    def test_a_human_branch_is_never_joined_through_its_body(self):
        # DRE-2027 stays true for every branch a person can name: the body is
        # prose, and "part of DRE-99" transitions nothing.
        body = dependabot_card.stamped_body("DRE-99", "part of DRE-99")
        self.assertIsNone(dependabot_card.card_from_pr("fix/tidy-things", "alice", body))
        self.assertIsNone(dependabot_card.card_from_pr("agent/DRE-100-x", "agent-bureau-bot[bot]", body))

    def test_a_human_authored_dependabot_named_branch_is_not_joined(self):
        body = dependabot_card.stamped_body(CARD, PR_BODY)
        self.assertIsNone(dependabot_card.card_from_pr(BRANCH, "alice", body))

    def test_every_dependabot_login_shape_counts_and_agrees_with_the_sweep(self):
        for login in ("dependabot", "dependabot[bot]", "app/dependabot"):
            self.assertTrue(dependabot_card.is_dependabot_login(login), login)
            self.assertEqual(
                dependabot_card.is_dependabot_pr(BRANCH, login),
                reconcile.is_dependabot_pr({"headRefName": BRANCH, "author": {"login": login}}),
                login,
            )
        self.assertFalse(dependabot_card.is_dependabot_login("dependabot-preview"))

    def test_the_marker_is_case_insensitive_and_upper_cased(self):
        self.assertEqual(dependabot_card.marker_card("**card:** dre-9001 — x"), CARD)

    def test_stamping_is_idempotent(self):
        once = dependabot_card.stamped_body(CARD, PR_BODY)
        twice = dependabot_card.stamped_body(CARD, once)
        self.assertEqual(once, twice)
        self.assertEqual(once.count(dependabot_card.marker_line(CARD)), 1)
        self.assertTrue(once.startswith(dependabot_card.marker_line(CARD)))
        self.assertIn(PR_BODY.splitlines()[0], once)

    def test_restamping_replaces_rather_than_stacks(self):
        body = dependabot_card.stamped_body("DRE-1", dependabot_card.stamped_body(CARD, PR_BODY))
        self.assertEqual(dependabot_card.marker_card(body), "DRE-1")
        self.assertNotIn(CARD, body.splitlines()[0])
        self.assertEqual(body.count("**Card:**"), 2)  # ours, plus the quoted changelog line

    def test_the_cli_answers_from_the_environment(self):
        env = {**os.environ, "HEAD_REF": BRANCH, "PR_AUTHOR": "dependabot[bot]",
               "PR_BODY": dependabot_card.stamped_body(CARD, PR_BODY)}
        out = subprocess.run([sys.executable, str(SCRIPTS / "dependabot_card.py"), "card-from-pr"],
                             capture_output=True, text=True, env=env, check=False)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), CARD)
        env["PR_AUTHOR"] = "alice"
        out = subprocess.run([sys.executable, str(SCRIPTS / "dependabot_card.py"), "card-from-pr"],
                             capture_output=True, text=True, env=env, check=False)
        self.assertEqual((out.returncode, out.stdout), (0, ""))


# --------------------------------------------------------------------------- #
# the card                                                                      #
# --------------------------------------------------------------------------- #

class CardShapeTest(unittest.TestCase):
    """What the card says, and that the live gate accepts it."""

    def setUp(self):
        _needs_module(self)

    def test_title_is_the_repo_prefix_plus_the_pull_request_title(self):
        self.assertEqual(dependabot_card.card_title(SLUG, PR_TITLE), f"{SLUG}: {PR_TITLE}")

    def test_labels_carry_the_repo_the_automation_marker_and_a_role(self):
        labels = dependabot_card.card_labels(SLUG)
        self.assertEqual(labels[0], f"repo:{SLUG}")
        self.assertIn(dependabot_card.LABEL, labels)
        self.assertTrue(any(l.startswith("agent:") for l in labels), labels)
        # `hand-built` means a PERSON does the work and no run is coming; a
        # dependabot pull request is a bot's, reviewed and merged by the
        # pipeline — the card says so with a marker of its own.
        self.assertNotIn(reconcile.HAND_BUILT_LABEL, labels)
        self.assertNotEqual(dependabot_card.LABEL, reconcile.HAND_BUILT_LABEL)

    def test_the_card_passes_the_live_card_gate(self):
        body = dependabot_card.card_body(_pr(), SLUG)
        labels = dependabot_card.card_labels(SLUG)
        self.assertEqual([], validate_card.missing(body, labels))
        self.assertIsNone(validate_card.repo_title_mismatch(
            dependabot_card.card_title(SLUG, PR_TITLE), labels))

    def test_the_body_names_the_pull_request_the_ecosystem_and_the_versions(self):
        body = dependabot_card.card_body(_pr(), SLUG)
        self.assertIn(f"<{PR_URL}>", body)  # delimited: the exact-URL confirm reads it
        self.assertIn("`uv`", body)
        self.assertIn("python-minor-patch-6372e7bff4", body)
        for line in ("Updates `ruff` from 0.16.5 to 0.16.6", "Updates `mcp` from 1.29.1 to 1.30.0"):
            self.assertIn(line, body)
        self.assertNotIn("DRE-42", body)  # the quoted release notes stay out

    def test_the_lane_is_the_review_lane(self):
        self.assertEqual(dependabot_card.LANE, lane_contract.lane("In Review")["name"])
        self.assertEqual(dependabot_card.LANE, reconcile.REVIEW_LANE)

    def test_the_branch_prefix_is_the_gates_prefix(self):
        self.assertEqual(dependabot_card.BRANCH_PREFIX, merge_gate.DEPENDABOT_BRANCH_PREFIX)
        self.assertEqual(dependabot_card.BRANCH_PREFIX, reconcile.DEPENDABOT_BRANCH_PREFIX)


# --------------------------------------------------------------------------- #
# linear-sync: the merge event's reader                                         #
# --------------------------------------------------------------------------- #

def _card_done_step() -> dict:
    doc = yaml.safe_load(LINEAR_SYNC.read_text())
    for step in doc["jobs"]["card-done"]["steps"]:
        if step.get("name") == "Card → Done":
            return step
    raise AssertionError("linear-sync.yml has no 'Card → Done' step")


def _fenced_arm() -> str:
    run = _card_done_step()["run"]
    if FENCE_OPEN not in run or FENCE_CLOSE not in run:
        raise AssertionError(f"linear-sync.yml's Card → Done step has no {FENCE_OPEN!r} block")
    # The opener line carries a trailing note ("— executed verbatim by …");
    # the block starts on the line after it.
    return run.split(FENCE_OPEN, 1)[1].split("\n", 1)[1].split(FENCE_CLOSE, 1)[0]


def _run_arm(*, card: str, head_ref: str, author: str, body: str) -> str:
    """Execute the shipped arm verbatim: `$CARD` as the head-ref line left it,
    the event's head ref / author / body in scope, `.bureau-pipeline` where
    the checkout puts it."""
    with tempfile.TemporaryDirectory() as tmp:
        os.symlink(ROOT, os.path.join(tmp, ".bureau-pipeline"))
        script = "set -euo pipefail\n" + f"CARD={json.dumps(card)}\n" + _fenced_arm() + '\nprintf "%s" "$CARD"\n'
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, cwd=tmp,
            env={**os.environ, "HEAD_REF": head_ref, "PR_AUTHOR": author, "PR_BODY": body},
            check=False,
        )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


class LinearSyncArmTest(unittest.TestCase):
    """The fenced `DRE-3665` arm of the Card → Done step, executed as shipped."""

    def setUp(self):
        _needs_module(self)

    def test_a_stamped_dependabot_merge_resolves_its_card(self):
        body = dependabot_card.stamped_body(CARD, PR_BODY)
        self.assertEqual(_run_arm(card="", head_ref=BRANCH, author="dependabot[bot]", body=body), CARD)

    def test_an_agent_branch_keeps_its_own_card(self):
        # The head-ref line answered already; the arm never overrides it —
        # even when the body carries a marker naming another card.
        body = dependabot_card.stamped_body("DRE-1234", "anything")
        self.assertEqual(
            _run_arm(card="DRE-100", head_ref="agent/DRE-100-x", author="agent-bureau-bot[bot]", body=body),
            "DRE-100",
        )

    def test_a_human_pull_request_body_is_still_prose(self):
        body = dependabot_card.stamped_body("DRE-99", "part of DRE-99")
        self.assertEqual(_run_arm(card="", head_ref="fix/tidy", author="alice", body=body), "")
        self.assertEqual(_run_arm(card="", head_ref=BRANCH, author="alice", body=body), "")

    def test_an_unstamped_dependabot_merge_dones_nothing(self):
        self.assertEqual(_run_arm(card="", head_ref=BRANCH, author="app/dependabot", body=PR_BODY), "")

    def test_the_step_hands_the_arm_the_body_and_the_author_through_env(self):
        env = _card_done_step().get("env") or {}
        self.assertEqual(env.get("PR_BODY"), "${{ github.event.pull_request.body }}")
        self.assertEqual(env.get("PR_AUTHOR"), "${{ github.event.pull_request.user.login }}")
        self.assertIn("dependabot_card.py card-from-pr", _fenced_arm())


# --------------------------------------------------------------------------- #
# the filer: reconcile.card_dependabot_prs                                      #
# --------------------------------------------------------------------------- #

class FakeLinear:
    """The Linear seam, recorded. Every method mirrors the real signature."""

    def __init__(self, *, by_url=None, fail_create=False):
        self.by_url = dict(by_url or {})
        self.fail_create = fail_create
        self.created: list[dict] = []
        self.searched: list[str] = []
        self.dones: list[tuple] = []
        self.states: list[tuple] = []
        self.comments: list[tuple] = []
        self.next_number = 9001

    def create_card(self, title, description, *, repo_slug, labels=(), lane="Planning"):
        if self.fail_create:
            raise reconcile.linear_ops.LinearError("Linear says 500")
        ident = f"DRE-{self.next_number}"
        self.next_number += 1
        self.created.append({"identifier": ident, "title": title, "description": description,
                             "repo_slug": repo_slug, "labels": list(labels), "lane": lane})
        return {"identifier": ident, "url": f"https://linear.app/x/issue/{ident}"}

    def find_by_pr_url(self, url):
        self.searched.append(url)
        return self.by_url.get(url)

    def cmd_card_done(self, identifier, pr_url):
        self.dones.append((identifier, pr_url))

    def cmd_state(self, identifier, state_name, *flags):
        self.states.append((identifier, state_name))

    def cmd_comment(self, identifier, body, *flags):
        self.comments.append((identifier, body))


def _gh_factory(state):
    """subprocess.run stub for exactly the gh calls the filer makes: the pull
    request listing, and `gh pr edit --body-file` for the stamp."""

    def fake_run(argv, **kwargs):
        assert argv[0] == "gh", f"unexpected call: {argv}"
        if argv[1:3] == ["pr", "list"]:
            state["listings"].append(argv)
            if state.get("list_rc"):
                return SimpleNamespace(returncode=1, stdout="", stderr="HTTP 403: rate limited")
            return SimpleNamespace(returncode=0, stdout=json.dumps(state["prs"]), stderr="")
        if argv[1:3] == ["pr", "edit"]:
            body = Path(argv[argv.index("--body-file") + 1]).read_text()
            state["edits"].append({"number": int(argv[3]), "body": body, "argv": argv})
            rc = state.get("edit_rc", 0)
            return SimpleNamespace(returncode=rc, stdout="", stderr="HTTP 403" if rc else "")
        raise AssertionError(f"unexpected gh call: {argv}")

    return fake_run


def _sweep(prs, *, live=(), by_url=None, fail_create=False, list_rc=0, edit_rc=0, slug=SLUG):
    state = {"prs": prs, "edits": [], "listings": [], "list_rc": list_rc, "edit_rc": edit_rc}
    fake = FakeLinear(by_url=by_url, fail_create=fail_create)
    ops = reconcile.linear_ops
    with patch.object(reconcile, "REPO", REPO), \
            patch.object(reconcile, "REPO_SLUG", slug), \
            patch.object(reconcile.subprocess, "run", side_effect=_gh_factory(state)), \
            patch.object(reconcile, "active_cards",
                         side_effect=lambda *a, **k: [_card(i, labels=(dependabot_card.LABEL,)) for i in live]), \
            patch.object(ops, "create_card", side_effect=fake.create_card), \
            patch.object(ops, "find_by_pr_url", side_effect=fake.find_by_pr_url, create=True), \
            patch.object(ops, "cmd_card_done", side_effect=fake.cmd_card_done), \
            patch.object(ops, "cmd_state", side_effect=fake.cmd_state), \
            patch.object(ops, "cmd_comment", side_effect=fake.cmd_comment):
        reconcile.card_dependabot_prs()
    return state, fake


class FilerTest(unittest.TestCase):
    """One card per dependabot pull request, and its lifecycle."""

    def setUp(self):
        _needs_module(self)
        reconcile._write_failures.clear()
        reconcile._read_failures.clear()

    def tearDown(self):
        reconcile._write_failures.clear()
        reconcile._read_failures.clear()

    def test_files_one_card_and_stamps_the_pull_request(self):
        state, fake = _sweep([_pr()])
        self.assertEqual(len(fake.created), 1)
        card = fake.created[0]
        self.assertEqual(card["title"], dependabot_card.card_title(SLUG, PR_TITLE))
        self.assertEqual(card["repo_slug"], SLUG)
        self.assertEqual(card["lane"], "In Review")
        self.assertIn(dependabot_card.LABEL, card["labels"])
        self.assertIn(f"<{PR_URL}>", card["description"])
        self.assertEqual([e["number"] for e in state["edits"]], [154])
        self.assertTrue(state["edits"][0]["body"].startswith(dependabot_card.marker_line("DRE-9001")))
        self.assertIn(PR_BODY.splitlines()[0], state["edits"][0]["body"])
        self.assertIn("--repo", state["edits"][0]["argv"])
        self.assertEqual(reconcile._write_failures, [])
        self.assertEqual(reconcile._read_failures, [])

    def test_the_listing_reads_every_state_for_dependabot_only(self):
        state, _ = _sweep([])
        argv = state["listings"][0]
        self.assertIn("all", argv[argv.index("--state") + 1])
        self.assertIn("author:app/dependabot", " ".join(argv))

    def test_a_second_sweep_files_nothing(self):
        state, fake = _sweep([_stamped(_pr())])
        self.assertEqual((fake.created, fake.searched, state["edits"]), ([], [], []))

    def test_a_wiped_marker_is_restamped_from_linear_not_refiled(self):
        # Dependabot regenerates a grouped pull request's body when the group
        # changes; the card already exists and Linear knows the URL.
        state, fake = _sweep([_pr()], by_url={PR_URL: CARD})
        self.assertEqual(fake.created, [])
        self.assertEqual(fake.searched, [PR_URL])
        self.assertEqual(dependabot_card.marker_card(state["edits"][0]["body"]), CARD)

    def test_a_merged_pull_request_closes_its_live_card(self):
        _, fake = _sweep([_stamped(_pr(state="MERGED"))], live=[CARD])
        self.assertEqual(fake.dones, [(CARD, PR_URL)])
        self.assertEqual((fake.created, fake.states), ([], []))

    def test_a_merged_pull_request_whose_card_is_already_closed_is_left_alone(self):
        state, fake = _sweep([_stamped(_pr(state="MERGED"))], live=[])
        self.assertEqual((fake.dones, fake.states, fake.created, state["edits"]), ([], [], [], []))

    def test_a_closed_unmerged_pull_request_cancels_its_card(self):
        _, fake = _sweep([_stamped(_pr(state="CLOSED"))], live=[CARD])
        self.assertEqual(fake.states, [(CARD, "Canceled")])
        self.assertEqual(fake.dones, [])
        self.assertEqual(len(fake.comments), 1)
        self.assertEqual(fake.comments[0][0], CARD)
        self.assertIn(PR_URL, fake.comments[0][1])

    def test_a_closed_pull_request_with_no_card_gets_none(self):
        state, fake = _sweep([_pr(state="CLOSED"), _pr(number=155, state="MERGED")])
        self.assertEqual((fake.created, fake.searched, fake.states, fake.dones, state["edits"]),
                         ([], [], [], [], []))

    def test_a_reopened_pull_request_keeps_its_card(self):
        # The card is terminal (Canceled) and its pull request is open again:
        # no second card, no edit, and no reopening — a finished card is ground
        # truth and is never reopened by an automated transition (DRE-1877);
        # the merge later moves Canceled → Done.
        state, fake = _sweep([_stamped(_pr(state="OPEN"))], live=[])
        self.assertEqual((fake.created, fake.searched, fake.states, fake.dones, state["edits"]),
                         ([], [], [], [], []))

    def test_a_pull_request_that_already_carries_a_card_is_left_alone(self):
        prs = [
            _pr(number=1, branch="agent/DRE-100-x", author="agent-bureau-bot[bot]",
                body=dependabot_card.stamped_body("DRE-100", "x")),
            _pr(number=2, branch=BRANCH, author="alice"),  # a human's dependabot-named branch
            _pr(number=3, branch="chore/deps", author="alice", body="part of DRE-99"),
        ]
        state, fake = _sweep(prs)
        self.assertEqual((fake.created, fake.searched, state["edits"]), ([], [], []))

    def test_filing_is_paced_per_sweep_oldest_first(self):
        cap = reconcile.DEPENDABOT_CARD_CAP
        prs = [_pr(number=n) for n in range(200, 200 + cap + 2)]
        state, fake = _sweep(list(reversed(prs)))
        self.assertEqual(len(fake.created), cap)
        self.assertEqual([e["number"] for e in state["edits"]], list(range(200, 200 + cap)))

    def test_a_repo_off_the_map_files_nothing_and_stays_green(self):
        # The harness sandbox (dreadnought-foundry/bureau-harness) runs this
        # sweep and is not a fleet repo: a refused create every 15 minutes
        # would be a red run every 15 minutes.
        self.assertNotIn("bureau-harness", validate_card.VALID_SLUGS)
        state, fake = _sweep([_pr()], slug="bureau-harness")
        self.assertEqual((fake.created, fake.searched, state["edits"], state["listings"]), ([], [], [], []))
        self.assertEqual((reconcile._write_failures, reconcile._read_failures), ([], []))

    def test_an_unreadable_listing_files_nothing_and_closes_nothing(self):
        # The same silent read `review_dependabot_prs` makes: an empty answer
        # here means "do nothing", and the next sweep asks again. Nothing is
        # ACTED on off the fabricated emptiness — no card, no edit, no close.
        state, fake = _sweep([_stamped(_pr(state="MERGED"))], live=[CARD], list_rc=1)
        self.assertEqual((fake.created, fake.dones, fake.states, state["edits"]), ([], [], [], []))

    def test_a_failed_create_is_recorded_and_stamps_nothing(self):
        state, fake = _sweep([_pr()], fail_create=True)
        self.assertEqual(state["edits"], [])
        self.assertTrue(any("154" in f for f in reconcile._write_failures), reconcile._write_failures)

    def test_a_failed_stamp_is_recorded_and_the_card_survives(self):
        state, fake = _sweep([_pr()], edit_rc=1)
        self.assertEqual(len(fake.created), 1)
        self.assertTrue(reconcile._write_failures)

    def test_the_backstop_runs_in_the_full_sweep(self):
        src = inspect.getsource(reconcile.main)
        self.assertIn("card_dependabot_prs,", src)


# --------------------------------------------------------------------------- #
# the sweep around the card                                                     #
# --------------------------------------------------------------------------- #

class SweepAroundTheCardTest(unittest.TestCase):
    """An automation card is not the fleet's work in flight: it is out of the
    WIP base, and out of the nudge loop — whose In Review no-PR branch would
    requeue it into Todo after two hours and dispatch an agent onto a
    dependency bump (`pr_for` searches `head:agent/DRE-n`, which a dependabot
    pull request never matches)."""

    def setUp(self):
        _needs_module(self)

    def test_the_predicate_reads_the_label(self):
        self.assertTrue(reconcile.automation_card(_card(CARD, labels=(dependabot_card.LABEL,))))
        self.assertFalse(reconcile.automation_card(_card(CARD)))
        self.assertFalse(reconcile.automation_card(_card(CARD, labels=(reconcile.HAND_BUILT_LABEL,))))

    def test_an_automation_card_is_out_of_the_wip_base_and_the_nudge_loop(self):
        seen = {}
        cards = [_card(CARD, labels=(dependabot_card.LABEL,)), _card("DRE-9002")]
        with patch.object(reconcile, "REPO_SLUG", SLUG), \
                patch.object(reconcile, "active_cards", side_effect=lambda *a, **k: list(cards)), \
                patch.object(reconcile, "merged_card_scope", return_value=None), \
                patch.object(reconcile, "promote_ready",
                             side_effect=lambda **kw: seen.setdefault("active_count", kw["active_count"])), \
                patch.object(reconcile.linear_ops, "open_pass"):
            reconcile.main(promote_only=True)
        self.assertEqual(seen["active_count"], 1)
        # The nudge loop iterates the same list the WIP base counts.
        self.assertIn("for card in mine:", inspect.getsource(reconcile.main))


# --------------------------------------------------------------------------- #
# the record                                                                    #
# --------------------------------------------------------------------------- #

class RecordTest(unittest.TestCase):

    def setUp(self):
        _needs_module(self)

    def test_the_cancel_comment_site_is_declared(self):
        findings = [p for p in check_act_receipts.problems() if "reconcile.py" in str(p)]
        self.assertEqual(findings, [])
        anchors = [d.get("anchor") for d in check_act_receipts.declarations()]
        self.assertTrue(any("cancel_note" in (a or "") for a in anchors), anchors)

    def test_the_sweep_may_write_canceled(self):
        self.assertIn("reconcile.py", lane_contract.lane_writers("Canceled"))
        self.assertIn("reconcile.py", lane_contract.lane_writers("In Review"))

    def test_the_readme_mentions_the_dependabot_case(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("DRE-3665", readme)
        self.assertIn("dependabot", readme.lower())


if __name__ == "__main__":
    unittest.main()
