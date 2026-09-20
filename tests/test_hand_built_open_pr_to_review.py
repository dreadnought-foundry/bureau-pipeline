"""RED-first tests: a hand-built card with an open PR reaches In Review (DRE-4356).

THE BUG (live, 2026-09-20): the CEO opened the console and saw four cards that
were visibly being worked — DRE-4348, DRE-4349, DRE-4350 and DRE-4352, each with
an open pull request sitting at CI or the merge gate — all reading "in Intake".
His words: *"They shouldn't be in Intake because they're being actively worked
on."* They were moved by hand, because nothing in the pipeline would ever have
moved them.

WHY NOTHING REACHED THEM. A fleet-built card is put in the review lane by the
run that opens its pull request; a hand-built card has no run. The sweep's nudge
loop carries "open PR → In Review" and is label-blind — but it only walks the
lanes it sweeps, and hand-built work usually waits in Intake, which it never
nudges, and in Todo, which has no open-PR branch at all. The merge event DOES
reach these cards (`linear-sync` reads the card id out of the branch name and
writes Done), which is why they go Intake → Done with nothing in between.

FIX UNDER TEST — `reconcile.move_hand_built_to_review()`. It starts from the
sweep's OWN open-pull-request listing (never by widening `SWEPT_LANES`, which
would put every Intake card through every nudge), looks the card up in the
board snapshot the sweep already read, and moves it once:

  * the head ref is `agent/DRE-<n>-…` or `repair/DRE-<n>-…`, and the pull
    request is OPEN and not a draft;
  * the card carries `hand-built`, is not an epic, and sits in a flow lane
    strictly upstream of the review lane;
  * the move is guarded (`cmd_advance`) and the receipt follows the REAL
    outcome (DRE-1254) — one move, one comment, per card, for ever;
  * every read that fails is reported as UNKNOWN and moves nothing (DRE-2034):
    an unreadable answer is never "no pull request".

Run: cd bureau-pipeline && python3 -m pytest tests/test_hand_built_open_pr_to_review.py -v
"""
from __future__ import annotations

import ast
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
# The same repo every other reconcile suite pins (test_hand_built_not_stranded.py):
# these are `setdefault`, so two suites naming two repos would make the first
# one imported decide REPO for the other — and a REPO that exists is a REPO a
# locally-authed `gh` will really answer for.
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import reconcile  # noqa: E402

_SOURCE = (ROOT / "scripts" / "reconcile.py").read_text()

# Spelled literally, exactly as test_hand_built_not_stranded.py spells it: the
# label as Linear has it, so a rename to something the board does not carry
# fails here rather than in a live sweep.
HAND_BUILT = "hand-built"
CARD = "DRE-4348"


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _card(
    identifier=CARD,
    state="Intake",
    labels=("repo:portico", HAND_BUILT, "agent:engineer"),
    title="the agent stubs grant id-token: write",
    children=(),
):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title,
        "description": "work",
        "updatedAt": _iso(5),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "children": {"nodes": [{"id": c} for c in children]},
    }


def _pr(number=270, branch=f"agent/{CARD}-grant-id-token-write", draft=False):
    """One row of `_open_pr_listing()` — the same seven fields it selects."""
    return {
        "number": number,
        "headRefName": branch,
        "headRefOid": "a" * 40,
        "baseRefName": "main",
        "mergeStateStatus": "BLOCKED",
        "isDraft": draft,
        "comments": [],
    }


def _board(cards):
    """A stand-in for `active_cards` that filters by lane exactly as the real
    one does — so a card in a lane this pass must not read is absent here for
    the same reason it is absent in production."""

    def active_cards(states=reconcile.SWEEP_STATES):
        return [c for c in cards if c["state"]["name"] in states]

    return active_cards


def _run(prs, cards, *, bodies=(), landed_in=None, **extra):
    """Run the pass over `prs` and `cards`; returns the mocks to assert on."""
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    mocks = {
        "_open_pr_listing": mock.MagicMock(return_value=list(prs)),
        "active_cards": mock.MagicMock(side_effect=_board(cards)),
        "card_state": mock.MagicMock(
            return_value=reconcile.REVIEW_LANE if landed_in is None else landed_in
        ),
    }
    mocks.update(extra)
    with mock.patch.multiple(reconcile, **mocks), mock.patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=list(bodies)
    ) as comment_bodies, mock.patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as cmd_advance, mock.patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as cmd_comment:
        reconcile.move_hand_built_to_review()
    failures = list(reconcile._write_failures) + list(reconcile._read_failures)
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    return mock.Mock(
        cmd_advance=cmd_advance,
        cmd_comment=cmd_comment,
        comment_bodies=comment_bodies,
        failures=failures,
    )


# --------------------------------------------------------------------------
# 1: the headline — the four cards the CEO saw
# --------------------------------------------------------------------------
def test_hand_built_intake_card_with_an_open_pr_is_moved_to_the_review_lane():
    """The DRE-4348 shape: hand-built, in Intake, with an open pull request on
    its own branch. The board must say what is actually happening."""
    s = _run([_pr()], [_card()])
    s.cmd_advance.assert_called_once()
    ident, to_lane, from_lanes = s.cmd_advance.call_args.args
    assert ident == CARD
    assert to_lane == reconcile.REVIEW_LANE
    assert "Intake" in [lane.strip() for lane in from_lanes.split(",")]


def test_exactly_one_comment_is_posted_and_it_names_the_pull_request():
    """One plain-English line, and the evidence in it: which pull request moved
    the card. A receipt that does not name it cannot be checked by a reader."""
    s = _run([_pr()], [_card()])
    s.cmd_comment.assert_called_once()
    body = s.cmd_comment.call_args.args[1]
    assert "#270" in body, "the receipt must name the pull request that moved it"
    assert f"agent/{CARD}-grant-id-token-write" in body
    assert reconcile.REVIEW_LANE in body
    assert HAND_BUILT in body


def test_a_repair_branch_moves_its_card_too():
    """`repair/DRE-<n>-…` carries the same own-branch provenance `agent/` does
    (DRE-3533) — the repair agent filed the card and is working it."""
    s = _run([_pr(branch=f"repair/{CARD}-a1b2c3d4e5f6")], [_card()])
    s.cmd_advance.assert_called_once()


# --------------------------------------------------------------------------
# 2: idempotent — one move, one comment, per card
# --------------------------------------------------------------------------
def test_a_second_pass_over_the_same_state_moves_nothing_and_posts_nothing():
    """The sweep runs every ~15 minutes. A receipt already on the card is the
    whole of the memory — keyed on the CARD, so a lane guard that returns the
    card to Intake cannot make this loop."""
    first = _run([_pr()], [_card()])
    said = first.cmd_comment.call_args.args[1]
    again = _run([_pr()], [_card()], bodies=[said])
    again.cmd_advance.assert_not_called()
    again.cmd_comment.assert_not_called()


def test_the_card_having_moved_is_itself_enough():
    """The ordinary second pass: the card is now in the review lane, so the
    board read this pass makes does not carry it at all."""
    s = _run([_pr()], [_card(state=reconcile.REVIEW_LANE)])
    s.cmd_advance.assert_not_called()
    s.cmd_comment.assert_not_called()


# --------------------------------------------------------------------------
# 3: the lanes and labels this pass must not touch
# --------------------------------------------------------------------------
@pytest.mark.parametrize("lane", ["In Review", "Done", "Canceled", "Duplicate"])
def test_a_card_at_or_past_the_review_lane_is_untouched(lane):
    s = _run([_pr()], [_card(state=lane)])
    s.cmd_advance.assert_not_called()
    s.cmd_comment.assert_not_called()


def test_a_card_without_the_hand_built_label_is_not_moved():
    """The fleet's own runs own that move — a dispatched card is put in the
    review lane by the run that opened its pull request."""
    s = _run([_pr()], [_card(labels=("repo:portico", "agent:engineer"))])
    s.cmd_advance.assert_not_called()
    s.cmd_comment.assert_not_called()


@pytest.mark.parametrize("near", ["handbuilt", "hand-built-later", "repo:hand-built"])
def test_a_similar_label_does_not_count_as_hand_built(near):
    s = _run([_pr()], [_card(labels=("repo:portico", near))])
    s.cmd_advance.assert_not_called()


def test_a_closed_or_merged_pull_request_moves_nothing():
    """`_open_pr_listing()` asks GitHub for `--state open`, so a closed or
    merged pull request is simply not in the listing this pass reads."""
    s = _run([], [_card()])
    s.cmd_advance.assert_not_called()
    s.cmd_comment.assert_not_called()


def test_the_listing_this_pass_reads_is_open_pull_requests_only():
    """Guard the sentence above: the one listing the sweep keeps is `--state
    open`, so "closed or merged moves nothing" is true by construction."""
    seen = []

    def gh(*args):
        seen.append(args)
        return "[]"

    reconcile.reset_sweep_cards()
    with mock.patch.object(reconcile, "gh", side_effect=gh):
        reconcile._open_pr_listing()
    reconcile.reset_sweep_cards()
    assert seen and seen[0][:2] == ("pr", "list")
    assert "--state" in seen[0] and "open" in seen[0]


# --------------------------------------------------------------------------
# 4: drafts, and marking one ready
# --------------------------------------------------------------------------
def test_a_draft_pull_request_does_not_move_the_card():
    """A draft is not "being checked" — no critic, no merge gate. In Review's
    evidence is not there yet."""
    s = _run([_pr(draft=True)], [_card()])
    s.cmd_advance.assert_not_called()
    s.cmd_comment.assert_not_called()


def test_marking_the_pull_request_ready_moves_the_card():
    """`ready_for_review` flips the same field the guard above reads, so the
    next pass moves the card with nothing else changed."""
    s = _run([_pr(draft=False)], [_card()])
    s.cmd_advance.assert_called_once()


# --------------------------------------------------------------------------
# 5: an epic is never moved, whatever its branch is named
# --------------------------------------------------------------------------
def test_an_epic_with_children_is_never_moved():
    s = _run([_pr()], [_card(children=("child-1",))])
    s.cmd_advance.assert_not_called()
    s.cmd_comment.assert_not_called()


def test_an_epic_by_title_is_never_moved():
    s = _run([_pr()], [_card(title="[EPIC] agent working logs")])
    s.cmd_advance.assert_not_called()


def test_a_planner_owned_card_is_never_moved():
    """`agent:planner` is the label the relay requires before it dispatches the
    planner, and the card names it as an epic tell. Refusing it fails CLOSED —
    the card stays exactly where a person put it."""
    s = _run([_pr()], [_card(labels=("repo:portico", HAND_BUILT, "agent:planner"))])
    s.cmd_advance.assert_not_called()
    s.cmd_comment.assert_not_called()


# --------------------------------------------------------------------------
# 6: a branch that names no card
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "branch",
    ["dependabot/pip/urllib3-2.5.0", "bot/standards-sync", "repair/a1b2c3d4e5f6",
     "ops/rotate-the-key", "main"],
)
def test_a_branch_that_is_not_a_cards_own_branch_moves_nothing(branch):
    s = _run([_pr(branch=branch)], [_card()])
    s.cmd_advance.assert_not_called()
    s.cmd_comment.assert_not_called()


# --------------------------------------------------------------------------
# 7: an unreadable answer is UNKNOWN, never "no pull request" (DRE-2034)
# --------------------------------------------------------------------------
def test_an_unreadable_pull_request_listing_moves_nothing_and_says_unknown(capsys):
    s = _run(
        [], [_card()], _open_pr_listing=mock.MagicMock(return_value=None)
    )
    s.cmd_advance.assert_not_called()
    s.cmd_comment.assert_not_called()
    out = capsys.readouterr()
    assert "unknown" in (out.out + out.err).lower()


def test_an_unreadable_board_moves_nothing_and_says_unknown(capsys):
    s = _run(
        [_pr()],
        [_card()],
        active_cards=mock.MagicMock(side_effect=RuntimeError("Linear 503")),
    )
    s.cmd_advance.assert_not_called()
    s.cmd_comment.assert_not_called()
    out = capsys.readouterr()
    assert "unknown" in (out.out + out.err).lower()
    assert s.failures, "an unreadable board must make the sweep run red"


def test_an_unreadable_comment_thread_moves_nothing_and_says_unknown(capsys):
    """The idempotency read. Unreadable is not "nothing said yet" — acting on
    it would post the receipt a second time."""
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    with mock.patch.multiple(
        reconcile,
        _open_pr_listing=mock.MagicMock(return_value=[_pr()]),
        active_cards=mock.MagicMock(side_effect=_board([_card()])),
        card_state=mock.MagicMock(return_value=reconcile.REVIEW_LANE),
    ), mock.patch.object(
        reconcile.linear_ops, "comment_bodies", side_effect=RuntimeError("Linear 503")
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as cmd_advance, mock.patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as cmd_comment:
        reconcile.move_hand_built_to_review()
    failures = list(reconcile._write_failures) + list(reconcile._read_failures)
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    cmd_advance.assert_not_called()
    cmd_comment.assert_not_called()
    out = capsys.readouterr()
    assert "unknown" in (out.out + out.err).lower()
    assert failures, "an unreadable thread must make the sweep run red"


def test_a_move_that_did_not_land_posts_no_receipt(capsys):
    """The receipt follows the REAL outcome (DRE-1254). `cmd_advance` is a
    guarded write — it declines a card that left the lane between the board
    read and the write — so a comment claiming the move must not be posted
    when the card is not in the review lane afterwards."""
    s = _run([_pr()], [_card()], landed_in="Intake")
    s.cmd_advance.assert_called_once()
    s.cmd_comment.assert_not_called()


# --------------------------------------------------------------------------
# 8: nothing that has no pull request is read, moved or commented on
# --------------------------------------------------------------------------
def test_an_intake_card_with_no_pull_request_is_never_even_read():
    """The pass starts from the pull-request listing, never from the lane. An
    Intake card with no pull request must not be looked at, moved, or spoken
    to — widening `SWEPT_LANES` instead would put every Intake card through
    every nudge in the sweep."""
    other = _card(identifier="DRE-4999")
    s = _run([_pr()], [_card(), other])
    assert s.cmd_advance.call_count == 1
    assert s.cmd_comment.call_count == 1
    assert s.cmd_advance.call_args.args[0] == CARD
    for call in s.comment_bodies.call_args_list:
        assert call.args[0] != "DRE-4999", (
            "a card with no pull request must not have its thread read"
        )


# --------------------------------------------------------------------------
# 9: the lane names come from the contract, never from a literal
# --------------------------------------------------------------------------
def test_the_lanes_are_derived_from_the_lane_contract():
    flow = lane_contract.flow_lanes()
    upstream = flow[: flow.index(lane_contract.lane("In Review")["name"])]
    assert reconcile.HAND_BUILT_REVIEW_LANES == tuple(
        name for name in upstream if name in reconcile.SWEPT_LANES
    )
    assert "Intake" in reconcile.HAND_BUILT_REVIEW_LANES
    assert reconcile.REVIEW_LANE not in reconcile.HAND_BUILT_REVIEW_LANES
    assert "Done" not in reconcile.HAND_BUILT_REVIEW_LANES


def _function_source(name: str) -> str:
    tree = ast.parse(_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(_SOURCE, node) or ""
    raise AssertionError(f"reconcile.py has no function {name!r}")


def test_no_lane_name_is_written_as_a_literal_in_the_new_path():
    """A lane literal is a dead string the day the board renames the lane —
    `lane_contract.lane()` raises at import instead."""
    for name in ("move_hand_built_to_review", "_advance_to_review"):
        source = _function_source(name)
        for lane in lane_contract.lane_names():
            assert f'"{lane}"' not in source and f"'{lane}'" not in source, (
                f"{name} writes the lane {lane!r} as a literal"
            )


# --------------------------------------------------------------------------
# 10: the sweep actually runs it
# --------------------------------------------------------------------------
def _call_owners(name: str) -> set[str]:
    """The functions that call `name` anywhere in reconcile.py."""
    tree = ast.parse(_SOURCE)
    funcs = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    def owner_of(lineno: int) -> str:
        best = None
        for f in funcs:
            if f.lineno <= lineno <= (f.end_lineno or f.lineno):
                if best is None or f.lineno > best.lineno:
                    best = f
        return best.name if best else "<module>"

    return {
        owner_of(n.lineno)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == name
    }


def test_the_full_sweep_is_the_only_caller():
    assert _call_owners("move_hand_built_to_review") == {"main"}


def _sweep_mocks(cards, prs, extra=None):
    """main() with every unrelated backstop stubbed, so the sweep under test is
    this pass and the nudge loop. Mirrors test_hand_built_not_stranded.py."""
    m = {
        "unstick_conflicts": mock.MagicMock(),
        "retrigger_dead_heads": mock.MagicMock(),
        "flag_no_checks_prs": mock.MagicMock(),
        "flag_unowned_prs": mock.MagicMock(),
        "fix_approved_but_red": mock.MagicMock(),
        "retry_dead_fix_runs": mock.MagicMock(),
        "restart_answered_blockers": mock.MagicMock(),
        "review_dependabot_prs": mock.MagicMock(),
        "card_dependabot_prs": mock.MagicMock(),
        "recover_crashed_reviews": mock.MagicMock(),
        "check_dependabot_capacity": mock.MagicMock(),
        "close_finished_epics": mock.MagicMock(),
        "promote_ready": mock.MagicMock(return_value=0),
        "flag_stranded": mock.MagicMock(return_value=set()),
        "active_cards": mock.MagicMock(side_effect=_board(cards)),
        "_open_pr_listing": mock.MagicMock(return_value=list(prs)),
        "card_state": mock.MagicMock(return_value=reconcile.REVIEW_LANE),
        "pr_for": mock.MagicMock(return_value=None),
        "agent_run_alive": mock.MagicMock(return_value=False),
        "redispatch": mock.MagicMock(return_value=True),
    }
    if extra:
        m.update(extra)
    return m


def _run_sweep(cards, prs, extra=None, **kwargs):
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    with mock.patch.multiple(
        reconcile, **_sweep_mocks(cards, prs, extra)
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_state"
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as cmd_comment, mock.patch.object(
        reconcile.linear_ops, "add_label"
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as cmd_advance, mock.patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=[]
    ), mock.patch.object(
        reconcile.linear_ops, "count_comments", return_value=0
    ):
        try:
            reconcile.main(**kwargs)
        except SystemExit:  # a red sweep still ran every phase
            pass
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    return mock.Mock(cmd_advance=cmd_advance, cmd_comment=cmd_comment)


def test_a_full_sweep_moves_the_card():
    """End to end through main(): the pass is wired into the full sweep."""
    s = _run_sweep([_card()], [_pr()])
    assert any(
        call.args[:2] == (CARD, reconcile.REVIEW_LANE)
        for call in s.cmd_advance.call_args_list
    ), "the full sweep must move the hand-built card into the review lane"


def test_the_promotion_only_pass_does_not_move_it():
    """`--promote-only` is the merge path's dependency gate and nothing else —
    it runs on every merge in the fleet and must not grow a board-wide pass."""
    s = _run_sweep([_card()], [_pr()], promote_only=True)
    s.cmd_advance.assert_not_called()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
