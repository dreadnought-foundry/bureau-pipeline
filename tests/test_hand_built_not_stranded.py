"""RED-first tests: a hand-built card is not stranded (DRE-2524).

THE BUG (live, 2026-08-17): five portico cards — DRE-2499, DRE-2500, DRE-2501,
DRE-2505 and DRE-2507 — each collected a 🚨 stranded-watchdog notice plus the
needs-human hold label, and ALL FIVE were false alarms. The work had been
hand-built before the card existed, and the run the watchdog reported as "never
started" opened a pull request forty minutes later.

Both of flag_stranded's strand classes are false alarms on hand-built work:

  * NO RUN  — no dispatched agent run is coming, by design.
  * NO ROUTE — routing is irrelevant when nothing is being routed. Worse, that
    notice's own remedy reads "so it must be hand-built (or the repo onboarded
    to the routing map first)" — on a card explicitly labelled hand-built the
    advice has already been taken, so firing tells us to do what we did.

FIX UNDER TEST — the `hand-built` label (it already exists in Linear)
suppresses, for that card:

  1. reconcile.flag_stranded(), BOTH strand classes — the alarm; and
  2. main()'s nudge loop on a card with NO PULL REQUEST — the sweep's own
     dispatch, which is the thing the alarm was reporting on. Silencing the
     alarm alone leaves the engine running: 15 minutes into hand-building, a
     Todo card is re-dispatched (a second, competing agent run), and a stale In
     Progress card is requeued to Todo — feeding that same dispatch — then
     parked to Backlog with the hold label once past REQUEUE_CAP, overriding
     the human's own state placement.

…and NOTHING else. Everything that keys on a PULL REQUEST stays label-blind:
the PR-level backstops in the same file (flag_no_checks_prs, flag_unowned_prs,
unstick_conflicts, retrigger_dead_heads, …) and main()'s own PR-carrying
branches (merged → Done, open PR → In QA). A hand-built card whose PR wedges
must still be caught, and once a human opens a PR the sweep shepherds it
exactly as before — so the label must not become a second, wider hold.

Also under test (Layer 0 of the reliability programme): the NO RUN notice used
to offer a three-way guess — "check the GitHub Actions budget, the LLM quota,
and the relay". A stall report names ONE cause or says it does not know. It now
states only what was observed.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hand_built_not_stranded.py -v
"""
from __future__ import annotations

import ast
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402
import validate_card  # noqa: E402

_SOURCE = (ROOT / "scripts" / "reconcile.py").read_text()

# The label as it exists in Linear. Spelled literally here on purpose: the
# tests must fail if the constant is renamed to something Linear does not have.
HAND_BUILT = "hand-built"


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so this sweep owns the
    portico cards regardless of collection order (test_human_hold.py's hazard)."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")


@pytest.fixture(autouse=True)
def _pin_valid_slugs(monkeypatch):
    """Pin the routing snapshot: portico is on the rail, ghost-product is not."""
    monkeypatch.setattr(
        validate_card, "VALID_SLUGS", {"portico", "atlas", "bureau-pipeline"}
    )


@pytest.fixture(autouse=True)
def _pin_live_snapshot(monkeypatch):
    """The canonical @main snapshot the NO-ROUTE adjudication re-checks
    (DRE-2260) — pinned to the same set, hermetically, so no test fetches."""
    monkeypatch.setattr(
        reconcile,
        "live_rail_slugs",
        lambda: frozenset({"portico", "atlas", "bureau-pipeline"}),
        raising=False,
    )


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _card(
    identifier="DRE-2499",
    state="Todo",
    labels=("repo:portico", HAND_BUILT),
    minutes_stale=999.0,
):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "work that was already done by hand",
        "description": "work",
        "updatedAt": _iso(minutes_stale),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


def _run_watchdog(cards, bodies=()):
    """Run flag_stranded over `cards`; returns (flagged, cmd_comment mock,
    add_label mock)."""
    with patch.object(
        reconcile, "active_cards", return_value=list(cards)
    ), patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=list(bodies)
    ), patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as comment, patch.object(
        reconcile.linear_ops, "add_label"
    ) as add_label:
        flagged = reconcile.flag_stranded()
    return flagged, comment, add_label


# --------------------------------------------------------------------------
# 1 + 2: neither strand class fires on hand-built work
# --------------------------------------------------------------------------
def test_hand_built_card_with_no_run_receipt_is_not_flagged():
    """The DRE-2499 shape: routable repo, no run receipt, long past the age
    threshold. No dispatched run is coming — that is the design, not a strand."""
    flagged, comment, add_label = _run_watchdog([_card()], bodies=[])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_hand_built_card_with_unroutable_slug_is_not_flagged():
    """NO ROUTE's own remedy is "it must be hand-built" — on a card labelled
    hand-built that advice has already been taken."""
    flagged, comment, add_label = _run_watchdog(
        [_card(labels=("repo:ghost-product", HAND_BUILT))], bodies=[]
    )
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_hand_built_card_with_no_repo_label_at_all_is_not_flagged():
    """A hand-built card need not carry a repo label — nothing is being routed."""
    flagged, comment, add_label = _run_watchdog([_card(labels=(HAND_BUILT,))], bodies=[])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


@pytest.mark.parametrize("spelling", ["hand-built", "Hand-Built", "HAND-BUILT"])
def test_the_label_match_is_case_insensitive(spelling):
    """Linear labels are typed by humans; `held()` already folds case and this
    guard must fold it the same way."""
    flagged, comment, _ = _run_watchdog([_card(labels=("repo:portico", spelling))])
    assert flagged == set()
    comment.assert_not_called()


# --------------------------------------------------------------------------
# 3: the guard must be NARROW — without the label nothing changes
# --------------------------------------------------------------------------
def test_without_the_label_a_receiptless_card_is_still_flagged():
    """The watchdog still exists. DRE-1978 sat in Planning for seven days with
    no run; suppressing that class wholesale would give the seven days back."""
    flagged, comment, add_label = _run_watchdog(
        [_card(labels=("repo:portico",))], bodies=[]
    )
    assert flagged == {"DRE-2499"}
    assert "no agent run" in comment.call_args.args[1]
    add_label.assert_called_once_with("DRE-2499", reconcile.HOLD_LABEL)


def test_without_the_label_an_unroutable_card_is_still_flagged():
    flagged, comment, _ = _run_watchdog(
        [_card(labels=("repo:ghost-product",))], bodies=[]
    )
    assert flagged == {"DRE-2499"}
    assert "hand-built" in comment.call_args.args[1]


def test_a_similar_label_does_not_suppress():
    """Substring near-misses must not disarm the watchdog by accident."""
    for near in ("handbuilt", "hand-built-later", "repo:hand-built"):
        flagged, _, _ = _run_watchdog([_card(labels=("repo:portico", near))], bodies=[])
        assert flagged == {"DRE-2499"}, f"{near!r} must not read as {HAND_BUILT!r}"


def test_hand_built_is_not_a_hold_label_alias():
    """Two different meanings: HOLD_LABEL is "a human owes this card an
    action"; hand-built is "no agent was ever coming". Conflating them would
    stand down unstick_conflicts on a live PR (the DRE-2180 five hours)."""
    assert reconcile.HAND_BUILT_LABEL == HAND_BUILT
    assert reconcile.HAND_BUILT_LABEL != reconcile.HOLD_LABEL
    assert not reconcile.held(_card())


# --------------------------------------------------------------------------
# 4: the PR-level backstops are label-blind and stay that way
# --------------------------------------------------------------------------
def _pr(number=270, branch="agent/DRE-2499-hand-built", mstate="DIRTY", sha="a" * 40):
    return {
        "number": number,
        "headRefName": branch,
        "headRefOid": sha,
        "mergeStateStatus": mstate,
        "isDraft": False,
    }


def _fake_gh(prs, statuses="[]", head_age_min=999.0):
    def gh(*args):
        if args[:2] == ("pr", "list"):
            return json.dumps(prs)
        if args[0] == "api" and "/check-runs" in args[1]:
            return statuses
        if args[0] == "api" and "/git/commits/" in args[1]:
            return json.dumps({"committer": {"date": _iso(head_age_min)}})
        return ""

    return gh


def test_hand_built_cards_wedged_pr_is_still_caught_by_the_pr_backstop():
    """A hand-built card whose PR goes silent must still raise a hand — and
    the backstop must reach that conclusion WITHOUT consulting the card's
    labels at all, which is what keeps the suppression from widening."""
    comments: list = []
    with mock.patch.object(
        reconcile, "gh", side_effect=_fake_gh([_pr()])
    ), mock.patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=[]
    ), mock.patch.object(
        reconcile.linear_ops,
        "cmd_comment",
        side_effect=lambda ident, body: comments.append((ident, body)),
    ), mock.patch.object(
        reconcile, "card_parked_for_human", return_value=False
    ), mock.patch.object(
        reconcile, "hand_built"
    ) as hand_built:
        reconcile.flag_no_checks_prs()
    assert len(comments) == 1, "the wedged PR must still be reported"
    assert comments[0][0] == "DRE-2499"
    assert "#270" in comments[0][1]
    assert not hand_built.called, (
        "flag_no_checks_prs must stay label-blind — it keys on the pull request"
    )


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


def test_only_the_watchdog_and_the_sweeps_own_dispatch_consult_the_label():
    """Structural guard, not a sentinel: if a later change wires hand-built
    into a PR-keyed repair path, this says so.

    Two readers, both in the "would the pipeline start or restart an agent on
    this card" family: flag_stranded (the alarm) and main (the no-PR dispatch
    the alarm was reporting on). Every PR-level backstop stays label-blind.
    """
    assert _call_owners("hand_built") == {"flag_stranded", "main"}


def test_the_owner_sweep_can_actually_see_a_call():
    """Guard the guard: a detector that matches nothing would pass forever."""
    assert _call_owners("held") == {"flag_stranded", "main"}


# --------------------------------------------------------------------------
# 5: the sweep's own dispatch is suppressed too — silencing the ALARM is not
#    silencing the ENGINE that raised it
# --------------------------------------------------------------------------
# main()'s nudge loop acts on exactly the conditions the watchdog was taught to
# read as "hand-built, leave alone": Todo with no PR past 15 minutes fires a
# real repository_dispatch, and In Progress with no PR past 3 hours requeues to
# Todo (which redispatches on the next sweep) and, past REQUEUE_CAP, parks the
# card to Backlog with the hold label. On hand-built work that is a second,
# competing agent run on a card the label says no run is coming for — and the
# park silently overrides the human's own state placement.
def _sweep_mocks(cards, extra=None):
    """main() with every unrelated backstop stubbed out, so these tests see
    only the nudge loop. Mirrors test_human_hold.py's _full_sweep_mocks."""
    m = {
        "unstick_conflicts": mock.MagicMock(),
        "retrigger_dead_heads": mock.MagicMock(),
        "flag_no_checks_prs": mock.MagicMock(),
        "flag_unowned_prs": mock.MagicMock(),
        "fix_approved_but_red": mock.MagicMock(),
        "retry_dead_fix_runs": mock.MagicMock(),
        "restart_answered_blockers": mock.MagicMock(),
        "review_dependabot_prs": mock.MagicMock(),
        "recover_crashed_reviews": mock.MagicMock(),
        "check_dependabot_capacity": mock.MagicMock(),
        "close_finished_epics": mock.MagicMock(),
        "promote_ready": mock.MagicMock(return_value=0),
        "flag_stranded": mock.MagicMock(return_value=set()),
        "active_cards": mock.MagicMock(return_value=list(cards)),
        "pr_for": mock.MagicMock(return_value=None),  # no PR yet
        # No dispatched run ever posted a receipt, because none was coming.
        "agent_run_alive": mock.MagicMock(return_value=False),
        "redispatch": mock.MagicMock(return_value=True),
    }
    if extra:
        m.update(extra)
    return m


def _run_sweep(cards, extra=None):
    """Run main() over `cards`; returns a namespace of the mocks a caller
    asserts on (`redispatch`, `cmd_state`, `cmd_comment`, `add_label`,
    `cmd_advance`)."""
    reconcile._write_failures.clear()
    mocks = _sweep_mocks(cards, extra)
    with mock.patch.multiple(reconcile, **mocks), mock.patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, mock.patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as cmd_comment, mock.patch.object(
        reconcile.linear_ops, "add_label"
    ) as add_label, mock.patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as cmd_advance, mock.patch.object(
        reconcile.linear_ops, "count_comments", return_value=0
    ):
        reconcile.main()
    reconcile._write_failures.clear()
    return SimpleNamespace(
        redispatch=mocks["redispatch"],
        cmd_state=cmd_state,
        cmd_comment=cmd_comment,
        add_label=add_label,
        cmd_advance=cmd_advance,
    )


def test_hand_built_card_stale_in_todo_is_not_redispatched():
    """The DRE-2524 shape, one layer down: 15 minutes into hand-building, the
    sweep fires `agent-execute` at the same card. No dispatch is coming BY
    DESIGN — so the sweep must not be the one to start one."""
    s = _run_sweep([_card(state="Todo")])
    s.redispatch.assert_not_called()
    s.cmd_state.assert_not_called()
    s.cmd_comment.assert_not_called()
    s.add_label.assert_not_called()


def test_without_the_label_a_stale_todo_card_is_still_redispatched():
    """Control: the sweep's whole reason for existing still works."""
    s = _run_sweep([_card(state="Todo", labels=("repo:portico",))])
    s.redispatch.assert_called_once()
    assert any(
        reconcile._TODO_REDISPATCH_NOTE in c.args[1]
        for c in s.cmd_comment.call_args_list
    ), "the unlabelled card must still get its re-dispatch receipt"


def test_hand_built_card_in_progress_with_no_pr_is_not_requeued():
    """agent_run_alive() is false on hand-built work by definition — nothing
    was dispatched to be alive. Requeueing to Todo feeds the redispatch above."""
    s = _run_sweep([_card(state="In Progress")])
    s.redispatch.assert_not_called()
    s.cmd_state.assert_not_called()
    s.cmd_comment.assert_not_called()
    s.add_label.assert_not_called()


def test_hand_built_card_in_progress_is_not_parked_after_the_requeue_cap():
    """Past the cap the same branch parks the card in Backlog with the hold
    label — the sweep overriding a human's own placement on their own work."""
    s = _run_sweep([_card(state="In Progress")], extra={"REQUEUE_CAP": 0})
    s.cmd_state.assert_not_called()
    s.add_label.assert_not_called()


def test_without_the_label_a_dead_in_progress_card_is_still_requeued():
    """Control: the dead-run requeue (DRE-1403/DRE-2032) is untouched."""
    s = _run_sweep([_card(state="In Progress", labels=("repo:portico",))])
    s.cmd_state.assert_called_once_with("DRE-2499", "Todo")


# The guard is scoped to the NO-PR branches on purpose: once a hand-built card
# HAS a pull request there is real work to shepherd, and the sweep shepherds it
# exactly as it does anyone else's — the same label-blindness the PR-level
# backstops above are held to.
def test_hand_built_card_with_an_open_pr_is_still_advanced_to_in_qa():
    open_pr = {"number": 270, "state": "OPEN", "headRefName": "hand/DRE-2499"}
    s = _run_sweep(
        [_card(state="In Progress")],
        extra={
            "pr_for": mock.MagicMock(return_value=open_pr),
            "_nudge": mock.MagicMock(return_value=True),
            "review_workflow": mock.MagicMock(return_value="qa-review.yml"),
        },
    )
    s.cmd_advance.assert_called_once_with("DRE-2499", "In QA", "In Progress")


def test_hand_built_card_with_a_merged_pr_is_still_moved_to_done():
    """The sweep still closes the loop on hand-built work that shipped."""
    merged = {"number": 270, "state": "MERGED", "headRefName": "hand/DRE-2499"}
    s = _run_sweep(
        [_card(state="In Progress")],
        extra={"pr_for": mock.MagicMock(return_value=merged)},
    )
    s.cmd_state.assert_called_once_with("DRE-2499", "Done")


# --------------------------------------------------------------------------
# Layer 0: the NO RUN notice reports one observation, not three suspects
# --------------------------------------------------------------------------
def test_no_run_notice_states_what_was_observed():
    """Plain English, and specific: how long, which lane, what was missing."""
    _, comment, _ = _run_watchdog([_card(labels=("repo:portico",))], bodies=[])
    body = comment.call_args.args[1]
    assert body.startswith(f"🚨 {reconcile.WATCHDOG_TAG}:")
    assert "no agent run" in body
    assert str(reconcile.WATCHDOG_MINUTES) in body
    assert "Todo" in body, "the notice must name the lane it observed"
    assert "receipt" in body, "the notice must name the evidence it looked for"


def test_no_run_notice_does_not_guess_at_three_causes():
    """A stall report names ONE cause or says it does not know. The old text
    listed the Actions budget, the LLM quota and the relay — three suspects,
    no evidence for any of them, and a reader left to check all three."""
    _, comment, _ = _run_watchdog([_card(labels=("repo:portico",))], bodies=[])
    body = comment.call_args.args[1].lower()
    for guess in ("actions budget", "llm quota", "the relay"):
        assert guess not in body, f"the notice still speculates about {guess!r}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
