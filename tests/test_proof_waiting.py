"""RED-first: the 🔬 proof-waiting act, and its two one-line writers (DRE-3275).

A proof card holds when the thing it exists to observe has not happened yet —
"a failing agent PR in a console repo" needs one to EXIST before anyone can
watch the pipeline handle it. Today that hold is prose somebody types by hand
(DRE-3135, 2026-09-06) and nothing reads it: it is not an act, so it carries no
trailer, and the console renders the card as an ordinary silent card.

This card declares the act and gives it a writer, so the hold is one command
and the console's `receipts.py` — which already carries the tag, console-first
per `docs/pipeline-acts.md` — renders `Waiting for proof — <reason>`.

TWO LINES, ONE ROW. `🔬 proof-waiting` is the act. `🔬 proof-observed` is NOT a
second act: it is the DISCHARGE RECORD, a plain comment, the same shape a
critic verdict has against a re-dispatch. Stamping the hold's trailer on the
discharge would make the console read the observation as a fresh hold — so the
discharge composes nothing, and says so in the registry's `unconverted` block
where the debt is countable rather than invisible.

WHY BOTH WRITERS REFUSE TWO PHRASES. The console's `enrich.HOLD_MARKERS` reads
`budget exhausted` and `holding for a human` as a FIX-BUDGET hold. A proof hold
whose reason happens to contain either sentence would be rendered as the wrong
kind of stuck card, on a surface nobody would think to check — so the refusal
is at the writer, where it costs a caller one reword.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_waiting.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import check_act_receipts  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402

ACT = "proof-observation-pending"
TAG = "proof-waiting"

# The card's own example, verbatim — the one line this whole card exists to
# post, so it is pinned as a string and not assembled from the code under test.
OBSERVED = "a failing agent PR in a console repo"
NEEDS = "one open agent/* PR with a red check"
EXPECTED_LINE = (
    "🔬 proof-waiting: a failing agent PR in a console repo — "
    "needs one open agent/* PR with a red check"
)


# ── the registry row ───────────────────────────────────────────────────────
def test_the_registry_declares_the_act():
    """Tag, name and kind are the CONTRACT shared with the console's `ACTS`
    entry — the three fields `check_act_consumers.py` compares across repos."""
    assert ACT in pipeline_act.acts()
    assert pipeline_act.tag(ACT) == TAG
    assert pipeline_act.kind(ACT) == "hold"


def test_the_row_holds_the_card_for_a_person():
    """A hold stops the work and hands it to somebody: nothing else is coming
    until a person makes the observation."""
    assert pipeline_act.state(ACT) == "held"
    assert pipeline_act.next_actor(ACT) == "operator"


def test_proof_observed_is_not_a_row():
    """The discharge record is prose in this row's reasoning, never an act of
    its own — a row would give the observation a trailer of its own, and the
    console would read the discharge as a second hold."""
    assert "proof-observed" not in pipeline_act.acts()
    assert "proof-observed" not in [pipeline_act.tag(a) for a in pipeline_act.acts()]
    # …and the row that IS declared names it, so the discharge is documented
    # where the act is rather than in somebody's memory.
    assert "proof-observed" in pipeline_act.record(ACT)["why"]
    # It discharges no PRIOR act — it is the start of an obligation, not the
    # end of one — and says so with null rather than by omission.
    assert pipeline_act.discharges(ACT) is None


def test_the_registry_still_checks_clean():
    """`pipeline_act.py check` binds the row to the code that emits it: the
    adopted tag must really be in `linear_ops.py`, the anchor must be there
    exactly once, and no name may carry another act's live key."""
    assert pipeline_act.problems() == []


# ── the two lines ──────────────────────────────────────────────────────────
def test_the_waiting_line_is_the_contract_grammar():
    """Byte-for-byte, the line the sibling console card parses."""
    assert linear_ops.proof_waiting_line(OBSERVED, NEEDS) == EXPECTED_LINE


def test_the_observed_line_is_the_contract_grammar():
    assert linear_ops.proof_observed_line(
        "the pipeline held PR #17 at 09:20 PT"
    ) == "🔬 proof-observed: the pipeline held PR #17 at 09:20 PT"


# ── what gets posted ───────────────────────────────────────────────────────
def _posted(fn, *args):
    """Run a writer with the network stubbed; return the body it posted."""
    with patch.object(linear_ops, "cmd_comment") as comment:
        fn(*args)
    assert comment.call_count == 1, "exactly one comment per command"
    assert comment.call_args.args[0] == "DRE-3275"
    return comment.call_args.args[1]


def test_proof_waiting_posts_the_line_then_the_trailer():
    """The acceptance criterion, over the composed body and no network: the
    line EXACTLY as specified, then the act's own trailer riding along.

    MUTATION CHECK: post the bare line instead of composing it and the trailer
    assertions go red; reword the line and the first one does.
    """
    body = _posted(linear_ops.cmd_proof_waiting, "DRE-3275", OBSERVED, NEEDS)
    assert body.startswith(EXPECTED_LINE)
    assert f"📎 pipeline-act: {ACT}" in body
    # …and it is the real writer's trailer, not a hand-built lookalike.
    assert body == pipeline_act.receipt(ACT, EXPECTED_LINE)
    assert (pipeline_act.read_trailer(body) or {}).get("tag") == TAG


def test_proof_observed_posts_a_plain_comment():
    """The discharge carries NO trailer. A trailer here would be the hold's own
    key on the comment that ends the hold, and the console would read the
    observation as a fresh `proof-waiting` row.
    """
    body = _posted(
        linear_ops.cmd_proof_observed, "DRE-3275", "the run held at 09:20 PT"
    )
    assert body == "🔬 proof-observed: the run held at 09:20 PT"
    assert pipeline_act.read_trailer(body) is None
    assert TAG not in body


# ── the refusals ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("args", "why"),
    [
        (("DRE-3275", "", NEEDS), "nothing to observe"),
        (("DRE-3275", "   ", NEEDS), "whitespace is not a reason"),
        (("DRE-3275", OBSERVED, ""), "no way for it to be observed"),
        (("DRE-3275", OBSERVED, "  "), "whitespace is not a way either"),
    ],
)
def test_proof_waiting_refuses_an_empty_half(args, why):
    """A hold that says what it waits for but not what would end it is a hold
    nobody can discharge — and half a line still burns the act's key."""
    with patch.object(linear_ops, "cmd_comment") as comment:
        with pytest.raises(linear_ops.LinearError):
            linear_ops.cmd_proof_waiting(*args)
    comment.assert_not_called(), why


def test_proof_observed_refuses_an_empty_text():
    with patch.object(linear_ops, "cmd_comment") as comment:
        with pytest.raises(linear_ops.LinearError):
            linear_ops.cmd_proof_observed("DRE-3275", "  ")
    comment.assert_not_called()


@pytest.mark.parametrize("phrase", ["budget exhausted", "holding for a human"])
@pytest.mark.parametrize("half", [0, 1])
def test_proof_waiting_refuses_a_console_hold_marker(phrase, half):
    """Either half. The console reads these two phrases anywhere in the card's
    comments as a FIX-BUDGET hold, so a proof hold carrying one renders as the
    wrong kind of stuck card — and the reason is named, because a refusal a
    caller cannot act on is a refusal they work around.
    """
    halves = [OBSERVED, NEEDS]
    halves[half] = f"{halves[half]} while the {phrase} note stands"
    with patch.object(linear_ops, "cmd_comment") as comment:
        with pytest.raises(linear_ops.LinearError) as caught:
            linear_ops.cmd_proof_waiting("DRE-3275", *halves)
    comment.assert_not_called()
    assert phrase in str(caught.value)
    assert "hold" in str(caught.value).lower()


@pytest.mark.parametrize("phrase", ["budget exhausted", "holding for a human"])
def test_proof_observed_refuses_a_console_hold_marker(phrase):
    with patch.object(linear_ops, "cmd_comment") as comment:
        with pytest.raises(linear_ops.LinearError) as caught:
            linear_ops.cmd_proof_observed("DRE-3275", f"seen — no {phrase} here")
    comment.assert_not_called()
    assert phrase in str(caught.value)


def test_the_markers_are_matched_whatever_the_casing():
    """The phrase is what the console reads, and a card is written by people:
    `Budget Exhausted` is the same hold to a reader and must be the same
    refusal here."""
    with pytest.raises(linear_ops.LinearError):
        linear_ops.cmd_proof_waiting("DRE-3275", "Budget Exhausted", NEEDS)


# ── the CLI seam ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("command", "fn"),
    [("proof-waiting", "cmd_proof_waiting"), ("proof-observed", "cmd_proof_observed")],
)
def test_both_commands_are_reachable_from_the_command_line(command, fn):
    """The card's contract is a CLI: `python3 scripts/linear_ops.py
    proof-waiting <CARD> "<observed>" "<needs>"`. A helper nothing can invoke
    is a helper the proof runner cannot use."""
    source = (ROOT / "scripts" / "linear_ops.py").read_text(encoding="utf-8")
    assert f'"{command}": linear_ops.{fn}' in source or f'"{command}": {fn}' in source


# ── the guards that must stay green ────────────────────────────────────────
def test_the_hold_composes_through_the_one_receipt_writer():
    """`check_act_receipts.py` reads every comment-writing call in `scripts/`.
    The hold must be one of the COMPOSED ones — not an `unconverted` row —
    because it is an act and its trailer is what the console reads.
    """
    composing = [
        s for s in check_act_receipts.python_sites()
        if s.path.endswith("linear_ops.py") and s.composed_as == ACT
    ]
    assert len(composing) == 1, "the proof hold posts exactly one composed receipt"


def test_the_discharge_is_declared_rather_than_left_silent():
    """The other half of the same guard: the plain comment is a receipt site
    too, and a site that neither composes nor is declared fails CI. It is
    declared `not-an-act` — a record, which creates no obligation and hands the
    work to nobody."""
    rows = [
        d for d in check_act_receipts.declarations()
        if d.get("file") == "scripts/linear_ops.py"
        and "proof_observed" in (d.get("anchor") or "")
    ]
    assert len(rows) == 1
    assert rows[0]["kind"] == "not-an-act"
    assert rows[0]["why"].strip()


def test_the_receipt_guard_is_clean():
    assert check_act_receipts.problems() == []


# ── the record a human reads ───────────────────────────────────────────────
def test_the_doc_carries_the_row():
    text = (ROOT / "docs" / "pipeline-acts.md").read_text(encoding="utf-8")
    for expected in (TAG, ACT, "proof-observed", "hold", "linear_ops.py"):
        assert expected in text
    assert EXPECTED_LINE.split(":")[0] in text  # the 🔬 mark itself


def test_the_registry_is_still_valid_json_the_sweep_can_read():
    """`reconcile.py` imports the reader on every product repo's sweep, where
    there is no pip install — so the row lands in JSON, parsed by stdlib."""
    with open(ROOT / "config" / "pipeline-acts.json", encoding="utf-8") as fh:
        doc = json.load(fh)
    assert any(a["name"] == ACT for a in doc["acts"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
