"""RED-first: the "work never landed" notice names the real reason (DRE-6777).

THE BUG (seen 2026-10-10, re-reading proof DRE-6364): the unlanded-work
watchdog's no-branch notice (`_flag_hand_built_idle`) posts for every card
`hand_built(card)` answers true for, and says "It is labelled 'hand-built'".
`hand_built` is true for four reasons — the CEO's `hand-built` mark, another
person mark (`operator-step`), a `PROOF:` title, and a person's verdict on a
card sitting in the lane it sends cards to — so on an operator-step card the
notice claimed the one mark only the CEO applies.

FIX UNDER TEST — the notice reads its reason from `hand_built_reason(card)`,
the one statement of why the card is a person's (DRE-6424), and says the CEO's
label only when the card carries it. When it posts, and that it posts once,
stay pinned by tests/test_unlanded_work_watchdog.py.

Run: cd bureau-pipeline && python3 -m pytest tests/test_unlanded_notice_names_reason.py -v
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("REPO_SLUG", "test")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

CARD = "DRE-7777"
HAND_WORK = "Hand-work"
HAND_BUILT = "hand-built"
OPERATOR_STEP = "operator-step"
NO_CODE = "no-code"


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """REPO_SLUG is bound at import; pin it so this sweep owns the card."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "test")


def _card(state="In Progress", labels=(HAND_BUILT,), title="a card", comments=()):
    stale = reconcile.HAND_IDLE_MINUTES + 60
    return {
        "identifier": CARD,
        "title": title,
        "description": "**Repo:** test",
        "updatedAt": (datetime.now(UTC) - timedelta(minutes=stale))
        .isoformat().replace("+00:00", "Z"),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {"nodes": [{"body": b} for b in comments]},
    }


def _notice(card) -> str:
    """The one notice the no-branch half posts for `card`."""
    posted = []
    with mock.patch.object(reconcile, "active_cards",
                           side_effect=lambda states=None: [
                               c for c in [card]
                               if c["state"]["name"] in (states or ())]), \
            mock.patch.object(reconcile.linear_ops, "comment_bodies", return_value=[]), \
            mock.patch.object(reconcile.linear_ops, "cmd_comment",
                              side_effect=lambda i, b: posted.append((i, b))):
        reconcile._flag_hand_built_idle([], set())
    assert len(posted) == 1, posted
    ident, body = posted[0]
    assert ident == CARD
    assert f"{reconcile.UNLANDED_TAG} no branch:" in body
    return body


def _claims_the_label(body: str) -> bool:
    """Either spelling: the claim, not the spelling, is what is false."""
    return any(f"{word} '{HAND_BUILT}'" in body for word in ("labelled", "labeled"))


def test_a_hand_built_card_is_said_to_carry_the_label():
    body = _notice(_card(labels=(HAND_BUILT,)))
    assert f"It is {reconcile.hand_built_reason(_card())}" in body
    assert "It is labeled 'hand-built'" in body
    assert "no dispatched run is coming by design" in body


def test_an_operator_step_card_names_the_operator_step():
    card = _card(state=HAND_WORK, labels=(OPERATOR_STEP, NO_CODE))
    body = _notice(card)
    assert not _claims_the_label(body)
    assert f"It is labeled '{OPERATOR_STEP}'" in body


def test_a_proof_card_says_the_proof_run_takes_it():
    """DRE-6364's shape: a PROOF: card wearing `no-code` and no person mark."""
    card = _card(state=HAND_WORK, labels=("agent:ops", NO_CODE),
                 title="PROOF: on the live board nothing automatic applies hand-built")
    body = _notice(card)
    assert not _claims_the_label(body)
    assert "It is a PROOF: card" in body
    assert "the proof run takes" in body
    assert "no dispatched run is coming" not in body


def test_a_workbench_card_in_hand_work_names_the_verdict():
    """Carried to Hand-work on a WORKBENCH verdict, which marks nothing."""
    verdict = routing_verdict.verdict_comment("WORKBENCH", "it drives a live auth flow")
    card = _card(state=HAND_WORK, labels=(), comments=(verdict,))
    body = _notice(card)
    assert not _claims_the_label(body)
    assert f"It is routed WORKBENCH to {HAND_WORK}" in body


def test_the_reason_is_read_from_hand_built_reason_and_never_restated():
    """One rule (DRE-6424): the notice reads the reason function, and spells
    neither the CEO's label nor a reason of its own. The docstring is prose
    and is left out: it may name the label it explains."""
    func = ast.parse(textwrap.dedent(inspect.getsource(reconcile._flag_hand_built_idle))).body[0]
    source = "\n".join(ast.unparse(stmt) for stmt in func.body[1:])
    assert ast.get_docstring(func), "the first statement is no longer the docstring"
    assert "hand_built_reason(card)" in source
    assert "HAND_BUILT_LABEL" not in source
    assert "routing_verdict" not in source
    assert "is_proof" not in source
