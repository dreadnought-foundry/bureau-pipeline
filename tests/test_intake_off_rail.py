"""A sweep off the routing rail moves no Intake card, and says so once (DRE-3629).

WHAT WENT WRONG. `reconcile.escalate_aged_intake()` has no repo filter by
design — an Intake card carries no `repo:` label yet, so a filter would fire on
nothing — and every repo's sweep therefore reads the WHOLE lane and moves the
oldest three past the window into Green Light. That design assumed every sweep
was a production sweep. The sandbox repos run the same reusable `reconcile.yml`,
and on the night of 2026-09-09/10 sandbox sweeps helped age 130+ cards out of
Intake into the CEO's queue. The operator threw a dated `INTAKE_HOLD` on both
sandbox stubs that day; this is the durable fence that replaces it.

WHAT THESE TESTS PIN:

  * **`intake_controls.may_escalate(slug, rail)` is the fourth reading of the
    pen**, beside `hold`, `max_age_minutes` and `escalation_cap`, and it answers
    one question: may a sweep running as repo `slug` move a board card it does
    not own? The answer is `slug in rail`, and the rail is the bundled routing
    snapshot — iterated from `config/repo-map.json` itself here, never restated,
    because a list copied into a test is the copy that goes stale the day a
    customer is onboarded.
  * **The refusal lands BEFORE the Intake walk.** A refused sweep spends no read
    on the lane at all — `active_cards` is never called — which is the whole
    reason the guard is not a filter inside the walk.
  * **The refusal is a normal pass.** Never a write failure, never a read
    failure, never red: the harness reads a red sandbox sweep as a dead sandbox
    and blocks `main`'s proving run, so a fence that turned the sweep red would
    cost more than the thing it fences.
  * **It says so, once per pass**, opening with the `off-rail` tag, because a
    sweep that moved nothing and said nothing is indistinguishable from the
    stall it exists to prevent (the same rule `intake-hold` already follows).
  * **An on-rail sweep is untouched.** The walk still reads and moves unlabelled
    cards exactly as before — guard the guard, or the fence would pass by
    fencing everybody.

Run: cd bureau-pipeline && python3 -m pytest tests/test_intake_off_rail.py -v
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import intake_controls  # noqa: E402
import reconcile  # noqa: E402
import validate_card  # noqa: E402

from test_intake_escalation import _aged_batch, _card, _main_mocks, _run  # noqa: E402

#: The sandbox slug the incident was run under. It is not on the rail and must
#: never be — `bureau-harness` is the harness's own scratch repo, not a product.
OFF_RAIL = "bureau-harness"

#: The rail, read from the file the relay and the Todo gate route on. Iterated,
#: never restated: onboarding a customer is a data write to this snapshot, and a
#: slug list copied into a test is the copy that silently disagrees with it.
REPO_MAP = json.loads((ROOT / "config" / "repo-map.json").read_text())


# --------------------------------------------------------------------------
# 1: the predicate — the fourth reading of the pen
# --------------------------------------------------------------------------
def test_the_sandbox_is_not_on_the_routing_rail():
    """THE CRITERION. The sweep that aged 130+ cards out of Intake ran as this
    slug, and this is the one answer that stops it."""
    assert intake_controls.may_escalate(OFF_RAIL, validate_card.VALID_SLUGS) is False


@pytest.mark.parametrize("slug", sorted(REPO_MAP))
def test_every_repo_on_the_map_may_escalate(slug):
    """Every product repo keeps the age-out it has always had. The set is
    iterated from the real snapshot, so onboarding a repo extends this test
    rather than contradicting it."""
    assert intake_controls.may_escalate(slug, validate_card.VALID_SLUGS) is True


def test_the_rail_the_sweep_passes_is_the_bundled_snapshot():
    """`validate_card.VALID_SLUGS` IS the repo map's keys — the deterministic,
    network-free reading. A sweep must not spend a request to decide whether it
    may spend requests, which is why `live_rail_slugs()` is not the rail here."""
    assert validate_card.VALID_SLUGS == set(REPO_MAP)


def test_the_slug_is_matched_case_insensitively():
    """The rail holds lower-case slugs; `REPO_SLUG` is whatever the stub typed.
    A capital letter must not read as "off the rail" and silence a production
    sweep's age-out."""
    assert intake_controls.may_escalate("Agent-Bureau", validate_card.VALID_SLUGS) is True
    assert intake_controls.may_escalate("  agent-bureau  ", validate_card.VALID_SLUGS) is True


@pytest.mark.parametrize("rail", [None, set(), (), [], {}])
def test_an_empty_rail_refuses_and_is_a_caller_defect(rail):
    """The caller passes the bundled snapshot, which is never empty. If it ever
    is, the safe answer is the one that moves no card — an empty rail read as
    "everything is allowed" is the incident again with no fence at all."""
    assert intake_controls.may_escalate("agent-bureau", rail) is False


@pytest.mark.parametrize("slug", [None, "", "   "])
def test_a_missing_slug_is_off_the_rail(slug):
    assert intake_controls.may_escalate(slug, validate_card.VALID_SLUGS) is False


def test_the_predicate_reads_no_environment(monkeypatch):
    """Pure by contract: the caller names the slug and the rail. A predicate
    that reached for `REPO_SLUG` itself could not be asked about a repo other
    than the one the process is running as — which is exactly what the wave's
    third epic will need when it narrows this to the age-out OWNER."""
    monkeypatch.setenv("REPO_SLUG", "agent-bureau")
    monkeypatch.setenv(intake_controls.ENV_HOLD, "2026-09-10")
    assert intake_controls.may_escalate(OFF_RAIL, validate_card.VALID_SLUGS) is False
    monkeypatch.setenv("REPO_SLUG", OFF_RAIL)
    assert intake_controls.may_escalate("agent-bureau", validate_card.VALID_SLUGS) is True


# --------------------------------------------------------------------------
# 2: the notice — one line, tagged, naming the slug and what it declined
# --------------------------------------------------------------------------
def test_the_tag_is_named_once_and_opens_the_notice():
    """The sibling card that fences the sweep's other three writers prints the
    SAME line, so the tag is a constant rather than a string in two modules."""
    assert intake_controls.TAG_OFF_RAIL == "off-rail"
    line = intake_controls.off_rail_notice(OFF_RAIL, "the Intake age-out")
    assert line.startswith(f"{intake_controls.TAG_OFF_RAIL}: ")


def test_the_notice_is_one_line_naming_the_slug_and_the_detail():
    line = intake_controls.off_rail_notice(OFF_RAIL, "the Intake age-out")
    assert "\n" not in line, "one line per pass, so a refused sweep stays readable"
    assert OFF_RAIL in line, "a refusal that does not name the repo names nobody"
    assert "the Intake age-out" in line, (
        "the caller's own words for what it declined — each writer knows a "
        "different true answer and a shared guess would be worse than four "
        "accurate ones"
    )
    assert "routing rail" in line
    assert "does not own" in line, (
        "the line must say WHY it refused — this sweep moves no board card it "
        "does not own — or it reads as a failure rather than a fence"
    )


def test_the_notice_carries_a_second_callers_detail_verbatim():
    """Guard the guard: `detail` is interpolated, not decoration."""
    line = intake_controls.off_rail_notice(OFF_RAIL, "the stranded-card watchdog")
    assert "the stranded-card watchdog" in line
    assert "the Intake age-out" not in line


# --------------------------------------------------------------------------
# 3: the sweep's age-out asks the predicate, before the walk
# --------------------------------------------------------------------------
def _off_rail_run(cards, slug=OFF_RAIL):
    """Run escalate_aged_intake() as `slug`. Returns
    (escalated, active_cards mock, cmd_comment mock, cmd_advance mock)."""
    reconcile._write_failures.clear()
    with mock.patch.object(reconcile, "REPO_SLUG", slug), mock.patch.object(
        reconcile, "active_cards", return_value=list(cards)
    ) as active, mock.patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=[]
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as comment, mock.patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as advanced:
        escalated = reconcile.escalate_aged_intake()
    return escalated, active, comment, advanced


def test_an_off_rail_sweep_moves_no_intake_card():
    escalated, _active, comment, advanced = _off_rail_run(_aged_batch(4))
    assert escalated == set()
    comment.assert_not_called()
    assert not advanced.called, "a sandbox sweep moved a card it does not own"


def test_an_off_rail_sweep_spends_no_read_on_the_lane():
    """The guard lands BEFORE the walk, which is why it is a guard and not a
    filter inside it: a refused pass costs the sandbox nothing at all."""
    _escalated, active, _comment, _advanced = _off_rail_run(_aged_batch(4))
    assert not active.called, (
        "the refused sweep still read the Intake lane — the guard is inside the "
        "walk rather than in front of it"
    )


def test_an_off_rail_sweep_says_so_exactly_once_per_pass(capsys):
    _off_rail_run(_aged_batch(4))
    lines = [
        line for line in capsys.readouterr().out.splitlines()
        if intake_controls.TAG_OFF_RAIL in line
    ]
    assert len(lines) == 1, f"expected one off-rail line per pass, got {lines}"
    assert lines[0].startswith(f"{intake_controls.TAG_OFF_RAIL}: ")
    assert OFF_RAIL in lines[0]


def test_the_refusal_is_never_a_write_failure():
    """A refusal is a normal pass. The harness reads a red sandbox sweep as a
    dead sandbox and blocks `main`'s proving run."""
    _off_rail_run(_aged_batch(4))
    assert reconcile._write_failures == []
    assert reconcile._read_failures == []


def test_an_on_rail_sweep_still_ages_an_unlabelled_card_out():
    """Guard the guard. The walk is unchanged for a production sweep: an Intake
    card carries no `repo:` label and is moved exactly as it always was."""
    escalated, _comment, advanced = _run([_card()])
    assert escalated == {"DRE-2687"}
    advanced.assert_called_once_with("DRE-2687", "Green Light", "Intake")


def test_an_on_rail_sweep_prints_no_off_rail_line(capsys):
    _run([_card()])
    out = capsys.readouterr().out
    assert intake_controls.TAG_OFF_RAIL not in out


@pytest.mark.parametrize("slug", sorted(REPO_MAP))
def test_every_product_repos_sweep_still_reads_the_lane(slug):
    """The fence is a sandbox fence, not a fleet one: every repo on the rail
    keeps the age-out, and the third epic narrows this predicate later."""
    _escalated, active, _comment, _advanced = _off_rail_run([], slug=slug)
    assert active.called, f"{slug} is on the rail and its sweep stopped reading Intake"


# --------------------------------------------------------------------------
# 4: a full sweep off the rail is GREEN
# --------------------------------------------------------------------------
def test_a_full_sweep_off_the_rail_reaches_the_age_out_and_exits_zero(capsys):
    """THE CRITERION. `main()` runs the age-out for real — nothing stubs it —
    and the refusal never lands on `_write_failures` or `_read_failures`, so the
    run exits 0 on that path."""
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(reconcile, "REPO_SLUG", OFF_RAIL))
        for m in _main_mocks():
            stack.enter_context(m)
        stack.enter_context(
            mock.patch.object(reconcile, "active_cards", return_value=[])
        )
        reconcile.main()  # no SystemExit: a refusal is a normal pass
    assert reconcile._write_failures == []
    assert reconcile._read_failures == []
    lines = [
        line for line in capsys.readouterr().out.splitlines()
        if intake_controls.TAG_OFF_RAIL in line
    ]
    assert len(lines) == 1, f"expected one off-rail line per sweep, got {lines}"


# --------------------------------------------------------------------------
# 5: the record — the module says four readings, the runbook names the fourth
# --------------------------------------------------------------------------
def test_the_module_docstring_says_four_readings_not_three():
    """A change that contradicts a document updates it in the same PR — and the
    module's own docstring is the first document this one contradicts."""
    doc = (intake_controls.__doc__ or "").lower()
    assert "four" in doc, "the pen still describes three knobs and has four"
    assert intake_controls.TAG_OFF_RAIL in doc


def test_the_cutover_runbook_names_the_off_rail_refusal():
    text = (ROOT / "docs" / "backlog-cutover.md").read_text()
    assert intake_controls.TAG_OFF_RAIL in text, (
        "the controls table names three controls and the lane has four"
    )
    assert OFF_RAIL in text or "sandbox" in text.lower()


def test_the_age_out_docstring_says_a_sandbox_is_refused_at_the_door():
    """"NO REPO FILTER, deliberately" is still true for a production sweep, and
    a reader who stops there would conclude a sandbox sweep moves cards."""
    doc = reconcile.escalate_aged_intake.__doc__ or ""
    assert "NO REPO FILTER, deliberately" in doc
    assert intake_controls.TAG_OFF_RAIL in doc


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
