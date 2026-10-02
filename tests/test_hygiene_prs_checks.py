"""RED-first: the hygiene agent's second pull-request lane (DRE-5371).

`scripts/hygiene_prs_checks.py` reads the open pull requests the core hands
every lane and takes two actions, never a third:

  1. a check PROVEN flaky is re-run once — red on the head, and either green
     for the same check on the same head sha in another run, or its failed log
     carries a transient signature `medic_classify` already knows. A real red
     test is left alone, and a head that already spent its rerun gets a `Left`
     row instead of a second one (DRE-1921: a retry at a vendor boundary is
     bounded).
  2. a moot pull request is closed with the evidence — a newer pull request
     for the same card has merged, or the card is `Canceled` / `Duplicate`.
     A `Done` card with no other merged pull request is LEFT for a person:
     closing it could discard shipped work.

The fixture is the board of 2026-09-30, scrubbed, in the contract's shape:
`{"lanes": ..., "prs": ..., "gh": ...}`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hygiene_prs_checks.py -v
"""
from __future__ import annotations

import copy
import json
import os
import sys
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import hygiene  # noqa: E402
import hygiene_prs_checks as lane  # noqa: E402
import linear_ops  # noqa: E402
import medic_classify  # noqa: E402
from test_hygiene import scan  # noqa: E402 — the core's static scan, not a copy

FIXTURE = ROOT / "tests" / "fixtures" / "hygiene-prs-checks-2026-09-30.json"

#: 21:05 UTC on 2026-09-30 is 14:05 in Pacific Daylight Time.
NOW = datetime(2026, 9, 30, 21, 5, tzinfo=UTC)
HOME = "dreadnought-foundry"
PORTICO = "dreadnought-foundry/portico"
PIPELINE = "dreadnought-foundry/bureau-pipeline"

#: The cards the board read does not carry — `Done`, `Canceled` and
#: `Duplicate` are not among the lanes it reads — answered by the lane's own
#: `ctx.linear` read.
OFF_BOARD = {"DRE-4106": "Canceled", "DRE-4107": "Done"}

REAL = "web-tests\tRun tests\t2026-09-30T18:05:40Z FAILED tests/test_queue.py - AssertionError\n"
UPSTREAM = ("api-tests\tRun tests\t2026-09-30T18:04:12Z gh: No server is currently "
            "available to service your request. (HTTP 503)\n")
RATE = ("api-tests\tRun tests\t2026-09-30T18:04:12Z linear: POST https://api.linear.app/graphql "
        "rate limited: 2500 requests/hour exhausted\n")


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FakeGh:
    """`ctx.gh`, answering from the fixture's `gh` map, every argv first
    put through the core's read-only wrapper — so a lane that asked for a
    write would raise here exactly as it would in the run."""

    def __init__(self, answers):
        self.answers = dict(answers)
        self.calls: list[list[str]] = []
        self._read = hygiene.read_only_gh(self._answer)

    def _answer(self, argv):
        key = " ".join(argv)
        if key not in self.answers:
            raise AssertionError(f"the fixture has no answer for {key!r}")
        answer = self.answers[key]
        if isinstance(answer, BaseException):
            raise answer  # a read that failed the way `gh` fails in the run
        return answer

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self._read(argv)


class FakeLinear:
    def __init__(self, states):
        self.states = dict(states)
        self.calls: list = []

    def __call__(self, query, variables=None):
        assert not query.lstrip().startswith("mutation")
        self.calls.append(dict(variables or {}))
        ident = (variables or {}).get("id")
        state = self.states.get(ident)
        if isinstance(state, BaseException):
            raise state  # a read that failed the way `linear_ops.gql` fails in the run
        if state is None:
            return {"issue": None}
        return {"issue": {"identifier": ident, "state": {"name": state}}}


def build(doc=None, *, states=OFF_BOARD):
    doc = doc if doc is not None else fixture()
    gh = FakeGh(doc["gh"])
    linear = FakeLinear(states)
    ctx = hygiene.make_context(HOME, gh=gh, linear=linear, dry_run=False, now=NOW,
                               summary_card="DRE-900")
    board = hygiene.Board(lanes=doc["lanes"], prs=doc["prs"])
    return board, ctx, gh, linear


def run(doc=None, **kw):
    board, ctx, gh, linear = build(doc, **kw)
    items = lane.plan(board, ctx)
    return items, board, ctx, gh, linear


def actions(items, target=None):
    return [i for i in items if isinstance(i, hygiene.Action)
            and (target is None or i.target == target)]


def left(items, target=None):
    return [i for i in items if isinstance(i, hygiene.Left)
            and (target is None or i.target == target)]


def pull(doc, repo, number):
    return next(p for p in doc["prs"][repo] if p["number"] == number)


def sha7(doc, repo, number):
    return pull(doc, repo, number)["headRefOid"][:7]


# --------------------------------------------------------------------------- #
# the module's shape                                                           #
# --------------------------------------------------------------------------- #


class TestTheLane:
    def test_it_is_the_pull_requests_lane(self):
        assert lane.LANE == "Pull requests"

    def test_the_core_discovers_it_by_the_glob(self):
        names = [m.__name__ for m in hygiene.discover()]
        assert "hygiene_prs_checks" in names

    def test_the_fixture_is_the_contracts_shape(self):
        assert set(fixture()) == {"lanes", "prs", "gh"}


# --------------------------------------------------------------------------- #
# (1) a check proven flaky                                                     #
# --------------------------------------------------------------------------- #


class TestAFlakyCheck:
    def test_red_here_and_green_in_another_run_on_the_same_head_is_rerun_once(self):
        doc = fixture()
        items, *_ = run(doc)
        [action] = actions(items, f"{PORTICO}#101")
        head = sha7(doc, PORTICO, 101)
        assert action.act == "hygiene-check-rerun"
        assert action.lane == "Pull requests"
        assert action.cause == f"web-tests red in run 91001, green in run 91002 at head {head}"
        reruns = [w for w in action.writes if w.kind == "gh_rerun"]
        assert [w.argv for w in reruns] == [
            ("gh", "run", "rerun", "91001", "--failed", "--repo", PORTICO)]
        [comment] = [w for w in action.writes if w.kind == "gh_pr_comment"]
        assert comment.argv[:6] == ("gh", "pr", "comment", "101", "--repo", PORTICO)
        first = comment.body.splitlines()[0]
        assert first.startswith("🧹 hygiene: hyg-check-rerun —")
        for named in ("web-tests", "91001", "91002", head):
            assert named in first
        assert "91001" in " ".join(action.evidence) and "91002" in " ".join(action.evidence)

    def test_the_receipt_is_the_cores_composition(self):
        items, *_ = run()
        [action] = actions(items, f"{PORTICO}#101")
        [comment] = [w for w in action.writes if w.kind == "gh_pr_comment"]
        assert comment.body == hygiene.receipt(action.act, action.cause, action.evidence, NOW)

    def test_a_green_run_on_another_head_proves_nothing(self):
        doc = fixture()
        rows = json.loads(doc["gh"][_runs(PORTICO, "agent/DRE-4101-search-tables")])
        for r in rows:
            if r["databaseId"] == 91002:
                r["headSha"] = "f" * 40
        doc["gh"][_runs(PORTICO, "agent/DRE-4101-search-tables")] = json.dumps(rows)
        doc["gh"][_log(PORTICO, 91001)] = REAL
        items, *_ = run(doc)
        assert actions(items, f"{PORTICO}#101") == []

    def test_a_red_check_on_an_upstream_5xx_is_rerun_naming_the_signature(self):
        doc = fixture()
        assert medic_classify.is_upstream_5xx(doc["gh"][_log(PORTICO, 91011)])
        items, *_ = run(doc)
        [action] = actions(items, f"{PORTICO}#102")
        head = sha7(doc, PORTICO, 102)
        assert action.act == "hygiene-check-rerun"
        assert action.cause == f"api-tests red in run 91011, upstream_5xx at head {head}"
        assert [w.argv for w in action.writes if w.kind == "gh_rerun"] == [
            ("gh", "run", "rerun", "91011", "--failed", "--repo", PORTICO)]
        [comment] = [w for w in action.writes if w.kind == "gh_pr_comment"]
        assert comment.body.startswith("🧹 hygiene: hyg-check-rerun — ")
        assert "upstream_5xx" in comment.body.splitlines()[0]

    def test_a_red_check_on_a_linear_rate_limit_is_rerun_naming_the_signature(self):
        doc = fixture()
        doc["gh"][_log(PORTICO, 91011)] = RATE
        assert medic_classify.is_linear_rate_limited(RATE)
        items, *_ = run(doc)
        [action] = actions(items, f"{PORTICO}#102")
        assert action.cause.endswith(f"linear_ratelimited at head {sha7(doc, PORTICO, 102)}")

    def test_a_real_red_test_gets_no_action_and_no_comment(self):
        doc = fixture()
        assert not medic_classify.is_upstream_5xx(doc["gh"][_log(PORTICO, 91021)])
        items, *_ = run(doc)
        assert actions(items, f"{PORTICO}#103") == []
        assert left(items, f"{PORTICO}#103") == []

    def test_the_same_pull_request_with_its_log_upstream_is_rerun(self):
        """The negative above is not vacuous: change only the log and it acts."""
        doc = fixture()
        doc["gh"][_log(PORTICO, 91021)] = UPSTREAM
        items, *_ = run(doc)
        assert len(actions(items, f"{PORTICO}#103")) == 1

    def test_a_green_pull_request_reads_no_run(self):
        _, _, _, gh, _ = run()
        listed = {c[c.index("--branch") + 1] for c in gh.calls if c[:3] == ["gh", "run", "list"]}
        assert "agent/DRE-4105-first-try" not in listed
        assert "dependabot/npm_and_yarn/vite-5.4.9" not in listed

    def test_one_rerun_per_head_in_a_pass_whatever_else_is_red(self):
        doc = fixture()
        p = pull(doc, PORTICO, 101)
        p["statusCheckRollup"].append({
            **p["statusCheckRollup"][0], "name": "e2e", "workflowName": "E2E",
            "detailsUrl": f"https://github.com/{PORTICO}/actions/runs/91003/job/4"})
        rows = json.loads(doc["gh"][_runs(PORTICO, "agent/DRE-4101-search-tables")])
        rows += [{"databaseId": 91003, "name": "E2E", "conclusion": "failure",
                  "headSha": p["headRefOid"]},
                 {"databaseId": 91004, "name": "E2E", "conclusion": "success",
                  "headSha": p["headRefOid"]}]
        doc["gh"][_runs(PORTICO, "agent/DRE-4101-search-tables")] = json.dumps(rows)
        items, *_ = run(doc)
        reruns = [w for a in actions(items, f"{PORTICO}#101") for w in a.writes
                  if w.kind == "gh_rerun"]
        assert len(reruns) == 1


class TestTheSpentRerun:
    """The lane's own stop, stricter than the core's key."""

    def test_a_head_that_spent_its_rerun_gets_a_left_row_and_no_rerun(self):
        doc = fixture()
        items, *_ = run(doc)
        target = f"{PIPELINE}#104"
        assert actions(items, target) == []
        [row] = left(items, target)
        head = sha7(doc, PIPELINE, 104)
        assert row.lane == "Pull requests"
        assert row.why == f"rerun already spent on head {head}"
        assert "read the run" in row.recommendation

    def test_the_cores_key_alone_would_have_rerun_it(self):
        """The new red run is a new run id and so a new cause: the key does
        not suppress it. Only the lane's stop does."""
        doc = fixture()
        board, ctx, *_ = build(doc)
        head = sha7(doc, PIPELINE, 104)
        new_cause = f"tests red in run 91033, green in run 91034 at head {head}"
        would_be = hygiene.Action(lane=lane.LANE, target=f"{PIPELINE}#104",
                                  act="hygiene-check-rerun", cause=new_cause,
                                  evidence=["run 91033"], writes=[])
        assert hygiene._suppressed(board, would_be) is False

    def test_without_the_spent_receipt_the_same_head_is_rerun(self):
        doc = fixture()
        pull(doc, PIPELINE, 104)["comments"] = []
        items, *_ = run(doc)
        [action] = actions(items, f"{PIPELINE}#104")
        assert action.cause == (f"tests red in run 91033, green in run 91034 "
                                f"at head {sha7(doc, PIPELINE, 104)}")
        assert left(items, f"{PIPELINE}#104") == []

    def test_a_receipt_for_another_head_spends_nothing_here(self):
        doc = fixture()
        p = pull(doc, PIPELINE, 104)
        p["headRefOid"] = "d" * 40  # the same runs, on a head the receipt never named
        rows = json.loads(doc["gh"][_runs(PIPELINE, "agent/DRE-4104-retry-bound")])
        for r in rows:
            r["headSha"] = "d" * 40
        doc["gh"][_runs(PIPELINE, "agent/DRE-4104-retry-bound")] = json.dumps(rows)
        items, *_ = run(doc)
        assert len(actions(items, f"{PIPELINE}#104")) == 1

    def test_a_first_pass_receipt_stops_the_second_pass(self):
        """Run the lane, put its own receipt on the pull request the way the
        write would, and the next pass on the same head leaves it."""
        doc = fixture()
        items, *_ = run(doc)
        [action] = actions(items, f"{PORTICO}#101")
        [comment] = [w for w in action.writes if w.kind == "gh_pr_comment"]
        again = copy.deepcopy(doc)
        pull(again, PORTICO, 101)["comments"].append({"body": comment.body})
        items, *_ = run(again)
        assert actions(items, f"{PORTICO}#101") == []
        assert [r.why for r in left(items, f"{PORTICO}#101")] == [
            f"rerun already spent on head {sha7(doc, PORTICO, 101)}"]


# --------------------------------------------------------------------------- #
# (2) a moot or superseded pull request                                        #
# --------------------------------------------------------------------------- #


class TestAMootPullRequest:
    def test_a_newer_merged_pull_request_for_the_card_closes_this_one(self):
        items, *_ = run()
        [action] = actions(items, f"{PORTICO}#105")
        assert action.act == "hygiene-pr-close"
        assert action.cause == "superseded by merged #110"
        [write] = action.writes
        assert write.kind == "gh_pr_close"
        assert write.argv[:7] == ("gh", "pr", "close", "105", "--repo", PORTICO, "--comment")
        first = write.body.splitlines()[0]
        assert first.startswith("🧹 hygiene: hyg-pr-closed —")
        assert "#110" in first
        assert write.body == hygiene.receipt(action.act, action.cause, action.evidence, NOW)

    def test_the_merged_pull_request_is_found_through_card_pr(self, monkeypatch):
        import card_pr

        asked: list = []
        real = card_pr.find

        def recording(*a, **k):
            asked.append((a, k))
            return real(*a, **k)

        monkeypatch.setattr(card_pr, "find", recording)
        run()
        assert any(a[:1] == ("DRE-4105",) and k.get("repo") == PORTICO for a, k in asked)

    def test_a_canceled_card_closes_its_pull_request(self):
        items, *_ = run()
        [action] = actions(items, f"{PORTICO}#106")
        assert action.act == "hygiene-pr-close"
        assert action.cause == "card DRE-4106 is Canceled"
        [write] = action.writes
        assert write.kind == "gh_pr_close"
        first = write.body.splitlines()[0]
        assert first.startswith("🧹 hygiene: hyg-pr-closed —")
        assert "DRE-4106" in first and "Canceled" in first

    def test_a_duplicate_card_closes_its_pull_request(self):
        items, *_ = run(states={**OFF_BOARD, "DRE-4106": "Duplicate"})
        [action] = actions(items, f"{PORTICO}#106")
        assert action.cause == "card DRE-4106 is Duplicate"

    def test_a_done_card_with_no_other_merged_pull_request_is_left(self):
        items, *_ = run()
        assert actions(items, f"{PORTICO}#107") == []
        [row] = left(items, f"{PORTICO}#107")
        assert row.why == "Done card, open pull request — a person decides"
        assert row.recommendation

    def test_a_card_still_on_the_board_is_not_read_again(self):
        _, _, _, _, linear = run()
        assert {c["id"] for c in linear.calls} == {"DRE-4106", "DRE-4107"}

    def test_a_branch_naming_no_card_is_not_looked_up(self):
        _, _, _, gh, linear = run()
        assert not any("dependabot" in " ".join(c) for c in gh.calls)
        assert all(c["id"].startswith("DRE-") for c in linear.calls)

    def test_a_card_linear_does_not_know_is_left_alone(self):
        items, *_ = run(states={"DRE-4107": "Done"})
        assert actions(items, f"{PORTICO}#106") == []
        assert left(items, f"{PORTICO}#106") == []

    def test_an_older_merged_pull_request_closes_nothing(self):
        """A rework on a branch the card search cannot see: the search finds
        only the card's first, older, merged pull request — and the open one
        is the newer of the two."""
        doc = _rework(merged=50)
        items, *_ = run(doc)
        assert actions(items, f"{PORTICO}#120") == []
        assert left(items, f"{PORTICO}#120") == []

    def test_the_same_rework_with_a_newer_merged_one_is_closed(self):
        """The negative above is not vacuous: make the merged one newer and it acts."""
        items, *_ = run(_rework(merged=130))
        [action] = actions(items, f"{PORTICO}#120")
        assert action.cause == "superseded by merged #130"

    def test_a_closed_pull_request_is_not_also_rerun(self):
        doc = fixture()
        p = pull(doc, PORTICO, 105)
        p["statusCheckRollup"][0]["conclusion"] = "FAILURE"
        items, *_ = run(doc)
        assert [a.act for a in actions(items, f"{PORTICO}#105")] == ["hygiene-pr-close"]


# --------------------------------------------------------------------------- #
# a read that fails, and data that is stale or absent                          #
# --------------------------------------------------------------------------- #

HTTP_502 = RuntimeError("gh exited 1: HTTP 502: Bad Gateway")
#: The ways `ctx.linear` fails in the run: the network past `linear_ops._send`'s
#: one retry, an API error, and the rate limit.
LINEAR_FAILURES = (
    urllib.error.URLError("timed out"),
    linear_ops.LinearError("Linear API returned 503 from https://api.linear.app/graphql"),
    linear_ops.LinearRateLimited("Linear API returned 400: RATELIMITED"),
)


class TestAnUnreadablePullRequest:
    """A read that fails is not "no evidence": the pull request gets a `Left`
    row, never an act, and every other pull request still gets its rows."""

    def _others_still_act(self, items, but):
        but = {but} if isinstance(but, str) else set(but)
        acted = {a.target for a in actions(items)}
        expected = {f"{PORTICO}#101", f"{PORTICO}#102", f"{PORTICO}#105", f"{PORTICO}#106"}
        assert acted == expected - but
        assert f"{PORTICO}#107" in {r.target for r in left(items)}

    def test_a_pr_list_that_fails_leaves_the_pull_request(self):
        doc = fixture()
        doc["gh"][_cards(PORTICO, "DRE-4105")] = HTTP_502
        items, *_ = run(doc)
        target = f"{PORTICO}#105"
        assert actions(items, target) == []
        [row] = left(items, target)
        assert row.why == "could not read the pull requests of DRE-4105"
        assert "HTTP 502" in row.recommendation
        self._others_still_act(items, but=target)

    def test_a_pr_list_that_is_not_json_is_card_prs_lookup_error_and_leaves_it(self):
        doc = fixture()
        doc["gh"][_cards(PORTICO, "DRE-4106")] = "<html>502</html>"
        items, *_ = run(doc)
        target = f"{PORTICO}#106"
        assert actions(items, target) == []
        assert [r.why for r in left(items, target)] == [
            "could not read the pull requests of DRE-4106"]
        self._others_still_act(items, but=target)

    def test_a_run_list_that_fails_leaves_the_pull_request(self):
        doc = fixture()
        doc["gh"][_runs(PORTICO, "agent/DRE-4101-search-tables")] = HTTP_502
        items, *_ = run(doc)
        target = f"{PORTICO}#101"
        assert actions(items, target) == []
        assert [r.why for r in left(items, target)] == [
            "could not read the runs of agent/DRE-4101-search-tables"]
        self._others_still_act(items, but=target)

    def test_a_run_list_that_is_not_json_leaves_the_pull_request(self):
        doc = fixture()
        doc["gh"][_runs(PORTICO, "agent/DRE-4101-search-tables")] = "not json"
        items, *_ = run(doc)
        target = f"{PORTICO}#101"
        assert actions(items, target) == []
        assert [r.why for r in left(items, target)] == [
            "could not read the runs of agent/DRE-4101-search-tables"]
        self._others_still_act(items, but=target)

    def test_a_failed_log_that_cannot_be_read_leaves_the_pull_request(self):
        doc = fixture()
        doc["gh"][_log(PORTICO, 91011)] = RuntimeError("gh run view exited 1: log expired")
        items, *_ = run(doc)
        target = f"{PORTICO}#102"
        assert actions(items, target) == []
        [row] = left(items, target)
        assert row.why == "could not read the log of run 91011"
        assert "log expired" in row.recommendation
        self._others_still_act(items, but=target)

    @pytest.mark.parametrize("failure", LINEAR_FAILURES, ids=lambda f: type(f).__name__)
    def test_a_card_state_that_cannot_be_read_leaves_the_pull_request(self, failure):
        """The Canceled card's pull request is not closed and the Done card's
        is not read as "no state": each gets the one unreadable row."""
        items, *_ = run(states={"DRE-4106": failure, "DRE-4107": failure})
        for number in (106, 107):
            target = f"{PORTICO}#{number}"
            assert actions(items, target) == []
            [row] = left(items, target)
            assert row.why == f"could not read the state of DRE-4{number}"
        self._others_still_act(items, but={f"{PORTICO}#106", f"{PORTICO}#107"})

    def test_a_refusal_is_never_swallowed_as_unreadable(self, monkeypatch):
        """The core's read-only wrapper refusing an argv is a lane bug, not a
        flaky read: it still stops the pass."""
        monkeypatch.setattr(lane, "run_list_argv",
                            lambda repo, branch: ["gh", "pr", "merge", "101", "--repo", repo])
        with pytest.raises(hygiene.Forbidden):
            run()

    def test_the_leg_still_writes_its_ledger(self, monkeypatch):
        doc = fixture()
        _, ctx, gh, _ = build(doc)
        monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [lane])
        ctx.dry_run = True
        answers = dict(doc["gh"])
        answers[_runs(PORTICO, "agent/DRE-4101-search-tables")] = HTTP_502
        for repo in sorted(ctx.repos):
            answers[" ".join(hygiene.pr_list_argv(repo))] = json.dumps(doc["prs"].get(repo, []))
        gh.answers = answers
        ledger = hygiene.run_leg({"lanes": doc["lanes"]}, ctx)
        assert len(ledger["actions"]) == 3
        assert f"{PORTICO}#101" in {r["target"] for r in ledger["left"]}

    @pytest.mark.parametrize("failure", LINEAR_FAILURES, ids=lambda f: type(f).__name__)
    def test_the_leg_still_writes_its_ledger_when_linear_fails(self, monkeypatch, failure):
        doc = fixture()
        _, ctx, gh, _ = build(doc, states={"DRE-4106": failure, "DRE-4107": failure})
        monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [lane])
        ctx.dry_run = True
        answers = dict(doc["gh"])
        for repo in sorted(ctx.repos):
            answers[" ".join(hygiene.pr_list_argv(repo))] = json.dumps(doc["prs"].get(repo, []))
        gh.answers = answers
        ledger = hygiene.run_leg({"lanes": doc["lanes"]}, ctx)
        assert {a["target"] for a in ledger["actions"]} == {
            f"{PORTICO}#101", f"{PORTICO}#102", f"{PORTICO}#105"}
        whys = {r["target"]: r["why"] for r in ledger["left"]}
        assert whys[f"{PORTICO}#106"] == "could not read the state of DRE-4106"
        assert whys[f"{PORTICO}#107"] == "could not read the state of DRE-4107"


class TestStaleOrAbsentData:
    @pytest.mark.parametrize("listed", ["", "[]"])
    def test_an_empty_run_list_proves_no_green_twin(self, listed):
        doc = fixture()
        doc["gh"][_runs(PORTICO, "agent/DRE-4101-search-tables")] = listed
        doc["gh"][_log(PORTICO, 91001)] = REAL
        items, *_ = run(doc)
        assert actions(items, f"{PORTICO}#101") == []
        assert left(items, f"{PORTICO}#101") == []

    @pytest.mark.parametrize("listed", ["", "[]"])
    def test_an_empty_run_list_still_reads_the_log(self, listed):
        doc = fixture()
        doc["gh"][_runs(PORTICO, "agent/DRE-4101-search-tables")] = listed
        doc["gh"][_log(PORTICO, 91001)] = UPSTREAM
        items, *_ = run(doc)
        [action] = actions(items, f"{PORTICO}#101")
        assert action.cause.startswith("web-tests red in run 91001, upstream_5xx at head ")

    def test_a_pull_request_with_no_check_rollup_reads_no_run(self):
        doc = fixture()
        del pull(doc, PORTICO, 101)["statusCheckRollup"]
        items, _, _, gh, _ = run(doc)
        assert actions(items, f"{PORTICO}#101") == []
        listed = {c[c.index("--branch") + 1] for c in gh.calls if c[:3] == ["gh", "run", "list"]}
        assert "agent/DRE-4101-search-tables" not in listed


# --------------------------------------------------------------------------- #
# what it may write, what it may read                                          #
# --------------------------------------------------------------------------- #


class TestWhatItWrites:
    def test_only_the_three_write_kinds(self):
        items, *_ = run()
        kinds = {w.kind for a in actions(items) for w in a.writes}
        assert kinds == {"gh_rerun", "gh_pr_close", "gh_pr_comment"}

    def test_the_whole_fixture_yields_exactly_these_rows(self):
        items, *_ = run()
        assert sorted((a.target, a.act) for a in actions(items)) == [
            (f"{PORTICO}#101", "hygiene-check-rerun"),
            (f"{PORTICO}#102", "hygiene-check-rerun"),
            (f"{PORTICO}#105", "hygiene-pr-close"),
            (f"{PORTICO}#106", "hygiene-pr-close"),
        ]
        assert sorted(r.target for r in left(items)) == [
            f"{PIPELINE}#104", f"{PORTICO}#107"]

    def test_every_write_passes_the_cores_guard(self):
        items, _, ctx, _, _ = run()
        for action in actions(items):
            for write in action.writes:
                hygiene.guard(write, ctx)

    def test_every_gh_argv_it_reads_with_is_admitted_by_the_read_wrapper(self):
        _, _, _, gh, _ = run()
        assert gh.calls, "the lane read nothing"
        verbs = {" ".join(c[1:3]) for c in gh.calls}
        assert {"run list", "run view", "pr list"} <= verbs
        for argv in gh.calls:
            assert hygiene.read_gh_refusal(argv) is None, argv

    def test_the_cores_static_scan_passes_over_it(self):
        path = ROOT / "scripts" / "hygiene_prs_checks.py"
        assert path.exists()
        assert scan(path) == []

    def test_the_core_runs_the_leg_end_to_end_in_dry_run(self, monkeypatch, capsys):
        doc = fixture()
        board, ctx, gh, _ = build(doc)
        monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [lane])
        ctx.dry_run = True
        answers = dict(doc["gh"])
        for repo in sorted(ctx.repos):
            answers[" ".join(hygiene.pr_list_argv(repo))] = json.dumps(doc["prs"].get(repo, []))
        gh.answers = answers
        ledger = hygiene.run_leg({"lanes": doc["lanes"]}, ctx)
        assert {a["outcome"] for a in ledger["actions"]} == {"would"}
        assert len(ledger["actions"]) == 4
        assert "would: gh run rerun 91001 --failed --repo" in capsys.readouterr().out


def _runs(repo, branch):
    return " ".join(lane.run_list_argv(repo, branch))


def _log(repo, run_id):
    return " ".join(lane.run_log_argv(repo, run_id))


def _cards(repo, ident):
    """The `gh pr list` argv `card_pr.find` runs for a card, as the fixture keys it."""
    return (f"gh pr list --repo {repo} --state all --limit 30 "
            f"--json number,url,headRefName,state --search head:agent/{ident}")


def _rework(*, merged):
    """The fixture plus open #120, a rework of DRE-4108 on a `ui/` branch, whose
    card search finds only merged #<merged> on the card's `agent/` branch."""
    doc = fixture()
    doc["prs"][PORTICO].append({
        **pull(doc, PORTICO, 106), "number": 120, "headRefName": "ui/DRE-4108-rework",
        "headRefOid": "e" * 40, "comments": []})
    doc["gh"][_cards(PORTICO, "DRE-4108")] = json.dumps([{
        "number": merged, "url": f"https://github.com/{PORTICO}/pull/{merged}",
        "headRefName": "agent/DRE-4108-first", "state": "MERGED"}])
    return doc
