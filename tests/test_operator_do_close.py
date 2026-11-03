"""RED-first tests: a `do` card's claim, receipt and close (DRE-8925, epic DRE-8738).

`scripts/operator_agent_apply.py` gains three commands. `do-claim` posts the
claim before a step runs, `do-post` posts the receipt after it — the ending
chosen from the transcript, the plan and the card's own close rule — and
`do-close` makes the close the newest receipt names: through the console's
outcome door (`scripts/console_outcome.py`) for a `no-code` card, by a move to
Done for any other, and for `own-pr-merge` only once `gh pr view` says the
receipt's pull request merged. A close that may land later writes nothing and
exits 4, so the next pass tries it again; a close that never can posts one
`do-close` line, and the card is left for a person.

Every scenario runs over `DoBoard`, the Triage repair's test board with the
card read, the comment URLs and the console's read door answered from the same
state, so a test reads back the comments a card carries and the lane it ends
in. The cards are four live cards from part 1's list
(`tests/fixtures/operator-do-close-cards.json`).

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_do_close.py -v
"""
from __future__ import annotations

import contextlib
import http.server
import json
import os
import re
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import bureau_read  # noqa: E402
import console_outcome  # noqa: E402
import linear_ops  # noqa: E402
import operator_agent  # noqa: E402
import operator_agent_apply  # noqa: E402
import operator_decide  # noqa: E402
import operator_do_ops  # noqa: E402
import pipeline_act  # noqa: E402
import pipeline_notice  # noqa: E402
from triage_repair_fakes import FLEET_USER, PERSON_USER, TriageRepairBoard  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "operator-do-close-cards.json"
ACTS = ROOT / "config" / "pipeline-acts.json"

#: 2026-11-02 17:05 UTC is 09:05 PT.
NOW = datetime(2026, 11, 2, 17, 5, tzinfo=UTC)

#: The two closes no fixture card names, added word for word as
#: tests/test_operator_do_ops.py adds them.
CARD_DONE = "This card closes with `card-done`."
OWN_PR_MERGE = "This card closes on the merge of the pull request it opens."

STEP = "set the repository variable ALARM_REPAIR_LIVE to true"
REASON = "the model read the card"
REPO = "dreadnought-foundry/bureau-pipeline"
VARIABLE_SET = f"gh variable set ALARM_REPAIR_LIVE --body true -R {REPO}"
VARIABLE_LIST = f"gh variable list -R {REPO}"
PR_URL = "https://github.com/dreadnought-foundry/bureau-pipeline/pull/4242"

REFUSED = re.compile(r"^operator-agent: (?P<card>DRE-\d+) write refused — do · (?P<what>.+)$",
                     re.MULTILINE)


# --------------------------------------------------------------------------- #
# the cards                                                                    #
# --------------------------------------------------------------------------- #


def _cards() -> dict:
    return {row["card"]: row for row in json.loads(FIXTURE.read_text())["cards"]}


def _description(card: str, adds: str | None = None) -> str:
    quotes = list(_cards()[card]["quotes"])
    return "\n\n".join(quotes + ([adds] if adds else []))


# --------------------------------------------------------------------------- #
# the board                                                                    #
# --------------------------------------------------------------------------- #


class DoBoard(TriageRepairBoard):
    """The Triage repair's board, answering also the card read a `do`
    command makes, the receipt's comment URL, and the console's read door —
    each from the same cards, so a write the command makes is read back."""

    def __init__(self) -> None:
        super().__init__()
        self.door_asks: list[dict] = []
        self.door_unknown: bureau_read.ReadUnknown | None = None

    def _comment_url(self, identifier: str, n: int) -> str:
        return f"https://linear.app/dreadnoughtfoundry/issue/{identifier}#comment-{n:04d}"

    def gql(self, query, variables=None, **kw):
        identifier = (variables or {}).get("id")
        if query.lstrip().startswith("mutation"):
            raise AssertionError(f"an unexpected mutation reached the board: {query[:80]}")
        if " url" in query:
            card = self._card(identifier)
            self.reads.append(("comment-urls", identifier, False))
            nodes = [{"body": c.body, "createdAt": c.created_at,
                      "url": self._comment_url(identifier, n)}
                     for n, c in enumerate(card.comments)]
            return {"issue": {"comments": {"nodes": list(reversed(nodes))}}}
        if "title" in query and "description" in query:
            card = self._card(identifier)
            self.reads.append(("card", identifier, False))
            return {"issue": {
                "id": f"id-{identifier}", "identifier": identifier, "title": card.title,
                "description": card.description, "state": {"name": card.lane},
                "labels": {"nodes": [{"name": n} for n in card.labels]}}}
        return super().gql(query, variables, **kw)

    def door_get(self, endpoint, query, **kw):
        """`bureau_read._get` for `/cards`: the card and its whole thread."""
        self.door_asks.append({"endpoint": endpoint, **query})
        if self.door_unknown is not None:
            raise self.door_unknown
        nodes = []
        for identifier in query["ids"].split(","):
            card = self._card(identifier)
            node = self._board_node(card)
            node["priority"] = 0
            node["comments"] = {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{"body": c.body, "createdAt": c.created_at, "user": {"id": c.user}}
                          for c in reversed(card.comments)]}
            for key in ("relations", "inverseRelations", "history"):
                node.pop(key, None)
            nodes.append(node)
        return {"schema": bureau_read.SCHEMA, "verdict": "FRESH",
                "freshness": {"as_of": "2026-11-02T17:04:30Z", "age_seconds": 3},
                "caller": {}, "viewer": {"id": "user-the-console"},
                "issues": {"nodes": nodes}}

    @contextlib.contextmanager
    def patched(self):
        with super().patched(), contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(linear_ops, "gql", side_effect=self.gql))
            stack.enter_context(patch.object(linear_ops, "viewer_id", return_value=FLEET_USER))
            stack.enter_context(patch.object(bureau_read, "_get", side_effect=self.door_get))
            stack.enter_context(patch.object(operator_agent_apply, "_now",
                                             side_effect=lambda: self.now))
            yield self

    now = NOW


def _board(card: str, *, adds: str | None = None, labels=None, lane=None) -> DoBoard:
    row = _cards()[card]
    board = DoBoard()
    board.add(card, lane=lane or row["lane"], title=row["title"],
              description=_description(card, adds),
              labels=labels if labels is not None else row["labels"])
    return board


def _row(board: DoBoard, card: str) -> dict:
    found = board.cards[card]
    return {"card": card, "repo": "", "lane": found.lane, "title": found.title,
            "description": found.description}


def _decision(board: DoBoard, card: str) -> operator_decide.Decision:
    return operator_decide.Decision(
        card=card, repo="", lane=board.cards[card].lane, outcome="do", reason=REASON,
        step=STEP, act="", deploy=False, evidence=[], host_cannot=[], finding="",
        question="", recommendation="", choices=None, held="",
        fingerprint=operator_decide.fingerprint(_row(board, card)))


def _posted(text: str) -> str:
    """A `do` comment as the operator agent posts it: through the act registry."""
    return pipeline_act.receipt("operator-agent", text)


def _claim(board: DoBoard, card: str, lines=(VARIABLE_SET, VARIABLE_LIST)) -> None:
    board.say(card, _posted(operator_decide.do_claim_text(_decision(board, card),
                                                         list(lines), NOW)))


def _transcript(card: str, exits=(0, 0), commands=(VARIABLE_SET, VARIABLE_LIST),
                writes=(True, False), pull_request=None, dry_run=False) -> dict:
    ran = [{"command": c, "writes": w, "exit": e, "output": f"output of {c.split()[2]}"}
           for c, w, e in zip(commands, writes, exits)]
    return {"card": card, "kind": "variable", "dry_run": dry_run, "commands": ran,
            "failed": any(e != 0 for e in exits), "pull_request": pull_request}


def _refusal(card: str) -> dict:
    """A refused plan's transcript, as `operator_do.py check` writes it."""
    return {"card": card, "kind": "variable", "dry_run": False,
            "commands": [{"command": "check", "writes": False, "exit": 1,
                          "output": "op 1 variable-set: `OTHER_FLAG` is not quoted in the "
                                    "card's text"}],
            "failed": True, "pull_request": None}


def _receipt(board: DoBoard, card: str, ending: dict, transcript: dict | None = None,
             when: datetime = NOW) -> None:
    transcript = transcript or _transcript(card)
    board.say(card, _posted(operator_decide.do_receipt_text(
        _decision(board, card), transcript, ending, when)))


def _write(path: Path, data) -> str:
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return str(path)


def _decisions(tmp_path: Path, decision: operator_decide.Decision, action: str) -> str:
    return _write(tmp_path / "decisions.json", [{**decision.to_json(), "action": action}])


def _check(tmp_path: Path, card: str, lines=(VARIABLE_SET, VARIABLE_LIST), ok=True) -> str:
    return _write(tmp_path / f"{card}.check.json",
                  {"ok": ok, "kind": "variable", "repo": REPO,
                   "command_lines": list(lines) if ok else []})


def _plan(tmp_path: Path, card: str, continues=None) -> str:
    return _write(tmp_path / f"{card}.plan.json",
                  {"card": card, "kind": "variable", "step": STEP, "reason": REASON,
                   "ops": [{"op": "variable-set", "repo": REPO, "name": "ALARM_REPAIR_LIVE",
                            "value": "true"}],
                   "continues": continues})


def _ending(body: str) -> dict:
    found = operator_decide.do_ending_from_receipt(body)
    assert found is not None, f"the comment does not read back:\n{body}"
    return found


def _last_line(body: str) -> str:
    """The comment's own last line, the act registry's trailer taken off."""
    lines = [line for line in body.strip().splitlines()
             if not line.startswith(f"{pipeline_act.TRAILER_MARK} {pipeline_act.TRAILER_TAG}:")]
    return [line for line in lines if line.strip()][-1]


@pytest.fixture(autouse=True)
def _a_fresh_run(monkeypatch):
    """Each test is one run: the read door on, never yet asked, and none of
    the runner's own tokens or door settings read."""
    for name in ("GH_TOKEN", "GITHUB_REPOSITORY", "BUREAU_READ_URL",
                 "BUREAU_NOTICE_AUDIENCE", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_EVENT_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BUREAU_READ", "on")
    bureau_read.reset_for_tests()
    yield
    bureau_read.reset_for_tests()


# --------------------------------------------------------------------------- #
# the stubs: the console's outcome door and `gh pr view`                       #
# --------------------------------------------------------------------------- #


class Console:
    """`console_outcome.record`, answering what it is told, or failing the
    test when called at all."""

    def __init__(self, status=200, outcome="recorded", forbid=False):
        self.calls: list[dict] = []
        self.status, self.outcome, self.forbid = status, outcome, forbid

    def record(self, card, outcome, receipt_url):
        if self.forbid:
            raise AssertionError("the console's outcome door was asked")
        self.calls.append({"card": card, "outcome": outcome, "receipt_url": receipt_url})
        if self.status == 200:
            return console_outcome.Answer(200, self.outcome, self.outcome)
        if self.status is None:
            return console_outcome.Answer(None, None, "unreachable", "ConnectionRefusedError")
        return console_outcome.Answer(self.status, None, f"http-{self.status}", "refused")

    @contextlib.contextmanager
    def patched(self):
        with patch.object(console_outcome, "record", side_effect=self.record):
            yield self


class Gh:
    """`gh pr view <url> --json state,mergedAt`, answered as GitHub would."""

    def __init__(self, state="MERGED", merged_at="2026-11-02T16:00:00Z", fails=False):
        self.calls: list[list] = []
        self.state, self.merged_at, self.fails = state, merged_at, fails

    def run(self, argv, *args, **kwargs):
        self.calls.append(list(argv))
        assert list(argv[:3]) == ["gh", "pr", "view"], argv
        assert list(argv[4:]) == ["--json", "state,mergedAt"], argv
        if self.fails:
            return subprocess.CompletedProcess(argv, 1, "", "HTTP 502: Bad Gateway")
        out = json.dumps({"state": self.state, "mergedAt": self.merged_at})
        return subprocess.CompletedProcess(argv, 0, out, "")

    @contextlib.contextmanager
    def patched(self):
        with patch.object(subprocess, "run", side_effect=self.run):
            yield self


def _run(*argv) -> int:
    return operator_agent_apply.main(list(argv))


# --------------------------------------------------------------------------- #
# the act registry's trailer reads back                                        #
# --------------------------------------------------------------------------- #


def test_a_do_comment_posted_through_the_act_registry_reads_back():
    board = _board("DRE-8637", adds=CARD_DONE)
    decision = _decision(board, "DRE-8637")
    for text in (operator_decide.do_claim_text(decision, [VARIABLE_SET], NOW),
                 operator_decide.do_receipt_text(decision, _transcript("DRE-8637"),
                                                 {"ending": "close", "rule": "card-done"}, NOW),
                 operator_decide.do_close_failed_text(decision, "the console refused", NOW)):
        assert operator_decide.do_ending_from_receipt(_posted(text)) == \
            operator_decide.do_ending_from_receipt(text)
        assert operator_decide.do_decision_from_receipt({"card": "DRE-8637"}, _posted(text))


# --------------------------------------------------------------------------- #
# do-claim                                                                     #
# --------------------------------------------------------------------------- #


def test_do_claim_posts_one_claim_for_a_decide_row_with_nothing_standing(tmp_path, capsys):
    board = _board("DRE-8637", adds=CARD_DONE)
    decision = _decision(board, "DRE-8637")
    with board.patched():
        code = _run("do-claim", "DRE-8637", "--decisions", _decisions(tmp_path, decision, "decide"),
                    "--check", _check(tmp_path, "DRE-8637"))
    assert code == 0
    assert [kind for kind, _ in board.writes("DRE-8637")] == ["comment"]
    body = board.newest("DRE-8637")
    assert pipeline_act.read_trailer(body)["act"] == "operator-agent"
    assert body.startswith(operator_decide.do_claim_text(decision, [VARIABLE_SET, VARIABLE_LIST],
                                                         NOW))
    assert _ending(body)["word"] == "do-claim"
    assert [c["command"] for c in operator_decide.do_commands_from_receipt(body)] == [
        VARIABLE_SET, VARIABLE_LIST]


def test_a_posted_claim_makes_the_next_select_read_the_card_as_spent(tmp_path):
    board = _board("DRE-8637", adds=CARD_DONE)
    decision = _decision(board, "DRE-8637")
    with board.patched():
        assert _run("do-claim", "DRE-8637", "--decisions",
                    _decisions(tmp_path, decision, "decide"),
                    "--check", _check(tmp_path, "DRE-8637")) == 0
    records = board.comment_records("DRE-8637", whole_thread=True)
    action, record = operator_agent.do_standing(records, decision.fingerprint, NOW)
    assert action == operator_agent.SPENT
    assert record["body"] == board.newest("DRE-8637")


def test_do_claim_posts_for_a_continue_row_whose_continues_has_come_due(tmp_path):
    board = _board("DRE-8637", adds=CARD_DONE)
    _claim(board, "DRE-8637")
    _receipt(board, "DRE-8637", {"ending": "continues",
                                 "after": NOW - timedelta(hours=2),
                                 "remains": "list the variable once more"})
    continued = operator_decide.do_decision_from_receipt(_row(board, "DRE-8637"),
                                                         board.newest("DRE-8637"))
    with board.patched():
        code = _run("do-claim", "DRE-8637", "--decisions",
                    _decisions(tmp_path, continued, "continue"),
                    "--check", _check(tmp_path, "DRE-8637", lines=(VARIABLE_LIST,)))
    assert code == 0
    assert [kind for kind, _ in board.writes("DRE-8637")] == ["comment"]
    assert _ending(board.newest("DRE-8637"))["word"] == "do-claim"
    assert [c["command"] for c in operator_decide.do_commands_from_receipt(
        board.newest("DRE-8637"))] == [VARIABLE_LIST]


def test_do_claim_posts_nothing_for_a_continue_row_not_yet_due(tmp_path, capsys):
    board = _board("DRE-8637", adds=CARD_DONE)
    _receipt(board, "DRE-8637", {"ending": "continues", "after": NOW + timedelta(hours=2),
                                 "remains": "list the variable once more"})
    continued = operator_decide.do_decision_from_receipt(_row(board, "DRE-8637"),
                                                         board.newest("DRE-8637"))
    with board.patched():
        code = _run("do-claim", "DRE-8637", "--decisions",
                    _decisions(tmp_path, continued, "continue"),
                    "--check", _check(tmp_path, "DRE-8637"))
    assert code == 3
    assert board.writes() == []


@pytest.mark.parametrize("standing", ["claim", "receipt", "close-failed"])
def test_do_claim_posts_nothing_when_a_claim_or_receipt_already_stands(tmp_path, capsys,
                                                                        standing):
    board = _board("DRE-8637", adds=CARD_DONE)
    _claim(board, "DRE-8637")
    if standing == "receipt":
        _receipt(board, "DRE-8637", {"ending": "close", "rule": "card-done"})
    if standing == "close-failed":
        board.say("DRE-8637", _posted(operator_decide.do_close_failed_text(
            _decision(board, "DRE-8637"), "the console refused the outcome", NOW)))
    decision = _decision(board, "DRE-8637")
    with board.patched():
        code = _run("do-claim", "DRE-8637", "--decisions",
                    _decisions(tmp_path, decision, "decide"),
                    "--check", _check(tmp_path, "DRE-8637"))
    assert code == 3
    assert board.writes() == []
    assert "Nothing posted" in capsys.readouterr().out


@pytest.mark.parametrize("action", ["spent", "close-only", "guard-returned", "apply-only"])
def test_do_claim_posts_nothing_for_a_row_whose_pass_is_spent_or_running(tmp_path, action):
    board = _board("DRE-8637", adds=CARD_DONE)
    with board.patched():
        code = _run("do-claim", "DRE-8637", "--decisions",
                    _decisions(tmp_path, _decision(board, "DRE-8637"), action),
                    "--check", _check(tmp_path, "DRE-8637"))
    assert code == 3
    assert board.writes() == []


def test_a_claim_linear_refuses_exits_4_with_the_write_refused_line(tmp_path, capsys):
    board = _board("DRE-8637", adds=CARD_DONE)
    board.fail("comment")
    with board.patched():
        code = _run("do-claim", "DRE-8637", "--decisions",
                    _decisions(tmp_path, _decision(board, "DRE-8637"), "decide"),
                    "--check", _check(tmp_path, "DRE-8637"))
    assert code == 4
    assert board.bodies("DRE-8637") == []
    found = REFUSED.search(capsys.readouterr().out)
    assert found and found.group("card") == "DRE-8637"
    assert found.group("what").startswith("the claim")


def test_do_claim_posts_nothing_when_the_check_refused_the_plan(tmp_path):
    board = _board("DRE-8637", adds=CARD_DONE)
    with board.patched():
        code = _run("do-claim", "DRE-8637", "--decisions",
                    _decisions(tmp_path, _decision(board, "DRE-8637"), "decide"),
                    "--check", _check(tmp_path, "DRE-8637", ok=False))
    assert code == 1
    assert board.writes() == []


# --------------------------------------------------------------------------- #
# do-post                                                                      #
# --------------------------------------------------------------------------- #


def _post(tmp_path, board, card, transcript, *, action="decide", continues=None,
          decision=None) -> int:
    path = tmp_path / "transcript.json"
    if transcript is not None:
        _write(path, transcript)
    with board.patched():
        return _run("do-post", card, "--decisions",
                    _decisions(tmp_path, decision or _decision(board, card), action),
                    "--transcript", str(path), "--plan", _plan(tmp_path, card, continues))


@pytest.mark.parametrize("card, adds, ending", [
    ("DRE-8637", CARD_DONE, "close: card-done"),
    ("DRE-7328", None, "close: record-outcome"),
    ("DRE-7978", OWN_PR_MERGE, "close: own-pr-merge"),
])
def test_do_post_posts_a_finished_runs_receipt_ending_in_the_cards_close_rule(
        tmp_path, card, adds, ending):
    board = _board(card, adds=adds)
    _claim(board, card)
    assert _post(tmp_path, board, card, _transcript(card)) == 0
    assert [kind for kind, _ in board.writes(card)] == ["comment"]
    body = board.newest(card)
    assert pipeline_act.read_trailer(body)["act"] == "operator-agent"
    assert _ending(body)["word"] == "do"
    assert _last_line(body) == ending
    assert [c["command"] for c in operator_decide.do_commands_from_receipt(body)] == [
        VARIABLE_SET, VARIABLE_LIST]


def test_do_post_ends_a_card_that_names_no_close_rule_none_named(tmp_path):
    board = _board("DRE-8623")
    _claim(board, "DRE-8623")
    assert _post(tmp_path, board, "DRE-8623", _transcript("DRE-8623")) == 0
    assert _last_line(board.newest("DRE-8623")) == operator_decide.NONE_NAMED
    assert _ending(board.newest("DRE-8623"))["rule"] == "none"


def test_do_post_names_the_pull_request_the_transcript_names(tmp_path):
    board = _board("DRE-7978", adds=OWN_PR_MERGE)
    _claim(board, "DRE-7978")
    assert _post(tmp_path, board, "DRE-7978",
                 _transcript("DRE-7978", pull_request=PR_URL)) == 0
    assert _ending(board.newest("DRE-7978"))["pull_request"] == PR_URL


def test_do_post_posts_a_failed_runs_receipt(tmp_path):
    board = _board("DRE-8637", adds=CARD_DONE)
    _claim(board, "DRE-8637")
    assert _post(tmp_path, board, "DRE-8637", _transcript("DRE-8637", exits=(0, 1))) == 0
    body = board.newest("DRE-8637")
    assert _ending(body)["ending"] == "failed"
    assert _last_line(body) == (f"failed: `{VARIABLE_LIST}` — exit 1; "
                                "the card is left where it was")
    assert f"left changed: `{VARIABLE_SET}`" in body


def test_do_post_posts_a_refused_plans_receipt_with_no_claim(tmp_path):
    board = _board("DRE-8637", adds=CARD_DONE)
    assert _post(tmp_path, board, "DRE-8637", _refusal("DRE-8637")) == 0
    assert len(board.bodies("DRE-8637")) == 1
    body = board.newest("DRE-8637")
    assert _ending(body)["ending"] == "failed"
    assert _last_line(body) == "failed: `check` — exit 1; the card is left where it was"


def test_do_post_posts_a_continues_receipt_when_the_plan_says_so(tmp_path):
    board = _board("DRE-8637", adds=CARD_DONE)
    _claim(board, "DRE-8637")
    continues = {"after": "2026-11-03T18:00Z", "remains": "list the variable once more"}
    assert _post(tmp_path, board, "DRE-8637", _transcript("DRE-8637"),
                 continues=continues) == 0
    body = board.newest("DRE-8637")
    ending = _ending(body)
    assert ending["ending"] == "continues"
    assert ending["after"] == datetime(2026, 11, 3, 18, 0, tzinfo=UTC)
    assert _last_line(body) == "continues: after 2026-11-03T18:00Z — list the variable once more"


@pytest.mark.parametrize("transcript", [None, "{not json", "[]"])
def test_do_post_posts_no_transcript_when_the_transcript_is_missing_or_unreadable(
        tmp_path, transcript):
    board = _board("DRE-8637", adds=CARD_DONE)
    _claim(board, "DRE-8637")
    assert _post(tmp_path, board, "DRE-8637", transcript) == 0
    body = board.newest("DRE-8637")
    assert _last_line(body) == operator_decide.NO_TRANSCRIPT
    assert _ending(body)["ending"] == "failed"


def test_do_post_posts_nothing_for_a_dry_run(tmp_path):
    board = _board("DRE-8637", adds=CARD_DONE)
    _claim(board, "DRE-8637")
    before = board.bodies("DRE-8637")
    transcript = _transcript("DRE-8637", dry_run=True)
    for ran in transcript["commands"]:
        ran.pop("exit")
    assert _post(tmp_path, board, "DRE-8637", transcript) == 0
    assert board.bodies("DRE-8637") == before
    assert board.writes() == []


def test_do_post_posts_nothing_when_a_receipt_already_stands_after_the_claim(tmp_path, capsys):
    board = _board("DRE-8637", adds=CARD_DONE)
    _claim(board, "DRE-8637")
    _receipt(board, "DRE-8637", {"ending": "close", "rule": "card-done"})
    assert _post(tmp_path, board, "DRE-8637", _transcript("DRE-8637")) == 3
    assert board.writes() == []
    assert _post(tmp_path, board, "DRE-8637", _refusal("DRE-8637")) == 3
    assert board.writes() == []


def test_do_post_posts_nothing_when_no_claim_stands_for_a_run_that_ran(tmp_path):
    board = _board("DRE-8637", adds=CARD_DONE)
    assert _post(tmp_path, board, "DRE-8637", _transcript("DRE-8637")) == 1
    assert board.writes() == []


def test_a_receipt_linear_refuses_exits_4_and_the_claim_still_stands(tmp_path, capsys):
    board = _board("DRE-8637", adds=CARD_DONE)
    _claim(board, "DRE-8637")
    claim = board.newest("DRE-8637")
    board.fail("comment")
    assert _post(tmp_path, board, "DRE-8637", _transcript("DRE-8637")) == 4
    assert board.bodies("DRE-8637") == [claim]
    found = REFUSED.search(capsys.readouterr().out)
    assert found and found.group("what").startswith("the receipt")
    assert _last_line(claim) == operator_decide.CLAIM_LAST
    records = board.comment_records("DRE-8637", whole_thread=True)
    action, _record = operator_agent.do_standing(
        records, _decision(board, "DRE-8637").fingerprint, NOW)
    assert action == operator_agent.SPENT


# --------------------------------------------------------------------------- #
# do-close                                                                     #
# --------------------------------------------------------------------------- #


def _closing(card: str, rule: str, *, adds=None, labels=None, pull_request=None,
             receipt=True) -> DoBoard:
    """A card whose newest `do` comment is a receipt ending `close: <rule>`."""
    board = _board(card, adds=adds, labels=labels)
    _claim(board, card)
    if receipt:
        _receipt(board, card, {"ending": "close", "rule": rule},
                 _transcript(card, pull_request=pull_request))
    return board


def _receipt_at(board: DoBoard, card: str) -> datetime:
    stamp = board.cards[card].comments[-1].created_at
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _close(board: DoBoard, card: str, console: Console, gh: Gh | None = None) -> int:
    with board.patched(), console.patched(), (gh or Gh(fails=True)).patched():
        return _run("do-close", card)


def _no_state_write(board: DoBoard, card: str) -> bool:
    return not [kind for kind, _ in board.writes(card) if kind.startswith("state")]


def test_record_the_outcome_closes_a_no_code_card_through_the_console(capsys):
    board = _closing("DRE-7328", "record-outcome")
    receipt = board.newest("DRE-7328")
    console = Console()
    assert _close(board, "DRE-7328", console) == 0
    assert console.calls == [{
        "card": "DRE-7328", "outcome": f"record-outcome: {STEP}",
        "receipt_url": board._comment_url("DRE-7328", 1)}]
    assert board.cards["DRE-7328"].comments[1].body == receipt
    assert _no_state_write(board, "DRE-7328")
    assert board.writes() == []
    assert "operator-agent: DRE-7328 closed — record-outcome · console" in \
        capsys.readouterr().out


def test_card_done_closes_a_no_code_card_through_the_console_and_never_by_state(capsys):
    board = _closing("DRE-8637", "card-done", adds=CARD_DONE)
    console = Console(outcome="already-recorded")
    with patch.object(linear_ops, "cmd_card_done",
                      side_effect=AssertionError("cmd_card_done was called")):
        assert _close(board, "DRE-8637", console) == 0
    assert [c["outcome"] for c in console.calls] == [f"card-done: {STEP}"]
    assert _no_state_write(board, "DRE-8637")
    assert "operator-agent: DRE-8637 closed — card-done · console" in capsys.readouterr().out


def test_card_done_moves_a_card_without_no_code_to_done_alone(capsys):
    labels = [n for n in _cards()["DRE-8637"]["labels"] if n != "no-code"]
    board = _closing("DRE-8637", "card-done", adds=CARD_DONE, labels=labels)
    assert _close(board, "DRE-8637", Console(forbid=True)) == 0
    assert board.writes("DRE-8637") == [("state", "Done")]
    assert board.lane("DRE-8637") == "Done"
    assert "operator-agent: DRE-8637 closed — card-done · state" in capsys.readouterr().out


def test_the_state_move_is_conditional_on_the_lane_the_close_read():
    labels = [n for n in _cards()["DRE-8637"]["labels"] if n != "no-code"]
    board = _closing("DRE-8637", "card-done", adds=CARD_DONE, labels=labels)
    seen = []

    def state(identifier, name, *flags, expect=None, **kw):
        seen.append((identifier, name, expect))
        return board.cmd_state(identifier, name, *flags, expect=expect, **kw)

    with board.patched(), Console(forbid=True).patched(), \
            patch.object(linear_ops, "cmd_state", side_effect=state):
        assert _run("do-close", "DRE-8637") == 0
    assert seen == [("DRE-8637", "Done", ("Queued",))]


def test_own_pr_merge_closes_once_the_pull_request_merged(capsys):
    board = _closing("DRE-7978", "own-pr-merge", adds=OWN_PR_MERGE, pull_request=PR_URL)
    console, gh = Console(), Gh(state="MERGED")
    assert _close(board, "DRE-7978", console, gh) == 0
    assert gh.calls == [["gh", "pr", "view", PR_URL, "--json", "state,mergedAt"]]
    assert [c["outcome"] for c in console.calls] == [f"own-pr-merge: {STEP}"]
    assert "operator-agent: DRE-7978 closed — own-pr-merge · console" in capsys.readouterr().out


def test_own_pr_merge_waits_while_the_pull_request_is_open(capsys):
    board = _closing("DRE-7978", "own-pr-merge", adds=OWN_PR_MERGE, pull_request=PR_URL)
    board.now = _receipt_at(board, "DRE-7978") + timedelta(days=3)
    assert _close(board, "DRE-7978", Console(forbid=True), Gh(state="OPEN", merged_at=None)) == 0
    assert board.writes() == []
    assert (f"operator-agent: DRE-7978 close waits — pull request {PR_URL} is open"
            in capsys.readouterr().out)
    records = board.comment_records("DRE-7978", whole_thread=True)
    action, _ = operator_agent.do_standing(
        records, _decision(board, "DRE-7978").fingerprint, board.now)
    assert action == operator_agent.CLOSE_ONLY


def test_own_pr_merge_open_past_pr_wait_days_posts_one_close_failure_line():
    board = _closing("DRE-7978", "own-pr-merge", adds=OWN_PR_MERGE, pull_request=PR_URL)
    days = operator_do_ops.load()["pr_wait_days"]
    assert days == 14
    board.now = _receipt_at(board, "DRE-7978") + timedelta(days=days, minutes=1)
    gh = Gh(state="OPEN", merged_at=None)
    assert _close(board, "DRE-7978", Console(forbid=True), gh) == 0
    assert [kind for kind, _ in board.writes("DRE-7978")] == ["comment"]
    body = board.newest("DRE-7978")
    assert _ending(body) == {"word": "do-close", "ending": "failed", "rule": None, "after": None,
                             "remains": None, "pull_request": None}
    assert PR_URL in body and "14 days" in body
    assert _close(board, "DRE-7978", Console(forbid=True), gh) == 0
    assert len(board.writes("DRE-7978")) == 1


def test_own_pr_merge_closed_unmerged_posts_one_close_failure_line_and_never_another(capsys):
    board = _closing("DRE-7978", "own-pr-merge", adds=OWN_PR_MERGE, pull_request=PR_URL)
    gh = Gh(state="CLOSED", merged_at=None)
    assert _close(board, "DRE-7978", Console(forbid=True), gh) == 0
    assert [kind for kind, _ in board.writes("DRE-7978")] == ["comment"]
    body = board.newest("DRE-7978")
    assert _ending(body)["word"] == "do-close"
    assert "closed unmerged" in body
    capsys.readouterr()
    assert _close(board, "DRE-7978", Console(forbid=True), gh) == 0
    assert len(board.writes("DRE-7978")) == 1
    assert "operator-agent: DRE-7978 close — nothing owed" in capsys.readouterr().out
    records = board.comment_records("DRE-7978", whole_thread=True)
    action, _ = operator_agent.do_standing(
        records, _decision(board, "DRE-7978").fingerprint, NOW)
    assert action == operator_agent.SPENT


def test_own_pr_merge_with_no_pull_request_line_posts_one_close_failure_line():
    board = _closing("DRE-7978", "own-pr-merge", adds=OWN_PR_MERGE)
    gh = Gh()
    assert _close(board, "DRE-7978", Console(forbid=True), gh) == 0
    assert gh.calls == []
    assert [kind for kind, _ in board.writes("DRE-7978")] == ["comment"]
    assert _ending(board.newest("DRE-7978"))["word"] == "do-close"


def test_a_pull_request_read_that_fails_may_land_later(capsys):
    board = _closing("DRE-7978", "own-pr-merge", adds=OWN_PR_MERGE, pull_request=PR_URL)
    assert _close(board, "DRE-7978", Console(forbid=True), Gh(fails=True)) == 4
    assert board.writes() == []
    assert REFUSED.search(capsys.readouterr().out)


def test_a_card_that_names_no_close_rule_is_not_closed(capsys):
    board = _closing("DRE-8623", "none")
    assert _last_line(board.newest("DRE-8623")) == operator_decide.NONE_NAMED
    assert _close(board, "DRE-8623", Console(forbid=True), Gh()) == 0
    assert board.writes() == []
    assert "operator-agent: DRE-8623 close — nothing owed" in capsys.readouterr().out


@pytest.mark.parametrize("why", ["claim-only", "failed", "guard-returned", "in-done",
                                 "text-changed"])
def test_do_close_owes_nothing_unless_a_closing_receipt_stands(capsys, why):
    board = _closing("DRE-8637", "card-done", adds=CARD_DONE, receipt=why != "claim-only")
    if why == "failed":
        _receipt(board, "DRE-8637", {"ending": "failed"}, _transcript("DRE-8637", exits=(1, 0)))
    if why == "guard-returned":
        board.say("DRE-8637", f"{operator_decide.GUARD_RETURN_TAG} — no Outcome record",
                  user=PERSON_USER)
    if why == "in-done":
        board.move("DRE-8637", "Done")
    if why == "text-changed":
        board.cards["DRE-8637"].description += "\n\nOne more sentence."
    assert _close(board, "DRE-8637", Console(forbid=True), Gh()) == 0
    assert board.writes() == []
    assert "operator-agent: DRE-8637 close — nothing owed" in capsys.readouterr().out


def test_a_receipt_written_by_someone_else_owes_nothing(capsys):
    board = _board("DRE-8637", adds=CARD_DONE)
    board.say("DRE-8637", _posted(operator_decide.do_receipt_text(
        _decision(board, "DRE-8637"), _transcript("DRE-8637"),
        {"ending": "close", "rule": "card-done"}, NOW)), user=PERSON_USER)
    assert _close(board, "DRE-8637", Console(forbid=True), Gh()) == 0
    assert board.writes() == []


@pytest.mark.parametrize("status", [503, 401, 403, 404, 500, None])
def test_a_console_close_that_may_land_later_writes_nothing_and_is_tried_again(capsys, status):
    board = _closing("DRE-7328", "record-outcome")
    assert _close(board, "DRE-7328", Console(status=status)) == 4
    assert board.writes() == []
    found = REFUSED.search(capsys.readouterr().out)
    assert found and found.group("card") == "DRE-7328"
    records = board.comment_records("DRE-7328", whole_thread=True)
    action, _ = operator_agent.do_standing(
        records, _decision(board, "DRE-7328").fingerprint, NOW)
    assert action == operator_agent.CLOSE_ONLY
    console = Console()
    assert _close(board, "DRE-7328", console) == 0
    assert len(console.calls) == 1
    assert "closed — record-outcome · console" in capsys.readouterr().out


def test_a_state_move_answering_false_writes_nothing_and_is_tried_again(capsys):
    labels = [n for n in _cards()["DRE-8637"]["labels"] if n != "no-code"]
    board = _closing("DRE-8637", "card-done", adds=CARD_DONE, labels=labels)
    with board.patched(), Console(forbid=True).patched(), \
            patch.object(linear_ops, "cmd_state", return_value=False):
        assert _run("do-close", "DRE-8637") == 4
    assert board.writes() == []
    assert REFUSED.search(capsys.readouterr().out)
    assert _close(board, "DRE-8637", Console(forbid=True)) == 0
    assert board.lane("DRE-8637") == "Done"


def test_no_door_url_refuses_the_close_and_writes_no_outcome_comment(capsys):
    """With no `BUREAU_READ_URL` the real client answers `no-url`: a refusal,
    and never an `Outcome:` comment written in the console's place."""
    board = _closing("DRE-7328", "record-outcome")
    with board.patched(), Gh(fails=True).patched():
        assert _run("do-close", "DRE-7328") == 4
    assert board.writes() == []
    out = capsys.readouterr().out
    assert "console-outcome: no-url card=DRE-7328" in out
    assert REFUSED.search(out)


@pytest.mark.parametrize("status", [409, 400])
def test_a_console_close_that_never_can_posts_one_close_failure_line(capsys, status):
    board = _closing("DRE-7328", "record-outcome")
    assert _close(board, "DRE-7328", Console(status=status)) == 0
    assert [kind for kind, _ in board.writes("DRE-7328")] == ["comment"]
    body = board.newest("DRE-7328")
    assert _ending(body)["word"] == "do-close"
    assert _last_line(body).endswith("— the card is left where it was")
    assert str(status) in body
    assert not any(line.lower().startswith("outcome:") for line in body.splitlines())
    assert _close(board, "DRE-7328", Console(forbid=True)) == 0
    assert len(board.writes("DRE-7328")) == 1


def test_a_close_failure_line_linear_refuses_exits_4(capsys):
    board = _closing("DRE-7328", "record-outcome")
    board.fail("comment")
    assert _close(board, "DRE-7328", Console(status=409)) == 4
    assert board.writes() == []
    assert REFUSED.search(capsys.readouterr().out)


# --- the read door -----------------------------------------------------------


def test_do_close_reads_its_card_and_whole_thread_through_the_read_door():
    board = _closing("DRE-7328", "record-outcome")
    assert _close(board, "DRE-7328", Console()) == 0
    assert board.door_asks == [{"endpoint": "/cards", "ids": "DRE-7328", "comments": "all",
                                "relations": "0"}]
    assert not [r for r in board.reads if r[0] in ("comment_records", "card")]


def test_do_close_asks_the_door_for_dispatch_freshness():
    board = _closing("DRE-7328", "record-outcome")
    seen = {}
    real = bureau_read.cards

    def cards(ids, **kw):
        seen.update(kw, ids=list(ids))
        return real(ids, **kw)

    with patch.object(bureau_read, "cards", side_effect=cards):
        assert _close(board, "DRE-7328", Console()) == 0
    assert seen == {"ids": ["DRE-7328"], "max_age": bureau_read.DISPATCH_CARDS_MAX_AGE,
                    "comments": "all", "relations": False}


def test_do_close_falls_back_to_linear_on_a_read_unknown_with_the_read_fallback_line(capsys):
    board = _closing("DRE-7328", "record-outcome")
    board.door_unknown = bureau_read.ReadUnknown("closed", "HTTP 503", status=503,
                                                 unavailable=True)
    console = Console()
    assert _close(board, "DRE-7328", console) == 0
    err = capsys.readouterr().err
    assert re.search(r'^read-fallback: reader=".+" read=cards reason=closed$', err, re.MULTILINE)
    assert ("comment_records", "DRE-7328", True) in board.reads
    assert len(console.calls) == 1


# --------------------------------------------------------------------------- #
# console_outcome.py — the outcome door's client                               #
# --------------------------------------------------------------------------- #


class Door(http.server.BaseHTTPRequestHandler):
    """The console's outcome door on loopback, answering what the test set."""

    answer: tuple = (200, {"card": "DRE-7328", "outcome": "recorded"})
    seen: list = []

    def do_POST(self):  # noqa: N802 — the stdlib's name
        length = int(self.headers.get("Content-Length") or 0)
        Door.seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                          "body": json.loads(self.rfile.read(length) or b"null")})
        status, body = Door.answer
        raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@pytest.fixture
def door(monkeypatch):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Door)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    Door.seen = []
    Door.answer = (200, {"card": "DRE-7328", "outcome": "recorded"})
    monkeypatch.setenv("BUREAU_READ_URL", f"http://127.0.0.1:{server.server_port}/base")
    minted = []

    def mint():
        minted.append(os.environ.get(pipeline_notice.AUDIENCE_ENV)
                      or pipeline_notice.DEFAULT_AUDIENCE)
        return "oidc-token-1"

    monkeypatch.setattr(pipeline_notice, "_mint", mint)
    yield {"minted": minted}
    server.shutdown()
    server.server_close()


@contextlib.contextmanager
def _no_linear():
    """A Linear stub that fails the test if anything is called on it."""
    def boom(*args, **kwargs):
        raise AssertionError("console_outcome.py reached Linear")
    with contextlib.ExitStack() as stack:
        for name in ("cmd_comment", "gql", "create_card", "find_open", "cmd_state"):
            stack.enter_context(patch.object(linear_ops, name, side_effect=boom))
        yield


def test_console_outcome_posts_the_card_the_outcome_and_the_receipt_url(door, capsys):
    url = "https://linear.app/dreadnoughtfoundry/issue/DRE-7328#comment-0001"
    with _no_linear():
        answer = console_outcome.record("DRE-7328", f"record-outcome: {STEP}", url)
    assert answer.status == 200 and answer.outcome == "recorded" and answer.recorded
    assert Door.seen == [{"path": "/base/api/v1/pipeline/outcomes",
                          "auth": "Bearer oidc-token-1",
                          "body": {"card": "DRE-7328", "outcome": f"record-outcome: {STEP}",
                                   "receipt_url": url}}]
    assert capsys.readouterr().out == "console-outcome: recorded card=DRE-7328\n"


def test_console_outcome_mints_for_the_notice_audience_imported_never_copied(door, monkeypatch):
    with _no_linear():
        console_outcome.record("DRE-7328", "card-done: x", "https://linear.app/x")
    assert door["minted"] == [pipeline_notice.DEFAULT_AUDIENCE]
    monkeypatch.setenv("BUREAU_NOTICE_AUDIENCE", "https://example.test/aud")
    with _no_linear():
        console_outcome.record("DRE-7328", "card-done: x", "https://linear.app/x")
    assert door["minted"][-1] == "https://example.test/aud"
    source = (ROOT / "scripts" / "console_outcome.py").read_text()
    assert pipeline_notice.DEFAULT_AUDIENCE not in source


def test_console_outcome_reads_already_recorded_as_recorded(door, capsys):
    Door.answer = (200, {"card": "DRE-7328", "outcome": "already-recorded"})
    with _no_linear():
        answer = console_outcome.record("DRE-7328", "card-done: x", "https://linear.app/x")
    assert answer.recorded and answer.outcome == "already-recorded"
    assert capsys.readouterr().out == "console-outcome: already-recorded card=DRE-7328\n"


@pytest.mark.parametrize("status, never", [(400, True), (409, True), (401, False),
                                           (403, False), (404, False), (503, False)])
def test_console_outcome_hands_back_every_refusal_and_writes_no_comment(door, capsys, status,
                                                                         never):
    Door.answer = (status, {"error": "refused"})
    with _no_linear():
        answer = console_outcome.record("DRE-7328", "card-done: x", "https://linear.app/x")
    assert answer.status == status and not answer.recorded
    assert answer.never is never
    assert capsys.readouterr().out == f"console-outcome: http-{status} card=DRE-7328\n"


@pytest.mark.parametrize("body", [b"not json", {"card": "DRE-7328", "outcome": "filed"},
                                  {"card": "DRE-1", "outcome": "recorded"}])
def test_a_malformed_200_is_not_a_close(door, body):
    Door.answer = (200, body)
    with _no_linear():
        answer = console_outcome.record("DRE-7328", "card-done: x", "https://linear.app/x")
    assert not answer.recorded and not answer.never
    assert answer.reason == "malformed"


def test_console_outcome_with_no_door_writes_no_comment(monkeypatch, capsys):
    with _no_linear():
        answer = console_outcome.record("DRE-7328", "card-done: x", "https://linear.app/x")
    assert answer.status is None and not answer.recorded and not answer.never
    assert capsys.readouterr().out == "console-outcome: no-url card=DRE-7328\n"


def test_console_outcome_with_no_oidc_token_writes_no_comment(monkeypatch, capsys):
    monkeypatch.setenv("BUREAU_READ_URL", "https://app.agent-bureau.com")
    with _no_linear():
        answer = console_outcome.record("DRE-7328", "card-done: x", "https://linear.app/x")
    assert answer.reason == "no-token" and not answer.recorded and not answer.never
    assert capsys.readouterr().out == "console-outcome: no-token card=DRE-7328\n"


def test_console_outcome_unreachable_writes_no_comment(monkeypatch, capsys):
    monkeypatch.setenv("BUREAU_READ_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(pipeline_notice, "_mint", lambda: "oidc-token-1")
    with _no_linear():
        answer = console_outcome.record("DRE-7328", "card-done: x", "https://linear.app/x")
    assert answer.reason == "unreachable" and answer.status is None
    assert capsys.readouterr().out == "console-outcome: unreachable card=DRE-7328\n"


# --------------------------------------------------------------------------- #
# the act registry                                                             #
# --------------------------------------------------------------------------- #


def test_the_operator_agent_act_covers_a_do_cards_claim_receipt_failure_line_and_close():
    row = next(a for a in json.loads(ACTS.read_text())["acts"] if a["name"] == "operator-agent")
    said = f"{row['means']} {row['why']}"
    for word in ("do-claim", "do-post", "do-close", "claim", "receipt", "close-failure",
                 "console_outcome.py"):
        assert word in said, word


def test_the_act_registry_check_passes():
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "pipeline_act.py"), "check"],
                          capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


def test_nothing_added_names_a_person_an_email_or_a_sign_in():
    for path in (ROOT / "scripts" / "console_outcome.py", FIXTURE):
        text = path.read_text()
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), path
        assert not re.search(r"\b(sign-in|signed in|Cognito)\b", text, re.IGNORECASE), path
