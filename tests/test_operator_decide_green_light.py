"""RED-first tests: the operator agent's two Green Light outcomes (DRE-9047).

A row in `Green Light` gets exactly one of three outcomes — `repair` (a
stopped run, sent back to `Planning` to be re-done), `answer` (a question the
facts answer, sent back with the answer) or `keep` (a genuine decision, which
stays with the CEO). `scripts/operator_decide.py` guards the two that move a
card so a model can never take a genuine decision out of the CEO's queue: a
`keep` is part 1's own `ceo` test run live, and every moving outcome must quote
evidence that verifies — the card's own text, or a file of the bureau-pipeline
checkout.

The rows are `tests/fixtures/operator-decide-green-light-cards.json`, two
syntheses from live cards in the board read's shape, and DRE-8173's committed
record in part 1's replay fixture with its lane set to `Green Light`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_decide_green_light.py -v
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import board_snapshot  # noqa: E402
import linear_ops  # noqa: E402
import operator_decide  # noqa: E402
import operator_rules  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "operator-decide-green-light-cards.json"
REPLAY = ROOT / "tests" / "fixtures" / "operator-agent-board-2026-11-01.json"
WORKFLOW = ".github/workflows/operator-agent.yml"
RULES_TEST = ROOT / "tests" / "test_operator_rules.py"

#: 2026-11-03 17:05 UTC is 09:05 PT.
NOW = datetime(2026, 11, 3, 17, 5, tzinfo=UTC)

GL = "green-light"
RECORD_KEYS = {"serves", "source", "copied", "cut_after", "added", "removed", "withheld",
               "card"}

STOPPED = "the critic produced no result"
STOPPED_PARK = "This card was about to go to the build queue"
ANSWERABLE = "It is planned in detail only when DRE-6587 is Done"
ANSWERABLE_ASKS = ("Should this memory-fix epic wait for the first epic's diagnostic "
                   "reading, or be planned now against today's evidence?")


def _records() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _row(record_id: str) -> dict:
    """A fixture record's board-shaped card as the row the decision reads."""
    card = _records()[record_id]["card"]
    return {
        "card": card["identifier"], "lane": card["state"]["name"], "title": card["title"],
        "description": card["description"],
        "labels": [label["name"] for label in card["labels"]["nodes"]],
        "comments": [{"body": c["body"], "created_at": c["createdAt"]}
                     for c in linear_ops.window_nodes(card["comments"])],
    }


def _thread(record_id: str) -> list:
    """The record's thread as `linear_ops.comment_records` rows, oldest to
    newest."""
    card = _records()[record_id]["card"]
    return [{"body": c["body"], "created_at": c["createdAt"],
             "authored_by_pipeline": c["user"]["id"] == "user-pipeline"}
            for c in linear_ops.window_nodes(card["comments"])]


def _kept_act_row() -> dict:
    record = next(r for r in json.loads(REPLAY.read_text(encoding="utf-8"))
                  if r["card"] == "DRE-8173")
    card = operator_rules.fixture_card(record)
    card["lane"] = "Green Light"
    return card


def _text(card: dict) -> str:
    return "\n\n".join([card["title"], card["description"]]
                       + [c["body"] for c in card.get("comments", [])])


def _answer(card: str, outcome: str, step: str = "re-run the review the critic never finished",
            **extra) -> dict:
    answer = {"card": card, "outcome": outcome, "step": step,
              "reason": "the model read the row", "evidence": [], "answer": ""}
    answer.update(extra)
    return answer


def _write(path: Path, data) -> str:
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(path)


def _decide(record_id: str, answer: dict, **kwargs):
    row = _row(record_id)
    return operator_decide.decide(row, answer, _text(row), group=GL, **kwargs)


def _repair(**extra):
    return _answer("DRE-2498", "repair", evidence=[STOPPED, STOPPED_PARK], **extra)


def _answered(**extra):
    extra.setdefault("evidence", [ANSWERABLE])
    return _answer("DRE-6589", "answer", step=ANSWERABLE_ASKS,
                   answer=("Plan it now.\nIts blocker, DRE-6587, is Done, and the card says "
                           "it is planned when DRE-6587 is Done."), **extra)


def _cron_line() -> str:
    return next(line.strip() for line in (ROOT / WORKFLOW).read_text(encoding="utf-8")
                .splitlines() if "cron:" in line)


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


def _business(card: str) -> dict:
    return _answer(
        card, "ceo", "choose whether the sandbox project stays",
        reason="keeping or closing the project is a choice about the business",
        finding="The retired sandbox project is still open on the board.",
        question="Should we archive it now or keep it for a month?",
        recommendation="Archive it now")


def _round_trip(row: dict, decision) -> str:
    body = operator_decide.receipt_text(decision, NOW)
    lines = body.splitlines()
    assert lines[0] == (f"🛠️ operator-agent: {decision.outcome} — {decision.reason} "
                        "· 09:05 PT")
    assert lines[1] == f"fingerprint: {decision.fingerprint}"
    assert lines[2] == f"step: {decision.step}"
    back = operator_decide.decision_from_receipt(row, body)
    assert back is not None, body
    for field in ("outcome", "reason", "fingerprint", "step", "act", "evidence",
                  "finding", "choices"):
        assert getattr(back, field) == getattr(decision, field), field
    assert back.answer == decision.answer
    assert back.card == row["card"]
    for line in lines:
        assert not line.lstrip().startswith(("Outcome:", "Released as")), line
    return body


# --------------------------------------------------------------------------- #
# the contract's names                                                         #
# --------------------------------------------------------------------------- #


def test_the_contract_names():
    d = operator_decide
    assert (d.REPAIR, d.ANSWER, d.KEEP) == ("repair", "answer", "keep")
    assert d.GREEN_LIGHT_OUTCOMES == ("repair", "answer", "keep")
    assert d.MOVING_OUTCOMES == ("repair", "answer")
    assert (d.WAITING_GROUP, d.GREEN_LIGHT_GROUP) == ("waiting", "green-light")
    assert "answer" in d.ANSWER_KEYS
    assert d.MAX_PATH_EVIDENCE == 3
    assert d.CHECKOUT_DIR == ".bureau-pipeline"
    assert d.KEEP_REASON == "a genuine decision — it stays with the CEO"
    assert d.GREEN_LIGHT_DESTINATION == "Planning"
    assert d.MOVED_LINE == "moved: Green Light → Planning"
    assert d.STAYS_LINE == "stays: Green Light"
    assert d.ANSWER_PREFIX == "answer: "


def test_the_waiting_outcomes_stay_the_four():
    assert operator_decide.OUTCOMES == ("do", "studio", "ceo", "done-already")


def test_root_is_operator_rules_root():
    assert operator_decide.ROOT is operator_rules.ROOT
    assert operator_decide.ROOT == ROOT


def test_decide_defaults_to_the_waiting_group():
    signature = inspect.signature(operator_decide.decide)
    assert list(signature.parameters) == ["card", "answer", "card_text", "as_built", "rules",
                                          "choices_file", "group"]
    assert signature.parameters["group"].default == operator_decide.WAITING_GROUP


# --------------------------------------------------------------------------- #
# repair and answer, from an answer file, read back                            #
# --------------------------------------------------------------------------- #


def test_neither_fixture_row_matches_a_fixed_rule():
    for record_id in ("gl-stopped-run", "gl-answerable"):
        row = _row(record_id)
        assert row["lane"] == "Green Light"
        assert operator_rules.rule_outcome(row["title"], row["description"]) is None


def test_a_repair_on_the_stopped_run_reads_back(tmp_path):
    path = _write(tmp_path / "DRE-2498.json", _repair())
    answer = operator_decide.read_answer(path, group=GL)
    assert answer is not None
    row = _row("gl-stopped-run")
    decision = operator_decide.decide(row, answer, _text(row), group=GL)
    assert decision.outcome == "repair"
    assert decision.step == "re-run the review the critic never finished"
    assert decision.evidence == [STOPPED, STOPPED_PARK]
    assert decision.answer == ""
    assert decision.fingerprint == operator_decide.fingerprint(row)
    body = _round_trip(row, decision)
    lines = body.splitlines()
    assert lines[3:] == [f'evidence: "{STOPPED}"', f'evidence: "{STOPPED_PARK}"',
                         "moved: Green Light → Planning"]


def test_an_answer_on_the_answerable_row_reads_back(tmp_path):
    path = _write(tmp_path / "DRE-6589.json", _answered())
    answer = operator_decide.read_answer(path, group=GL)
    row = _row("gl-answerable")
    decision = operator_decide.decide(row, answer, _text(row), group=GL)
    assert decision.outcome == "answer"
    assert decision.step == ANSWERABLE_ASKS
    assert decision.answer == ("Plan it now. Its blocker, DRE-6587, is Done, and the card "
                               "says it is planned when DRE-6587 is Done.")
    assert decision.evidence == [ANSWERABLE]
    body = _round_trip(row, decision)
    assert body.splitlines()[3:] == [
        f"answer: {decision.answer}", f'evidence: "{ANSWERABLE}"',
        "moved: Green Light → Planning"]


def test_the_waiting_read_of_an_answer_file_is_unchanged(tmp_path):
    path = _write(tmp_path / "DRE-6589.json", _answered())
    assert operator_decide.read_answer(path) is None


def test_the_log_lines():
    repair = _decide("gl-stopped-run", _repair())
    assert operator_decide.log_line(repair) == (
        "operator-agent: DRE-2498 → repair — the model read the row · step: "
        "re-run the review the critic never finished")
    answer = _decide("gl-answerable", _answered())
    assert operator_decide.log_line(answer).startswith("operator-agent: DRE-6589 → answer — ")
    keep = operator_decide.decide(_kept_act_row(), _answer("DRE-8173", "repair"),
                                  _text(_kept_act_row()), group=GL)
    assert operator_decide.log_line(keep).startswith(
        "operator-agent: DRE-8173 → keep — a genuine decision — it stays with the CEO · step: ")


def test_a_decision_round_trips_through_json():
    decision = _decide("gl-answerable", _answered(
        evidence=[{"path": WORKFLOW, "quote": _cron_line()}]))
    back = operator_decide.Decision.from_json(json.loads(json.dumps(decision.to_json())))
    assert back == decision
    assert back.answer == decision.answer


# --------------------------------------------------------------------------- #
# the guards                                                                   #
# --------------------------------------------------------------------------- #


def test_a_quote_not_in_the_card_text_is_no_decision():
    assert _decide("gl-stopped-run", _repair(evidence=[STOPPED, "the critic approved it"])) \
        is None
    assert _decide("gl-answerable", _answered(evidence=["DRE-6587 was canceled"])) is None


def test_a_quote_matches_with_whitespace_collapsed():
    spaced = "the   critic\nproduced no\tresult"
    decision = _decide("gl-stopped-run", _repair(evidence=[spaced]))
    assert decision.evidence == [STOPPED]


@pytest.mark.parametrize("path", [
    "/etc/hostname", "../README.md", ".bureau-pipeline/../README.md",
    "scripts/../../README.md", ".bureau-pipeline/", "", "scripts",
    "no/such/file.txt",
])
def test_a_file_outside_the_checkout_or_with_dot_dot_is_no_decision(path):
    item = {"path": path, "quote": "a"}
    assert _decide("gl-answerable", _answered(evidence=[item])) is None
    assert _decide("gl-stopped-run", _repair(evidence=[STOPPED, item])) is None


def test_a_symlink_out_of_the_checkout_is_no_decision(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("the quote is here\n", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)
    (root / "inside.txt").write_text("the quote is here\n", encoding="utf-8")
    monkeypatch.setattr(operator_decide, "ROOT", root)
    assert _decide("gl-answerable", _answered(
        evidence=[{"path": "link.txt", "quote": "the quote is here"}])) is None
    assert _decide("gl-answerable", _answered(
        evidence=[{"path": "inside.txt", "quote": "the quote is here"}])) is not None


def test_a_file_quote_not_in_that_file_is_no_decision():
    item = {"path": WORKFLOW, "quote": "cron: \"0 0 * * *\" — never written there"}
    assert _decide("gl-answerable", _answered(evidence=[item])) is None


def test_four_file_items_are_no_decision_and_three_are_one():
    item = {"path": WORKFLOW, "quote": _cron_line()}
    assert _decide("gl-answerable", _answered(evidence=[item] * 4)) is None
    assert _decide("gl-stopped-run", _repair(evidence=[STOPPED] + [item] * 4)) is None
    assert _decide("gl-answerable", _answered(evidence=[item] * 3)) is not None


@pytest.mark.parametrize("item", [
    {"path": WORKFLOW}, {"quote": "cron"}, {"path": WORKFLOW, "quote": 3},
    {"path": 3, "quote": "cron"}, {"path": WORKFLOW, "quote": "cron", "line": 150},
    ["cron"], 7,
])
def test_a_malformed_evidence_item_is_no_decision(item):
    assert _decide("gl-answerable", _answered(evidence=[item])) is None


def test_repair_needs_evidence():
    assert _decide("gl-stopped-run", _repair(evidence=[])) is None
    assert _decide("gl-stopped-run", _answer("DRE-2498", "repair")) is None


@pytest.mark.parametrize("step", ["", "   ", None])
def test_repair_and_answer_need_a_step(step):
    assert _decide("gl-stopped-run", _answer("DRE-2498", "repair", step,
                                             evidence=[STOPPED])) is None
    assert _decide("gl-answerable", _answer("DRE-6589", "answer", step, evidence=[ANSWERABLE],
                                            answer="Plan it now.")) is None


@pytest.mark.parametrize("text", ["", "  \n ", None])
def test_answer_needs_an_answer(text):
    assert _decide("gl-answerable", _answer("DRE-6589", "answer", ANSWERABLE_ASKS,
                                            evidence=[ANSWERABLE], answer=text)) is None


def test_answer_needs_evidence():
    assert _decide("gl-answerable", _answered(evidence=[])) is None


def test_a_missing_answer_is_no_decision():
    row = _row("gl-stopped-run")
    assert operator_decide.decide(row, None, _text(row), group=GL) is None


@pytest.mark.parametrize("outcome", ["do", "studio", "done-already"])
def test_a_waiting_outcome_on_a_green_light_row_is_no_decision(outcome):
    for record_id, card in (("gl-stopped-run", "DRE-2498"), ("gl-answerable", "DRE-6589")):
        answer = _answer(card, outcome, evidence=[STOPPED if card == "DRE-2498" else ANSWERABLE],
                         access="production database")
        assert _decide(record_id, answer) is None, (record_id, outcome)


def test_a_model_keep_with_no_kept_act_is_no_decision():
    assert _decide("gl-stopped-run", _answer("DRE-2498", "keep", evidence=[STOPPED])) is None


@pytest.mark.parametrize("outcome", ["repair", "answer", "keep"])
def test_a_green_light_outcome_never_comes_out_of_a_waiting_row(outcome):
    for record_id, card in (("gl-stopped-run", "DRE-2498"), ("gl-answerable", "DRE-6589")):
        row = _row(record_id)
        answer = _answer(card, outcome, evidence=[STOPPED if card == "DRE-2498" else ANSWERABLE],
                         answer="Plan it now.")
        assert operator_decide.decide(row, answer, _text(row)) is None
        assert operator_decide.decide(row, answer, _text(row), group="waiting") is None


def test_a_ceo_on_a_waiting_row_is_never_keep(tmp_path):
    path = _write(tmp_path / "DRE-2498.choices.json", _choices())
    row = _row("gl-stopped-run")
    decision = operator_decide.decide(row, _business("DRE-2498"), _text(row), choices_file=path)
    assert (decision.outcome, decision.act) == ("ceo", "business-choice")
    kept = _kept_act_row()
    decision = operator_decide.decide(kept, _answer("DRE-8173", "do"), _text(kept))
    assert (decision.outcome, decision.act) == ("ceo", "github-org-membership")


def test_an_unknown_group_is_refused():
    row = _row("gl-stopped-run")
    with pytest.raises(ValueError):
        operator_decide.decide(row, _repair(), _text(row), group="Green Light")


# --------------------------------------------------------------------------- #
# file evidence over the real workflow file                                    #
# --------------------------------------------------------------------------- #


def test_file_evidence_over_the_real_workflow_file():
    quote = _cron_line()
    assert quote.startswith("- cron: ")
    seen = operator_decide.decide(
        _row("gl-answerable"), _answered(evidence=[
            {"path": f".bureau-pipeline/{WORKFLOW}", "quote": quote}]),
        _text(_row("gl-answerable")), group=GL)
    bare = _decide("gl-answerable", _answered(evidence=[{"path": WORKFLOW, "quote": quote}]))
    assert seen is not None and seen.outcome == "answer"
    assert seen == bare
    assert seen.evidence == [{"path": WORKFLOW, "quote": quote}]
    row = _row("gl-answerable")
    for decision in (seen, bare):
        body = _round_trip(row, decision)
        assert f'evidence: {WORKFLOW}: "{quote}"' in body.splitlines()
        back = operator_decide.decision_from_receipt(row, body)
        assert back.evidence == [{"path": WORKFLOW, "quote": quote}]


@pytest.mark.parametrize("path", [".bureau-pipeline/../README.md", "/etc/hostname",
                                  ".bureau-pipeline/"])
def test_file_evidence_paths_the_guard_refuses(path):
    assert _decide("gl-answerable", _answered(
        evidence=[{"path": path, "quote": _cron_line()}])) is None


def test_the_prefix_is_removed_once():
    quote = _cron_line()
    assert _decide("gl-answerable", _answered(evidence=[
        {"path": f".bureau-pipeline/.bureau-pipeline/no-such-dir/{WORKFLOW}",
         "quote": quote}])) is None


def test_a_mixed_receipt_reads_back_in_order():
    quote = _cron_line()
    decision = _decide("gl-stopped-run", _repair(evidence=[
        STOPPED, {"path": f".bureau-pipeline/{WORKFLOW}", "quote": quote}]))
    body = _round_trip(_row("gl-stopped-run"), decision)
    assert body.splitlines()[3:5] == [f'evidence: "{STOPPED}"',
                                      f'evidence: {WORKFLOW}: "{quote}"']


# --------------------------------------------------------------------------- #
# keep — a genuine decision stays with the CEO                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("outcome", ["repair", "answer", "ceo", "keep", "do", "done-already"])
def test_a_kept_act_on_a_green_light_row_is_keep(outcome):
    row = _kept_act_row()
    text = _text(row)
    quote = text.split("\n")[0]
    answer = _answer("DRE-8173", outcome, evidence=[quote], answer="Grant it now.")
    decision = operator_decide.decide(row, answer, text, group=GL)
    assert decision.outcome == "keep", outcome
    assert decision.act == "github-org-membership"
    assert decision.reason == operator_decide.KEEP_REASON
    assert (decision.finding, decision.question, decision.recommendation) == ("", "", "")
    body = _round_trip(row, decision)
    lines = body.splitlines()
    assert lines[3:] == ["act: github-org-membership", "stays: Green Light"]
    assert not any(re.match(r"(?:🔎 )?(?:Finding|Question|Recommendation)\b", line)
                   for line in lines)
    assert operator_decide.decision_from_receipt(_row("gl-stopped-run"), body).outcome == "keep"


def test_a_screened_ceo_on_a_green_light_row_is_keep(tmp_path):
    path = _write(tmp_path / "DRE-2498.choices.json", _choices())
    decision = _decide("gl-stopped-run", _business("DRE-2498"), choices_file=path)
    assert decision.outcome == "keep"
    assert decision.act == "business-choice"
    assert decision.choices is None
    body = _round_trip(_row("gl-stopped-run"), decision)
    assert body.splitlines()[3:] == ["act: business-choice", "stays: Green Light"]
    assert "choices:" not in body


@pytest.mark.parametrize("block", [
    _choices(choices=_choices()["choices"][:1]),
    _choices(why="This is not a real decision, just housekeeping."),
    "{not json",
])
def test_a_ceo_whose_choices_the_screen_refuses_is_no_decision(tmp_path, block):
    path = _write(tmp_path / "DRE-2498.choices.json", block)
    assert _decide("gl-stopped-run", _business("DRE-2498"), choices_file=path) is None


def test_a_ceo_with_no_choices_file_is_no_decision(tmp_path):
    assert _decide("gl-stopped-run", _business("DRE-2498")) is None
    assert _decide("gl-stopped-run", _business("DRE-2498"),
                   choices_file=str(tmp_path / "none.json")) is None


def test_the_screen_is_the_one_decide_uses(tmp_path, monkeypatch):
    seen = []

    def screen(reason, path, writer="the planner"):
        seen.append((path, writer))
        return None

    monkeypatch.setattr(operator_decide.planning_escalation, "screen", screen)
    path = _write(tmp_path / "DRE-2498.choices.json", _choices())
    assert _decide("gl-stopped-run", _business("DRE-2498"), choices_file=path).outcome == "keep"
    assert seen == [(path, operator_decide.WRITER)]


# --------------------------------------------------------------------------- #
# receipts that do not read back                                               #
# --------------------------------------------------------------------------- #


def _body(decision) -> list:
    return operator_decide.receipt_text(decision, NOW).splitlines()


@pytest.mark.parametrize("drop", ["moved: ", "evidence: "])
def test_a_repair_receipt_missing_a_line_reads_as_none(drop):
    lines = _body(_decide("gl-stopped-run", _repair()))
    body = "\n".join(line for line in lines if not line.startswith(drop))
    assert operator_decide.decision_from_receipt(_row("gl-stopped-run"), body) is None


@pytest.mark.parametrize("drop", ["moved: ", "evidence: ", "answer: "])
def test_an_answer_receipt_missing_a_line_reads_as_none(drop):
    lines = _body(_decide("gl-answerable", _answered()))
    body = "\n".join(line for line in lines if not line.startswith(drop))
    assert operator_decide.decision_from_receipt(_row("gl-answerable"), body) is None


@pytest.mark.parametrize("drop", ["stays: ", "act: "])
def test_a_keep_receipt_missing_a_line_reads_as_none(drop):
    row = _kept_act_row()
    lines = _body(operator_decide.decide(row, _answer("DRE-8173", "ceo"), _text(row), group=GL))
    body = "\n".join(line for line in lines if not line.startswith(drop))
    assert operator_decide.decision_from_receipt(row, body) is None


def test_a_keep_receipt_carrying_moved_reads_as_none():
    row = _kept_act_row()
    body = operator_decide.receipt_text(
        operator_decide.decide(row, _answer("DRE-8173", "ceo"), _text(row), group=GL), NOW)
    assert operator_decide.decision_from_receipt(row, body + "\nmoved: Green Light → Planning") \
        is None


def test_a_done_already_receipt_with_file_evidence_reads_as_none():
    body = ("🛠️ operator-agent: done-already — met · 09:05 PT\nfingerprint: 0123456789ab\n"
            f'step: x\nevidence: {WORKFLOW}: "cron"\nclose: done')
    assert operator_decide.decision_from_receipt(_row("gl-stopped-run"), body) is None


# --------------------------------------------------------------------------- #
# keep_stands — one reader, used twice                                         #
# --------------------------------------------------------------------------- #


def _keep_record(fingerprint: str, at: str) -> dict:
    """A pipeline-authored `keep` receipt for DRE-2498 over `fingerprint`."""
    kept = operator_decide.decide(_kept_act_row(), _answer("DRE-8173", "ceo"),
                                  _text(_kept_act_row()), group=GL)
    body = operator_decide.receipt_text(
        operator_decide.dataclasses.replace(kept, card="DRE-2498", fingerprint=fingerprint), NOW)
    return {"body": body, "authored_by_pipeline": True, "created_at": at}


def test_keep_stands_over_the_stopped_run():
    fingerprint = operator_decide.fingerprint(_row("gl-stopped-run"))
    thread = _thread("gl-stopped-run")
    assert operator_decide.keep_stands(thread, fingerprint) is False
    thread.append(_keep_record(fingerprint, "2026-11-03T17:05:00.000Z"))
    assert operator_decide.keep_stands(thread, fingerprint) is True
    assert operator_decide.keep_stands(thread, "0123456789ab") is False
    thread.append({"body": "🧹 hygiene: a note", "authored_by_pipeline": False,
                   "created_at": "2026-11-03T18:00:00.000Z"})
    assert operator_decide.keep_stands(thread, fingerprint) is False
    thread.append(_keep_record(fingerprint, "2026-11-03T18:05:00.000Z"))
    assert operator_decide.keep_stands(thread, fingerprint) is True


@pytest.mark.parametrize("author", [True, False])
def test_keep_stands_is_false_once_any_comment_follows(author):
    fingerprint = operator_decide.fingerprint(_row("gl-stopped-run"))
    thread = _thread("gl-stopped-run") + [
        _keep_record(fingerprint, "2026-11-03T17:05:00.000Z"),
        {"body": "a comment", "authored_by_pipeline": author,
         "created_at": "2026-11-03T17:06:00.000Z"}]
    assert operator_decide.keep_stands(thread, fingerprint) is False


def test_keep_stands_reads_the_receipt_with_its_act_trailer():
    fingerprint = operator_decide.fingerprint(_row("gl-stopped-run"))
    record = _keep_record(fingerprint, "2026-11-03T17:05:00.000Z")
    record["body"] = operator_decide.pipeline_act.receipt("operator-agent", record["body"])
    assert operator_decide.keep_stands(_thread("gl-stopped-run") + [record], fingerprint)


def test_keep_stands_never_reads_a_persons_receipt():
    fingerprint = operator_decide.fingerprint(_row("gl-stopped-run"))
    record = _keep_record(fingerprint, "2026-11-03T17:05:00.000Z")
    record["authored_by_pipeline"] = False
    assert operator_decide.keep_stands(_thread("gl-stopped-run") + [record], fingerprint) \
        is False


def test_keep_stands_is_false_over_a_newest_receipt_that_is_not_keep():
    row = _row("gl-stopped-run")
    body = operator_decide.receipt_text(_decide("gl-stopped-run", _repair()), NOW)
    thread = _thread("gl-stopped-run") + [
        {"body": body, "authored_by_pipeline": True, "created_at": "2026-11-03T17:05:00.000Z"}]
    assert operator_decide.keep_stands(thread, operator_decide.fingerprint(row)) is False
    assert operator_decide.keep_stands([], operator_decide.fingerprint(row)) is False


# --------------------------------------------------------------------------- #
# the fixture                                                                  #
# --------------------------------------------------------------------------- #


def _rules_test():
    spec = importlib.util.spec_from_file_location("_operator_rules_tests", RULES_TEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_fixture_holds_the_two_records_with_the_eight_keys():
    records = _records()
    assert set(records) == {"gl-stopped-run", "gl-answerable"}
    for record_id, record in records.items():
        assert set(record) == RECORD_KEYS, record_id
        assert record["card"]["state"] == {"name": "Green Light"}
        assert re.fullmatch(r"\d{4}-\d\d-\d\d", record["copied"])
        for key in ("added", "removed", "withheld"):
            assert isinstance(record[key], list) and record[key], (record_id, key)
    assert (records["gl-stopped-run"]["serves"], records["gl-stopped-run"]["source"]) == \
        ("repair", "DRE-2498")
    assert (records["gl-answerable"]["serves"], records["gl-answerable"]["source"]) == \
        ("answer", "DRE-6589")


def test_each_record_is_in_the_board_reads_shape():
    for record in _records().values():
        card = record["card"]
        for key in ("id", "identifier", "title", "description", "createdAt", "updatedAt",
                    "state", "labels", "parent", "children", "comments", "relations",
                    "inverseRelations", "history"):
            assert key in card, key
            assert re.search(rf"\b{key}\b", board_snapshot.CARD_QUERY), key
        for comment in card["comments"]["nodes"]:
            assert set(comment) == {"body", "createdAt", "user"}
            assert comment["user"]["id"] in ("user-pipeline", "user-person")


def test_each_thread_is_cut_after_its_escalation():
    records = _records()
    for record_id, at in (("gl-stopped-run", "2026-10-13T15:45:46.817Z"),
                          ("gl-answerable", "2026-10-10T09:58:16.475Z")):
        record = records[record_id]
        assert record["cut_after"] == at
        newest = linear_ops.window_nodes(record["card"]["comments"])[-1]
        assert newest["createdAt"] == at
        assert newest["body"].startswith("🙋 planning-escalation:")
        assert newest["user"]["id"] == "user-pipeline"
        assert all(c["createdAt"] <= at for c in record["card"]["comments"]["nodes"])
    stopped = linear_ops.window_nodes(records["gl-stopped-run"]["card"]["comments"])[-1]
    assert "🔎 Finding: the critic produced no result" in stopped["body"]


def test_neither_newest_comment_carries_an_escalation_choices_block():
    for record_id, record in _records().items():
        newest = linear_ops.window_nodes(record["card"]["comments"])[-1]
        assert "escalation-choices" not in newest["body"], record_id
        assert "```" not in newest["body"], record_id


def test_the_fixture_is_held_to_part_ones_limits(monkeypatch):
    rules_test = _rules_test()
    monkeypatch.setattr(rules_test, "FIXTURE", FIXTURE)
    rules_test.test_the_fixture_names_no_address_link_or_account_number()
    rules_test.test_the_fixture_names_the_ceo_and_no_one_else()


def test_part_ones_limits_do_catch_a_leak(tmp_path, monkeypatch):
    rules_test = _rules_test()
    leaky = tmp_path / "leaky.json"
    leaky.write_text(FIXTURE.read_text(encoding="utf-8").replace(
        "the CEO", "person" + "@" + "elsewhere.test", 1), encoding="utf-8")
    monkeypatch.setattr(rules_test, "FIXTURE", leaky)
    with pytest.raises(AssertionError):
        rules_test.test_the_fixture_names_no_address_link_or_account_number()


def test_the_fixture_names_no_linear_user_but_the_two_stand_ins():
    text = FIXTURE.read_text(encoding="utf-8")
    users = set(re.findall(r'"user": \{\s*"id": "([^"]*)"', text))
    assert users <= {"user-pipeline", "user-person"} and users
    assert "the CEO" in text


# --------------------------------------------------------------------------- #
# nothing names a person                                                       #
# --------------------------------------------------------------------------- #


def test_this_test_and_its_fixture_name_no_person_email_or_sign_in():
    for path in (Path(__file__), FIXTURE):
        # The two patterns' own lines are set aside: each spells what it refuses.
        text = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                         if "re.search(" not in line)
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), path
        assert not re.search(r"(?i)sign[- ]?in\b|signed in|log[- ]?in\b", text), path
        assert "the CEO" in text, path
