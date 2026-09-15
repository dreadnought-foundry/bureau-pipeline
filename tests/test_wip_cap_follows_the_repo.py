"""A repo's WIP cap holds on EVERY path that promotes, not just the sweep (DRE-3994).

What leaked. On 2026-09-15 at about 7:52 AM PT the CEO approved Portico epic
DRE-3777. Portico was held at `max_wip: "0"` — every reconcile sweep for a week
had printed "WIP at cap (5/0) — none promoted" — and yet the approval promoted
three unrelated Portico cards to build (DRE-3445, DRE-3822, DRE-3823).

Why. The cap is a per-repo value, and the only place a product repo writes it
is the `max_wip:` line of its reconcile.yml STUB. The two event-driven promotion
paths — plan.yml's activate route and linear-sync.yml's merge handler — run the
same `reconcile.py --promote-only`, but each takes its cap from ITS OWN stub's
`max_wip` input, and no product stub passes one there (atlas holds reconcile at
"0", agent-bureau-demo at "0", deltasolv at "4"; their plan.yml and
linear-sync.yml stubs pass nothing). So both paths ran at the reusable's
declared default of "8". DRE-2529 made the three paths take the SAME INPUT; it
could not make three stubs pass the same VALUE.

What holds now. When a run's `max_wip` input is empty, reconcile.py reads the
cap from the caller's own reconcile.yml stub — the value the sweep runs at — in
the caller checkout every one of those jobs already has at $GITHUB_WORKSPACE.
The reusables declare no default, so a silent stub renders an empty input and
this fallback actually runs. No product stub has to change. The promotion step
prints the cap it applied and where it read it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_wip_cap_follows_the_repo.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
PROMOTING_REUSABLES = ("plan.yml", "linear-sync.yml", "reconcile.yml")

FLEET = routing_verdict.verdict_comment("FLEET", "the acceptance criteria are unit-testable")
GREEN_LIGHT = "2026-08-01T00:00:00.000Z"
CREATED = "2026-07-01T00:00:00.000Z"  # before the green light: never a mid-epic addition


# --------------------------------------------------------------------------
# Caller workspaces, shaped like the live product stubs
# --------------------------------------------------------------------------
def _stub(max_wip_line: str | None, *, ref: str = "stable") -> str:
    """A reconcile.yml trigger stub. `max_wip_line` is written verbatim under
    `with:`; None leaves it out — and leaves a COMMENT naming max_wip, the way
    portico's stub does, because a reader that matched comments would read a
    cap nobody set."""
    held = f"      {max_wip_line}\n" if max_wip_line is not None else (
        "      # No max_wip: the reusable's default applies. Held at \"0\" from\n"
        "      # 2026-09-08 until 2026-09-15. To hold again, set max_wip here.\n"
    )
    return (
        "name: Reconcile\n"
        "on:\n"
        "  schedule:\n"
        "    - cron: \"*/15 * * * *\"\n"
        "jobs:\n"
        "  call:\n"
        f"    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/reconcile.yml@{ref}\n"
        "    with:\n"
        f"      pipeline_ref: {ref}\n"
        f"{held}"
        "      intake_hold: ${{ vars.INTAKE_HOLD }}\n"
        "    secrets: inherit\n"
    )


PLAN_STUB = (
    "name: Agent Plan\n"
    "on:\n"
    "  repository_dispatch:\n"
    "    types: [agent-plan]\n"
    "jobs:\n"
    "  call:\n"
    "    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/plan.yml@stable\n"
    "    with:\n"
    "      pipeline_ref: stable\n"
    "    secrets: inherit\n"
)


def _workspace(tmp_path: Path, reconcile_stub: str | None) -> Path:
    """A caller checkout: a plan.yml stub that passes no cap (every product
    repo today) and, optionally, its reconcile.yml stub."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "plan.yml").write_text(PLAN_STUB)
    if reconcile_stub is not None:
        (wf / "reconcile.yml").write_text(reconcile_stub)
    return tmp_path


# --------------------------------------------------------------------------
# The resolver
# --------------------------------------------------------------------------
class TestTheCapIsReadFromTheRepo:
    def test_a_silent_plan_stub_in_a_repo_held_at_zero_gets_zero(self, tmp_path):
        """THE DEFECT: the activate route's input is empty (the stub passed
        nothing), the repo's reconcile stub says "0" — so the cap is 0."""
        ws = _workspace(tmp_path, _stub('max_wip: "0"'))
        cap, source = reconcile.cap_and_source("", str(ws))
        assert cap == 0
        assert ".github/workflows/reconcile.yml" in source

    def test_an_unset_input_reads_the_stub_too(self, tmp_path):
        ws = _workspace(tmp_path, _stub('max_wip: "4"'))
        assert reconcile.cap_and_source(None, str(ws))[0] == 4

    def test_an_unquoted_cap_and_a_trailing_comment_both_read(self, tmp_path):
        ws = _workspace(tmp_path, _stub("max_wip: 3  # lower while phase 1 lands"))
        assert reconcile.cap_and_source("", str(ws))[0] == 3

    def test_an_explicit_input_still_wins(self, tmp_path):
        """agent-bureau passes "12" on all three stubs; that stays the cap."""
        ws = _workspace(tmp_path, _stub('max_wip: "0"'))
        cap, source = reconcile.cap_and_source("12", str(ws))
        assert cap == 12
        assert "input" in source

    def test_a_stub_that_passes_no_cap_is_the_one_default(self, tmp_path):
        """Portico since DRE-3993: no max_wip line, only a comment naming it.
        The comment must not be read as a cap."""
        ws = _workspace(tmp_path, _stub(None))
        cap, source = reconcile.cap_and_source("", str(ws))
        assert cap == reconcile.DEFAULT_MAX_WIP
        assert ".github/workflows/reconcile.yml" in source
        assert "default" in source

    def test_no_workspace_is_the_one_default(self):
        cap, source = reconcile.cap_and_source("", None)
        assert cap == reconcile.DEFAULT_MAX_WIP
        assert "default" in source

    def test_a_checkout_with_no_reconcile_stub_is_the_one_default(self, tmp_path):
        ws = _workspace(tmp_path, None)
        cap, source = reconcile.cap_and_source("", str(ws))
        assert cap == reconcile.DEFAULT_MAX_WIP
        assert "no reconcile stub" in source

    def test_a_cap_that_is_not_a_number_is_the_default_and_says_why(self, tmp_path):
        ws = _workspace(tmp_path, _stub("max_wip: ${{ vars.MAX_WIP }}"))
        cap, source = reconcile.cap_and_source("", str(ws))
        assert cap == reconcile.DEFAULT_MAX_WIP
        assert "vars.MAX_WIP" in source

    def test_this_repo_resolves_through_its_own_self_stub(self):
        """bureau-pipeline self-hosts through self-reconcile.yml, which passes
        no cap — so it stays at the one default, exactly as today."""
        cap, source = reconcile.cap_and_source("", str(ROOT))
        assert cap == reconcile.DEFAULT_MAX_WIP
        assert "self-reconcile.yml" in source

    def test_the_module_names_where_its_cap_came_from(self):
        assert isinstance(reconcile.MAX_WIP_SOURCE, str) and reconcile.MAX_WIP_SOURCE


# --------------------------------------------------------------------------
# The behaviour: an epic is approved, and the cap decides what builds
# --------------------------------------------------------------------------
def _child(identifier: str) -> dict:
    """A Backlog child of an APPROVED epic, eligible on every other ground."""
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "work",
        "description": "work",
        "createdAt": CREATED,
        "parent": {"identifier": "DRE-3777", "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": "repo:bureau-pipeline"}, {"name": "agent:engineer"}]},
        "comments": {"nodes": [{"body": FLEET}]},
        "inverseRelations": {"nodes": []},
    }


class _Activation:
    """`reconcile.main(promote_only=True)` — exactly what plan.yml's activate
    step runs — over an approved epic's children, at the cap the repo's own
    stub resolves to, with every gate but the cap held open."""

    def __init__(self, tmp_path, stub_line, *, children=3, active=0):
        self.cap, self.source = reconcile.cap_and_source(
            "", str(_workspace(tmp_path, _stub(stub_line)))
        )
        self.children = [_child(f"DRE-38{n:02d}") for n in range(children)]
        self.active = [
            {
                "identifier": f"DRE-37{n:02d}",
                "title": "work",
                "description": "work",
                "state": {"name": "In Progress"},
                "children": {"nodes": []},
                "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
                "updatedAt": "2026-09-15T00:00:00Z",
            }
            for n in range(active)
        ]
        self.advanced: list[str] = []

    def approve(self) -> None:
        with patch.object(reconcile, "MAX_WIP", self.cap), patch.object(
            reconcile, "MAX_WIP_SOURCE", self.source
        ), patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "active_cards", return_value=self.active
        ), patch.object(
            reconcile, "backlog_children", return_value=self.children
        ), patch.object(
            reconcile, "merged_card_scope", return_value=None
        ), patch.object(
            reconcile, "epic_blockers_unmet", return_value=False
        ), patch.object(
            reconcile, "epic_thread", return_value=[]
        ), patch.object(
            reconcile.plan_critic, "promotion_refusal", return_value=None
        ), patch.object(
            reconcile.mid_epic, "last_green_light", return_value=GREEN_LIGHT
        ), patch.object(
            reconcile.linear_ops, "cmd_advance",
            side_effect=lambda ident, to, frm: self.advanced.append(ident),
        ), patch.object(
            reconcile.linear_ops, "cmd_comment"
        ), patch.object(
            reconcile.linear_ops, "count_comments", return_value=0
        ):
            reconcile.main(promote_only=True)


@pytest.fixture(autouse=True)
def _clear_failures():
    reconcile._write_failures.clear()
    reconcile._card_skips.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._card_skips.clear()


class TestEpicApprovalAtTheCap:
    def test_a_repo_held_at_zero_builds_nothing_when_an_epic_is_approved(
        self, tmp_path, capsys
    ):
        """DRE-3777's approval, replayed: held at "0", nothing in flight,
        three eligible children — none of them may leave Backlog."""
        run = _Activation(tmp_path, 'max_wip: "0"', children=3, active=0)
        run.approve()
        assert run.advanced == []
        out = capsys.readouterr().out
        assert "WIP at cap (0/0)" in out

    def test_the_run_says_which_cap_it_applied_and_where_it_read_it(
        self, tmp_path, capsys
    ):
        run = _Activation(tmp_path, 'max_wip: "0"')
        run.approve()
        out = capsys.readouterr().out
        said = [ln for ln in out.splitlines() if "WIP cap 0" in ln]
        assert len(said) == 1, out
        assert ".github/workflows/reconcile.yml" in said[0]

    def test_at_a_non_zero_cap_with_the_cap_in_flight_nothing_more_builds(
        self, tmp_path, capsys
    ):
        run = _Activation(tmp_path, 'max_wip: "2"', children=3, active=2)
        run.approve()
        assert run.advanced == []
        assert "WIP at cap (2/2)" in capsys.readouterr().out

    def test_under_the_cap_it_promotes_up_to_the_cap_and_no_further(self, tmp_path):
        run = _Activation(tmp_path, 'max_wip: "2"', children=3, active=0)
        run.approve()
        assert run.advanced == ["DRE-3800", "DRE-3801"]

    def test_the_slot_left_under_the_cap_is_the_only_slot_used(self, tmp_path):
        run = _Activation(tmp_path, 'max_wip: "2"', children=3, active=1)
        run.approve()
        assert run.advanced == ["DRE-3800"]


# --------------------------------------------------------------------------
# The contract that makes the fallback reachable in a real run
# --------------------------------------------------------------------------
def _doc(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _on(doc: dict) -> dict:
    return doc.get("on", doc.get(True)) or {}


class TestTheReusablesLetTheRepoSpeak:
    @pytest.mark.parametrize("name", PROMOTING_REUSABLES)
    def test_the_max_wip_input_declares_no_default(self, name):
        """A declared default renders "8" into MAX_WIP whenever a stub is
        silent, and the script can then never tell "the repo said 8" from
        "the repo said nothing" — which is the whole leak."""
        spec = _on(_doc(name))["workflow_call"]["inputs"]["max_wip"]
        assert "default" not in spec, f"{name}: max_wip still defaults to {spec['default']!r}"

    @pytest.mark.parametrize("name", PROMOTING_REUSABLES)
    def test_every_promoting_job_has_the_caller_checked_out_first(self, name):
        """The fallback reads the caller's stub off disk, so the job that
        promotes must check out the CALLER (a checkout with no `repository:`)
        before the step that runs reconcile.py."""
        doc = _doc(name)
        found = 0
        for job_id, job in (doc.get("jobs") or {}).items():
            steps = (job or {}).get("steps") or []
            caller_checked_out = False
            for step in steps:
                uses = str(step.get("uses", ""))
                if uses.startswith("actions/checkout@") and not (
                    step.get("with") or {}
                ).get("repository"):
                    caller_checked_out = True
                run = step.get("run")
                if isinstance(run, str) and "reconcile.py" in run and (
                    "--promote-only" in run or name == "reconcile.yml"
                ):
                    found += 1
                    assert caller_checked_out, (
                        f"{name}: jobs.{job_id} promotes before checking out the caller"
                    )
        assert found >= 1, f"{name}: no promotion step found — the check went vacuous"
