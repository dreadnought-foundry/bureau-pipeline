"""TDD for the dependency-gate's unresolved-agent-blocker guard (DRE-1585).

PROBLEM: when the engineer agent hits a genuine, DETERMINISTIC blocker it posts
a `🛑 Agent blocked` comment and parks the card back in Backlog ON PURPOSE — a
Todo return would redispatch the next agent into the identical wall. But the
dependency gate (promote_ready) only looks at FORMAL blockers (blocks relations
+ "Blocked by:" lines). When those happen to be Done and the parent epic is
active, the next reconcile sweep re-promoted the card anyway. Real incident:
DRE-1572 looped Backlog→Todo→In Progress→Backlog FIVE times, burning five
engineer runs.

FIX UNDER TEST: reconcile.has_unresolved_blocker(card) + a guard in
promote_ready — before promoting a Backlog card, skip it if its latest decisive
comment is the engineer's `🛑 Agent blocked` marker with no human reply after
it. The gate's own "🧹 Auto-promoted" receipt is a machine marker, so it can
never clear the blocker and re-arm the loop; a human comment after the marker
does resolve it.

SINCE DRE-6448 the gate no longer only skips such a card: on the full sweep
(`promote_ready(..., resolve_blockers=True)`) it names the blocker's class,
re-reads the card live and hands it to `blocker_resolve.resolve_blocker`, and
the card is never promoted on the pass that read its marker. Every test here
that carries a marker stubs that ONE function, so no test in this file reaches
a real action module or the network, on this tree or after the modules land.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import ast
import contextlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import blocker_class  # noqa: E402
import blocker_resolve  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

BLOCKER = (
    "🛑 Agent blocked: the upstream `/v2/widgets` endpoint does not exist — "
    "parked in Backlog until the blocker is resolved. Run: https://x"
)


ROUTING_FLEET = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so promote_ready
    recognises this test's agent-bureau cards regardless of collection order
    (the same test-isolation hazard guarded in test_human_hold.py)."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


def _candidate(comments, identifier="DRE-1572", *, labels=("size:M",), relations=()):
    """A Backlog child with an active parent epic, no formal blockers, and the
    given comment bodies (oldest→newest) — eligible on formal grounds, so only
    the new blocker guard can hold it back."""
    return {
        "identifier": identifier,
        "description": "**Repo:** agent-bureau\nwork",
        "parent": {"identifier": "DRE-1268", "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": n} for n in labels]},
        # Served NEWEST FIRST, the order Linear answers a comment window in
        # (DRE-3250); `comments` is written oldest→newest above.
        # The routing verdict is the OLDEST comment on every card — it is
        # written at planning exit — and since DRE-3385 a card carrying none is
        # refused promotion, which would mask the blocker guard under test.
        "comments": {"nodes": [
            {"body": b} for b in reversed([ROUTING_FLEET, *comments])
        ]},
        "inverseRelations": {"nodes": [
            {"type": "blocks", "issue": {"identifier": i, "state": {"name": s}}}
            for i, s in relations
        ]},
    }


# --------------------------------------------------------------------------
# has_unresolved_blocker — the detector
# --------------------------------------------------------------------------
def test_blocker_is_latest_comment_is_unresolved():
    card = _candidate(["🤖 PR opened: https://x", BLOCKER])
    assert reconcile.has_unresolved_blocker(card) is True


def test_human_reply_after_blocker_resolves_it():
    """A plain-English human comment after the marker clears it."""
    card = _candidate([BLOCKER, "Created the endpoint, you can proceed now."])
    assert reconcile.has_unresolved_blocker(card) is False


def test_gate_receipt_after_blocker_does_not_resolve():
    """The gate's own machine receipt must NOT count as a human resolution —
    otherwise the very act of (wrongly) promoting would clear the guard and
    re-arm the five-run loop."""
    card = _candidate(
        [BLOCKER, "🧹 Auto-promoted Backlog → Todo: parent epic active and all blockers Done."]
    )
    assert reconcile.has_unresolved_blocker(card) is True


def test_no_blocker_comment_is_not_blocked():
    card = _candidate(["🤖 PR opened: https://x"])
    assert reconcile.has_unresolved_blocker(card) is False


def test_missing_comments_key_is_not_blocked():
    """Hand-built fixtures without a comments key are treated as unblocked."""
    card = {"identifier": "DRE-1", "labels": {"nodes": []}}
    assert reconcile.has_unresolved_blocker(card) is False




# --------------------------------------------------------------------------
# The machine prefixes (DRE-6448) — the pipeline's own ask is never a reply
# --------------------------------------------------------------------------
def test_the_ask_prefix_is_a_machine_prefix():
    assert "🙋" in reconcile._AGENT_COMMENT_PREFIXES


def test_the_pipelines_ask_after_a_marker_leaves_it_open():
    """The sweep's ask and the build run's escalation both open with 🙋: an
    ask whose Green Light move did not land is not the person answering."""
    card = _candidate([BLOCKER, "🙋 Which reading of the retry budget is right?"])
    assert reconcile.has_unresolved_blocker(card) is True


def test_a_persons_reply_after_a_marker_still_resolves_it():
    card = _candidate([BLOCKER, "a person's reply"])
    assert reconcile.has_unresolved_blocker(card) is False


def test_the_resolvers_receipt_after_a_marker_resolves_it():
    """The receipt opens with 🧹, a machine prefix — it is its TAG that
    closes the marker, not a person's voice."""
    card = _candidate([BLOCKER, RECEIPT])
    assert reconcile.has_unresolved_blocker(card) is False


def test_open_agent_blocker_reads_the_class_off_the_comments():
    found = reconcile.open_agent_blocker(_candidate([CLASS_MARKER]))
    assert isinstance(found, blocker_class.Blocker)
    assert found.cls == "nothing-to-change"
    assert reconcile.open_agent_blocker(_candidate([CLASS_MARKER, RECEIPT])) is None


# --------------------------------------------------------------------------
# promote_ready — the gate hands an open blocker to the resolver (DRE-6448)
# --------------------------------------------------------------------------
CLASS_MARKER = (
    "🛑 Agent blocked: class=nothing-to-change · There is nothing for this card "
    "to change. — parked in Backlog until the blocker is resolved. Run: https://x"
)
# The resolver's receipt as DRE-6508 posts it: 🧹, then the act's tag.
RECEIPT = "🧹 agent-blocker-resolved: class=question action=asked — asked in Green Light"
DRE_5195 = next(
    row for row in json.loads(
        (_ROOT / "tests" / "fixtures" / "blocker-reasons.json").read_text())
    if row["card"] == "DRE-5195"
)


class _Pass:
    """One `promote_ready` pass over `cards`, every Linear read and write
    stubbed. `_fetch_backlog_linear` answers the same cards as the board by
    default (`live` overrides it: a list, or an exception to raise), and
    `blocker_resolve.resolve_blocker` is the ONE stub of the epic — it records
    each call and answers `answer` (a pair, None, or an exception)."""

    def __init__(self, cards, *, live=None, answer=("canceled", "every criterion holds")):
        self.cards = cards
        self.live = live
        self.answer = answer
        self.calls: list[tuple[dict, blocker_class.Blocker, str]] = []
        self.fetched: list[list[str]] = []
        self.comments: list[tuple[str, str]] = []
        self.advance = None
        self.promoted = None

    def _resolve(self, card, blocker, *, repo):
        self.calls.append((card, blocker, repo))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer

    def _fetch(self, only, *, stamped=False):
        self.fetched.append(list(only))
        if isinstance(self.live, BaseException):
            raise self.live
        live = self.cards if self.live is None else self.live
        return [c for c in live if c["identifier"] in only]

    def run(self, active_count=0, epic_unmet=False, **kwargs):
        reconcile._write_failures.clear()
        reconcile._card_skips.clear()
        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(reconcile, "backlog_children", return_value=self.cards))
            enter(patch.object(reconcile, "_fetch_backlog_linear", side_effect=self._fetch))
            enter(patch.object(blocker_resolve, "resolve_blocker", side_effect=self._resolve))
            enter(patch.object(reconcile, "epic_blockers_unmet", return_value=epic_unmet))
            enter(patch.object(reconcile, "card_state", return_value="Done"))
            enter(patch.object(reconcile.mid_epic, "last_green_light", return_value=None))
            enter(patch.object(reconcile.linear_ops, "count_comments", return_value=0))
            enter(patch.object(reconcile.linear_ops, "first_comment_at", return_value=None))
            enter(patch.object(reconcile.linear_ops, "add_label"))
            enter(patch.object(reconcile.linear_ops, "cmd_comment",
                               side_effect=lambda i, b: self.comments.append((i, b))))
            self.advance = enter(patch.object(reconcile.linear_ops, "cmd_advance"))
            self.promoted = reconcile.promote_ready(active_count=active_count, **kwargs)
        return self


def _gate_lines(out, identifier="DRE-1572"):
    return [ln for ln in out.splitlines()
            if ln.startswith(f"promotion: {identifier} agent-blocker class=")]


def test_a_class_marker_is_handed_to_the_resolver_and_never_promoted(capsys):
    run = _Pass([_candidate([CLASS_MARKER])]).run(resolve_blockers=True)
    assert len(run.calls) == 1
    _card, blocker, repo = run.calls[0]
    assert blocker.cls == "nothing-to-change"
    assert repo == reconcile.REPO
    assert run.promoted == 0
    run.advance.assert_not_called()
    out = capsys.readouterr().out
    assert ("promotion: DRE-1572 agent-blocker class=nothing-to-change resolved — "
            "canceled: every criterion holds") in out


def test_a_legacy_marker_reaches_the_resolver_through_the_phrase_fallback():
    """DRE-5195's own marker, no `class=` on it: the phrases name the class."""
    assert "class=" not in DRE_5195["body"]
    run = _Pass([_candidate([DRE_5195["body"]])]).run(resolve_blockers=True)
    assert [b.cls for _c, b, _r in run.calls] == ["nothing-to-change"]
    run.advance.assert_not_called()


def test_a_none_from_the_resolver_prints_nothing_more_and_the_next_pass_asks_again(capsys):
    """None means the resolver printed its own line and the marker is open."""
    card = _candidate([CLASS_MARKER])
    run = _Pass([card], answer=None).run(resolve_blockers=True)
    assert run.promoted == 0
    run.advance.assert_not_called()
    assert _gate_lines(capsys.readouterr().out) == []
    again = _Pass([card], answer=None).run(resolve_blockers=True)
    assert len(again.calls) == 1
    again.advance.assert_not_called()


def test_without_the_keyword_the_blocker_is_named_and_left_for_the_full_sweep(capsys):
    run = _Pass([_candidate([CLASS_MARKER])]).run()
    assert run.calls == []
    assert run.fetched == []
    assert run.promoted == 0
    run.advance.assert_not_called()
    lines = _gate_lines(capsys.readouterr().out)
    assert lines == [
        "promotion: DRE-1572 agent-blocker class=nothing-to-change — not resolved "
        "this pass: blockers are resolved by the full sweep"
    ]


def test_exactly_one_promote_ready_call_resolves_blockers_and_it_is_the_full_sweeps():
    tree = ast.parse((_ROOT / "scripts" / "reconcile.py").read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "promote_ready"]
    assert len(calls) == 3, [ast.unparse(c) for c in calls]

    def kw(call, name):
        return next((k.value for k in call.keywords if k.arg == name), None)

    resolving = [c for c in calls
                 if isinstance(kw(c, "resolve_blockers"), ast.Constant)
                 and kw(c, "resolve_blockers").value is True]
    assert len(resolving) == 1, [ast.unparse(c) for c in calls]
    assert ast.unparse(kw(resolving[0], "close_epics")) == "True"
    # The two `--promote-only` calls pass nothing: the default is False.
    assert all(kw(c, "resolve_blockers") is None for c in calls if c is not resolving[0])


def test_a_card_no_longer_in_backlog_is_skipped_without_a_call(capsys):
    run = _Pass([_candidate([CLASS_MARKER])], live=[]).run(resolve_blockers=True)
    assert run.calls == []
    run.advance.assert_not_called()
    assert _gate_lines(capsys.readouterr().out) == [
        "promotion: DRE-1572 agent-blocker class=nothing-to-change — no longer in "
        "Backlog since the board was read — skipping"
    ]


def test_a_card_resolved_since_the_board_was_read_is_skipped_without_a_call(capsys):
    live = [_candidate([CLASS_MARKER, "Answered — go ahead."])]
    run = _Pass([_candidate([CLASS_MARKER])], live=live).run(resolve_blockers=True)
    assert run.calls == []
    run.advance.assert_not_called()
    assert _gate_lines(capsys.readouterr().out) == [
        "promotion: DRE-1572 agent-blocker class=nothing-to-change — resolved since "
        "the board was read — skipping"
    ]


def test_an_unreadable_live_card_is_left_for_the_next_pass_and_the_pass_goes_on(capsys):
    blocked = _candidate([CLASS_MARKER])
    clean = _candidate(["🤖 PR opened: https://x"], identifier="DRE-1580")
    run = _Pass([blocked, clean], live=linear_ops.LinearError("HTTP 502"))
    run.run(resolve_blockers=True)
    assert run.calls == []
    assert run.promoted == 1
    run.advance.assert_called_once_with("DRE-1580", "Todo", "Backlog")
    assert _gate_lines(capsys.readouterr().out) == [
        "promotion: DRE-1572 agent-blocker class=nothing-to-change — not resolved "
        "this pass: it could not be read live (HTTP 502)"
    ]


def test_a_spent_quota_on_the_live_read_ends_the_pass():
    run = _Pass([_candidate([CLASS_MARKER])], live=linear_ops.LinearRateLimited("quota"))
    with pytest.raises(linear_ops.LinearRateLimited):
        run.run(resolve_blockers=True)
    assert run.calls == []


def test_the_resolver_is_handed_the_live_card_not_the_boards():
    board = _candidate([CLASS_MARKER])
    live = _candidate([CLASS_MARKER], labels=("size:M", "infra"))
    run = _Pass([board], live=[live]).run(resolve_blockers=True)
    assert len(run.calls) == 1
    assert run.calls[0][0] is live


@pytest.mark.parametrize("error", [RuntimeError("boom"), linear_ops.LinearError("boom")])
def test_a_resolver_failure_is_one_cards_and_the_next_card_still_promotes(error, capsys):
    blocked = _candidate([CLASS_MARKER])
    clean = _candidate(["🤖 PR opened: https://x"], identifier="DRE-1580")
    run = _Pass([blocked, clean], answer=error)

    def never(*_a, **_k):
        raise AssertionError("skip_bad_reference must not be reached by the blocker block")

    with patch.object(reconcile, "skip_bad_reference", side_effect=never):
        run.run(resolve_blockers=True)
    assert reconcile._write_failures == [
        "DRE-1572 agent-blocker class=nothing-to-change: boom"]
    assert not [b for i, b in run.comments if b.startswith("🚨")]
    assert run.promoted == 1
    run.advance.assert_called_once_with("DRE-1580", "Todo", "Backlog")
    err = capsys.readouterr().err
    assert "ERROR: DRE-1572 agent-blocker class=nothing-to-change — not resolved: boom" in err


def test_a_spent_quota_in_the_resolver_ends_the_pass_and_is_no_write_failure():
    run = _Pass([_candidate([CLASS_MARKER])], answer=linear_ops.LinearRateLimited("quota"))
    with pytest.raises(linear_ops.LinearRateLimited):
        run.run(resolve_blockers=True)
    assert reconcile._write_failures == []


def test_at_the_cap_a_blocker_is_still_resolved_and_never_counted_as_waiting(capsys):
    blocked = _candidate([CLASS_MARKER])
    waiting = _candidate(["🤖 PR opened: https://x"], identifier="DRE-1600")
    run = _Pass([blocked, waiting]).run(
        active_count=reconcile.MAX_WIP, resolve_blockers=True)
    assert len(run.calls) == 1
    assert run.calls[0][0]["identifier"] == "DRE-1572"
    assert run.promoted == 0
    run.advance.assert_not_called()
    budget = [ln for ln in capsys.readouterr().out.splitlines()
              if "WIP budget spent" in ln]
    assert len(budget) == 1, budget
    assert "— 1 candidate(s) not considered" in budget[0]
    assert "lowest-numbered DRE-1600" in budget[0]


@pytest.mark.parametrize("epic_unmet, held_line", [
    (False, "promotion: DRE-1572 is held by DRE-1571 (In Review)"),
    (True, "promotion: DRE-1572's epic DRE-1268 is not releasing its children"),
])
def test_the_blocker_block_sits_ahead_of_the_gates_inside_the_read_guard(
        epic_unmet, held_line, capsys):
    """The card's stated design (DRE-6448): the blocker block sits BEFORE the
    read guard, so ahead of the epic gate and the formal-blockedBy hold. An
    open marker records a build that already ran, and the block can never
    promote — it ends in one `continue` — so on the pass that resolves the
    marker the card prints its `agent-blocker class=` line, and the gate that
    holds it prints its own line on the NEXT pass, once the receipt is on the
    thread."""
    first = _candidate([CLASS_MARKER], relations=[("DRE-1571", "In Review")])
    run = _Pass([first]).run(epic_unmet=epic_unmet, resolve_blockers=True)
    assert len(run.calls) == 1
    run.advance.assert_not_called()
    out = capsys.readouterr().out
    assert len(_gate_lines(out)) == 1
    assert "resolved — canceled:" in out
    assert held_line not in out

    second = _candidate([CLASS_MARKER, RECEIPT], relations=[("DRE-1571", "In Review")])
    run = _Pass([second]).run(epic_unmet=epic_unmet, resolve_blockers=True)
    assert run.calls == []
    run.advance.assert_not_called()
    out = capsys.readouterr().out
    assert _gate_lines(out) == []
    assert held_line in out


def test_promote_ready_promotes_card_with_resolved_blocker():
    """Control A: a human resolved the blocker — the card IS promoted, and
    the resolver is never asked."""
    run = _Pass([_candidate([BLOCKER, "Fixed upstream, go ahead."])])
    run.run(resolve_blockers=True)
    assert run.promoted == 1
    assert run.calls == []
    run.advance.assert_called_once_with("DRE-1572", "Todo", "Backlog")


def test_promote_ready_promotes_card_without_blocker():
    """Control B: a card that never blocked is promoted as before."""
    run = _Pass([_candidate(["🤖 PR opened: https://x"])]).run(resolve_blockers=True)
    assert run.promoted == 1
    assert run.calls == []
    assert run.fetched == []
    run.advance.assert_called_once_with("DRE-1572", "Todo", "Backlog")
