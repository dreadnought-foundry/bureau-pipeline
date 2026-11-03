"""RED-first tests: the operator agent's claim and apply carry out a Green Light
decision (DRE-9051, epic DRE-8738).

`scripts/operator_agent_apply.py` takes the three Green Light outcomes
(DRE-9047) the way it takes `studio`, `ceo` and `done-already`: `claim` posts
the one receipt `operator_decide.receipt_text` writes, through the act
registry, and `apply` acts only under it. For `repair` and `answer` it re-reads
the card live and moves it from `Green Light` to `Planning`, or says it has
already gone. For `keep` the receipt is the whole of it: no live read, no
write. A `keep` with a comment after it is claimed once more — the re-keep — so
a `keep` row is re-read once per comment and never every pass.

The rows are the decision card's records in
`tests/fixtures/operator-decide-green-light-cards.json` — `gl-stopped-run`
(DRE-2498) for `repair` and `keep`, `gl-answerable` (DRE-6589) for `answer` —
each placed on `OperatorBoard` with its own thread, the pipeline's comments as
the pipeline's.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_agent_apply_green_light.py -v
"""
from __future__ import annotations

import dataclasses
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import linear_ops  # noqa: E402
import operator_agent_apply  # noqa: E402
import operator_decide  # noqa: E402
import pipeline_act  # noqa: E402
import test_operator_agent_apply as base  # noqa: E402 — the board, the clock, the calls
from triage_repair_fakes import FLEET_USER, PERSON_USER  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "operator-decide-green-light-cards.json"
MODULE = ROOT / "scripts" / "operator_agent_apply.py"
NOW = base.NOW

GL = "green-light"
GREEN_LIGHT = "Green Light"
STOPPED = "the critic produced no result"
STOPPED_PARK = "This card was about to go to the build queue"
ANSWERABLE = "It is planned in detail only when DRE-6587 is Done"
ANSWERABLE_ASKS = ("Should this memory-fix epic wait for the first epic's diagnostic "
                   "reading, or be planned now against today's evidence?")
ANSWER = ("Plan it now.\nIts blocker, DRE-6587, is Done, and the card says it is planned "
          "when DRE-6587 is Done.")

MOVED = re.compile(r"^operator-agent: (?P<card>DRE-\d+) moved — Green Light → Planning$",
                   re.MULTILINE)
NOT_MOVED = re.compile(r"^operator-agent: (?P<card>DRE-\d+) not moved — it is in "
                       r"(?P<lane>.+) now$", re.MULTILINE)
KEPT = re.compile(r"^operator-agent: (?P<card>DRE-\d+) kept — it stays in Green Light "
                  r"with the CEO$", re.MULTILINE)


# --------------------------------------------------------------------------- #
# the rows                                                                     #
# --------------------------------------------------------------------------- #


def _record(record_id: str) -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))[record_id]["card"]


def _row(record_id: str) -> dict:
    """A fixture record as the row the decision reads."""
    card = _record(record_id)
    return {
        "card": card["identifier"], "lane": card["state"]["name"], "title": card["title"],
        "description": card["description"],
        "labels": [label["name"] for label in card["labels"]["nodes"]],
        "comments": [{"body": c["body"], "created_at": c["createdAt"]}
                     for c in linear_ops.window_nodes(card["comments"])],
    }


def _text(row: dict) -> str:
    return "\n\n".join([row["title"], row["description"]]
                       + [c["body"] for c in row["comments"]])


def _board(record_id: str) -> base.OperatorBoard:
    """The record's card on the operator board, in its lane, with its thread
    oldest first: the pipeline's comments as the pipeline's, a person's as a
    person's."""
    card = _record(record_id)
    board = base.OperatorBoard()
    board.add(card["identifier"], lane=card["state"]["name"], title=card["title"],
              description=card["description"],
              labels=[label["name"] for label in card["labels"]["nodes"]],
              comments=[(c["body"], FLEET_USER if c["user"]["id"] == "user-pipeline"
                         else PERSON_USER)
                        for c in linear_ops.window_nodes(card["comments"])])
    return board


def _answer(card: str, outcome: str, step: str, **extra) -> dict:
    answer = {"card": card, "outcome": outcome, "step": step,
              "reason": "the model read the row", "evidence": [], "answer": ""}
    answer.update(extra)
    return answer


def _repair():
    row = _row("gl-stopped-run")
    decision = operator_decide.decide(
        row, _answer("DRE-2498", "repair", "re-run the review the critic never finished",
                     evidence=[STOPPED, STOPPED_PARK]), _text(row), group=GL)
    assert decision is not None and decision.outcome == operator_decide.REPAIR
    return decision


def _answered():
    row = _row("gl-answerable")
    decision = operator_decide.decide(
        row, _answer("DRE-6589", "answer", ANSWERABLE_ASKS, evidence=[ANSWERABLE],
                     answer=ANSWER), _text(row), group=GL)
    assert decision is not None and decision.outcome == operator_decide.ANSWER
    return decision


def _keep(tmp_path: Path):
    """A screened business choice on the stopped run: part 1's `ceo` test, run
    live on a Green Light row, is `keep`."""
    path = tmp_path / "DRE-2498.choices.json"
    path.write_text(json.dumps(base._choices()), encoding="utf-8")
    row = _row("gl-stopped-run")
    decision = operator_decide.decide(
        row, _answer("DRE-2498", "ceo", "choose whether the sandbox project stays",
                     reason="keeping or closing the project is a choice about the business",
                     finding="The retired sandbox project is still open on the board.",
                     question="Should we archive it now or keep it for a month?",
                     recommendation="Archive it now"),
        _text(row), choices_file=str(path), group=GL)
    assert decision is not None and decision.outcome == operator_decide.KEEP
    return decision


def _receipts(board, card: str) -> list[str]:
    return base._receipts(board, card)


def _comments(board, card: str) -> list[str]:
    return base._comment_writes(board, card)


def _lane_writes(board, card: str) -> list:
    return [w for w in board.writes(card) if w[0].startswith("state")]


class _Reads:
    """Every live read of a card `apply` makes, recorded."""

    def __init__(self, board):
        self.calls: list[str] = []
        real = board.get_issue

        def get_issue(identifier, *, fresh=False):
            self.calls.append(identifier)
            return real(identifier, fresh=fresh)

        board.get_issue = get_issue


# --------------------------------------------------------------------------- #
# the contract's names                                                         #
# --------------------------------------------------------------------------- #


def test_the_written_outcomes_read_the_green_light_outcomes_off_the_decision_module():
    assert set(operator_decide.GREEN_LIGHT_OUTCOMES) <= set(operator_agent_apply.WRITTEN)
    source = MODULE.read_text(encoding="utf-8")
    assert "operator_decide.GREEN_LIGHT_OUTCOMES" in source
    assert "operator_decide.MOVING_OUTCOMES" in source
    assert "operator_decide.GREEN_LIGHT_DESTINATION" in source
    assert "operator_decide.keep_stands(" in source
    for word in ('"repair"', '"answer"', '"keep"', '"Planning"'):
        assert word not in source, word


def test_the_module_docstring_names_the_green_light_outcomes():
    doc = operator_agent_apply.__doc__
    for word in ("`repair`", "`answer`", "`keep`", "Planning", "re-keep"):
        assert word in doc, word


# --------------------------------------------------------------------------- #
# repair                                                                       #
# --------------------------------------------------------------------------- #


class TestRepair:
    def test_claim_posts_one_receipt_and_apply_moves_the_card(self, tmp_path, capsys):
        board = _board("gl-stopped-run")
        decision = _repair()
        assert base._claim(board, tmp_path, decision) == 0
        [posted] = _comments(board, "DRE-2498")
        assert posted == pipeline_act.receipt("operator-agent",
                                              operator_decide.receipt_text(decision, NOW))
        first = posted.splitlines()[0]
        assert first.startswith("🛠️ operator-agent: repair — ")
        assert decision.reason in first and first.endswith("· 09:05 PT")
        assert operator_decide.MOVED_LINE in posted.splitlines()
        assert pipeline_act.read_trailer(posted)["act"] == "operator-agent"
        assert _lane_writes(board, "DRE-2498") == []

        assert base._apply(board, tmp_path, decision) == 0
        assert board.lane("DRE-2498") == "Planning"
        assert _lane_writes(board, "DRE-2498") == [("state", "Planning")]
        assert len(_receipts(board, "DRE-2498")) == 1
        found = MOVED.search(capsys.readouterr().out)
        assert found and found["card"] == "DRE-2498"

    def test_the_move_is_conditional_on_the_lane_it_read(self, tmp_path):
        board = _board("gl-stopped-run")
        decision = _repair()
        assert base._claim(board, tmp_path, decision) == 0
        seen = []
        real = board.cmd_state

        def spy(identifier, state_name, *flags, **kw):
            seen.append((state_name, kw.get("expect")))
            return real(identifier, state_name, *flags, **kw)

        board.cmd_state = spy
        assert base._apply(board, tmp_path, decision) == 0
        assert seen == [("Planning", (GREEN_LIGHT,))]

    def test_an_apply_only_repair_is_applied_under_the_standing_receipt(self, tmp_path):
        board = _board("gl-stopped-run")
        decision = _repair()
        assert base._claim(board, tmp_path, decision) == 0
        assert base._apply(board, tmp_path, decision, action="apply-only") == 0
        assert board.lane("DRE-2498") == "Planning"


# --------------------------------------------------------------------------- #
# answer                                                                       #
# --------------------------------------------------------------------------- #


class TestAnswer:
    def test_claim_posts_the_answer_receipt_and_apply_moves_the_card(self, tmp_path, capsys):
        board = _board("gl-answerable")
        decision = _answered()
        assert base._claim(board, tmp_path, decision) == 0
        [posted] = _comments(board, "DRE-6589")
        lines = posted.splitlines()
        assert lines[0].startswith("🛠️ operator-agent: answer — ")
        assert lines[0].endswith("· 09:05 PT")
        assert f"answer: {operator_decide._flat(ANSWER)}" in lines
        assert f'evidence: "{ANSWERABLE}"' in lines
        assert "moved: Green Light → Planning" in lines

        assert base._apply(board, tmp_path, decision) == 0
        assert board.lane("DRE-6589") == "Planning"
        assert _lane_writes(board, "DRE-6589") == [("state", "Planning")]
        assert len(_receipts(board, "DRE-6589")) == 1
        found = MOVED.search(capsys.readouterr().out)
        assert found and found["card"] == "DRE-6589"


# --------------------------------------------------------------------------- #
# keep                                                                         #
# --------------------------------------------------------------------------- #


class TestKeep:
    def test_claim_posts_one_receipt_and_apply_writes_and_reads_nothing(
            self, tmp_path, capsys):
        board = _board("gl-stopped-run")
        decision = _keep(tmp_path)
        assert base._claim(board, tmp_path, decision) == 0
        [posted] = _comments(board, "DRE-2498")
        lines = posted.splitlines()
        assert lines[0].startswith("🛠️ operator-agent: keep — ")
        assert f"act: {decision.act}" in lines
        assert "stays: Green Light" in lines
        assert "moved: Green Light → Planning" not in lines

        reads = _Reads(board)
        before = list(board.events)
        code, spy = base._apply_ceo(board, tmp_path, decision)
        assert code == 0
        assert board.events == before
        assert reads.calls == []
        assert spy.calls == []
        assert board.lane("DRE-2498") == GREEN_LIGHT
        assert len(_receipts(board, "DRE-2498")) == 1
        found = KEPT.search(capsys.readouterr().out)
        assert found and found["card"] == "DRE-2498"

    def test_a_keep_with_no_receipt_is_not_applied(self, tmp_path, capsys):
        board = _board("gl-stopped-run")
        decision = _keep(tmp_path)
        assert base._apply(board, tmp_path, decision) == 1
        assert board.writes("DRE-2498") == []
        assert not KEPT.search(capsys.readouterr().out)


# --------------------------------------------------------------------------- #
# a second pass writes nothing                                                 #
# --------------------------------------------------------------------------- #


def _decisions(tmp_path):
    return {"repair": ("gl-stopped-run", _repair()),
            "answer": ("gl-answerable", _answered()),
            "keep": ("gl-stopped-run", _keep(tmp_path))}


@pytest.mark.parametrize("outcome", ["repair", "answer", "keep"])
def test_a_second_claim_over_the_unchanged_card_exits_3_and_posts_nothing(tmp_path, outcome):
    record_id, decision = _decisions(tmp_path)[outcome]
    board = _board(record_id)
    assert base._claim(board, tmp_path, decision) == 0
    before = list(board.events)
    assert base._claim(board, tmp_path, decision) == 3
    assert board.events == before
    assert len(_receipts(board, decision.card)) == 1


@pytest.mark.parametrize("outcome", ["repair", "answer"])
def test_a_second_apply_moves_nothing_once_the_card_left_green_light(
        tmp_path, capsys, outcome):
    record_id, decision = _decisions(tmp_path)[outcome]
    board = _board(record_id)
    assert base._claim(board, tmp_path, decision) == 0
    assert base._apply(board, tmp_path, decision) == 0
    assert board.lane(decision.card) == "Planning"
    capsys.readouterr()
    before = list(board.events)
    assert base._apply(board, tmp_path, decision, action="apply-only") == 0
    assert board.events == before
    found = NOT_MOVED.search(capsys.readouterr().out)
    assert found and found["card"] == decision.card and found["lane"] == "Planning"


@pytest.mark.parametrize("outcome", ["repair", "answer"])
def test_a_card_in_another_lane_is_not_moved_and_says_so(tmp_path, capsys, outcome):
    record_id, decision = _decisions(tmp_path)[outcome]
    board = _board(record_id)
    assert base._claim(board, tmp_path, decision) == 0
    board.move(decision.card, "Queued")
    before = list(board.events)
    assert base._apply(board, tmp_path, decision) == 0
    assert board.events == before
    assert board.lane(decision.card) == "Queued"
    out = capsys.readouterr().out
    found = NOT_MOVED.search(out)
    assert found and found["card"] == decision.card and found["lane"] == "Queued"
    assert not MOVED.search(out)


@pytest.mark.parametrize("outcome", ["repair", "answer"])
def test_a_move_linear_refuses_exits_4_and_posts_nothing(tmp_path, capsys, outcome):
    record_id, decision = _decisions(tmp_path)[outcome]
    board = _board(record_id)
    assert base._claim(board, tmp_path, decision) == 0
    board.cmd_state = lambda *a, **k: False
    posted = len(board.bodies(decision.card))
    assert base._apply(board, tmp_path, decision) == 4
    assert len(board.bodies(decision.card)) == posted
    found = base.REFUSED.search(capsys.readouterr().out)
    assert found and found["card"] == decision.card and found["outcome"] == outcome
    assert "Planning" in found["what"]


def test_a_move_that_raised_exits_4(tmp_path, capsys):
    board = _board("gl-stopped-run")
    decision = _repair()
    assert base._claim(board, tmp_path, decision) == 0
    board.fail("state", "Planning")
    assert base._apply(board, tmp_path, decision) == 4
    assert board.lane("DRE-2498") == GREEN_LIGHT
    found = base.REFUSED.search(capsys.readouterr().out)
    assert found and found["outcome"] == "repair"


# --------------------------------------------------------------------------- #
# the re-keep                                                                  #
# --------------------------------------------------------------------------- #


def _kept_then_said(tmp_path):
    """The stopped run with a `keep` receipt and one comment by a person
    after it."""
    board = _board("gl-stopped-run")
    decision = _keep(tmp_path)
    assert base._claim(board, tmp_path, decision) == 0
    board.say("DRE-2498", "One more thing about the share panel.", user=PERSON_USER)
    return board, decision


def test_a_comment_after_a_keep_is_claimed_once_more(tmp_path):
    board, decision = _kept_then_said(tmp_path)
    assert base._claim(board, tmp_path, decision) == 0
    receipts = _receipts(board, "DRE-2498")
    assert len(receipts) == 2
    assert board.newest("DRE-2498") == receipts[-1]
    assert receipts[-1].splitlines()[0].startswith("🛠️ operator-agent: keep — ")
    assert len(_comments(board, "DRE-2498")) == 2

    before = list(board.events)
    assert base._claim(board, tmp_path, decision) == 3
    assert board.events == before


def test_a_repair_after_a_kept_and_answered_row_posts_and_moves(tmp_path, capsys):
    board, _kept = _kept_then_said(tmp_path)
    decision = _repair()
    assert base._claim(board, tmp_path, decision) == 0
    assert board.newest("DRE-2498").splitlines()[0].startswith("🛠️ operator-agent: repair — ")
    assert base._apply(board, tmp_path, decision) == 0
    assert board.lane("DRE-2498") == "Planning"
    assert MOVED.search(capsys.readouterr().out)


def test_a_keep_with_another_fingerprint_does_not_stop_the_claim(tmp_path):
    board = _board("gl-stopped-run")
    kept = _keep(tmp_path)
    base._receipt(board, dataclasses.replace(kept, fingerprint="0123456789ab"))
    assert base._claim(board, tmp_path, kept) == 0
    assert len(_receipts(board, "DRE-2498")) == 2


def test_a_keep_by_a_person_never_stops_the_claim(tmp_path):
    board = _board("gl-stopped-run")
    kept = _keep(tmp_path)
    base._receipt(board, kept, user=PERSON_USER)
    assert base._claim(board, tmp_path, kept) == 0
    assert len(_comments(board, "DRE-2498")) == 1


@pytest.mark.parametrize("decision", [
    base._decision("studio"), base._decision("ceo"), base._decision("done-already")],
    ids=["studio", "ceo", "done-already"])
def test_a_waiting_receipt_with_a_comment_after_it_still_spends_the_pass(tmp_path, decision):
    board = base._board()
    base._receipt(board, decision)
    board.say(base.CARD, "A note after the receipt.", user=PERSON_USER)
    board.say(base.CARD, "🧹 hygiene: a note")
    before = list(board.events)
    assert base._claim(board, tmp_path, decision) == 3
    assert board.events == before


def test_a_waiting_decision_over_a_standing_keep_with_a_comment_after_it_is_spent(tmp_path):
    """The re-keep is the Green Light group's: a waiting decision over the
    same fingerprint is spent by any standing receipt, a `keep` included."""
    board = base._board()
    waiting = base._decision("ceo")
    kept = dataclasses.replace(waiting, outcome=operator_decide.KEEP,
                               reason=operator_decide.KEEP_REASON, finding="", question="",
                               recommendation="")
    base._receipt(board, kept)
    board.say(base.CARD, "A note after the receipt.", user=PERSON_USER)
    before = list(board.events)
    assert base._claim(board, tmp_path, waiting) == 3
    assert board.events == before


# --------------------------------------------------------------------------- #
# what it says                                                                 #
# --------------------------------------------------------------------------- #


def test_no_comment_it_posts_opens_outcome_or_released_as_or_names_a_person(tmp_path):
    bodies = []
    for outcome, (record_id, decision) in _decisions(tmp_path).items():
        board = _board(record_id)
        assert base._claim(board, tmp_path, decision) == 0, outcome
        base._apply(board, tmp_path, decision)
        bodies += _comments(board, decision.card)
    assert len(bodies) == 3
    for body in bodies:
        assert "@" not in body
        for line in body.splitlines():
            assert not line.lstrip().startswith(("Outcome:", "Released as")), line
