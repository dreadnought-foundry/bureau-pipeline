"""Intake is a pen the operator controls (DRE-3035).

The CEO's question, asked of a 209-card cutover: *"If we add all the cards, it
will just kick off a storm — what mechanism lets us control the inflow as we
start to turn on the pipes?"* Three things answer it. The groomer batch is the
valve he approves; PARKED is the per-card "stay still"; and this card closes the
two holes that make the age-out a storm rather than a trickle.

WHAT THESE TESTS PIN:

  * **`INTAKE_HOLD` is one switch and it closes the whole pen.** Set, neither
    `reconcile.escalate_aged_intake()` nor `groomer.drain()` moves a card, and
    each says so in one line per pass — the pen is VISIBLY closed rather than
    silently stuck. That distinction is the point: about 480 consecutive green
    sweeps once printed the exact reason five cards were frozen and nobody read
    one, so a hold that printed nothing would be a stall with an alibi.
  * **The age-out honours PARKED.** `escalate_aged_intake()` skipped only
    `hand_built`. PARKED is the vocabulary's own "deliberately not dispatchable,
    never reported as stalled", and a clock that pushes a PARKED card into the
    CEO's queue overrides a decision somebody made on purpose.
  * **The window and the cap are real inputs.** `reconcile.py` read
    `INTAKE_MAX_AGE_MINUTES` / `INTAKE_ESCALATION_CAP` from its environment and
    nothing ever set them, so the cutover ADR's "widen the window as a
    deliberate operator act" described a knob that did not exist. They are
    `workflow_call` inputs now, threaded verbatim, with an ABSENT input leaving
    the code default in force — a bare `int("")` on an unset input would have
    turned a window question into a red sweep (the `resolve_max_wip` lesson).
  * **Neither name is a bare env edit any more.** No workflow may assign any of
    the three a literal; the value comes from the caller's input or not at all.

Run: cd bureau-pipeline && python3 -m pytest tests/test_intake_pen.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import check_wip_cap  # noqa: E402
import groomer  # noqa: E402
import intake_controls  # noqa: E402
import lane_contract  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

from test_groomer_approval_gate import FakeOps, PROPOSAL_CARD, _approval, _proposal  # noqa: E402
from test_intake_escalation import _aged_batch, _card, _run  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
HOLD_SINCE = "2026-09-03"


# --------------------------------------------------------------------------
# 1: the switch itself — one reading of it, shared by both readers
# --------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [None, "", "   ", "false", "FALSE", "0", "no", "off"])
def test_the_pen_is_open_unless_the_operator_closes_it(raw):
    """Absent, empty and the spellings of "no" all mean OPEN. Empty matters
    most: an unset `workflow_call` input interpolates to the empty string on
    every event where the `inputs` context is empty, and a hold that read that
    as "closed" would stop the whole fleet's intake on a schedule event."""
    assert intake_controls.hold(raw) is None


def test_a_date_closes_the_pen_and_is_carried_verbatim():
    assert intake_controls.hold(HOLD_SINCE) == HOLD_SINCE


@pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes", "on"])
def test_a_bare_switch_closes_the_pen_with_no_date(raw):
    """The operator is asked for a date, and a bare `true` still holds — a
    switch that refused an unexpected value would be a hold that silently is
    not one."""
    assert intake_controls.hold(raw) == ""


def test_the_notice_names_the_switch_the_date_and_the_counts():
    line = intake_controls.notice(HOLD_SINCE, 209, "17 past the window")
    assert "\n" not in line, "one line per pass, so a held sweep stays readable"
    assert HOLD_SINCE in line
    assert "209 cards waiting" in line
    assert "17 past the window" in line
    assert intake_controls.ENV_HOLD in line, (
        "the line must name what to clear, or the pen is closed and nobody "
        "knows which switch opens it"
    )


def test_the_notice_says_so_when_no_date_was_given():
    line = intake_controls.notice("", 1, "0 past the window")
    assert "1 card waiting" in line
    assert "no date" in line, "absent is rendered as absent, never invented"


# --------------------------------------------------------------------------
# 2: the window and the cap resolve like the WIP cap — empty is the default
# --------------------------------------------------------------------------
def test_the_window_default_is_the_lane_contracts_own_number():
    """The number a reader finds in docs/lane-contract.md is the number that
    runs, and an absent input leaves exactly that in force."""
    contract = lane_contract.stale_minutes()["Intake"]
    assert intake_controls.max_age_minutes(None) == contract
    assert intake_controls.max_age_minutes("") == contract
    assert reconcile.INTAKE_MAX_AGE_MINUTES == contract


@pytest.mark.parametrize("raw", ["", "   ", None, "not-a-number"])
def test_an_absent_or_unparseable_cap_leaves_the_code_default_in_force(raw):
    assert intake_controls.escalation_cap(raw) == intake_controls.DEFAULT_CAP
    assert reconcile.INTAKE_ESCALATION_CAP == intake_controls.DEFAULT_CAP


def test_the_operator_can_actually_move_both_numbers():
    assert intake_controls.max_age_minutes("20160") == 20160
    assert intake_controls.escalation_cap("1") == 1


@pytest.mark.parametrize("raw", ["", "   ", None, "not-a-number"])
def test_an_absent_window_never_crashes_the_sweep(raw):
    """`int("")` would raise, and an unset input IS the empty string — that is
    a window question turned into a red run across the whole fleet."""
    assert intake_controls.max_age_minutes(raw) == lane_contract.stale_minutes()["Intake"]


# --------------------------------------------------------------------------
# 2b: the wire — env in, module constant out
# --------------------------------------------------------------------------
# Everything above tests the resolver, and everything below patches the module
# constant. Neither proves the ONE thing the workflow actually does: set an
# environment variable and start the process. A fresh interpreter is the only
# honest way to assert an import-time constant, so the wire gets its own test
# rather than being assumed by the two halves that surround it.
_PROBE = (
    "import json, reconcile, groomer;"
    "print(json.dumps({'hold': reconcile.INTAKE_HOLD,"
    " 'groomer_hold': groomer.INTAKE_HOLD,"
    " 'window': reconcile.INTAKE_MAX_AGE_MINUTES,"
    " 'cap': reconcile.INTAKE_ESCALATION_CAP}))"
)


def _probe(**env) -> dict:
    import json
    import subprocess

    child = dict(os.environ)
    child.update({"LINEAR_API_KEY": "test-key", "REPO": "test/test",
                  "REPO_SLUG": "test", "GH_TOKEN": "test"})
    for name in ("INTAKE_HOLD", "INTAKE_MAX_AGE_MINUTES", "INTAKE_ESCALATION_CAP"):
        child.pop(name, None)
    child.update(env)
    out = subprocess.run(  # nosec B603 — fixed argv, no shell
        [sys.executable, "-c", _PROBE], cwd=str(ROOT / "scripts"),
        env=child, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_environment_the_workflow_sets_reaches_the_constants_the_code_reads():
    got = _probe(INTAKE_HOLD=HOLD_SINCE, INTAKE_MAX_AGE_MINUTES="20160",
                 INTAKE_ESCALATION_CAP="1")
    assert got["hold"] == HOLD_SINCE
    assert got["groomer_hold"] == HOLD_SINCE, (
        "the drain read a different switch from the sweep — a pen with a hole"
    )
    assert got["window"] == 20160
    assert got["cap"] == 1


def test_an_absent_input_leaves_the_code_default_in_force():
    """The card's own criterion. An unset workflow input is the EMPTY STRING,
    not an absent variable, so both spellings are asserted."""
    for env in ({}, {"INTAKE_HOLD": "", "INTAKE_MAX_AGE_MINUTES": "",
                     "INTAKE_ESCALATION_CAP": ""}):
        got = _probe(**env)
        assert got["hold"] is None, f"the pen closed itself on {env!r}"
        assert got["groomer_hold"] is None
        assert got["window"] == lane_contract.stale_minutes()["Intake"]
        assert got["cap"] == intake_controls.DEFAULT_CAP


# --------------------------------------------------------------------------
# 3: the sweep's age-out honours the hold
# --------------------------------------------------------------------------
def test_a_held_intake_moves_no_card(monkeypatch):
    monkeypatch.setattr(reconcile, "INTAKE_HOLD", HOLD_SINCE)
    escalated, comment, advanced = _run(_aged_batch(4))
    assert escalated == set()
    comment.assert_not_called()
    assert not advanced.called, "the clock pushed a card past a closed pen"


def test_a_held_intake_says_so_once_per_pass(monkeypatch, capsys):
    """Visibly closed, not silently stuck: one line naming the date, how many
    cards are waiting and how many the clock would otherwise have taken."""
    monkeypatch.setattr(reconcile, "INTAKE_HOLD", HOLD_SINCE)
    _run(_aged_batch(4))
    lines = [
        line for line in capsys.readouterr().out.splitlines()
        if intake_controls.TAG in line
    ]
    assert len(lines) == 1, f"expected one hold line per pass, got {lines}"
    assert HOLD_SINCE in lines[0]
    assert "4 cards waiting" in lines[0]
    assert "4 past the window" in lines[0]


def test_a_bare_switch_holds_the_sweep_just_as_hard(monkeypatch):
    """`hold()` returns `""` for a dated-less switch, and `""` is FALSY. A
    reader written as `if INTAKE_HOLD:` would open the pen for exactly the
    operator who typed `true` — the check is against None and this pins it."""
    monkeypatch.setattr(reconcile, "INTAKE_HOLD", "")
    escalated, _comment, advanced = _run(_aged_batch(2))
    assert escalated == set()
    assert not advanced.called


def test_clearing_the_hold_resumes_the_age_out(monkeypatch):
    monkeypatch.setattr(reconcile, "INTAKE_HOLD", None)
    escalated, _comment, advanced = _run(_aged_batch(1))
    assert escalated == {"DRE-1"}
    advanced.assert_called_once_with("DRE-1", "Green Light", "Intake")


# --------------------------------------------------------------------------
# 4: the age-out honours PARKED
# --------------------------------------------------------------------------
def _parked(card: dict) -> dict:
    """The routing verdict, on the card, the way the board read returns it —
    `active_cards` selects `comments(last: 50)` inline (DRE-2929), so this
    costs the sweep no request at all."""
    body = routing_verdict.verdict_comment(
        "PARKED", "well-formed and deliberately not to be built this quarter")
    card["comments"] = {"nodes": [{"body": body}]}
    return card


def test_a_parked_intake_card_is_never_pushed_into_green_light_by_a_clock():
    """PARKED is the vocabulary's own 'deliberately not dispatchable, never
    reported as stalled'. A clock that overrides it un-parks a decision
    somebody made on purpose, and lands it in the queue the CEO reads."""
    escalated, comment, advanced = _run([_parked(_card())])
    assert escalated == set()
    comment.assert_not_called()
    advanced.assert_not_called()


def test_the_parked_skip_prints_the_same_reason_the_watchdog_prints(capsys):
    _run([_parked(_card())])
    out = capsys.readouterr().out
    assert "is routed PARKED" in out
    assert "deliberately not built" in out


def test_a_parked_card_does_not_consume_the_per_sweep_cap():
    """The cap holds cards it must still take. Spending a slot on a card that
    is never going to move would make the cap forget the ones that are."""
    cards = _aged_batch(reconcile.INTAKE_ESCALATION_CAP + 1)
    _parked(cards[0])
    escalated, _comment, _advanced = _run(cards)
    assert "DRE-1" not in escalated
    assert len(escalated) == reconcile.INTAKE_ESCALATION_CAP


def test_an_unparked_intake_card_still_ages_out():
    """Guard the guard: the skip must read the marker, not simply never fire."""
    escalated, _comment, _advanced = _run([_card()])
    assert escalated == {"DRE-2687"}


# --------------------------------------------------------------------------
# 5: the groomer's drain honours the same switch
# --------------------------------------------------------------------------
def test_a_held_drain_moves_nothing_even_with_a_valid_approval(monkeypatch):
    """The hold is checked BEFORE the approval, and before any write: an
    operator who closed the pen has said "not this week" about every batch,
    including one the CEO approved last week."""
    monkeypatch.setattr(groomer, "INTAKE_HOLD", HOLD_SINCE)
    proposal = _proposal()
    ops = FakeOps(comments=[_approval(proposal)])
    with pytest.raises(groomer.IntakeHeld) as exc:
        groomer.drain(ops, proposal, card=PROPOSAL_CARD)
    assert ops.state_writes == [], "cards left Intake through a closed pen"
    assert ops.mutations == [], "cycles were assigned through a closed pen"
    assert HOLD_SINCE in str(exc.value)
    assert intake_controls.ENV_HOLD in str(exc.value), (
        "the refusal must name the switch that would unblock it"
    )


def test_a_bare_switch_holds_the_drain_just_as_hard(monkeypatch):
    monkeypatch.setattr(groomer, "INTAKE_HOLD", "")
    proposal = _proposal()
    ops = FakeOps(comments=[_approval(proposal)])
    with pytest.raises(groomer.IntakeHeld):
        groomer.drain(ops, proposal, card=PROPOSAL_CARD)
    assert ops.state_writes == []


def test_clearing_the_hold_lets_the_approved_batch_drain(monkeypatch):
    monkeypatch.setattr(groomer, "INTAKE_HOLD", None)
    proposal = _proposal()
    ops = FakeOps(comments=[_approval(proposal)])
    result = groomer.drain(ops, proposal, card=PROPOSAL_CARD)
    assert result["moved"], "the pen is open and the approved batch stayed put"


def test_a_held_drain_exits_refused_rather_than_silently_doing_nothing(monkeypatch):
    """The CLI's refusal path: a drain the operator dispatched against a closed
    pen must report as refused, not as a successful run that moved zero cards."""
    monkeypatch.setattr(groomer, "INTAKE_HOLD", HOLD_SINCE)
    proposal = _proposal()
    ops = FakeOps(comments=[_approval(proposal)])
    monkeypatch.setattr(groomer, "_build", lambda args: proposal)
    # The whole write layer is the fake, so a regression that walks past the
    # hold fails as an assertion here rather than as a live Linear call.
    monkeypatch.setattr(groomer, "linear_ops", ops)
    assert groomer.main(["drain", "--card", PROPOSAL_CARD]) == 2
    assert ops.state_writes == []


# --------------------------------------------------------------------------
# 6: the knobs are workflow inputs — never a bare env edit (DRE-2692's shape)
# --------------------------------------------------------------------------
def _doc(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _live_docs() -> dict:
    docs = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict):
            docs[path.name] = doc
    return docs


def _call_inputs(doc) -> dict:
    call = check_wip_cap._on_block(doc).get("workflow_call") or {}
    return call.get("inputs") or {}


def _assignments(doc, name, variable):
    """Every place this workflow puts `variable` in an environment, as
    [(where, value)] — the same three scopes check_wip_cap reads for MAX_WIP."""
    found = []
    if variable in check_wip_cap._env(doc):
        found.append((f"{name}: env.{variable}", check_wip_cap._env(doc)[variable]))
    for job_id, job in (doc.get("jobs") or {}).items():
        if variable in check_wip_cap._env(job):
            found.append((f"{name}: jobs.{job_id}.env.{variable}",
                          check_wip_cap._env(job)[variable]))
    for job_id, i, step in check_wip_cap._steps(doc):
        if variable in check_wip_cap._env(step):
            found.append((f"{name}: jobs.{job_id}.steps[{i}].env.{variable}",
                          check_wip_cap._env(step)[variable]))
    return found


#: env var → the workflow_call input that must supply it.
KNOBS = {
    "INTAKE_HOLD": "intake_hold",
    "INTAKE_MAX_AGE_MINUTES": "intake_max_age_minutes",
    "INTAKE_ESCALATION_CAP": "intake_escalation_cap",
}


def test_the_reusable_sweep_declares_all_three_knobs_as_optional_inputs():
    inputs = _call_inputs(_doc("reconcile.yml"))
    for variable, name in KNOBS.items():
        assert name in inputs, (
            f"{name} is not a workflow_call input, so {variable} is still a "
            f"knob nobody outside this repo can turn"
        )
        assert inputs[name].get("type") == "string"
        assert inputs[name].get("required") is False


def test_an_omitted_window_or_cap_input_defaults_to_empty_not_to_a_number():
    """The default lives in ONE place — the lane contract for the window,
    `intake_controls.DEFAULT_CAP` for the cap. A number declared here too would
    be a second source that drifts the day the contract's does."""
    inputs = _call_inputs(_doc("reconcile.yml"))
    for name in ("intake_max_age_minutes", "intake_escalation_cap", "intake_hold"):
        assert inputs[name].get("default") == ""


def test_the_sweep_step_threads_every_knob_verbatim():
    doc = _doc("reconcile.yml")
    for variable, name in KNOBS.items():
        places = _assignments(doc, "reconcile.yml", variable)
        assert places, f"the sweep step never puts {variable} in the environment"
        for where, value in places:
            assert str(value).strip() == "${{ inputs.%s }}" % name, (
                f"{where} sets {variable} to {value!r} — the caller's input is "
                f"the only value it may carry"
            )


def test_the_groomer_takes_the_same_switch():
    doc = _doc("groomer.yml")
    assert "intake_hold" in _call_inputs(doc), (
        "the drain reads INTAKE_HOLD, so its caller must be able to set it"
    )
    places = _assignments(doc, "groomer.yml", "INTAKE_HOLD")
    assert places
    for _where, value in places:
        assert str(value).strip() == "${{ inputs.intake_hold }}"


def test_no_workflow_anywhere_hardcodes_one_of_the_knobs():
    """The acceptance criterion, as a check: the two names appear nowhere as a
    bare env edit any more. A literal here is a per-repo value baked into the
    shared channel, which is how one repo's cutover window becomes everyone's."""
    offences = []
    for name, doc in _live_docs().items():
        for variable, input_name in KNOBS.items():
            for where, value in _assignments(doc, name, variable):
                if str(value).strip() != "${{ inputs.%s }}" % input_name:
                    offences.append(f"{where} = {value!r}")
    assert offences == [], "; ".join(offences)


def test_the_guard_would_notice_a_hardcoded_knob():
    """Guard the guard: a checker that never fires reports ok forever."""
    doc = yaml.safe_load("""
    on: {workflow_call: {}}
    jobs:
      sweep:
        steps:
          - name: Sweep
            env:
              INTAKE_MAX_AGE_MINUTES: "20160"
            run: python3 .bureau-pipeline/scripts/reconcile.py
    """)
    found = _assignments(doc, "synthetic.yml", "INTAKE_MAX_AGE_MINUTES")
    assert [value for _w, value in found] == ["20160"]


def test_this_repos_own_stubs_carry_the_switch_as_data():
    """The pen is one switch per repo, and the operator sets it where the repo's
    own values live — the DRE-2692 canonical-guard shape: the stub is generated,
    the per-repo value is data. A stub that cannot pass it is a repo whose
    intake cannot be held."""
    for stub in ("self-reconcile.yml", "self-groomer.yml"):
        with_block = ((_doc(stub).get("jobs") or {}).get("call") or {}).get("with") or {}
        assert "intake_hold" in with_block, (
            f"{stub} passes no intake_hold — this repo's own intake cannot be held"
        )


def test_the_cutover_runbook_points_at_the_inputs_not_at_an_env_edit():
    """A change that contradicts a document updates that document in the same
    PR. The runbook told the operator to set two env vars on reconcile.py; the
    place to set them is the stub's inputs."""
    text = (ROOT / "docs" / "backlog-cutover.md").read_text()
    for name in KNOBS.values():
        assert name in text, f"the runbook never names the {name} input"
    assert "env overrides on `reconcile.py`" not in text, (
        "the runbook still sends the operator to a bare env edit"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
