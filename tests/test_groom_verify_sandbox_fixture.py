"""RED-first: the groomer leaves a proof run's sandbox fixture card where it
is in Intake (DRE-7783).

Once proof runs make their own presses on the sandbox (DRE-7772), a proof that
files a card from a task leaves one card in the sandbox's Intake, labeled
`repo:agent-bureau-demo` and titled `SANDBOX FIXTURE (proof DRE-<n>): …`. The
console has no delete for a filed card, so it stays there as the proof
record's residue. Held here:

  * a card with BOTH the title opener and the label is excluded without
    judgement as `sandbox fixture filed by a proof run`, through `targets`,
    `verdict` and `apply`, so it leaves the batch and stays on the board;
  * a card with only one of the two is judged as it was;
  * the open-children reason still wins over it, and it wins over the hand
    marks;
  * `exclusion --card <ID>` prints the one line the PROOF quotes, and writes
    nothing.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_verify_sandbox_fixture.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_verify_agent as gva  # noqa: E402
import groomer  # noqa: E402

from test_groom_verify import planning  # noqa: E402
from test_groom_verify_agent import (  # noqa: E402
    PROOF_LINE, CardText, build_targets, issue_node, proposal, raw_answer,
    read, run_apply, run_verdict)

REASON = "sandbox fixture filed by a proof run"
FIXTURE_TITLE = "SANDBOX FIXTURE (proof DRE-9001): filed by the run"
DEMO = "repo:agent-bureau-demo"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "app-installation-token")


def fixture_card(ident="DRE-101", *, title=FIXTURE_TITLE,
                 labels=(DEMO, "agent:engineer"), **fields):
    return issue_node(ident, title=title, labels=labels, **fields)


def excluded(issue):
    return gva.exclusion(issue, now=datetime.now(timezone.utc))


# --------------------------------------------------------------------------
# the contract shared with DRE-7776 and DRE-7778
# --------------------------------------------------------------------------
def test_the_contract_constants():
    assert gva.SANDBOX_FIXTURE_TITLE_OPENER == "SANDBOX FIXTURE (proof DRE-"
    assert gva.SANDBOX_FIXTURE_REPO_LABEL == "repo:agent-bureau-demo"
    assert gva.EXCLUDE_SANDBOX_FIXTURE == REASON


def test_the_module_docstring_names_the_exclusion_with_dre_7772():
    flat = " ".join(gva.__doc__.split())
    assert "sandbox fixture" in flat and "DRE-7772" in flat


# --------------------------------------------------------------------------
# `exclusion` — both marks, or the card is judged as today
# --------------------------------------------------------------------------
def test_a_fixture_card_is_excluded_as_a_sandbox_fixture():
    assert excluded(fixture_card()) == REASON


def test_the_label_is_compared_lowercased_as_the_hand_marks_are():
    assert excluded(fixture_card(labels=("Repo:Agent-Bureau-Demo",))) == REASON


@pytest.mark.parametrize("fields", [
    # The title alone: a fixture opener on another repo's card.
    dict(title=FIXTURE_TITLE, labels=("repo:agent-bureau", "agent:engineer")),
    # The label alone: a real card for the sandbox project.
    dict(title="Make the demo page faster", labels=(DEMO, "agent:engineer")),
    # The opener somewhere other than the start of the title.
    dict(title=f"Re: {FIXTURE_TITLE}", labels=(DEMO,)),
])
def test_a_card_with_only_one_of_the_two_marks_is_not_excluded(fields):
    assert excluded(fixture_card(**fields)) is None


def test_a_fixture_that_is_an_epic_with_an_open_child_keeps_that_reason():
    issue = fixture_card(children=[("DRE-150", "In Progress")])
    assert excluded(issue) == "parent epic with 1 open child"


def test_the_fixture_rule_is_read_before_the_hand_marks():
    issue = fixture_card(labels=(DEMO, "hand-built"))
    assert excluded(issue) == REASON


# --------------------------------------------------------------------------
# the reason's exact shape
# --------------------------------------------------------------------------
def test_is_exclusion_accepts_the_reason_exactly():
    assert gva.is_exclusion(REASON)
    assert gva._EXCLUSION_RE.fullmatch(REASON)


@pytest.mark.parametrize("bad", [
    f"{REASON} today", f"{REASON}s", f" {REASON}", REASON.upper(),
    "sandbox fixture filed by a proof"])
def test_is_exclusion_refuses_any_other_shape(bad):
    assert not gva.is_exclusion(bad)


# --------------------------------------------------------------------------
# through the job: `targets` excludes it, and `apply` drops it
# --------------------------------------------------------------------------
def test_targets_excludes_the_fixture_and_apply_drops_it_from_the_batch(tmp_path):
    prop = proposal()
    assert planning(prop) == ["DRE-101", "DRE-102", "DRE-103"]
    lops = CardText(issues={"DRE-101": fixture_card()})
    pfile, targets, matrix = build_targets(tmp_path, prop, lops=lops)

    rows = {r["card"]: r for r in read(targets)}
    assert rows["DRE-101"]["excluded"] == REASON
    assert rows["DRE-102"]["excluded"] is None
    assert {"card": "DRE-101", "repository": ""} in read(matrix)

    # The agent's answer, whatever it is, cannot judge an excluded card.
    got = run_verdict(tmp_path, "DRE-101", targets, raw=raw_answer(
        tmp_path, "DRE-101", "obsolete", [PROOF_LINE]))
    assert (got["verdict"], got["reason"]) == ("excluded", REASON)
    for other in ("DRE-102", "DRE-103", "DRE-104"):
        run_verdict(tmp_path, other, targets, raw=raw_answer(
            tmp_path, other, "still-needed", [PROOF_LINE]))

    after = run_apply(tmp_path, pfile, tmp_path / "verdicts")

    assert planning(after) == ["DRE-102", "DRE-103", "DRE-104"]
    assert after["verify"]["excluded"] == [
        {"identifier": "DRE-101", "reason": REASON}]
    waiting = {r["identifier"]: r for r in after["outcomes"]["not-now"]}
    assert waiting["DRE-101"]["reason"] == \
        f"excluded without judgement: {REASON}"
    text = groomer.render_proposal(after)
    block = text[text.index(groomer.VERIFIED_HEADING):]
    assert f"- DRE-101 — {REASON}" in block


# --------------------------------------------------------------------------
# `exclusion --card <ID>` — the one line the PROOF quotes
# --------------------------------------------------------------------------
class NoWrites:
    """`linear_ops` that refuses every call: with `_read_card` stubbed, the
    subcommand must not reach Linear for anything else."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"unexpected linear_ops.{name}")
        return call


@pytest.mark.parametrize("issue, line", [
    (fixture_card("DRE-9001"), f"exclusion: DRE-9001 {REASON}"),
    (fixture_card("DRE-9001", title="Make the demo page faster"),
     "exclusion: DRE-9001 none"),
])
def test_the_subcommand_prints_one_line_and_writes_nothing(
        monkeypatch, capsys, issue, line):
    read_ = []
    lops = NoWrites()

    def stub(got_lops, identifier):
        assert got_lops is lops
        read_.append(identifier)
        return issue

    monkeypatch.setattr(gva, "_read_card", stub)
    assert gva.main(["exclusion", "--card", "DRE-9001"], lops=lops) == 0
    out, err = capsys.readouterr()
    assert out == line + "\n"
    assert err == ""
    assert read_ == ["DRE-9001"]
    assert lops.calls == []


def test_the_subcommand_reads_the_card_with_queries_only(capsys):
    class Recording(CardText):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.queries = []

        def gql(self, query, variables=None):
            self.queries.append(query)
            return super().gql(query, variables)

    lops = Recording(issues={"DRE-9001": fixture_card("DRE-9001")})
    assert gva.main(["exclusion", "--card", "DRE-9001"], lops=lops) == 0
    assert capsys.readouterr().out == f"exclusion: DRE-9001 {REASON}\n"
    assert lops.queries and all(
        not q.lstrip().startswith("mutation") for q in lops.queries)


def test_the_subcommand_exits_1_with_one_stderr_line_when_the_read_raises(
        monkeypatch, capsys):
    def stub(lops, identifier):
        raise RuntimeError("Linear said 429:\nrate limited")

    monkeypatch.setattr(gva, "_read_card", stub)
    assert gva.main(["exclusion", "--card", "DRE-9001"], lops=NoWrites()) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.endswith("\n") and err.count("\n") == 1
    assert "DRE-9001" in err and "rate limited" in err


# --------------------------------------------------------------------------
# the doc
# --------------------------------------------------------------------------
def test_the_doc_names_the_reason_word_for_word():
    text = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    start = text.index("**What verify excludes without judging (DRE-5306).**")
    para = " ".join(text[start:text.index("\n\n", start)].split())
    assert f"`{REASON}`" in para
    assert "`SANDBOX FIXTURE (proof DRE-`" in para
    assert "`repo:agent-bureau-demo`" in para
