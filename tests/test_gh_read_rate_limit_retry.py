"""RED-first tests: a BRIEF rate-limit refusal is a retry, not a red sweep
(DRE-4109).

THE INCIDENT (2026-09-16, twice in a row, and on portico seven minutes earlier
as DRE-4107): the unlanded watchdog's branch listing —
`gh api repos/<repo>/branches --paginate` through `gh_read` — came back
`HTTP 403: API rate limit exceeded for installation ID 123249480`. `gh_read`
raised on the first refusal, `card_branches` recorded it in the write-failure
ledger, the sweep exited 1, the medic fired, and a pipeline-failure card was
filed. Every other phase of that sweep had run normally: one throttled read
from a SHARED App installation read as a broken pipeline.

THE CEO'S ANSWER (signed console answer, 2026-09-16 19:51 PT): "Retry quietly,
and only tell me if it persists. A brief refusal from an outside service is a
retry, not a failure... Say in the run log each time a request was retried, so
the pattern is visible without a card."

FIX UNDER TEST — all of it inside `gh_read`, the sweep's loud read helper, and
nowhere else:
  - a non-zero `gh` exit whose stderr carries GitHub's rate-limit text
    (`API rate limit exceeded`, `secondary rate limit`, an HTTP 429) is
    retried: three attempts in all, waiting ~15s then ~45s — under a minute,
    inside the sweep's 10-minute job timeout;
  - every retry prints ONE line to the run log naming the `gh` command and
    `attempt N of 3`, so the pattern is visible without a card;
  - a refusal on every attempt raises `ReconcileReadError` exactly as today
    (since DRE-4214 the `ReconcileRateLimited` subclass, so the caller can
    tell the fault it handles from one it does not);
  - anything that is NOT a rate-limit refusal — `403 Resource not accessible
    by integration`, a 404, a network error — raises AT ONCE, with no retry
    and no wait. Retrying a permission failure only burns quota.

Nothing else moves: `gh()` keeps its own fallbacks, `gh_actions_read()` /
`_actions_read()` keep their "answer None and fail closed" contract under a
DIFFERENT token's quota, and the sweep asks for the data exactly as often as
it does today (the freshness is worth more than the occasional blip).

Run: cd bureau-pipeline && python3 -m pytest tests/test_gh_read_rate_limit_retry.py -v
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

#: The stderr GitHub actually answered on 2026-09-16, verbatim from the card.
RATE_LIMITED = (
    "gh: HTTP 403: API rate limit exceeded for installation ID 123249480 "
    "(https://api.github.com/repos/dreadnought-foundry/bureau-pipeline/branches)"
)
#: The OTHER 403 — a permission failure, which no amount of waiting fixes.
NOT_PERMITTED = "gh: HTTP 403: Resource not accessible by integration"

BRANCH_LISTING = "\n".join([
    json.dumps({"name": "agent/DRE-4109-quiet-retry", "sha": "a" * 40}),
    json.dumps({"name": "main", "sha": "b" * 40}),
])


@pytest.fixture(autouse=True)
def _clean_failure_state(monkeypatch):
    """Pin REPO/REPO_SLUG (bound at import — the same collection-order hazard
    test_reconcile_pr_lookup guards against) and clear the sweep's ledgers
    before AND after, so a leftover failure cannot turn another sweep red."""
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/bureau-pipeline")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    monkeypatch.setattr(reconcile, "_gh_read_retries_spent", 0, raising=False)
    _clear_ledgers()
    yield
    _clear_ledgers()


def _clear_ledgers() -> None:
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    degraded = getattr(reconcile, "_degraded", None)  # DRE-4214's ledger
    if degraded is not None:
        degraded.clear()


def _run_stub(answers):
    """subprocess.run stub answering `answers` in order, recording each argv.

    Each answer is (returncode, stdout, stderr). The last answer repeats, so a
    test can say "refused forever" with one entry.
    """
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        rc, out, err = answers[min(len(calls) - 1, len(answers) - 1)]
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    return fake_run, calls


def _sleep_stub():
    """time.sleep stub recording every wait instead of taking it."""
    waits: list[float] = []
    return (lambda seconds: waits.append(seconds)), waits


def _retry_lines(captured: str) -> list[str]:
    """Every run-log line that announces a retry."""
    return [ln for ln in captured.splitlines() if "attempt" in ln and "of 3" in ln]


# --------------------------------------------------------------------------
# The card's headline case: the watchdog's branch listing, refused once
# --------------------------------------------------------------------------
def test_card_branches_survives_one_rate_limit_refusal(capsys):
    """REPLICATION of DRE-4109. The branch listing is refused once and answers
    on the retry: `card_branches` must return the listing, the write-failure
    ledger must stay EMPTY (nothing failed — the sweep stays green and no card
    is filed), and exactly one retry line must reach the run log.

    On the unfixed code this FAILS: gh_read raises on the first refusal,
    card_branches returns None and records a write failure."""
    fake_run, calls = _run_stub([
        (1, "", RATE_LIMITED),
        (0, BRANCH_LISTING, ""),
    ])
    sleep, waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        branches = reconcile.card_branches()

    assert branches is not None, "a brief refusal must not read as unreadable"
    assert [b["name"] for b in branches] == ["agent/DRE-4109-quiet-retry"]
    assert reconcile._write_failures == [], (
        "a retried-and-answered read is not a failure — the ledger that turns "
        "the sweep red and files a card must stay empty"
    )
    assert len(calls) == 2, "one refusal, one retry, one answer"
    assert waits == [15], "the first backoff is ~15s"

    out = capsys.readouterr()
    lines = _retry_lines(out.out + out.err)
    assert len(lines) == 1, f"exactly one retry line, got {lines!r}"
    assert "attempt 1 of 3" in lines[0]
    assert "repos/dreadnought-foundry/bureau-pipeline/branches" in lines[0], (
        "the line must name the gh command that was retried"
    )


# --------------------------------------------------------------------------
# gh_read itself: how many, how long, and what the log says
# --------------------------------------------------------------------------
def test_three_attempts_with_backoff_under_a_minute():
    """Two refusals then an answer: three attempts at most, waiting 15s then
    45s — a minute of waiting sits well inside the sweep's 10-minute job
    timeout."""
    fake_run, calls = _run_stub([
        (1, "", RATE_LIMITED),
        (1, "", RATE_LIMITED),
        (0, "the listing", ""),
    ])
    sleep, waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        assert reconcile.gh_read("api", "repos/o/r/branches") == "the listing"

    assert len(calls) == 3
    assert waits == [15, 45], "the card's own numbers: about 15s, then 45s"
    # The card names both the two waits and their size ("under a minute of
    # waiting, inside the sweep's 10-minute job timeout"); 15 + 45 is the
    # card's own arithmetic, so the bound asserted here is a minute at most.
    # What the budget has to fit inside is the job timeout, and 60s of waiting
    # leaves nine minutes of it.
    assert sum(waits) <= 60, "the whole retry budget is a minute at most"


def test_every_retry_prints_one_line_naming_the_command_and_the_attempt(capsys):
    """The CEO asked for the pattern to be visible without a card: one line
    per retry, naming the `gh` command and `attempt N of 3`."""
    fake_run, _ = _run_stub([
        (1, "", RATE_LIMITED),
        (1, "", RATE_LIMITED),
        (0, "the listing", ""),
    ])
    sleep, _waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        reconcile.gh_read("api", "repos/o/r/branches", "--paginate")

    out = capsys.readouterr()
    lines = _retry_lines(out.out + out.err)
    assert len(lines) == 2, f"one line per retry, got {lines!r}"
    assert "attempt 1 of 3" in lines[0] and "attempt 2 of 3" in lines[1]
    for line in lines:
        assert "gh api repos/o/r/branches --paginate" in line


def test_a_secondary_rate_limit_is_also_a_brief_refusal():
    """GitHub's other throttle wording. Same treatment."""
    fake_run, calls = _run_stub([
        (1, "", "gh: You have exceeded a secondary rate limit"),
        (0, "the listing", ""),
    ])
    sleep, _waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        assert reconcile.gh_read("api", "repos/o/r/branches") == "the listing"
    assert len(calls) == 2


def test_an_http_429_is_also_a_brief_refusal():
    fake_run, calls = _run_stub([
        (1, "", "gh: HTTP 429: Too Many Requests"),
        (0, "the listing", ""),
    ])
    sleep, _waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        assert reconcile.gh_read("api", "repos/o/r/branches") == "the listing"
    assert len(calls) == 2


# --------------------------------------------------------------------------
# What is NOT retried
# --------------------------------------------------------------------------
def test_a_permission_403_raises_at_once_with_no_retry(capsys):
    """`403 Resource not accessible by integration` is a permission failure —
    no amount of waiting fixes it, and retrying only burns quota. It must
    raise on the FIRST answer, exactly as today, with nothing slept and no
    retry line in the log."""
    fake_run, calls = _run_stub([(1, "", NOT_PERMITTED)])
    sleep, waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        with pytest.raises(reconcile.ReconcileReadError) as exc_info:
            reconcile.gh_read("api", "repos/o/r/branches")

    assert len(calls) == 1, "a permission failure is not retried"
    assert waits == [], "and nothing is slept for it"
    assert "Resource not accessible" in str(exc_info.value), (
        "the error must still carry the gh stderr"
    )
    out = capsys.readouterr()
    assert _retry_lines(out.out + out.err) == []


def test_a_404_raises_at_once_with_no_retry():
    fake_run, calls = _run_stub([(1, "", "gh: Not Found (HTTP 404)")])
    sleep, waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        with pytest.raises(reconcile.ReconcileReadError):
            reconcile.gh_read("api", "repos/o/r/branches")
    assert len(calls) == 1 and waits == []


# --------------------------------------------------------------------------
# It still fails — and still files — when the refusals PERSIST
# --------------------------------------------------------------------------
def test_a_persisting_rate_limit_still_raises_after_three_attempts():
    """"Only tell me if it persists." An hourly bucket that is genuinely empty
    will not clear in under a minute: that run still raises
    ReconcileReadError, so the sweep exits 1 and the medic files a card —
    unchanged from today."""
    fake_run, calls = _run_stub([(1, "", RATE_LIMITED)])
    sleep, waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        with pytest.raises(reconcile.ReconcileReadError) as exc_info:
            reconcile.gh_read("api", "repos/o/r/branches")

    assert len(calls) == 3, "three attempts in all, then it is a failure"
    assert waits == [15, 45]
    assert "rate limit exceeded" in str(exc_info.value)


def test_card_branches_degrades_rather_than_fails_when_it_persists():
    """A REAL outage of the bucket: card_branches still answers None (an
    unreadable listing is never an empty one), and the watchdog reports
    nothing this sweep. What changed with DRE-4214 (the operator's scope on
    that card, 2026-09-18, after six red sweeps in two hours for this exact
    reason): the refusal is a fault the step HANDLED, so it goes on the
    degraded ledger and not the write-failure one — the run says what it
    could not read and does not exit red for it. An unhandled reason (a
    permission 403) still lands on `_write_failures`: see
    tests/test_degraded_step_is_not_a_red_run.py."""
    fake_run, _calls = _run_stub([(1, "", RATE_LIMITED)])
    sleep, _waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        assert reconcile.card_branches() is None

    assert reconcile._write_failures == [], "a handled fault is not a failure"
    assert len(reconcile._degraded) == 1
    assert "branch listing" in reconcile._degraded[0]


# --------------------------------------------------------------------------
# The retry is bounded PER SWEEP too (premortem Q3/Q5, DRE-1921)
# --------------------------------------------------------------------------
def test_the_sweep_stops_retrying_once_its_retry_budget_is_spent(capsys):
    """Every retry at a vendor boundary is bounded (standards/vendor-boundaries
    Q5, the DRE-1921 quota-burn class). Per-read it is three attempts; per
    SWEEP there is a budget too, because `pr_for` runs once PER CARD and a
    genuinely empty bucket refuses all of them. Unbounded, a dozen refused
    reads would spend a dozen minutes of waiting and the 10-minute job timeout
    would KILL the sweep — turning a loud, fast, correctly-filed failure into a
    hung run that never prints its ledger. Past the budget, gh_read behaves
    exactly as it did before this card: raise on the first refusal."""
    fake_run, calls = _run_stub([(1, "", RATE_LIMITED)])
    sleep, waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        # Spend the budget on reads that are refused all the way down.
        while len(waits) < reconcile.GH_READ_RETRY_BUDGET:
            with pytest.raises(reconcile.ReconcileReadError):
                reconcile.gh_read("api", "repos/o/r/branches")
        spent_calls = len(calls)
        with pytest.raises(reconcile.ReconcileReadError):
            reconcile.gh_read("api", "repos/o/r/branches")

    assert len(waits) == reconcile.GH_READ_RETRY_BUDGET, (
        "the sweep never waits more than its whole-sweep budget"
    )
    assert len(calls) == spent_calls + 1, (
        "past the budget a refused read costs ONE request, as it did before"
    )
    assert sum(waits) <= 180, "the worst case stays minutes, not the job timeout"
    out = capsys.readouterr()
    assert "retry budget" in (out.out + out.err), (
        "and the run log says why the retrying stopped"
    )


def test_the_retry_budget_is_not_spent_by_reads_that_answer():
    """A blip that clears costs one retry; a sweep of clean reads costs none,
    so the budget is there for the sweep that needs it."""
    fake_run, _calls = _run_stub([(0, "the listing", "")])
    sleep, waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        for _ in range(20):
            reconcile.gh_read("api", "repos/o/r/branches")

    assert waits == []
    assert reconcile._gh_read_retries_spent == 0


# --------------------------------------------------------------------------
# The seams the card says must NOT move
# --------------------------------------------------------------------------
def test_the_silent_gh_helper_does_not_retry():
    """`gh()` has its own fallbacks by design — one call, whatever happens."""
    fake_run, calls = _run_stub([(1, "", RATE_LIMITED)])
    sleep, waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        assert reconcile.gh("api", "repos/o/r/branches") == ""
    assert len(calls) == 1 and waits == []


def test_the_actions_read_helper_does_not_retry(monkeypatch):
    """`gh_actions_read` runs under GH_DISPATCH_TOKEN — a DIFFERENT GitHub
    quota from the App installation that was refused — and its contract is
    "answer None and fail closed", not "raise". One call, no waiting."""
    monkeypatch.setenv("GH_DISPATCH_TOKEN", "dispatch-token")
    fake_run, calls = _run_stub([(1, "", RATE_LIMITED)])
    sleep, waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        assert reconcile.gh_actions_read("run", "list") is None
    assert len(calls) == 1 and waits == []


def test_a_clean_read_never_sleeps_and_never_logs():
    """Control: the overwhelming majority of reads answer first time, and the
    run log must look exactly as it does today for them."""
    fake_run, calls = _run_stub([(0, "the listing", "")])
    sleep, waits = _sleep_stub()

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(reconcile.time, "sleep", side_effect=sleep):
        assert reconcile.gh_read("api", "repos/o/r/branches") == "the listing"
    assert len(calls) == 1 and waits == []
