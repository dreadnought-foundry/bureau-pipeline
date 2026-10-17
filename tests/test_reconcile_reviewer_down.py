"""Tests for the sweep's fleet-outage backstop — DRE-3435, repointed by DRE-7455.

DRE-3433 built the DECISION (`scripts/reviewer_down.py`): pure functions over
the outcomes a sweep can already see. DRE-3435 wired it into one full-sweep
backstop, `reconcile.report_fleet_reviewer_outage()`, which kept ONE Linear
card in Triage — filed, appended to, retitled and closed by the sweep.

DRE-7455 makes the outage a console alert instead of that card. The backstop
raises `reviewer_outage:fleet` at CRITICAL through `scripts/pipeline_notice.py`
with the title and body the decision composes, on every full sweep that finds
the outage, and resolves it with the all-clear copy on a sweep that finds no
could-not-run at all. The one-row-per-key alert is the dedupe, so the board
machinery that kept one card is gone, and when the door cannot take the raise
the client files today's card — its title, Triage, `repo:<slug>` and
`no-code`.

The decision itself is NOT re-tested here (tests/test_reviewer_down.py owns
it), and the raise and resolve are faked: tests/test_pipeline_notice.py owns
the client. What this file pins is the wrapper: what is read, what is raised,
under which token, and above all that a QUIET SWEEP COSTS NO LINEAR REQUEST —
the alarm for a fleet-wide outage must not itself become a fleet-wide expense.

The seams it holds:

  * `_open_pr_listing()` — ONE open-pull-request read for the crashed-review
    region, memoised per sweep and shared by `recover_crashed_reviews` (which
    re-dispatches per head) and this backstop (which counts across the fleet).
    A listing failure records the same `_write_failures` line it always did
    and both functions return without acting.
  * The fleet witness comes off `active_cards()` — already read once per sweep
    and served from the pass cache — so the backstop adds ZERO Linear requests
    to any sweep.
  * A `reviewer_environment.hold_receipt(...)` is NOT an outcome (DRE-3428): it
    records a second crash whose evidence note is already counted.

Run: cd bureau-pipeline && python3 -m pytest tests/test_reconcile_reviewer_down.py -v
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import medic_classify  # noqa: E402
import pipeline_act  # noqa: E402
import pipeline_notice  # noqa: E402
import reconcile  # noqa: E402
import reviewer_down  # noqa: E402
import reviewer_environment  # noqa: E402

SHA = "d34db33fcafe1234d34db33fcafe1234d34db33f"
RUN_URL = (
    "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/"
    "34512000001/job/96000000001"
)
#: The crashed QA Review run itself, as `gh run list --json url` names it —
#: the link the card owes its reader (DRE-5292).
CRASHED_RUN_ID = "34512000001"
CRASHED_RUN_URL = (
    "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/"
    + CRASHED_RUN_ID
)
#: What GitHub fills a check run's `details_url` with when the posting App set
#: none: the App's homepage. DRE-5273 linked exactly this as its "first run".
APP_HOMEPAGE = "https://github.com/dreadnought-foundry/agent-bureau"
NATIVE_BINARY_LOG = (
    "agent\tqa\t2026-09-08T22:19:03.1234567Z ReferenceError: Claude Code "
    "native binary not found at /home/runner/.local/bin/claude"
)


@pytest.fixture(autouse=True)
def _pin_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/agent-bureau")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


# --------------------------------------------------------------------------
# fixtures: the two shapes a could-not-run outcome arrives in
# --------------------------------------------------------------------------
def _neutral(pr=141, at=None, n=1):
    """The critic's own could-not-run receipt, on a pull request."""
    return {
        "body": f"{medic_classify.CRITIC_NEUTRAL_MARKER} — the run died at startup",
        "createdAt": at or _iso(10),
        "url": f"https://github.com/x/y/pull/{pr}#issuecomment-{pr}{n}",
    }


def _verdict_comment(at=None):
    return {
        "body": f"🔎 QA Critic — VERDICT: APPROVE @{SHA}\n\nLooks solid.",
        "createdAt": at or _iso(1),
        "url": "https://github.com/x/y/pull/141#issuecomment-999",
    }


def _pr(number=141, comments=(), sha=SHA):
    return {
        "number": number,
        "headRefName": f"agent/DRE-{3400 + number}-widget",
        "headRefOid": sha,
        "baseRefName": "main",
        "mergeStateStatus": "CLEAN",
        "isDraft": False,
        "comments": list(comments),
    }


def _card(identifier="DRE-9001", repo="atlas", bodies=(), state="Todo"):
    """A card in the shape `active_cards` returns it — comments NEWEST FIRST,
    which is what Linear answers and what `window_nodes` reverses."""
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a card whose medic note witnesses another repo's crash",
        "description": "work",
        "updatedAt": _iso(45),
        "state": {"name": state},
        "labels": {"nodes": [{"name": f"repo:{repo}"}]},
        "comments": {
            "nodes": [
                {"body": b, "createdAt": at}
                for b, at in reversed(list(bodies))
            ]
        },
    }


def _medic_note():
    """The GENERIC infra-crash note the medic's backoff job posts."""
    return (
        f"{reviewer_down.MEDIC_BACKOFF_MARKER} — the review run crashed at "
        "startup; retrying once."
    )


def _environment_note(run_url=RUN_URL):
    """DRE-3430's evidence note, built by its own WRITER — never restated."""
    signature = reviewer_environment.SIGNATURES[0]
    return reviewer_environment.evidence_note(signature, SHA, run_url)


#: A run in ANOTHER repository than this sweep's own (`agent-bureau`).
ATLAS_RUN_URL = "https://github.com/EveryBite/atlas/actions/runs/34512000777"


def _hold_receipt():
    signature = reviewer_environment.SIGNATURES[0]
    return pipeline_act.receipt(
        reviewer_environment.HOLD_ACT,
        reviewer_environment.hold_receipt(signature, SHA, 2),
    )




# --------------------------------------------------------------------------
# the harness
# --------------------------------------------------------------------------
RECOVERY_TITLE = "Reviewer back — reviews are running again"


class Written:
    """Everything the backstop raised and resolved, and every Linear write it
    attempted — of which there must be none."""

    def __init__(self, outcome=None):
        self.raises: list = []
        self.resolves: list = []
        self.created: list = []
        self.comments: list = []
        self.states: list = []
        self.titles: list = []
        self.prefix_reads: int = 0
        self.outcome = outcome
        self.open = False

    # the notice client's two calls, answered as the door answers one row
    def raise_notice(self, notice, *, severity, title, body, action_url=None,
                     action_label=None, fallback=None):
        self.raises.append({"notice": notice, "severity": severity,
                            "title": title, "body": body, "fallback": fallback})
        if self.outcome:
            return self.outcome
        outcome = "repeat" if self.open else "raised"
        self.open = True
        return outcome

    def resolve_notice(self, notice, *, recovery_title=None, recovery_body=None):
        self.resolves.append({"notice": notice, "recovery_title": recovery_title,
                              "recovery_body": recovery_body})
        was, self.open = self.open, False
        return "resolved" if was else "not-open"

    @property
    def raised(self) -> dict:
        assert len(self.raises) == 1, self.raises
        return self.raises[0]


def _gh_factory(state):
    """`reconcile.gh` for exactly the reads this backstop makes: the shared
    open-PR listing and the head's review check runs."""

    def fake_gh(*args):
        state["gh_calls"].append(tuple(args))
        if args[:2] == ("pr", "list"):
            if state.get("listing_raises"):
                raise RuntimeError("HTTP 403: rate limited")
            return json.dumps(state["prs"])
        if args[0] == "api" and "/check-runs" in args[1]:
            # `gh api --jq` prints a STRING raw, the way `.default_branch` and
            # `.sha` are already read in this file — no quotes to strip.
            return state.get("details_url", RUN_URL)
        raise AssertionError(f"unexpected gh call: {args}")

    return fake_gh


def _crashed_run(run_id=CRASHED_RUN_ID, conclusion="failure", minutes_ago=12):
    """One row of `gh run list --json databaseId,url,conclusion,createdAt`."""
    return {
        "databaseId": int(run_id),
        "url": ("https://github.com/dreadnought-foundry/agent-bureau/actions/"
                f"runs/{run_id}"),
        "conclusion": conclusion,
        "createdAt": _iso(minutes_ago),
    }


def _actions_factory(state, log):
    """`reconcile.gh_actions_read` for the two Actions reads the evidence
    makes: the head's review workflow runs, and the crashed run's log."""

    def fake_actions(*args):
        state["actions_calls"].append(tuple(args))
        if args[:2] == ("run", "list"):
            runs = state.get("runs", [_crashed_run()])
            return runs if runs is None else json.dumps(runs)
        if args[:2] == ("run", "view"):
            return log
        raise AssertionError(f"unexpected Actions read: {args}")

    return fake_actions


@contextlib.contextmanager
def _outage(prs=(), cards=(), log=NATIVE_BINARY_LOG, outcome=None, door=False,
            written=None, **gh_state):
    """Drive `report_fleet_reviewer_outage` over a fixed world.

    The notice client is `written`, and every Linear write is recorded so a
    test can say there was none. `door=True` is `BUREAU_READ=on`, which no
    longer changes anything here: there is no open card to look up."""
    written = written or Written(outcome)
    state = {"prs": list(prs), "gh_calls": [], "actions_calls": [], **gh_state}
    board = list(cards)

    def active_cards(states=reconcile.SWEEP_STATES):
        return [c for c in board if c["state"]["name"] in states]

    def find_open_prefix(prefix):
        written.prefix_reads += 1
        return None

    def create_card(title, description, *, repo_slug, **kw):
        written.created.append((title, description, repo_slug, kw))
        return {"identifier": "DRE-9999", "url": "https://linear.app/x/DRE-9999"}

    with patch.object(reconcile, "pipeline_notice", written), \
        patch.object(reconcile, "gh", side_effect=_gh_factory(state)), \
        patch.object(reconcile, "gh_actions_read",
                     side_effect=_actions_factory(state, log)), \
        patch.object(reconcile, "active_cards", side_effect=active_cards), \
        patch.object(reconcile.bureau_read, "mode",
                     return_value="on" if door else "off"), \
        patch.object(reconcile.linear_ops, "find_open_prefix",
                     side_effect=find_open_prefix), \
        patch.object(reconcile.linear_ops, "create_card", side_effect=create_card), \
        patch.object(reconcile.linear_ops, "cmd_comment",
                     side_effect=lambda i, b, *f: written.comments.append((i, b))), \
        patch.object(reconcile.linear_ops, "cmd_state",
                     side_effect=lambda i, s, *f: written.states.append((i, s))), \
        patch.object(reconcile.linear_ops, "set_title",
                     side_effect=lambda i, t: written.titles.append((i, t))):
        reconcile.reset_sweep_cards()
        yield written, state


def _run_outage(**kw):
    with _outage(**kw) as (written, state):
        reconcile.report_fleet_reviewer_outage()
    return written, state


def _capture(fn, *a, **kw):
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


def _no_linear_write(written):
    assert written.created == [] and written.comments == []
    assert written.states == [] and written.titles == []


# --------------------------------------------------------------------------
# 1. the quiet sweep costs no Linear request, and clears the alert
# --------------------------------------------------------------------------
def test_a_quiet_sweep_reads_nothing_further_and_resolves_the_alert():
    """No could-not-run in the window → one log line, no Linear write, and
    the alert resolved with the all-clear copy (the door answers `not-open`
    when nothing was raised, and sends nothing)."""
    with _outage(prs=[_pr(comments=[_verdict_comment()])],
                 cards=[_card(bodies=[("ordinary chatter", _iso(5))])]) as (w, _):
        out = _capture(reconcile.report_fleet_reviewer_outage)
    assert "reviewer-outage: nothing to report" in out
    assert "0 could-not-run" in out
    assert w.prefix_reads == 0
    assert w.raises == []
    assert [r["notice"] for r in w.resolves] == ["reviewer_outage:fleet"]
    assert w.resolves[0]["recovery_title"] == RECOVERY_TITLE
    _no_linear_write(w)


def test_a_quiet_sweep_makes_no_linear_request_at_all():
    """The claim stated as a transport-level fact: with the board already read
    for this sweep, a quiet backstop reaches `linear_ops.gql` ZERO times. The
    two budget suites prove the same thing from inside a whole `main()` — this
    one proves it where the failure would be loudest."""
    reconcile.reset_sweep_cards()
    reconcile._swept_cards = [_card(bodies=[("ordinary chatter", _iso(5))])]
    calls: list = []
    notices = Written()

    def refuse(query, variables=None):
        calls.append(query)
        raise AssertionError("a quiet sweep must not touch Linear")

    with patch.object(reconcile, "gh",
                      side_effect=lambda *a: json.dumps([_pr()])), \
        patch.object(reconcile, "pipeline_notice", notices), \
        patch.object(reconcile.bureau_read, "mode", return_value="off"), \
        patch.object(reconcile.linear_ops, "find_open_prefix",
                     side_effect=AssertionError("no open-card lookup")) as lookup, \
        patch.object(reconcile.linear_ops, "gql", side_effect=refuse):
        reconcile.report_fleet_reviewer_outage()
    assert calls == []
    assert lookup.call_count == 0, "there is no open card to look up any more"
    assert len(notices.resolves) == 1


def test_a_full_sweep_lists_the_open_pull_requests_once():
    """ACCEPTANCE: the two functions in the crashed-review region, run in the
    order `main()` runs them, cost ONE `gh pr list` between them."""
    reconcile.reset_sweep_cards()
    reconcile._swept_cards = []
    calls: list = []

    def fake_gh(*args):
        calls.append(tuple(args))
        if args[:2] == ("pr", "list"):
            return json.dumps([_pr(comments=[_verdict_comment()])])
        return "[]"

    with patch.object(reconcile, "gh", side_effect=fake_gh), \
        patch.object(reconcile, "pipeline_notice", Written()), \
        patch.object(reconcile, "verdict_bound", return_value=True), \
        patch.object(reconcile.linear_ops, "gql",
                     side_effect=AssertionError("no Linear read on a quiet sweep")):
        reconcile.recover_crashed_reviews()
        reconcile.report_fleet_reviewer_outage()
    assert sum(1 for c in calls if c[:2] == ("pr", "list")) == 1, (
        f"one listing per sweep, shared: {[c[:2] for c in calls]}"
    )


def test_the_quiet_log_line_names_the_registry_window():
    """The window is DATA on the act row, so the line quotes the number an
    operator would change rather than a literal."""
    threshold = reviewer_down.threshold_from_registry()
    with _outage(prs=[], cards=[]):
        out = _capture(reconcile.report_fleet_reviewer_outage)
    assert f"in the last {threshold.window_s // 60} min" in out


# --------------------------------------------------------------------------
# 2. ONE listing, shared
# --------------------------------------------------------------------------
def test_the_open_pr_listing_is_read_once_and_shared():
    """ACCEPTANCE: `recover_crashed_reviews` and the outage backstop read the
    SAME `gh pr list` — one per sweep, memoised in `_pr_listing`."""
    listing = [_pr()]
    calls: list = []

    def fake_gh(*args):
        calls.append(tuple(args))
        if args[:2] == ("pr", "list"):
            return json.dumps(listing)
        return "[]"

    reconcile.reset_sweep_cards()
    with patch.object(reconcile, "gh", side_effect=fake_gh):
        first = reconcile._open_pr_listing()
        second = reconcile._open_pr_listing()
    assert first == listing and second == listing
    assert sum(1 for c in calls if c[:2] == ("pr", "list")) == 1, (
        "two readers of one listing must not buy it twice"
    )


def test_the_listing_carries_the_same_seven_fields_as_today():
    """DRE-3431 builds on this helper; the fields it reads are the contract."""
    seen: list = []

    def fake_gh(*args):
        seen.append(args)
        return "[]"

    reconcile.reset_sweep_cards()
    with patch.object(reconcile, "gh", side_effect=fake_gh):
        reconcile._open_pr_listing()
    args = seen[0]
    assert args[:2] == ("pr", "list")
    assert "--state" in args and args[args.index("--state") + 1] == "open"
    assert "--limit" in args and args[args.index("--limit") + 1] == "30"
    fields = args[args.index("--json") + 1].split(",")
    assert fields == [
        "number", "headRefName", "headRefOid", "baseRefName",
        "mergeStateStatus", "isDraft", "comments",
    ]


def test_a_failed_listing_records_the_same_line_and_neither_function_acts():
    """ACCEPTANCE: the failure path is byte-identical to the one
    `recover_crashed_reviews` has today, and both functions return — an
    unreadable listing neither raises the alert nor clears it."""
    reconcile.reset_sweep_cards()
    with patch.object(reconcile, "gh",
                      side_effect=RuntimeError("HTTP 403: rate limited")):
        assert reconcile._open_pr_listing() is None
    assert any(
        f.startswith("crashed-review recovery: PR listing failed:")
        for f in reconcile._write_failures
    ), f"got {reconcile._write_failures}"

    reconcile._write_failures.clear()
    with _outage(prs=[], listing_raises=True) as (w, _):
        reconcile.report_fleet_reviewer_outage()
    assert w.raises == [] and w.resolves == []
    assert reconcile._write_failures, "an unreadable listing takes the sweep red"


def test_recover_crashed_reviews_reads_through_the_shared_helper():
    """With the listing already memoised `recover_crashed_reviews` issues no
    listing of its own."""
    reconcile.reset_sweep_cards()
    reconcile._pr_listing = []
    calls: list = []
    with patch.object(reconcile, "gh",
                      side_effect=lambda *a: calls.append(a) or "[]"):
        reconcile.recover_crashed_reviews()
    assert not any(c[:2] == ("pr", "list") for c in calls), (
        "recover_crashed_reviews must read through _open_pr_listing()"
    )


# --------------------------------------------------------------------------
# 3. the raise — three crashes, ONE alert, no card
# --------------------------------------------------------------------------
def _file_world(witness_body):
    return {
        "prs": [
            _pr(141, comments=[_neutral(141, _iso(12))]),
            _pr(142, comments=[_neutral(142, _iso(8), n=2)]),
        ],
        "cards": [_card(bodies=[(witness_body, _iso(6))])],
    }


@pytest.mark.parametrize(
    "witness, repos", [("generic", 1), ("environment", 2)],
    ids=["generic-medic-note", "environment-evidence-note"],
)
def test_three_could_not_run_across_two_repos_raise_one_critical_alert(witness, repos):
    """ACCEPTANCE: two local receipts plus one witness → ONE raise of
    `reviewer_outage:fleet` at CRITICAL, titled from the decision, described
    by `card_body`, and no card. The environment note names a run in atlas,
    so it is a second repository; the generic note names no run, so it is a
    third run and no repository at all (DRE-5291) — the run rule raises it
    either way."""
    body = (_medic_note() if witness == "generic"
            else _environment_note(ATLAS_RUN_URL))
    written, _ = _run_outage(**_file_world(body))

    raised = written.raised
    assert raised["notice"] == "reviewer_outage:fleet"
    assert raised["severity"] == "CRITICAL"
    assert raised["title"].startswith(reviewer_down.TITLE_PREFIX)
    assert raised["title"].endswith(f"— 3 runs, {repos} repos"), raised["title"]
    assert "The three usual suspects" in raised["body"], "the body is card_body's"
    assert raised["body"].count(reviewer_down.LEDGER_PREFIX) == 3, "the run list"
    assert written.resolves == []
    _no_linear_write(written)


def test_a_note_naming_this_sweeps_own_repository_is_not_counted_again():
    """DRE-5291: the note is attributed to the repository in its run link —
    here this sweep's own — never to the card's `repo:` label. This sweep
    already counts its own crashes off its pull requests, so the note adds
    nothing: two crashes in one repository, below both rules — so nothing is
    raised, and with crashes still in the window nothing is resolved."""
    world = _file_world(_environment_note(RUN_URL))
    world["cards"] = [_card(repo="bureau-pipeline",
                            bodies=[(_environment_note(RUN_URL), _iso(6))])]
    with _outage(**world) as (written, _):
        out = _capture(reconcile.report_fleet_reviewer_outage)
    assert written.raises == [] and written.resolves == []
    assert "below the threshold" in out, out


def test_the_raised_alert_carries_the_first_runs_evidence():
    """ACCEPTANCE (step 5): the run url off the head's own QA Review workflow
    run (DRE-5292 — never a check run's `details_url`), the log tail through
    `reviewer_down.error_line`, the action ref off this checkout's own
    qa-review.yml."""
    written, state = _run_outage(**_file_world(_medic_note()))
    description = written.raised["body"]
    assert CRASHED_RUN_URL in description
    assert "native binary not found" in description
    assert "anthropics/claude-code-action@" in description
    assert not any(
        c[0] == "api" and "/check-runs" in c[1] for c in state["gh_calls"]
    ), "a check run's details_url is not a run — it is never read (DRE-5292)"


def test_the_run_read_asks_for_the_review_workflow_on_the_crashed_head():
    """The listing names the crashed repository, the review workflow this
    sweep dispatches, and the crashed head's commit — so the run it answers
    is the one that crashed there, not the newest run anywhere."""
    written, state = _run_outage(**_file_world(_medic_note()))
    listing = next(c for c in state["actions_calls"] if c[:2] == ("run", "list"))
    assert listing[listing.index("--repo") + 1] == reconcile.REPO
    assert listing[listing.index("--workflow") + 1] == reconcile.review_workflow()
    assert listing[listing.index("--commit") + 1] == SHA


# --------------------------------------------------------------------------
# 3a. DRE-5292 — the first-run link is the crashed run, never the App homepage
# --------------------------------------------------------------------------
def _first_run_statement(description):
    """The sentence naming the first run, and the log line under it."""
    lines = description.splitlines()
    at = next(i for i, l in enumerate(lines) if l.startswith("The first crashed run"))
    return lines[at], lines[at + 3]


def test_an_app_homepage_details_url_never_becomes_the_first_run_link():
    """ACCEPTANCE: the DRE-5273 shape — the head's review check run carries
    the posting App's homepage as its `details_url`. The alert links
    `…/actions/runs/<id>` for the crashed QA Review run in the crashed
    repository, and the log tail is read from THAT run."""
    written, state = _run_outage(details_url=APP_HOMEPAGE,
                                 **_file_world(_medic_note()))
    description = written.raised["body"]
    sentence, log_line = _first_run_statement(description)
    assert f"({CRASHED_RUN_URL})" in sentence, sentence
    assert f"({APP_HOMEPAGE})" not in description
    assert "native binary not found" in log_line
    views = [c for c in state["actions_calls"] if c[:2] == ("run", "view")]
    assert len(views) == 1 and views[0][2] == CRASHED_RUN_ID, views
    assert views[0][views[0].index("--repo") + 1] == reconcile.REPO
    assert "--log-failed" in views[0]


def test_the_first_crashed_run_is_the_earliest_failed_one_on_the_head():
    """A head collects runs: the crash, a later green re-review, one still
    going. The alert names the run that CRASHED — the earliest failure."""
    runs = [
        _crashed_run("34512000009", conclusion="success", minutes_ago=2),
        _crashed_run("34512000005", conclusion="failure", minutes_ago=6),
        _crashed_run(CRASHED_RUN_ID, conclusion="failure", minutes_ago=12),
        _crashed_run("34512000010", conclusion="", minutes_ago=1),
    ]
    written, state = _run_outage(runs=runs, **_file_world(_medic_note()))
    sentence, _ = _first_run_statement(written.raised["body"])
    assert f"({CRASHED_RUN_URL})" in sentence, sentence
    views = [c for c in state["actions_calls"] if c[:2] == ("run", "view")]
    assert [v[2] for v in views] == [CRASHED_RUN_ID]


@pytest.mark.parametrize(
    "runs", [[], [_crashed_run(conclusion="success")], None],
    ids=["no-run-on-the-head", "no-failed-run", "listing-unreadable"],
)
def test_no_resolvable_run_says_so_and_prints_no_url(runs):
    """ACCEPTANCE: no crashed run can be resolved → the body says no run was
    found, prints no URL in its place, and reads no log."""
    written, state = _run_outage(runs=runs, details_url=APP_HOMEPAGE,
                                 **_file_world(_medic_note()))
    sentence, log_line = _first_run_statement(written.raised["body"])
    assert "no run found" in log_line, log_line
    assert "http" not in sentence and "http" not in log_line, (sentence, log_line)
    assert not [c for c in state["actions_calls"] if c[:2] == ("run", "view")]


def test_the_evidence_is_the_earliest_LOCAL_crash():
    """A witness outcome is a note about a run in ANOTHER repository — this
    sweep can read neither its check runs nor its log — so the evidence comes
    off the earliest crash this repo can actually prove."""
    written, state = _run_outage(
        prs=[
            _pr(141, comments=[_neutral(141, _iso(12))]),
            _pr(142, comments=[_neutral(142, _iso(8), n=2)]),
        ],
        # the OLDEST outcome of the three, and unreachable from here
        cards=[_card(bodies=[(_medic_note(), _iso(20))])],
    )
    description = written.raised["body"]
    assert CRASHED_RUN_URL in description
    assert "native binary not found" in description
    assert "agent-bureau#141" in description, (
        "the first run named is the earliest LOCAL crash, not the witness"
    )


def test_an_unreadable_actions_log_never_fabricates_a_line():
    """ACCEPTANCE: `gh_actions_read` → None yields the honest sentence and the
    run url, never an invented error."""
    written, _ = _run_outage(log=None, **_file_world(_medic_note()))
    assert f"log tail unreadable — see {CRASHED_RUN_URL}" in written.raised["body"]


# --------------------------------------------------------------------------
# 3b. the fallback is today's card: Triage, `repo:<slug>`, `no-code`
# --------------------------------------------------------------------------
def test_the_fallback_card_is_created_in_triage_and_never_passes_planning():
    """DRE-5292's rule, kept for the card the client files when the door
    cannot take the raise: created in Triage — Planning entry is what the
    relay dispatches the planner on (DRE-5273's Agent Plan run
    36658173529)."""
    written, _ = _run_outage(**_file_world(_medic_note()))
    fallback = written.raised["fallback"]
    assert isinstance(fallback, pipeline_notice.Fallback)
    assert fallback.lane == "Triage" == reconcile.FLEET_OUTAGE_LANE
    assert fallback.title == written.raised["title"]


def test_triage_is_a_lane_this_sweep_may_create_in():
    """The lane contract names reconcile.py a permitted Triage writer."""
    contract = json.loads((ROOT / "config" / "lane-contract.json").read_text())
    triage = next(l for l in contract["lanes"] if l["name"] == "Triage")
    assert "reconcile.py" in triage["clauses"]["writers"]["who"]


def test_the_fallback_card_wears_no_agent_role_and_is_marked_no_code():
    """No `agent:*` role — 25 of 30 earlier outage cards wore
    `agent:engineer` — and `no-code`, the mark every gate reads as "no run is
    coming for this": the alarm is not work to build or plan."""
    written, _ = _run_outage(**_file_world(_medic_note()))
    labels = [l.lower() for l in written.raised["fallback"].labels]
    assert labels == ["repo:agent-bureau", linear_ops.NO_CODE_LABEL]
    assert not [l for l in labels if l.startswith("agent:")], labels
    assert reconcile.HAND_BUILT_LABEL not in labels
    assert not reconcile.counts_against_wip(
        {"labels": {"nodes": [{"name": l} for l in labels]}}
    )


def test_a_door_that_is_not_there_files_todays_card_in_one_mutation(monkeypatch):
    """The same claim through the REAL client: with no `BUREAU_READ_URL` the
    door is not reached, so `pipeline_notice` files the fallback — and the ONE
    Linear write that mints it carries the outage's title, Triage's state id,
    `repo:<slug>` and `no-code`, and no `agent:*` label."""
    monkeypatch.delenv("BUREAU_READ_URL", raising=False)
    mutations: list = []

    def fake_gql(query, variables=None, **_kw):
        if "teams(" in query:
            return {"teams": {"nodes": [{"id": "team-1"}]}}
        if "issueCreate" in query:
            mutations.append(variables["input"])
            return {"issueCreate": {"success": True, "issue": {
                "id": "u", "identifier": "DRE-9999",
                "url": "https://linear.app/x/DRE-9999"}}}
        raise AssertionError(f"unexpected Linear call: {query[:80]}")

    world = _file_world(_medic_note())
    with patch.object(linear_ops, "gql", side_effect=fake_gql), \
        patch.object(linear_ops, "find_open", return_value=None), \
        patch.object(linear_ops, "state_id",
                     side_effect=lambda team, lane: f"state:{lane}"), \
        patch.object(linear_ops, "_team_label_ids",
                     side_effect=lambda team, names: [f"label:{n}" for n in names]), \
        patch.object(reconcile, "gh", side_effect=_gh_factory(
            {"prs": world["prs"], "gh_calls": []})), \
        patch.object(reconcile, "gh_actions_read", side_effect=_actions_factory(
            {"actions_calls": []}, NATIVE_BINARY_LOG)), \
        patch.object(reconcile, "active_cards",
                     side_effect=lambda *a, **k: world["cards"]), \
        patch.object(reconcile.linear_ops, "cmd_comment",
                     side_effect=AssertionError("no receipt is posted")), \
        patch.object(reconcile.linear_ops, "cmd_state",
                     side_effect=AssertionError("no state write on this path")):
        reconcile.reset_sweep_cards()
        out = _capture(reconcile.report_fleet_reviewer_outage)
    assert len(mutations) == 1
    assert mutations[0]["title"].startswith(reviewer_down.TITLE_PREFIX)
    assert mutations[0]["stateId"] == "state:Triage"
    assert mutations[0]["labelIds"] == [
        "label:repo:agent-bureau", f"label:{linear_ops.NO_CODE_LABEL}"]
    assert ("pipeline-notice: fallback-card no-url "
            "kind=pipeline_notice:reviewer_outage:fleet card=DRE-9999") in out, out
    assert reconcile._write_failures == []


def test_a_lost_raise_lands_in_write_failures():
    """The door refused and the fallback card could not be filed either: a
    failed write takes the sweep red (the DRE-1254 discipline)."""
    written, _ = _run_outage(outcome="lost", **_file_world(_medic_note()))
    _no_linear_write(written)
    assert any("reviewer-outage" in f for f in reconcile._write_failures), (
        f"got {reconcile._write_failures}"
    )


def test_a_recorded_write_failure_takes_the_whole_sweep_red():
    """The other half of the criterion above: `_write_failures` is the rail
    `main()` exits non-zero on, so a lost alert is a red run the medic sees
    rather than a silent miss (the DRE-1254 discipline)."""
    _run_outage(outcome="lost", **_file_world(_medic_note()))
    assert reconcile._write_failures
    with patch.object(reconcile, "active_cards", return_value=[]), \
        patch.object(reconcile, "merged_card_scope", return_value=None), \
        patch.object(reconcile, "promote_ready", return_value=0), \
        patch.object(reconcile.linear_ops, "open_pass"), \
        pytest.raises(SystemExit) as exit_info:
        reconcile.main(promote_only=True)
    assert "write failure" in str(exit_info.value)


def test_a_hold_receipt_alone_contributes_zero_outcomes():
    """ACCEPTANCE (DRE-3428): `runner-environment-hold` is not an outcome — it
    records a SECOND crash whose evidence note is already counted."""
    hold = {
        "body": _hold_receipt(),
        "createdAt": _iso(5),
        "url": "https://github.com/x/y/pull/141#issuecomment-77",
    }
    with _outage(prs=[_pr(141, comments=[hold])], cards=[]) as (w, _):
        out = _capture(reconcile.report_fleet_reviewer_outage)
    assert "nothing to report" in out
    assert w.raises == []
    _no_linear_write(w)


# --------------------------------------------------------------------------
# 4. a second sweep raises the same key again — the alert is the dedupe
# --------------------------------------------------------------------------
def test_a_second_sweep_finding_the_same_outage_raises_the_same_key_again():
    """The door counts it a repeat: the text is refreshed in place and
    nothing is delivered again. No card is appended to or retitled."""
    written = Written()
    _run_outage(written=written, **_file_world(_medic_note()))
    _run_outage(written=written, **_file_world(_medic_note()))
    assert [r["notice"] for r in written.raises] == ["reviewer_outage:fleet"] * 2
    _no_linear_write(written)


def test_a_fourth_crash_raises_the_recomputed_title():
    """The title carries the counts, so the next raise carries the new ones —
    the door refreshes the open alert's text in place."""
    written = Written()
    world = _file_world(_medic_note())
    _run_outage(written=written, **world)
    world["prs"].append(_pr(143, comments=[_neutral(143, _iso(3), n=9)]))
    _run_outage(written=written, **world)
    assert written.raises[0]["title"].endswith("— 3 runs, 1 repos")
    assert written.raises[1]["title"].endswith("— 4 runs, 1 repos")


def test_two_repositories_raising_in_the_same_minute_is_one_alert():
    """The cross-repo race the board machinery used to clean up after: two
    sweeps raise the same key, and the second is a repeat."""
    written = Written()
    _run_outage(written=written, **_file_world(_medic_note()))
    with patch.object(reconcile, "REPO_SLUG", "atlas"), \
        patch.object(reconcile, "REPO", "EveryBite/atlas"):
        world = _file_world(_environment_note(RUN_URL))
        _run_outage(written=written, **world)
    assert len(written.raises) == 2
    assert {r["notice"] for r in written.raises} == {"reviewer_outage:fleet"}
    assert written.open is True
    _no_linear_write(written)


# --------------------------------------------------------------------------
# 5. the resolve — the all-clear copy, once the outage no longer holds
# --------------------------------------------------------------------------
def test_a_verdict_after_every_crash_aged_out_resolves_with_the_all_clear():
    """The reviewer is back and no could-not-run is left in the window: the
    alert is resolved with the contract's all-clear copy, which the console
    delivers to everyone who was told it went down."""
    written = Written()
    _run_outage(written=written, **_file_world(_medic_note()))
    _run_outage(written=written,
                prs=[_pr(141, comments=[_neutral(141, _iso(130)),
                                        _verdict_comment(at=_iso(60))])],
                cards=[])
    (resolve,) = written.resolves
    assert resolve["notice"] == "reviewer_outage:fleet"
    assert resolve["recovery_title"] == RECOVERY_TITLE
    assert resolve["recovery_body"].startswith("The reconcile sweep at ")
    assert resolve["recovery_body"].endswith(
        " found the fleet-wide reviewer outage cleared.")
    assert written.open is False
    _no_linear_write(written)


def test_a_verdict_with_a_crash_still_in_the_window_resolves_nothing():
    """Hysteresis, on purpose: while any sweep can still see a crash in the
    window another repository's sweep may still be raising, and a resolve
    between two raises would deliver the outage to the CEO twice."""
    written = Written()
    _run_outage(written=written, **_file_world(_medic_note()))
    _run_outage(written=written,
                prs=[_pr(141, comments=[_neutral(141, _iso(14)),
                                        _verdict_comment(at=_iso(2))])],
                cards=[])
    assert written.resolves == []
    assert written.open is True


def test_under_the_read_door_nothing_is_looked_up():
    """`BUREAU_READ=on` used to cost one `find_open_prefix` request on every
    pass, because the door's snapshot did not hold the alarm lane. There is
    no open card to look up any more."""
    with _outage(prs=[_pr(comments=[_verdict_comment()])], cards=[],
                 door=True) as (w, _):
        out = _capture(reconcile.report_fleet_reviewer_outage)
    assert w.prefix_reads == 0
    assert "nothing to report" in out
    assert len(w.resolves) == 1


# --------------------------------------------------------------------------
# 6. where it sits
# --------------------------------------------------------------------------
def test_the_backstop_sits_immediately_after_recover_crashed_reviews():
    """ACCEPTANCE + the DRE-3431 contract: the two read the same listing, so
    the order is load-bearing and DRE-3431 leaves it alone."""
    source = (ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8")
    main = source[source.index("def main("):]
    tuple_text = main[main.index("for backstop in ("):]
    tuple_text = tuple_text[: tuple_text.index("\n        ):")]
    names = [
        line.strip().rstrip(",")
        for line in tuple_text.splitlines()
        if line.strip().rstrip(",").isidentifier()
    ]
    assert "report_fleet_reviewer_outage" in names
    assert names.index("report_fleet_reviewer_outage") == (
        names.index("recover_crashed_reviews") + 1
    ), names


def test_the_backstop_runs_on_full_sweeps_only():
    """Never on the promote-only / conflicts-only / close-only event paths."""
    for kwargs in ({"promote_only": True}, {"conflicts_only": True},
                   {"close_only": True}):
        with patch.object(reconcile, "report_fleet_reviewer_outage") as backstop, \
            patch.object(reconcile, "unstick_conflicts"), \
            patch.object(reconcile, "active_cards", return_value=[]), \
            patch.object(reconcile, "merged_card_scope", return_value=None), \
            patch.object(reconcile, "backlog_children", return_value=[]), \
            patch.object(reconcile, "close_finished_epics"), \
            patch.object(reconcile, "promote_ready", return_value=0), \
            patch.object(reconcile.linear_ops, "open_pass"):
            reconcile.main(**kwargs)
        assert backstop.call_count == 0, kwargs


def test_report_fleet_reviewer_outage_never_dispatches_a_workflow():
    """Report only: the fleet-wide answer to a reviewer that cannot start is
    an alert for a person, never another dispatch (DRE-1921)."""
    with _outage(**_file_world(_medic_note())) as (_, state):
        with patch.object(reconcile, "_nudge") as nudge, \
            patch.object(reconcile, "gh_dispatch") as dispatch:
            reconcile.report_fleet_reviewer_outage()
    assert nudge.call_count == 0 and dispatch.call_count == 0
    assert not any(c[:2] == ("workflow", "run") for c in state["gh_calls"])


# --------------------------------------------------------------------------
# 7. linear_ops.find_open_prefix and set_title stay as linear_ops verbs
# --------------------------------------------------------------------------
def test_find_open_prefix_returns_the_oldest_open_match():
    """The OLDEST non-terminal DRE card whose title starts with the prefix,
    with its comment window and the newer matches beside it."""
    nodes = [
        {"identifier": "DRE-2", "createdAt": "2026-09-08T23:00:00Z",
         "title": reviewer_down.TITLE_PREFIX + "16:00 PT — 1 runs, 1 repos",
         "description": "newer", "url": "u2", "comments": {"nodes": []}},
        {"identifier": "DRE-1", "createdAt": "2026-09-08T22:00:00Z",
         "title": reviewer_down.TITLE_PREFIX + "15:19 PT — 3 runs, 2 repos",
         "description": "oldest", "url": "u1", "comments": {"nodes": []}},
    ]
    seen: list = []

    def fake_gql(query, variables=None):
        seen.append((query, variables))
        return {"issues": {"nodes": nodes}}

    with patch.object(linear_ops, "gql", side_effect=fake_gql):
        found = linear_ops.find_open_prefix(reviewer_down.TITLE_PREFIX)
    assert found["identifier"] == "DRE-1"
    assert found["description"] == "oldest"
    assert found["url"] == "u1"
    assert "comments" in found
    assert [d["identifier"] for d in found["duplicates"]] == ["DRE-2"]
    query, variables = seen[0]
    assert "startsWith" in query
    assert '"completed"' in query and '"canceled"' in query
    assert linear_ops.COMMENT_WINDOW_GQL in " ".join(query.split())
    assert variables["prefix"] == reviewer_down.TITLE_PREFIX


def test_find_open_prefix_answers_none_when_nothing_matches():
    with patch.object(linear_ops, "gql", return_value={"issues": {"nodes": []}}):
        assert linear_ops.find_open_prefix("Reviewer down since ") is None


def test_find_open_prefix_has_a_cli_spelling_beside_find_open():
    source = (ROOT / "scripts" / "linear_ops.py").read_text(encoding="utf-8")
    assert '"find-open-prefix": cmd_find_open_prefix' in source
    with patch.object(linear_ops, "gql", return_value={"issues": {"nodes": [
        {"identifier": "DRE-7", "createdAt": "2026-09-08T22:00:00Z",
         "description": "", "url": "u", "comments": {"nodes": []}},
    ]}}):
        out = _capture(linear_ops.cmd_find_open_prefix, "Reviewer down since ")
    assert out.strip() == "DRE-7"


def test_set_title_writes_the_recomputed_title():
    calls: list = []
    with patch.object(linear_ops, "get_issue", return_value={"id": "uuid-1"}), \
        patch.object(linear_ops, "gql",
                     side_effect=lambda q, v=None, **_kw: calls.append((q, v)) or {}):
        linear_ops.set_title("DRE-9500", "Reviewer down since 15:19 PT — 4 runs, 2 repos")
    query, variables = calls[0]
    assert "issueUpdate" in query
    assert variables["input"]["title"].endswith("4 runs, 2 repos")


# --------------------------------------------------------------------------
# 8. the receipt is no longer posted
# --------------------------------------------------------------------------
def test_the_receipt_guard_stays_green_and_the_act_row_is_untouched():
    import check_act_receipts

    problems = check_act_receipts.problems()
    assert problems == [], problems
    record = pipeline_act.record(reviewer_down.ACT)
    assert record and record["tag"] == reviewer_down.OUTAGE_TAG


def test_the_outage_receipt_is_posted_nowhere():
    """DRE-7455: the `reviewer-outage-fleet-wide` receipt is no longer posted
    to a card. Nothing in reconcile.py composes it, and the act reads as one
    nothing posts — `check_act_receipts.pending_acts()`, which holds the
    emission guards to that and lifts the exemption the moment a poster
    spells the act again."""
    import check_act_receipts

    ours = [
        site for site in check_act_receipts.sites()
        if site.path == "scripts/reconcile.py"
        and site.composed_as == reviewer_down.ACT
    ]
    assert ours == []
    assert reviewer_down.ACT in check_act_receipts.pending_acts()


# --------------------------------------------------------------------------
# DRE-4577: a repo the pipeline does not serve reports, and raises nothing
# --------------------------------------------------------------------------
def test_a_repo_the_pipeline_does_not_serve_reports_the_outage_and_raises_nothing(monkeypatch):
    """Live: bureau-harness Reconcile 35670916057 (2026-09-22 00:11Z) reached
    the file branch and `linear_ops.create_card` refused the slug, and that
    refused write took the sandbox sweep red. The sandbox is outside the
    roster BY DESIGN (config/repo-map.json), and the alert's fallback is a
    card under its slug that `create_card` would refuse the same way — so the
    sweep says what it saw in its own log and raises nothing, and nothing
    lands on the fail-loudly rail. A served repo's sweep raises the alert."""
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/bureau-harness")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-harness")
    assert "bureau-harness" not in reconcile.validate_card.VALID_SLUGS, (
        "the sandbox must stay outside the roster for this test to mean anything"
    )
    with _outage(**_file_world(_medic_note())) as (written, _):
        out = _capture(reconcile.report_fleet_reviewer_outage)
    assert written.raises == [] and written.resolves == []
    _no_linear_write(written)
    assert reconcile._write_failures == [], reconcile._write_failures
    assert "bureau-harness" in out and "3 could-not-run" in out, out
    assert "a served repo's sweep raises the alert" in out, out


# --------------------------------------------------------------------------
# DRE-6576: the board read still carries Triage; what the sweep ACTS on does
# not change
# --------------------------------------------------------------------------
def test_swept_lanes_is_unchanged():
    assert reconcile.SWEPT_LANES == (
        "Todo", "In Progress", "In Review", "Hand-work", "Planning", "Intake")


def test_the_board_read_lanes_are_swept_lanes_plus_the_alarm_lane():
    # And the retiring lanes (DRE-6753): the drain reads them off this read
    # rather than buying a request of its own on every sweep.
    assert reconcile.BOARD_READ_LANES == (
        reconcile.SWEPT_LANES + (reconcile.FLEET_OUTAGE_LANE,)
        + reconcile.RETIRING_LANES)
    assert reconcile.FLEET_OUTAGE_LANE == "Triage"
    for unread in (reconcile.QUEUE_LANE, "Green Light"):
        assert unread not in reconcile.BOARD_READ_LANES


def test_the_one_board_read_is_made_with_the_board_read_lanes():
    sent: list = []

    def gql(query, variables=None, **_kw):
        sent.append((query, dict(variables or {})))
        return {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False,
                                                     "endCursor": None}}}

    reconcile.reset_sweep_cards()
    with patch.object(reconcile.bureau_read, "mode", return_value="off"), \
        patch.object(linear_ops, "gql", side_effect=gql):
        reconcile.active_cards(reconcile.SWEPT_LANES)
        reconcile.active_cards((reconcile.FLEET_OUTAGE_LANE,))
    assert len(sent) == 1, "the alarm lane rides the one read"
    query, variables = sent[0]
    assert variables["states"] == list(reconcile.BOARD_READ_LANES)
    assert "createdAt" in query.split()


def test_active_cards_of_swept_lanes_returns_no_triage_card():
    triage_card = {**_card(identifier="DRE-9500", state="Triage"),
                   "title": reviewer_down.TITLE_PREFIX + "15:19 PT — 1 runs, 1 repos"}
    reconcile.reset_sweep_cards()
    reconcile._swept_cards = [_card(state="Todo"), triage_card]
    with patch.object(reconcile.bureau_read, "mode", return_value="off"), \
        patch.object(linear_ops, "gql", side_effect=AssertionError("cached")):
        swept = reconcile.active_cards(reconcile.SWEPT_LANES)
        triage = reconcile.active_cards((reconcile.FLEET_OUTAGE_LANE,))
    assert [c["identifier"] for c in swept] == ["DRE-9001"]
    assert [c["identifier"] for c in triage] == ["DRE-9500"]


# --------------------------------------------------------------------------
# DRE-6576: nothing still says the card closes on a verdict posted after it
# was filed
# --------------------------------------------------------------------------
def test_no_document_names_the_filed_at_close_rule():
    import ast

    reconcile_src = (ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8")
    module = (ROOT / "scripts" / "reviewer_down.py").read_text(encoding="utf-8")
    tree = ast.parse(module)
    first_verdict = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                         and n.name == "_first_verdict_after")
    row = pipeline_act.record(reviewer_down.ACT)
    docs = {
        "report_fleet_reviewer_outage": reconcile.report_fleet_reviewer_outage.__doc__,
        "reviewer_down module": ast.get_docstring(tree),
        "_first_verdict_after": ast.get_docstring(first_verdict),
        "docs/pipeline-acts.md": (ROOT / "docs" / "pipeline-acts.md").read_text(
            encoding="utf-8"),
        "the act row's why": row["why"],
    }
    for name, text in docs.items():
        flat = " ".join((text or "").split())
        for stale in ("posted after it was filed", "after the card was filed",
                      "the card waits for the person it was filed for"):
            assert stale not in flat, (name, stale)
    assert "def report_fleet_reviewer_outage" in reconcile_src
