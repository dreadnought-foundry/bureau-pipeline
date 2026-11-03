"""RED-first tests: a `do` card's claim and receipt, read back (DRE-8923, epic DRE-8738).

`scripts/operator_decide.py` composes the three comments a `do` card can get —
the claim posted before any command runs, the receipt posted after, and the
close-failure line — and reads each back with `do_ending_from_receipt`.
`scripts/operator_agent.py`'s `select` reads the newest one on the next pass:
a claim with no receipt after it, a `failed:` or `close: none named` receipt
and a close-failure line are `spent`; a `close:` receipt is `close-only` until
the relay's lane guard returns it, then `guard-returned`; a `continues:`
receipt is `spent` before its time and `continue` at or after it.

The board is the stub `tests/test_operator_agent.py` builds. Every card here
is synthetic.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_do_receipt.py -v
"""
from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import operator_agent  # noqa: E402
import operator_decide  # noqa: E402
from test_operator_agent import (  # noqa: E402
    Board, _by_card, _decide, _fp, _record, _select, _tag_lines,
)

#: 2026-11-02 17:05 UTC is 09:05 PT.
NOW = datetime(2026, 11, 2, 17, 5, tzinfo=UTC)
RECEIPT_AT = "2026-11-02T17:05:00.000Z"
AFTER_RECEIPT = "2026-11-02T18:00:00.000Z"
HOUR = "2026-11-02T19:00:00Z"
PR_URL = "https://github.com/dreadnought-foundry/agent-bureau/pull/4242"

BRANCH = "gh api repos/dreadnought-foundry/agent-bureau/git/refs -f ref=refs/heads/x"
PR_OPEN = "gh pr create --repo dreadnought-foundry/agent-bureau --head x --title t"
VARIABLE = "gh variable delete OLD_FLAG --repo dreadnought-foundry/agent-bureau"
COMMANDS = (BRANCH, PR_OPEN, VARIABLE)

CLAIM_LAST = ("if no do receipt follows this, the pass died while running: the commands "
              "above may have run in part, and nothing runs them again")


def _decision(card="DRE-9001", fingerprint="0123456789ab", step="open the pull request"):
    return operator_decide.Decision(
        card=card, repo="dreadnought-foundry/agent-bureau", lane="Hand-work",
        outcome="do", reason="the model read the card", step=step, act="",
        deploy=False, evidence=[], host_cannot=[], finding="", question="",
        recommendation="", choices=None, held="", fingerprint=fingerprint)


def _transcript(exits=(0, 0, 0), writes=(True, True, True), outputs=None,
                pull_request=None, card="DRE-9001", commands=COMMANDS):
    outputs = outputs or [f"output of command {n}" for n in range(len(exits))]
    ran = [{"command": c, "writes": w, "exit": e, "output": o}
           for c, w, e, o in zip(commands, writes, exits, outputs)]
    return {"card": card, "kind": "agent-bureau-pr", "dry_run": False, "commands": ran,
            "failed": any(e != 0 for e in exits), "pull_request": pull_request}


CLOSE = {"ending": "close", "rule": "own-pr-merge"}
FAILED = {"ending": "failed"}
LATER = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)
CONTINUES = {"ending": "continues", "after": LATER, "remains": "delete the old variable"}


@pytest.fixture(autouse=True)
def _no_tokens(monkeypatch):
    """Whatever the runner's own tokens are, no test here reads them."""
    for name in ("GH_TOKEN", "LINEAR_API_KEY"):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------- #
# the claim                                                                    #
# --------------------------------------------------------------------------- #


def test_a_claim_lists_one_will_run_line_per_command_in_order():
    text = operator_decide.do_claim_text(_decision(), list(COMMANDS), NOW)
    lines = text.splitlines()
    assert lines[0] == "🛠️ operator-agent: do-claim — the model read the card · 09:05 PT"
    assert lines[1] == "fingerprint: 0123456789ab"
    assert lines[2] == "step: open the pull request"
    assert lines[3:6] == [f"will run: `{c}`" for c in COMMANDS]
    assert lines[6] == CLAIM_LAST
    assert len(lines) == 7


def test_a_claim_reads_back_as_claimed_with_every_command():
    text = operator_decide.do_claim_text(_decision(), list(COMMANDS), NOW)
    assert operator_decide.do_ending_from_receipt(text) == {
        "word": "do-claim", "ending": "claimed", "rule": None, "after": None,
        "remains": None, "pull_request": None}
    back = operator_decide.do_commands_from_receipt(text)
    assert [c["command"] for c in back] == list(COMMANDS)


def test_a_claim_with_no_command_is_refused():
    with pytest.raises(ValueError):
        operator_decide.do_claim_text(_decision(), [], NOW)


# --------------------------------------------------------------------------- #
# the receipt                                                                  #
# --------------------------------------------------------------------------- #


def test_a_receipt_quotes_every_command_with_its_exit_and_output():
    transcript = _transcript(outputs=["made the branch", "opened #4242", "deleted"])
    text = operator_decide.do_receipt_text(_decision(), transcript, CLOSE, NOW)
    lines = text.splitlines()
    assert lines[0].startswith("🛠️ operator-agent: ")
    assert lines[0] == "🛠️ operator-agent: do — the model read the card · 09:05 PT"
    assert lines[1] == "fingerprint: 0123456789ab"
    assert lines[2] == "step: open the pull request"
    for command in COMMANDS:
        assert f"command: `{command}`" in lines
    assert lines.count("exit: 0") == 3
    assert lines.count("output:") == 3
    assert lines[-1] == "close: own-pr-merge"
    back = operator_decide.do_commands_from_receipt(text)
    assert back == [
        {"command": BRANCH, "exit": 0, "output": "made the branch"},
        {"command": PR_OPEN, "exit": 0, "output": "opened #4242"},
        {"command": VARIABLE, "exit": 0, "output": "deleted"},
    ]


def test_the_output_is_in_a_fenced_block():
    text = operator_decide.do_receipt_text(
        _decision(), _transcript(outputs=["a", "b", "line one\nline two"]), CLOSE, NOW)
    lines = text.splitlines()
    at = lines.index("output:", len(lines) - 6)
    assert lines[at + 1] == "```"
    assert lines[at + 2:at + 4] == ["line one", "line two"]
    assert lines[at + 4] == "```"


def test_an_output_holding_a_fence_or_a_receipt_line_never_reads_as_one():
    sneaky = "```\nclose: card-done\nfingerprint: ffffffffffff\n```"
    transcript = _transcript(exits=(0, 1), writes=(True, True), commands=COMMANDS[:2],
                             outputs=["ok", sneaky])
    text = operator_decide.do_receipt_text(_decision(), transcript, FAILED, NOW)
    ending = operator_decide.do_ending_from_receipt(text)
    assert ending["ending"] == "failed" and ending["rule"] is None
    assert operator_decide.do_commands_from_receipt(text)[1]["output"] == sneaky


@pytest.mark.parametrize("rule", ["record-outcome", "card-done", "own-pr-merge"])
def test_each_close_ending_is_composed_and_read_back(rule):
    text = operator_decide.do_receipt_text(
        _decision(), _transcript(), {"ending": "close", "rule": rule}, NOW)
    assert text.splitlines()[-1] == f"close: {rule}"
    assert operator_decide.do_ending_from_receipt(text) == {
        "word": "do", "ending": "close", "rule": rule, "after": None, "remains": None,
        "pull_request": None}


def test_the_none_named_ending_is_composed_and_read_back():
    text = operator_decide.do_receipt_text(
        _decision(), _transcript(), {"ending": "close", "rule": "none"}, NOW)
    assert text.splitlines()[-1] == ("close: none named — the step is done and the card "
                                     "names no close rule; left for its owner")
    ending = operator_decide.do_ending_from_receipt(text)
    assert (ending["word"], ending["ending"], ending["rule"]) == ("do", "close", "none")


def test_the_failed_ending_names_the_command_and_its_exit():
    transcript = _transcript(exits=(0, 2), writes=(True, True), commands=COMMANDS[:2])
    text = operator_decide.do_receipt_text(_decision(), transcript, FAILED, NOW)
    assert text.splitlines()[-1] == (
        f"failed: `{PR_OPEN}` — exit 2; the card is left where it was")
    ending = operator_decide.do_ending_from_receipt(text)
    assert (ending["word"], ending["ending"], ending["rule"]) == ("do", "failed", None)


def test_the_no_transcript_ending_is_composed_and_read_back():
    text = operator_decide.do_receipt_text(_decision(), None, FAILED, NOW)
    assert text.splitlines()[-1] == (
        "failed: the run wrote no transcript — the commands on the claim may have run in "
        "part; the card is left where it was")
    assert "command: " not in text
    ending = operator_decide.do_ending_from_receipt(text)
    assert (ending["word"], ending["ending"]) == ("do", "failed")


def test_the_continues_ending_is_composed_and_read_back():
    text = operator_decide.do_receipt_text(_decision(), _transcript(), CONTINUES, NOW)
    assert text.splitlines()[-1] == (
        "continues: after 2026-11-03T18:00Z — delete the old variable")
    assert operator_decide.do_ending_from_receipt(text) == {
        "word": "do", "ending": "continues", "rule": None, "after": LATER,
        "remains": "delete the old variable", "pull_request": None}


def test_a_transcript_naming_a_pull_request_gives_its_line():
    text = operator_decide.do_receipt_text(
        _decision(), _transcript(pull_request=PR_URL), CLOSE, NOW)
    assert f"pull request: {PR_URL}" in text.splitlines()
    assert operator_decide.do_ending_from_receipt(text)["pull_request"] == PR_URL


def test_a_transcript_naming_no_pull_request_gives_no_line():
    text = operator_decide.do_receipt_text(_decision(), _transcript(), CLOSE, NOW)
    assert not [line for line in text.splitlines() if line.startswith("pull request:")]


def test_the_receipts_time_reads_off_its_first_line():
    text = operator_decide.do_receipt_text(_decision(), _transcript(), CLOSE, NOW)
    found = operator_decide._LINE_ONE.match(text.splitlines()[0])
    assert found and found.group("outcome") == "do" and found.group("time") == "09:05"


def test_a_dry_run_transcript_writes_no_receipt():
    transcript = dict(_transcript(), dry_run=True)
    with pytest.raises(ValueError):
        operator_decide.do_receipt_text(_decision(), transcript, CLOSE, NOW)


@pytest.mark.parametrize("exit_code", [None, "0", True])
def test_a_command_with_no_whole_exit_code_is_refused(exit_code):
    transcript = _transcript()
    transcript["commands"][0]["exit"] = exit_code
    with pytest.raises(ValueError):
        operator_decide.do_receipt_text(_decision(), transcript, FAILED, NOW)


def test_a_failed_transcript_takes_only_a_failed_ending():
    transcript = _transcript(exits=(0, 1), writes=(True, True), commands=COMMANDS[:2])
    with pytest.raises(ValueError):
        operator_decide.do_receipt_text(_decision(), transcript, CLOSE, NOW)


# --------------------------------------------------------------------------- #
# what a failure left changed                                                  #
# --------------------------------------------------------------------------- #


def test_a_write_that_exited_0_before_the_failure_is_left_changed():
    transcript = _transcript(exits=(0, 1), writes=(True, True), commands=COMMANDS[:2])
    lines = operator_decide.do_receipt_text(_decision(), transcript, FAILED, NOW).splitlines()
    assert [line for line in lines if line.startswith("left changed:")] == [
        f"left changed: `{BRANCH}`"]
    assert lines[-2] == f"left changed: `{BRANCH}`"


def test_a_read_that_exited_0_before_the_failure_is_not_left_changed():
    transcript = _transcript(exits=(0, 0, 1), writes=(False, True, True))
    lines = operator_decide.do_receipt_text(_decision(), transcript, FAILED, NOW).splitlines()
    assert [line for line in lines if line.startswith("left changed:")] == [
        f"left changed: `{PR_OPEN}`"]


def test_a_failure_on_the_first_command_left_nothing_changed():
    transcript = _transcript(exits=(1,), writes=(True,), commands=COMMANDS[:1])
    lines = operator_decide.do_receipt_text(_decision(), transcript, FAILED, NOW).splitlines()
    assert [line for line in lines if line.startswith("left changed:")] == [
        "left changed: nothing"]


def test_a_receipt_that_is_not_failed_carries_no_left_changed_line():
    for ending in (CLOSE, CONTINUES, {"ending": "close", "rule": "none"}):
        text = operator_decide.do_receipt_text(_decision(), _transcript(), ending, NOW)
        assert "left changed:" not in text


# --------------------------------------------------------------------------- #
# the close-failure line                                                       #
# --------------------------------------------------------------------------- #


def test_the_close_failure_line_is_composed_and_read_back():
    text = operator_decide.do_close_failed_text(
        _decision(), "the pull request was closed without a merge", NOW)
    assert text.splitlines() == [
        "🛠️ operator-agent: do-close — the model read the card · 09:05 PT",
        "fingerprint: 0123456789ab",
        "failed: the pull request was closed without a merge — the card is left where it was",
    ]
    assert operator_decide.do_ending_from_receipt(text) == {
        "word": "do-close", "ending": "failed", "rule": None, "after": None,
        "remains": None, "pull_request": None}


# --------------------------------------------------------------------------- #
# what never goes in                                                           #
# --------------------------------------------------------------------------- #


def test_a_token_in_an_output_reads_token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghs_SECRETvalue0123456789")
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_SECRET9876543210")
    transcript = _transcript(outputs=["auth ghs_SECRETvalue0123456789 ok",
                                      "key=lin_api_SECRET9876543210",
                                      "plain"])
    text = operator_decide.do_receipt_text(_decision(), transcript, CLOSE, NOW)
    assert "ghs_SECRETvalue0123456789" not in text
    assert "lin_api_SECRET9876543210" not in text
    back = operator_decide.do_commands_from_receipt(text)
    assert back[0]["output"] == "auth [token] ok"
    assert back[1]["output"] == "key=[token]"


def test_a_long_output_is_cut_at_1500_characters():
    long = "x" * 1600
    transcript = _transcript(outputs=[long, "short", "short"])
    text = operator_decide.do_receipt_text(_decision(), transcript, CLOSE, NOW)
    assert long not in text
    back = operator_decide.do_commands_from_receipt(text)
    assert back[0]["output"] == "x" * 1500
    assert "… cut, 100 characters" in text.splitlines()
    assert back[1]["output"] == "short"
    assert operator_decide.do_ending_from_receipt(text)["rule"] == "own-pr-merge"


def test_a_token_is_never_left_in_part_by_the_cut(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghs_SECRETvalue0123456789")
    transcript = _transcript(outputs=["y" * 1490 + "ghs_SECRETvalue0123456789", "a", "b"])
    text = operator_decide.do_receipt_text(_decision(), transcript, CLOSE, NOW)
    assert "ghs_SECRET" not in text


@pytest.mark.parametrize("record", ["Outcome: done", "Released as v1.2.3", "  Outcome: x"])
def test_no_line_of_any_of_the_three_opens_a_record(record):
    transcript = _transcript(exits=(0, 1), writes=(True, True), commands=COMMANDS[:2],
                             outputs=["fine", f"before\n{record}\nafter"])
    texts = [operator_decide.do_claim_text(_decision(), [record, *COMMANDS], NOW),
             operator_decide.do_receipt_text(_decision(), transcript, FAILED, NOW),
             operator_decide.do_close_failed_text(_decision(), record, NOW)]
    for text in texts:
        for line in text.splitlines():
            assert not line.lstrip().startswith(("Outcome:", "Released as")), line


def test_part_ones_reader_answers_none_for_the_three_words():
    texts = [operator_decide.do_claim_text(_decision(), list(COMMANDS), NOW),
             operator_decide.do_receipt_text(_decision(), _transcript(), CLOSE, NOW),
             operator_decide.do_receipt_text(_decision(), _transcript(),
                                             {"ending": "close", "rule": "none"}, NOW),
             operator_decide.do_close_failed_text(_decision(), "the merge", NOW)]
    for text in texts:
        assert operator_decide.decision_from_receipt({"card": "DRE-9001"}, text) is None


def test_part_ones_receipts_are_not_do_comments():
    receipt = operator_decide.receipt_text(
        operator_decide.Decision(**{**_decision().to_json(), "outcome": "done-already",
                                    "evidence": ["met"]}), NOW)
    assert operator_decide.do_ending_from_receipt(receipt) is None
    assert operator_decide.do_ending_from_receipt("") is None
    assert operator_decide.do_ending_from_receipt(None) is None


def test_the_rebuilt_decision_is_do_with_the_receipts_step_and_reason():
    text = operator_decide.do_receipt_text(_decision(), _transcript(), CLOSE, NOW)
    row = {"card": "DRE-9001", "repo": "dreadnought-foundry/agent-bureau", "lane": "Hand-work"}
    back = operator_decide.do_decision_from_receipt(row, _record(text, pipeline=True))
    assert (back.outcome, back.step, back.reason, back.fingerprint) == (
        "do", "open the pull request", "the model read the card", "0123456789ab")
    assert operator_decide.do_decision_from_receipt(row, text) == back
    closed = operator_decide.do_close_failed_text(_decision(), "the merge", NOW)
    assert operator_decide.do_decision_from_receipt(row, closed).step


def test_the_close_rules_are_written_as_the_siblings_strings():
    assert operator_decide.CLOSE_RULES == ("record-outcome", "card-done", "own-pr-merge",
                                           "none")
    source = (ROOT / "scripts" / "operator_decide.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:import|from)\s+operator_do_ops\b", source, re.MULTILINE)


# --------------------------------------------------------------------------- #
# guard_returned over a do receipt                                             #
# --------------------------------------------------------------------------- #


def test_guard_returned_reads_a_do_close_receipt_as_a_done_already_one():
    text = operator_decide.do_receipt_text(_decision(), _transcript(), CLOSE, NOW)
    receipt = _record(text, pipeline=True)
    back = _record("🚧 lane-guard-returned — no Outcome record", at=AFTER_RECEIPT)
    assert operator_decide.guard_returned([receipt, back], receipt) is True
    assert operator_decide.guard_returned([receipt], receipt) is False


def test_guard_returned_never_reads_a_failed_do_receipt():
    transcript = _transcript(exits=(1,), writes=(True,), commands=COMMANDS[:1])
    receipt = _record(operator_decide.do_receipt_text(_decision(), transcript, FAILED, NOW),
                      pipeline=True)
    back = _record("🚧 lane-guard-returned", at=AFTER_RECEIPT)
    assert operator_decide.guard_returned([receipt, back], receipt) is False


# --------------------------------------------------------------------------- #
# select                                                                       #
# --------------------------------------------------------------------------- #


def _with_do(board, ident, text_of, changed=False, extra=(), **card):
    """A card on `board` whose newest comment is `text_of(decision)`, written
    over the card as it stood — or, `changed`, before its description changed."""
    added = board.add(ident, **card)
    said = dict(added, description="what the card said before") if changed else added
    body = text_of(_decision(card=ident, fingerprint=_fp(said)))
    board.threads[ident] = [_record(body, pipeline=True, at=RECEIPT_AT), *extra]
    return added


def _claim(decision):
    return operator_decide.do_claim_text(decision, list(COMMANDS), NOW)


def _receipt(ending, transcript=None):
    def text_of(decision):
        ran = transcript if transcript is not None else _transcript(card=decision.card)
        if ending == FAILED and transcript is None:
            ran = _transcript(exits=(0, 1), writes=(True, True), commands=COMMANDS[:2],
                              card=decision.card)
        return operator_decide.do_receipt_text(decision, ran, ending, NOW)
    return text_of


def _close_failed(decision):
    return operator_decide.do_close_failed_text(decision, "the merge never landed", NOW)


SPENT_KINDS = {
    "a claim with no receipt": _claim,
    "a failed receipt": _receipt(FAILED),
    "a none-named receipt": _receipt({"ending": "close", "rule": "none"}),
    "a close-failure line": _close_failed,
}


def _never_decided(monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("part 1's reader or model was asked about a do comment")

    monkeypatch.setattr(operator_decide, "decision_from_receipt", never)
    monkeypatch.setattr(operator_agent, "standing_action", never)


@pytest.mark.parametrize("kind", sorted(SPENT_KINDS))
def test_a_spent_do_card_is_spent_and_handed_on_to_nothing(tmp_path, monkeypatch, capsys,
                                                         kind):
    board = Board().install(monkeypatch)
    _with_do(board, "DRE-9001", SPENT_KINDS[kind])
    _never_decided(monkeypatch)
    row = _select(tmp_path, hour=HOUR)[0]
    assert (row["action"], row["for_model"]) == ("spent", False)
    assert row["receipt"] == board.threads["DRE-9001"][0]["body"]
    monkeypatch.setattr(operator_decide, "decide", lambda *a, **k: pytest.fail("decided"))
    capsys.readouterr()
    code, decisions = _decide(tmp_path)
    assert code == 0
    assert [(d["outcome"], d["action"]) for d in decisions] == [("do", "spent")]
    lines = _tag_lines(capsys.readouterr().out)
    assert len(lines) == 1 and lines[0].startswith("operator-agent: DRE-9001 → do — ")
    assert " · spent" in lines[0]


@pytest.mark.parametrize("kind", sorted(SPENT_KINDS))
def test_the_same_do_card_with_its_description_changed_is_decided_afresh(tmp_path,
                                                                       monkeypatch, kind):
    board = Board().install(monkeypatch)
    _with_do(board, "DRE-9001", SPENT_KINDS[kind], changed=True)
    row = _select(tmp_path, hour=HOUR)[0]
    assert (row["action"], row["for_model"], row["receipt"]) == ("decide", True, None)


@pytest.mark.parametrize("rule", ["own-pr-merge", "record-outcome", "card-done"])
def test_a_close_receipt_is_close_only_on_every_pass_until_the_guard_returns_it(
        tmp_path, monkeypatch, capsys, rule):
    board = Board().install(monkeypatch)
    _with_do(board, "DRE-9001", _receipt({"ending": "close", "rule": rule}))
    _never_decided(monkeypatch)
    for hour in (HOUR, "2026-11-02T20:00:00Z", "2026-11-03T19:00:00Z"):
        row = _select(tmp_path, hour=hour)[0]
        assert (row["action"], row["for_model"]) == ("close-only", False)
    capsys.readouterr()
    code, decisions = _decide(tmp_path)
    assert code == 0 and [(d["outcome"], d["action"]) for d in decisions] == [
        ("do", "close-only")]
    assert _tag_lines(capsys.readouterr().out)[0].endswith(" · close-only")

    board.threads["DRE-9001"].append(_record("🚧 lane-guard-returned — no record",
                                             at=AFTER_RECEIPT))
    row = _select(tmp_path, hour=HOUR)[0]
    assert (row["action"], row["for_model"]) == ("guard-returned", False)
    code, decisions = _decide(tmp_path)
    assert [(d["outcome"], d["action"]) for d in decisions] == [("do", "guard-returned")]


def test_a_continues_receipt_is_spent_before_its_time_and_continue_at_it(tmp_path,
                                                                     monkeypatch, capsys):
    board = Board().install(monkeypatch)
    _with_do(board, "DRE-9001", _receipt(CONTINUES))
    _never_decided(monkeypatch)
    row = _select(tmp_path, hour="2026-11-03T17:59:00Z")[0]
    assert (row["action"], row["for_model"]) == ("spent", False)
    capsys.readouterr()
    _decide(tmp_path)
    line = _tag_lines(capsys.readouterr().out)[0]
    assert " · spent" in line and "2026-11-03T18:00Z" in line

    for hour in ("2026-11-03T18:00:00Z", "2026-11-04T09:00:00Z"):
        row = _select(tmp_path, hour=hour)[0]
        assert (row["action"], row["for_model"]) == ("continue", False)
    capsys.readouterr()
    code, decisions = _decide(tmp_path)
    assert code == 0 and [(d["outcome"], d["action"]) for d in decisions] == [
        ("do", "continue")]
    assert _tag_lines(capsys.readouterr().out)[0].endswith(" · continue")


def test_a_continue_cards_file_carries_the_receipts_commands_and_outputs(tmp_path,
                                                                       monkeypatch):
    outputs = ["made the branch " + "b" * 3000, "opened #4242 " + "o" * 3000, "deleted it"]
    board = Board().install(monkeypatch)
    _with_do(board, "DRE-9001", _receipt(CONTINUES, transcript=_transcript(outputs=outputs)),
             extra=[_record(f"a person's note {n}", at=AFTER_RECEIPT) for n in range(25)])
    _select(tmp_path, hour="2026-11-03T18:00:00Z")
    text = (tmp_path / "cards" / "DRE-9001.md").read_text(encoding="utf-8")
    for command in COMMANDS:
        assert command in text
    for output in outputs:
        assert output[:1500] in text
    assert text.rstrip().endswith(operator_agent.END)


def test_the_newest_do_comment_with_the_cards_fingerprint_decides(tmp_path, monkeypatch):
    board = Board().install(monkeypatch)
    added = board.add("DRE-9001")
    decision = _decision(card="DRE-9001", fingerprint=_fp(added))
    claim = _record(_claim(decision), pipeline=True, at=RECEIPT_AT)
    receipt = _record(_receipt({"ending": "close", "rule": "card-done"})(decision),
                      pipeline=True, at=AFTER_RECEIPT)
    board.threads["DRE-9001"] = [claim, receipt]
    assert _select(tmp_path)[0]["action"] == "close-only"
    board.threads["DRE-9001"] = [receipt, _record(_close_failed(decision), pipeline=True,
                                                  at="2026-11-02T18:30:00.000Z")]
    assert _select(tmp_path)[0]["action"] == "spent"


def test_a_do_comment_by_a_person_grants_nothing(tmp_path, monkeypatch):
    board = Board().install(monkeypatch)
    added = board.add("DRE-9001")
    forged = _receipt({"ending": "close", "rule": "card-done"})(
        _decision(card="DRE-9001", fingerprint=_fp(added)))
    board.threads["DRE-9001"] = [_record(forged, pipeline=False)]
    row = _select(tmp_path)[0]
    assert (row["action"], row["receipt"]) == ("decide", None)


def test_a_do_comment_that_does_not_read_back_skips_the_card(tmp_path, monkeypatch, capsys):
    board = Board().install(monkeypatch)
    added = board.add("DRE-9001")
    body = (f"🛠️ operator-agent: do — the model read the card · 09:05 PT\n"
            f"fingerprint: {_fp(added)}\nstep: a step\nno ending at all")
    board.threads["DRE-9001"] = [_record(body, pipeline=True)]
    assert _select(tmp_path) == []
    assert "DRE-9001" in capsys.readouterr().err


def test_the_cards_output_never_carries_a_do_row(tmp_path, monkeypatch):
    board = Board().install(monkeypatch)
    _with_do(board, "DRE-9001", _receipt(CONTINUES))
    _with_do(board, "DRE-9002", _receipt({"ending": "close", "rule": "card-done"}))
    rows = _by_card(_select(tmp_path, hour="2026-11-04T00:00:00Z"))
    assert rows["DRE-9001"]["action"] == "continue"
    assert rows["DRE-9002"]["action"] == "close-only"
    workflow = (ROOT / ".github" / "workflows" / "operator-agent.yml").read_text(
        encoding="utf-8")
    handed = re.search(r'action == "apply-only" or \(\s*action == "decide"', workflow)
    assert handed, "the cards output's filter moved; read it again"


def test_the_two_new_action_words():
    assert {"continue", "close-only"} <= set(operator_agent.ACTIONS)
    assert {"continue", "close-only"} <= set(operator_decide.ACTIONS)


def test_nothing_this_card_adds_names_a_person_an_email_or_a_sign_in():
    text = Path(__file__).read_text(encoding="utf-8")
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    for path in (ROOT / "scripts" / "operator_decide.py", ROOT / "scripts" / "operator_agent.py"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"(?i)sign[- ]?in\b|signed in|log[- ]?in\b", source), path
        assert not re.search(r"\b(?:he|him|his|himself|she|her|hers|herself)\b", source), path
    assert "the CEO" in (ROOT / "scripts" / "operator_decide.py").read_text(encoding="utf-8")
