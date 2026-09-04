"""A merge on a branch named for an EPIC must NOT move the epic to Done.

THE BUG (DRE-3119, found 2026-09-04 landing agent-bureau PR #2273): the branch
was `agent/DRE-3060-one-river-phase-chain` — named for the EPIC the work served,
not for a card. `linear-sync.yml` reads the card id straight out of an
`agent/DRE-<n>-…` head ref and runs `card-done` on it, and
`auto_done_skip_reason` refused exactly two classes: a `no-code` card and a
`DEMO:`-titled card. An epic is neither, so the merge would have moved DRE-3060
— in Planning, 21 children, a planner run in flight — to Done. The PR was
rebuilt on a card branch by hand; nothing in the pipeline would have stopped it.

The head-ref anchor that closed the DRE-99 incident ("part of DRE-99" in a PR
title auto-Doneing the epic) assumes a branch is named for a CARD. A branch
named for an epic passes that anchor and lands on the one card class whose Done
is a fleet event: an epic's Done releases nothing, but it ENDS THE PLAN, and
`epic_autoclose` and `prove_phase` read it.

FIX UNDER TEST — a third arm on the same pure predicate,
`linear_ops.auto_done_skip_reason()`, given the one fact it was missing (does
the card have children); `agent:planner` is the second leg, because a card the
planner owns is never dispatched to a build agent at all (`plan_run.py` routes
on that label), so a merge on its branch is always a branch named for the wrong
card. `cmd_card_done` passes the facts through from the `get_issue` query it
already makes, which must therefore SELECT the children.

Reconcile's merged-PR backstop — the other caller of the predicate — never
reaches an epic: `main()` drops `repo_epics(mine)` from the nudge loop before
the merged branch runs (`reconcile.card_is_epic`). Pinned below so the reason
this file does not touch that path stays true.

Run: cd bureau-pipeline && python3 -m pytest tests/test_linear_ops.py -v
"""
from __future__ import annotations

import inspect
import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import reconcile  # noqa: E402


# --------------------------------------------------------------------------
# the pure predicate: an epic is the third class a merge may not close
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("title", "labels", "has_children", "why"),
    [
        # The DRE-3060 shape itself: an epic with children, planner-owned.
        ("[EPIC] One River — the home page rebuilt at /dashboard-next",
         ["repo:agent-bureau", "agent:planner"], True, "the DRE-3060 shape"),
        # Either leg alone is enough — the card names both.
        ("One River — the phase chain", ["repo:agent-bureau"], True,
         "children alone make it an epic (validate_card.infer_agent_label)"),
        ("One River — the phase chain", ["repo:agent-bureau", "agent:planner"],
         False, "agent:planner alone — a card the planner owns is not built"),
        ("One River — the phase chain", ["Agent:Planner"], False,
         "label match is case-insensitive, like the no-code arm"),
        # …and the title stamp, which is where an epic with no children yet
        # (a plan still being cut) is readable at all.
        ("[EPIC] One River", [], False, "[EPIC] in the title"),
        ("[epic] one river", [], False, "[EPIC] is case-insensitive"),
    ],
)
def test_an_epic_branch_is_refused(title, labels, has_children, why):
    assert linear_ops.auto_done_skip_reason(
        title, labels, has_children
    ) is not None, why


@pytest.mark.parametrize(
    ("title", "labels", "has_children", "why"),
    [
        # The whole pipeline rides this: an ordinary card on an agent/DRE-<n>-
        # branch still goes Done, exactly as before.
        ("Add folder ACL enforcement", ["repo:portico", "agent:engineer"], False,
         "ordinary code card"),
        ("One River: the design contract reaches main",
         ["repo:agent-bureau", "agent:engineer"], False,
         "the DRE-3106 shape — the card the PR was rebuilt on"),
        ("Rename the epic runner", ["agent:engineer"], False,
         "MENTIONING an epic is not being one"),
        ("Fix the planner's label read", ["agent:engineer", "agent:frontend"],
         False, "a build role is not agent:planner"),
        ("Fix codegen", ["no-codegen"], False,
         "the existing arms still answer for their own shapes"),
    ],
)
def test_an_ordinary_card_still_closes(title, labels, has_children, why):
    assert linear_ops.auto_done_skip_reason(title, labels, has_children) is None, why


def test_has_children_defaults_to_false():
    """The two existing callers pass two arguments and must keep working —
    `reconcile`'s merged backstop is one of them (and never sees an epic)."""
    assert linear_ops.auto_done_skip_reason("Add folder ACL enforcement", []) is None


def test_the_refusal_says_epic_and_names_the_card_branch_convention():
    """The reason is read by a human on the epic's own thread, so it says in
    plain words what happened and what to do instead — the same voice as the
    `no-code` and `DEMO:` reasons."""
    reason = linear_ops.auto_done_skip_reason("[EPIC] One River", [], True)
    assert "epic" in reason.lower()
    assert "branch" in reason.lower()
    # …and the convention it should have been named for, spelled out.
    assert "agent/DRE-" in reason


# --------------------------------------------------------------------------
# path (a): linear-sync's `card-done` subcommand
# --------------------------------------------------------------------------
def _issue(title, labels, children=0, identifier="DRE-3060"):
    """A `get_issue` answer, with the children the real query now selects."""
    return {
        "id": "card-uuid",
        "identifier": identifier,
        "title": title,
        "team": {"id": "team-1"},
        "state": {"name": "Planning", "type": "unstarted"},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "children": {"nodes": [{"id": f"kid-{i}"} for i in range(children)]},
    }


def _run_card_done(issue):
    """cmd_card_done against a faked card; returns (stdout, cmd_state mock,
    cmd_comment mock)."""
    buf = io.StringIO()
    with patch.object(
        linear_ops, "get_issue", return_value=issue
    ), patch.object(linear_ops, "cmd_state") as state, patch.object(
        linear_ops, "cmd_comment"
    ) as comment:
        with redirect_stdout(buf):
            linear_ops.cmd_card_done(
                issue["identifier"],
                "https://github.com/dreadnought-foundry/agent-bureau/pull/2273",
            )
    return buf.getvalue(), state, comment


def test_epic_card_is_not_transitioned():
    """The DRE-3060 shape end-to-end: PR #2273 merges on `agent/DRE-3060-…`,
    the epic's state is untouched and the merge is commented on it.

    MUTATION CHECK: delete the epic arm of auto_done_skip_reason (or stop
    passing the children through) and `state` records ('DRE-3060', 'Done') —
    red here.
    """
    out, state, comment = _run_card_done(
        _issue("[EPIC] One River — the home page rebuilt",
               ["repo:agent-bureau", "agent:planner"], children=21)
    )
    state.assert_not_called()
    body = comment.call_args.args[1]
    assert "/pull/2273" in body
    assert linear_ops.MERGED_NOT_CLOSED_MARKER in body
    assert "NOT auto-closed" in body
    # The one instruction that must NOT appear on an epic: a running plan is
    # closed by its children finishing, never by a person tidying up after a
    # merge (that is the DRE-3060 outcome wearing a different hat).
    assert "close it by hand" not in body.lower()
    # …and the job log says so loudly, like the other two classes.
    assert "AUTO-DONE SKIPPED" in out


def test_epic_without_children_yet_is_not_transitioned():
    """An epic whose plan is still being cut has no children to read — the
    `agent:planner` leg is what refuses it."""
    out, state, _ = _run_card_done(
        _issue("One River — the phase chain", ["agent:planner"], children=0)
    )
    state.assert_not_called()
    assert "AUTO-DONE SKIPPED" in out


def test_ordinary_card_still_goes_done():
    """The everyday path, through the widened query: → Done, ✅ comment."""
    _, state, comment = _run_card_done(
        _issue("One River: the design contract reaches main",
               ["repo:agent-bureau", "agent:engineer"], children=0,
               identifier="DRE-3106")
    )
    state.assert_called_once_with("DRE-3106", "Done")
    comment.assert_called_once_with(
        "DRE-3106",
        "✅ Merged: https://github.com/dreadnought-foundry/agent-bureau/pull/2273",
    )


def test_get_issue_selects_the_children_card_done_reads():
    """The wiring, not the logic: the guard is only live if the query that
    feeds it fetches the fact. A card read by a query that never selected
    `children` reads as having none — which is exactly how an epic gets past
    a predicate that is otherwise correct (reconcile.card_is_epic carries the
    same warning, DRE-3044)."""
    assert "children" in inspect.getsource(linear_ops.get_issue), (
        "get_issue no longer selects children — cmd_card_done's epic guard is "
        "reading a field the query never fetched"
    )


# --------------------------------------------------------------------------
# path (b): reconcile's merged-PR backstop never reaches an epic
# --------------------------------------------------------------------------
def test_the_sweep_drops_epics_before_its_merged_backstop():
    """Why this fix lands in one caller and not two: the sweep excludes every
    epic from the loop that owns the merged-PR backstop, so the backstop's
    two-argument call cannot re-close one sweep later what card-done just
    refused. Delete that exclusion and this says so."""
    source = inspect.getsource(reconcile.main)
    assert "repo_epics(mine)" in source
    assert 'c["identifier"] not in epics' in source


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
