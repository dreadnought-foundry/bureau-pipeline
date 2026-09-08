"""A run says how many cards it ranked, and keeps the answer (DRE-3331).

`Ranked by claude-fable-5-1 (asked) / claude-fable-5-1 (answered) in 1 call
over 260 cards` was the whole receipt of run 34185093277, and 204 of the 260
were unranked. So the count rides everywhere the receipt does — the proposal
JSON, the page's first line, the `🧠 model-attempt:` comment — and the raw
answer is written beside `proposal.json` for the artifact, because the run that
found this kept nothing.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_accounting.py -v
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_context  # noqa: E402
import groom_judgement  # noqa: E402
import groomer  # noqa: E402
import groomer_receipt  # noqa: E402
import planning_classify  # noqa: E402

NOW = "2026-09-05T12:00:00Z"
PACK = groom_context.pack(now=NOW)
CYCLES = [{"number": 13, "id": "c13", "startsAt": "2026-09-08T00:00:00Z",
           "endsAt": "2026-09-22T00:00:00Z"}]
WORKFLOW = ROOT / ".github" / "workflows" / "groomer.yml"


def card(identifier, *, days=1):
    return {"identifier": identifier, "title": f"{identifier} does a thing",
            "description": "a body\nwith two lines", "priority": 0,
            "createdAt": "2026-09-04T12:00:00Z", "state": {"name": "Intake"},
            "labels": {"nodes": [{"name": "repo:portico"}]},
            "parent": None, "project": None, "cycle": None,
            "inverseRelations": {"nodes": []}}


class Call:
    def __init__(self, answer, continuations=0):
        self.answer, self.continuations = answer, continuations

    def __call__(self, model, prompt, *, max_tokens=None, timeout_seconds=None):
        return planning_classify.Answer(text=self.answer, model="test-model",
                                        continuations=self.continuations)


def judged(cards, answer, continuations=0):
    rows = groom_judgement.census(cards, now=NOW)
    return groom_judgement.run(rows, PACK, call=Call(answer, continuations),
                               model="test-model")


def line(identifier, outcome, reason, pointer=None):
    return " | ".join([identifier, outcome, reason] + ([pointer] if pointer else []))


# --------------------------------------------------------------------------- #
# the proposal carries the count                                               #
# --------------------------------------------------------------------------- #

def _four_cards_two_ranked():
    cards = [card(f"DRE-{n}") for n in range(1, 5)]
    answer = "\n".join([line("DRE-1", "now", "wanted"),
                        line("DRE-2", "unranked", "cannot tell"),
                        line("DRE-3", "not-now", "later", "the trigger")])
    return cards, judged(cards, answer)


def test_the_judgement_block_carries_the_ranked_count_and_the_accounting():
    cards, judgement = _four_cards_two_ranked()
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judgement)
    block = proposal["judgement"]
    assert block["ranked"] == 2
    assert block["accounting"] == {"ranked": 2, "declined": 1, "garbled": 0,
                                   "omitted": 1, "ceiling": 0}
    assert block["continuations"] == 0
    assert len(block["unranked"]) == 2, "the per-card list is unchanged"


def test_the_page_says_the_count_in_its_first_line():
    cards, judgement = _four_cards_two_ranked()
    text = groomer.render_proposal(groomer.propose(
        cards, cycles=CYCLES, capacity=5, now=NOW, judgement=judgement))
    receipt = next(l for l in text.splitlines() if l.startswith("Ranked by"))
    assert "2 of 4 cards ranked" in receipt
    assert "1 the model declined" in receipt
    assert "1 never reached" in receipt


def test_a_continued_answer_is_said_on_the_page():
    cards = [card("DRE-1"), card("DRE-2")]
    judgement = judged(cards, "\n".join([line("DRE-1", "now", "w"),
                                         line("DRE-2", "now", "w")]),
                       continuations=2)
    text = groomer.render_proposal(groomer.propose(
        cards, cycles=CYCLES, capacity=5, now=NOW, judgement=judgement))
    receipt = next(l for l in text.splitlines() if l.startswith("Ranked by"))
    assert "3 pieces" in receipt
    assert "token" not in text.lower(), "a joined answer is not a cut one"


def test_a_proposal_from_before_this_card_invents_no_count():
    cards, judgement = _four_cards_two_ranked()
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judgement)
    for key in ("ranked", "accounting", "continuations"):
        proposal["judgement"].pop(key)
    text = groomer.render_proposal(proposal)
    assert "ranked" not in text.split("Ranked by", 1)[1].split("\n", 1)[0].lower()
    assert "pieces" not in text


def test_the_rules_only_page_says_nothing_about_a_count():
    cards = [card("DRE-1")]
    text = groomer.render_proposal(
        groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW))
    assert "cards ranked" not in text


# --------------------------------------------------------------------------- #
# the receipt comment                                                          #
# --------------------------------------------------------------------------- #

def test_the_receipt_comment_carries_the_count():
    cards, judgement = _four_cards_two_ranked()
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judgement)
    receipt = groomer_receipt.receipt_line(proposal)
    assert "ranked the census in 1 call — 2 of 4 cards ranked" in receipt


def test_the_receipt_comment_without_the_key_reads_as_before():
    cards, judgement = _four_cards_two_ranked()
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judgement)
    proposal["judgement"].pop("ranked")
    assert "cards ranked" not in groomer_receipt.receipt_line(proposal)


# --------------------------------------------------------------------------- #
# the answer is kept beside the proposal                                       #
# --------------------------------------------------------------------------- #

def _build(monkeypatch, cards, judgement, **extra):
    monkeypatch.setattr(groomer, "read_population", lambda lops, lane: cards)
    monkeypatch.setattr(groomer, "read_cycles", lambda lops: CYCLES)
    monkeypatch.setattr(groom_context, "read_pack", lambda lops: PACK)
    monkeypatch.setattr(groom_judgement, "run", lambda rows, pack: judgement)
    args = dict(lane="Intake", capacity=10, batch_cycles=1, judgement=True,
                window_days=groomer.WINDOW_DAYS,
                priority=",".join(groomer.REPO_PRIORITY), keep_answer=None)
    args.update(extra)
    return groomer._build(argparse.Namespace(**args))


def test_build_writes_the_raw_answer_where_it_is_told(monkeypatch, tmp_path, capsys):
    cards, judgement = _four_cards_two_ranked()
    target = tmp_path / "judgement-answer.txt"
    _build(monkeypatch, cards, judgement, keep_answer=str(target))
    assert target.read_text(encoding="utf-8") == judgement.answer
    assert "judgement-answer.txt" in capsys.readouterr().err


def test_build_writes_nothing_when_no_answer_came_back(monkeypatch, tmp_path):
    cards = [card("DRE-1")]
    rows = groom_judgement.census(cards, now=NOW)

    def dead(model, prompt, *, max_tokens=None, timeout_seconds=None):
        raise RuntimeError("no transport")

    judgement = groom_judgement.run(rows, PACK, call=dead, model="m")
    target = tmp_path / "judgement-answer.txt"
    _build(monkeypatch, cards, judgement, keep_answer=str(target))
    assert not target.exists(), "no answer, no file — the artifact step ignores absence"


def test_build_logs_the_ranked_count(monkeypatch, capsys):
    cards, judgement = _four_cards_two_ranked()
    _build(monkeypatch, cards, judgement)
    assert "ranked 2 of 4" in capsys.readouterr().err


def test_the_cli_documents_the_switch():
    assert "--keep-answer" in (groomer.__doc__ or "")


# --------------------------------------------------------------------------- #
# the workflow keeps it, and its clock covers the wider call                   #
# --------------------------------------------------------------------------- #

def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step(name):
    job = _workflow()["jobs"]["groom"]
    return next(s for s in job["steps"] if s.get("name") == name)


def test_the_groom_step_keeps_the_answer_and_the_artifact_carries_it():
    assert "--keep-answer judgement-answer.txt" in _step("Groom")["run"]
    paths = _step("Keep the proposal")["with"]["path"]
    assert "judgement-answer.txt" in paths
    assert "proposal.json" in paths


def test_the_job_clock_covers_the_widest_call():
    job = _workflow()["jobs"]["groom"]
    assert int(job["timeout-minutes"]) * 60 >= \
        groom_judgement.MAX_WALL_CLOCK_SECONDS + 300
