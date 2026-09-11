"""RED-first tests for DRE-3435 — the sweep's fleet-outage backstop.

DRE-3433 built the DECISION (`scripts/reviewer_down.py`): pure functions over
the outcomes a sweep can already see, answering `file` / `append` / `close` /
`nothing`. Nothing called it. This card is the wrapper — one full-sweep
backstop, `reconcile.report_fleet_reviewer_outage()`, that feeds the decision
what it can see and does exactly what it says.

The decision itself is NOT re-tested here (tests/test_reviewer_down.py owns
it). What this file pins is the wrapper: what is read, what is written, under
which token, and above all that a QUIET SWEEP COSTS NOTHING EXTRA — the alarm
for a fleet-wide outage must not itself become a fleet-wide expense.

The seams it holds:

  * `_open_pr_listing()` — ONE open-pull-request read for the crashed-review
    region, memoised per sweep and shared by `recover_crashed_reviews` (which
    re-dispatches per head) and this backstop (which counts across the fleet).
    A listing failure records the same `_write_failures` line it always did
    and both functions return without acting.
  * The fleet witness comes off `active_cards()` — already read once per sweep
    and served from the pass cache — so the backstop adds ZERO Linear requests
    to a quiet sweep.
  * A `reviewer_environment.hold_receipt(...)` is NOT an outcome (DRE-3428): it
    records a second crash whose evidence note is already counted.
  * Every comment composes through `pipeline_act.receipt()` with the one act
    DRE-3433 declared, so `scripts/check_act_receipts.py` stays green with no
    edit to `config/pipeline-acts.json`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_reconcile_reviewer_down.py -v
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
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
import publish_review_check  # noqa: E402
import reconcile  # noqa: E402
import reviewer_down  # noqa: E402
import reviewer_environment  # noqa: E402

SHA = "d34db33fcafe1234d34db33fcafe1234d34db33f"
RUN_URL = (
    "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/"
    "34512000001/job/96000000001"
)
NATIVE_BINARY_LOG = (
    "agent\tqa\t2026-09-08T22:19:03.1234567Z ReferenceError: Claude Code "
    "native binary not found at /home/runner/.local/bin/claude"
)


@pytest.fixture(autouse=True)
def _pin_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/agent-bureau")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    monkeypatch.setattr(reconcile, "FLEET_OUTAGE_SWEEP_CAP", 1, raising=False)
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


def _environment_note():
    """DRE-3430's evidence note, built by its own WRITER — never restated."""
    signature = reviewer_environment.SIGNATURES[0]
    return reviewer_environment.evidence_note(signature, SHA, RUN_URL)


def _hold_receipt():
    signature = reviewer_environment.SIGNATURES[0]
    return pipeline_act.receipt(
        reviewer_environment.HOLD_ACT,
        reviewer_environment.hold_receipt(signature, SHA, 2),
    )


# --------------------------------------------------------------------------
# the harness
# --------------------------------------------------------------------------
class Written:
    """Everything the backstop wrote, and everything it read from Linear."""

    def __init__(self):
        self.created: list = []
        self.comments: list = []
        self.states: list = []
        self.titles: list = []
        self.prefix_reads: int = 0


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
            return json.dumps(state.get("details_url", RUN_URL))
        raise AssertionError(f"unexpected gh call: {args}")

    return fake_gh


@contextlib.contextmanager
def _outage(prs=(), cards=(), open_card=None, duplicates=(),
            log=NATIVE_BINARY_LOG, create_fails=False, **gh_state):
    """Drive `report_fleet_reviewer_outage` over a fixed world."""
    written = Written()
    state = {"prs": list(prs), "gh_calls": [], **gh_state}

    def find_open_prefix(prefix):
        written.prefix_reads += 1
        assert prefix == reviewer_down.TITLE_PREFIX
        if open_card is None:
            return None
        return {**open_card, "duplicates": list(duplicates)}

    def create_card(title, description, *, repo_slug, **kw):
        if create_fails:
            raise linear_ops.LinearError("Linear API error 500: issueCreate failed")
        written.created.append((title, description, repo_slug))
        return {"identifier": "DRE-9999", "url": "https://linear.app/x/DRE-9999"}

    with patch.object(reconcile, "gh", side_effect=_gh_factory(state)), \
        patch.object(reconcile, "gh_actions_read", side_effect=lambda *a: log), \
        patch.object(reconcile, "active_cards", side_effect=lambda *a, **k: list(cards)), \
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


# --------------------------------------------------------------------------
# 1. the quiet sweep costs nothing
# --------------------------------------------------------------------------
def test_a_quiet_sweep_reads_nothing_further_and_files_nothing():
    """ACCEPTANCE: no could-not-run in the window → one log line, no open-card
    read, no write. The alarm must not itself become a fleet-wide expense."""
    with _outage(prs=[_pr(comments=[_verdict_comment()])],
                 cards=[_card(bodies=[("ordinary chatter", _iso(5))])]) as (w, _):
        out = _capture(reconcile.report_fleet_reviewer_outage)
    assert "fleet-reviewer-outage: nothing to report" in out
    assert "0 could-not-run" in out
    assert w.prefix_reads == 0, (
        "the open card is read only PAST the early exit — a quiet sweep must "
        "make no Linear request beyond the board read it already makes"
    )
    assert w.created == [] and w.comments == [] and w.states == []


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
    `recover_crashed_reviews` has today, and both functions return."""
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
    assert w.prefix_reads == 0 and w.created == []
    assert reconcile._write_failures, "an unreadable listing takes the sweep red"


def test_recover_crashed_reviews_reads_through_the_shared_helper():
    """The ONLY edit this card makes inside `recover_crashed_reviews` is the
    swap — so with the listing already memoised it issues no listing of its
    own."""
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
# 3. the file path — three crashes, two repos, ONE card
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
    "witness", ["generic", "environment"],
    ids=["generic-medic-note", "environment-evidence-note"],
)
def test_three_could_not_run_across_two_repos_file_exactly_one_card(witness):
    """ACCEPTANCE: two local receipts plus one witness on a card labelled
    another repo → ONE card, titled from the decision, described by
    `card_body`, receipted once, and moved to Triage."""
    body = _medic_note() if witness == "generic" else _environment_note()
    written, _ = _run_outage(**_file_world(body))

    assert len(written.created) == 1, "ONE card for one outage — never two"
    title, description, slug = written.created[0]
    assert title.startswith(reviewer_down.TITLE_PREFIX)
    assert title.endswith("— 3 runs, 2 repos"), title
    assert slug == reconcile.REPO_SLUG
    assert "The three usual suspects" in description, "the body is card_body's"
    assert reviewer_down.LEDGER_PREFIX in description

    assert len(written.comments) == 1
    identifier, receipt = written.comments[0]
    assert identifier == "DRE-9999"
    assert reviewer_down.OUTAGE_TAG in receipt
    assert pipeline_act.trailer(reviewer_down.ACT) in receipt, (
        "every comment composes through the ONE act DRE-3433 declared"
    )
    assert written.states == [("DRE-9999", "Triage")]


def test_the_filed_card_carries_the_first_runs_evidence():
    """ACCEPTANCE (step 5): the run url off the head's review check run, the
    log tail through `reviewer_down.error_line`, the action ref off this
    checkout's own qa-review.yml."""
    written, state = _run_outage(**_file_world(_medic_note()))
    _, description, _ = written.created[0]
    assert RUN_URL in description
    assert "native binary not found" in description
    assert "anthropics/claude-code-action@" in description
    assert any(
        c[0] == "api" and "/check-runs" in c[1] for c in state["gh_calls"]
    ), "the run url is read off the head's review check run"


def test_the_check_run_read_asks_for_the_published_review_check():
    written, state = _run_outage(**_file_world(_medic_note()))
    api = next(c for c in state["gh_calls"] if c[0] == "api")
    assert publish_review_check.CHECK_NAME in " ".join(api)


def test_an_unreadable_actions_log_never_fabricates_a_line():
    """ACCEPTANCE: `gh_actions_read` → None yields the honest sentence and the
    run url, never an invented error."""
    written, _ = _run_outage(log=None, **_file_world(_medic_note()))
    _, description, _ = written.created[0]
    assert f"log tail unreadable — see {RUN_URL}" in description


def test_a_failed_create_lands_in_write_failures():
    """ACCEPTANCE: a failed write takes the sweep red (the DRE-1254
    discipline) and nothing downstream of it is claimed."""
    written, _ = _run_outage(create_fails=True, **_file_world(_medic_note()))
    assert written.comments == [] and written.states == []
    assert any("fleet-reviewer-outage" in f for f in reconcile._write_failures), (
        f"got {reconcile._write_failures}"
    )


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
    assert w.created == [] and w.comments == []


# --------------------------------------------------------------------------
# 4. the append path
# --------------------------------------------------------------------------
def _open_card_payload(lines, filed_minutes_ago=20.0):
    return {
        "identifier": "DRE-9500",
        "createdAt": _iso(filed_minutes_ago),
        "description": reviewer_down.card_body(None, lines),
        "url": "https://linear.app/x/DRE-9500",
        "comments": {"nodes": []},
    }


def _ledger_for(repo, pr, at, src):
    return reviewer_down.ledger_line(
        reviewer_down.Outcome(repo, at, reviewer_down.COULD_NOT_RUN, src, pr)
    )


def test_a_fourth_receipt_appends_one_line_and_retitles():
    """ACCEPTANCE: one comment per NEW ledger line, and the title is
    recomputed from the whole ledger."""
    at = [_iso(14), _iso(12), _iso(10)]
    existing = [
        _ledger_for("agent-bureau", 141, at[0], "u1"),
        _ledger_for("agent-bureau", 142, at[1], "u2"),
        _ledger_for("atlas", None, at[2], "linear:DRE-9001:x"),
    ]
    fresh = _neutral(143, _iso(3), n=9)
    written, _ = _run_outage(
        prs=[_pr(143, comments=[fresh])],
        cards=[],
        open_card=_open_card_payload(existing, filed_minutes_ago=9),
    )
    assert written.created == [], "an open card is appended to, never doubled"
    assert len(written.comments) == 1, "one comment per new ledger line"
    identifier, body = written.comments[0]
    assert identifier == "DRE-9500"
    assert fresh["url"] in body, "the ledger line carries the idempotency key"
    assert pipeline_act.trailer(reviewer_down.ACT) in body
    assert written.titles == [("DRE-9500", written.titles[0][1])]
    assert written.titles[0][1].endswith("— 4 runs, 2 repos"), written.titles
    assert written.states == [], "an append never moves the card"


def test_the_same_sweep_run_twice_posts_nothing_the_second_time():
    """ACCEPTANCE: idempotent on `src` — the ledger line is the record."""
    at = [_iso(14), _iso(12), _iso(10)]
    fresh = _neutral(143, _iso(3), n=9)
    existing = [
        _ledger_for("agent-bureau", 141, at[0], "u1"),
        _ledger_for("agent-bureau", 142, at[1], "u2"),
        _ledger_for("atlas", None, at[2], "linear:DRE-9001:x"),
        _ledger_for("agent-bureau", 143, fresh["createdAt"], fresh["url"]),
    ]
    written, _ = _run_outage(
        prs=[_pr(143, comments=[fresh])],
        cards=[],
        open_card=_open_card_payload(existing, filed_minutes_ago=9),
    )
    assert written.comments == [] and written.titles == []


# --------------------------------------------------------------------------
# 5. the close path
# --------------------------------------------------------------------------
def test_a_verdict_after_the_card_was_filed_closes_it():
    """ACCEPTANCE: the first successful verdict posted after the card was
    filed closes it, with the `reviewer back at …` line."""
    filed = _iso(9)
    existing = [_ledger_for("agent-bureau", 141, _iso(14), "u1")]
    written, _ = _run_outage(
        prs=[_pr(141, comments=[_neutral(141, _iso(14)),
                                _verdict_comment(at=_iso(2))])],
        cards=[],
        open_card={**_open_card_payload(existing), "createdAt": filed},
    )
    assert len(written.comments) == 1
    identifier, body = written.comments[0]
    assert identifier == "DRE-9500"
    assert "reviewer back at" in body
    assert pipeline_act.trailer(reviewer_down.ACT) in body
    assert written.states == [("DRE-9500", "Done")]


def test_a_second_sweep_after_the_close_does_nothing():
    """The card is Done, so `find_open_prefix` finds nothing and the sweep is
    back to the quiet path."""
    with _outage(prs=[_pr(141, comments=[_neutral(141, _iso(14)),
                                         _verdict_comment(at=_iso(2))])],
                 cards=[], open_card=None) as (w, _):
        reconcile.report_fleet_reviewer_outage()
    assert w.comments == [] and w.states == [] and w.created == []


# --------------------------------------------------------------------------
# 6. the cross-repo filing race
# --------------------------------------------------------------------------
def test_two_open_cards_the_newer_is_noted_and_canceled():
    """ACCEPTANCE: two sweeps crossing the threshold in the same minute leave
    two alarms; the next sweep resolves it — oldest keeps the ledger, newer
    gets one line naming it and goes to Canceled."""
    at = [_iso(14), _iso(12)]
    existing = [
        _ledger_for("agent-bureau", 141, at[0], "u1"),
        _ledger_for("atlas", None, at[1], "linear:DRE-9001:x"),
    ]
    fresh = _neutral(143, _iso(3), n=9)
    written, _ = _run_outage(
        prs=[_pr(143, comments=[fresh])],
        cards=[],
        open_card=_open_card_payload(existing, filed_minutes_ago=9),
        duplicates=[{"identifier": "DRE-9501",
                     "url": "https://linear.app/x/DRE-9501"}],
    )
    assert ("DRE-9501", "Canceled") in written.states
    dupe = [b for i, b in written.comments if i == "DRE-9501"]
    assert len(dupe) == 1, "one line on the duplicate, not a second alarm"
    assert "DRE-9500" in dupe[0], "the note names the card that survives"
    assert pipeline_act.trailer(reviewer_down.ACT) in dupe[0]
    assert any(i == "DRE-9500" for i, _ in written.comments), (
        "the OLDEST card still receives the append"
    )


# --------------------------------------------------------------------------
# 7. the off switch
# --------------------------------------------------------------------------
def test_the_sweep_cap_at_zero_files_nothing(monkeypatch):
    """ACCEPTANCE: `FLEET_OUTAGE_SWEEP_CAP=0` disables FILING fleet-wide."""
    monkeypatch.setattr(reconcile, "FLEET_OUTAGE_SWEEP_CAP", 0)
    written, _ = _run_outage(**_file_world(_medic_note()))
    assert written.created == [] and written.states == []


def test_the_sweep_cap_at_zero_still_appends_and_closes(monkeypatch):
    """ACCEPTANCE: appends and closes still run, so an open card is never
    orphaned by the off switch."""
    monkeypatch.setattr(reconcile, "FLEET_OUTAGE_SWEEP_CAP", 0)
    existing = [_ledger_for("agent-bureau", 141, _iso(14), "u1")]
    fresh = _neutral(143, _iso(3), n=9)
    written, _ = _run_outage(
        prs=[_pr(143, comments=[fresh])],
        cards=[],
        open_card=_open_card_payload(existing, filed_minutes_ago=9),
    )
    assert len(written.comments) == 1 and written.titles

    closed, _ = _run_outage(
        prs=[_pr(141, comments=[_neutral(141, _iso(14)),
                                _verdict_comment(at=_iso(2))])],
        cards=[],
        open_card=_open_card_payload(existing, filed_minutes_ago=9),
    )
    assert closed.states == [("DRE-9500", "Done")]


def test_the_cap_default_is_one():
    assert reconcile.FLEET_OUTAGE_SWEEP_CAP in (0, 1), (
        "the default is 1 card per sweep; the env may only turn it down"
    )


# --------------------------------------------------------------------------
# 8. where it sits
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
    """Report-and-file only: the fleet-wide answer to a reviewer that cannot
    start is a card for a person, never another dispatch (DRE-1921)."""
    with _outage(**_file_world(_medic_note())) as (_, state):
        with patch.object(reconcile, "_nudge") as nudge, \
            patch.object(reconcile, "gh_dispatch") as dispatch:
            reconcile.report_fleet_reviewer_outage()
    assert nudge.call_count == 0 and dispatch.call_count == 0
    assert not any(c[:2] == ("workflow", "run") for c in state["gh_calls"])


# --------------------------------------------------------------------------
# 9. linear_ops.find_open_prefix
# --------------------------------------------------------------------------
def test_find_open_prefix_returns_the_oldest_open_match():
    """ACCEPTANCE: the OLDEST non-terminal DRE card whose title starts with the
    prefix, with its comment window and the newer matches beside it."""
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
                     side_effect=lambda q, v=None: calls.append((q, v)) or {}):
        linear_ops.set_title("DRE-9500", "Reviewer down since 15:19 PT — 4 runs, 2 repos")
    query, variables = calls[0]
    assert "issueUpdate" in query
    assert variables["input"]["title"].endswith("4 runs, 2 repos")


# --------------------------------------------------------------------------
# 10. the registry is DRE-3433's, untouched here
# --------------------------------------------------------------------------
def test_this_card_edits_no_act_row():
    """Every comment composes through the act DRE-3433 declared — so the
    registry needs no edit, and `check_act_receipts` stays green."""
    import check_act_receipts

    problems = check_act_receipts.problems()
    assert problems == [], problems
    record = pipeline_act.record(reviewer_down.ACT)
    assert record and record["tag"] == reviewer_down.OUTAGE_TAG
