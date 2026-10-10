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

The caller's BUREAU_MAX_WIP Actions variable comes third (DRE-6631): after the
input and the stub, before the built-in default. It is what the console
writes, and until then only agent-bureau's own stubs read it. A value at any
source that is not a whole number of 0 or more is skipped to the next source
with one `promotion: ignored …` line; an empty one is skipped in silence.

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

    def test_a_cap_that_is_not_a_number_is_skipped_and_says_why(self, tmp_path):
        """DRE-6631: an unreadable stub cap no longer folds its reason into the
        source text — it is skipped to the next source, and the skip is a line
        of its own. With no variable set, the next source is the default."""
        ws = _workspace(tmp_path, _stub("max_wip: ${{ vars.MAX_WIP }}"))
        ignored: list[str] = []
        cap, source = reconcile.cap_and_source("", str(ws), "", ignored=ignored)
        assert cap == reconcile.DEFAULT_MAX_WIP
        assert "default" in source
        assert len(ignored) == 1, ignored
        assert "${{ vars.MAX_WIP }}" in ignored[0]

    def test_this_repo_resolves_through_its_own_self_stub(self):
        """bureau-pipeline self-hosts through self-reconcile.yml, and an empty
        input resolves to the cap that stub writes. The expected number is
        read from the stub itself, so raising the restart cap (3 under
        DRE-5733, 8 under DRE-5954) changes the stub, not this test."""
        stub = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "self-reconcile.yml").read_text())
        written = int(next(iter(stub["jobs"].values()))["with"]["max_wip"])
        cap, source = reconcile.cap_and_source("", str(ROOT))
        assert cap == written
        assert "self-reconcile.yml" in source

    def test_the_module_names_where_its_cap_came_from(self):
        assert isinstance(reconcile.MAX_WIP_SOURCE, str) and reconcile.MAX_WIP_SOURCE


# --------------------------------------------------------------------------
# The four sources, in order (DRE-6631)
# --------------------------------------------------------------------------
NOT_A_NUMBER = ("lots", "-1", "12.0", "${{ vars.SOMETHING }}")


def _resolve(raw, ws, variable):
    """cap_and_source with the run-log lines it would print collected."""
    ignored: list[str] = []
    cap, source = reconcile.cap_and_source(raw, ws, variable, ignored=ignored)
    return cap, source, ignored


class TestTheFourSources:
    """The run's input, else the caller's reconcile stub, else the caller's
    BUREAU_MAX_WIP variable, else reconcile.DEFAULT_MAX_WIP. The variable is
    what the console writes, and before DRE-6631 no reusable read it: only
    agent-bureau's own stubs folded it into the input, so the console could
    change the number for one project of five."""

    def test_the_input_beats_the_stub_and_the_variable(self, tmp_path):
        """agent-bureau, portico and agent-bureau-demo pass a number on every
        stub today; setting their variable must change nothing."""
        ws = _workspace(tmp_path, _stub('max_wip: "0"'))
        cap, source, ignored = _resolve("12", str(ws), "5")
        assert (cap, ignored) == (12, [])
        assert source == "this run's max_wip input"

    def test_the_stub_beats_the_variable_when_the_input_is_empty(self, tmp_path):
        """Portico with a variable set and its literal still standing: the
        sweep gets the literal as its input, so the fast paths, whose stubs
        pass nothing, must read the same literal — not the variable — or the
        repo promotes at two caps again (DRE-2529)."""
        ws = _workspace(tmp_path, _stub('max_wip: "12"'))
        cap, source, ignored = _resolve("", str(ws), "3")
        assert (cap, ignored) == (12, [])
        assert ".github/workflows/reconcile.yml" in source

    def test_the_variable_wins_when_neither_names_a_number(self, tmp_path):
        """atlas and deltasolv: their stubs pass nothing and set no max_wip,
        so the console's number is the cap."""
        ws = _workspace(tmp_path, _stub(None))
        cap, source, ignored = _resolve("", str(ws), "5")
        assert (cap, ignored) == (5, [])
        assert source == "the caller's BUREAU_MAX_WIP variable"

    def test_the_variable_wins_with_no_reconcile_stub_or_no_checkout(self, tmp_path):
        ws = _workspace(tmp_path, None)
        assert _resolve(None, str(ws), "6")[:2] == (6, "the caller's BUREAU_MAX_WIP variable")
        assert _resolve(None, None, "6")[:2] == (6, "the caller's BUREAU_MAX_WIP variable")

    def test_a_variable_of_zero_holds_the_repo(self, tmp_path):
        """Zero is a number, not "unset": agent-bureau-demo held from the
        console must build nothing."""
        ws = _workspace(tmp_path, _stub(None))
        assert _resolve("", str(ws), "0")[:2] == (0, "the caller's BUREAU_MAX_WIP variable")

    def test_the_default_when_all_three_are_empty(self, tmp_path):
        ws = _workspace(tmp_path, _stub(None))
        cap, source, ignored = _resolve("", str(ws), "")
        assert (cap, ignored) == (reconcile.DEFAULT_MAX_WIP, [])
        assert source.startswith(f"the default {reconcile.DEFAULT_MAX_WIP}")
        assert "BUREAU_MAX_WIP" in source

    def test_an_unset_variable_is_the_default_too(self, tmp_path):
        ws = _workspace(tmp_path, _stub(None))
        cap, _source, ignored = _resolve("", str(ws), None)
        assert (cap, ignored) == (reconcile.DEFAULT_MAX_WIP, [])

    def test_empty_values_at_every_source_say_nothing(self, tmp_path):
        """Empty is the normal "not set" — a project with no variable must not
        warn on every sweep. A stub line that names no value is empty too."""
        ws = _workspace(tmp_path, _stub('max_wip: ""'))
        cap, _source, ignored = _resolve("  ", str(ws), "")
        assert (cap, ignored) == (reconcile.DEFAULT_MAX_WIP, [])

    @pytest.mark.parametrize("value", NOT_A_NUMBER)
    def test_an_input_that_is_not_a_number_is_skipped_to_the_stub(self, tmp_path, value):
        ws = _workspace(tmp_path, _stub('max_wip: "3"'))
        cap, source, ignored = _resolve(value, str(ws), "7")
        assert cap == 3
        assert ".github/workflows/reconcile.yml" in source
        assert ignored == [
            f"promotion: ignored this run's max_wip input = {value}: "
            "not a whole number of 0 or more"
        ]

    @pytest.mark.parametrize("value", NOT_A_NUMBER)
    def test_a_stub_cap_that_is_not_a_number_is_skipped_to_the_variable(
        self, tmp_path, value
    ):
        ws = _workspace(tmp_path, _stub(f'max_wip: "{value}"'))
        cap, source, ignored = _resolve("", str(ws), "7")
        assert (cap, source) == (7, "the caller's BUREAU_MAX_WIP variable")
        assert ignored == [
            "promotion: ignored the caller's reconcile stub "
            f".github/workflows/reconcile.yml = {value}: "
            "not a whole number of 0 or more"
        ]

    @pytest.mark.parametrize("value", NOT_A_NUMBER)
    def test_a_variable_that_is_not_a_number_is_skipped_to_the_default(
        self, tmp_path, value
    ):
        ws = _workspace(tmp_path, _stub(None))
        cap, source, ignored = _resolve("", str(ws), value)
        assert cap == reconcile.DEFAULT_MAX_WIP
        assert source.startswith(f"the default {reconcile.DEFAULT_MAX_WIP}")
        assert ignored == [
            f"promotion: ignored the caller's BUREAU_MAX_WIP variable = {value}: "
            "not a whole number of 0 or more"
        ]

    def test_every_source_unreadable_names_each_one_in_order(self, tmp_path):
        ws = _workspace(tmp_path, _stub('max_wip: "lots"'))
        cap, _source, ignored = _resolve("-1", str(ws), "12.0")
        assert cap == reconcile.DEFAULT_MAX_WIP
        assert [line.split(" = ")[0] for line in ignored] == [
            "promotion: ignored this run's max_wip input",
            "promotion: ignored the caller's reconcile stub .github/workflows/reconcile.yml",
            "promotion: ignored the caller's BUREAU_MAX_WIP variable",
        ]

    def test_the_module_reads_the_variable_from_the_environment(self):
        """The one module-level call passes BUREAU_MAX_WIP as its third
        argument — the name each promotion step's env: carries."""
        src = (ROOT / "scripts" / "reconcile.py").read_text()
        call = src.split("MAX_WIP, MAX_WIP_SOURCE = cap_and_source(", 1)[1].split("\n)\n", 1)[0]
        assert 'os.environ.get("BUREAU_MAX_WIP")' in call
        assert isinstance(reconcile.MAX_WIP_IGNORED, list)


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

    def __init__(self, tmp_path, stub_line, *, children=3, active=0,
                 max_wip="", variable=""):
        self.ignored: list[str] = []
        self.cap, self.source = reconcile.cap_and_source(
            max_wip, str(_workspace(tmp_path, _stub(stub_line))), variable,
            ignored=self.ignored,
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
        ), patch.object(
            reconcile, "MAX_WIP_IGNORED", self.ignored
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

    def test_the_run_log_names_the_variable_when_it_set_the_cap(
        self, tmp_path, capsys
    ):
        """DRE-6631: a project whose stubs pass nothing promotes at its
        BUREAU_MAX_WIP, and the cap line says that is where it came from."""
        run = _Activation(tmp_path, None, children=3, active=0, variable="2")
        run.approve()
        assert run.advanced == ["DRE-3800", "DRE-3801"]
        out = capsys.readouterr().out
        said = [ln for ln in out.splitlines() if ln.startswith("promotion: WIP cap ")]
        assert said == ["promotion: WIP cap 2, read from the caller's BUREAU_MAX_WIP variable"]

    def test_an_ignored_value_is_logged_before_the_cap_line(self, tmp_path, capsys):
        run = _Activation(tmp_path, 'max_wip: "lots"', variable="1")
        run.approve()
        assert run.advanced == ["DRE-3800"]
        lines = capsys.readouterr().out.splitlines()
        ignored = [i for i, ln in enumerate(lines) if ln.startswith("promotion: ignored ")]
        cap = [i for i, ln in enumerate(lines) if ln.startswith("promotion: WIP cap ")]
        assert len(ignored) == 1 and len(cap) == 1, lines
        assert ignored[0] < cap[0]
        assert lines[ignored[0]] == (
            "promotion: ignored the caller's reconcile stub "
            ".github/workflows/reconcile.yml = lots: not a whole number of 0 or more"
        )

    def test_an_unset_variable_logs_no_ignored_line(self, tmp_path, capsys):
        run = _Activation(tmp_path, None)
        run.approve()
        assert "promotion: ignored" not in capsys.readouterr().out

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

    @pytest.mark.parametrize("name", PROMOTING_REUSABLES)
    def test_every_promoting_step_carries_the_callers_variable(self, name):
        """In a reusable workflow `vars` is the caller's, so this one line is
        how the console's number reaches the script — beside the input, never
        folded into it (DRE-6631)."""
        doc = _doc(name)
        found = 0
        for job_id, job in (doc.get("jobs") or {}).items():
            for step in (job or {}).get("steps") or []:
                run = step.get("run")
                if isinstance(run, str) and "reconcile.py" in run and (
                    "--promote-only" in run or name == "reconcile.yml"
                ):
                    found += 1
                    env = {**(doc.get("env") or {}), **(job.get("env") or {}),
                           **(step.get("env") or {})}
                    assert env.get("BUREAU_MAX_WIP") == "${{ vars.BUREAU_MAX_WIP }}", (
                        f"{name}: jobs.{job_id} promotes without the caller's BUREAU_MAX_WIP"
                    )
                    assert env.get("MAX_WIP") == "${{ inputs.max_wip }}", name
        assert found >= 1, f"{name}: no promotion step found — the check went vacuous"
