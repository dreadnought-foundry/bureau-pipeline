"""Intake is a pen the operator controls (DRE-3035, narrowed by DRE-4141).

The CEO's question, asked of a 209-card cutover: *"If we add all the cards, it
will just kick off a storm — what mechanism lets us control the inflow as we
start to turn on the pipes?"* Two things answer it now. The groomer batch is the
valve he approves, and PARKED is the per-card "stay still".

WHAT DRE-4141 TOOK OUT OF THIS FILE. The third answer used to be the sweep's
age-out, and the two holes this card closed were holes in it: the hold it
ignored and the PARKED card it overrode. On the CEO's signed console answer of
2026-09-17 there is no age-out at all — no card leaves Intake because it is old
— so a hold over it, a window on it and a cap under it are all switches on a
thing that does not happen. `tests/test_intake_no_age_out.py` owns that half.

WHAT THESE TESTS PIN:

  * **`INTAKE_HOLD` is one switch and it closes the pen.** Set, `groomer.drain`
    moves no card and says so in one line per pass — the pen is VISIBLY closed
    rather than silently stuck. That distinction is the point: about 480
    consecutive green sweeps once printed the exact reason five cards were
    frozen and nobody read one, so a hold that printed nothing would be a stall
    with an alibi.
  * **ONE READER, and it is the drain.** The drain is the one thing that moves
    a card out of Intake, so the switch that holds it holds the lane. The sweep
    does not read it, because the sweep moves nothing.
  * **The switch is a real input, never a bare env edit.** It is a
    `workflow_call` input on `groomer.yml`, threaded verbatim, with an ABSENT
    input leaving the pen OPEN — an unset input is the EMPTY STRING, and a hold
    that read that as "closed" would stop the fleet's intake on a schedule
    event.

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
import reconcile  # noqa: E402

from test_groomer_approval_gate import FakeOps, PROPOSAL_CARD, _proposal, _thread  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
HOLD_SINCE = "2026-09-03"


# --------------------------------------------------------------------------
# 1: the switch itself — one reading of it, one reader left
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
    line = intake_controls.notice(HOLD_SINCE, 209, "17 in the approved batch")
    assert "\n" not in line, "one line per pass, so a held pass stays readable"
    assert HOLD_SINCE in line
    assert "209 cards waiting" in line
    assert "17 in the approved batch" in line
    assert intake_controls.ENV_HOLD in line, (
        "the line must name what to clear, or the pen is closed and nobody "
        "knows which switch opens it"
    )


def test_the_notice_says_so_when_no_date_was_given():
    line = intake_controls.notice("", 1, "0 in the approved batch")
    assert "1 card waiting" in line
    assert "no date" in line, "absent is rendered as absent, never invented"


# --------------------------------------------------------------------------
# 2: the wire — env in, module constant out
# --------------------------------------------------------------------------
# Everything above tests the resolver, and everything below patches the module
# constant. Neither proves the ONE thing the workflow actually does: set an
# environment variable and start the process. A fresh interpreter is the only
# honest way to assert an import-time constant, so the wire gets its own test
# rather than being assumed by the two halves that surround it.
#
# ONE constant since DRE-4141. `reconcile.INTAKE_HOLD` was the other, and the
# probe asserted the two agreed because a switch two readers interpret
# separately is a pen with a hole in it. The sweep is not a reader any more —
# it moves no card out of Intake — so the probe asserts its ABSENCE instead: a
# constant nothing consults is the same hole with the light off.
_PROBE = (
    "import json, reconcile, groomer;"
    "print(json.dumps({'groomer_hold': groomer.INTAKE_HOLD,"
    " 'sweep_reads_the_hold': hasattr(reconcile, 'INTAKE_HOLD')}))"
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


def test_the_environment_the_workflow_sets_reaches_the_constant_the_code_reads():
    got = _probe(INTAKE_HOLD=HOLD_SINCE)
    assert got["groomer_hold"] == HOLD_SINCE
    assert got["sweep_reads_the_hold"] is False, (
        "the sweep read the pen's switch — it moves nothing out of Intake, so "
        "a switch it consults is a move somebody could put back"
    )


def test_an_absent_input_leaves_the_pen_open():
    """The card's own criterion. An unset workflow input is the EMPTY STRING,
    not an absent variable, so both spellings are asserted."""
    for env in ({}, {"INTAKE_HOLD": ""}):
        got = _probe(**env)
        assert got["groomer_hold"] is None, f"the pen closed itself on {env!r}"


# --------------------------------------------------------------------------
# 3: the groomer's drain honours the switch — the one exit from Intake
# --------------------------------------------------------------------------
def test_a_held_drain_moves_nothing_even_with_a_valid_approval(monkeypatch):
    """The hold outranks the approval, and lands before any write: an operator
    who closed the pen has said "not this week" about every batch, including
    one the CEO approved last week."""
    monkeypatch.setattr(groomer, "INTAKE_HOLD", HOLD_SINCE)
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal))
    with pytest.raises(groomer.IntakeHeld) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == [], "cards left Intake through a closed pen"
    assert ops.mutations == [], "cycles were assigned through a closed pen"
    # Since DRE-3370 a held drain WRITES — one line saying it refused, on the
    # card the CEO approved on, because a refusal said only in a workflow log
    # is a stall with an alibi. What it must never write is a record of a move.
    assert len(ops.written) == 1, "a held drain left no record of its refusal"
    body = ops.written[0][1]
    assert body.startswith(f"{groomer.MARK} {groomer.DRAIN_REFUSED_TAG}: ")
    assert groomer.DRAINED_TAG not in body, (
        "a held drain wrote a record of a move it refused"
    )
    assert HOLD_SINCE in body, "the refusal on the card does not name the hold"
    assert HOLD_SINCE in str(exc.value)
    assert intake_controls.ENV_HOLD in str(exc.value), (
        "the refusal must name the switch that would unblock it"
    )


def test_a_held_drain_still_refuses_an_unapproved_batch_as_held(monkeypatch):
    """The hold is the operator's answer about the LANE, so it is read before
    the approval verdict is acted on — a drain against a closed pen reports the
    pen, whatever the thread says about approval."""
    monkeypatch.setattr(groomer, "INTAKE_HOLD", HOLD_SINCE)
    ops = FakeOps(comments=[{"body": "nice", "authored_by_pipeline": False}])
    with pytest.raises(groomer.IntakeHeld):
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == []


def test_a_bare_switch_holds_the_drain_just_as_hard(monkeypatch):
    monkeypatch.setattr(groomer, "INTAKE_HOLD", "")
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal))
    with pytest.raises(groomer.IntakeHeld):
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == []


def test_clearing_the_hold_lets_the_approved_batch_drain(monkeypatch):
    monkeypatch.setattr(groomer, "INTAKE_HOLD", None)
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"], "the pen is open and the approved batch stayed put"


def test_a_held_drain_exits_refused_rather_than_silently_doing_nothing(monkeypatch):
    """The CLI's refusal path: a drain the operator dispatched against a closed
    pen must report as refused, not as a successful run that moved zero cards."""
    monkeypatch.setattr(groomer, "INTAKE_HOLD", HOLD_SINCE)
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal))
    # The whole write layer is the fake, so a regression that walks past the
    # hold fails as an assertion here rather than as a live Linear call.
    monkeypatch.setattr(groomer, "linear_ops", ops)
    assert groomer.main(["drain", "--card", PROPOSAL_CARD]) == 2
    assert ops.state_writes == []


# --------------------------------------------------------------------------
# 4: the switch is a workflow input — never a bare env edit (DRE-2692's shape)
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


#: env var → the workflow_call input that must supply it. ONE row since
#: DRE-4141: the window and the cap bounded a move that no longer happens, and
#: `tests/test_intake_no_age_out.py` pins their retirement.
KNOBS = {
    "INTAKE_HOLD": "intake_hold",
}


def test_the_groomer_declares_the_switch_as_an_optional_input():
    inputs = _call_inputs(_doc("groomer.yml"))
    for variable, name in KNOBS.items():
        assert name in inputs, (
            f"{name} is not a workflow_call input, so {variable} is still a "
            f"knob nobody outside this repo can turn"
        )
        assert inputs[name].get("type") == "string"
        assert inputs[name].get("required") is False


def test_an_omitted_switch_defaults_to_empty_which_is_open():
    """Empty is load-bearing: an unset `workflow_call` input interpolates to
    the empty string on every event where the `inputs` context is empty, and a
    default of anything else would close the fleet's pen on a schedule event."""
    assert _call_inputs(_doc("groomer.yml"))["intake_hold"].get("default") == ""


def test_the_groomer_threads_the_switch_verbatim():
    doc = _doc("groomer.yml")
    places = _assignments(doc, "groomer.yml", "INTAKE_HOLD")
    assert places, "the drain step never puts INTAKE_HOLD in the environment"
    for _where, value in places:
        assert str(value).strip() == "${{ inputs.intake_hold }}"


def test_the_sweep_threads_no_intake_knob_at_all():
    """The other half of "one reader" (DRE-4141), read at the wire. The sweep
    moves no card out of Intake, so nothing on `reconcile.yml` may put any of
    the three names in an environment — a knob that reaches the process is a
    knob somebody can act on."""
    doc = _doc("reconcile.yml")
    for variable in ("INTAKE_HOLD", "INTAKE_MAX_AGE_MINUTES",
                     "INTAKE_ESCALATION_CAP"):
        assert _assignments(doc, "reconcile.yml", variable) == [], variable


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
              INTAKE_HOLD: "2026-09-08"
            run: python3 .bureau-pipeline/scripts/reconcile.py
    """)
    found = _assignments(doc, "synthetic.yml", "INTAKE_HOLD")
    assert [value for _w, value in found] == ["2026-09-08"]


def test_this_repos_own_stub_takes_the_switch_from_the_repository_variable():
    """The pen is one switch per repo, and this repo's one reader passes it. A
    stub that cannot pass it is a repo whose intake cannot be held.

    ONE STUB since DRE-4141. `self-reconcile.yml` passed it too, because the
    age-out and the drain were the two things that moved a card out of Intake;
    the age-out is gone, so the drain's stub is the pen.

    WHERE the value comes from changed in DRE-3285: every stub in the fleet now
    threads `${{ vars.INTAKE_HOLD }}`, a repository variable, rather than a
    value committed into the file. Same shape as before — the stub is
    boilerplate, the per-repo value is data — but the data lives where the
    operator can change it with one command instead of a pull request, which is
    what a hold thrown on cutover morning needs.

    So the EXPRESSION is what this pins, not merely the input's presence. A
    literal back in that field is not an operator pinning a date any more — it
    is the pre-DRE-3285 mechanism silently restored, and the hold it buys costs
    a pull request, a critic round and the merge gate on the morning somebody
    needs the pen shut within the hour. Same treatment the sibling fleet brake
    already gets in `test_the_brake_is_read_before_any_surface_runs`."""
    for stub in ("self-groomer.yml",):
        with_block = ((_doc(stub).get("jobs") or {}).get("call") or {}).get("with") or {}
        assert "intake_hold" in with_block, (
            f"{stub} passes no intake_hold — this repo's own intake cannot be held"
        )
        assert str(with_block["intake_hold"]).strip() == "${{ vars.INTAKE_HOLD }}", (
            f"{stub} sets intake_hold to {with_block['intake_hold']!r} — the "
            f"repository variable is the only value it may carry, or the pen "
            f"takes a pull request to close again"
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
