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
suppresses reconcile.flag_stranded() for that card, BOTH classes, and nothing
else. The PR-level backstops in the same file (flag_no_checks_prs,
flag_unowned_prs, unstick_conflicts, retrigger_dead_heads, …) key on the PULL
REQUEST, never on the card's labels: a hand-built card whose PR wedges must
still be caught, so the label must not become a second, wider hold.

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


def test_only_the_watchdog_consults_the_hand_built_label():
    """Structural guard, not a sentinel: if a later change wires hand-built
    into a repair or dispatch path, this says so. The label suppresses the
    stranded watchdog and NOTHING else (DRE-2524)."""
    assert _call_owners("hand_built") == {"flag_stranded"}


def test_the_owner_sweep_can_actually_see_a_call():
    """Guard the guard: a detector that matches nothing would pass forever."""
    assert _call_owners("held") == {"flag_stranded", "main"}


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
