"""RED-first tests: the `question` class's action module (DRE-6459).

A blocker the sweep cannot act on is asked once, in Green Light, with the three
lines. The reporter (DRE-6444) keeps that for every NEW note; what it cannot
reach is the board as it stands — every `🛑 Agent blocked` marker posted before
the blocker-class epic carries no class and is read as a question — and the
mechanical cases an action module hands back as a person's call. The resolver
(DRE-6508) calls `scripts/blocker_ask.py` for both.

WHAT THESE TESTS PIN, with `linear_ops` replaced and the module driven alone —
the run through the real resolver and one real sweep pass is DRE-6509's:

  * One comment, built by `compose`: the `🙋` first line, the agent's reason
    completed with the Finding, Question and Recommendation lines (`none given
    — …` where the agent recommended nothing; a reason that already declares
    them comes back unchanged), and the closing line naming the agent's run —
    the `Run:` URL read off the marker body, or `not recorded`.
  * Then the Green Light move, `held=True`, then the fresh read, in that order;
    the return is `("asked", "asked in Green Light: <the Question line>")`.
  * A full thread (the comment-cap condition) is `NotNow` and moves nothing.
  * A move that did not land is `NotNow` naming the lane, with the comment
    standing; the next pass finds that comment newer than the marker, posts
    nothing and only moves.
  * The module never names the resolver's tag, and the poster's call is the
    one the receipts guard's `unconverted` row is anchored on.

Run: cd bureau-pipeline && python3 -m pytest tests/test_blocker_ask.py -v
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import blocker_ask as module  # noqa: E402
import blocker_class  # noqa: E402
import console_escalation  # noqa: E402
import linear_ops  # noqa: E402

SCRIPT = ROOT / "scripts" / "blocker_ask.py"
CARD = "DRE-9002"
RUN = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/4242"
PARKED = (" — parked in Backlog until the blocker is resolved (a Todo return here "
          "would redispatch agents into the same wall).")

#: A free-prose reason: no Finding, Question or Recommendation line.
REASON = ("The card asks for the old export to go, but two customers still pull "
          "it every night. Should we keep it running for a week first?")
MARKER = f"🛑 Agent blocked: class=question · {REASON}{PARKED} Run: {RUN}"
LEGACY_MARKER = f"🛑 Agent blocked: {REASON}{PARKED}"

#: A reason that already declares the three lines.
DECLARED = (
    "The export has two nightly readers.\n\n"
    f"{console_escalation.FINDING_PREFIX} Two customers still pull the old export.\n"
    f"{console_escalation.QUESTION_PREFIX} Retire it now, or keep it a week?\n"
    f"{console_escalation.RECOMMENDATION_PREFIX} Keep it a week — the customers "
    "need notice."
)

FIRST_LINE = ("🙋 The build agent stopped on this card and the sweep could not act "
              "on its note — it needs your call.")
CLOSING = ("Answer here, then move this card to **Todo** to proceed (a fresh run "
           "picks up your guidance), or to **Backlog** to drop it. Asked by the "
           "sweep; the agent's run: ")


def _card(*bodies):
    """The sweep's card dict; `bodies` oldest→newest, stored newest-first the
    way the API answers."""
    return {
        "identifier": CARD,
        "title": "Retire the old export",
        "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
        "description": "Retire it.",
        "comments": {"nodes": [{"body": body} for body in reversed(bodies)]},
    }


class StubLinear:
    """`linear_ops` as the module sees it: the comment, the advance, the fresh
    read and the window reader, the first three recorded in order — anything
    else is an AttributeError, so "no other write" is a fact the stub enforces."""

    COMMENT_CAP_CONDITION = linear_ops.COMMENT_CAP_CONDITION

    def __init__(self, *, lands=True, lane="Backlog", capped=False):
        self.calls = []
        self.lands = lands
        self.lane = lane
        self.capped = capped

    def cmd_comment(self, identifier, body, *flags):
        self.calls.append(("cmd_comment", identifier, body, flags))
        return self.COMMENT_CAP_CONDITION if self.capped else None

    def cmd_advance(self, identifier, to_state, from_states_csv, *flags, **kwargs):
        self.calls.append(("cmd_advance", identifier, to_state, from_states_csv, flags, kwargs))
        if self.lands and self.lane == "Backlog":
            self.lane = to_state
        else:
            print(f"{identifier} is in {self.lane}, not {from_states_csv} — not advancing")

    def get_issue(self, identifier, **kwargs):
        self.calls.append(("get_issue", identifier, kwargs))
        return {"identifier": identifier, "state": {"name": self.lane}}

    def window_nodes(self, comments):
        return linear_ops.window_nodes(comments)

    def comments(self):
        return [call[2] for call in self.calls if call[0] == "cmd_comment"]


ADVANCED = [
    ("cmd_advance", CARD, "Green Light", "Backlog", (), {"held": True}),
    ("get_issue", CARD, {"fresh": True}),
]


@pytest.fixture
def stub(monkeypatch):
    def install(**kwargs):
        linear = StubLinear(**kwargs)
        monkeypatch.setattr(module, "linear_ops", linear)
        return linear

    return install


def _line(body, prefix):
    return next(line for line in body.split("\n") if line.startswith(prefix))


class TestTheAsk:
    def test_one_comment_then_the_move_then_the_fresh_read(self, stub):
        linear = stub()
        result = module.resolve(_card("⏳ 1/5 plan", MARKER), REASON, repo="o/r")

        assert [c[0] for c in linear.calls] == ["cmd_comment", "cmd_advance", "get_issue"]
        [comment] = linear.comments()
        assert linear.calls[0][1] == CARD
        assert linear.calls[1:] == ADVANCED

        assert comment.startswith("🙋 ")
        assert comment.split("\n", 1)[0] == FIRST_LINE
        for prefix in (console_escalation.FINDING_PREFIX,
                       console_escalation.QUESTION_PREFIX,
                       console_escalation.RECOMMENDATION_PREFIX):
            assert prefix in comment, prefix
        recommendation = _line(comment, console_escalation.RECOMMENDATION_PREFIX)
        assert recommendation.startswith(
            f"{console_escalation.RECOMMENDATION_PREFIX} "
            f"{console_escalation.NONE_GIVEN}{console_escalation.SEPARATOR}")
        assert console_escalation.problems(comment) == []

        action, note = result
        assert action == "asked"
        question = console_escalation.parse(comment).question
        assert note == f"asked in Green Light: {question[:120]}"
        assert note.startswith("asked in Green Light: ")

    def test_the_comment_is_compose_over_the_reason_and_the_run(self, stub):
        linear = stub()
        module.resolve(_card(MARKER), REASON, repo="o/r")
        [comment] = linear.comments()
        assert comment == module.compose(REASON, RUN)

    def test_compose_is_the_first_line_the_completed_reason_and_the_closing(self):
        completed = console_escalation.complete(
            REASON, question="Which way should this card go?", who="the build agent")
        assert module.compose(REASON, RUN) == (
            f"{FIRST_LINE}\n\n{completed}\n\n{CLOSING}{RUN}")
        # The agent's reason is quoted, then the three lines follow it.
        assert REASON in module.compose(REASON, RUN)

    def test_a_reason_that_declares_the_three_lines_is_posted_unchanged(self, stub):
        linear = stub()
        result = module.resolve(_card(MARKER), DECLARED, repo="o/r")
        [comment] = linear.comments()
        assert comment == f"{FIRST_LINE}\n\n{DECLARED}\n\n{CLOSING}{RUN}"
        assert _line(comment, console_escalation.RECOMMENDATION_PREFIX).endswith(
            "Keep it a week — the customers need notice.")
        assert result == ("asked", "asked in Green Light: Retire it now, or keep it a week?")

    def test_the_note_quotes_at_most_120_chars_of_the_question(self, stub):
        long_question = "Should we " + "keep the old export running " * 10 + "?"
        stub()
        _, note = module.resolve(_card(MARKER), long_question, repo="o/r")
        assert note == f"asked in Green Light: {long_question[:120]}"

    def test_a_mechanical_hand_off_reason_is_asked_the_same_way(self, stub):
        # The resolver appends one line naming the class after the reason when
        # a mechanical module returned None.
        reason = (f"{REASON}\n(class=wrong-repo: the sweep could not act on this "
                  "mechanically)")
        linear = stub()
        assert module.resolve(_card(MARKER), reason, repo="o/r")[0] == "asked"
        [comment] = linear.comments()
        assert "(class=wrong-repo: the sweep could not act on this mechanically)" in comment
        assert console_escalation.problems(comment) == []


class TestTheRunUrl:
    def test_the_comment_names_the_run_the_marker_names(self, stub):
        linear = stub()
        module.resolve(_card(MARKER), REASON, repo="o/r")
        [comment] = linear.comments()
        assert comment.endswith(f"the agent's run: {RUN}")

    def test_a_marker_naming_no_run_says_not_recorded(self, stub):
        linear = stub()
        module.resolve(_card(LEGACY_MARKER), REASON, repo="o/r")
        [comment] = linear.comments()
        assert comment.endswith("the agent's run: not recorded")
        assert module.compose(REASON, None).endswith("the agent's run: not recorded")

    def test_the_run_is_read_off_the_open_marker_not_an_older_one(self, stub):
        older = MARKER.replace("4242", "1111")
        linear = stub()
        module.resolve(_card(older, "a person's reply", LEGACY_MARKER), REASON, repo="o/r")
        [comment] = linear.comments()
        assert comment.endswith("the agent's run: not recorded")


class TestAFullThread:
    def test_the_comment_cap_is_not_now_and_nothing_moves(self, stub):
        linear = stub(capped=True)
        with pytest.raises(blocker_class.NotNow) as raised:
            module.resolve(_card(MARKER), REASON, repo="o/r")
        assert str(raised.value) == "the thread is full — the question could not be posted"
        assert [c[0] for c in linear.calls] == ["cmd_comment"]
        assert linear.lane == "Backlog"


class TestAMoveThatDidNotLand:
    def test_not_now_names_green_light_and_the_lane_with_the_comment_standing(self, stub):
        linear = stub(lands=False)
        with pytest.raises(blocker_class.NotNow) as raised:
            module.resolve(_card(MARKER), REASON, repo="o/r")
        assert str(raised.value) == "Green Light move did not land — the card is in Backlog"
        assert [c[0] for c in linear.calls] == ["cmd_comment", "cmd_advance", "get_issue"]
        assert len(linear.comments()) == 1

    def test_the_next_pass_finds_the_ask_posts_nothing_and_only_moves(self, stub):
        first = stub(lands=False)
        with pytest.raises(blocker_class.NotNow):
            module.resolve(_card(MARKER), REASON, repo="o/r")
        [ask] = first.comments()

        second = stub()
        result = module.resolve(_card(MARKER, ask), REASON, repo="o/r")
        assert second.comments() == []
        assert second.calls == ADVANCED
        assert result[0] == "asked"
        assert result[1].startswith("asked in Green Light: ")

    def test_an_ask_older_than_the_open_marker_does_not_count(self, stub):
        # An earlier marker was asked and resolved; a new marker owes its own ask.
        ask = module.compose(REASON, RUN)
        linear = stub()
        module.resolve(_card(MARKER, ask, "a person's reply", MARKER), REASON, repo="o/r")
        assert len(linear.comments()) == 1

    def test_a_card_another_writer_carried_to_green_light_passes(self, stub):
        linear = stub(lane="Green Light")
        result = module.resolve(_card(MARKER), REASON, repo="o/r")
        assert result[0] == "asked"
        assert len(linear.comments()) == 1


class TestTheMarkerIsReadOffTheLiveThread:
    def test_no_marker_in_the_window_is_not_now_naming_the_card(self, stub):
        linear = stub()
        with pytest.raises(blocker_class.NotNow) as raised:
            module.resolve(_card("⏳ 1/5 plan", "a person's reply"), REASON, repo="o/r")
        assert CARD in str(raised.value)
        assert linear.calls == []

    def test_the_prefix_comes_from_the_vocabulary(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert 'blocker_class.load()["marker"]' in source
        assert "🛑" not in source

    def test_the_thread_is_walked_newest_to_oldest_off_window_nodes(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert 'reversed(linear_ops.window_nodes(card' in source


class TestTheModuleShape:
    def test_the_module_never_names_the_resolvers_tag(self):
        assert "agent-blocker-resolved" not in SCRIPT.read_text(encoding="utf-8")

    def test_the_one_post_is_spelled_as_the_receipts_guard_anchors_it(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert source.count('cmd_comment(card["identifier"], ask)') == 1
        assert source.count("linear_ops.cmd_comment(") == 1
        assert "ask = compose(reason, run_url)" in source

    def test_the_green_light_move_names_its_lane_as_a_literal(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert ('linear_ops.cmd_advance(card["identifier"], "Green Light", '
                '"Backlog", held=True)') in source

    def test_the_module_does_not_borrow_the_planners_escalation(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert "planning_escalation" not in source

    def test_resolve_has_the_contracts_signature(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "resolve")
        assert [a.arg for a in fn.args.args] == ["card", "reason"]
        assert [a.arg for a in fn.args.kwonlyargs] == ["repo"]

    def test_the_vocabulary_names_this_module_for_the_question_class(self):
        assert blocker_class.load()["classes"]["question"]["action"] == SCRIPT.stem

    def test_the_ask_opens_with_a_machine_prefix_the_resolver_hands_open_blocker(self):
        # DRE-6448: a standing ask is the pipeline's own comment, so the marker
        # stays open and the next pass finds the ask rather than a reply.
        os.environ.setdefault("LINEAR_API_KEY", "test-key")
        os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
        os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
        os.environ.setdefault("GH_TOKEN", "x")
        import reconcile

        assert module.FIRST_LINE.startswith(reconcile._AGENT_COMMENT_PREFIXES)
        bodies = [MARKER, module.compose(REASON, RUN)]
        assert reconcile.open_agent_blocker(_card(*bodies)) is not None

    def test_check_act_receipts_is_green(self):
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_act_receipts.py")],
            cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT / "scripts")},
        )
        assert done.returncode == 0, done.stdout + done.stderr
