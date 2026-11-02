"""RED-first: the reconcile sweep's later phases run whenever the `Sweep` step
ran, pass or fail (DRE-8000).

**One alarm took three phases down with it.** On 2026-10-25, from 12:32 PM PT
to 3:46 PM PT, all 14 scheduled agent-bureau Reconcile runs failed at `Sweep`
on the idle-board alarm (DRE-6472: one card in Queued, nothing dispatched, a
person must act). The alarm was right to go red. But `Dispatch proof runs`,
`Reply to the CEO's Green Light comments` and `Read the switches` were gated
only on `if: inputs.sweep_reason == ''`, and a step `if:` with no status
function gets an implied `success()` — so every one of them showed `skipped`,
and no proof run started in agent-bureau for more than three hours. Runs
40224152321 and 40225414708 showed the same chain one step later: a rate-limited
`Dispatch proof runs` skipped the Green Light reply and the switch read.

**The rule.** Every step after `Sweep` runs when `Sweep` itself ran — its
outcome `success` or `failure` — on a full pass, and never when the job was
cancelled or a gate BEFORE `Sweep` stopped it (`Refuse a pipeline_ref the stub
did not call`, a token that failed to mint: those leave `Sweep` `skipped`). A
bare `always()` or `!cancelled()` is not enough, because it would run the
phases past a refused ref. And the job stays red when the alarm fires: `Sweep`
keeps `exit "$STATUS"` and carries no `continue-on-error`.

Two halves. The structural checks read each step's `if:` the way the card
words it. The scenario checks evaluate those same `if:` strings — a small
evaluator for the expression subset this workflow uses, with GitHub's implied
`success()` — over the job's real step list, so "the phase still runs when
Sweep fails" is shown, not inferred from a substring.

Run: cd bureau-pipeline && python3 -m pytest \\
         tests/test_reconcile_workflow_phase_independence.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "reconcile.yml"
SWEEP_STEP = "Sweep"
REF_GATE = "Refuse a pipeline_ref the stub did not call"

#: The three phases the 2026-10-25 runs skipped, by name — the floor. The
#: rule itself is read over EVERY step after `Sweep` (`_after_sweep`).
PHASES = (
    "Dispatch proof runs",
    "Reply to the CEO's Green Light comments",
    "Read the switches",
)

#: A step after `Sweep` that is exempt from the rule, by name, with the reason
#: it should NOT run after a failed sweep. Empty today: every phase after the
#: sweep is its own process with its own status, and none depends on the
#: sweep having passed.
ALLOWED: dict[str, str] = {}

_STATUS_FUNCTIONS = ("success()", "failure()", "always()", "cancelled()")


# --------------------------------------------------------------------------
# helpers — the job's steps, live
# --------------------------------------------------------------------------
def _steps() -> list:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["sweep"]["steps"]


def _sweep_index(steps: list) -> int:
    found = [i for i, s in enumerate(steps) if s.get("name") == SWEEP_STEP]
    assert len(found) == 1, f"{SWEEP_STEP!r} is not one step of reconcile.yml"
    return found[0]


def _after_sweep() -> list:
    steps = _steps()
    return [s for s in steps[_sweep_index(steps) + 1:]
            if s.get("name") not in ALLOWED]


def _sweep() -> dict:
    steps = _steps()
    return steps[_sweep_index(steps)]


def _bare(condition: str) -> str:
    """The expression inside an `if:`, with any `${{ }}` wrapper removed."""
    text = str(condition).strip()
    match = re.fullmatch(r"\$\{\{(.*)\}\}", text, re.S)
    return (match.group(1) if match else text).strip()


# --------------------------------------------------------------------------
# a GitHub Actions expression evaluator — the subset this workflow uses
# --------------------------------------------------------------------------
_TOKEN = re.compile(r"\s*(?:(\|\||&&|==|!=|!|\(|\))|'((?:[^']|'')*)'|([A-Za-z_][\w.-]*))")


def _tokens(text: str) -> list:
    out, pos = [], 0
    text = text.rstrip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        assert match, f"cannot read the expression at {text[pos:]!r}"
        op, literal, name = match.groups()
        if op is not None:
            out.append(("op", op))
        elif literal is not None:
            out.append(("str", literal.replace("''", "'")))
        else:
            out.append(("name", name))
        pos = match.end()
    return out


class _Eval:
    """Recursive descent over `||`, `&&`, `==`/`!=`, `!`, parentheses, string
    literals, dotted context paths and the four status functions. Values and
    truthiness follow GitHub's: `&&`/`||` return an operand, '' is falsy, a
    missing context path is ''."""

    def __init__(self, tokens: list, context: dict, status: dict):
        self.tokens, self.i = tokens, 0
        self.context, self.status = context, status

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def take(self):
        token = self.peek()
        self.i += 1
        return token

    def run(self):
        value = self.or_()
        assert self.i == len(self.tokens), f"trailing tokens {self.tokens[self.i:]}"
        return value

    def or_(self):
        value = self.and_()
        while self.peek() == ("op", "||"):
            self.take()
            right = self.and_()
            value = value if value else right
        return value

    def and_(self):
        value = self.eq()
        while self.peek() == ("op", "&&"):
            self.take()
            right = self.eq()
            value = right if value else value
        return value

    def eq(self):
        value = self.unary()
        if self.peek() in (("op", "=="), ("op", "!=")):
            _, op = self.take()
            right = self.unary()
            same = str(value).lower() == str(right).lower()
            return same if op == "==" else not same
        return value

    def unary(self):
        if self.peek() == ("op", "!"):
            self.take()
            return not self.unary()
        return self.primary()

    def primary(self):
        kind, text = self.take()
        if (kind, text) == ("op", "("):
            value = self.or_()
            assert self.take() == ("op", ")"), "unbalanced parenthesis"
            return value
        if kind == "str":
            return text
        assert kind == "name", f"unexpected token {text!r}"
        if text in ("true", "false"):
            return text == "true"
        if self.peek() == ("op", "("):
            self.take()
            assert self.take() == ("op", ")"), f"{text}() takes no arguments here"
            assert text in self.status, f"unknown function {text}()"
            return self.status[text]
        value = self.context
        for part in text.split("."):
            value = value.get(part, "") if isinstance(value, dict) else ""
        return value


def _should_run(condition, context: dict, *, failed: bool, cancelled: bool) -> bool:
    expression = _bare(condition) if condition is not None else "success()"
    if not any(fn in expression.replace(" ", "") for fn in _STATUS_FUNCTIONS):
        # GitHub's rule: an `if:` with no status function is `success() && (…)`.
        expression = f"success() && ({expression})"
    status = {
        "success": not failed and not cancelled,
        "failure": failed and not cancelled,
        "always": True,
        "cancelled": cancelled,
    }
    return bool(_Eval(_tokens(expression), context, status).run())


def _simulate(*, fails=(), sweep_reason="", cancelled=False) -> dict:
    """Walk the job's real steps in order and return, per step name, whether
    it ran — plus `__job_failed__`. A step named in `fails` fails if it runs;
    its failure counts against the job unless the step is `continue-on-error`."""
    context = {
        "inputs": {"sweep_reason": sweep_reason},
        "env": {"BUREAU_APP_ID_2": "2", "BUREAU_APP_ID_3": "3", "BUREAU_APP_ID_4": "4"},
        "steps": {},
    }
    job_failed = False
    ran = {}
    for step in _steps():
        name = step.get("name") or step.get("uses")
        runs = _should_run(step.get("if"), context, failed=job_failed, cancelled=cancelled)
        outcome = "skipped"
        if runs:
            outcome = "failure" if name in fails else "success"
            if outcome == "failure" and not step.get("continue-on-error"):
                job_failed = True
        if step.get("id"):
            context["steps"][step["id"]] = {"outcome": outcome}
        ran[name] = runs
    ran["__job_failed__"] = job_failed
    return ran


def _gates_before_sweep() -> list:
    """Every step before `Sweep` whose failure fails the job."""
    steps = _steps()
    return [s.get("name") or s.get("uses") for s in steps[:_sweep_index(steps)]
            if not s.get("continue-on-error")]


# --------------------------------------------------------------------------
# 1: the Sweep step — addressable, and still red when the alarm fires
# --------------------------------------------------------------------------
def test_the_sweep_step_has_an_id_the_later_phases_can_read():
    assert _sweep().get("id"), (
        "the Sweep step has no `id`, so no later step can ask whether it ran"
    )


def test_the_sweep_step_is_never_continue_on_error():
    assert "continue-on-error" not in _sweep(), (
        "continue-on-error on Sweep would turn the idle-board alarm green"
    )


def test_the_sweep_step_still_exits_with_the_passes_status():
    run = _sweep()["run"].rstrip()
    assert run.endswith('exit "$STATUS"'), "Sweep no longer exits with reconcile.py's status"


# --------------------------------------------------------------------------
# 2: every step after Sweep — the condition, read structurally
# --------------------------------------------------------------------------
def test_every_named_phase_is_after_the_sweep():
    names = [s.get("name") for s in _after_sweep()]
    for phase in PHASES:
        assert phase in names, f"{phase!r} is not a step after Sweep"


def test_the_allow_list_names_only_real_steps_with_a_reason():
    steps = _steps()
    after = {s.get("name") for s in steps[_sweep_index(steps) + 1:]}
    for name, reason in ALLOWED.items():
        assert name in after, f"ALLOWED names {name!r}, which is no step after Sweep"
        assert reason.strip(), f"ALLOWED names {name!r} with no reason"


@pytest.mark.parametrize("step", _after_sweep(), ids=lambda s: s.get("name"))
def test_each_later_step_keeps_the_full_pass_gate(step):
    assert "inputs.sweep_reason == ''" in _bare(step.get("if") or ""), (
        f"{step.get('name')!r} lost `inputs.sweep_reason == ''` and would run on "
        "a relay-scoped pass"
    )


@pytest.mark.parametrize("step", _after_sweep(), ids=lambda s: s.get("name"))
def test_each_later_step_overrides_the_implied_success(step):
    condition = _bare(step.get("if") or "").replace(" ", "")
    assert "!cancelled()" in condition or "always()" in condition, (
        f"{step.get('name')!r} has no `!cancelled()` or `always()`, so GitHub "
        "reads its `if:` as `success() && …` and a failed Sweep skips it"
    )


@pytest.mark.parametrize("step", _after_sweep(), ids=lambda s: s.get("name"))
def test_each_later_step_is_gated_on_the_sweeps_own_outcome(step):
    sweep_id = _sweep().get("id")
    assert sweep_id, "the Sweep step has no `id` to gate on"
    condition = _bare(step.get("if") or "")
    assert f"steps.{sweep_id}.outcome" in condition, (
        f"{step.get('name')!r} is not gated on the Sweep step's outcome, so a "
        "failed pre-Sweep gate (a refused pipeline_ref) would not stop it"
    )


# --------------------------------------------------------------------------
# 3: the scenarios — the same `if:` strings, evaluated over the real steps
# --------------------------------------------------------------------------
def test_a_clean_full_pass_runs_every_phase():
    ran = _simulate()
    for phase in PHASES:
        assert ran[phase], phase
    assert not ran["__job_failed__"]


def test_a_failed_sweep_still_runs_every_later_phase_and_the_job_stays_red():
    """The 2026-10-25 runs: the idle-board alarm fails Sweep (exit 1)."""
    ran = _simulate(fails={SWEEP_STEP})
    for step in _after_sweep():
        assert ran[step["name"]], f"{step['name']!r} is skipped when Sweep fails"
    assert ran["__job_failed__"], "a failed Sweep no longer fails the job"


def test_a_failed_proof_dispatch_still_runs_the_phases_after_it():
    """Runs 40224152321 and 40225414708: the dispatch hit the rate limit."""
    ran = _simulate(fails={"Dispatch proof runs"})
    assert ran["Reply to the CEO's Green Light comments"]
    assert ran["Read the switches"]
    assert ran["__job_failed__"]


@pytest.mark.parametrize("phase", PHASES)
def test_a_failed_phase_never_stops_the_phases_after_it(phase):
    ran = _simulate(fails={phase})
    names = [s["name"] for s in _after_sweep()]
    for later in names[names.index(phase) + 1:]:
        assert ran[later], f"{later!r} is skipped when {phase!r} fails"


@pytest.mark.parametrize("gate", _gates_before_sweep())
def test_a_failed_gate_before_the_sweep_runs_no_later_phase(gate):
    ran = _simulate(fails={gate})
    assert not ran[SWEEP_STEP], f"Sweep ran after {gate!r} failed"
    for step in _after_sweep():
        assert not ran[step["name"]], f"{step['name']!r} ran after {gate!r} failed"


def test_the_ref_gate_is_one_of_the_gates_that_stops_everything():
    assert REF_GATE in _gates_before_sweep()
    ran = _simulate(fails={REF_GATE})
    for phase in PHASES:
        assert not ran[phase], f"{phase!r} ran past a refused pipeline_ref"


@pytest.mark.parametrize("failing", [(), (SWEEP_STEP,)], ids=["sweep-passes", "sweep-fails"])
def test_a_scoped_pass_runs_no_later_phase(failing):
    ran = _simulate(fails=set(failing), sweep_reason="card-done")
    assert ran[SWEEP_STEP]
    for step in _after_sweep():
        assert not ran[step["name"]], f"{step['name']!r} ran on a scoped pass"


def test_a_cancelled_job_runs_no_later_phase():
    ran = _simulate(cancelled=True)
    for step in _after_sweep():
        assert not ran[step["name"]], f"{step['name']!r} ran in a cancelled job"


# --------------------------------------------------------------------------
# 4: the evaluator itself — it must read GitHub's rules, or the scenarios lie
# --------------------------------------------------------------------------
@pytest.mark.parametrize("condition,failed,cancelled,expected", [
    ("inputs.sweep_reason == ''", False, False, True),
    ("inputs.sweep_reason == ''", True, False, False),   # the implied success()
    ("always()", True, False, True),
    ("${{ !cancelled() }}", True, False, True),
    ("${{ !cancelled() }}", False, True, False),
    ("${{ failure() && inputs.sweep_reason == '' }}", True, False, True),
    ("${{ steps.s.outcome == 'success' || steps.s.outcome == 'failure' }}", False, False, False),
    (None, True, False, False),
])
def test_the_evaluator_reads_githubs_status_rules(condition, failed, cancelled, expected):
    context = {"inputs": {"sweep_reason": ""}, "steps": {"s": {"outcome": "skipped"}}}
    assert _should_run(condition, context, failed=failed, cancelled=cancelled) is expected
