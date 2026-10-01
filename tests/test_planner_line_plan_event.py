"""The planner line dispatches the PLAN route, whatever the card's labels (DRE-5366).

THE INCIDENT. On 2026-09-30 DRE-5198 — a one-off with no `agent:planner` label
— was sent back to Planning and waited in the fleet planner line (DRE-5167).
When its turn came, the line's dispatch called `plan_run.fire`, which chose
the event off the labels alone: no `agent:planner`, so `agent-execute`. A
build run started on a card that was waiting to be PLANNED, and moved it to In
Progress with no classification, no plan and no critic.

A card waiting in the planner line is waiting to be planned. Both places that
serve the line — `planner_queue.py dispatch` (plan.yml's end-of-run hand-off,
DRE-5180) and `reconcile.serve_planner_line` (the sweep's backstop, DRE-5178)
— must fire `agent-plan`, and the label rule stays for `fire`'s other callers:
the reconcile re-dispatch of a Todo card and `review_rerun.py dispatch`.

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
import planner_queue  # noqa: E402
import reconcile  # noqa: E402
import review_rerun  # noqa: E402
from test_planner_queue_sweep import THIS, _pin, _waiting, _world  # noqa: E402,F401

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
# The line's dispatch paths                                                    #
# --------------------------------------------------------------------------- #


def _cli_dispatch(record: dict, trigger: str = "Planning", reason: str = "") -> FakeDispatch:
    """`planner_queue.py dispatch` exactly as plan.yml's end-of-run step runs it."""
    posted = []
    with _fake_dispatch() as fake, \
            mock.patch.object(linear_ops, "gql", return_value={"issue": record}), \
            mock.patch.object(planner_queue, "post_dispatched",
                              side_effect=lambda *a, **k: posted.append((a, k))), \
            contextlib.redirect_stdout(io.StringIO()):
        rc = planner_queue.main(["dispatch", record["identifier"], "--run-id", "run-end",
                                 "--repo", REPO, "--trigger-state", trigger,
                                 "--reason", reason])
    assert rc == 0
    assert len(posted) == 1, "a confirmed dispatch posts its `dispatched` receipt"
    return fake


def test_the_end_of_run_hand_off_plans_a_one_off_it_serves_from_the_line():
    fake = _cli_dispatch(_record("DRE-5198", ONE_OFF_LABELS), reason="re-run")
    assert fake.events == ["agent-plan"]
    assert fake.sent[0]["repo"] == REPO
    assert fake.sent[0]["client_payload"]["identifier"] == "DRE-5198"


def test_the_end_of_run_hand_off_plans_an_epic_as_before():
    assert _cli_dispatch(_record("DRE-9001", EPIC_LABELS)).events == ["agent-plan"]


def test_the_end_of_run_hand_off_plans_a_card_with_no_labels_at_all():
    assert _cli_dispatch(_record("DRE-9002", [])).events == ["agent-plan"]


def test_the_end_of_run_hand_off_keeps_the_activate_route_on_the_plan_event():
    fake = _cli_dispatch(_record("DRE-9003", EPIC_LABELS), trigger="in progress",
                         reason="re-review")
    assert fake.events == ["agent-plan"]
    assert fake.sent[0]["client_payload"]["trigger_state"] == "in progress"


def _serve_one_off(ident: str, labels: list) -> FakeDispatch:
    """`reconcile.serve_planner_line` over one waiting card, the real `fire`
    under the fake dispatch."""
    card = _waiting(ident, 30.0, reason="re-run")
    card["labels"] = {"nodes": list(labels)}
    real_fire = plan_run.fire
    with _world([card]) as world, \
            mock.patch.object(reconcile.plan_run, "fire", side_effect=real_fire), \
            _fake_dispatch() as fake:
        reconcile.serve_planner_line()
    served = world.receipts("dispatched")
    assert [ident for ident, _ in served] == [card["identifier"]]
    return fake


def test_the_sweep_backstop_plans_a_one_off_it_serves_from_the_line():
    fake = _serve_one_off("DRE-5314", ONE_OFF_LABELS)
    assert fake.events == ["agent-plan"]
    assert fake.sent[0]["repo"] == THIS
    assert fake.sent[0]["client_payload"]["identifier"] == "DRE-5314"


def test_the_sweep_backstop_plans_an_epic_as_before():
    assert _serve_one_off("DRE-9004", EPIC_LABELS).events == ["agent-plan"]


# --------------------------------------------------------------------------- #
# fire's other callers keep the label rule                                     #
# --------------------------------------------------------------------------- #


def test_reconcile_redispatch_of_a_todo_card_still_fires_agent_execute(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", REPO)
    with _fake_dispatch() as fake:
        assert reconcile.redispatch(_record("DRE-9005", ONE_OFF_LABELS)) is True
    assert fake.events == ["agent-execute"]


def test_reconcile_redispatch_of_a_planner_card_still_fires_agent_plan(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", REPO)
    with _fake_dispatch() as fake:
        assert reconcile.redispatch(_record("DRE-9006", EPIC_LABELS)) is True
    assert fake.events == ["agent-plan"]


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
# No planner-line call site can produce agent-execute                          #
# --------------------------------------------------------------------------- #

# Every call of `plan_run.fire` in scripts/, by (file, enclosing function).
# The planner line's call sites MUST pin the plan event; the others keep the
# label rule. A new call site fails `test_every_fire_call_site_is_classified`
# until it is placed in one of the two sets.
PLANNER_LINE_SITES = {
    ("planner_queue.py", "_cmd_dispatch"),
    ("reconcile.py", "serve_planner_line"),
}
LABEL_RULE_SITES = {
    ("reconcile.py", "redispatch"),
}
# Passes `event=` from its `--route`: `activate` (the default) hands it None,
# which is the label rule; `plan` hands it PLAN_EVENT (DRE-5376).
ROUTED_SITES = {
    ("review_rerun.py", "_cmd_dispatch"),
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
    assert sites == PLANNER_LINE_SITES | LABEL_RULE_SITES | ROUTED_SITES, (
        "a plan_run.fire call site was added or moved — say whether it serves "
        "the planner line (it must pass event=plan_run.PLAN_EVENT) or keeps "
        f"the label rule: {sorted(sites)}")


def test_no_planner_line_call_site_can_produce_agent_execute():
    for name, fn, node in _fire_calls():
        if (name, fn) not in PLANNER_LINE_SITES:
            continue
        event = [kw.value for kw in node.keywords if kw.arg == "event"]
        assert len(event) == 1, f"{name}:{fn} fires without pinning the event"
        value = event[0]
        assert (isinstance(value, ast.Attribute) and value.attr == "PLAN_EVENT"
                and isinstance(value.value, ast.Name) and value.value.id == "plan_run"), (
            f"{name}:{fn} must pass event=plan_run.PLAN_EVENT, not {ast.unparse(value)}")
    assert plan_run.PLAN_EVENT == "agent-plan"


def test_the_label_rule_sites_do_not_pin_an_event():
    for name, fn, node in _fire_calls():
        if (name, fn) in LABEL_RULE_SITES:
            assert not any(kw.arg == "event" for kw in node.keywords), (
                f"{name}:{fn} is a label-rule caller and must fire as it did")
