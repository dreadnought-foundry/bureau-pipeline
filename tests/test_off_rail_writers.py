"""RED-first: a sweep off the routing rail skips its fleet-wide board writers
(DRE-3632).

THE PROMISE. The harness sandbox, `bureau-harness`, runs the reusable sweep
with a `REPO_SLUG` that is not a key of `config/repo-map.json`, and
`scripts/harness/README.md` promises it makes zero Linear writes. The 130+
cards aged out of Intake on 2026-09-09/10 were that promise broken by ONE
fleet-wide writer. The age-out is gone (DRE-4141), but a full pass still has
phases that write to cards this repo does not own, because an unlabelled card
is "everybody's" (`_another_repos_card`). Read function by function on
2026-10-01 there are exactly eight, in the order `main()` runs them — the
names in `OFF_RAIL_SKIPPED`.

WHAT THESE TESTS PIN:

  * `off_rail()` is True for the sandbox's slug and False for every key of
    the real `config/repo-map.json` — iterated off the file, never restated.
  * A FULL `main()` under `REPO_SLUG=bureau-harness`, over a board holding a
    trigger card for every one of the eight, prints exactly eight
    `off-rail:` lines, makes none of the writes, records no failure, exits 0,
    and prints no `sweep-spend:` line for any of the eight phases.
  * For each of the eight, one board holding only that phase's trigger card,
    run twice: under `agent-bureau` the phase makes its write and no
    `off-rail:` line is printed; under `bureau-harness` exactly one line names
    the phase and nothing is written. So every fixture is one the fence
    actually stops — a fixture that wrote nothing on the rail would prove the
    fence by doing nothing.
  * `OFF_RAIL_SKIPPED` is exactly the eight names, each a function defined in
    `scripts/reconcile.py`, each reached from `main()` as a backstop-tuple
    member or a `_phase("<name>")` literal — a renamed, removed or relocated
    phase fails by name.

Every trigger card is built from the fixture of the suite that owns its
phase, imported rather than restated, so the shapes move with those suites.

Run: cd bureau-pipeline && python3 -m pytest tests/test_off_rail_writers.py -v
"""
from __future__ import annotations

import ast
import contextlib
import itertools
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402
from test_intake_no_age_out import THIRTY_DAYS, _main_mocks  # noqa: E402
from test_intake_no_age_out import _card as intake_card  # noqa: E402
from test_intake_urgent_fast_path import STANDING, _ship  # noqa: E402
from test_intake_urgent_fast_path import Board as UrgentBoard  # noqa: E402
from test_intake_urgent_fast_path import card as urgent_card  # noqa: E402
from test_lane_fold_in_review import FAKE_RETIRING, _retiring, _stranded  # noqa: E402
from test_limit_recovery import card as limit_card  # noqa: E402
from test_limit_recovery import marker  # noqa: E402
from test_planner_queue_sweep import TTL, _claimed  # noqa: E402
from test_reconcile_epic_carry import _board  # noqa: E402
from test_reconcile_epic_carry import _card as epic_card  # noqa: E402
from test_reconcile_reviewer_down import (  # noqa: E402
    ATLAS_RUN_URL,
    _environment_note,
    _iso,
    _ledger_for,
    _open_card_payload,
)
from test_reconcile_reviewer_down import _card as outage_witness  # noqa: E402

SANDBOX = "bureau-harness"
ON_RAIL = "agent-bureau"
THIS = "dreadnought-foundry/agent-bureau"

#: The eight, in the order `main()` runs them. Spelled out ONCE, here, as the
#: card's contract — the mapping in reconcile.py is checked against it.
EIGHT = (
    "drain_retiring_lanes",
    "recover_limit_deaths",
    "report_fleet_reviewer_outage",
    "flag_stranded",
    "advance_urgent_intake",
    "repair_frozen_planning_holds",
    "serve_planner_line",
    "carry_epics_out_of_todo",
)

#: Every write the card names. A sweep off the rail calls none of them.
WRITE_SEAMS = (
    "cmd_advance", "cmd_state", "cmd_comment", "add_label", "remove_label",
    "set_title", "find_open_prefix", "post_released", "fire",
    "post_refusal",
)


# --------------------------------------------------------------------------
# the eight trigger cards — one per phase, each from its own suite's fixture
# --------------------------------------------------------------------------
def _drain_card():
    """A card stranded in the injected retiring lane (the `_retiring`
    pattern): the drain moves it whoever owns it."""
    return _stranded(FAKE_RETIRING["name"])


def _limit_card():
    """An UNLABELLED card whose newest limit-death marker's reset has passed."""
    return limit_card(ident="DRE-3171", lane="In Progress", labels=(),
                      bodies=[marker(reset=datetime(2026, 1, 1, tzinfo=UTC))])


def _outage_witness():
    """A could-not-run witness inside the window: another repository's run,
    named in the medic's evidence note on an atlas card."""
    return outage_witness(identifier="DRE-3435", repo="atlas",
                          bodies=[(_environment_note(ATLAS_RUN_URL), _iso(6))])


def _open_outage_card():
    """The fleet's one open outage card, with a ledger the witness is not on —
    so the decision is to APPEND to it."""
    return _open_card_payload([
        _ledger_for(ON_RAIL, 141, _iso(14), "u1"),
        _ledger_for(ON_RAIL, 142, _iso(12), "u2"),
    ], filed_minutes_ago=13)


def _stalled_planning_card():
    """An unlabelled Planning card past its stall window."""
    return intake_card(identifier="DRE-2736", state="Planning",
                       minutes_stale=THIRTY_DAYS)


def _urgent_card():
    """A non-epic Intake card raised to Urgent after the ship moment."""
    return urgent_card("DRE-4150", raised=_ship() + timedelta(hours=2))


def _frozen_card():
    """A Planning card carrying HOLD_LABEL and the watchdog's own receipt."""
    frozen = intake_card(identifier="DRE-4124", state="Planning",
                         labels=(reconcile.HOLD_LABEL,), minutes_stale=THIRTY_DAYS)
    frozen["comments"] = {"nodes": [{
        "body": f"🚨 {reconcile.WATCHDOG_TAG}: planning has produced nothing.",
        "createdAt": _iso(THIRTY_DAYS),
    }]}
    return frozen


def _expired_claim_card():
    """An open `planner-slot: claimed` receipt older than the line's TTL."""
    return _claimed("DRE-5178", "777", minutes_ago=TTL + 30, repo=THIS)


def _epic_in_todo():
    """An `[EPIC]` card in Todo with no `repo:` label — everybody's."""
    return epic_card("DRE-5347", title="[EPIC] an epic somebody dragged into Todo",
                     labels=())


#: phase → (its trigger cards, the write that proves it acted, on which card).
TRIGGERS = {
    "drain_retiring_lanes": ((_drain_card,), "cmd_advance", "DRE-9999"),
    "recover_limit_deaths": ((_limit_card,), "cmd_state", "DRE-3171"),
    "report_fleet_reviewer_outage": ((_outage_witness,), "set_title", "DRE-9500"),
    # The stall exit parks in Triage through cmd_state itself (DRE-5286).
    "flag_stranded": ((_stalled_planning_card,), "cmd_state", "DRE-2736"),
    "advance_urgent_intake": ((_urgent_card,), "cmd_advance", "DRE-4150"),
    "repair_frozen_planning_holds": ((_frozen_card,), "remove_label", "DRE-4124"),
    "serve_planner_line": ((_expired_claim_card,), "post_released", "DRE-5178"),
    "carry_epics_out_of_todo": ((_epic_in_todo,), "post_refusal", "DRE-5347"),
}


# --------------------------------------------------------------------------
# the world a full pass runs in
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", THIS)
    monkeypatch.setattr(reconcile, "REPO_SLUG", ON_RAIL)
    monkeypatch.setattr(reconcile, "FLEET_OUTAGE_SWEEP_CAP", 1, raising=False)
    monkeypatch.setattr(reconcile.planner_queue, "cap", lambda: 4)
    monkeypatch.setenv(reconcile.GROOM_CARD_ENV, STANDING)
    for name in ("INTAKE_HOLD", "MERGED_CARD", "SWEEP_REASON", "SWEEP_CARD",
                 "CLAUDE_ACCOUNT", reconcile.planner_queue.CONFIG_ENV):
        monkeypatch.delenv(name, raising=False)
    ledgers = (reconcile._write_failures, reconcile._read_failures,
               reconcile._stale_defects)
    for ledger in ledgers:
        ledger.clear()
    reconcile.reset_sweep_cards()
    yield
    for ledger in ledgers:
        ledger.clear()


def _gh(*args):
    """GitHub for the reads the live phases make: no open pull request."""
    if args[:2] == ("pr", "list"):
        return "[]"
    return ""


#: The GitHub-side backstops `_main_mocks` does not already stand down. They
#: act on this repo's own pull requests and are not what this suite is about.
_ALSO_STOOD_DOWN = (
    "refresh_stale_merge_refs", "flag_stranded_fixes",
    "redispatch_standing_verdicts", "settle_repair_cards",
    "move_hand_built_to_review",
)


@contextlib.contextmanager
def _world(cards, slug):
    """A whole `main()` pass over `cards` under `slug`, every write recorded.

    Yields `(writes, result)`: `writes` maps each seam in WRITE_SEAMS to its
    mock, and `result` fills in `red` once the pass has run."""
    urgent = UrgentBoard([c for c in cards if "history" in c])
    rows = [urgent._board_row(c) if "history" in c else c for c in cards]
    open_card = _open_outage_card()
    writes = {
        "cmd_advance": mock.MagicMock(),
        "cmd_state": mock.MagicMock(),
        "cmd_comment": mock.MagicMock(),
        "add_label": mock.MagicMock(),
        "remove_label": mock.MagicMock(),
        "set_title": mock.MagicMock(),
        "find_open_prefix": mock.MagicMock(return_value=open_card),
        "post_released": mock.MagicMock(),
        "fire": mock.MagicMock(return_value=(True, "")),
        "post_refusal": mock.MagicMock(return_value=True),
    }
    result = SimpleNamespace(red=False)
    lops = reconcile.linear_ops
    with contextlib.ExitStack() as stack:
        enter = stack.enter_context
        enter(mock.patch.object(reconcile, "REPO_SLUG", slug))
        for m in _main_mocks():
            # The eight stay LIVE: they are the phases under test.
            if m.attribute not in EIGHT:
                enter(m)
        for name in _ALSO_STOOD_DOWN:
            enter(mock.patch.object(reconcile, name))
        enter(mock.patch.object(reconcile, "report_epic_growth", return_value=[]))
        enter(_retiring(FAKE_RETIRING))
        enter(mock.patch.object(reconcile, "active_cards", side_effect=_board(rows)))
        # GitHub: every helper answered, so nothing leaves the process.
        enter(mock.patch.object(reconcile, "gh", side_effect=_gh))
        for name in ("gh_read", "gh_dispatch", "gh_actions_read"):
            enter(mock.patch.object(reconcile, name, return_value=None))
        # Linear's reads.
        enter(mock.patch.object(lops, "gql_paged", side_effect=urgent.gql_paged))
        enter(mock.patch.object(lops, "gql", return_value={}))
        enter(mock.patch.object(lops, "comment_records", return_value=[]))
        enter(mock.patch.object(lops, "comment_bodies", return_value=[]))
        enter(mock.patch.object(lops, "count_comments", return_value=0))
        enter(mock.patch.object(lops, "get_issue",
                                return_value={"state": {"name": "Planning"}}))
        enter(mock.patch.object(lops, "create_card"))
        # Every phase entered "spends" a request, so a phase that ran prints
        # its `sweep-spend:` line and the absence of one means something.
        enter(mock.patch.object(lops, "requests_made",
                                side_effect=itertools.count().__next__))
        # Linear's writes.
        for name in ("cmd_advance", "cmd_state", "cmd_comment", "add_label",
                     "remove_label", "set_title", "find_open_prefix"):
            enter(mock.patch.object(lops, name, writes[name]))
        enter(mock.patch.object(reconcile.planner_queue, "post_released",
                                writes["post_released"]))
        enter(mock.patch.object(reconcile.plan_run, "fire", writes["fire"]))
        enter(mock.patch.object(reconcile.epic_todo_gate, "post_refusal",
                                writes["post_refusal"]))
        yield writes, result


def _sweep(cards, slug, capsys):
    """Run one full pass; return `(writes, stdout lines, red)`."""
    capsys.readouterr()
    with _world(cards, slug) as (writes, result):
        try:
            reconcile.main()
        except SystemExit:
            result.red = True
    return writes, capsys.readouterr().out.splitlines(), result.red


def _off_rail_lines(lines):
    return [line for line in lines if line.startswith("off-rail:")]


def _called(writes):
    return {name: m.call_args_list for name, m in writes.items() if m.called}


def _targets(mock_):
    """The card identifiers a write mock was called on, wherever the
    identifier sits in the call (first argument, or second after `ops`)."""
    return {
        arg for call in mock_.call_args_list for arg in call.args[:2]
        if isinstance(arg, str) and arg.startswith("DRE-")
    }


def _full_board():
    return [build() for cards, _seam, _ident in TRIGGERS.values() for build in cards]


# --------------------------------------------------------------------------
# 1: the predicate, the notice, the mapping
# --------------------------------------------------------------------------
def test_off_rail_is_true_for_the_sandbox(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", SANDBOX)
    assert reconcile.off_rail() is True


def test_off_rail_is_false_for_every_slug_in_the_real_repo_map(monkeypatch):
    """Iterates the FILE, never a restated list: a slug onboarded tomorrow is
    on the rail tomorrow without anyone editing this test."""
    slugs = json.loads((ROOT / "config" / "repo-map.json").read_text())
    assert slugs, "the routing map is empty — this test would prove nothing"
    for slug in slugs:
        monkeypatch.setattr(reconcile, "REPO_SLUG", slug)
        assert reconcile.off_rail() is False, slug


def test_off_rail_reads_the_bundled_snapshot_and_never_the_network(monkeypatch):
    """A sweep must not spend a request to decide whether it may spend
    requests: the rail is `validate_card.VALID_SLUGS`, never `live_rail_slugs`."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", SANDBOX)
    with mock.patch.object(reconcile, "live_rail_slugs") as live, \
            mock.patch.object(reconcile, "gh") as gh:
        reconcile.off_rail()
    live.assert_not_called()
    gh.assert_not_called()


def test_the_prefix_is_off_rail():
    assert reconcile.OFF_RAIL_PREFIX == "off-rail"


def test_the_notice_is_one_line_naming_the_slug_the_phase_and_the_write(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", SANDBOX)
    line = reconcile.off_rail_notice("flag_stranded", "escalated a stalled card")
    assert "\n" not in line
    assert line.startswith(f"{reconcile.OFF_RAIL_PREFIX}: ")
    assert SANDBOX in line
    assert "not on the routing rail" in line
    assert "flag_stranded" in line
    assert "escalated a stalled card" in line


def test_the_mapping_is_exactly_the_eight():
    """A ninth fleet-wide writer joins deliberately, by name — here and in
    the mapping together."""
    assert tuple(reconcile.OFF_RAIL_SKIPPED) == EIGHT
    for name, would in reconcile.OFF_RAIL_SKIPPED.items():
        assert isinstance(would, str) and would.strip(), name


def _main_source() -> tuple[str, ast.Module]:
    source = (ROOT / "scripts" / "reconcile.py").read_text()
    return source, ast.parse(source)


def test_every_name_is_a_function_in_reconcile_and_a_phase_of_main():
    """Read with `ast`: each name is a top-level function of reconcile.py and
    is reached from `main()` either as a member of the backstop tuple or as a
    `_phase("<name>")` literal — so a renamed, removed or relocated phase
    fails here, by name."""
    source, tree = _main_source()
    defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    backstops: set[str] = set()
    phases: set[str] = set()
    for node in ast.walk(main):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and node.target.id == "backstop" and isinstance(node.iter, ast.Tuple)):
            backstops |= {e.id for e in node.iter.elts if isinstance(e, ast.Name)}
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_phase" and node.args
                and isinstance(node.args[0], ast.Constant)):
            phases.add(node.args[0].value)
    assert backstops, "main() has no backstop tuple — the read is broken"
    for name in reconcile.OFF_RAIL_SKIPPED:
        assert name in defined, f"{name} is not a function in scripts/reconcile.py"
        assert name in backstops or name in phases, (
            f"{name} is neither in main()'s backstop tuple nor a _phase literal"
        )


def test_main_keeps_the_text_other_suites_read():
    """Several suites read `main()` as text; the fence is a guard beside the
    tuple, never a second tuple."""
    import inspect

    source = inspect.getsource(reconcile.main)
    for text in ("for backstop in (", "drain_retiring_lanes,", "repo_epics(mine)"):
        assert text in source, text


# --------------------------------------------------------------------------
# 2: THE CRITERION — a full pass off the rail writes nothing
# --------------------------------------------------------------------------
def test_a_full_sandbox_pass_skips_all_eight_and_writes_nothing(capsys):
    writes, lines, red = _sweep(_full_board(), SANDBOX, capsys)

    off = _off_rail_lines(lines)
    assert len(off) == 8, off
    for name in EIGHT:
        assert sum(name in line for line in off) == 1, (name, off)
    assert _called(writes) == {}, _called(writes)
    assert reconcile._write_failures == []
    assert reconcile._read_failures == []
    assert not red, "a skipped phase is a normal pass — never red"
    spend = [line for line in lines if line.startswith("sweep-spend:")]
    for name in EIGHT:
        assert not any(line.startswith(f"sweep-spend: {name} ") for line in spend), (
            name, spend)


def test_the_same_board_on_the_rail_enters_every_phase(capsys):
    """The control for the spend assertion above: on the rail each of the
    eight is entered, so each prints its `sweep-spend:` line, and no
    `off-rail:` line appears."""
    _writes, lines, _red = _sweep(_full_board(), ON_RAIL, capsys)
    assert _off_rail_lines(lines) == []
    for name in EIGHT:
        assert any(line.startswith(f"sweep-spend: {name} ") for line in lines), name


# --------------------------------------------------------------------------
# 3: each fixture is one the fence actually stops
# --------------------------------------------------------------------------
@pytest.mark.parametrize("phase", EIGHT)
def test_each_trigger_writes_on_the_rail_and_nothing_off_it(phase, capsys):
    builders, seam, ident = TRIGGERS[phase]

    writes, lines, _red = _sweep([b() for b in builders], ON_RAIL, capsys)
    assert _off_rail_lines(lines) == []
    assert ident in _targets(writes[seam]), (
        f"on the rail, {phase} must make its write ({seam} on {ident}) — or "
        f"this fixture proves nothing about the fence; writes: {_called(writes)}"
    )

    writes, lines, red = _sweep([b() for b in builders], SANDBOX, capsys)
    off = _off_rail_lines(lines)
    assert [line for line in off if phase in line] and sum(
        phase in line for line in off) == 1, off
    assert _called(writes) == {}, _called(writes)
    assert reconcile._write_failures == []
    assert reconcile._read_failures == []
    assert not red


# --------------------------------------------------------------------------
# 4: the document the change keeps true
# --------------------------------------------------------------------------
def test_the_harness_readme_says_the_promise_is_held_by_the_fence():
    text = (ROOT / "scripts" / "harness" / "README.md").read_text()
    section = text.split("## The Linear side", 1)[1].split("\n## ", 1)[0]
    assert "off-rail:" in section, (
        "The Linear side must say the zero-writes promise is held by the "
        "off-rail fence"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
