"""RED-first: the groomer computes, the verify matrix checks, and then it posts
(DRE-4971).

`propose` used to compute a proposal and post it in one invocation. The verify
matrix (DRE-4970) has to run between those two acts, in different jobs, so they
are split:

  * `propose --card DRE-N --out proposal.json` reads the standing card's thread
    exactly as `--post DRE-N` does — an earlier `groom-excluded` is honored, a
    decline is answered — writes the record, and posts NOTHING.
  * `post --card DRE-N --proposal proposal-verified.json [--dry-run]` reads the
    thread and posts the record through `post_proposal`, so the idempotence
    (`already_proposed`) and the empty-proposal refusal are the ones that
    always held.

And the page renders what the verify step found and spent, under
`## Verified against main`, after the Cancel table — while the drain still
reads the Cancel table back off the posted comment, never the artifact.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_post.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import groom_verify_agent as gva  # noqa: E402
import groomer  # noqa: E402

from test_groomer import CYCLES, GOLDEN, NOW, card  # noqa: E402
from test_groomer_batch_reasons import GOLDEN_RENDER  # noqa: E402
from test_groomer_render import section  # noqa: E402
from test_groomer_retry import FakeOps as RetryOps  # noqa: E402

PROPOSAL_CARD = "DRE-2683"
PROOF = {"file": "src/legacy_migration_lib.ts", "line": 886,
         "quote": "export function migrateRoster(portal) {"}
PROOF_TEXT = "src/legacy_migration_lib.ts:886"


class FakeOps(RetryOps):
    """The retry test's thread — it remembers what was posted — which also
    remembers whether the whole thread was asked for."""

    def __init__(self, comments=None):
        super().__init__(comments)
        self.whole_thread_asked: list[bool] = []

    def comment_records(self, identifier, *, whole_thread=False):
        self.whole_thread_asked.append(bool(whole_thread))
        return super().comment_records(identifier, whole_thread=whole_thread)


@pytest.fixture
def ops(monkeypatch):
    fake = FakeOps()
    monkeypatch.setattr(groomer, "linear_ops", fake)
    return fake


def _lane_proposal():
    """What `propose` builds off `FakeOps`' lane — the same six cards."""
    cards = [card(f"DRE-{n:03d}") for n in range(6)]
    return groomer.propose(cards, cycles=CYCLES, capacity=3, batch_cycles=1)


def _planning(proposal):
    return [r["identifier"] for r in sorted(proposal["outcomes"]["now"],
                                            key=lambda r: r["position"])]


def _everywhere(proposal):
    return [r["identifier"] for r in proposal["sequence"]]


def _posted(proposal):
    return {"body": groomer.proposal_comment(proposal),
            "authored_by_pipeline": True}


def _ceo(tag, pid, *, card_id=None, reason=None):
    return {"body": groomer.decision_comment(tag, pid, card=card_id,
                                             reason=reason),
            "authored_by_pipeline": False}


PROPOSE = ["propose", "--lane", "Intake", "--capacity", "3",
           "--no-judgement", "--no-verify"]


# --------------------------------------------------------------------------
# propose --card: the thread is read, nothing is posted
# --------------------------------------------------------------------------
def test_propose_card_reads_the_thread_writes_the_file_and_posts_nothing(
        ops, tmp_path):
    earlier = _lane_proposal()
    excluded = _planning(earlier)[0]
    ops.comments = [_posted(earlier),
                    _ceo(groomer.EXCLUDE_TAG, earlier["id"], card_id=excluded)]
    out = tmp_path / "f.json"

    assert groomer.main([*PROPOSE, "--card", PROPOSAL_CARD,
                         "--out", str(out)]) == 0

    assert ops.posted == [], "`propose --card` posted to the card"
    assert ops.whole_thread_asked and ops.whole_thread_asked[0], (
        "the standing decisions can be the oldest comment on the card — the "
        "whole thread has to be read"
    )
    written = json.loads(out.read_text(encoding="utf-8"))
    assert excluded not in _everywhere(written), (
        "an earlier `groom-excluded` was not honored — the thread was not read"
    )
    assert written["excluded_before"] == [excluded]
    assert written["id"] == groomer.proposal_id(written)


def test_propose_card_answers_a_decline_on_the_thread(ops, tmp_path):
    earlier = _lane_proposal()
    ops.comments = [_posted(earlier),
                    _ceo(groomer.DECLINE_TAG, earlier["id"],
                         reason="two of these cards edit one file")]
    out = tmp_path / "f.json"
    assert groomer.main([*PROPOSE, "--card", PROPOSAL_CARD,
                         "--out", str(out)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["answering"]["id"] == earlier["id"]
    assert ops.posted == []


def test_propose_post_keeps_its_meaning(ops):
    assert groomer.main([*PROPOSE, "--post", PROPOSAL_CARD]) == 0
    assert len(ops.posted) == 1


def test_card_and_post_together_are_refused(ops, capsys):
    with pytest.raises(SystemExit) as refused:
        groomer.main([*PROPOSE, "--card", PROPOSAL_CARD,
                      "--post", PROPOSAL_CARD])
    assert refused.value.code == 2
    assert ops.posted == []


# --------------------------------------------------------------------------
# post --card --proposal: one comment, once
# --------------------------------------------------------------------------
def _record_file(tmp_path, proposal, name="proposal-verified.json"):
    path = tmp_path / name
    path.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    return path


def test_post_posts_exactly_one_proposal_and_a_rerun_posts_nothing(
        ops, tmp_path, capsys):
    record = _lane_proposal()
    path = _record_file(tmp_path, record)
    argv = ["post", "--card", PROPOSAL_CARD, "--proposal", str(path)]

    assert groomer.main(argv) == 0
    assert len(ops.posted) == 1
    target, body = ops.posted[0]
    assert target == PROPOSAL_CARD
    assert body.splitlines()[0] == (
        f"{groomer.MARK} {groomer.PROPOSAL_TAG}: {record['id']}")
    assert groomer.title_line(record) in capsys.readouterr().out, (
        "`post` prints the page, for the step summary"
    )

    assert groomer.main(argv) == 0
    assert len(ops.posted) == 1, "a re-run posted a second copy"


def test_post_dry_run_posts_nothing_and_prints_the_page(ops, tmp_path, capsys):
    record = _lane_proposal()
    path = _record_file(tmp_path, record)
    assert groomer.main(["post", "--card", PROPOSAL_CARD, "--proposal",
                         str(path), "--dry-run"]) == 0
    assert ops.posted == []
    out = capsys.readouterr().out
    assert "dry run — nothing posted" in out
    assert groomer.render_proposal(record) in out
    assert ops.whole_thread_asked, "a dry run reads the thread a real one does"


def test_post_refuses_an_empty_record(ops, tmp_path):
    record = groomer.propose([], cycles=CYCLES, capacity=3)
    path = _record_file(tmp_path, record)
    assert groomer.main(["post", "--card", PROPOSAL_CARD,
                         "--proposal", str(path)]) == 0
    assert ops.posted == []


def test_post_answers_the_decline_standing_on_the_thread(ops, tmp_path):
    earlier = _lane_proposal()
    record = groomer.propose(
        [card(f"DRE-{n:03d}") for n in range(1, 7)], cycles=CYCLES,
        capacity=3, batch_cycles=1)
    assert record["id"] != earlier["id"]
    ops.comments = [_posted(earlier),
                    _ceo(groomer.DECLINE_TAG, earlier["id"],
                         reason="two of these cards edit one file")]
    path = _record_file(tmp_path, record)
    assert groomer.main(["post", "--card", PROPOSAL_CARD,
                         "--proposal", str(path)]) == 0
    assert len(ops.posted) == 1
    assert f"Answering your decline of `{earlier['id']}`" in ops.posted[0][1]


# --------------------------------------------------------------------------
# the page renders the verify step
# --------------------------------------------------------------------------
def _verified():
    """A record `groom_verify_agent.apply` wrote: DRE-102 proved obsolete with
    a file:line, DRE-103 never answered, DRE-104 takes the emptied slot."""
    cards = [card(f"DRE-{100 + n}", days=n) for n in range(1, 8)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=3, now=NOW)
    assert _planning(proposal) == ["DRE-101", "DRE-102", "DRE-103"]

    def doc(identifier, verdict, cost, start, finish, **extra):
        return {"card": identifier, "verdict": verdict,
                "summary": extra.get("summary", "still to build"),
                "proof": extra.get("proof", [PROOF]), "reason": None,
                "cost_usd": cost, "duration_ms": 60000,
                "model": "claude-sonnet-5",
                "started_at": start, "finished_at": finish}

    found = {
        "DRE-101": doc("DRE-101", "still-needed", 0.5,
                       "2026-09-27T13:00:00Z", "2026-09-27T13:02:00Z"),
        "DRE-102": doc("DRE-102", "obsolete", 0.25,
                       "2026-09-27T13:00:30Z", "2026-09-27T13:05:07Z",
                       summary="The roster migration already reads the "
                               "portal directly."),
        "DRE-104": doc("DRE-104", "still-needed", 1.0,
                       "2026-09-27T13:01:00Z", "2026-09-27T13:03:00Z"),
    }
    rows = [{"card": c, "list": "planning" if c <= "DRE-103" else "spare"}
            for c in ("DRE-101", "DRE-102", "DRE-103", "DRE-104")]
    return gva.apply(proposal, found, rows, repo_map={})


def test_the_verified_record_is_the_shape_this_card_renders():
    record = _verified()
    assert record["verify"]["cards"] == 4
    assert record["verify"]["counts"] == {
        **{v: 0 for v in gva.VERDICTS}, "still-needed": 2, "obsolete": 1,
        "unverified": 1}
    assert list(record["verify"]["counts"]) == list(gva.VERDICTS)
    assert record["verify"]["unverified"] == ["DRE-103"]
    assert record["verify"]["wall_clock_seconds"] == 307
    dead = record["outcomes"]["dead"]
    assert [(r["identifier"], r["source"]) for r in dead] == [
        ("DRE-102", gva.CANCEL_SOURCE)]


def test_the_page_carries_the_verified_section_after_the_cancel_table():
    record = _verified()
    text = groomer.render_proposal(record)
    assert groomer.VERIFIED_HEADING == "## Verified against main"
    assert groomer.VERIFIED_HEADING in text
    assert (text.index(groomer.CANCEL_HEADING)
            < text.index(groomer.VERIFIED_HEADING)), (
        "the verify block renders after the Cancel table"
    )
    block = section(text, groomer.VERIFIED_HEADING)
    assert "Verify step: 4 cards, $1.75, 5 min 7 s wall clock" in block
    for verdict, count in {**{v: 0 for v in gva.VERDICTS},
                           "still-needed": 2, "obsolete": 1,
                           "unverified": 1}.items():
        assert f"- {verdict}: {count}" in block, (
            f"no count line for {verdict}")
    assert "DRE-103 — no verdict artifact" in block, (
        "an unverified card is listed by id with its reason"
    )


def test_a_verify_agent_cancel_row_carries_its_file_line_proof_whole():
    record = _verified()
    text = groomer.render_proposal(record)
    rows = [line for line in section(text, groomer.CANCEL_HEADING).splitlines()
            if line.startswith("| 1 | DRE-102 |")]
    assert len(rows) == 1
    reason = record["outcomes"]["dead"][0]["reason"]
    assert PROOF_TEXT in reason
    assert rows[0].endswith(f"| {groomer._whole_cell(reason)} |"), (
        "the Reason cell is the verify step's reason, uncut"
    )
    assert PROOF_TEXT in rows[0]


def test_the_drain_reads_the_verify_agent_cancel_row_back():
    record = _verified()
    parsed = groomer.parse_proposal_comment(groomer.proposal_comment(record))
    dead = record["outcomes"]["dead"][0]
    assert parsed["id"] == record["id"]
    assert parsed["cancel"] == [{
        "identifier": "DRE-102", "position": dead["position"],
        "repo": dead["repo"], "reason": dead["reason"]}]
    assert [r["identifier"] for r in parsed["batch"]] == _planning(record)


def test_a_planning_row_renders_its_verdict_in_the_batch_reasons():
    record = _verified()
    text = groomer.render_proposal(record)
    assert groomer.BATCH_REASONS_HEADING in text, (
        "a verified batch carries its verdicts even on a rules-only read"
    )
    block = section(text, groomer.BATCH_REASONS_HEADING)
    per_card = block.split("### ")
    for identifier in ("DRE-101", "DRE-104"):
        [entry] = [e for e in per_card if e.startswith(identifier)]
        assert "still-needed" in entry, f"{identifier}'s verdict is not shown"
    [entry] = [e for e in per_card if e.startswith("DRE-103")]
    assert "unverified" in entry


def test_a_record_with_no_verify_block_renders_byte_for_byte_as_today():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    built = groomer.propose(
        golden["cards"], cycles=golden["cycles"], capacity=golden["capacity"],
        batch_cycles=golden["batch_cycles"], now=golden["now"], judgement=None)
    assert "verify" not in built
    text = groomer.render_proposal(built)
    assert groomer.VERIFIED_HEADING not in text
    assert text == GOLDEN_RENDER.read_text(encoding="utf-8")


def test_an_unknown_cost_or_clock_is_said_not_zeroed():
    record = _verified()
    record["verify"].update(cost_usd=None, wall_clock_seconds=None)
    block = section(groomer.render_proposal(record), groomer.VERIFIED_HEADING)
    assert "$0.00" not in block and "0 min 0 s" not in block
    assert "unknown" in block


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------
def test_the_doc_says_compute_verify_then_post():
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    assert "compute, verify, then post" in doc
    assert "propose --card" in doc
    assert "groomer.py post --card" in doc
    assert "## Verified against main" in doc
    [cost] = [p for p in doc.split("\n\n") if "What the run cost" in p]
    assert "Verified against main" in cost, (
        "the `What the run cost` sentence does not say where the verify "
        "step's cost is recorded"
    )
