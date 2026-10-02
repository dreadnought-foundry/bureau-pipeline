"""RED-first: the hygiene agent's first pull-request lane (DRE-5370).

`scripts/hygiene_prs_held.py` reads the open card-branch pull requests the core
hands it and takes the one unsticking action for three holds the pipeline's
own state makes, each with its cause read from the run logs:

  1. a gate `wait` on a fix run that has since finished, with no gate run
     after it — the gate stub is dispatched again;
  2. a `BEHIND` branch whose red required check's workflow file changed on
     the base since the merge base — the branch is refreshed;
  3. a standing `🛑` hold whose named reason no longer holds on the head —
     the gate stub is dispatched again.

A hold only a person can answer gets one `hyg-decision-needed` note carrying
the copy-pasteable decision line, and a `Left` row — never a decision of its
own, which the fix loop would rightly not read.

The fixture is `tests/fixtures/hygiene-prs-held-2026-09-30.json`, in the shape
every hygiene lane fixture takes: `lanes`, `prs` and a `gh` map from an argv
joined with single spaces to the stdout the fake `ctx.gh` answers.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hygiene_prs_held.py -v
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import fix_context  # noqa: E402
import hygiene  # noqa: E402
import stranded_fix  # noqa: E402

MODULE_PATH = ROOT / "scripts" / "hygiene_prs_held.py"
FIXTURE = ROOT / "tests" / "fixtures" / "hygiene-prs-held-2026-09-30.json"

#: 21:05 UTC on 2026-09-30 is 14:05 in Pacific Daylight Time.
NOW = datetime(2026, 9, 30, 21, 5, tzinfo=UTC)
HOME = "dreadnought-foundry"
BP = "dreadnought-foundry/bureau-pipeline"
AB = "dreadnought-foundry/agent-bureau"
PO = "dreadnought-foundry/portico"
QA = "agent-bureau-qa-bot"
WORKER = "agent-bureau-bot"


def load_lane():
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("hygiene_prs_held", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lane = load_lane()


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FixtureGh:
    """The read-only `gh` a lane sees, answering ONLY from the fixture's `gh`
    map — a read the map does not hold fails the test. `gh pr list` is the
    core's read, not the lane's, and is answered from the fixture's `prs`."""

    def __init__(self, doc: dict):
        self.answers = dict(doc["gh"])
        self.prs = doc["prs"]
        self.calls: list = []

    def __call__(self, argv):
        argv = [str(a) for a in argv]
        refusal = hygiene.read_gh_refusal(argv)
        assert refusal is None, f"the lane asked ctx.gh for a write: {refusal}"
        if argv[:3] == ["gh", "pr", "list"]:
            return json.dumps(self.prs.get(argv[argv.index("--repo") + 1], []))
        self.calls.append(" ".join(argv))
        key = " ".join(argv)
        if key not in self.answers:
            raise AssertionError(f"the fixture's gh map has no answer for {key!r}")
        return self.answers[key]


def context(doc, *, dry_run=False):
    gh = FixtureGh(doc)
    ctx = hygiene.make_context(HOME, gh=gh, linear=lambda q, v=None: {},
                               dry_run=dry_run, now=NOW, summary_card="DRE-900")
    return ctx, gh


def plan(doc=None):
    doc = doc if doc is not None else fixture()
    ctx, gh = context(doc)
    board = hygiene.Board(lanes=doc["lanes"], prs=doc["prs"])
    assert lane is not None, f"{MODULE_PATH.name} does not exist yet"
    return lane.plan(board, ctx), ctx, gh


def actions(items, target=None):
    return [i for i in items if isinstance(i, hygiene.Action)
            and (target is None or i.target == target)]


def lefts(items, target=None):
    return [i for i in items if isinstance(i, hygiene.Left)
            and (target is None or i.target == target)]


def pull(doc, repo, number):
    return next(p for p in doc["prs"][repo] if p["number"] == number)


def head7(doc, repo, number):
    return pull(doc, repo, number)["headRefOid"][:7]


def kinds(action):
    return [w.kind for w in action.writes]


def comment_of(action):
    return next(w for w in action.writes if w.kind == "gh_pr_comment").body


# --------------------------------------------------------------------------- #
# the lane's shape                                                             #
# --------------------------------------------------------------------------- #


class TestTheLane:
    def test_its_heading_is_pull_requests(self):
        assert lane.LANE == "Pull requests"

    def test_the_core_discovers_it_by_the_glob(self):
        names = [m.__name__ for m in hygiene.discover()]
        assert "hygiene_prs_held" in names

    def test_every_row_it_returns_takes_its_heading(self):
        items, _ctx, _gh = plan()
        assert items
        assert {i.lane for i in items} == {"Pull requests"}

    def test_a_branch_that_is_not_a_cards_is_never_read(self):
        items, _ctx, gh = plan()
        assert not actions(items, f"{BP}#603") and not lefts(items, f"{BP}#603")
        assert not any(" 603 " in c or "/603/" in c for c in gh.calls)

    def test_another_owners_repo_is_never_read(self):
        doc = fixture()
        doc["prs"]["EveryBite/atlas"] = [copy.deepcopy(pull(doc, BP, 601))]
        items, _ctx, gh = plan(doc)
        assert not any(i.target.startswith("EveryBite/") for i in items)
        assert not any("EveryBite/" in c for c in gh.calls)


# --------------------------------------------------------------------------- #
# (1) a wait on a fix run that has finished                                    #
# --------------------------------------------------------------------------- #


class TestWaitOnAFinishedFixRun:
    @pytest.mark.parametrize("repo, number, stub, fix_run, conclusion", [
        (BP, 601, "self-merge-gate.yml", 37010000001, "success"),
        (AB, 2701, "merge-gate.yml", 37020000001, "failure"),
    ])
    def test_the_gate_stub_is_dispatched_again_with_one_receipt(
            self, repo, number, stub, fix_run, conclusion):
        doc = fixture()
        items, _ctx, _gh = plan(doc)
        found = actions(items, f"{repo}#{number}")
        assert len(found) == 1
        action = found[0]
        assert action.act == "hygiene-gate-redispatch"
        assert kinds(action) == ["gh_dispatch", "gh_pr_comment"]
        dispatch = action.writes[0]
        assert dispatch.argv == ("gh", "workflow", "run", stub, "--repo", repo,
                                 "-f", f"pr_number={number}")
        sha7 = head7(doc, repo, number)
        assert action.cause == f"wait on fix run {fix_run} ({conclusion}) at head {sha7}"
        first = comment_of(action).splitlines()[0]
        assert first.startswith("🧹 hygiene: hyg-gate-redispatched — ")
        assert f"fix run {fix_run} ({conclusion})" in first and sha7 in first
        assert not lefts(items, f"{repo}#{number}")

    def test_the_cores_guard_admits_the_dispatch(self):
        items, ctx, _gh = plan()
        for target in (f"{BP}#601", f"{AB}#2701"):
            for write in actions(items, target)[0].writes:
                hygiene.guard(write, ctx)  # raises Forbidden on a refusal

    def test_the_reason_it_reads_is_the_gates_own_wording(self):
        reason = stranded_fix.lane_refusal(
            stranded_fix.Lane(by_pr={812: 37099999999}), 812)
        assert lane.fix_run_waited_on(reason, 812) == 37099999999
        assert lane.fix_run_waited_on(reason, 813) is None
        assert lane.fix_run_waited_on("no critic verdict yet — wait", 812) is None

    def test_a_gate_run_after_the_fix_run_finished_means_the_gate_already_looked(self):
        doc = fixture()
        key = next(k for k in doc["gh"] if k.startswith(f"gh run list --repo {BP} --workflow self-merge-gate.yml"))
        runs = json.loads(doc["gh"][key])
        runs.insert(0, {"databaseId": 37010000200, "status": "in_progress", "conclusion": "",
                        "createdAt": "2026-09-30T19:43:00Z"})
        doc["gh"][key] = json.dumps(runs)
        items, _ctx, _gh = plan(doc)
        assert actions(items, f"{BP}#601") == []

    def test_a_fix_run_still_in_flight_is_waited_on(self):
        doc = fixture()
        key = f"gh run view 37010000001 --repo {BP} --json status,conclusion,updatedAt"
        doc["gh"][key] = json.dumps({"status": "in_progress", "conclusion": "",
                                     "updatedAt": "2026-09-30T19:41:37Z"})
        items, _ctx, _gh = plan(doc)
        assert actions(items, f"{BP}#601") == []

    def test_a_gate_that_decided_anything_but_a_fix_wait_is_left_alone(self):
        doc = fixture()
        key = f"gh run view 37010000100 --repo {BP} --log"
        doc["gh"][key] = doc["gh"][key].replace("decision=wait", "decision=hold").replace(
            "reason=Agent Fix run", "reason=latest verdict is not APPROVE — holding. Agent Fix run")
        items, _ctx, _gh = plan(doc)
        assert actions(items, f"{BP}#601") == []

    def test_no_gate_run_at_all_is_no_action(self):
        items, _ctx, _gh = plan()
        assert actions(items, f"{PO}#640") == []


# --------------------------------------------------------------------------- #
# (2) behind a changed workflow file                                           #
# --------------------------------------------------------------------------- #


class TestBehindAChangedLimit:
    def test_the_branch_is_refreshed_at_its_own_head(self):
        doc = fixture()
        items, _ctx, _gh = plan(doc)
        found = actions(items, f"{BP}#602")
        assert len(found) == 1
        action = found[0]
        assert action.act == "hygiene-branch-refresh"
        assert kinds(action) == ["gh_update_branch", "gh_pr_comment"]
        head = pull(doc, BP, 602)["headRefOid"]
        assert action.writes[0].argv == (
            "gh", "api", "-X", "PUT", f"repos/{BP}/pulls/602/update-branch",
            "-f", f"expected_head_sha={head}")
        assert action.cause == (f"behind base, .github/workflows/tests.yml changed "
                                f"since the merge base, head {head[:7]}")
        assert comment_of(action).startswith("🧹 hygiene: hyg-branch-refreshed — ")
        assert "check scripts unit tests" in action.evidence
        assert "file .github/workflows/tests.yml" in action.evidence

    def test_the_guard_admits_the_refresh(self):
        items, ctx, _gh = plan()
        for write in actions(items, f"{BP}#602")[0].writes:
            hygiene.guard(write, ctx)

    def test_a_behind_branch_whose_checks_are_all_green_is_not_touched(self):
        items, _ctx, gh = plan()
        assert actions(items, f"{PO}#640") == []
        assert not lefts(items, f"{PO}#640")
        assert not any(c.startswith("gh pr checks 640") for c in gh.calls)

    def test_a_red_check_whose_workflow_file_did_not_change_is_not_a_refresh(self):
        doc = fixture()
        key = f"gh api repos/{BP}/compare/{pull(doc, BP, 602)['headRefOid']}...main"
        body = json.loads(doc["gh"][key])
        body["files"] = [f for f in body["files"] if f["filename"] != ".github/workflows/tests.yml"]
        doc["gh"][key] = json.dumps(body)
        # With no refresh to make, the lane goes on to ask the gate.
        doc["gh"][f"gh run list --repo {BP} --workflow self-merge-gate.yml --branch "
                  "agent/DRE-4102-behind-limit --json databaseId,status,conclusion,createdAt "
                  "--limit 20"] = "[]"
        items, _ctx, _gh = plan(doc)
        assert actions(items, f"{BP}#602") == []

    def test_a_branch_that_is_not_behind_is_not_refreshed(self):
        doc = fixture()
        pull(doc, BP, 602)["mergeStateStatus"] = "BLOCKED"
        doc["gh"][f"gh run list --repo {BP} --workflow self-merge-gate.yml --branch "
                  "agent/DRE-4102-behind-limit --json databaseId,status,conclusion,createdAt "
                  "--limit 20"] = "[]"
        items, _ctx, _gh = plan(doc)
        assert actions(items, f"{BP}#602") == []


# --------------------------------------------------------------------------- #
# (3) a hold whose reason cleared                                              #
# --------------------------------------------------------------------------- #


def rest(cid, login, kind, body, at):
    return {"id": cid, "user": {"login": login, "type": kind}, "body": body,
            "created_at": at}


def thread_key(repo, number):
    return (f"gh api --paginate --slurp "
            f"repos/{repo}/issues/{number}/comments?per_page=100")


def set_thread(doc, repo, number, comments):
    doc["gh"][thread_key(repo, number)] = json.dumps([comments])


class TestAHoldWhoseReasonCleared:
    def test_a_hold_whose_named_check_is_green_redispatches_the_gate(self):
        doc = fixture()
        items, ctx, _gh = plan(doc)
        found = actions(items, f"{AB}#2702")
        assert len(found) == 1
        action = found[0]
        assert action.act == "hygiene-gate-redispatch"
        assert kinds(action) == ["gh_dispatch", "gh_pr_comment"]
        assert action.writes[0].argv[3] == "merge-gate.yml"
        sha7 = head7(doc, AB, 2702)
        assert action.cause == (f"hold reason cleared, check TDD commit discipline "
                                f"green at head {sha7}")
        assert "check TDD commit discipline" in action.evidence
        assert "fix run 37040000300" in action.evidence
        for write in action.writes:
            hygiene.guard(write, ctx)

    def test_a_hold_with_an_operator_decision_after_it_is_the_sweeps(self):
        items, _ctx, gh = plan()
        assert actions(items, f"{AB}#2703") == []
        assert lefts(items, f"{AB}#2703") == []
        # Nothing past the thread is read: the sweep owns that restart.
        assert [c for c in gh.calls if "2703" in c] == [thread_key(AB, 2703)]

    def test_a_named_check_still_red_is_not_cleared(self):
        doc = fixture()
        for c in pull(doc, AB, 2702)["statusCheckRollup"]:
            if c["name"] == "TDD commit discipline":
                c["conclusion"] = "FAILURE"
        items, _ctx, _gh = plan(doc)
        assert actions(items, f"{AB}#2702") == []

    def _verdict_hold(self, doc, verdict_sha):
        head = pull(doc, AB, 2702)["headRefOid"]
        hold = ("🛑 Fix attempt 2 blocked: the reviewer's finding about the retry "
                "wording is disputed.\n\n" + fix_context.ANSWER_FORMAT)
        set_thread(doc, AB, 2702, [
            rest(6101, f"{QA}[bot]", "Bot",
                 f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{'0' * 40}", "2026-09-30T15:00:00Z"),
            rest(6102, f"{WORKER}[bot]", "Bot", hold, "2026-09-30T16:05:00Z"),
            rest(6103, f"{QA}[bot]", "Bot",
                 f"🔎 QA Critic — VERDICT: APPROVE @{verdict_sha}", "2026-09-30T18:00:00Z"),
        ])
        return head

    def test_the_critics_approve_on_the_head_clears_a_verdict_hold(self):
        doc = fixture()
        head = self._verdict_hold(doc, pull(doc, AB, 2702)["headRefOid"])
        items, _ctx, _gh = plan(doc)
        found = actions(items, f"{AB}#2702")
        assert len(found) == 1
        assert found[0].cause == f"hold reason cleared, critic APPROVE at head {head[:7]}"

    def test_an_approve_bound_to_another_head_does_not(self):
        doc = fixture()
        self._verdict_hold(doc, "1" * 40)
        items, _ctx, _gh = plan(doc)
        assert actions(items, f"{AB}#2702") == []

    def test_a_worker_comment_after_the_hold_means_the_loop_moved_on(self):
        doc = fixture()
        comments = json.loads(doc["gh"][thread_key(AB, 2702)])[0]
        comments.append(rest(5199, f"{WORKER}[bot]", "Bot",
                             "🤖 Fix attempt 3 pushed — CI and critic review re-running",
                             "2026-09-30T17:00:00Z"))
        set_thread(doc, AB, 2702, comments)
        doc["gh"][f"gh run list --repo {AB} --workflow merge-gate.yml --branch "
                  "agent/DRE-4202-hold-check-green --json databaseId,status,conclusion,"
                  "createdAt --limit 20"] = "[]"
        items, _ctx, _gh = plan(doc)
        assert actions(items, f"{AB}#2702") == []


# --------------------------------------------------------------------------- #
# a hold only a person can answer                                              #
# --------------------------------------------------------------------------- #


class TestAHoldThatNeedsAPerson:
    def test_one_note_and_one_left_row_and_no_state_write(self):
        doc = fixture()
        items, ctx, _gh = plan(doc)
        found = actions(items, f"{PO}#641")
        assert len(found) == 1
        action = found[0]
        assert action.act == "hygiene-decision-needed"
        assert kinds(action) == ["gh_pr_comment"]
        body = comment_of(action)
        assert body.startswith("🧹 hygiene: hyg-decision-needed — ")
        assert "**Operator decision** — " in body
        sha7 = head7(doc, PO, 641)
        assert action.cause == (f"hold needs a person, a criterion that cannot be met "
                                f"before merge at head {sha7}")
        left = lefts(items, f"{PO}#641")
        assert len(left) == 1
        assert left[0].recommendation.strip()
        for write in action.writes:
            hygiene.guard(write, ctx)

    def test_the_note_is_not_a_decision_the_fix_loop_would_read(self):
        doc = fixture()
        items, _ctx, _gh = plan(doc)
        body = comment_of(actions(items, f"{PO}#641")[0])
        thread = json.loads(doc["gh"][thread_key(PO, 641)])[0]
        # Posted by the App's bot identity, it decides nothing.
        thread.append(rest(9999, "agent-bureau-bot[bot]", "Bot", body, "2026-09-30T21:05:00Z"))
        assert fix_context.operator_decision(thread, "agent-bureau-bot[bot]") is None
        assert not fix_context.is_decision_body(body)

    def test_a_second_pass_with_the_note_present_leaves_the_row_and_posts_nothing(
            self, monkeypatch):
        doc = fixture()
        sent, ledger = run(doc, monkeypatch)
        note = [w for w in sent if w.kind == "gh_pr_comment" and w.target == f"{PO}#641"]
        assert len(note) == 1
        add_receipts(doc, sent)
        sent2, ledger2 = run(doc, monkeypatch)
        assert [w for w in sent2 if w.target == f"{PO}#641"] == []
        assert [r for r in ledger2["left"] if r["target"] == f"{PO}#641"] == \
            [r for r in ledger["left"] if r["target"] == f"{PO}#641"]

    def test_the_note_on_the_thread_does_not_unseat_the_hold(self):
        doc = fixture()
        items, _ctx, _gh = plan(doc)
        body = comment_of(actions(items, f"{PO}#641")[0])
        thread = json.loads(doc["gh"][thread_key(PO, 641)])[0]
        thread.append(rest(9999, "agent-bureau-bot[bot]", "Bot", body, "2026-09-30T21:05:00Z"))
        set_thread(doc, PO, 641, thread)
        items, _ctx, _gh = plan(doc)
        assert len(lefts(items, f"{PO}#641")) == 1
        assert len(actions(items, f"{PO}#641")) == 1

    def test_a_business_choice_reads_as_one(self):
        doc = fixture()
        thread = json.loads(doc["gh"][thread_key(PO, 641)])[0]
        thread[1]["body"] = ("🛑 Fix attempt 1 blocked: whether the free tier keeps the "
                             "export is a business choice, not a defect.\n\n"
                             + fix_context.ANSWER_FORMAT)
        set_thread(doc, PO, 641, thread)
        items, _ctx, _gh = plan(doc)
        assert actions(items, f"{PO}#641")[0].cause == (
            f"hold needs a person, a business choice at head {head7(doc, PO, 641)}")

    def test_a_hold_that_names_neither_is_left_to_the_loop(self):
        doc = fixture()
        thread = json.loads(doc["gh"][thread_key(PO, 641)])[0]
        thread[1]["body"] = ("🛑 Fix attempt 1 blocked: the finding is disputed.\n\n"
                             + fix_context.ANSWER_FORMAT)
        set_thread(doc, PO, 641, thread)
        items, _ctx, _gh = plan(doc)
        assert actions(items, f"{PO}#641") == [] and lefts(items, f"{PO}#641") == []


# --------------------------------------------------------------------------- #
# idempotency, through the core's key                                         #
# --------------------------------------------------------------------------- #


def run(doc, monkeypatch):
    """One leg through the core, this lane alone, the write seam recorded."""
    sent: list = []
    monkeypatch.setattr(hygiene, "send", lambda write, ctx: sent.append(write))
    monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [lane])
    ctx, _gh = context(doc)
    board_doc = {"taken_at": "2026-09-30T21:00:00Z", "lanes": doc["lanes"]}
    return sent, hygiene.run_leg(board_doc, ctx)


def add_receipts(doc, sent):
    """Every receipt the pass posted, now on its pull request's comments."""
    for write in sent:
        if write.kind != "gh_pr_comment":
            continue
        repo, _, number = write.target.rpartition("#")
        pull(doc, repo, int(number))["comments"].append(
            {"author": {"login": "agent-bureau-bot"}, "body": write.body,
             "createdAt": "2026-09-30T21:05:00Z"})


def advance_heads(doc) -> dict:
    """The same fixture after one push to every pull request: every head sha,
    wherever the fixture spells it, is a new one."""
    text = json.dumps(doc)
    for repo_prs in doc["prs"].values():
        for p in repo_prs:
            old = p["headRefOid"]
            text = text.replace(old, old[::-1])
    return json.loads(text)


EXPECTED = {f"{BP}#601": "hygiene-gate-redispatch", f"{AB}#2701": "hygiene-gate-redispatch",
            f"{BP}#602": "hygiene-branch-refresh", f"{AB}#2702": "hygiene-gate-redispatch",
            f"{PO}#641": "hygiene-decision-needed"}


class TestOneActionPerHead:
    def test_the_fixture_yields_exactly_the_five_actions(self):
        items, _ctx, _gh = plan()
        assert {a.target: a.act for a in actions(items)} == EXPECTED
        assert len(actions(items)) == len(EXPECTED)
        assert [r.target for r in lefts(items)] == [f"{PO}#641"]

    def test_a_repeated_pass_over_an_unchanged_fixture_sends_nothing(self, monkeypatch):
        doc = fixture()
        sent, ledger = run(doc, monkeypatch)
        assert {a["target"] for a in ledger["actions"] if a["outcome"] == "executed"} == set(EXPECTED)
        add_receipts(doc, sent)
        sent2, ledger2 = run(doc, monkeypatch)
        assert sent2 == []
        assert {a["outcome"] for a in ledger2["actions"]} == {"suppressed"}

    def test_every_head_advanced_by_one_push_yields_each_action_again(self, monkeypatch):
        doc = fixture()
        sent, _ledger = run(doc, monkeypatch)
        receipts = [w for w in sent if w.kind == "gh_pr_comment"]
        moved = advance_heads(doc)
        add_receipts(moved, receipts)  # the old heads' receipts stand on the thread
        sent2, ledger2 = run(moved, monkeypatch)
        executed = {a["target"]: a["act"] for a in ledger2["actions"] if a["outcome"] == "executed"}
        assert executed == EXPECTED
        assert len(sent2) == len(sent)


# --------------------------------------------------------------------------- #
# causes, evidence and reads                                                   #
# --------------------------------------------------------------------------- #

_CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b|\bPT\b|\bUTC\b")
_COUNT = re.compile(r"\b\d+\s+(?:time|times|attempt|attempts|round|rounds|minute|minutes|"
                    r"hour|hours|run\(s\)|check\(s\)|comment|comments|pull requests)\b", re.I)
_EVIDENCE = re.compile(r"^(?:gate run \d+|fix run \d+|run \d+|check .+|file .+)$")


class TestCausesEvidenceAndReads:
    def test_every_cause_names_its_head_and_no_count_or_clock(self):
        doc = fixture()
        items, _ctx, _gh = plan(doc)
        for action in actions(items):
            repo, _, number = action.target.rpartition("#")
            assert action.cause.endswith(f"head {head7(doc, repo, int(number))}"), action.cause
            assert not _CLOCK.search(action.cause), action.cause
            assert not _COUNT.search(action.cause), action.cause

    def test_every_action_names_a_run_a_check_or_a_file(self):
        items, _ctx, _gh = plan()
        for action in actions(items):
            assert any(_EVIDENCE.match(e) for e in action.evidence), action.evidence

    def test_every_read_is_answered_from_the_fixture_and_counted(self):
        doc = fixture()
        _items, _ctx, gh = plan(doc)
        # 3 + 3 for the two gate waits, 3 for the refresh, 1 for the green
        # BEHIND branch's gate list, 2 + 1 + 2 for the three holds.
        assert len(gh.calls) == 15
        assert len(set(gh.calls)) == 15
        assert set(gh.calls) == set(doc["gh"])


# --------------------------------------------------------------------------- #
# what it may write                                                            #
# --------------------------------------------------------------------------- #

ALLOWED = {"gh_dispatch", "gh_update_branch", "gh_pr_comment"}
WRITE_CONSTRUCTORS = {"linear_state", "linear_comment", "linear_label", "linear_relation",
                      "gh_dispatch", "gh_rerun", "gh_update_branch", "gh_pr_close",
                      "gh_pr_comment"}


class TestWhatItMayWrite:
    def test_only_three_write_kinds_are_returned(self):
        items, _ctx, _gh = plan()
        assert {w.kind for a in actions(items) for w in a.writes} <= ALLOWED

    def test_the_module_calls_no_other_constructor(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        used = {n.attr for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id == "hygiene" and n.attr in WRITE_CONSTRUCTORS}
        assert used <= ALLOWED
        assert used

    def test_every_dispatch_names_the_repos_gate_stub(self):
        items, _ctx, _gh = plan()
        dispatches = [w for a in actions(items) for w in a.writes if w.kind == "gh_dispatch"]
        assert len(dispatches) == 3
        for w in dispatches:
            assert w.workflow == hygiene.gate_stub(w.repo)

    def test_the_cores_static_scan_passes_over_it(self):
        spec = importlib.util.spec_from_file_location(
            "test_hygiene_core", ROOT / "tests" / "test_hygiene.py")
        core_tests = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core_tests)
        assert MODULE_PATH.exists()
        assert core_tests.scan(MODULE_PATH) == []


class TestAReadThatFails:
    def test_a_failed_read_skips_that_pull_request_and_no_other(self, capsys):
        doc = fixture()
        del doc["gh"][f"gh run view 37010000100 --repo {BP} --log"]
        ctx, gh = context(doc)

        def failing(argv):
            if " ".join(argv) == f"gh run view 37010000100 --repo {BP} --log":
                raise RuntimeError("gh exited 1: HTTP 502")
            return gh(argv)

        ctx.gh = failing
        items = lane.plan(hygiene.Board(lanes=doc["lanes"], prs=doc["prs"]), ctx)
        assert actions(items, f"{BP}#601") == []
        assert {a.target for a in actions(items)} == set(EXPECTED) - {f"{BP}#601"}
        assert f"{BP}#601 skipped" in capsys.readouterr().err

    def test_a_read_the_core_refuses_is_never_swallowed(self):
        doc = fixture()
        ctx, _gh = context(doc)

        def refusing(argv):
            raise hygiene.Forbidden("ctx.gh is read-only")

        ctx.gh = refusing
        with pytest.raises(hygiene.Forbidden):
            lane.plan(hygiene.Board(lanes=doc["lanes"], prs=doc["prs"]), ctx)
