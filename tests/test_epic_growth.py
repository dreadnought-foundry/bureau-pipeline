"""RED-first: an epic that grows well past the size the CEO approved is put
back in front of him (DRE-6414).

The sweep printed `epic-growth: DRE-4721 green-lit at 10 cards, now running 50
cards` every pass, and nothing compared the two numbers. Now:

  * `config/epic-growth.json` holds the threshold — more than `ratio` times
    the approved size, and at least `minimum_added` more cards;
  * an epic past it gets ONE question card, created by the sweep in Green
    Light, `no-code`, parentless, carrying the three declared lines
    (`scripts/epic_growth.py`, rendered by `console_escalation.render`); the
    epic keeps running;
  * the epic's growth record carries the question's line, which is what stops
    a second one;
  * the CEO's signed answer settles it: `re-approve` closes the card and moves
    the approved size to the count he answered at, `split` files a mid-epic
    amendment, and a card that left Green Light unanswered is withdrawn.

The pure module is tested directly; the sweep is driven through
`reconcile.report_epic_growth` over a fake Linear that holds one epic, its
children and the question cards the sweep creates.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_growth.py -v
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import console_escalation  # noqa: E402
import epic_growth  # noqa: E402
import green_light_reply  # noqa: E402
import linear_ops  # noqa: E402
import mid_epic  # noqa: E402
import reconcile  # noqa: E402
import spoken_thread  # noqa: E402

EPIC = "DRE-4721"
GREEN_LIGHT = "2026-10-01T17:00:00.000Z"
BEFORE = "2026-10-01T16:00:00.000Z"
JOINED = "2026-10-03T18:30:00.000Z"
CEO = "ceo-linear-user"
SOMEONE = "someone-else"
BOT = "pipeline-bot"


# --------------------------------------------------------------------------- #
# the threshold                                                                #
# --------------------------------------------------------------------------- #


class TestTheThresholdIsData:
    def test_the_file_states_the_numbers_and_who_decided_them(self):
        doc = json.loads((ROOT / "config" / "epic-growth.json").read_text())
        assert (doc["ratio"], doc["minimum_added"]) == (2, 10)
        for key in ("decided", "decided_by", "why"):
            assert str(doc[key]).strip(), key
        assert "DRE-6414" in doc["decided_by"]

    def test_the_reader_reads_the_file(self):
        assert epic_growth.threshold() == epic_growth.Threshold(ratio=2, minimum_added=10)

    def test_a_file_without_numbers_is_refused(self, tmp_path):
        bad = tmp_path / "epic-growth.json"
        bad.write_text(json.dumps({"ratio": "two", "minimum_added": 10}))
        with pytest.raises(ValueError):
            epic_growth.threshold(bad)

    def test_the_readme_names_the_file(self):
        assert "`epic-growth.json`" in (ROOT / "config" / "README.md").read_text()

    @pytest.mark.parametrize("approved, running, crossed", [
        (10, 25, True),    # 25 > 20 and 15 ≥ 10
        (10, 20, False),   # 20 is not more than double
        (4, 9, False),     # 5 added is under 10
        (17, 41, True),    # DRE-5577
        (10, 50, True),    # DRE-4721
        (25, 45, False),   # re-approved at 25: 45 is not more than 50
        (25, 51, True),
        (None, 50, False),  # an unreadable green light never crosses
    ])
    def test_crossed(self, approved, running, crossed):
        assert epic_growth.crossed(approved, running) is crossed


# --------------------------------------------------------------------------- #
# the question                                                                 #
# --------------------------------------------------------------------------- #

JOINED_CARDS = [
    {"id": "DRE-900", "route": "addition", "because": "a second call site"},
    {"id": "DRE-901", "route": "unrecorded",
     "because": "unrecorded: joined 2026-10-03 11:30 PT with no discovery record"},
]


class TestTheQuestion:
    def test_the_title(self):
        assert epic_growth.title(EPIC, 10, 25) == (
            "Epic grew past its green light: DRE-4721 — approved at 10 cards, running 25")
        assert epic_growth.title(EPIC, 10, 25).startswith(epic_growth.title_prefix(EPIC))

    def test_the_body_is_the_three_lines_and_nothing_else(self):
        body = epic_growth.body(EPIC, 10, 25, JOINED_CARDS)
        assert console_escalation.problems(body) == []
        assert body == console_escalation.render(console_escalation.parse(body))
        assert len(body.split("\n")) == 3
        assert "```" not in body, "no escalation-choices block: the answer is his words"

    def test_the_finding_names_both_numbers_and_every_joined_card_with_its_route(self):
        esc = console_escalation.parse(epic_growth.body(EPIC, 10, 25, JOINED_CARDS))
        for needle in (EPIC, "10 cards", "running 25",
                       "DRE-900 (addition: a second call site)",
                       "DRE-901 (unrecorded: joined 2026-10-03 11:30 PT with no "
                       "discovery record)"):
            assert needle in esc.finding, needle

    def test_the_question_asks_for_one_of_two_words(self):
        esc = console_escalation.parse(epic_growth.body(EPIC, 10, 25, JOINED_CARDS))
        assert "re-approve" in esc.question and "split" in esc.question

    @pytest.mark.parametrize("approved, running, pick", [
        (10, 25, "re-approve"), (10, 29, "re-approve"),
        (10, 30, "split"), (10, 50, "split"),
    ])
    def test_the_recommendation_is_split_at_three_times(self, approved, running, pick):
        esc = console_escalation.parse(epic_growth.body(EPIC, approved, running, []))
        assert esc.recommendation == pick
        assert esc.why.strip()

    def test_the_question_is_written_without_code(self):
        body = epic_growth.body(EPIC, 10, 25, JOINED_CARDS)
        for word in ("`", ".py", "reconcile", "mid_epic", "sweep"):
            assert word not in body, word


# --------------------------------------------------------------------------- #
# the answer                                                                   #
# --------------------------------------------------------------------------- #

CONSOLE_HEAD = "Answer from Test Owner (signed in to the console), 2026-10-09 09:52 PT:"


class TestTheWords:
    @pytest.mark.parametrize("text, outcome", [
        ("Re-approve", epic_growth.RE_APPROVE),
        ("reapprove, keep going", epic_growth.RE_APPROVE),
        ("Approve.", epic_growth.RE_APPROVE),
        ("Split it along the seam", epic_growth.SPLIT),
        ("SPLIT", epic_growth.SPLIT),
        ("approve the split", None),
        ("not sure yet", None),
        ("", None),
        ("let me think\nsplit", None),  # only the first line is read
        (f"{CONSOLE_HEAD}\n\nsplit\n\n🔏 console-answer: v1 card=DRE-7001", epic_growth.SPLIT),
    ])
    def test_the_first_line_decides(self, text, outcome):
        assert epic_growth.read_words(text) == outcome


#: The console's comment box writes this heading, not the Answer box's — the
#: shape of the 15:21 PT comment on DRE-6480 (DRE-6503).
COMMENT_HEAD = "Comment from Sid Conklin (signed in to the console), 2026-10-09 15:21 PT:"
RECEIPT = ("🔏 console-answer: v1 card=DRE-6480 sha256=" + "0" * 64
           + " user=sid at=2026-10-09T22:21:58Z kid=" + "0" * 16 + " sig=" + "A" * 86)


def _commented(words):
    return f"{COMMENT_HEAD}\n\n{words}\n\n{RECEIPT}"


class TestTheCommentBox:
    def test_re_approve_under_a_comment_heading_is_a_re_approval(self):
        assert epic_growth.read_words(_commented("re-approve")) == epic_growth.RE_APPROVE

    def test_split_under_a_comment_heading_is_a_split(self):
        assert epic_growth.read_words(_commented("split")) == epic_growth.SPLIT

    def test_neither_word_under_a_comment_heading_is_unreadable(self):
        assert epic_growth.read_words(_commented("let me look tomorrow")) is None
        assert _answer(_voice(spoken_thread.CEO_VIA_CONSOLE, LATER,
                              _commented("let me look tomorrow"))) == epic_growth.UNREADABLE

    def test_his_signed_comment_is_his_answer(self):
        assert _answer(_voice(spoken_thread.CEO_VIA_CONSOLE, LATER,
                              _commented("re-approve"))) == epic_growth.RE_APPROVE

    def test_the_answer_box_heading_still_reads(self):
        assert epic_growth.read_words(
            f"{CONSOLE_HEAD}\n\nre-approve\n\n{RECEIPT}") == epic_growth.RE_APPROVE

    def test_only_a_heading_on_the_first_line_is_stripped(self):
        assert epic_growth.read_words(f"not sure\n{COMMENT_HEAD}\nre-approve") is None


def _voice(kind, at, body="", author=None):
    node = {"body": body, "createdAt": at, "user": {"id": author} if author else None}
    return node, spoken_thread.Voice(kind, kind, at, body)


ASKED = "2026-10-05T17:00:00Z"
LATER = "2026-10-06T17:00:00Z"
LATEST = "2026-10-07T17:00:00Z"


def _answer(*pairs, ids=frozenset({CEO}), after=ASKED):
    nodes = [n for n, _ in pairs]
    voices = [v for _, v in pairs]
    return epic_growth.answer(nodes, voices, ids, after=after)


class TestHisSignedAnswer:
    def test_a_console_answer_is_his(self):
        assert _answer(_voice(spoken_thread.CEO_VIA_CONSOLE, LATER, "split")) == epic_growth.SPLIT

    def test_a_declared_linear_user_is_his(self):
        assert _answer(_voice(spoken_thread.PERSON, LATER, "re-approve", CEO)) == (
            epic_growth.RE_APPROVE)

    def test_any_other_person_is_not(self):
        assert _answer(_voice(spoken_thread.PERSON, LATER, "split", SOMEONE)) is None

    def test_the_pipeline_is_not(self):
        assert _answer(_voice(spoken_thread.PIPELINE, LATER, "split", BOT)) is None

    def test_the_newest_of_his_comments_decides(self):
        assert _answer(
            _voice(spoken_thread.PERSON, LATER, "split", CEO),
            _voice(spoken_thread.PERSON, LATEST, "re-approve", CEO),
        ) == epic_growth.RE_APPROVE

    def test_a_comment_before_the_question_was_asked_is_not_an_answer(self):
        assert _answer(_voice(spoken_thread.PERSON, "2026-10-04T17:00:00Z", "split", CEO)) is None

    @pytest.mark.parametrize("kind", [spoken_thread.REFUSED, spoken_thread.UNCHECKED])
    def test_a_withheld_console_answer_is_left_for_a_person(self, kind):
        assert _answer(_voice(kind, LATER)) == epic_growth.UNREADABLE

    def test_his_words_neither_rule_reads_are_left_for_a_person(self):
        assert _answer(_voice(spoken_thread.PERSON, LATER, "hmm", CEO)) == epic_growth.UNREADABLE


class TestTheClosingComment:
    def test_re_approved(self):
        assert epic_growth.closing_comment(
            epic_growth.RE_APPROVE, epic=EPIC, running=25, at="2026-10-09 10:00 PT"
        ) == "✅ epic-growth-question: re-approved at 25 cards on 2026-10-09 10:00 PT"

    def test_split(self):
        assert epic_growth.closing_comment(
            epic_growth.SPLIT, epic=EPIC, running=25, at="2026-10-09 10:00 PT"
        ) == ("✅ epic-growth-question: split asked at 25 cards on 2026-10-09 10:00 PT "
              "— amendment filed on DRE-4721")

    def test_the_amendment_because(self):
        assert epic_growth.split_because(10, 25, "DRE-7001") == (
            "the CEO asked at 25 cards, approved at 10, that this epic be split "
            "along its seam — DRE-7001")


# --------------------------------------------------------------------------- #
# the sweep, over a fake Linear                                                #
# --------------------------------------------------------------------------- #


def _now_plus(minutes: int) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


class Board:
    """One epic, its children and the cards the sweep creates — the Linear the
    sweep's growth phase reads and writes, nothing more."""

    def __init__(self, approved: int, running: int, *, green_lit_at=GREEN_LIGHT,
                 description="The plan."):
        self.description = description
        self.green_lit_at = green_lit_at
        self.children = [{"identifier": f"DRE-{100 + n}", "createdAt": BEFORE}
                         for n in range(approved)]
        self.states = {EPIC: "In Progress"}
        self.titles: dict[str, str] = {}
        self.threads: dict[str, list] = {EPIC: []}
        self.creates: list[dict] = []
        self.moves: list[tuple[str, str]] = []
        self.posts: list[tuple[str, str]] = []
        self._next = 7001
        self.grow_to(running)

    # --- the board's own motions -----------------------------------------
    def grow_to(self, running: int) -> None:
        while len(self.children) < running:
            self.children.append({"identifier": f"DRE-{900 + len(self.children)}",
                                  "createdAt": JOINED})

    def say(self, card: str, body: str, author: str = CEO) -> None:
        self.threads[card].append({"body": body, "createdAt": _now_plus(1),
                                   "user": {"id": author}})

    def drag(self, card: str, lane: str) -> None:
        self.states[card] = lane

    def record(self) -> dict:
        return mid_epic.parse_artifact(self.description)

    def questions(self) -> list:
        return self.record()["questions"]

    # --- linear_ops ------------------------------------------------------
    def epic_record(self) -> dict:
        history = ([{"createdAt": self.green_lit_at, "toState": {"name": "In Progress"}}]
                   if self.green_lit_at else [])
        return {
            "id": "uuid-epic", "identifier": EPIC, "description": self.description,
            "state": {"name": self.states[EPIC]},
            "children": {"nodes": [dict(c) for c in self.children]},
            "history": {"nodes": history},
            "comments": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        }

    def gql(self, query, variables=None):
        assert "children(first: 250)" in query, query
        return {"issue": self.epic_record()}

    def set_description(self, identifier, body):
        assert identifier == EPIC
        self.description = body

    def cmd_comment(self, identifier, body):
        self.posts.append((identifier, body))
        self.threads.setdefault(identifier, []).append(
            {"body": body, "createdAt": _now_plus(0), "user": {"id": BOT}})

    def count_comments(self, identifier, needle, **kw):
        return sum(1 for n in self.threads.get(identifier, []) if needle in n["body"])

    def cmd_state(self, identifier, state, *flags, **kw):
        self.moves.append((identifier, state))
        self.states[identifier] = state
        return True

    def create_card(self, title, description, *, repo_slug, labels=(), lane="Intake"):
        ident = f"DRE-{self._next}"
        self._next += 1
        self.creates.append({"identifier": ident, "title": title, "body": description,
                             "repo_slug": repo_slug, "labels": tuple(labels),
                             "lane": lane})
        self.states[ident] = lane
        self.titles[ident] = title
        self.threads[ident] = []
        return {"identifier": ident, "url": f"https://linear.app/x/{ident}"}

    def find_open_prefix(self, prefix):
        for ident, title in self.titles.items():
            if title.startswith(prefix) and self.states[ident] not in ("Done", "Canceled"):
                return {"identifier": ident}
        return None

    def thread_and_viewer(self, identifier, *needs, whole=False):
        return [dict(n) for n in self.threads[identifier]], BOT

    def get_issue(self, identifier, *, fresh=False):
        return {"identifier": identifier, "state": {"name": self.states[identifier]}}


@pytest.fixture
def sweep(monkeypatch):
    """`sweep(board)` runs the growth phase of one full sweep over `board`."""
    monkeypatch.setattr(green_light_reply, "ceo_user_ids", lambda *a, **k: frozenset({CEO}))
    reconcile._idle_pass.clear()
    reconcile._write_failures.clear()

    def run(board: Board, *, idle: bool = False):
        for name in ("gql", "set_description", "cmd_comment", "count_comments",
                     "cmd_state", "create_card", "find_open_prefix", "get_issue"):
            monkeypatch.setattr(linear_ops, name, getattr(board, name))
        monkeypatch.setattr(linear_ops, "_thread_and_viewer", board.thread_and_viewer)
        monkeypatch.setattr(reconcile, "epic_records",
                            lambda epics: {EPIC: board.epic_record()})
        if idle:
            reconcile._idle_pass.append("nothing in motion")
        try:
            reconcile.report_epic_growth({EPIC})
        finally:
            reconcile._idle_pass.clear()
        assert reconcile._write_failures == []

    yield run
    reconcile._write_failures.clear()


def _seed_addition(board: Board) -> None:
    """DRE-910 joined through the discovery route, the rest by hand."""
    mid_epic.refresh_epic_growth(
        board, EPIC, add={"id": "DRE-910", "because": "a second call site"},
        issue=board.epic_record())


class TestAnEpicPastTheThresholdIsAsked:
    def test_one_question_card_in_green_light(self, sweep):
        board = Board(10, 25)
        _seed_addition(board)
        sweep(board)
        assert len(board.creates) == 1
        card = board.creates[0]
        assert card["lane"] == "Green Light"
        assert card["labels"] == (linear_ops.NO_CODE_LABEL,)
        assert card["repo_slug"] == reconcile.REPO_SLUG
        assert card["title"] == epic_growth.title(EPIC, 10, 25)
        assert not any(label.startswith("agent:") for label in card["labels"])

    def test_the_card_carries_the_three_lines(self, sweep):
        board = Board(10, 25)
        _seed_addition(board)
        sweep(board)
        body = board.creates[0]["body"]
        assert console_escalation.problems(body) == []
        esc = console_escalation.parse(body)
        assert "approved at 10 cards" in esc.finding and "running 25" in esc.finding
        assert "DRE-910 (addition: a second call site)" in esc.finding
        for n in range(10, 25):
            ident = f"DRE-{900 + n}"
            if ident != "DRE-910":
                assert f"{ident} (unrecorded: joined " in esc.finding, ident
        assert "re-approve" in esc.question and "split" in esc.question
        assert esc.recommendation == "re-approve" and esc.why

    def test_the_epic_keeps_running_and_its_record_carries_the_open_line(self, sweep):
        board = Board(10, 25)
        sweep(board)
        assert board.states[EPIC] == "In Progress"
        assert [m for m in board.moves if m[0] == EPIC] == []
        assert [p for p in board.posts if p[0] == EPIC
                and "epic-growth" in p[1]] == []
        [line] = board.questions()
        assert (line["id"], line["at"], line["status"]) == (
            board.creates[0]["identifier"], 25, "open")

    def test_the_next_pass_with_the_question_open_asks_nothing_and_posts_nothing(self, sweep):
        board = Board(10, 25)
        sweep(board)
        posts, moves = list(board.posts), list(board.moves)
        sweep(board)
        assert len(board.creates) == 1
        assert board.posts == posts and board.moves == moves
        # Grown further while it is open: still the one question.
        board.grow_to(40)
        sweep(board)
        assert len(board.creates) == 1
        assert [p for p in board.posts[len(posts):]
                if p[0] != EPIC or "epic-growth" in p[1]] == []
        assert board.questions()[0]["status"] == "open"

    def test_a_card_created_but_never_recorded_is_adopted_rather_than_asked_twice(self, sweep):
        board = Board(10, 25)
        board.create_card(epic_growth.title(EPIC, 10, 25), "x", repo_slug="bureau-pipeline",
                          labels=(linear_ops.NO_CODE_LABEL,), lane="Green Light")
        sweep(board)
        assert len(board.creates) == 1
        assert board.questions()[0]["id"] == board.creates[0]["identifier"]


class TestUnderTheThresholdNothingIsAsked:
    @pytest.mark.parametrize("approved, running", [(10, 20), (4, 9)])
    def test_under_the_threshold(self, sweep, approved, running):
        board = Board(approved, running)
        sweep(board)
        assert board.creates == []
        assert board.questions() == []

    def test_an_unreadable_green_light(self, sweep):
        board = Board(10, 40, green_lit_at=None)
        sweep(board)
        assert board.creates == []

    def test_an_idle_pass_asks_nothing(self, sweep):
        board = Board(10, 25)
        sweep(board, idle=True)
        assert board.creates == []

    def test_a_contended_refresh_asks_nothing(self, sweep, monkeypatch):
        board = Board(10, 25)
        report = {"green_lit": 10, "approved": 10, "current": 25, "unrecorded": [],
                  "re_approved": [], "capped": None, "comments": 0, "joined": [],
                  "questions": [], "contended": "the description changed under the write"}
        monkeypatch.setattr(mid_epic, "refresh_epic_growth", lambda *a, **k: report)
        sweep(board)
        assert board.creates == []


class TestARecordGap:
    def test_the_gap_is_reported_and_the_cards_are_listed(self, sweep, capsys):
        board = Board(17, 41)
        sweep(board)
        out = capsys.readouterr().out
        assert f"epic-growth: {EPIC} grew without its plan changing — " in out
        additions = board.record()["additions"]
        assert len(additions) == 24
        assert all(a["because"].startswith("unrecorded: joined ") for a in additions)


def _asked(sweep) -> tuple[Board, str]:
    board = Board(10, 25)
    sweep(board)
    return board, board.creates[0]["identifier"]


class TestReApprove:
    def test_the_record_settles_and_the_card_closes(self, sweep):
        board, question = _asked(sweep)
        board.say(question, "Re-approve — the extra cards are the same plan.")
        sweep(board)
        [line] = board.questions()
        assert (line["status"], line["at"]) == ("re-approved", 25)
        assert board.states[question] == "Done"
        closing = [b for i, b in board.posts if i == question]
        assert len(closing) == 1
        assert closing[0].startswith("✅ epic-growth-question: re-approved at 25 cards on ")
        assert board.states[EPIC] == "In Progress"

    def test_the_threshold_measures_from_the_re_approved_count(self, sweep):
        board, question = _asked(sweep)
        board.say(question, "re-approve")
        sweep(board)
        board.grow_to(45)
        sweep(board)
        assert len(board.creates) == 1
        board.grow_to(51)
        sweep(board)
        assert len(board.creates) == 2
        assert board.creates[1]["title"] == epic_growth.title(EPIC, 25, 51)

    def test_settled_once(self, sweep):
        board, question = _asked(sweep)
        board.say(question, "re-approve")
        sweep(board)
        posts = list(board.posts)
        sweep(board)
        assert board.posts == posts


class TestSplit:
    def test_the_amendment_is_filed_and_the_card_closes(self, sweep):
        board, question = _asked(sweep)
        board.say(question, "Split it.")
        sweep(board)
        assert board.states[EPIC] == mid_epic.AMENDMENT_STATE
        assert [b for i, b in board.posts
                if i == EPIC and b.startswith(f"🔁 {mid_epic.AMENDMENT_TAG}")]
        [amendment] = board.record()["amendments"]
        assert amendment["because"] == epic_growth.split_because(10, 25, question)
        assert board.states[question] == "Done"
        closing = [b for i, b in board.posts if i == question]
        assert len(closing) == 1
        assert closing[0].startswith("✅ epic-growth-question: split asked at 25 cards on ")
        assert closing[0].endswith(f"— amendment filed on {EPIC}")
        assert board.questions()[0]["status"] == "split"

    def test_a_split_that_stopped_after_the_amendment_files_no_second_one(self, sweep):
        board, question = _asked(sweep)
        board.say(question, "split")
        sweep(board)
        # The epic is read active again and the question line still reads
        # open, as if the sweep stopped before settling it: the closing
        # comment on the question card says the amendment was filed.
        board.states[EPIC] = "In Progress"
        [line] = board.questions()
        mid_epic.refresh_epic_growth(board, EPIC, question=dict(
            line, status="open", settled=None), issue=board.epic_record())
        sweep(board)
        assert len(board.record()["amendments"]) == 1
        assert len([b for i, b in board.posts if i == question]) == 1
        assert board.questions()[0]["status"] == "split"


class TestWithdrawnAndUnreadable:
    def test_a_card_that_left_green_light_unanswered_is_withdrawn(self, sweep):
        board, question = _asked(sweep)
        board.say(question, "split", author=SOMEONE)  # not his voice
        board.drag(question, "Backlog")
        posts, moves = list(board.posts), list(board.moves)
        sweep(board)
        [line] = board.questions()
        assert (line["status"], line["at"]) == ("withdrawn", 25)
        assert board.posts == posts and board.moves == moves
        assert board.states[EPIC] == "In Progress"

    def test_the_threshold_measures_from_the_withdrawn_count(self, sweep):
        board, question = _asked(sweep)
        board.drag(question, "Canceled")
        sweep(board)
        board.grow_to(45)
        sweep(board)
        assert len(board.creates) == 1
        board.grow_to(51)
        sweep(board)
        assert len(board.creates) == 2

    def test_a_withdrawn_card_still_open_is_never_adopted_as_the_next_question(self, sweep):
        board, question = _asked(sweep)
        board.drag(question, "Backlog")
        sweep(board)
        board.grow_to(51)
        sweep(board)
        assert len(board.creates) == 2
        assert board.questions()[-1]["id"] == board.creates[1]["identifier"]

    def test_an_open_question_with_no_answer_stays_open(self, sweep):
        board, question = _asked(sweep)
        posts = list(board.posts)
        sweep(board)
        assert board.questions()[0]["status"] == "open"
        assert board.posts == posts

    def test_words_neither_rule_reads_are_left_with_one_line(self, sweep, capsys):
        board, question = _asked(sweep)
        board.say(question, "Let me think about it.")
        capsys.readouterr()
        posts, moves = list(board.posts), list(board.moves)
        sweep(board)
        out = capsys.readouterr().out
        assert out.count(
            f"epic-growth: {EPIC} question {question} answered in words this "
            "cannot read — left") == 1
        assert board.posts == posts and board.moves == moves
        board.grow_to(80)
        sweep(board)
        assert len(board.creates) == 1, "nothing is asked again"
        assert board.questions()[0]["status"] == "open"
