"""RED-first tests: the operator agent's decision (DRE-8744, epic DRE-8738).

A card waiting on a person gets at most one outcome per pass — `do`, `studio`,
`ceo` or `done-already` — and a card the model has not answered gets none.
`scripts/operator_decide.py` is the pure module that picks the outcome from
the fixed routes of `scripts/operator_rules.py` (DRE-8743) and the model's
answer, guards that answer so a model can never put a non-decision in front of
the CEO, and writes a decision down as a receipt and reads it back.

What the replay proves is PRECISION: a fixed route fires on no card it does
not belong to, and a model's answer never reaches the CEO unscreened. Recall
is the live pass's to measure, in the epic's PROOF, DRE-8837.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_decide.py -v
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import types
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import console_escalation  # noqa: E402
import operator_decide  # noqa: E402
import operator_rules  # noqa: E402
import planning_escalation  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "operator-agent-board-2026-11-01.json"
MODULE = ROOT / "scripts" / "operator_decide.py"
PROOF_REACH = ROOT / "config" / "proof-reach.json"

#: 2026-11-02 17:05 UTC is 09:05 PT — standard time began on 2026-11-01.
NOW = datetime(2026, 11, 2, 17, 5, tzinfo=UTC)

ATLAS_LIVE = "Atlas is live, 2026-09-21 11:07 PT."
HOST_QUOTE = "In this child the studio user can do everything but deploy."

#: The fixture cards the rules send to the CEO once the model has answered
#: them, and the ones they send to the studio host. Every other card decides
#: `do` on a `do` answer.
CEO_BY_RULE = {
    "DRE-7078": "destructive-go",
    "DRE-7224": "aws-access",
    "DRE-8094": "billing-and-account-settings",
    "DRE-8173": "github-org-membership",
    "DRE-8227": "github-org-creation",
    "DRE-8582": "aws-access",
    "DRE-8643": "aws-access",
}
STUDIO_BY_RULE = {"DRE-7223", "DRE-7328"}


def _records() -> dict:
    return {r["card"]: r for r in json.loads(FIXTURE.read_text(encoding="utf-8"))}


def _card(ident: str) -> dict:
    return operator_rules.fixture_card(_records()[ident])


def _text(card: dict, comments: bool = True) -> str:
    """The card text the model read: the title, the description and, unless
    left out, the comment bodies, joined."""
    parts = [card["title"], card["description"]]
    if comments:
        parts += [c["body"] for c in card.get("comments", [])]
    return "\n\n".join(parts)


def _answer(card: str, outcome: str = "do", step: str = "do the step the card names",
            **extra) -> dict:
    answer = {
        "card": card, "outcome": outcome, "step": step,
        "reason": "the model read the card", "evidence": [], "access": "",
        "deploy": False, "host_cannot": [], "finding": "", "question": "",
        "recommendation": "",
    }
    answer.update(extra)
    return answer


def _synthetic(ident: str = "DRE-9001", title: str = "agent-bureau: a synthetic card",
               description: str = "A step a person takes.", lane: str = "Hand-work",
               labels=("repo:agent-bureau", "operator-step")) -> dict:
    return {"card": ident, "lane": lane, "title": title, "labels": list(labels),
            "description": description, "comments": []}


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


def _write(path: Path, data) -> str:
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(path)


def _business_answer(card: str) -> dict:
    return _answer(
        card, "ceo", "choose whether the sandbox project stays",
        reason="keeping or closing the project is a choice about the business",
        finding="The retired sandbox project is still open on the board.",
        question="Should we archive it now or keep it for a month?",
        recommendation="Archive it now",
    )


# --------------------------------------------------------------------------- #
# the contract's constants                                                     #
# --------------------------------------------------------------------------- #


def test_the_outcomes_are_four_and_unread_is_not_one():
    assert operator_decide.OUTCOMES == ("do", "studio", "ceo", "done-already")
    assert "unread" not in operator_decide.OUTCOMES


def test_the_spellings():
    assert operator_decide.LOG_TAG == "operator-agent:"
    assert operator_decide.GUARD_RETURN_TAG == "🚧 lane-guard-returned"
    assert operator_decide.UNREAD_LIMIT == "not read by the model on this pass"
    assert operator_decide.UNREAD_NO_ANSWER == "the model wrote no answer for it"
    assert operator_decide.HELD_NOT_READ == "the as-built document was not read"
    assert operator_decide.HELD_NO_QUOTE == "no quote from the as-built document"
    assert operator_decide.HELD_GO_LIVE == "the card is the studio go-live itself"


def test_the_decision_fields_in_order():
    names = [f.name for f in operator_decide.dataclasses.fields(operator_decide.Decision)]
    assert names == ["card", "repo", "lane", "outcome", "reason", "step", "act", "deploy",
                     "evidence", "host_cannot", "finding", "question", "recommendation",
                     "choices", "held", "fingerprint"]


def test_a_decision_round_trips_through_json():
    card = _synthetic(description="Turn on branch protection for the main branch.")
    decision = operator_decide.decide(card, _answer("DRE-9001"), _text(card))
    written = json.loads(json.dumps(decision.to_json()))
    assert written["choices"] is None
    assert operator_decide.Decision.from_json(written) == decision


# --------------------------------------------------------------------------- #
# no answer, no decision                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ident", ["DRE-8173", "DRE-7078", "DRE-7579"])
def test_a_missing_answer_file_decides_nothing(tmp_path, ident):
    answer = operator_decide.read_answer(tmp_path / f"{ident}.json")
    assert answer is None
    card = _card(ident)
    assert operator_decide.decide(card, answer, _text(card)) is None


@pytest.mark.parametrize("ident", ["DRE-8173", "DRE-7078", "DRE-7579"])
@pytest.mark.parametrize("body", ["{not json", "[]", '"do"', ""])
def test_a_malformed_answer_file_decides_nothing(tmp_path, ident, body):
    answer = operator_decide.read_answer(_write(tmp_path / f"{ident}.json", body))
    assert answer is None
    card = _card(ident)
    assert operator_decide.decide(card, answer, _text(card)) is None


@pytest.mark.parametrize("ident", ["DRE-8173", "DRE-7078", "DRE-7579"])
@pytest.mark.parametrize("outcome", ["unread", "spent", "CEO", "", None])
def test_an_outcome_outside_the_four_decides_nothing(tmp_path, ident, outcome):
    raw = _answer(ident, outcome)
    assert operator_decide.read_answer(_write(tmp_path / f"{ident}.json", raw)) is None
    card = _card(ident)
    assert operator_decide.decide(card, raw, _text(card)) is None


@pytest.mark.parametrize("ident", ["DRE-8173", "DRE-7078", "DRE-7579"])
@pytest.mark.parametrize("step", ["", "   ", None])
def test_an_empty_step_decides_nothing(tmp_path, ident, step):
    raw = _answer(ident, "do", step)
    assert operator_decide.read_answer(_write(tmp_path / f"{ident}.json", raw)) is None
    card = _card(ident)
    assert operator_decide.decide(card, raw, _text(card)) is None


def test_a_good_answer_file_is_read(tmp_path):
    raw = _answer("DRE-7579", "do", "archive the project")
    assert operator_decide.read_answer(_write(tmp_path / "DRE-7579.json", raw)) == raw


def test_an_answer_naming_another_card_decides_nothing():
    card = _card("DRE-7579")
    assert operator_decide.decide(card, _answer("DRE-7942"), _text(card)) is None


def test_replay_with_no_answer_files_decides_nothing(tmp_path):
    cards = [operator_rules.fixture_card(r) for r in _records().values()]
    assert len(cards) == 51
    for card in cards:
        answer = operator_decide.read_answer(tmp_path / f"{card['card']}.json")
        assert operator_decide.decide(card, answer, _text(card)) is None, card["card"]


# --------------------------------------------------------------------------- #
# the replay, every card answered `do`                                         #
# --------------------------------------------------------------------------- #


def test_replay_with_a_do_answer_for_every_card():
    got = {}
    for record in _records().values():
        card = operator_rules.fixture_card(record)
        step = f"the step {card['card']} names"
        decision = operator_decide.decide(card, _answer(card["card"], "do", step), _text(card))
        assert decision is not None and decision.step == step, card["card"]
        got[card["card"]] = decision

    for ident in ("DRE-7579", "DRE-7942", "DRE-7978"):
        assert got[ident].outcome == "do", ident
        assert got[ident].step == f"the step {ident} names"

    assert got["DRE-7328"].outcome == "studio"
    assert got["DRE-7328"].held == "the as-built document was not read"

    for ident in ("DRE-8173", "DRE-8227", "DRE-7078"):
        assert got[ident].outcome == "ceo", ident

    # Precision: the full set each fixed route sends, and nothing more.
    assert {i: d.act for i, d in got.items() if d.outcome == "ceo"} == CEO_BY_RULE
    assert {i for i, d in got.items() if d.outcome == "studio"} == STUDIO_BY_RULE
    assert all(d.choices is None for d in got.values())
    assert not [i for i, d in got.items() if d.outcome == "done-already"]


# --------------------------------------------------------------------------- #
# rule 1 — done-already                                                        #
# --------------------------------------------------------------------------- #


def test_dre_4434_quoting_its_comment_decides_done_already():
    card = _card("DRE-4434")
    assert ATLAS_LIVE in [c["body"] for c in card["comments"]]
    answer = _answer("DRE-4434", "done-already", "bring the Atlas portal live",
                     evidence=[ATLAS_LIVE])
    decision = operator_decide.decide(card, answer, _text(card))
    assert decision.outcome == "done-already"
    assert decision.evidence == [ATLAS_LIVE]
    assert decision.act == ""


def test_dre_4434_quoting_words_not_in_the_card_decides_do():
    card = _card("DRE-4434")
    answer = _answer("DRE-4434", "done-already", "bring the Atlas portal live",
                     evidence=["Atlas went live last spring."])
    decision = operator_decide.decide(card, answer, _text(card))
    assert decision.outcome == "do"
    assert decision.step == "bring the Atlas portal live"


def test_done_already_with_no_quote_does_not_hold():
    card = _card("DRE-4434")
    answer = _answer("DRE-4434", "done-already", "bring the Atlas portal live", evidence=[])
    assert operator_decide.decide(card, answer, _text(card)).outcome == "do"


def test_an_empty_quote_proves_nothing():
    card = _card("DRE-4434")
    answer = _answer("DRE-4434", "done-already", "bring the Atlas portal live",
                     evidence=["   "])
    assert operator_decide.decide(card, answer, _text(card)).outcome == "do"


def test_every_quote_must_be_in_the_card():
    card = _card("DRE-4434")
    answer = _answer("DRE-4434", "done-already", "bring the Atlas portal live",
                     evidence=[ATLAS_LIVE, "And every grant is written."])
    assert operator_decide.decide(card, answer, _text(card)).outcome == "do"


def test_a_quote_matches_with_whitespace_collapsed():
    card = _card("DRE-4434")
    answer = _answer("DRE-4434", "done-already", "bring the Atlas portal live",
                     evidence=["Atlas  is live,\n2026-09-21   11:07 PT."])
    assert operator_decide.decide(card, answer, _text(card)).outcome == "done-already"


def test_the_quote_is_checked_against_the_card_text_given_and_nothing_else():
    card = _card("DRE-4434")
    answer = _answer("DRE-4434", "done-already", "bring the Atlas portal live",
                     evidence=[ATLAS_LIVE])
    with_comment = operator_decide.decide(card, answer, _text(card, comments=True))
    without = operator_decide.decide(card, answer, _text(card, comments=False))
    assert with_comment.outcome == "done-already"
    assert without.outcome == "do"


MET = "Branch protection is on for the main branch, and every criterion below is met."


def _branch_card() -> dict:
    return _synthetic(title="agent-bureau: turn on branch protection for the main branch",
                      description=f"Turn on branch protection for the main branch.\n\n{MET}")


def test_done_already_comes_before_a_kept_act():
    card = _branch_card()
    answer = _answer("DRE-9001", "done-already", "turn on branch protection", evidence=[MET])
    decision = operator_decide.decide(card, answer, _text(card))
    assert decision.outcome == "done-already"
    assert decision.evidence == [MET]


def test_done_already_quoting_words_not_in_the_card_falls_to_the_kept_act():
    card = _branch_card()
    answer = _answer("DRE-9001", "done-already", "turn on branch protection",
                     evidence=["Branch protection was turned on last week."])
    decision = operator_decide.decide(card, answer, _text(card))
    assert decision.outcome == "ceo"
    assert decision.act == "github-branch-protection"
    assert decision.choices is None


def test_done_already_on_a_card_closed_through_record_the_outcome_decides_do():
    card = _synthetic(
        title="OPERATOR: set the mode live, then close this card through the console's "
              "Record the outcome",
        description="Turn the mode on, live.\n\nThe mode is on, live, since this morning.")
    answer = _answer("DRE-9001", "done-already", "set the mode live",
                     evidence=["The mode is on, live, since this morning."])
    decision = operator_decide.decide(card, answer, _text(card))
    assert decision.outcome == "do"
    assert decision.step == "close the card through the console's Record the outcome"


def test_done_already_on_dre_7328_decides_do_with_its_close():
    card = _card("DRE-7328")
    answer = _answer("DRE-7328", "done-already", "set the mode live",
                     evidence=["Turn the relay's no-record return on, live."])
    decision = operator_decide.decide(card, answer, _text(card))
    assert decision.outcome == "do"
    assert decision.step == "close the card through the console's Record the outcome"


def test_done_already_on_a_card_closed_by_a_pull_request_merge_decides_do():
    card = _synthetic(description="The card closes on its pull request's merge.\n\n"
                                  "The flag is already set.")
    answer = _answer("DRE-9001", "done-already", "set the flag",
                     evidence=["The flag is already set."])
    decision = operator_decide.decide(card, answer, _text(card))
    assert decision.outcome == "do"
    assert decision.step == "close the card through a pull request's merge"


# --------------------------------------------------------------------------- #
# rule 2 — ceo by rule                                                         #
# --------------------------------------------------------------------------- #


def test_a_do_answer_on_dre_7078_still_decides_ceo():
    card = _card("DRE-7078")
    decision = operator_decide.decide(card, _answer("DRE-7078", "do", "purge and force-push"),
                                      _text(card))
    assert decision.outcome == "ceo"
    assert decision.act == "destructive-go"
    assert decision.choices is None


@pytest.mark.parametrize("outcome", ["do", "studio", "ceo", "done-already"])
def test_no_answer_but_an_accepted_done_already_moves_a_kept_act(tmp_path, outcome):
    card = _card("DRE-8173")
    answer = _answer("DRE-8173", outcome, "grant the App permission",
                     access="the operator's AWS profile", host_cannot=[HOST_QUOTE])
    choices = _write(tmp_path / "DRE-8173.choices.json", _choices())
    decision = operator_decide.decide(card, answer, _text(card),
                                      as_built=operator_rules.host_limits_text(),
                                      choices_file=choices)
    assert (decision.outcome, decision.act) == ("ceo", "github-org-membership")
    assert decision.choices is None


def test_a_ceo_by_rule_writes_its_three_lines_from_the_rule():
    card = _card("DRE-8173")
    decision = operator_decide.decide(card, _answer("DRE-8173", "do", "grant the permission"),
                                      _text(card))
    assert "grant the permission" in decision.finding
    assert "membership of the GitHub organization" in decision.finding
    assert decision.question.count("?") == 1
    assert decision.recommendation == "none given"
    lines = operator_decide.three_lines(decision)
    assert f"💡 Recommendation: none given — {decision.reason}" in lines
    assert planning_escalation.refusal(lines) is None


def test_a_ceo_answer_whose_lines_pass_keeps_them():
    card = _card("DRE-8173")
    answer = _answer("DRE-8173", "ceo", "grant the permission",
                     finding="The roster reader needs one more permission.",
                     question="Shall we grant it to the five apps?",
                     recommendation="Grant it")
    decision = operator_decide.decide(card, answer, _text(card))
    assert decision.finding == "The roster reader needs one more permission."
    assert decision.question == "Shall we grant it to the five apps?"
    assert decision.recommendation == "Grant it"


@pytest.mark.parametrize("bad", [
    {"question": "Shall we grant it? Or wait?"},
    {"question": "We should grant it."},
    {"finding": "See scripts/name_roster.py for the reader."},
    {"recommendation": ""},
])
def test_a_ceo_answer_whose_lines_fail_is_written_from_the_rule(bad):
    card = _card("DRE-8173")
    lines = dict(finding="The roster reader needs one more permission.",
                 question="Shall we grant it to the five apps?", recommendation="Grant it")
    lines.update(bad)
    decision = operator_decide.decide(
        card, _answer("DRE-8173", "ceo", "grant the permission", **lines), _text(card))
    assert decision.recommendation == "none given"
    assert "grant the permission" in decision.finding


# --------------------------------------------------------------------------- #
# rule 3 — ceo as a business choice                                            #
# --------------------------------------------------------------------------- #


def test_a_ceo_answer_on_dre_7579_with_no_choices_file_decides_do(tmp_path):
    card = _card("DRE-7579")
    decision = operator_decide.decide(card, _business_answer("DRE-7579"), _text(card),
                                      choices_file=str(tmp_path / "DRE-7579.choices.json"))
    assert decision.outcome == "do"
    assert decision.choices is None
    assert operator_decide.decide(card, _business_answer("DRE-7579"),
                                  _text(card)).outcome == "do"


@pytest.mark.parametrize("block", [
    _choices(choices=_choices()["choices"][:1]),
    _choices(context="This is not a decision, just a note for the record."),
])
def test_a_ceo_answer_on_dre_7579_with_a_refused_choices_file_decides_do(tmp_path, block):
    card = _card("DRE-7579")
    path = _write(tmp_path / "DRE-7579.choices.json", block)
    assert planning_escalation.screen("x", path, writer="the operator agent") is not None
    decision = operator_decide.decide(card, _business_answer("DRE-7579"), _text(card),
                                      choices_file=path)
    assert decision.outcome == "do"
    assert decision.choices is None


def test_a_screened_business_choice_decides_ceo(tmp_path):
    card = _synthetic()
    path = _write(tmp_path / "DRE-9001.choices.json", _choices())
    decision = operator_decide.decide(card, _business_answer("DRE-9001"), _text(card),
                                      choices_file=path)
    assert decision.outcome == "ceo"
    assert decision.act == "business-choice"
    assert decision.choices == _choices()
    assert decision.held == ""


def test_the_screen_reads_the_path_and_the_rendered_three_lines(tmp_path, monkeypatch):
    calls = []

    def recording_screen(reason, path, writer="the planner"):
        calls.append((reason, path, writer))
        return None

    monkeypatch.setattr(planning_escalation, "screen", recording_screen)
    card = _synthetic()
    path = _write(tmp_path / "DRE-9001.choices.json", _choices())
    decision = operator_decide.decide(card, _business_answer("DRE-9001"), _text(card),
                                      choices_file=path)
    assert decision.act == "business-choice"
    assert len(calls) == 1
    reason, got_path, writer = calls[0]
    assert isinstance(got_path, str) and got_path == path
    assert not isinstance(got_path, dict)
    assert writer == "the operator agent"
    expected = console_escalation.render(console_escalation.Escalation(
        finding="The retired sandbox project is still open on the board.",
        question="Should we archive it now or keep it for a month?",
        recommendation="Archive it now",
        why="keeping or closing the project is a choice about the business"))
    assert reason == expected
    assert operator_decide.three_lines(decision) == expected


# --------------------------------------------------------------------------- #
# rule 4 — studio                                                              #
# --------------------------------------------------------------------------- #


def test_replay_dre_7328_quoting_the_host_limits_decides_studio():
    card = _card("DRE-7328")
    answer = _answer("DRE-7328", "studio", "set the mode on the live relay",
                     access="the operator's AWS profile", host_cannot=[HOST_QUOTE])
    as_built = operator_rules.host_limits_text()
    decision = operator_decide.decide(card, answer, _text(card), as_built=as_built)
    assert decision.outcome == "studio"
    assert decision.held == ""
    assert decision.host_cannot == [HOST_QUOTE]
    assert decision.deploy is True


def _studio_card() -> dict:
    return _synthetic(description="Read the production database row by hand.")


def test_a_studio_answer_keeps_a_quote_in_the_as_built_text():
    card = _studio_card()
    answer = _answer("DRE-9001", "studio", "read the row", access="production database",
                     host_cannot=["Its boundary denies every IAM write."])
    decision = operator_decide.decide(card, answer, _text(card),
                                      as_built=operator_rules.host_limits_text())
    assert (decision.outcome, decision.held) == ("studio", "")
    assert decision.host_cannot == ["Its boundary denies every IAM write."]
    assert decision.deploy is False


def test_a_studio_answer_drops_a_quote_not_in_the_as_built_text():
    card = _studio_card()
    answer = _answer("DRE-9001", "studio", "read the row", access="production database",
                     host_cannot=["Its boundary denies every IAM write.",
                                  "The studio host cannot read a database."])
    decision = operator_decide.decide(card, answer, _text(card),
                                      as_built=operator_rules.host_limits_text())
    assert decision.host_cannot == ["Its boundary denies every IAM write."]


def test_a_studio_answer_with_no_quote_kept_is_held():
    card = _studio_card()
    answer = _answer("DRE-9001", "studio", "read the row", access="production database",
                     host_cannot=["The studio host cannot read a database."])
    decision = operator_decide.decide(card, answer, _text(card),
                                      as_built=operator_rules.host_limits_text())
    assert decision.outcome == "studio"
    assert decision.host_cannot == []
    assert decision.held == "no quote from the as-built document"


def test_a_studio_answer_with_no_as_built_document_is_held():
    card = _studio_card()
    answer = _answer("DRE-9001", "studio", "read the row", access="production database",
                     host_cannot=[HOST_QUOTE])
    decision = operator_decide.decide(card, answer, _text(card), as_built=None)
    assert decision.held == "the as-built document was not read"
    assert operator_decide.decide(card, answer, _text(card), as_built="  ").held == \
        "the as-built document was not read"


def test_a_studio_answer_with_no_access_decides_do():
    card = _studio_card()
    answer = _answer("DRE-9001", "studio", "read the row", access="  ",
                     host_cannot=[HOST_QUOTE])
    decision = operator_decide.decide(card, answer, _text(card),
                                      as_built=operator_rules.host_limits_text())
    assert decision.outcome == "do"


def test_a_studio_answer_saying_deploy_makes_a_deploy_step():
    card = _studio_card()
    answer = _answer("DRE-9001", "studio", "read the row", access="production database",
                     host_cannot=[HOST_QUOTE], deploy=True)
    decision = operator_decide.decide(card, answer, _text(card),
                                      as_built=operator_rules.host_limits_text())
    assert decision.deploy is True


def test_a_studio_rule_on_a_do_answer_decides_studio():
    card = _card("DRE-7223")
    answer = _answer("DRE-7223", "do", "run the production deploy", host_cannot=[HOST_QUOTE])
    decision = operator_decide.decide(card, answer, _text(card),
                                      as_built=operator_rules.host_limits_text())
    assert (decision.outcome, decision.held, decision.deploy) == ("studio", "", True)


@pytest.mark.parametrize("by", ["rule", "answer"])
def test_studio_on_the_go_live_card_itself_is_held(by):
    blocker = operator_rules.load_rules()["studio"]["blocker"]
    assert blocker == "DRE-8582"
    description = ("Deploy with the operator's AWS profile." if by == "rule"
                   else "Make the studio host's machine ready.")
    card = _synthetic(ident=blocker, description=description)
    answer = _answer(blocker, "studio" if by == "answer" else "do", "go live",
                     access="production access", host_cannot=[HOST_QUOTE])
    decision = operator_decide.decide(card, answer, _text(card),
                                      as_built=operator_rules.host_limits_text())
    assert decision.outcome == "studio"
    assert decision.held == "the card is the studio go-live itself"


# --------------------------------------------------------------------------- #
# rule 5 — do                                                                  #
# --------------------------------------------------------------------------- #


def test_everything_else_decides_do_with_the_answers_step():
    card = _card("DRE-7942")
    decision = operator_decide.decide(card, _answer("DRE-7942", "do", "run the check by hand"),
                                      _text(card))
    assert decision.outcome == "do"
    assert decision.step == "run the check by hand"
    assert decision.act == ""
    assert decision.held == ""


def test_a_decision_carries_the_cards_place():
    card = _card("DRE-7579")
    decision = operator_decide.decide(card, _answer("DRE-7579"), _text(card))
    assert (decision.card, decision.lane, decision.repo) == ("DRE-7579", "Hand-work",
                                                             "agent-bureau")
    assert decision.fingerprint == operator_decide.fingerprint(card)


# --------------------------------------------------------------------------- #
# the receipt                                                                  #
# --------------------------------------------------------------------------- #


def _studio_decision(deploy: bool):
    card = _studio_card()
    answer = _answer("DRE-9001", "studio", "read the row", access="production database",
                     host_cannot=[HOST_QUOTE, "Its boundary denies every IAM write."],
                     deploy=deploy)
    return card, operator_decide.decide(card, answer, _text(card),
                                        as_built=operator_rules.host_limits_text())


def _ceo_rule_decision():
    card = _card("DRE-8173")
    return card, operator_decide.decide(card, _answer("DRE-8173", "do", "grant it"),
                                        _text(card))


def _business_decision(tmp_path):
    card = _synthetic()
    path = _write(tmp_path / "DRE-9001.choices.json", _choices())
    return card, operator_decide.decide(card, _business_answer("DRE-9001"), _text(card),
                                        choices_file=path)


def _done_decision():
    card = _card("DRE-4434")
    answer = _answer("DRE-4434", "done-already", "bring the Atlas portal live",
                     evidence=[ATLAS_LIVE])
    return card, operator_decide.decide(card, answer, _text(card))


def _assert_round_trip(card, decision):
    body = operator_decide.receipt_text(decision, NOW)
    lines = body.splitlines()
    assert lines[0] == (f"🛠️ operator-agent: {decision.outcome} — {decision.reason} "
                        "· 09:05 PT")
    assert lines[1] == f"fingerprint: {decision.fingerprint}"
    assert lines[2] == f"step: {decision.step}"
    back = operator_decide.decision_from_receipt(card, body)
    for field in ("outcome", "reason", "fingerprint", "step", "act", "deploy",
                  "evidence", "host_cannot", "choices"):
        assert getattr(back, field) == getattr(decision, field), field
    assert back.card == card["card"]
    return body


def test_receipt_round_trip_studio():
    card, decision = _studio_decision(deploy=False)
    body = _assert_round_trip(card, decision)
    assert f'as built, the studio host cannot: "{HOST_QUOTE}"' in body.splitlines()
    assert body.splitlines()[-1] == "waits on: DRE-8582"
    assert "choices:" not in body


def test_receipt_round_trip_studio_deploy():
    card, decision = _studio_decision(deploy=True)
    body = _assert_round_trip(card, decision)
    assert body.splitlines()[-1] == "waits on: DRE-8582, DRE-8487"
    assert operator_decide.decision_from_receipt(card, body).deploy is True
    assert "choices:" not in body


def test_receipt_round_trip_ceo_by_rule():
    card, decision = _ceo_rule_decision()
    body = _assert_round_trip(card, decision)
    assert "act: github-org-membership" in body.splitlines()
    assert "choices:" not in body


def test_receipt_round_trip_business_choice(tmp_path):
    card, decision = _business_decision(tmp_path)
    body = _assert_round_trip(card, decision)
    choice_lines = [line for line in body.splitlines() if line.startswith("choices: ")]
    assert len(choice_lines) == 1
    assert json.loads(choice_lines[0][len("choices: "):]) == _choices()
    assert operator_decide.decision_from_receipt(card, body).choices == _choices()


@pytest.mark.parametrize("char", [" ", " ", "\x85"])
@pytest.mark.parametrize("field", ["context", "effect", "label"])
def test_receipt_round_trip_business_choice_with_a_line_separator(tmp_path, field, char):
    block = _choices()
    if field == "context":
        block["context"] = f"Two ways{char}to close."
    else:
        block["choices"][0][field] = f"Archive{char}it now"
    card = _synthetic()
    path = _write(tmp_path / "DRE-9001.choices.json", block)
    decision = operator_decide.decide(card, _business_answer("DRE-9001"), _text(card),
                                      choices_file=path)
    assert decision.act == operator_decide.BUSINESS_CHOICE
    body = _assert_round_trip(card, decision)
    assert char not in body
    assert operator_decide.decision_from_receipt(card, body).choices == block


def test_a_business_choice_receipt_with_no_choices_line_reads_as_none(tmp_path):
    card, decision = _business_decision(tmp_path)
    body = "\n".join(line for line in operator_decide.receipt_text(decision, NOW).split("\n")
                     if not line.startswith("choices: "))
    assert "act: business-choice" in body.split("\n")
    assert operator_decide.decision_from_receipt(card, body) is None


def test_a_ceo_receipt_with_choices_and_another_act_reads_as_none():
    card, decision = _ceo_rule_decision()
    body = (operator_decide.receipt_text(decision, NOW) + "\nchoices: "
            + json.dumps(_choices()))
    assert "act: github-org-membership" in body.split("\n")
    assert operator_decide.decision_from_receipt(card, body) is None


def test_receipt_round_trip_done_already():
    card, decision = _done_decision()
    body = _assert_round_trip(card, decision)
    assert f'evidence: "{ATLAS_LIVE}"' in body.splitlines()
    assert body.splitlines()[-1] == "close: done"
    assert "choices:" not in body


def test_no_receipt_line_opens_outcome_or_released_as(tmp_path):
    bodies = [operator_decide.receipt_text(d, NOW) for _, d in (
        _studio_decision(False), _studio_decision(True), _ceo_rule_decision(),
        _business_decision(tmp_path), _done_decision())]
    card = _card("DRE-7942")
    bodies.append(operator_decide.receipt_text(
        operator_decide.decide(card, _answer("DRE-7942"), _text(card)), NOW))
    for body in bodies:
        for line in body.splitlines():
            assert not line.lstrip().startswith(("Outcome:", "Released as")), line


def test_a_held_decision_writes_no_receipt():
    card = _studio_card()
    answer = _answer("DRE-9001", "studio", "read the row", access="production database")
    decision = operator_decide.decide(card, answer, _text(card), as_built=None)
    with pytest.raises(ValueError):
        operator_decide.receipt_text(decision, NOW)


@pytest.mark.parametrize("body", [
    "", "Atlas is live.", "🛠️ operator-agent: unread — nobody read it · 09:05 PT\n"
    "fingerprint: 0123456789ab\nstep: x",
    "🛠️ operator-agent: do — the reason · 09:05 PT\nstep: x",
])
def test_a_body_that_is_not_a_receipt_reads_as_none(body):
    assert operator_decide.decision_from_receipt(_card("DRE-7579"), body) is None


def _record(body, pipeline=True, at="2026-11-02T17:05:00.000Z"):
    return {"body": body, "authored_by_pipeline": pipeline, "created_at": at}


def test_newest_receipt_is_the_newest_the_pipeline_wrote():
    card, decision = _done_decision()
    older = operator_decide.receipt_text(decision, NOW)
    newer = older.replace("09:05 PT", "10:05 PT")
    records = [
        _record(older, at="2026-11-02T17:05:00.000Z"),
        _record("a plain comment", at="2026-11-02T17:30:00.000Z"),
        _record(newer, at="2026-11-02T18:05:00.000Z"),
        _record(newer.replace("10:05", "11:05"), pipeline=False,
                at="2026-11-02T19:05:00.000Z"),
    ]
    got = operator_decide.newest_receipt(records)
    assert got is records[2]
    assert got["created_at"] == "2026-11-02T18:05:00.000Z"


def test_newest_receipt_never_returns_a_persons_comment():
    card, decision = _done_decision()
    forged = operator_decide.receipt_text(decision, NOW)
    assert operator_decide.newest_receipt([_record(forged, pipeline=False)]) is None
    assert operator_decide.newest_receipt([]) is None


# --------------------------------------------------------------------------- #
# the relay's Done guard                                                       #
# --------------------------------------------------------------------------- #


def _guard_thread(guard_at):
    card, decision = _done_decision()
    receipt = _record(operator_decide.receipt_text(decision, NOW), at="2026-11-02T17:05:00.000Z")
    records = [receipt]
    if guard_at is not None:
        guard = _record("🚧 lane-guard-returned: no record in the hour before",
                        pipeline=False, at=guard_at)
        records = [guard, receipt] if guard_at < receipt["created_at"] else [receipt, guard]
    return records, receipt


def test_guard_returned_when_the_return_follows_the_receipt():
    records, receipt = _guard_thread("2026-11-02T17:06:00.000Z")
    assert operator_decide.guard_returned(records, receipt) is True


def test_not_guard_returned_when_the_return_is_older_than_the_receipt():
    records, receipt = _guard_thread("2026-11-02T16:00:00.000Z")
    assert operator_decide.guard_returned(records, receipt) is False


def test_not_guard_returned_when_there_is_no_return():
    records, receipt = _guard_thread(None)
    assert operator_decide.guard_returned(records, receipt) is False


# --------------------------------------------------------------------------- #
# the log lines                                                                #
# --------------------------------------------------------------------------- #


def test_log_line_for_a_decision():
    card = _card("DRE-7579")
    decision = operator_decide.decide(card, _answer("DRE-7579", "do", "archive the project",
                                                    reason="a person archives it"), _text(card))
    assert operator_decide.log_line(decision) == (
        "operator-agent: DRE-7579 → do — a person archives it · step: archive the project")


@pytest.mark.parametrize("action", ["spent", "apply-only", "guard-returned"])
def test_log_line_names_the_action(action):
    card = _card("DRE-7579")
    decision = operator_decide.decide(card, _answer("DRE-7579"), _text(card))
    line = operator_decide.log_line(decision, action)
    assert line.endswith(f" · {action}")
    assert line == operator_decide.log_line(decision) + f" · {action}"


def test_a_studio_decision_logged_spent_still_reads_studio():
    card, decision = _studio_decision(deploy=False)
    back = operator_decide.decision_from_receipt(card, operator_decide.receipt_text(decision, NOW))
    line = operator_decide.log_line(back, "spent")
    assert "→ studio" in line
    assert "→ do" not in line
    assert line.endswith(" · spent")


def test_a_held_studio_line_ends_with_its_reason():
    card = _studio_card()
    answer = _answer("DRE-9001", "studio", "read the row", access="production database")
    decision = operator_decide.decide(card, answer, _text(card), as_built=None)
    assert operator_decide.log_line(decision).endswith(
        " · held: the as-built document was not read")


def test_unread_lines():
    assert operator_decide.unread_line("DRE-7579", operator_decide.UNREAD_LIMIT) == (
        "operator-agent: DRE-7579 unread — not read by the model on this pass")
    line = operator_decide.unread_line(_card("DRE-7579"), operator_decide.UNREAD_NO_ANSWER)
    assert line == "operator-agent: DRE-7579 unread — the model wrote no answer for it"
    for why in (operator_decide.UNREAD_LIMIT, operator_decide.UNREAD_NO_ANSWER):
        assert "→" not in operator_decide.unread_line("DRE-7579", why)


def test_both_lines_open_with_the_log_tag(monkeypatch):
    card = _card("DRE-7579")
    decision = operator_decide.decide(card, _answer("DRE-7579"), _text(card))
    monkeypatch.setattr(operator_decide, "LOG_TAG", "probe-tag:")
    assert operator_decide.log_line(decision).startswith("probe-tag: DRE-7579 → ")
    assert operator_decide.unread_line("DRE-7579", operator_decide.UNREAD_LIMIT).startswith(
        "probe-tag: DRE-7579 unread")


def test_the_log_tag_is_declared_in_proof_reach():
    lines = json.loads(PROOF_REACH.read_text(encoding="utf-8"))["log_lines"]
    entry = next(e for e in lines if e["tag"] == "operator-agent:")
    assert entry["printed_by"] == "scripts/operator_decide.py"
    assert entry["constant"] == "LOG_TAG"
    assert "Decide dry run" in entry["when"]
    assert "unread" in entry["when"] and "left alone" in entry["when"]
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "proof_reach.py"), "check"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


# --------------------------------------------------------------------------- #
# the fingerprint                                                              #
# --------------------------------------------------------------------------- #


def test_the_fingerprint_is_twelve_hex():
    assert re.fullmatch(r"[0-9a-f]{12}", operator_decide.fingerprint(_card("DRE-7579")))


def test_the_fingerprint_changes_with_the_title():
    card = _card("DRE-7579")
    assert operator_decide.fingerprint(card) != operator_decide.fingerprint(
        {**card, "title": card["title"] + " now"})


def test_the_fingerprint_changes_with_the_description():
    card = _card("DRE-7579")
    assert operator_decide.fingerprint(card) != operator_decide.fingerprint(
        {**card, "description": card["description"] + "\n\nOne more line."})


def test_the_fingerprint_ignores_the_lane():
    card = _card("DRE-7579")
    for lane in ("Green Light", "Queued", "In Progress", "Done"):
        assert operator_decide.fingerprint(card) == operator_decide.fingerprint(
            {**card, "lane": lane})


@pytest.mark.parametrize("label", ["hand-step:machine", "hand-step:person", "operator-step",
                                   "no-code", "needs-human"])
def test_the_fingerprint_ignores_a_label_added_or_removed(label):
    card = _card("DRE-7579")
    without = {**card, "labels": [x for x in card["labels"] if x != label]}
    with_it = {**card, "labels": without["labels"] + [label]}
    assert operator_decide.fingerprint(without) == operator_decide.fingerprint(with_it)
    assert operator_decide.fingerprint(card) == operator_decide.fingerprint(with_it)


# --------------------------------------------------------------------------- #
# purity                                                                       #
# --------------------------------------------------------------------------- #


def test_decide_makes_no_linear_or_network_call(tmp_path, monkeypatch):
    class Refuse(types.ModuleType):
        def __getattr__(self, name):
            raise AssertionError(f"linear_ops.{name} was called")

    def refuse(*args, **kwargs):
        raise AssertionError("a network or process call")

    as_built = operator_rules.host_limits_text()
    path = _write(tmp_path / "DRE-9001.choices.json", _choices())
    records = list(_records().values())
    # Undone before the test ends: the suite's own teardown reads linear_ops.
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "linear_ops", Refuse("linear_ops"))
        patch.setattr(urllib.request, "urlopen", refuse)
        patch.setattr(subprocess, "run", refuse)
        for record in records:
            card = operator_rules.fixture_card(record)
            for outcome in operator_decide.OUTCOMES:
                operator_decide.decide(card, _answer(card["card"], outcome, "a step",
                                                     access="production", evidence=["x"],
                                                     host_cannot=[HOST_QUOTE]),
                                       _text(card), as_built=as_built)
        decision = operator_decide.decide(_synthetic(), _business_answer("DRE-9001"), "",
                                          choices_file=path)
    assert decision.act == "business-choice"


def test_nothing_names_a_person_an_email_or_a_sign_in():
    for path in (MODULE, Path(__file__)):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), path
    module = MODULE.read_text(encoding="utf-8")
    assert not re.search(r"(?i)sign[- ]?in\b|signed in|log[- ]?in\b", module)
    assert "the CEO" in module
    assert not re.search(r"\b(?:he|him|his|himself|she|her|hers|herself)\b", module)
