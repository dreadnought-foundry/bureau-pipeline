"""RED-first: the reconcile workflow shows what a pass spent, and carries the
dispatch's reason and card into the sweep (DRE-3640).

TWO GAPS, both in the YAML this repo owns.

**The spend was written and never shown.** The sweep prints
`linear-budget: <first> → <last> (spent N this run; …)` on stderr as it exits,
and one `sweep-spend:` line per phase on stdout — and nothing surfaces either.
Reading what a pass cost means opening the run log, which is exactly the thing
nobody does at 15-minute intervals. The `Sweep` step keeps its output flowing
to the log line for line and additionally lifts those lines, VERBATIM, onto
`$GITHUB_STEP_SUMMARY`. A pass that printed no budget line says so in one
summary line rather than leaving the page blank — a blank page reads as "no
spend" when it means "unknown".

**The dispatch's scope was thrown away.** `self-reconcile.yml` takes
`repository_dispatch: types: [reconcile]` from the relay and calls the reusable
with fixed inputs, so a dispatch fired because ONE card went Done runs a full
board pass — 20 of them on 2026-09-10 on this repo alone, each at the full
price. The reusable takes `sweep_reason` / `sweep_card` as optional
`workflow_call` inputs, threaded into the step's env as `SWEEP_REASON` /
`SWEEP_CARD` verbatim, and the stub passes the relay's payload. On `schedule`
and `workflow_dispatch` the `inputs` context is empty and both interpolate to
`""`, which the sweep reads as a full pass — today's behaviour, unchanged.

The input NAMES are the contract shared with the sibling that reads the env;
the stub is the adapter, and a literal in either place is the per-repo value
baked into the shared channel that `test_intake_pen` already refuses for the
intake knobs.

Run: cd bureau-pipeline && python3 -m pytest \\
         tests/test_reconcile_workflow_spend_and_scope.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_reconcile_env  # noqa: E402
import check_wip_cap  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
SWEEP_STEP = "Sweep"

#: env var → the `workflow_call` input that must supply it, and nothing else.
SCOPE = {"SWEEP_REASON": "sweep_reason", "SWEEP_CARD": "sweep_card"}

#: the stub's `with:` key → the payload expression it must carry. The relay
#: names the card `identifier` and the cause `reason` — the same two keys
#: `plan_run.payload()` puts on every dispatch this fleet fires.
STUB_WITH = {
    "sweep_reason": "${{ github.event.client_payload.reason }}",
    "sweep_card": "${{ github.event.client_payload.identifier }}",
}

#: What the summary must say when the pass never printed its trailer. The
#: wording is free; saying SOMETHING is not.
NO_BUDGET_NEEDLE = "no linear-budget:"


# --------------------------------------------------------------------------
# helpers — live extraction, the shape test_intake_pen.py reads the knobs with
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
    [(where, value)] — workflow, job and step scope."""
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


def _sweep_step() -> dict:
    for _job_id, _i, step in check_wip_cap._steps(_doc("reconcile.yml")):
        if step.get("name") == SWEEP_STEP:
            return step
    raise AssertionError(f"reconcile.yml has no {SWEEP_STEP!r} step")


# --------------------------------------------------------------------------
# 1: the two inputs exist, optional, string, empty — and thread verbatim
# --------------------------------------------------------------------------
@pytest.mark.parametrize("variable,name", sorted(SCOPE.items()))
def test_the_reusable_declares_both_scope_inputs(variable, name):
    inputs = _call_inputs(_doc("reconcile.yml"))
    assert name in inputs, (
        f"{name} is not a workflow_call input, so {variable} is a scope no "
        f"caller can narrow and every dispatch buys a full board pass"
    )
    assert inputs[name].get("type") == "string"
    assert inputs[name].get("required") is False


@pytest.mark.parametrize("name", sorted(SCOPE.values()))
def test_an_omitted_scope_input_defaults_to_empty_not_to_a_word(name):
    """Empty is the full pass. A default of `card-done` here would silently
    narrow every scheduled sweep in the fleet."""
    assert _call_inputs(_doc("reconcile.yml"))[name].get("default") == ""


@pytest.mark.parametrize("variable,name", sorted(SCOPE.items()))
def test_the_sweep_step_threads_each_input_verbatim(variable, name):
    doc = _doc("reconcile.yml")
    places = _assignments(doc, "reconcile.yml", variable)
    assert places, f"the sweep step never puts {variable} in the environment"
    for where, value in places:
        assert str(value).strip() == "${{ inputs.%s }}" % name, (
            f"{where} sets {variable} to {value!r} — the caller's input is the "
            f"only value it may carry"
        )


def test_the_variables_live_on_the_step_that_runs_the_sweep():
    """Job- or workflow-scope would also reach reconcile.py, but the step that
    runs it is where the sweep's other arguments are declared and where a
    reader looks for them."""
    env = check_wip_cap._env(_sweep_step())
    for variable in SCOPE:
        assert variable in env, (
            f"{variable} is not on the {SWEEP_STEP!r} step's own env block"
        )


def test_no_workflow_anywhere_assigns_a_literal():
    """A literal in the shared channel is one repo's scope becoming the
    fleet's — the same refusal the intake knobs already carry."""
    offences = []
    for name, doc in _live_docs().items():
        for variable, input_name in SCOPE.items():
            for where, value in _assignments(doc, name, variable):
                if str(value).strip() != "${{ inputs.%s }}" % input_name:
                    offences.append(f"{where} = {value!r}")
    assert offences == [], "; ".join(offences)


def test_the_guard_would_notice_a_literal():
    """Guard the guard: a checker that never fires reports ok forever."""
    doc = yaml.safe_load("""
    on: {workflow_call: {}}
    jobs:
      sweep:
        steps:
          - name: Sweep
            env:
              SWEEP_REASON: card-done
            run: python3 .bureau-pipeline/scripts/reconcile.py
    """)
    assert [v for _w, v in _assignments(doc, "synthetic.yml", "SWEEP_REASON")] \
        == ["card-done"]


# --------------------------------------------------------------------------
# 2: the stub is the adapter — it passes the relay's payload, not a literal
# --------------------------------------------------------------------------
@pytest.mark.parametrize("key,expression", sorted(STUB_WITH.items()))
def test_the_stub_passes_the_dispatch_payload(key, expression):
    with_block = ((_doc("self-reconcile.yml").get("jobs") or {})
                  .get("call") or {}).get("with") or {}
    assert key in with_block, (
        f"self-reconcile.yml passes no {key} — the relay's dispatch still "
        f"buys a full board pass for one card"
    )
    assert str(with_block[key]).strip() == expression, (
        f"self-reconcile.yml sets {key} to {with_block[key]!r} — the relay's "
        f"payload is the only value it may carry, and a literal here would "
        f"scope the SCHEDULED sweep too"
    )


# --------------------------------------------------------------------------
# 3: the step's own shell, executed over a fake sweep log
# --------------------------------------------------------------------------
#: A pass that printed both kinds — three phase lines and the exit trailer.
BOTH_KINDS = """\
reconcile: 260 backlog cards, 30 open PRs, 5 epics
sweep-spend: board 41 request(s)
sweep-spend: backlog 29 request(s)
promoted DRE-3601 to Todo
sweep-spend: total 93 request(s) over 4 phase(s)
linear-budget: 2400 → 2307 (spent 93 this run; window resets 16:00 PT; budget: fleet)
"""

#: A pass that printed neither — it died before its trailer.
NEITHER_KIND = """\
reconcile: 260 backlog cards, 30 open PRs, 5 epics
Traceback (most recent call last):
reconcile: Linear API returned 400
"""

BUDGET_LINE = (
    "linear-budget: 2400 → 2307 (spent 93 this run; window resets 16:00 PT; "
    "budget: fleet)"
)
SPEND_LINES = [
    "sweep-spend: board 41 request(s)",
    "sweep-spend: backlog 29 request(s)",
    "sweep-spend: total 93 request(s) over 4 phase(s)",
]

#: Stands in for reconcile.py: it replays a fake log, putting the budget
#: trailer on stderr and everything else on stdout, exactly as the real sweep
#: does, and exits with whatever status the case asks for.
STUB_RECONCILE = '''\
import os
import sys

for line in open(os.environ["FAKE_SWEEP_LOG"]).read().splitlines():
    stream = sys.stderr if line.startswith("linear-budget:") else sys.stdout
    print(line, file=stream)
sys.exit(int(os.environ.get("FAKE_SWEEP_STATUS", "0")))
'''


def _run_sweep_step(tmp_path, log_text, status=0):
    """The LIVE `Sweep` step's shell, run over a fake log. Returns
    (completed process, summary file contents)."""
    scripts = tmp_path / ".bureau-pipeline" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "reconcile.py").write_text(STUB_RECONCILE)
    log = tmp_path / "fake-sweep.log"
    log.write_text(log_text)
    summary = tmp_path / "step-summary.md"
    summary.write_text("")
    env = dict(os.environ)
    env.update({
        "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
        "GITHUB_STEP_SUMMARY": str(summary),
        "FAKE_SWEEP_LOG": str(log),
        "FAKE_SWEEP_STATUS": str(status),
    })
    # `bash -e {0}` is the shell GitHub runs a `run:` block with.
    proc = subprocess.run(["bash", "-e", "-c", _sweep_step()["run"]],
                          cwd=str(tmp_path), env=env,
                          capture_output=True, text=True)
    return proc, summary.read_text()


def test_a_pass_that_printed_both_kinds_puts_every_line_on_the_summary(tmp_path):
    proc, summary = _run_sweep_step(tmp_path, BOTH_KINDS)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = summary.splitlines()
    for line in SPEND_LINES + [BUDGET_LINE]:
        assert line in lines, (
            f"the run summary does not carry {line!r} verbatim — got:\n{summary}"
        )


def test_the_summary_carries_nothing_but_the_sweeps_own_spend_lines(tmp_path):
    """Verbatim, and only those: a summary that also lifted `promoted DRE-…`
    would be a second, drifting copy of the run log."""
    _proc, summary = _run_sweep_step(tmp_path, BOTH_KINDS)
    assert "promoted DRE-3601" not in summary
    assert "260 backlog cards" not in summary


def test_the_pass_still_flows_to_the_run_log(tmp_path):
    """Capturing the output must not swallow it — the log is still where the
    detail lives, and the summary is a lift, not a move."""
    proc, _summary = _run_sweep_step(tmp_path, BOTH_KINDS)
    output = proc.stdout + proc.stderr
    assert "promoted DRE-3601 to Todo" in output
    assert BUDGET_LINE in output
    for line in SPEND_LINES:
        assert line in output


def test_a_pass_that_printed_no_budget_line_says_so(tmp_path):
    """One line saying the spend is unknown, rather than a blank page that
    reads as "nothing was spent"."""
    proc, summary = _run_sweep_step(tmp_path, NEITHER_KIND)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert summary.strip(), "a pass with no spend lines wrote nothing at all"
    assert NO_BUDGET_NEEDLE in summary.lower(), (
        f"the summary does not say the budget line was missing — got:\n{summary}"
    )
    assert not [ln for ln in summary.splitlines()
                if ln.startswith("linear-budget:")], (
        "the summary invented a budget line the pass never printed"
    )
    assert "sweep-spend:" not in summary


def test_a_red_sweep_stays_red_and_still_reports_its_spend(tmp_path):
    """`tee` returns 0 whatever reconcile.py did, so the step needs pipefail —
    and the summary is written anyway, because a failed pass is exactly when
    somebody wants to know what the hour cost."""
    proc, summary = _run_sweep_step(tmp_path, BOTH_KINDS, status=7)
    assert proc.returncode == 7, (
        "a failing sweep reported success — the pipeline's status was masked"
    )
    assert BUDGET_LINE in summary.splitlines()


# --------------------------------------------------------------------------
# 4: the step is still a reconcile.py call site the live guards recognise
# --------------------------------------------------------------------------
def test_the_sweep_is_still_a_call_site_passing_every_argument():
    """The capture rewrote the invocation line. `check_reconcile_env` and
    `check_wip_cap` both find their call sites by reading that line, and a
    guard that stops recognising it goes quiet while reporting ok."""
    doc = _doc("reconcile.yml")
    assert check_reconcile_env.call_sites(doc), (
        "no reconcile.py call site in reconcile.yml — the argument lint is now "
        "vacuous on the workflow it was written for"
    )
    assert check_reconcile_env.check_workflow(doc, "reconcile.yml") == []
    assert check_wip_cap.promotion_steps(doc), (
        "the sweep is no longer read as a promoting step, so nothing checks "
        "its WIP cap"
    )
