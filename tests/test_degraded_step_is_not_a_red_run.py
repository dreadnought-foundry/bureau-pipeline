"""RED-first tests: a sweep step that degrades gracefully does not fail the run
(DRE-4214).

THE INCIDENT (2026-09-18, six times between 08:26 and 10:35 PT): GitHub cut
the shared App installation's API access mid-sweep at the hourly limit, on the
unlanded watchdog's branch listing — `gh api repos/<repo>/branches --paginate`
through `gh_read`. The retry DRE-4109 added was exhausted (an hourly bucket
does not refill in a minute), `card_branches` recorded the refusal on the
WRITE-failure ledger, and the sweep exited 1 — after it had already printed
`unlanded: branch listing unreadable — reporting nothing this sweep` and gone
on to finish every other phase normally: card promotion, stale-card cleanup,
the reviewer-outage report. Six red runs, six medic diagnoses, one card. A run
that goes red after handling a fault correctly teaches everyone to ignore red
runs.

THE OPERATOR'S SCOPE (on the card, 2026-09-18 11:14 PT): a sweep step that
degrades gracefully reports what it could not read and does NOT fail the run;
a step that fails for a reason it did not handle still does. The rate limit
itself owes no fix. This supersedes the pin DRE-4109 left — "that run still
raises, still exits 1 and still files" — for the ONE fault the read seam
already classifies and handles: a rate-limit refusal that outlasts the retry.

FIX UNDER TEST:
  - `gh_read` names the fault it could not clear: a refusal whose stderr is
    GitHub's rate-limit wording raises `ReconcileRateLimited`, a subclass of
    `ReconcileReadError`, so every existing `except ReconcileReadError` and
    every `pytest.raises(ReconcileReadError)` still holds;
  - `card_branches` handles THAT fault by degrading: the listing answers None
    (never [] — the DRE-2034 discipline is untouched), the watchdog reports
    nothing this sweep, and the fact goes on a FOURTH ledger, `_degraded`,
    with one `DEGRADED:` line in the run log naming what could not be read;
  - `_degraded` never decides the exit: the two red-run decisions in `main`
    read `_write_failures`, `_read_failures` and `_stale_defects` exactly as
    before, and `main` prints one summary line for the degraded steps;
  - any OTHER failure of the same listing — a permission 403, a 404, a network
    error — is a reason nothing handles, and it stays on the write-failure
    ledger: red run, medic, card, exactly as today. A wrong token must never
    leave the watchdog blind under green runs.

Run: cd bureau-pipeline && python3 -m pytest tests/test_degraded_step_is_not_a_red_run.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402

#: GitHub's answer on 2026-09-18, verbatim from the card.
RATE_LIMITED = (
    "gh: HTTP 403: API rate limit exceeded for installation ID 123249480 "
    "(https://api.github.com/repos/dreadnought-foundry/bureau-pipeline/branches)"
)
#: The OTHER 403 — a permission failure nothing in the sweep handles.
NOT_PERMITTED = "gh: HTTP 403: Resource not accessible by integration"

BRANCH = "agent/DRE-4214-degraded-not-red"
BRANCH_LISTING = json.dumps({"name": BRANCH, "sha": "a" * 40})


@pytest.fixture(autouse=True)
def _clean_sweep_state(monkeypatch):
    """Pin the import-time bindings and clear every ledger before AND after,
    so a leftover entry cannot colour another test's sweep."""
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/bureau-pipeline")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    monkeypatch.setattr(reconcile, "_gh_read_retries_spent", 0, raising=False)
    _clear_ledgers()
    yield
    _clear_ledgers()


def _clear_ledgers() -> None:
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()
    degraded = getattr(reconcile, "_degraded", None)
    if degraded is not None:
        degraded.clear()


def _run_stub(answer_for):
    """subprocess.run stub: `answer_for(argv)` -> (returncode, stdout, stderr)."""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        rc, out, err = answer_for(list(argv))
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    return fake_run, calls


def _branches_refused(stderr: str):
    """Every `gh` call answers cleanly except the branch listing, refused with
    `stderr` on every attempt — the shape of a bucket that is genuinely empty."""
    def answer(argv):
        if argv[:2] == ["gh", "api"] and argv[2].endswith("/branches"):
            return 1, "", stderr
        if argv[:3] == ["gh", "pr", "list"]:
            return 0, "[]", ""
        return 0, "", ""
    return answer


def _no_sleep():
    return mock.patch.object(reconcile.time, "sleep", side_effect=lambda s: None)


# --------------------------------------------------------------------------
# The seam names the fault it handled
# --------------------------------------------------------------------------
def test_gh_read_names_a_rate_limit_refusal_it_could_not_clear():
    """Three refusals, all rate-limit wording: the read seam raises
    `ReconcileRateLimited` — a `ReconcileReadError`, so nothing that catches
    the parent changes, and a caller that wants to know WHICH fault it is
    can ask."""
    fake_run, calls = _run_stub(lambda argv: (1, "", RATE_LIMITED))
    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), _no_sleep():
        with pytest.raises(reconcile.ReconcileReadError) as exc_info:
            reconcile.gh_read("api", "repos/o/r/branches", "--paginate")
    assert isinstance(exc_info.value, reconcile.ReconcileRateLimited)
    assert issubclass(reconcile.ReconcileRateLimited, reconcile.ReconcileReadError)
    assert len(calls) == reconcile.GH_READ_ATTEMPTS, "the retry itself is unchanged"
    assert "rate limit exceeded" in str(exc_info.value)


def test_gh_read_names_the_fault_past_the_sweep_retry_budget():
    """Once the sweep's retry budget is spent a refused read raises on its
    first answer (DRE-4109) — and it is STILL the rate-limit fault, because
    the classification reads the stderr, not whether a retry was afforded."""
    fake_run, _calls = _run_stub(lambda argv: (1, "", RATE_LIMITED))
    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), _no_sleep(), \
            mock.patch.object(reconcile, "_gh_read_retries_spent", reconcile.GH_READ_RETRY_BUDGET):
        with pytest.raises(reconcile.ReconcileRateLimited):
            reconcile.gh_read("api", "repos/o/r/branches")


def test_gh_read_does_not_name_a_permission_refusal_a_rate_limit():
    fake_run, _calls = _run_stub(lambda argv: (1, "", NOT_PERMITTED))
    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), _no_sleep():
        with pytest.raises(reconcile.ReconcileReadError) as exc_info:
            reconcile.gh_read("api", "repos/o/r/branches")
    assert not isinstance(exc_info.value, reconcile.ReconcileRateLimited)


# --------------------------------------------------------------------------
# The step that degraded: the branch listing, refused for quota all the way
# --------------------------------------------------------------------------
def test_a_persisting_rate_limit_on_the_branch_listing_is_degraded_not_failed(capsys):
    """REPLICATION of DRE-4214. The listing is refused for rate limit on every
    attempt: `card_branches` answers None (unreadable is never empty), NEITHER
    failure ledger gains an entry, and the degraded ledger carries one line
    naming the branch listing and the refusal.

    On the unfixed code this FAILS: the refusal lands on `_write_failures`,
    which is what turned six sweeps red on 2026-09-18."""
    fake_run, _calls = _run_stub(_branches_refused(RATE_LIMITED))
    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), _no_sleep():
        assert reconcile.card_branches() is None

    assert reconcile._write_failures == [], (
        "a fault the step handled must not turn the run red"
    )
    assert reconcile._read_failures == []
    assert len(reconcile._degraded) == 1, reconcile._degraded
    entry = reconcile._degraded[0]
    assert "branch listing" in entry, "the ledger names WHAT could not be read"
    assert "rate limit exceeded" in entry, "and carries GitHub's own reason"


def test_the_degraded_line_reaches_the_run_log_naming_what_could_not_be_read(capsys):
    """Reporting what it could not read is the whole of the step's duty once
    the run is not red for it: one `DEGRADED:` line, in the run log, naming
    the listing and saying nothing was reported for it this sweep."""
    fake_run, _calls = _run_stub(_branches_refused(RATE_LIMITED))
    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), _no_sleep():
        reconcile.card_branches()
    out = capsys.readouterr()
    lines = [ln for ln in (out.out + out.err).splitlines() if ln.startswith("DEGRADED:")]
    assert len(lines) == 1, f"exactly one DEGRADED line, got {lines!r}"
    assert "branch listing" in lines[0]
    assert "reporting nothing this sweep" in lines[0]
    assert "rate limit" in lines[0].lower()


def test_a_permission_refusal_on_the_branch_listing_still_fails_the_run():
    """The other half of the operator's scope: a reason the step did NOT
    handle stays red. `403 Resource not accessible by integration` is a wrong
    token, and a wrong token must never leave the watchdog blind under green
    runs — that is the silent class DRE-4198 is about."""
    fake_run, _calls = _run_stub(_branches_refused(NOT_PERMITTED))
    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), _no_sleep():
        assert reconcile.card_branches() is None

    assert len(reconcile._write_failures) == 1, reconcile._write_failures
    assert "branch listing failed" in reconcile._write_failures[0]
    assert "Resource not accessible" in reconcile._write_failures[0]
    assert reconcile._degraded == [], "an unhandled reason is a failure, not a degrade"


def test_the_watchdog_reports_nothing_for_the_listing_it_could_not_read():
    """Degrading is not guessing: with the listing unreadable the watchdog
    posts nothing, on any card, and the hand-built half never runs (the
    DRE-2034 discipline test_unlanded_work_watchdog pins, re-asserted here
    through the real read seam rather than a stubbed `gh_read`)."""
    fake_run, _calls = _run_stub(_branches_refused(RATE_LIMITED))
    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), _no_sleep(), \
            mock.patch.object(reconcile, "_flag_hand_built_idle") as idle, \
            mock.patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        reconcile.flag_unlanded_work()
    idle.assert_not_called()
    comment.assert_not_called()
    assert len(reconcile._degraded) == 1
    assert reconcile._write_failures == []


# --------------------------------------------------------------------------
# The run: green when the only fault was handled, red when one was not
# --------------------------------------------------------------------------
_BACKSTOPS_STUBBED = (
    "drain_retiring_lanes", "unstick_conflicts", "refresh_stale_merge_refs",
    "retrigger_dead_heads", "flag_no_checks_prs", "flag_unowned_prs",
    "fix_approved_but_red", "retry_dead_fix_runs", "redispatch_standing_verdicts",
    "recover_limit_deaths", "restart_answered_blockers", "card_dependabot_prs",
    "review_dependabot_prs", "recover_crashed_reviews",
    "report_fleet_reviewer_outage", "check_dependabot_capacity",
    "report_intake_depth", "repair_frozen_planning_holds", "close_finished_epics",
    "report_break_glass", "report_fix_concurrency", "report_evicted_fix_runs",
)


def _sweep(answer_for, extra=None):
    """main() with every backstop but the unlanded watchdog stubbed, every
    `gh` answered by `answer_for`, and Linear answering an empty board."""
    mocks = {name: mock.MagicMock() for name in _BACKSTOPS_STUBBED}
    mocks.update({
        "flag_stranded": mock.MagicMock(return_value=set()),
        "promote_ready": mock.MagicMock(return_value=0),
        "report_epic_growth": mock.MagicMock(return_value=[]),
        "active_cards": mock.MagicMock(return_value=[]),
    })
    if extra:
        mocks.update(extra)
    fake_run, calls = _run_stub(answer_for)
    with mock.patch.multiple(reconcile, **mocks), \
            mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            _no_sleep(), \
            mock.patch.object(reconcile.linear_ops, "open_pass"), \
            mock.patch.object(reconcile.linear_ops, "cmd_comment"), \
            mock.patch.object(reconcile.linear_ops, "comment_bodies", return_value=[]):
        reconcile.main()
    return calls


def test_a_sweep_whose_only_fault_was_handled_exits_green(capsys):
    """The card's whole point. Every other phase ran; the branch listing was
    refused for quota and the watchdog said so and reported nothing. The run
    must NOT exit red — and must say, once, that a step degraded."""
    _sweep(_branches_refused(RATE_LIMITED))  # no SystemExit is the assertion

    assert reconcile._write_failures == []
    assert len(reconcile._degraded) == 1
    out = capsys.readouterr().out
    summary = [ln for ln in out.splitlines() if "degraded" in ln and ln.startswith("reconcile:")]
    assert len(summary) == 1, f"one summary line for the degraded steps, got {summary!r}"
    assert "1 step" in summary[0]


def test_a_clean_sweep_prints_no_degraded_summary(capsys):
    """Control: the run log of a sweep that read everything looks exactly as
    it does today — the summary line appears only when there is something
    to summarise."""
    def clean(argv):
        if argv[:2] == ["gh", "api"] and argv[2].endswith("/branches"):
            return 0, "", ""
        if argv[:3] == ["gh", "pr", "list"]:
            return 0, "[]", ""
        return 0, "", ""

    _sweep(clean)
    assert reconcile._degraded == []
    out = capsys.readouterr().out
    assert not [ln for ln in out.splitlines() if ln.startswith("DEGRADED:")]
    assert not [ln for ln in out.splitlines() if "degraded" in ln and ln.startswith("reconcile:")]


def test_a_fault_the_step_did_not_handle_still_exits_red():
    """The same sweep, but one branch's evaluation raises for a reason the
    watchdog has no answer to. That is a write failure, as it has been since
    DRE-2035, and the run exits 1 exactly as before — the degrade ledger has
    changed nothing about it."""
    def listing_readable(argv):
        if argv[:2] == ["gh", "api"] and argv[2].endswith("/branches"):
            return 0, BRANCH_LISTING, ""
        if argv[:3] == ["gh", "pr", "list"]:
            return 0, "[]", ""
        return 0, "", ""

    boom = mock.MagicMock(side_effect=RuntimeError("linear exploded"))
    with pytest.raises(SystemExit) as exc_info:
        _sweep(listing_readable, extra={
            "_flag_one_unlanded_branch": boom,
            "_flag_hand_built_idle": mock.MagicMock(),
        })
    assert boom.called, "the branch was evaluated, and its failure is the point"
    assert "1 write" in str(exc_info.value)
    assert reconcile._degraded == []


def test_the_degraded_ledger_alone_never_decides_the_exit():
    """Pinned directly: an entry on `_degraded` with the three failure ledgers
    empty is a green run; one entry on `_write_failures` is a red one. The
    exit reads the same three ledgers it read before this card."""
    def clean(argv):
        if argv[:3] == ["gh", "pr", "list"]:
            return 0, "[]", ""
        return 0, "", ""

    stubbed_watchdog = {"flag_unlanded_work": mock.MagicMock()}
    reconcile._degraded.append("a step degraded")
    _sweep(clean, extra=stubbed_watchdog)  # green

    _clear_ledgers()
    reconcile._write_failures.append("a write that did not happen")
    with pytest.raises(SystemExit):
        _sweep(clean, extra=stubbed_watchdog)


def test_the_promote_only_exit_reads_the_same_ledgers():
    """The event-driven gate has its own red-run decision; it, too, ignores
    the degraded ledger and still honours the write ledger."""
    with mock.patch.multiple(
        reconcile,
        active_cards=mock.MagicMock(return_value=[]),
        promote_ready=mock.MagicMock(return_value=0),
        merged_card_scope=mock.MagicMock(return_value=None),
    ), mock.patch.object(reconcile.linear_ops, "open_pass"):
        reconcile._degraded.append("a step degraded")
        reconcile.main(promote_only=True)  # green
        reconcile._write_failures.append("a write that did not happen")
        with pytest.raises(SystemExit):
            reconcile.main(promote_only=True)
