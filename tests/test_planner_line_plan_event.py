"""A plan is asked for on the PLAN route, whatever the card's labels (DRE-5366).

THE INCIDENT. On 2026-09-30 DRE-5198 — a one-off with no `agent:planner` label
— was sent back to Planning and waited in the fleet planner line (DRE-5167).
When its turn came, the line's dispatch called `plan_run.fire`, which chose
the event off the labels alone: no `agent:planner`, so `agent-execute`. A
build run started on a card that was waiting to be PLANNED, and moved it to In
Progress with no classification, no plan and no critic.

A card waiting in the planner line was waiting to be planned, so both places
that served the line fired `agent-plan` (DRE-5180, DRE-5178). The line went
with the planner cap (DRE-8618), and its ledger and the sweep's drain of it
with DRE-8622, so neither call site is left. What stays is the rule for
`plan_run.fire`'s other callers: a caller asking for a plan names
`agent-plan`, the label rule stays for `review_rerun.py dispatch`'s activate
route, and the reconcile re-dispatch of a promoted card that has not started
names `agent-execute` outright since DRE-7108: its cards are never epics, and
left to the labels a one-off wearing `agent:planner` would be sent to the
planner.

Every dispatch here goes through the REAL `plan_run.fire`; only the `gh api
repos/<repo>/dispatches` call is faked, and it records the event_type GitHub
would have received.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planner_line_plan_event.py -v
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import plan_run  # noqa: E402
import reconcile  # noqa: E402
import review_rerun  # noqa: E402

REPO = "dreadnought-foundry/agent-bureau"
# The labels DRE-5198 carried when it was served: a one-off, no agent:planner.
ONE_OFF_LABELS = [{"name": "agent:engineer"}, {"name": "repo:agent-bureau"}]
EPIC_LABELS = [{"name": "agent:planner"}, {"name": "repo:agent-bureau"}]


def _record(ident: str, labels: list) -> dict:
    """A card as `plan_run.CARD_QUERY` returns it."""
    return {
        "id": f"uuid-{ident}",
        "identifier": ident,
        "title": f"card {ident}",
        "description": "work",
        "labels": {"nodes": list(labels)},
        "children": {"nodes": []},
    }


class FakeDispatch:
    """`gh api repos/<repo>/dispatches --input <file>`, recording what GitHub
    would have received — read off the input file before `fire` deletes it."""

    def __init__(self):
        self.sent: list[dict] = []

    def run(self, argv, **kw):
        assert argv[:2] == ["gh", "api"] and argv[2].endswith("/dispatches"), argv
        with open(argv[argv.index("--input") + 1], encoding="utf-8") as f:
            body = json.load(f)
        self.sent.append({"repo": argv[2].split("/dispatches")[0].removeprefix("repos/"),
                          **body})
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    @property
    def events(self) -> list[str]:
        return [s["event_type"] for s in self.sent]


@contextlib.contextmanager
def _fake_dispatch():
    fake = FakeDispatch()
    with mock.patch.object(plan_run.subprocess, "run", side_effect=fake.run):
        yield fake


# --------------------------------------------------------------------------- #
# fire's other callers keep the label rule                                     #
# --------------------------------------------------------------------------- #


def test_reconcile_redispatch_of_a_todo_card_still_fires_agent_execute(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", REPO)
    with _fake_dispatch() as fake:
        assert reconcile.redispatch(_record("DRE-9005", ONE_OFF_LABELS)) is True
    assert fake.events == ["agent-execute"]


def test_reconcile_redispatch_of_a_planner_labelled_card_fires_a_build(monkeypatch):
    """DRE-7108: the sweep's re-fire is a build whatever the labels say — the
    card it re-fires is a promoted one-off, never an epic."""
    monkeypatch.setattr(reconcile, "REPO", REPO)
    with _fake_dispatch() as fake:
        assert reconcile.redispatch(_record("DRE-9006", EPIC_LABELS)) is True
    assert fake.events == ["agent-execute"]


def _review_rerun(record: dict) -> FakeDispatch:
    with _fake_dispatch() as fake, \
            mock.patch.object(linear_ops, "gql", return_value={"issue": record}), \
            mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "777"}), \
            contextlib.redirect_stdout(io.StringIO()):
        rc = review_rerun.main(["dispatch", "--epic", record["identifier"],
                                "--repo", REPO, "--reason", "re-review"])
    assert rc == 0
    return fake


def test_review_rerun_of_an_epic_still_fires_agent_plan():
    fake = _review_rerun(_record("DRE-9007", EPIC_LABELS))
    assert fake.events == ["agent-plan"]
    payload = fake.sent[0]["client_payload"]
    assert (payload["trigger_state"], payload["reason"], payload["sent_by_run"]) == (
        review_rerun.TRIGGER_STATE_ACTIVATE, "re-review", "777")


def test_review_rerun_follows_the_labels_as_before():
    assert _review_rerun(_record("DRE-9008", ONE_OFF_LABELS)).events == ["agent-execute"]


def test_fire_with_no_event_follows_the_labels():
    with _fake_dispatch() as fake:
        assert plan_run.fire(_record("DRE-9009", ONE_OFF_LABELS), REPO) == (True, "")
        assert plan_run.fire(_record("DRE-9010", EPIC_LABELS), REPO) == (True, "")
    assert fake.events == ["agent-execute", "agent-plan"]


def test_the_proof_event_is_a_known_event():
    # DRE-5921: the proof runner's dispatch event, shared byte for byte with
    # the `repository_dispatch` type of the stub that runs it.
    assert plan_run.PROOF_EVENT == "proof-execute"
    assert plan_run.PROOF_EVENT in plan_run.EVENTS


def test_fire_sends_the_proof_event_when_named():
    with _fake_dispatch() as fake:
        assert plan_run.fire(_record("DRE-9012", ONE_OFF_LABELS), REPO,
                             event=plan_run.PROOF_EVENT) == (True, "")
    assert fake.events == ["proof-execute"]
    assert fake.sent[0]["client_payload"]["identifier"] == "DRE-9012"


def test_fire_refuses_an_event_it_does_not_know():
    with _fake_dispatch() as fake:
        try:
            plan_run.fire(_record("DRE-9011", ONE_OFF_LABELS), REPO, event="agent-plna")
        except ValueError:
            pass
        else:
            raise AssertionError("an unknown event must not be dispatched")
    assert fake.sent == []


# --------------------------------------------------------------------------- #
# No plan call site can produce agent-execute                                  #
# --------------------------------------------------------------------------- #

# Every call of `plan_run.fire` in scripts/, by (file, enclosing function).
# A call site asking for a plan MUST pin the plan event; the others keep the
# label rule or name their own. A new call site fails
# `test_every_fire_call_site_is_classified` until it is placed in one of the
# sets below.
# The write layer's in-place restart of a card already in the start lane
# (DRE-7109) keeps the label rule; the sweep's `redispatch` no longer does
# (DRE-7108).
LABEL_RULE_SITES = {
    ("linear_ops.py", "_redispatch_in_place"),
}
# The sweep's re-fire of a promoted card no run started on is a BUILD, named
# outright as `plan_run.EXECUTE_EVENT` (DRE-7108). So is the planner gate's
# hand-back of a one-off card it was wrongly handed (DRE-7110).
BUILD_SITES = {
    ("reconcile.py", "redispatch"),
    ("dedupe_dispatch.py", "_hand_back"),
}
# Passes `event=` from its `--route`: `activate` (the default) hands it None,
# which is the label rule; `plan` hands it PLAN_EVENT (DRE-5376).
ROUTED_SITES = {
    ("review_rerun.py", "_cmd_dispatch"),
}
# Asks for an epic's activate run, which lives in plan.yml, so it pins
# PLAN_EVENT, as the old planner line did, rather than trusting the label
# (DRE-5152: the sweep's one re-dispatch of a start the relay never
# activated).
EPIC_START_SITES = {
    ("reconcile.py", "_redispatch_epic_start"),
}


def _fire_calls() -> list[tuple[str, str, ast.Call]]:
    found = []
    for path in sorted(SCRIPTS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                if (isinstance(f, ast.Attribute) and f.attr == "fire"
                        and isinstance(f.value, ast.Name) and f.value.id == "plan_run"):
                    found.append((path.name, fn.name, node))
    # A call inside a nested function is walked under each enclosing def;
    # keep the innermost one, which ast.walk reaches last.
    innermost = {}
    for name, fn, node in found:
        innermost[id(node)] = (name, fn, node)
    return list(innermost.values())


def test_every_fire_call_site_is_classified():
    sites = {(name, fn) for name, fn, _ in _fire_calls()}
    assert sites == (LABEL_RULE_SITES | ROUTED_SITES
                     | EPIC_START_SITES | BUILD_SITES), (
        "a plan_run.fire call site was added or moved — say whether it asks "
        "for a plan (it must pass event=plan_run.PLAN_EVENT) or keeps "
        f"the label rule: {sorted(sites)}")


def test_no_plan_call_site_can_produce_agent_execute():
    found = 0
    for name, fn, node in _fire_calls():
        if (name, fn) not in EPIC_START_SITES:
            continue
        found += 1
        event = [kw.value for kw in node.keywords if kw.arg == "event"]
        assert len(event) == 1, f"{name}:{fn} fires without pinning the event"
        value = event[0]
        assert (isinstance(value, ast.Attribute) and value.attr == "PLAN_EVENT"
                and isinstance(value.value, ast.Name) and value.value.id == "plan_run"), (
            f"{name}:{fn} must pass event=plan_run.PLAN_EVENT, not {ast.unparse(value)}")
    assert found == len(EPIC_START_SITES)
    assert plan_run.PLAN_EVENT == "agent-plan"


def test_the_build_sites_pin_agent_execute():
    found = 0
    for name, fn, node in _fire_calls():
        if (name, fn) not in BUILD_SITES:
            continue
        found += 1
        event = [kw.value for kw in node.keywords if kw.arg == "event"]
        assert len(event) == 1, f"{name}:{fn} fires without pinning the event"
        value = event[0]
        assert (isinstance(value, ast.Attribute) and value.attr == "EXECUTE_EVENT"
                and isinstance(value.value, ast.Name) and value.value.id == "plan_run"), (
            f"{name}:{fn} must pass event=plan_run.EXECUTE_EVENT, not {ast.unparse(value)}")
    assert found == len(BUILD_SITES)
    assert plan_run.EXECUTE_EVENT == "agent-execute"


def test_the_label_rule_sites_do_not_pin_an_event():
    for name, fn, node in _fire_calls():
        if (name, fn) in LABEL_RULE_SITES:
            assert not any(kw.arg == "event" for kw in node.keywords), (
                f"{name}:{fn} is a label-rule caller and must fire as it did")
