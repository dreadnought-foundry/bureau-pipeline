"""TDD for the plan-critic bound exit (DRE-6452, epic DRE-6415).

Every bound site in `plan.yml` used to do the same three things — hold, move
to Triage, post the 🛑 note — and the card then had no owner and no next step.
DRE-4059 reached the second critic's bound at 2026-10-08 20:22 PT and its seven
planned children sat in Backlog until an operator moved it back by hand the
next day. `scripts/plan_bound.py` is the "try without a person first" step:
it reads the worst finding of every send-back round, classifies each with
DRE-6356's `send_back_class`, and either asks the CEO one question or grants
the planner one more rewrite. Only when neither applies does it answer `park`.

These tests drive the module with the findings the critics really wrote on
DRE-4059 (2026-10-08) and DRE-3879, verbatim.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_bound.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import console_escalation  # noqa: E402
import plan_bound  # noqa: E402
import plan_critic  # noqa: E402
import planning_escalation  # noqa: E402
import send_back_class  # noqa: E402

CARD = "DRE-4059"
REPO_SLUG = "dreadnought-foundry/bureau-pipeline"

# The three `plan-critic: stage=post round=1..3 result=SEND_BACK` reasons on
# DRE-4059's thread, 2026-10-08 — copied verbatim off the card.
DRE_4059_POST = (
    "DRE-6380's precheck reads only the card body, so a one-off the exit would "
    "route by its label or title (agent:ops, no-code, a PROOF title) but that "
    "has no checklist gets sent to the rewrite and spends send-back rounds.",
    "DRE-6360 claims the one-off critic and the planner will read the "
    "description the answer-writer just wrote, but both embed the dispatch "
    "payload's copy (`steps.card.outputs.description`), which was fixed before "
    "the writer ran.",
    'DRE-6358: the "you have not been asked anything on this card before" form '
    "is read off `escalate()`'s default comment read, which is only the fifty "
    "newest comments, so a signed answer that has scrolled out of that window "
    "makes the note tell the CEO something false",
)

# DRE-3879 round 1, the one-off critic's — a choice nobody has made.
DRE_3879_ROUND_1 = (
    "The card offers two different fixes for the same problem without picking "
    "one, and choosing between them is a security policy call, not a coding "
    "task."
)

# Plain-English findings the classifier reads as revisions.
REVISIONS = (
    "The card doesn't say which of the two lanes the card lands in.",
    "The answer is never written into the card's own checklist.",
    "The claim about the queue doesn't show up anywhere we can actually verify.",
)

# A decision-class finding that names a file: the question built from it
# would be refused before the CEO ever saw it.
DECISION_WITH_PATH = (
    "The plan offers two ways to change scripts/hold.py without picking one."
)


# --------------------------------------------------------------------------- #
# thread helpers                                                               #
# --------------------------------------------------------------------------- #


def _rec(body: str, pipeline: bool = True, at: str = "2026-10-08T20:00:00Z") -> dict:
    return {"body": body, "authored_by_pipeline": pipeline, "created_at": at}


def _send_back(stage: str, round_n: int, reason: str) -> dict:
    return _rec(plan_critic.marker(stage, round_n, plan_critic.SEND_BACK, reason))


def _boundary(card: str = CARD) -> dict:
    return _rec(plan_critic.cycle_marker(card))


def _thread(stage: str, findings, *, boundary: bool = True) -> list[dict]:
    rows = [_boundary()] if boundary and stage != "one-off" else []
    rows += [_send_back(stage, n, f) for n, f in enumerate(findings, 1)]
    return rows


def _receipt(findings=REVISIONS, refused: bool = False,
             at: str = "2026-10-08T21:00:00Z", pipeline: bool = True) -> dict:
    return _rec(plan_bound.receipt_body(list(findings), refused=refused),
                pipeline=pipeline, at=at)


class _World:
    """Every write `exit` can make, stubbed and recorded in order."""

    def __init__(self, monkeypatch, records, *, dispatch_rc: int = 0,
                 outcome: planning_escalation.Outcome | None = None,
                 read_fails: bool = False):
        self.events: list[tuple] = []
        self.records = records

        def comment_records(card, *, whole_thread=False):
            self.events.append(("read", card, whole_thread))
            if read_fails:
                raise RuntimeError("Linear said no")
            return list(self.records)

        def cmd_comment(card, body, *flags):
            self.events.append(("comment", card, body))

        def escalate(linear_ops, card, reason, *args, choices=None, **kwargs):
            self.events.append(("escalate", card, reason, choices))
            return outcome or planning_escalation.Outcome(
                parked=True, posted=True, stood_down=None)

        def dispatch(args):
            self.events.append(("dispatch", args))
            return dispatch_rc

        monkeypatch.setattr(plan_bound.linear_ops, "comment_records", comment_records)
        monkeypatch.setattr(plan_bound.linear_ops, "cmd_comment", cmd_comment)
        monkeypatch.setattr(plan_bound.planning_escalation, "escalate", escalate)
        monkeypatch.setattr(plan_bound.review_rerun, "_cmd_dispatch", dispatch)

    def of(self, kind: str) -> list[tuple]:
        return [e for e in self.events if e[0] == kind]

    def writes(self) -> list[tuple]:
        return [e for e in self.events if e[0] in ("comment", "escalate", "dispatch")]


def _exit(stage: str = "post", card: str = CARD) -> int:
    return plan_bound.main(["exit", card, "--stage", stage, "--repo", REPO_SLUG])


# --------------------------------------------------------------------------- #
# 1. what the merged classifier says about the epic's own evidence             #
# --------------------------------------------------------------------------- #


class TestTheMergedClassifierOnDre4059:
    """Unpatched. A phrase-list change in `send_back_class.py` that flips one
    of these is found here, by name, and not in a workflow run."""

    @pytest.mark.parametrize("finding", DRE_4059_POST,
                             ids=["round-1", "round-2", "round-3"])
    def test_classify_cannot_place_it(self, finding):
        assert send_back_class.classify(finding) is None, (
            f"send_back_class.classify no longer returns None for DRE-4059's "
            f"second-critic finding {finding!r}")

    @pytest.mark.parametrize("finding", DRE_4059_POST,
                             ids=["round-1", "round-2", "round-3"])
    def test_why_says_uncertain(self, finding):
        assert send_back_class.why(finding).startswith("uncertain"), (
            f"send_back_class.why no longer says uncertain for DRE-4059's "
            f"second-critic finding {finding!r}")

    @pytest.mark.parametrize("finding", DRE_4059_POST,
                             ids=["round-1", "round-2", "round-3"])
    def test_route_class_sends_it_to_the_ceo(self, finding):
        assert send_back_class.route_class(finding) == send_back_class.DECISION, (
            f"send_back_class.route_class no longer answers DECISION for "
            f"DRE-4059's second-critic finding {finding!r}")


# --------------------------------------------------------------------------- #
# 2. decide — the stage rule and nothing else                                  #
# --------------------------------------------------------------------------- #


def _patch_classify(monkeypatch, table: dict):
    real = send_back_class.classify

    def classify(finding):
        if finding in table:
            return table[finding]
        return real(finding)

    monkeypatch.setattr(send_back_class, "classify", classify)


class TestDecide:
    def test_the_uncertain_table_is_this_modules_own(self):
        assert plan_bound.UNCERTAIN_BY_STAGE == {
            "one-off": send_back_class.DECISION,
            "pre": send_back_class.REVISION,
            "post": send_back_class.REVISION,
        }
        # One-off is DRE-6356's own collapse.
        assert (plan_bound.UNCERTAIN_BY_STAGE["one-off"]
                == send_back_class.UNCERTAIN_GOES_TO)

    def test_uncertain_on_post_is_a_rewrite_then_a_park(self, monkeypatch):
        _patch_classify(monkeypatch, {f: None for f in DRE_4059_POST})
        assert plan_bound.decide(list(DRE_4059_POST), False, "post")[0] == "rewrite"
        assert plan_bound.decide(list(DRE_4059_POST), True, "post")[0] == "park"

    def test_uncertain_on_pre_is_a_rewrite(self, monkeypatch):
        _patch_classify(monkeypatch, {f: None for f in DRE_4059_POST})
        assert plan_bound.decide(list(DRE_4059_POST), False, "pre")[0] == "rewrite"

    @pytest.mark.parametrize("granted", [False, True])
    def test_uncertain_on_one_off_is_a_question(self, monkeypatch, granted):
        _patch_classify(monkeypatch, {f: None for f in DRE_4059_POST})
        outcome, _ = plan_bound.decide(list(DRE_4059_POST), granted, "one-off")
        assert outcome == "question"

    def test_the_one_off_rule_is_route_class(self, monkeypatch):
        _patch_classify(monkeypatch, {f: None for f in DRE_4059_POST})
        for finding in DRE_4059_POST:
            assert (plan_bound.stage_class(finding, "one-off")
                    == send_back_class.route_class(finding))

    @pytest.mark.parametrize("stage", ["pre", "post", "one-off"])
    @pytest.mark.parametrize("granted", [False, True])
    def test_a_decision_finding_is_a_question_on_every_stage(
            self, monkeypatch, stage, granted):
        table = {f: None for f in DRE_4059_POST}
        table[DRE_3879_ROUND_1] = send_back_class.DECISION
        _patch_classify(monkeypatch, table)
        findings = list(DRE_4059_POST) + [DRE_3879_ROUND_1]
        assert plan_bound.decide(findings, granted, stage)[0] == "question"

    def test_three_revisions_on_post_are_a_rewrite(self, monkeypatch):
        _patch_classify(monkeypatch,
                        {f: send_back_class.REVISION for f in DRE_4059_POST})
        assert plan_bound.decide(list(DRE_4059_POST), False, "post")[0] == "rewrite"

    @pytest.mark.parametrize("stage", ["pre", "post", "one-off"])
    @pytest.mark.parametrize("granted", [False, True])
    def test_no_findings_is_a_park_on_every_stage(self, stage, granted):
        assert plan_bound.decide([], granted, stage)[0] == "park"

    def test_decide_gives_a_reason(self):
        outcome, reason = plan_bound.decide([], False, "post")
        assert outcome == "park" and reason.strip()


# --------------------------------------------------------------------------- #
# 3. exit — the writes                                                         #
# --------------------------------------------------------------------------- #


class TestExitQuestion:
    def test_question_escalates_once_and_writes_nothing_else(self, monkeypatch):
        world = _World(monkeypatch, _thread("post", [DRE_3879_ROUND_1]))
        assert _exit("post") == 0
        escalations = world.of("escalate")
        assert len(escalations) == 1
        _, card, reason, choices = escalations[0]
        assert card == CARD
        for prefix in (console_escalation.FINDING_PREFIX,
                       console_escalation.QUESTION_PREFIX,
                       console_escalation.RECOMMENDATION_PREFIX):
            assert any(line.startswith(prefix) for line in reason.splitlines()), prefix
        assert "Finding:" in reason and "Question:" in reason
        assert "Recommendation:" in reason
        assert choices["recommended"] == "answer"
        assert planning_escalation.choices_problem(choices) is None
        assert not world.of("comment")
        assert not world.of("dispatch")

    def test_the_question_names_the_card_and_recommends_nothing(self, monkeypatch):
        world = _World(monkeypatch, _thread("post", [DRE_3879_ROUND_1]))
        _exit("post")
        reason = world.of("escalate")[0][2]
        assert (f"{console_escalation.QUESTION_PREFIX} Which way should {CARD} go "
                "on the choice the critic named, so the planner can write it in "
                "and plan again?") in reason
        assert (f"{console_escalation.RECOMMENDATION_PREFIX} none given — the "
                "critic names the choice, not a side") in reason
        assert f"{console_escalation.FINDING_PREFIX} {DRE_3879_ROUND_1}" in reason

    def test_the_choices_answer_replans_and_drop_closes(self, monkeypatch):
        world = _World(monkeypatch, _thread("post", [DRE_3879_ROUND_1]))
        _exit("post")
        choices = {c["id"]: c for c in world.of("escalate")[0][3]["choices"]}
        assert choices["answer"]["outcome"] == "replan"
        assert choices["drop"]["outcome"] == "close"
        assert choices["answer"]["effect"] == (
            "Answer the question here and send the card back to Planning; the "
            "planner re-plans with your answer in front of it")

    def test_no_hold_is_written_on_a_question(self, monkeypatch):
        world = _World(monkeypatch, _thread("post", [DRE_3879_ROUND_1]))
        _exit("post")
        assert [e[0] for e in world.writes()] == ["escalate"]

    def test_a_stood_down_escalation_exits_3(self, monkeypatch, capsys):
        world = _World(
            monkeypatch, _thread("post", [DRE_3879_ROUND_1]),
            outcome=planning_escalation.Outcome(
                parked=False, posted=True, stood_down="it is in Backlog"))
        assert _exit("post") == 3
        assert [e[0] for e in world.writes()] == ["escalate"]
        assert "it is in Backlog" in capsys.readouterr().out

    def test_a_refused_question_takes_the_rewrite(self, monkeypatch, capsys):
        world = _World(monkeypatch, _thread("post", [DECISION_WITH_PATH]))
        assert _exit("post") == 0
        assert not world.of("escalate")
        comments = world.of("comment")
        assert len(comments) == 1
        body = comments[0][2]
        assert body.startswith("🔁 bound-rewrite:")
        assert plan_bound.REFUSED_SENTENCE in body
        dispatches = world.of("dispatch")
        assert len(dispatches) == 1
        out = capsys.readouterr().out
        assert "refused" in out and "file path" in out

    def test_a_refused_question_after_the_grant_parks(self, monkeypatch, capsys):
        records = [_receipt(at="2026-10-01T00:00:00Z")]
        records += _thread("post", [DECISION_WITH_PATH])
        world = _World(monkeypatch, records)
        assert _exit("post") == 3
        assert not world.writes()
        out = capsys.readouterr().out
        assert "refused" in out and "file path" in out


class TestExitRewrite:
    def test_rewrite_dispatches_then_writes_one_receipt(self, monkeypatch):
        world = _World(monkeypatch, _thread("post", REVISIONS))
        assert _exit("post") == 0
        kinds = [e[0] for e in world.writes()]
        # DRE-2034: the receipt is written FROM the confirmed dispatch.
        assert kinds == ["dispatch", "comment"]
        body = world.of("comment")[0][2]
        assert body.startswith("🔁 bound-rewrite:")
        for n, finding in enumerate(REVISIONS, 1):
            assert f"{n}. (revision) {finding}" in body
        assert plan_bound.REFUSED_SENTENCE not in body

    def test_the_receipt_is_composed_as_the_act(self, monkeypatch):
        world = _World(monkeypatch, _thread("post", REVISIONS))
        _exit("post")
        body = world.of("comment")[0][2]
        import pipeline_act
        trailer = pipeline_act.read_trailer(body)
        assert trailer and trailer["act"] == "plan-bound-rewrite"

    def test_each_finding_carries_its_class_word(self, monkeypatch):
        findings = [REVISIONS[0], DRE_4059_POST[0]]
        world = _World(monkeypatch, _thread("post", findings))
        assert _exit("post") == 0
        body = world.of("comment")[0][2]
        assert f"1. (revision) {REVISIONS[0]}" in body
        assert f"2. (uncertain) {DRE_4059_POST[0]}" in body

    def test_the_dispatch_is_the_plan_route_with_reason_bound_rewrite(self, monkeypatch):
        world = _World(monkeypatch, _thread("post", REVISIONS))
        _exit("post")
        args = world.of("dispatch")[0][1]
        assert args.epic == CARD
        assert args.repo == REPO_SLUG
        assert args.route == "plan"
        assert args.reason == "bound-rewrite"
        assert args.trigger_state == "planning"

    def test_a_failed_dispatch_posts_nothing_and_parks(self, monkeypatch):
        world = _World(monkeypatch, _thread("post", REVISIONS), dispatch_rc=1)
        assert _exit("post") == 3
        assert not world.of("comment")
        assert not world.of("escalate")

    def test_pre_and_post_findings_of_the_attempt_are_both_read(self, monkeypatch):
        records = [_boundary(),
                   _send_back("pre", 1, REVISIONS[0]),
                   _send_back("post", 1, REVISIONS[1]),
                   _send_back("post", 2, REVISIONS[1])]
        world = _World(monkeypatch, records)
        assert _exit("pre") == 0
        body = world.of("comment")[0][2]
        assert f"1. (revision) {REVISIONS[0]}" in body
        assert f"2. (revision) {REVISIONS[1]}" in body
        assert "3." not in body.split("If the critics")[0].split("oldest first:")[1]

    def test_a_one_off_takes_the_same_dispatch(self, monkeypatch):
        world = _World(monkeypatch, _thread("one-off", REVISIONS))
        assert _exit("one-off") == 0
        assert world.of("dispatch")[0][1].reason == "bound-rewrite"


class TestExitPark:
    def test_no_findings_parks_writing_nothing(self, monkeypatch):
        world = _World(monkeypatch, [_boundary()])
        assert _exit("post") == 3
        assert not world.writes()

    def test_a_granted_rewrite_parks_writing_nothing(self, monkeypatch):
        records = [_receipt(at="2026-10-01T00:00:00Z")] + _thread("post", REVISIONS)
        world = _World(monkeypatch, records)
        assert _exit("post") == 3
        assert not world.writes()

    def test_an_unreadable_thread_is_a_crash(self, monkeypatch):
        world = _World(monkeypatch, [], read_fails=True)
        code = _exit("post")
        assert code not in (0, 3)
        assert not world.writes()

    def test_exit_refuses_a_note_file(self, monkeypatch, capsys):
        world = _World(monkeypatch, _thread("post", REVISIONS))
        with pytest.raises(SystemExit) as raised:
            plan_bound.main(["exit", CARD, "--stage", "post", "--repo", REPO_SLUG,
                             "--note-file", "/tmp/note.md"])
        assert raised.value.code not in (0, 3)
        assert "--note-file" in capsys.readouterr().err
        assert not world.writes()

    def test_exit_reads_the_whole_thread(self, monkeypatch):
        world = _World(monkeypatch, [_boundary()])
        _exit("post")
        assert world.of("read") == [("read", CARD, True)]

    def test_a_marker_from_another_author_is_not_a_finding(self, monkeypatch):
        records = [_boundary(),
                   _rec(plan_critic.marker("post", 1, plan_critic.SEND_BACK,
                                           REVISIONS[0]), pipeline=False)]
        world = _World(monkeypatch, records)
        assert _exit("post") == 3
        assert not world.writes()


# --------------------------------------------------------------------------- #
# 4. granted is read off the thread                                            #
# --------------------------------------------------------------------------- #


class TestGranted:
    def test_a_receipt_in_an_earlier_attempt_still_counts(self):
        records = [_boundary(), _receipt(), _boundary(),
                   _send_back("post", 1, REVISIONS[0])]
        assert plan_bound.granted(records) is True

    def test_a_receipt_from_another_author_does_not(self):
        records = [_receipt(pipeline=False), _send_back("post", 1, REVISIONS[0])]
        assert plan_bound.granted(records) is False

    def test_no_receipt_is_not_granted(self):
        assert plan_bound.granted(_thread("post", REVISIONS)) is False

    def test_a_quoted_receipt_does_not_count(self):
        quoted = _rec("The operator said:\n> 🔁 bound-rewrite: something")
        assert plan_bound.granted([quoted]) is False

    def test_an_earlier_attempts_receipt_parks_the_exit(self, monkeypatch):
        records = [_boundary(), _receipt(), _boundary()]
        records += [_send_back("post", n, f) for n, f in enumerate(REVISIONS, 1)]
        world = _World(monkeypatch, records)
        assert _exit("post") == 3
        assert not world.writes()


# --------------------------------------------------------------------------- #
# 5. read and context                                                          #
# --------------------------------------------------------------------------- #


class TestRead:
    def _read(self, monkeypatch, capsys, records, *args):
        world = _World(monkeypatch, records)
        code = plan_bound.main(["read", CARD, *args])
        assert code == 0
        assert not world.writes()
        return capsys.readouterr().out.splitlines()

    def test_read_prints_the_outcome_and_one_line_per_finding(self, monkeypatch, capsys):
        findings = [REVISIONS[0], DRE_4059_POST[0], DRE_3879_ROUND_1]
        lines = self._read(monkeypatch, capsys, _thread("post", findings),
                           "--stage", "post")
        assert lines[0] == "question"
        rows = [line.split("\t") for line in lines[1:]]
        assert len(rows) == 3
        assert rows[0] == ["revision", send_back_class.why(REVISIONS[0]), REVISIONS[0]]
        assert rows[1] == ["uncertain", send_back_class.why(DRE_4059_POST[0]),
                           DRE_4059_POST[0]]
        assert rows[2][0] == "decision"

    def test_uncertain_findings_read_as_rewrite_on_post_and_question_on_one_off(
            self, monkeypatch, capsys):
        records = (_thread("post", DRE_4059_POST)
                   + [_send_back("one-off", n, f)
                      for n, f in enumerate(DRE_4059_POST, 1)])
        post = self._read(monkeypatch, capsys, records, "--stage", "post")
        assert post[0] == "rewrite"
        one_off = self._read(monkeypatch, capsys, records, "--stage", "one-off")
        assert one_off[0] == "question"
        assert all(line.startswith("uncertain\t") for line in one_off[1:])

    def test_whole_thread_reads_past_the_newest_boundary(self, monkeypatch, capsys):
        records = _thread("post", DRE_4059_POST) + [_boundary()]
        current = self._read(monkeypatch, capsys, records, "--stage", "post")
        assert current == ["park"]
        whole = self._read(monkeypatch, capsys, records, "--stage", "post",
                           "--whole-thread")
        assert whole[0] == "rewrite"
        assert [line.split("\t")[2] for line in whole[1:]] == list(DRE_4059_POST)

    def test_whole_thread_reads_granted_off_the_whole_thread(self, monkeypatch, capsys):
        records = [_receipt()] + _thread("post", DRE_4059_POST) + [_boundary()]
        whole = self._read(monkeypatch, capsys, records, "--stage", "post",
                           "--whole-thread")
        assert whole[0] == "park"


class TestContext:
    def _context(self, monkeypatch, capsys, records):
        world = _World(monkeypatch, records)
        assert plan_bound.main(["context", CARD]) == 0
        assert not world.writes()
        return capsys.readouterr().out

    def test_no_receipt(self, monkeypatch, capsys):
        out = self._context(monkeypatch, capsys, _thread("post", REVISIONS))
        assert out.splitlines()[0] == "## Findings the critics left open on this card"
        assert ("BOUND STATUS: none — no bound-rewrite receipt on this card"
                in out)
        assert "1." not in out

    def test_a_receipt_is_read_back_with_its_class_words(self, monkeypatch, capsys):
        findings = [REVISIONS[0], DRE_4059_POST[0]]
        records = [_receipt(findings, at="2026-10-09T03:22:00Z")]
        out = self._context(monkeypatch, capsys, records)
        assert out.splitlines()[0] == "## Findings the critics left open on this card"
        assert ("BOUND STATUS: rewrite granted at 2026-10-09T03:22:00Z — answer "
                "every finding below before anything else") in out
        assert f"1. (revision) {REVISIONS[0]}" in out
        assert f"2. (uncertain) {DRE_4059_POST[0]}" in out
        assert plan_bound.CONTEXT_CHOICE_LINE not in out

    def test_the_newest_receipt_is_read(self, monkeypatch, capsys):
        records = [_receipt([REVISIONS[0]], at="2026-10-01T00:00:00Z"),
                   _receipt([REVISIONS[1]], at="2026-10-02T00:00:00Z")]
        out = self._context(monkeypatch, capsys, records)
        assert REVISIONS[1] in out and REVISIONS[0] not in out

    def test_a_refused_question_receipt_carries_the_choice_line(self, monkeypatch, capsys):
        records = [_receipt([DECISION_WITH_PATH], refused=True)]
        out = self._context(monkeypatch, capsys, records)
        assert ("One of these is a choice: ask the CEO in plain English through "
                "the escalation exit, or settle it in the plan.") in out
        assert f"1. (decision) {DECISION_WITH_PATH}" in out

    def test_another_authors_receipt_is_not_read(self, monkeypatch, capsys):
        out = self._context(monkeypatch, capsys, [_receipt(pipeline=False)])
        assert "BOUND STATUS: none" in out


# --------------------------------------------------------------------------- #
# 6. the receipt and the question's words                                      #
# --------------------------------------------------------------------------- #


class TestWords:
    def test_the_receipt_says_what_the_card_states(self):
        body = plan_bound.receipt_body(list(REVISIONS))
        assert body.startswith(
            "🔁 bound-rewrite: the critics held this plan at their bound, and "
            "before anyone parks it the planner gets one more rewrite with the "
            "worst finding of every round in front of it — a fresh planning "
            "attempt starts now. This happens once per card. The findings, "
            "oldest first:")
        assert body.rstrip().endswith(
            "If the critics hold it again, the card parks in Triage for an operator.")

    def test_the_refused_sentence_sits_between_the_opening_and_the_list(self):
        body = plan_bound.receipt_body([DECISION_WITH_PATH], refused=True)
        opening_end = body.index("oldest first:")
        sentence = body.index(plan_bound.REFUSED_SENTENCE)
        first = body.index("1. (decision)")
        assert opening_end < sentence < first
        assert plan_bound.REFUSED_SENTENCE == (
            "One finding names a choice, and it was written in terms the CEO "
            "does not read: the planner asks him in plain English through its "
            "own escalation exit, or settles it in the plan.")

    def test_the_finding_list_is_capped_at_five(self):
        findings = [f"{DRE_3879_ROUND_1} Number {n}." for n in range(1, 8)]
        reason = plan_bound.question_reason(CARD, findings)
        lines = [line for line in reason.splitlines()
                 if line.startswith(console_escalation.FINDING_PREFIX)]
        assert len(lines) == plan_bound.MAX_FINDING_LINES == 5
        assert lines[0].endswith("Number 1.") and lines[-1].endswith("Number 5.")

    def test_a_plain_english_reason_passes_the_refusal(self):
        reason = plan_bound.question_reason(CARD, [DRE_3879_ROUND_1])
        assert planning_escalation.refusal(reason) is None
        assert not console_escalation.problems(reason)

    def test_a_reason_naming_a_file_is_refused(self):
        reason = plan_bound.question_reason(CARD, [DECISION_WITH_PATH])
        assert planning_escalation.refusal(reason) is not None

    def test_the_choices_pass_the_console_contract(self):
        choices = plan_bound.question_choices(CARD, [DRE_3879_ROUND_1])
        assert planning_escalation.choices_problem(choices) is None

    def test_send_back_class_is_imported_never_copied(self):
        source = (SCRIPTS / "plan_bound.py").read_text(encoding="utf-8")
        assert "import send_back_class" in source
        for phrase in ("without picking one", "doesn't say which"):
            assert phrase not in source


# --------------------------------------------------------------------------- #
# 7. the lane contract                                                         #
# --------------------------------------------------------------------------- #


CONTRACT_SENTENCE = (
    "On the one-off route that is the critic's QUESTION, or the revising "
    "planner's own, and a critic that decided nothing — never a card the critic "
    "sent back for a defect, which the planner revises (DRE-5376). The round "
    "bound, on the one-off route and on the epic route alike, reaches this lane "
    "by one door only: the bound exit (scripts/plan_bound.py, DRE-6415) reads "
    "the worst finding of every send-back round on the attempt, the one its "
    "marker records, before anything parks, and a decision-class finding among "
    "them arrives here as one question through this same writer, carrying the "
    "Finding, Question and Recommendation lines, when that question is in words "
    "the CEO reads; a bound with no such finding, or whose question the refusal "
    "would not show, grants the planner one more rewrite or parks in Triage, and "
    "is never a row here."
)


def _lane(name: str) -> dict:
    doc = json.loads((REPO / "config" / "lane-contract.json").read_text("utf-8"))
    return next(lane for lane in doc["lanes"] if lane["name"] == name)


class TestLaneContract:
    def test_green_light_clause_b_is_reworded(self):
        entrance = _lane("Green Light")["clauses"]["entrance"]
        text = entrance["text"]
        assert "never the one-off round bound" not in text
        assert "plan_bound.py" in text
        assert "in words the CEO reads" in text
        clause_b = text.split("(b) ", 1)[1].split(" (c) ", 1)[0]
        assert clause_b.endswith(CONTRACT_SENTENCE)

    def test_the_question_arrival_declares_the_bound_exit_as_a_caller(self):
        arrivals = _lane("Green Light")["clauses"]["entrance"]["arrivals"]
        question = next(a for a in arrivals
                        if a["where"] == "planning_escalation.py#escalate")
        assert "plan_bound.py#_question" in question["callers"]

    def test_planning_exit_names_the_bound_exit(self):
        text = _lane("Planning")["clauses"]["exit"]["text"]
        assert "plan_bound.py" in text and "bound exit" in text
        assert "once per card" in text

    def test_triage_entrance_names_the_bound_exit(self):
        text = _lane("Triage")["clauses"]["entrance"]["text"]
        assert "plan_bound.py" in text and "bound exit" in text
        assert "once per card" in text

    def test_the_doc_is_the_render(self):
        import lane_contract
        doc = (REPO / "docs" / "lane-contract.md").read_text(encoding="utf-8")
        assert doc == lane_contract.render_markdown()


# --------------------------------------------------------------------------- #
# 8. the act registry and its console consumer                                 #
# --------------------------------------------------------------------------- #


def _acts() -> list[dict]:
    doc = json.loads((REPO / "config" / "pipeline-acts.json").read_text("utf-8"))
    return doc["acts"]


class TestActRegistry:
    def test_the_row(self):
        row = next(a for a in _acts() if a["name"] == "plan-bound-rewrite")
        assert row["tag"] == "bound-rewrite"
        assert row["kind"] == "recovery"
        assert row["state"] == "dispatched"
        assert row["next_actor"] == "plan.yml"
        assert row["subscriber"] == "plan.yml"
        assert row["discharges"] is None
        started = next(a for a in _acts() if a["name"] == "epic-queue-started")
        assert row["cadence_s"] == started["cadence_s"] == 6900
        assert "epic-queue-started" in row["cadence_why"]
        assert row["emits"] == {
            "file": "scripts/plan_bound.py",
            "anchor": 'pipeline_act.receipt("plan-bound-rewrite"',
        }

    def test_the_row_is_inserted_never_appended(self):
        names = [a["name"] for a in _acts()]
        at = names.index("plan-bound-rewrite")
        assert at != len(names) - 1
        assert names[at - 1] == "epic-start-redispatched"

    def test_the_registry_check_passes(self):
        out = subprocess.run([sys.executable, str(SCRIPTS / "pipeline_act.py"),
                              "check"], capture_output=True, text=True)
        assert out.returncode == 0, out.stdout + out.stderr

    def test_the_receipt_guard_passes(self):
        out = subprocess.run([sys.executable,
                              str(SCRIPTS / "check_act_receipts.py")],
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stdout + out.stderr


def _console(tags) -> str:
    rows = "".join(f'    "{t}": "recovery",\n' for t in tags)
    return textwrap.dedent('"""The console\'s receipt vocabulary."""\n\n') + (
        "ACTS = {\n" + rows + "}\n")


class TestConsoleConsumer:
    """The `act registry consumers` job's question, asked offline against a
    copy of the console's table — the fixture pattern of
    `tests/test_pipeline_acts_consumers.py`."""

    def _run(self, source: str):
        with tempfile.TemporaryDirectory() as tmp:
            console = os.path.join(tmp, "receipts.py")
            with open(console, "w", encoding="utf-8") as fh:
                fh.write(source)
            environ = {k: v for k, v in os.environ.items()
                       if k not in ("BUREAU_CONSOLE_TOKEN",
                                    "BUREAU_CONSOLE_ACTS_FILE")}
            environ["BUREAU_CONSOLE_ACTS_FILE"] = console
            return subprocess.run(
                [sys.executable, str(SCRIPTS / "check_act_consumers.py"), "check"],
                capture_output=True, text=True, env=environ)

    def test_a_console_carrying_bound_rewrite_passes(self):
        tags = [a["tag"] for a in _acts()]
        assert "bound-rewrite" in tags
        out = self._run(_console(tags))
        assert out.returncode == 0, out.stdout + out.stderr

    def test_a_console_without_it_fails_naming_it(self):
        tags = [a["tag"] for a in _acts() if a["tag"] != "bound-rewrite"]
        out = self._run(_console(tags))
        assert out.returncode == 1, out.stdout + out.stderr
        assert "bound-rewrite" in out.stdout
        assert "plan-bound-rewrite" in out.stdout
