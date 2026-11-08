"""RED-first tests: the operator agent's claim and apply (DRE-8746, epic DRE-8738).

`scripts/operator_agent_apply.py` holds every Linear write the operator agent
makes. `claim` posts the one receipt that spends a card's pass —
`operator_decide.receipt_text`, through `pipeline_act.receipt("operator-agent",
…)`. `apply` acts only under that receipt, re-reads the card live, and makes
the one write its outcome names: a `blockedBy` relation to the studio host's
go-live for `studio`, one question in Green Light for `ceo`, the close to Done
for `done-already`. A write that did not land is a refusal: one log line, no
comment, exit 4.

Every scenario runs over `OperatorBoard`, the Triage repair's test board with
the card's blockers added, so the real `planning_escalation.escalate` runs over
it with the real lane contract and the test reads back the lane a card ends in
and the comments it carries.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_agent_apply.py -v
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import console_escalation  # noqa: E402
import linear_ops  # noqa: E402
import operator_agent_apply  # noqa: E402
import operator_decide  # noqa: E402
import operator_rules  # noqa: E402
import pipeline_act  # noqa: E402
import planning_escalation  # noqa: E402
from triage_repair_fakes import PERSON_USER, TriageRepairBoard  # noqa: E402

MODULE = ROOT / "scripts" / "operator_agent_apply.py"
ACTS = ROOT / "config" / "pipeline-acts.json"
ACTS_DOC = ROOT / "docs" / "pipeline-acts.md"

#: 2026-11-02 17:05 UTC is 09:05 PT.
NOW = datetime(2026, 11, 2, 17, 5, tzinfo=UTC)

CARD = "DRE-9001"
LANE = "Hand-work"
TITLE = "agent-bureau: a step a person takes"
DESCRIPTION = "Atlas is live, 2026-09-21 11:07 PT.\n\nRun the bootstrap deploy."
HOST_QUOTE = "In this child the studio user can do everything but deploy."

STUDIO = operator_rules.load_rules()["studio"]
BLOCKER = STUDIO["blocker"]
NEVER = STUDIO["never_blocker"]
DEPLOY_EPIC = STUDIO["deploy_epic"]

REFUSED = re.compile(r"^operator-agent: (?P<card>DRE-\d+) write refused — "
                     r"(?P<outcome>\S+) · (?P<what>.+)$", re.MULTILINE)


# --------------------------------------------------------------------------- #
# the board                                                                    #
# --------------------------------------------------------------------------- #


class OperatorBoard(TriageRepairBoard):
    """The Triage repair's board, with each card's `blockedBy` relations: the
    `inverseRelations` read answers them, and `_add_blocked_by` writes one —
    or, told to `drop`, answers as Linear does and writes nothing."""

    def __init__(self) -> None:
        super().__init__()
        self.blocked_by: dict[str, list[str]] = {}
        self.drop_relations = False

    def gql(self, query, variables=None, **kw):
        if "inverseRelations" in query:
            identifier = (variables or {}).get("id")
            self._card(identifier)
            nodes = [{"type": "blocks", "issue": {"identifier": b}}
                     for b in self.blocked_by.get(identifier, [])]
            return {"issue": {"inverseRelations": {
                "pageInfo": {"hasNextPage": False}, "nodes": nodes}}}
        return super().gql(query, variables, **kw)

    def add_blocked_by(self, issue_id, blockers):
        identifier = issue_id.removeprefix("id-")
        self._card(identifier)
        for blocker in blockers:
            self._card(blocker)
            self.events.append(("relation", identifier, blocker))
            if not self.drop_relations:
                self.blocked_by.setdefault(identifier, []).append(blocker)
        return list(blockers)

    @contextlib.contextmanager
    def patched(self):
        with super().patched(), contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(linear_ops, "gql", side_effect=self.gql))
            stack.enter_context(patch.object(linear_ops, "_add_blocked_by",
                                             side_effect=self.add_blocked_by))
            stack.enter_context(patch.object(operator_agent_apply, "_now",
                                             return_value=NOW))
            yield self


def _board(lane: str = LANE, **kw) -> OperatorBoard:
    board = OperatorBoard()
    board.add(CARD, lane=lane, title=TITLE, description=DESCRIPTION,
              labels=["repo:agent-bureau", "operator-step", "no-code"], **kw)
    board.add(BLOCKER, lane="Queued", title="agent-bureau: the studio host goes live",
              labels=["repo:agent-bureau"])
    board.add(NEVER, lane="Done", title="agent-bureau: the studio host decision",
              labels=["repo:agent-bureau"])
    board.add(DEPLOY_EPIC, lane="In Progress", title="[EPIC] the studio deploy",
              labels=["repo:agent-bureau"], children=["DRE-1"])
    return board


# --------------------------------------------------------------------------- #
# the decisions                                                                #
# --------------------------------------------------------------------------- #


def _choices(**override) -> dict:
    block = {
        "question": "Should the old sandbox project be archived now or kept a month?",
        "context": "Two ways to close the retired sandbox project.",
        "choices": [
            {"id": "archive-now", "label": "Archive it now",
             "effect": "The project is archived today.", "outcome": "proceed"},
            {"id": "keep-a-month", "label": "Keep it a month",
             "effect": "The project stays for a month, then closes.", "outcome": "close"},
        ],
        "recommended": "archive-now",
        "why": "Nothing reads the project any more.",
    }
    block.update(override)
    return block


def _decision(outcome: str, card: str = CARD, lane: str = LANE, **kw):
    base = dict(
        card=card, repo="agent-bureau", lane=lane, outcome=outcome,
        reason="the model read the card", step="run the bootstrap deploy",
        act="", deploy=False, evidence=[], host_cannot=[], finding="",
        question="", recommendation="", choices=None, held="",
        fingerprint=operator_decide.fingerprint(
            {"title": TITLE, "description": DESCRIPTION}))
    if outcome == "studio":
        base.update(reason="the step needs production deploy, production access "
                           "no test environment gives",
                    host_cannot=[HOST_QUOTE])
    elif outcome == "ceo":
        base.update(act="aws-access", reason="the card names AWS access, an act the CEO keeps",
                    finding="The step this card names, “run the bootstrap deploy”, is "
                            "AWS access: IAM users, roles and policies, an act the CEO keeps.",
                    question="Shall we go ahead with this step?",
                    recommendation=console_escalation.NONE_GIVEN)
    elif outcome == "done-already":
        base.update(evidence=["Atlas is live, 2026-09-21 11:07 PT."])
    base.update(kw)
    return operator_decide.Decision(**base)


def _business(**kw):
    fields = dict(act=operator_decide.BUSINESS_CHOICE,
                  reason="keeping or closing the project is a choice about the business",
                  step="choose whether the sandbox project stays",
                  finding="The retired sandbox project is still open on the board.",
                  question="Should we archive it now or keep it for a month?",
                  recommendation="Archive it now", choices=_choices())
    fields.update(kw)
    return _decision("ceo", **fields)


def _file(tmp_path: Path, decision, action: str = "decide") -> str:
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps([dict(decision.to_json(), action=action)]),
                    encoding="utf-8")
    return str(path)


def _receipt(board, decision, *, user=None, rules=None) -> None:
    """The claim's receipt, posted as the claim posts it — or, with `user`,
    the same text written by somebody else."""
    body = pipeline_act.receipt("operator-agent",
                                operator_decide.receipt_text(decision, NOW, rules))
    if user is None:
        board.say(decision.card, body)
    else:
        board.say(decision.card, body, user=user)


def _claim(board, tmp_path, decision, action="decide") -> int:
    with board.patched():
        return operator_agent_apply.main(
            ["claim", decision.card, "--decisions", _file(tmp_path, decision, action)])


def _apply(board, tmp_path, decision, action="decide", rules=None) -> int:
    path = _file(tmp_path, decision, action)
    with board.patched():
        if rules is not None:
            with patch.object(operator_rules, "load_rules", return_value=rules):
                return operator_agent_apply.main(["apply", decision.card,
                                                  "--decisions", path])
        return operator_agent_apply.main(["apply", decision.card, "--decisions", path])


@pytest.fixture(autouse=True)
def _runner_temp(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner-temp"))


def _receipts(board, card=CARD) -> list[str]:
    return [b for b in board.bodies(card) if b.startswith(operator_decide.RECEIPT_TAG)]


def _comment_writes(board, card=CARD) -> list[str]:
    return [detail for kind, detail in board.writes(card) if kind == "comment"]


# --------------------------------------------------------------------------- #
# claim                                                                        #
# --------------------------------------------------------------------------- #


class TestClaim:
    @pytest.mark.parametrize("decision", [
        _decision("studio"), _decision("ceo"), _business(), _decision("done-already")],
        ids=["studio", "ceo", "business-choice", "done-already"])
    def test_posts_exactly_the_receipt_through_the_act(self, tmp_path, decision):
        board = _board()
        assert _claim(board, tmp_path, decision) == 0
        expected = pipeline_act.receipt("operator-agent",
                                        operator_decide.receipt_text(decision, NOW))
        assert _comment_writes(board) == [expected]
        assert "· 09:05 PT" in expected.splitlines()[0]
        assert pipeline_act.read_trailer(expected)["act"] == "operator-agent"
        assert not [w for w in board.writes(CARD) if w[0] != "comment"]

    def test_a_standing_receipt_with_the_fingerprint_exits_3_and_posts_nothing(self, tmp_path):
        board = _board()
        decision = _decision("studio")
        _receipt(board, decision)
        assert _claim(board, tmp_path, decision) == 3
        assert board.writes(CARD) == []

    def test_a_receipt_a_person_wrote_does_not_spend_the_pass(self, tmp_path):
        board = _board()
        decision = _decision("studio")
        _receipt(board, decision, user=PERSON_USER)
        assert _claim(board, tmp_path, decision) == 0
        assert len(_comment_writes(board)) == 1

    def test_a_receipt_with_another_fingerprint_does_not_spend_the_pass(self, tmp_path):
        board = _board()
        _receipt(board, _decision("studio", fingerprint="0123456789ab"))
        assert _claim(board, tmp_path, _decision("studio")) == 0
        assert len(_comment_writes(board)) == 1

    @pytest.mark.parametrize("decision,action", [
        (_decision("do"), "decide"),
        (_decision("studio", held=operator_decide.HELD_NO_QUOTE, host_cannot=[]), "decide"),
        (_decision("studio"), "spent"),
        (_decision("done-already"), "guard-returned"),
        (_decision("ceo"), "apply-only"),
    ], ids=["do", "held", "spent", "guard-returned", "apply-only"])
    def test_refuses_and_posts_nothing(self, tmp_path, decision, action):
        board = _board()
        assert _claim(board, tmp_path, decision, action) != 0
        assert board.writes(CARD) == []

    def test_a_card_missing_from_the_decisions_posts_nothing(self, tmp_path):
        board = _board()
        path = _file(tmp_path, _decision("studio", card="DRE-1"))
        with board.patched():
            assert operator_agent_apply.main(["claim", CARD, "--decisions", path]) != 0
        assert board.writes(CARD) == []

    def test_the_clock_is_read_in_one_place(self):
        source = MODULE.read_text(encoding="utf-8")
        assert source.count("datetime.now(") == 1
        assert "def _now()" in source


# --------------------------------------------------------------------------- #
# apply — only under its receipt                                               #
# --------------------------------------------------------------------------- #


class TestOnlyUnderItsReceipt:
    def test_no_receipt_writes_nothing_and_exits_1(self, tmp_path):
        board = _board()
        assert _apply(board, tmp_path, _decision("done-already")) == 1
        assert board.writes(CARD) == [] and board.lane(CARD) == LANE

    def test_a_receipt_with_another_fingerprint_writes_nothing(self, tmp_path):
        board = _board()
        _receipt(board, _decision("done-already", fingerprint="0123456789ab"))
        assert _apply(board, tmp_path, _decision("done-already")) == 1
        assert board.writes(CARD) == [] and board.lane(CARD) == LANE

    def test_a_receipt_with_another_outcome_writes_nothing(self, tmp_path):
        board = _board()
        _receipt(board, _decision("studio"))
        assert _apply(board, tmp_path, _decision("done-already")) == 1
        assert board.writes(CARD) == [] and board.lane(CARD) == LANE

    def test_a_receipt_a_person_wrote_writes_nothing(self, tmp_path):
        board = _board()
        decision = _decision("done-already")
        _receipt(board, decision, user=PERSON_USER)
        assert _apply(board, tmp_path, decision) == 1
        assert board.writes(CARD) == [] and board.lane(CARD) == LANE

    @pytest.mark.parametrize("decision", [
        _decision("do"),
        _decision("studio", held=operator_decide.HELD_NOT_READ, host_cannot=[])],
        ids=["do", "held"])
    def test_a_do_or_held_decision_is_refused(self, tmp_path, decision):
        board = _board()
        _receipt(board, dataclasses.replace(decision, outcome="studio", held=""))
        assert _apply(board, tmp_path, decision) == 1
        assert board.writes(CARD) == [] and board.blocked_by.get(CARD) is None

    def test_a_spent_decision_writes_nothing(self, tmp_path):
        board = _board()
        decision = _decision("done-already")
        _receipt(board, decision)
        assert _apply(board, tmp_path, decision, action="spent") != 0
        assert board.writes(CARD) == [] and board.lane(CARD) == LANE


# --------------------------------------------------------------------------- #
# apply — live first                                                           #
# --------------------------------------------------------------------------- #


class TestLiveFirst:
    @pytest.mark.parametrize("decision", [
        _decision("studio"), _decision("ceo"), _decision("done-already")],
        ids=["studio", "ceo", "done-already"])
    def test_a_card_that_left_the_lane_writes_nothing_and_says_why(
            self, tmp_path, capsys, decision):
        board = _board()
        _receipt(board, decision)
        board.move(CARD, "In Review")
        assert _apply(board, tmp_path, decision) == 0
        assert board.writes(CARD) == [] and board.lane(CARD) == "In Review"
        assert board.blocked_by.get(CARD) is None
        out = capsys.readouterr().out
        assert f"operator-agent: {CARD}" in out and "In Review" in out and LANE in out


# --------------------------------------------------------------------------- #
# studio                                                                       #
# --------------------------------------------------------------------------- #


class TestStudio:
    def test_adds_exactly_one_relation_to_the_go_live(self, tmp_path):
        board = _board()
        decision = _decision("studio")
        _receipt(board, decision)
        assert _apply(board, tmp_path, decision) == 0
        assert board.blocked_by[CARD] == [BLOCKER]
        assert [w for w in board.writes(CARD)] == [("relation", BLOCKER)]
        assert NEVER not in board.blocked_by[CARD]
        assert board.lane(CARD) == LANE

    def test_run_again_it_writes_nothing(self, tmp_path):
        board = _board()
        decision = _decision("studio")
        _receipt(board, decision)
        assert _apply(board, tmp_path, decision) == 0
        before = list(board.events)
        assert _apply(board, tmp_path, decision, action="apply-only") == 0
        assert board.events == before
        assert board.blocked_by[CARD] == [BLOCKER]

    def test_a_relation_already_there_is_skipped(self, tmp_path):
        board = _board()
        board.blocked_by[CARD] = ["DRE-1", BLOCKER]
        decision = _decision("studio")
        _receipt(board, decision)
        assert _apply(board, tmp_path, decision) == 0
        assert board.writes(CARD) == []

    def test_a_relation_to_the_never_blocker_is_refused(self, tmp_path, capsys):
        rules = operator_rules.load_rules()
        rules = dict(rules, studio=dict(rules["studio"], blocker=NEVER))
        board = _board()
        decision = _decision("studio")
        _receipt(board, decision, rules=rules)
        assert _apply(board, tmp_path, decision, rules=rules) != 0
        assert board.writes(CARD) == [] and board.blocked_by.get(CARD) is None
        out = capsys.readouterr().out
        assert NEVER in out and "never a blocker" in out

    def test_a_relation_to_an_epic_is_refused(self, tmp_path, capsys):
        rules = operator_rules.load_rules()
        rules = dict(rules, studio=dict(rules["studio"], blocker="DRE-7777"))
        board = _board()
        board.add("DRE-7777", lane="In Progress", title="a card with children",
                  labels=["repo:agent-bureau"], children=["DRE-7778"])
        decision = _decision("studio")
        _receipt(board, decision, rules=rules)
        assert _apply(board, tmp_path, decision, rules=rules) != 0
        assert board.writes(CARD) == [] and board.blocked_by.get(CARD) is None
        assert "DRE-7777 is an epic" in capsys.readouterr().out

    def test_the_go_live_card_is_never_blocked_by_itself(self, tmp_path, capsys):
        board = _board()
        board.cards[BLOCKER].lane = LANE
        board.cards[BLOCKER].title = TITLE
        board.cards[BLOCKER].description = DESCRIPTION
        decision = _decision("studio", card=BLOCKER)
        _receipt(board, decision)
        assert _apply(board, tmp_path, decision) != 0
        assert board.writes(BLOCKER) == []
        assert board.blocked_by.get(BLOCKER) is None
        out = capsys.readouterr().out
        assert f"operator-agent: {BLOCKER}" in out and "itself" in out

    def test_a_deploy_step_names_the_deploy_epic_on_its_receipt_only(self, tmp_path):
        board = _board()
        decision = _decision("studio", deploy=True)
        assert _claim(board, tmp_path, decision) == 0
        assert f"waits on: {BLOCKER}, {DEPLOY_EPIC}" in board.newest(CARD)
        assert _apply(board, tmp_path, decision, action="decide") == 0
        assert board.blocked_by[CARD] == [BLOCKER]

    def test_a_relation_that_did_not_land_is_a_refusal(self, tmp_path, capsys):
        board = _board()
        board.drop_relations = True
        decision = _decision("studio")
        _receipt(board, decision)
        posted = len(board.bodies(CARD))
        assert _apply(board, tmp_path, decision) == 4
        assert len(board.bodies(CARD)) == posted
        found = REFUSED.search(capsys.readouterr().out)
        assert found and found["card"] == CARD and found["outcome"] == "studio"
        assert BLOCKER in found["what"]


# --------------------------------------------------------------------------- #
# ceo                                                                          #
# --------------------------------------------------------------------------- #


class _Escalations:
    """The calls `escalate` received, recorded, and the real one run."""

    def __init__(self):
        self.calls: list[dict] = []
        self.files: list[dict | None] = []
        self._real = planning_escalation.escalate

    def __call__(self, ops, identifier, reason, *args, **kwargs):
        self.calls.append(dict(kwargs, identifier=identifier, reason=reason, args=args))
        path = kwargs.get("choices_file")
        self.files.append(json.loads(Path(path).read_text(encoding="utf-8"))
                          if path else None)
        return self._real(ops, identifier, reason, *args, **kwargs)


def _apply_ceo(board, tmp_path, decision, action="decide"):
    spy = _Escalations()
    with patch.object(planning_escalation, "escalate", side_effect=spy):
        code = _apply(board, tmp_path, decision, action)
    return code, spy


class TestCeo:
    def test_a_kept_act_lands_in_green_light_with_one_question(self, tmp_path):
        board = _board()
        decision = _decision("ceo")
        _receipt(board, decision)
        code, spy = _apply_ceo(board, tmp_path, decision)
        assert code == 0
        assert board.lane(CARD) == "Green Light"
        notes = [b for b in _comment_writes(board)
                 if planning_escalation.ESCALATION_TAG in b]
        assert len(notes) == 1
        parsed = console_escalation.parse(notes[0])
        assert parsed is not None
        assert parsed.finding == decision.finding
        assert parsed.question == decision.question
        assert parsed.question.count("?") == 1
        # `none given` reads back as no recommendation, with the reason.
        assert parsed.recommendation is None
        assert f"Recommendation: {console_escalation.NONE_GIVEN}" in notes[0]

    def test_a_kept_act_calls_escalate_with_no_choices_file_and_never_choices(self, tmp_path):
        board = _board()
        decision = _decision("ceo")
        _receipt(board, decision)
        _code, spy = _apply_ceo(board, tmp_path, decision)
        [call] = spy.calls
        assert "choices" not in call
        assert call.get("choices_file") is None
        assert call["from_lane"] == LANE
        assert call["reason"] == operator_decide.three_lines(decision)

    def test_a_business_choice_calls_escalate_with_its_choices_file(self, tmp_path):
        board = _board()
        decision = _business()
        _receipt(board, decision)
        code, spy = _apply_ceo(board, tmp_path, decision)
        assert code == 0
        [call] = spy.calls
        assert "choices" not in call
        assert call["choices_file"] == os.path.join(
            os.environ["RUNNER_TEMP"], "operator-agent", "choices", f"{CARD}.json")
        assert spy.files == [decision.choices]
        assert call["from_lane"] == LANE
        assert board.lane(CARD) == "Green Light"
        note = next(b for b in board.bodies(CARD) if planning_escalation.ESCALATION_TAG in b)
        assert console_escalation.parse(note).question == decision.question

    def test_a_business_choice_the_screen_refuses_writes_nothing_and_exits_4(
            self, tmp_path, capsys):
        board = _board()
        decision = _business(choices=_choices(
            context="This is not a decision, just a note for the record."))
        _receipt(board, decision)
        code, spy = _apply_ceo(board, tmp_path, decision)
        assert code == 4
        assert spy.calls == []
        assert board.writes(CARD) == [] and board.lane(CARD) == LANE
        out = capsys.readouterr().out
        found = REFUSED.search(out)
        assert found and found["outcome"] == "ceo"

    def test_an_apply_only_business_choice_uses_the_receipt_s_block(self, tmp_path):
        board = _board()
        claimed = _business()
        _receipt(board, claimed)
        rebuilt = operator_decide.decision_from_receipt(
            {"card": CARD, "lane": LANE}, operator_decide.receipt_text(claimed, NOW))
        assert rebuilt.choices == claimed.choices
        code, spy = _apply_ceo(board, tmp_path, rebuilt, action="apply-only")
        assert code == 0
        assert spy.files == [claimed.choices]
        assert board.lane(CARD) == "Green Light"
        assert len(_receipts(board)) == 1

    def test_an_apply_only_ceo_card_is_applied_under_the_standing_receipt(self, tmp_path):
        board = _board()
        decision = _decision("ceo")
        assert _claim(board, tmp_path, decision) == 0
        code, _spy = _apply_ceo(board, tmp_path, decision, action="apply-only")
        assert code == 0
        assert board.lane(CARD) == "Green Light"
        assert len(_receipts(board)) == 1

    def test_a_card_not_in_green_light_after_escalate_is_a_refusal(self, tmp_path, capsys):
        board = _board()
        decision = _decision("ceo")
        _receipt(board, decision)
        real = board.cmd_state

        def stuck(identifier, state_name, *flags, **kw):
            if state_name == "Green Light":
                board.events.append(("state-dropped", identifier, state_name))
                return True
            return real(identifier, state_name, *flags, **kw)

        board.cmd_state = stuck
        code, _spy = _apply_ceo(board, tmp_path, decision)
        assert code == 4
        assert board.lane(CARD) == LANE
        found = REFUSED.search(capsys.readouterr().out)
        assert found and found["outcome"] == "ceo" and "Green Light" in found["what"]
        assert len(_receipts(board)) == 1

    def test_a_stood_down_escalation_is_not_a_refusal(self, tmp_path, capsys):
        board = _board()
        decision = _decision("ceo")
        _receipt(board, decision)
        stood = planning_escalation.Outcome(parked=False, posted=False,
                                            stood_down="it is in In Review")
        with patch.object(planning_escalation, "escalate", return_value=stood):
            code = _apply(board, tmp_path, decision)
        assert code == 0
        assert board.writes(CARD) == []
        out = capsys.readouterr().out
        assert "write refused" not in out and "it is in In Review" in out


# --------------------------------------------------------------------------- #
# done-already                                                                 #
# --------------------------------------------------------------------------- #


class TestDoneAlready:
    def test_moves_the_card_to_done_from_its_lane(self, tmp_path):
        board = _board()
        decision = _decision("done-already")
        _receipt(board, decision)
        assert _apply(board, tmp_path, decision) == 0
        assert board.lane(CARD) == "Done"
        assert board.writes(CARD) == [("state", "Done")]

    def test_the_move_is_conditional_on_the_lane_it_read(self, tmp_path):
        board = _board()
        decision = _decision("done-already")
        _receipt(board, decision)
        seen = []
        real = board.cmd_state

        def spy(identifier, state_name, *flags, **kw):
            seen.append((state_name, kw.get("expect")))
            return real(identifier, state_name, *flags, **kw)

        board.cmd_state = spy
        assert _apply(board, tmp_path, decision) == 0
        assert seen == [("Done", (LANE,))]

    def test_a_refused_move_exits_4_and_posts_nothing(self, tmp_path, capsys):
        board = _board()
        decision = _decision("done-already")
        _receipt(board, decision)
        board.cmd_state = lambda *a, **k: False
        posted = len(board.bodies(CARD))
        assert _apply(board, tmp_path, decision) == 4
        assert len(board.bodies(CARD)) == posted
        found = REFUSED.search(capsys.readouterr().out)
        assert found and found["card"] == CARD and found["outcome"] == "done-already"
        assert "Done" in found["what"]

    def test_an_apply_only_close_is_applied_under_the_standing_receipt(self, tmp_path):
        board = _board()
        decision = _decision("done-already")
        assert _claim(board, tmp_path, decision) == 0
        assert _apply(board, tmp_path, decision, action="apply-only") == 0
        assert board.lane(CARD) == "Done"
        assert len(_receipts(board)) == 1

    def test_a_guard_returned_card_is_never_closed_again(self, tmp_path):
        board = _board()
        decision = _decision("done-already")
        assert _claim(board, tmp_path, decision) == 0
        assert _apply(board, tmp_path, decision) == 0
        board.move(CARD, "Queued")
        board.say(CARD, f"{operator_decide.GUARD_RETURN_TAG}: {CARD} reached Done with "
                        "no record in the hour before")
        before = list(board.events)
        returned = dataclasses.replace(decision, lane="Queued")
        assert _apply(board, tmp_path, returned, action="guard-returned") != 0
        assert _claim(board, tmp_path, returned, action="guard-returned") != 0
        assert board.events == before
        assert board.lane(CARD) == "Queued"
        assert len(_receipts(board)) == 1

    def test_a_guard_return_after_the_receipt_stops_the_apply_whatever_the_action(
            self, tmp_path):
        board = _board(lane="Queued")
        decision = _decision("done-already", lane="Queued")
        _receipt(board, decision)
        board.say(CARD, f"{operator_decide.GUARD_RETURN_TAG}: returned")
        assert _apply(board, tmp_path, decision, action="apply-only") != 0
        assert board.writes(CARD) == [] and board.lane(CARD) == "Queued"


# --------------------------------------------------------------------------- #
# no forged record                                                             #
# --------------------------------------------------------------------------- #


def test_no_comment_it_posts_opens_outcome_or_released_as(tmp_path):
    bodies = []
    for decision in (_decision("studio"), _decision("ceo"), _business(),
                     _decision("done-already")):
        board = _board()
        assert _claim(board, tmp_path, decision) == 0
        _apply_ceo(board, tmp_path, decision)
        bodies += _comment_writes(board)
    assert len(bodies) >= 6
    for body in bodies:
        for line in body.splitlines():
            assert not line.lstrip().startswith(("Outcome:", "Released as")), line


def test_the_refusal_line_opens_with_the_log_tag():
    assert operator_agent_apply.refusal_line(CARD, "studio", "the relation") == (
        f"{operator_decide.LOG_TAG} {CARD} write refused — studio · the relation")


def test_it_defines_no_tag_constant_of_its_own():
    """`pipeline_act.py check` reads every `*_TAG` constant in an emitter file
    as a tag the registry must declare; `operator_decide`'s are imported."""
    source = MODULE.read_text(encoding="utf-8")
    assert not re.search(r"^\w+_TAG\s*=", source, re.MULTILINE)
    assert source.count('pipeline_act.receipt("operator-agent"') == 1


# --------------------------------------------------------------------------- #
# the act                                                                      #
# --------------------------------------------------------------------------- #


def test_the_act_row():
    row = pipeline_act.record("operator-agent")
    assert row["tag"] == "operator-agent"
    assert row["adopted"] is True
    assert row["kind"] == "recovery"
    assert row["state"] == "unchanged"
    assert row["next_actor"] == "operator"
    assert row["subscriber"] == "operator-agent.yml"
    assert row["discharges"] is None
    assert row["cadence_s"] is None
    assert row["emits"] == {"file": "scripts/operator_agent_apply.py",
                            "anchor": 'pipeline_act.receipt("operator-agent"'}
    assert "DRE-8738" in row["why"]


def test_the_acts_page_carries_the_section_with_the_row_s_values():
    text = ACTS_DOC.read_text(encoding="utf-8")
    heading = ("## The row that records an Operator Agent pass — "
               "`🛠️ operator-agent` (DRE-8746)")
    assert heading in text
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    row = pipeline_act.record("operator-agent")
    assert f"`{row['tag']}`" in section
    assert f"`{row['kind']}` · `{row['state']}` · `{row['next_actor']}`" in section
    assert "`null`" in section
    assert "operator_agent_apply.py" in section
