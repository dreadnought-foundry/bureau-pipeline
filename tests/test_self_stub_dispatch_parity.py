"""RED-first tests for DRE-2056 — every dispatch the sweep makes must target
a workflow that is actually dispatchable in the SELF-HOST repo.

THE BUG (live, 2026-07-12, run 29198533233):
reconcile's unstick_conflicts dispatched `gh workflow run agent-fix.yml`
against bureau-pipeline itself — but in this repo that filename IS the
reusable definition (workflow_call only), so GitHub answered
"HTTP 422: Workflow does not have 'workflow_dispatch' trigger" and the
DRE-1979 fail-loudly rail turned every sweep red. The dispatchable stub
(self-agent-fix.yml, with workflow_dispatch + pr_number since DRE-1929)
was never the target. Exactly the DRE-2047 qa-review.yml/pr-review.yml
lesson, re-fired on a different workflow — and merge-gate.yml's DIRTY arm
plus the sweep's merge-gate nudges carry the same class of literal.

FIX UNDER TEST (three legs):
  1. reconcile.fix_workflow() / reconcile.gate_workflow() resolve the
     DISPATCHABLE stub filename per repo, exactly like review_workflow():
     product repos keep the fleet names (agent-fix.yml / merge-gate.yml);
     bureau-pipeline resolves to self-agent-fix.yml / self-merge-gate.yml.
     Every fix dispatch, busy-guard `run list`, and merge-gate nudge in the
     sweep routes through the resolvers.
  2. merge-gate.yml's DIRTY-branch arm resolves the same way in shell —
     its `|| true` made the 422 SILENT there, so conflicts on this repo's
     own PRs never reached the fix agent at all.
  3. Parity guard: every stub the sweep can dispatch in this repo must
     declare workflow_dispatch with a required pr_number input, and no
     dispatch site may literally name a workflow_call-only file — the
     structural rail against the NEXT missing-trigger stub.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / "scripts"

# fleet filename -> the stub that is dispatchable in THIS repo. The fleet
# name here is the workflow_call reusable itself, so a dispatch must resolve.
SELF_DISPATCH_TARGETS = {
    "qa-review.yml": "pr-review.yml",
    "agent-fix.yml": "self-agent-fix.yml",
    "merge-gate.yml": "self-merge-gate.yml",
}


def _on(doc: dict) -> dict:
    # YAML 1.1 parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _load(name: str) -> dict:
    path = WORKFLOWS / name
    assert path.is_file(), f"missing workflow {name}"
    return yaml.safe_load(path.read_text())


class TestResolvers:
    """fix_workflow()/gate_workflow() mirror review_workflow() (DRE-2047)."""

    def test_fix_workflow_resolves_self_stub_on_bureau_pipeline(self, monkeypatch):
        monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
        assert reconcile.fix_workflow() == "self-agent-fix.yml"

    def test_fix_workflow_keeps_fleet_name_on_product_repos(self, monkeypatch):
        monkeypatch.setattr(reconcile, "REPO_SLUG", "atlas")
        assert reconcile.fix_workflow() == "agent-fix.yml"

    def test_gate_workflow_resolves_self_stub_on_bureau_pipeline(self, monkeypatch):
        monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
        assert reconcile.gate_workflow() == "self-merge-gate.yml"

    def test_gate_workflow_keeps_fleet_name_on_product_repos(self, monkeypatch):
        monkeypatch.setattr(reconcile, "REPO_SLUG", "atlas")
        assert reconcile.gate_workflow() == "merge-gate.yml"


def _fake_run_factory(calls: list):
    """subprocess.run stub: records every gh call; one DIRTY agent PR."""

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "run" and argv[2] == "list":
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")
        if argv[1] == "pr" and argv[2] == "list":
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    '[{"number": 103, '
                    '"headRefName": "agent/DRE-2040-something", '
                    '"mergeStateStatus": "DIRTY"}]'
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return fake_run


class TestUnstickConflictsSelfHost:
    """The exact site that fired: unstick_conflicts on bureau-pipeline must
    dispatch (and busy-check) the self stub, never the reusable."""

    def _sweep(self, monkeypatch) -> list:
        calls: list = []
        monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
        monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/bureau-pipeline")
        with patch.object(
            reconcile.subprocess, "run", side_effect=_fake_run_factory(calls)
        ), patch.object(reconcile, "card_parked_for_human", return_value=False):
            reconcile.unstick_conflicts()
        return calls

    def test_dispatch_targets_the_self_stub(self, monkeypatch):
        calls = self._sweep(monkeypatch)
        dispatches = [c for c in calls if c[1] == "workflow" and c[2] == "run"]
        assert dispatches, "the DIRTY PR must trigger a dispatch"
        assert dispatches[0][3] == "self-agent-fix.yml", (
            "on bureau-pipeline agent-fix.yml is the workflow_call reusable "
            "— dispatching it 422s (run 29198533233); the stub is "
            "self-agent-fix.yml"
        )

    def test_busy_guard_watches_the_self_stub(self, monkeypatch):
        calls = self._sweep(monkeypatch)
        run_lists = [c for c in calls if c[1] == "run" and c[2] == "list"]
        assert run_lists, "unstick_conflicts starts with a busy-guard run list"
        joined = " ".join(run_lists[0])
        assert "--workflow self-agent-fix.yml" in joined, (
            "the busy guard must watch the stub that actually produces runs "
            "here — the reusable never runs under its own name, so watching "
            "it reads permanently idle"
        )


class TestSelfStubDispatchParity:
    """Every stub the sweep can dispatch in this repo must accept exactly
    the dispatch the sweep makes: workflow_dispatch + required pr_number."""

    @pytest.mark.parametrize("fleet,stub", sorted(SELF_DISPATCH_TARGETS.items()))
    def test_self_stub_accepts_workflow_dispatch_with_pr_number(self, fleet, stub):
        on = _on(_load(stub))
        assert "workflow_dispatch" in on, (
            f"{stub} must carry workflow_dispatch — the sweep dispatches "
            f"{fleet} against product repos and {stub} here"
        )
        inputs = (on.get("workflow_dispatch") or {}).get("inputs") or {}
        assert "pr_number" in inputs, f"{stub} workflow_dispatch needs a pr_number input"
        assert inputs["pr_number"].get("required") is True, (
            f"{stub} pr_number input must be required — every sweep dispatch "
            f"passes -f pr_number=N"
        )

    @pytest.mark.parametrize("fleet,stub", sorted(SELF_DISPATCH_TARGETS.items()))
    def test_resolution_is_actually_needed_here(self, fleet, stub):
        """If the fleet-named file ever becomes dispatchable (or the mapping
        goes stale), this table is wrong — fail so it gets re-decided."""
        assert set(_on(_load(fleet))) == {"workflow_call"}, (
            f"{fleet} is expected to be the workflow_call-only reusable in "
            f"this repo; if that changed, revisit SELF_DISPATCH_TARGETS"
        )
        assert (WORKFLOWS / stub).is_file()

    def test_resolvers_cover_the_full_mapping(self, monkeypatch):
        """The sweep-side resolvers and this test's table must agree."""
        monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
        resolved = {
            "qa-review.yml": reconcile.review_workflow(),
            "agent-fix.yml": reconcile.fix_workflow(),
            "merge-gate.yml": reconcile.gate_workflow(),
        }
        assert resolved == SELF_DISPATCH_TARGETS


class TestNoLiteralDispatchOfReusables:
    """Structural rail: a dispatch site that literally names a workflow file
    must name one that is dispatchable IN THIS REPO. Fleet-only names must
    route through a resolver (scripts) or a shell resolution (workflows) —
    a new literal reintroduces the 422 class this card fixed."""

    # the dispatch shapes that exist in this codebase; a literal .yml in any
    # of them is a workflow this repo must be able to run/list under that name
    PY_PATTERNS = (
        r'"workflow",\s*"run",\s*"([\w.-]+\.yml)"',   # gh_dispatch sites
        r'_nudge\(\s*"([\w.-]+\.yml)"',               # merge-gate/review nudges
        r'"--workflow",\s*"([\w.-]+\.yml)"',          # busy-guard run lists
    )
    WF_PATTERN = r"gh workflow run\s+([\w.-]+\.yml)"

    def _assert_dispatchable(self, name: str, where: str):
        assert (WORKFLOWS / name).is_file(), f"{where} dispatches missing workflow {name}"
        assert "workflow_dispatch" in _on(_load(name)), (
            f"{where} literally dispatches {name}, which has no "
            f"workflow_dispatch trigger in this repo (HTTP 422, the "
            f"DRE-2056 class) — route it through a per-repo resolver"
        )

    def test_scripts_never_hardcode_a_workflow_call_only_target(self):
        for path in sorted(SCRIPTS.glob("*.py")):
            src = path.read_text()
            for pattern in self.PY_PATTERNS:
                for name in re.findall(pattern, src):
                    self._assert_dispatchable(name, f"scripts/{path.name}")

    def test_workflows_never_hardcode_a_workflow_call_only_target(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for name in re.findall(self.WF_PATTERN, path.read_text()):
                self._assert_dispatchable(name, f".github/workflows/{path.name}")


class TestMergeGateDirtyArmResolves:
    """merge-gate.yml's conflict dispatch must resolve the stub per repo —
    its `|| true` swallowed the 422, so self-repo conflicts silently never
    reached the fix agent (reconcile's loud sweep was the only signal)."""

    def test_dirty_arm_resolves_self_stub(self):
        doc = _load("merge-gate.yml")
        runs = [
            s["run"]
            for s in doc["jobs"]["evaluate"]["steps"]
            if s.get("name") == "Evaluate and merge"
        ]
        assert len(runs) == 1
        block = runs[0]
        assert "self-agent-fix.yml" in block, (
            "the DIRTY arm must dispatch self-agent-fix.yml when the target "
            "repo is bureau-pipeline itself"
        )
        assert 'gh workflow run "$FIX_WF"' in block, (
            "the dispatch must go through the resolved variable, not a "
            "literal fleet filename"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
