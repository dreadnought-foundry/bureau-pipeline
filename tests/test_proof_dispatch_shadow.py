"""The proof dispatcher's shadow (DRE-7287).

While the repository variable `PROOF_SHADOW_MODEL` is set, each confirmed real
proof dispatch on a live pass is followed by one `proof-shadow` dispatch for
the same card, carrying the model to shadow on and the pass's time. The shadow
writes nothing to Linear, never counts in the tally or the first-run budget,
and a failed one never fails the pass. `Proof Shadow`
(`self-proof-shadow.yml`) answers the event; its stub shape is pinned in
tests/test_self_host_stubs.py.

Pinned here:

  * `plan_run.payload` with no extras is the payload it has always been, key
    for key, and with both extras it carries them inside GitHub's 10-key cap;
  * the variable unset or empty: a live pass fires exactly what it fires today;
  * the variable set: one shadow after each confirmed real dispatch, none after
    an unconfirmed one;
  * nothing written to Linear, the tally unchanged, a failed shadow named on
    its line and costing the pass nothing;
  * the dry pass's `would: shadow` line;
  * the `Dispatch proof runs` step passing the variable through.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import plan_run  # noqa: E402
import proof_dispatch  # noqa: E402
from test_proof_dispatch import (  # noqa: E402
    NOW,
    REPO,
    SLUG,
    Board,
    Harness,
    _lines,
    _step,
    fake_voices,
    lane_card,
    state,
)

MODEL = "claude-sonnet-5-5"
#: `NOW` in the contract's form: UTC, `YYYY-MM-DDTHH:MM:SSZ`.
DISPATCHED_AT = "2026-10-06T17:30:00Z"


def card(ident: str = "DRE-5930") -> dict:
    return {
        "id": f"uuid-{ident}", "identifier": ident, "title": f"PROOF: {ident}",
        "description": None,
        "labels": {"nodes": [{"name": "repo:bureau-pipeline"}, {"name": "Agent:Ops"}]},
    }


# --------------------------------------------------------------------------- #
# plan_run                                                                     #
# --------------------------------------------------------------------------- #


def test_the_shadow_event_is_one_of_the_events():
    assert plan_run.PROOF_SHADOW_EVENT == "proof-shadow"
    assert plan_run.PROOF_SHADOW_EVENT in plan_run.EVENTS
    for event in ("agent-plan", "agent-execute", "proof-execute"):
        assert event in plan_run.EVENTS


def test_a_payload_with_no_extras_is_byte_identical_to_todays():
    expected = {
        "card_id": "uuid-DRE-5930",
        "identifier": "DRE-5930",
        "title": "PROOF: DRE-5930",
        "description": "",
        "labels": ["repo:bureau-pipeline", "agent:ops"],
        "url": "https://linear.app/dreadnoughtfoundry/issue/DRE-5930",
        "reason": "first proof run",
    }
    got = plan_run.payload(card(), reason="first proof run")
    assert json.dumps(got) == json.dumps(expected)
    assert "shadow_model" not in plan_run.payload(card())
    assert "dispatched_at" not in plan_run.payload(card())


def test_a_shadow_payload_carries_both_extras_inside_the_ten_key_cap():
    got = plan_run.payload(card(), reason="first proof run",
                           shadow_model=MODEL, dispatched_at=DISPATCHED_AT)
    assert got["shadow_model"] == MODEL
    assert got["dispatched_at"] == DISPATCHED_AT
    assert len(got) == 9
    assert len(got) <= plan_run.PAYLOAD_KEY_CAP == 10


def test_a_payload_over_the_cap_is_refused_before_anything_is_sent():
    with pytest.raises(ValueError, match="10"):
        plan_run.payload(card(), trigger_state="in progress", reason="r",
                         sent_by_run=1, shadow_model=MODEL,
                         dispatched_at=DISPATCHED_AT)


def _sent(monkeypatch, **kwargs) -> dict:
    """What `fire` hands `gh api` — the file named by `--input`, read while
    it still exists."""
    seen = {}

    def run(argv, **_):
        seen["argv"] = argv
        seen["body"] = json.loads(Path(argv[argv.index("--input") + 1]).read_text())
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(plan_run.subprocess, "run", run)
    assert plan_run.fire(card(), REPO, **kwargs) == (True, "")
    return seen["body"]


def test_fire_sends_the_shadow_event_with_both_extras(monkeypatch):
    body = _sent(monkeypatch, reason="first proof run",
                 event=plan_run.PROOF_SHADOW_EVENT, shadow_model=MODEL,
                 dispatched_at=DISPATCHED_AT)
    assert body["event_type"] == "proof-shadow"
    assert body["client_payload"]["shadow_model"] == MODEL
    assert body["client_payload"]["dispatched_at"] == DISPATCHED_AT
    assert body["client_payload"]["identifier"] == "DRE-5930"


def test_fire_without_extras_sends_todays_payload(monkeypatch):
    body = _sent(monkeypatch, reason="first proof run", event=plan_run.PROOF_EVENT)
    assert body == {"event_type": "proof-execute",
                    "client_payload": plan_run.payload(card(), reason="first proof run")}


# --------------------------------------------------------------------------- #
# the dispatcher                                                               #
# --------------------------------------------------------------------------- #


class ShadowHarness(Harness):
    """`Harness`, with a `fire` that takes the shadow's two keys and can fail
    the real dispatch and the shadow apart."""

    def __init__(self, monkeypatch, board, *, shadow_ok=True, shadow_raises=False,
                 **kwargs):
        super().__init__(monkeypatch, board, **kwargs)
        self.shadow_ok, self.shadow_raises = shadow_ok, shadow_raises
        self.calls: list = []

    def fire(self, card, repo, **kwargs):
        self.calls.append((card["identifier"], repo, kwargs))
        if kwargs.get("event") == plan_run.PROOF_SHADOW_EVENT:
            if self.shadow_raises:
                raise RuntimeError("gh is not on PATH")
            return (True, "") if self.shadow_ok else (False, "gh api refused: HTTP 422")
        return (True, "") if self.fire_ok else (False, "gh api refused: HTTP 403")

    def sweep(self, *, live=True, now=NOW, shadow_model=None):
        return proof_dispatch.sweep(
            REPO, SLUG, live=live, linear=self.board, read=lambda path: None,
            find_pr=self.find_pr, run_state=self.run_state,
            release=self.release, fire=self.fire, voices=fake_voices, now=now,
            find_record=self.find_record, read_record=self.read_record,
            shadow_model=shadow_model)


REAL = {"reason": "first proof run", "event": "proof-execute"}


@pytest.mark.parametrize("value", [None, ""])
def test_with_the_variable_unset_or_empty_a_live_pass_fires_what_it_fires_today(
        monkeypatch, capsys, value):
    if value is None:
        monkeypatch.delenv(proof_dispatch.SHADOW_VARIABLE, raising=False)
    else:
        monkeypatch.setenv(proof_dispatch.SHADOW_VARIABLE, value)
    h = ShadowHarness(monkeypatch, Board(hand=[lane_card("DRE-5930")]))
    tally = h.sweep()
    assert h.calls == [("DRE-5930", REPO, REAL)]
    assert tally.dispatched == 1
    assert not [line for line in _lines(capsys) if "proof-shadow" in line]


def test_the_variable_is_read_from_the_environment(monkeypatch, capsys):
    monkeypatch.setenv("PROOF_SHADOW_MODEL", MODEL)
    assert proof_dispatch.SHADOW_VARIABLE == "PROOF_SHADOW_MODEL"
    h = ShadowHarness(monkeypatch, Board(hand=[lane_card("DRE-5930")]))
    h.sweep()
    assert [c[2]["event"] for c in h.calls] == ["proof-execute", "proof-shadow"]


def test_a_confirmed_dispatch_is_followed_by_exactly_one_shadow(monkeypatch, capsys):
    h = ShadowHarness(monkeypatch, Board(hand=[lane_card("DRE-5930")]))
    tally = h.sweep(shadow_model=MODEL)
    assert h.calls == [
        ("DRE-5930", REPO, REAL),
        ("DRE-5930", REPO, {"reason": "first proof run", "event": "proof-shadow",
                            "shadow_model": MODEL, "dispatched_at": DISPATCHED_AT}),
    ]
    assert f"proof-shadow: dispatched DRE-5930 on {MODEL}" in _lines(capsys)
    assert tally.dispatched == 1 and tally.eligible == 1 and tally.failures == []


def test_an_unconfirmed_dispatch_starts_no_shadow(monkeypatch, capsys):
    h = ShadowHarness(monkeypatch, Board(hand=[lane_card("DRE-5930")]), fire_ok=False)
    tally = h.sweep(shadow_model=MODEL)
    assert h.calls == [("DRE-5930", REPO, REAL)]
    assert tally.failures
    assert not [line for line in _lines(capsys) if "proof-shadow" in line]


def test_the_shadow_writes_nothing_to_linear_and_leaves_the_tally_alone(
        monkeypatch, capsys):
    plain = ShadowHarness(monkeypatch, Board(hand=[lane_card("DRE-5930")]))
    without = plain.sweep(shadow_model="")
    shadowed = ShadowHarness(monkeypatch, Board(hand=[lane_card("DRE-5930")]))
    with_ = shadowed.sweep(shadow_model=MODEL)
    assert len(shadowed.calls) == 2
    assert shadowed.posted == plain.posted and len(shadowed.posted) == 1
    assert shadowed.holds == plain.holds == []
    assert shadowed.order == plain.order
    assert with_ == without


@pytest.mark.parametrize("raises", [False, True])
def test_a_failed_shadow_costs_the_pass_nothing(monkeypatch, capsys, raises):
    plain = ShadowHarness(monkeypatch, Board(hand=[lane_card("DRE-5930")]))
    without = plain.sweep(shadow_model="")
    capsys.readouterr()
    h = ShadowHarness(monkeypatch, Board(hand=[lane_card("DRE-5930")]),
                      shadow_ok=False, shadow_raises=raises)
    tally = h.sweep(shadow_model=MODEL)
    lines = _lines(capsys)
    assert tally == without and tally.failures == []
    assert h.posted == plain.posted
    (line,) = [line for line in lines if line.startswith("proof-shadow:")]
    assert line.startswith("proof-shadow: not dispatched DRE-5930 — ")
    assert ("gh is not on PATH" if raises else "HTTP 422") in line


def test_a_failed_shadow_leaves_main_exiting_as_it_would_have(monkeypatch, capsys):
    monkeypatch.setenv("REPO", REPO)
    monkeypatch.setenv("REPO_SLUG", SLUG)
    monkeypatch.setenv("PROOF_DISPATCH_LIVE", "true")
    monkeypatch.setenv("PROOF_SHADOW_MODEL", MODEL)
    h = ShadowHarness(monkeypatch, Board(hand=[lane_card("DRE-5930")]), shadow_ok=False)
    real_sweep = proof_dispatch.sweep

    def sweep(repo, slug, *, live, **_):
        return real_sweep(repo, slug, live=live, linear=h.board,
                          read=lambda path: None, find_pr=h.find_pr,
                          run_state=h.run_state, release=h.release, fire=h.fire,
                          voices=fake_voices, now=NOW, find_record=h.find_record,
                          read_record=h.read_record)

    monkeypatch.setattr(proof_dispatch, "sweep", sweep)
    assert proof_dispatch.main([]) == 0
    assert [c[2]["event"] for c in h.calls] == ["proof-execute", "proof-shadow"]
    assert any(line.startswith("proof-shadow: not dispatched DRE-5930 — ")
               for line in _lines(capsys))


def test_a_second_dispatch_is_shadowed_too(monkeypatch, capsys):
    """Every confirmed real dispatch is followed by a shadow: the second
    dispatch, the return and the re-runs all go through the one `dispatch`,
    and the shadow carries the reason the real one was sent with."""
    h = ShadowHarness(monkeypatch, Board(hand=[lane_card("DRE-5930")]),
                      states={"DRE-5930": state("never-started", dispatches=1)})
    h.sweep(shadow_model=MODEL)
    assert [c[2]["event"] for c in h.calls] == ["proof-execute", "proof-shadow"]
    assert h.calls[0][2]["reason"] == h.calls[1][2]["reason"]


def test_a_dry_pass_prints_the_shadow_it_would_start_and_fires_nothing(
        monkeypatch, capsys):
    h = ShadowHarness(monkeypatch, Board(hand=[lane_card("DRE-5930")]))
    h.sweep(live=False, shadow_model=MODEL)
    lines = _lines(capsys)
    assert h.calls == [] and h.posted == [] and h.holds == []
    dispatch = lines.index("would: dispatch DRE-5930 — first proof run")
    assert lines[dispatch + 1] == f"would: shadow DRE-5930 on {MODEL}"


def test_a_dry_pass_without_the_variable_prints_no_shadow(monkeypatch, capsys):
    h = ShadowHarness(monkeypatch, Board(hand=[lane_card("DRE-5930")]))
    h.sweep(live=False, shadow_model="")
    assert not [line for line in _lines(capsys) if "shadow" in line]


def test_the_shadow_writes_through_no_linear_seam(monkeypatch, capsys):
    """Counted at the seam itself: every Linear writer the phase could reach
    is stubbed and counted, and the shadow adds none of them."""
    counts = {"comment": 0, "proof_waiting": 0, "advance": 0}

    def count(name):
        def bump(*args, **kwargs):
            counts[name] += 1
        return bump

    h = ShadowHarness(monkeypatch, Board(hand=[lane_card("DRE-5930")]))
    monkeypatch.setattr(linear_ops, "cmd_comment", count("comment"))
    monkeypatch.setattr(linear_ops, "cmd_proof_waiting", count("proof_waiting"))
    monkeypatch.setattr(linear_ops, "cmd_advance", count("advance"), raising=False)
    h.sweep(shadow_model=MODEL)
    assert counts == {"comment": 1, "proof_waiting": 0, "advance": 0}


# --------------------------------------------------------------------------- #
# the step                                                                     #
# --------------------------------------------------------------------------- #


def test_the_step_passes_the_callers_shadow_variable():
    env = _step()["env"]
    assert env["PROOF_SHADOW_MODEL"] == "${{ vars.PROOF_SHADOW_MODEL }}"
    assert env["PROOF_DISPATCH_LIVE"] == "${{ vars.PROOF_DISPATCH_LIVE }}"
