"""RED-first: the sandbox groom proposal (DRE-8782).

The groomer's memory proofs (DRE-8331, DRE-8333, DRE-8335, DRE-8357) press on
sandbox cards, never on real ones, so they need a proposal that grooms the
sandbox alone. `propose --sandbox` is that proposal:

  * it reads only the lane's cards labeled `repo:agent-bureau-demo`, the
    sandbox fixtures among them, and never the sandbox groom card itself,
    which `config/groom-sandbox.json` names — committed config, because a
    proof run cannot read a repo variable (`config/proof-reach.json`);
  * the proposal says it is the sandbox's, and `targets` then judges a
    sandbox fixture instead of excluding it (DRE-7783 is the real
    proposal's rule, and those fixtures are exactly what this one grooms);
  * with `--sandbox` off nothing changes: the real proposal still reads the
    whole lane and still leaves a sandbox fixture where it is;
  * nobody drains a sandbox proposal: `drain` on the sandbox groom card
    refuses before it reads or writes anything, and says why.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_sandbox.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_sandbox  # noqa: E402
import groom_verify_agent as gva  # noqa: E402
import groomer  # noqa: E402

from test_groomer import card  # noqa: E402
from test_groom_verify import _args, cancel, planning  # noqa: E402
from test_groomer_memory import patch  # noqa: E402
from test_groom_verify_agent import (  # noqa: E402
    CardText, build_targets, issue_node, read)

SANDBOX_CARD = "DRE-8780"
DEMO = "agent-bureau-demo"
FIXTURE = "SANDBOX FIXTURE (proof DRE-8333): a card the proof filed"
SUPERSEDED = "Superseded by: DRE-1"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "app-installation-token")


def the_lane():
    """Real cards and sandbox cards side by side, as the Intake lane holds
    them: a real card and a sandbox card each superseded (the Cancel list),
    a sandbox fixture, and the sandbox groom card itself."""
    return [
        card("DRE-201", repo="portico", days=1),
        card("DRE-202", repo="bureau-pipeline", days=2),
        card("DRE-203", repo="portico", days=3, description=SUPERSEDED),
        card("DRE-301", repo=DEMO, days=4),
        card("DRE-302", repo=DEMO, days=5, title=FIXTURE),
        card("DRE-303", repo=DEMO, days=6, description=SUPERSEDED),
        card(SANDBOX_CARD, repo=DEMO, days=7,
             title="SANDBOX FIXTURE (proof DRE-8333): the sandbox groom card"),
    ]


def build(monkeypatch, *, sandbox, current):
    patch(monkeypatch, the_lane())
    extra = {"sandbox": True} if sandbox else {}
    return groomer._build(_args(card=current, capacity=20, **extra))


def labels_of(identifier):
    return {n["name"] for n in next(
        c for c in the_lane() if c["identifier"] == identifier)["labels"]["nodes"]}


# --------------------------------------------------------------------------
# the committed config
# --------------------------------------------------------------------------
def test_the_sandbox_groom_card_is_named_in_committed_config():
    doc = json.loads((ROOT / "config" / "groom-sandbox.json").read_text())
    assert doc["card"] == SANDBOX_CARD
    assert groom_sandbox.CONFIG == ROOT / "config" / "groom-sandbox.json"
    assert groom_sandbox.card() == SANDBOX_CARD


def test_the_label_is_the_fixture_exclusions_own():
    """One sandbox label: the DRE-7783 exclusion reads the same one."""
    assert groom_sandbox.REPO_LABEL == gva.SANDBOX_FIXTURE_REPO_LABEL


@pytest.mark.parametrize("text", ["", "{", "[]", '{"card": ""}',
                                  '{"card": "not a card"}'])
def test_an_unreadable_config_names_no_card(tmp_path, text):
    path = tmp_path / "groom-sandbox.json"
    path.write_text(text)
    assert groom_sandbox.card(path) is None


def test_a_missing_config_names_no_card(tmp_path):
    assert groom_sandbox.card(tmp_path / "absent.json") is None


def test_the_cli_writes_the_card_for_the_gate(tmp_path, capsys):
    out = tmp_path / "github-output"
    assert groom_sandbox.main(["card", "--github-output", str(out)]) == 0
    assert out.read_text().splitlines() == [f"card={SANDBOX_CARD}"]
    assert SANDBOX_CARD in capsys.readouterr().out


def test_the_cli_fails_loudly_when_the_config_names_no_card(
        tmp_path, monkeypatch, capsys):
    bad = tmp_path / "groom-sandbox.json"
    bad.write_text("{}")
    monkeypatch.setattr(groom_sandbox, "CONFIG", bad)
    out = tmp_path / "github-output"
    assert groom_sandbox.main(["card", "--github-output", str(out)]) == 1
    assert not out.exists() or out.read_text() == ""
    assert "groom-sandbox.json" in capsys.readouterr().err


# --------------------------------------------------------------------------
# scope — the sandbox's cards, never its groom card
# --------------------------------------------------------------------------
def test_scope_keeps_the_sandboxs_cards_and_drops_its_groom_card():
    kept = groom_sandbox.scope(the_lane(), card=SANDBOX_CARD)
    assert [c["identifier"] for c in kept] == ["DRE-301", "DRE-302", "DRE-303"]


def test_scope_reads_any_repo_label_not_only_the_first():
    both = card("DRE-401", repo="portico")
    both["labels"]["nodes"].append({"name": "repo:agent-bureau-demo"})
    assert groom_sandbox.scope([both], card=SANDBOX_CARD) == [both]


# --------------------------------------------------------------------------
# propose --sandbox
# --------------------------------------------------------------------------
def test_a_sandbox_propose_lists_only_sandbox_cards(monkeypatch):
    proposal = build(monkeypatch, sandbox=True, current=SANDBOX_CARD)
    listed = planning(proposal) + [r["identifier"] for r in cancel(proposal)]
    assert listed, "the sandbox proposal listed nothing"
    for identifier in listed:
        assert groom_sandbox.REPO_LABEL in labels_of(identifier), identifier
    assert "DRE-302" in planning(proposal), "the sandbox fixture was left out"
    assert [r["identifier"] for r in cancel(proposal)] == ["DRE-303"]
    assert SANDBOX_CARD not in listed
    assert SANDBOX_CARD not in {r["identifier"] for r in proposal["sequence"]}
    assert proposal["sandbox"] is True


def test_without_sandbox_the_lists_are_the_whole_lane_as_today(monkeypatch):
    proposal = build(monkeypatch, sandbox=False, current="DRE-4541")
    assert "sandbox" not in proposal
    listed = set(planning(proposal)) | {r["identifier"] for r in cancel(proposal)}
    assert {"DRE-201", "DRE-202", "DRE-301", "DRE-302"} <= set(planning(proposal))
    assert {r["identifier"] for r in cancel(proposal)} == {"DRE-203", "DRE-303"}
    assert SANDBOX_CARD in listed


def test_the_cli_takes_sandbox_and_writes_the_flag(monkeypatch, tmp_path):
    patch(monkeypatch, the_lane())
    out = tmp_path / "proposal.json"
    assert groomer.main(["propose", "--sandbox", "--card", SANDBOX_CARD,
                         "--no-judgement", "--no-verify", "--capacity", "20",
                         "--out", str(out)]) == 0
    written = json.loads(out.read_text())
    assert written["sandbox"] is True
    assert set(planning(written)) == {"DRE-301", "DRE-302"}


def test_a_sandbox_propose_with_no_groom_card_in_config_refuses(
        monkeypatch, tmp_path, capsys):
    """Without the card to leave out, the sandbox would groom its own groom
    card — refused, before the lane is read."""
    bad = tmp_path / "groom-sandbox.json"
    bad.write_text("{}")
    monkeypatch.setattr(groom_sandbox, "CONFIG", bad)
    patch(monkeypatch, the_lane())
    assert groomer.main(["propose", "--sandbox", "--card", SANDBOX_CARD,
                         "--no-judgement", "--no-verify"]) == 2
    assert "groom-sandbox.json" in capsys.readouterr().err


# --------------------------------------------------------------------------
# targets — the sandbox proposal judges its fixtures
# --------------------------------------------------------------------------
def _fixture_targets(tmp_path, *, sandbox):
    prop = groomer.propose(
        [card("DRE-101", repo=DEMO, days=1, title=FIXTURE),
         card("DRE-102", repo=DEMO, days=2)],
        cycles=[{"number": 13, "id": "cyc-13",
                 "startsAt": "2099-09-21T07:00:00.000Z",
                 "endsAt": "2099-10-05T07:00:00.000Z"}], capacity=3)
    if sandbox:
        prop["sandbox"] = True
    fixture = issue_node("DRE-101", title=FIXTURE,
                         labels=(groom_sandbox.REPO_LABEL, "agent:engineer"))
    lops = CardText(issues={"DRE-101": fixture})
    _, targets, _ = build_targets(tmp_path, prop, lops=lops)
    return {r["card"]: r for r in read(targets)}


def test_the_real_proposal_still_excludes_a_sandbox_fixture(tmp_path):
    rows = _fixture_targets(tmp_path, sandbox=False)
    assert rows["DRE-101"]["excluded"] == gva.EXCLUDE_SANDBOX_FIXTURE


def test_the_sandbox_proposal_judges_its_fixture(tmp_path):
    rows = _fixture_targets(tmp_path, sandbox=True)
    assert rows["DRE-101"]["excluded"] is None


def test_the_sandbox_still_applies_every_other_exclusion():
    epic = issue_node("DRE-101", title=FIXTURE,
                      labels=(groom_sandbox.REPO_LABEL,),
                      children=[("DRE-9", "Todo")])
    from datetime import datetime, timezone
    assert gva.exclusion(epic, now=datetime.now(timezone.utc),
                         sandbox=True) == "parent epic with 1 open child"


# --------------------------------------------------------------------------
# nobody drains a sandbox proposal
# --------------------------------------------------------------------------
class Untouchable:
    """`linear_ops` that records and refuses every call."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"unexpected linear_ops.{name}")
        return call


def test_a_drain_of_the_sandbox_groom_card_refuses_before_any_read_or_write():
    lops = Untouchable()
    with pytest.raises(groomer.SandboxNeverDrained) as caught:
        groomer.drain(lops, card=SANDBOX_CARD)
    assert lops.calls == []
    assert "sandbox" in str(caught.value) and "never drained" in str(caught.value)


def test_the_drain_cli_says_why_and_exits_2(monkeypatch, capsys):
    lops = Untouchable()
    monkeypatch.setattr(groomer, "linear_ops", lops)
    assert groomer.main(["drain", "--card", SANDBOX_CARD]) == 2
    assert lops.calls == []
    err = capsys.readouterr().err
    assert "never drained" in err and SANDBOX_CARD in err


# --------------------------------------------------------------------------
# the record
# --------------------------------------------------------------------------
def test_docs_say_in_one_paragraph_what_it_is_and_that_it_is_never_drained():
    text = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    paragraphs = [" ".join(p.split()) for p in text.split("\n\n")]
    found = [p for p in paragraphs
             if "sandbox groom proposal" in p and "never drained" in p]
    assert len(found) == 1, found
    for token in (SANDBOX_CARD, "config/groom-sandbox.json",
                  "repo:agent-bureau-demo", "DRE-7783"):
        assert token in found[0], token
